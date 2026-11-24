import os
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import text

from app.config import settings
from app.core.security import create_access_token
from app.database import SessionLocal, verify_and_resolve_tenant_membership, set_tenant_context
from app.modules.auth.idp_verifier import validate_idp_token
from app.modules.auth.nonce_service import create_idp_login_nonce, consume_idp_login_nonce

router = APIRouter(prefix="/auth", tags=["Auth"])


class DemoTokenRequest(BaseModel):
    user_id: str
    org_id: str


class DemoTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str
    org_id: str
    expires_in: int


class IdpNonceRequest(BaseModel):
    org_id: str
    idp_provider: str
    state: Optional[str] = None
    code_challenge: Optional[str] = None


class IdpNonceResponse(BaseModel):
    nonce: str
    expires_in: int
    org_id: str
    idp_provider: str
    state: str
    session_token: str
    code_challenge: Optional[str] = None


class IdpTokenExchangeRequest(BaseModel):
    idp_provider: str
    idp_token: str
    org_id: str
    nonce: Optional[str] = None
    state: Optional[str] = None
    code_verifier: Optional[str] = None
    session_token: Optional[str] = None
    user_email: Optional[str] = None


def is_demo_token_allowed() -> bool:
    """
    Determines if demo token generation is permitted.
    Demo tokens are disabled by default across all environments.
    They are only permitted when:
    1. The environment is explicitly designated as local/test ('development' or 'test').
    2. ALLOW_DEMO_TOKENS is explicitly set to true.
    """
    raw_env = os.getenv("ENVIRONMENT") or getattr(settings, "ENVIRONMENT", "")
    env = str(raw_env).strip().lower() if raw_env else ""
    if env not in ("development", "test"):
        return False
    
    allow_flag = getattr(settings, "ALLOW_DEMO_TOKENS", False) or (
        os.getenv("ALLOW_DEMO_TOKENS", "").strip().lower() in ("true", "1", "yes")
    )
    return bool(allow_flag)


@router.post("/demo-token", response_model=DemoTokenResponse)
def issue_demo_token(payload: DemoTokenRequest) -> DemoTokenResponse:
    """
    Development/test demo token issuer. Strictly prohibited in production and staging.

    Disabled by default. Permitted exclusively in designated local/test environments
    when ALLOW_DEMO_TOKENS=true is explicitly configured. An unset, invalid, production,
    or staging environment strictly fails closed with HTTP 403 Forbidden.
    """
    raw_env = os.getenv("ENVIRONMENT") or getattr(settings, "ENVIRONMENT", "")
    env = str(raw_env).strip().lower() if raw_env else "unspecified"
    
    if env not in ("development", "test"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"DEMO_TOKEN_PROHIBITED: /auth/demo-token is strictly disabled in '{env}' environment. "
                   "Production and staging requests must authenticate via an authorized Identity Provider.",
        )

    if not is_demo_token_allowed():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="DEMO_TOKEN_DISABLED_BY_DEFAULT: /auth/demo-token is disabled by default. "
                   "It can only be enabled in designated local/test environments via explicit ALLOW_DEMO_TOKENS=true configuration.",
        )

    try:
        uuid.UUID(payload.user_id)
        uuid.UUID(payload.org_id)
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid user_id or org_id")

    db = SessionLocal()
    try:
        if not verify_and_resolve_tenant_membership(db, payload.user_id, payload.org_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User does not possess active membership in the requested organization",
            )
    finally:
        db.close()

    token = create_access_token(subject=payload.user_id, org_id=payload.org_id)
    return DemoTokenResponse(
        access_token=token,
        user_id=payload.user_id,
        org_id=payload.org_id,
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.post("/idp-nonce", response_model=IdpNonceResponse)
def request_idp_login_nonce(
    payload: IdpNonceRequest,
    request: Request,
    response: Response,
) -> IdpNonceResponse:
    """
    Issues a server-tracked single-use OIDC login nonce bound to an organization,
    Identity Provider, and initiating browser session. Enforces database tenant isolation,
    rate limits, and active-nonce caps. Sets a secure HttpOnly session cookie to bind
    the subsequent callback to the initiating user agent.
    """
    raw_env = os.getenv("ENVIRONMENT") or getattr(settings, "ENVIRONMENT", "")
    env = str(raw_env).strip().lower() if raw_env else "development"

    existing_cookie_token = request.cookies.get("myca_auth_session")
    header_session_token = request.headers.get("X-Auth-Session-Id")
    inbound_session_token = existing_cookie_token or header_session_token

    client_ip = request.client.host if request.client else "127.0.0.1"

    db = SessionLocal()
    try:
        result = create_idp_login_nonce(
            db=db,
            org_id=payload.org_id,
            idp_provider=payload.idp_provider,
            state=payload.state,
            code_challenge=payload.code_challenge,
            session_token=inbound_session_token,
            client_ip=client_ip,
        )

        # Set secure HttpOnly session cookie on initiating user-agent
        response.set_cookie(
            key="myca_auth_session",
            value=result["session_token"],
            max_age=300,
            httponly=True,
            secure=True if env in ("production", "staging") else False,
            samesite="lax",
        )

        return IdpNonceResponse(**result)
    finally:
        db.close()


@router.post("/idp-exchange", response_model=DemoTokenResponse)
def exchange_idp_token(
    payload: IdpTokenExchangeRequest,
    request: Request,
    response: Response,
) -> DemoTokenResponse:
    """
    Production authenticated login flow via federated Identity Provider (IdP).
    Rigorously validates IdP token signature, algorithm confusion defenses, issuer,
    audience, expiration, and claims.
    
    Validates and atomically consumes single-use authentication transaction nonces
    under PostgreSQL RLS, validating PKCE S256 and browser session binding to prevent
    token replay, login CSRF, and cross-session transaction substitution.
    """
    try:
        uuid.UUID(payload.org_id)
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid org_id format")

    raw_env = os.getenv("ENVIRONMENT") or getattr(settings, "ENVIRONMENT", "")
    env = str(raw_env).strip().lower() if raw_env else "development"

    # Cryptographically validate the IdP token and its claims (fails closed on invalid/untrusted tokens)
    verified_claims = validate_idp_token(
        payload.idp_provider,
        payload.idp_token,
        env=env,
        expected_nonce=payload.nonce,
    )
    
    # Extract identity strictly from verified token claims (never from unauthenticated payload fields)
    verified_email = verified_claims["email"].strip().lower()

    # Extract and validate transaction nonce for replay protection
    token_nonce = verified_claims.get("nonce")
    if not token_nonce or not str(token_nonce).strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="MISSING_IDP_NONCE: IdP token must contain an authentication transaction nonce claim.",
        )

    # Resolve browser session token for binding validation
    session_token = (
        request.cookies.get("myca_auth_session")
        or request.headers.get("X-Auth-Session-Id")
        or payload.session_token
    )

    db = SessionLocal()
    try:
        # Atomically consume the single-use nonce enforcing RLS, browser session binding, and PKCE S256
        consume_idp_login_nonce(
            db=db,
            nonce=str(token_nonce).strip(),
            org_id=payload.org_id,
            idp_provider=payload.idp_provider,
            email=verified_email,
            session_token=session_token,
            state=payload.state,
            code_verifier=payload.code_verifier,
        )

        # Clear session cookie on successful transaction consumption
        response.delete_cookie("myca_auth_session")

        # Set tenant org context so security_barrier view v_org_users allows reading org users
        set_tenant_context(db, org_id=payload.org_id, user_id=None)

        # Query user identity and membership via canonical secure view public.v_org_users
        user_row = None
        try:
            user_row = db.execute(
                text("SELECT id, email, membership_status FROM public.v_org_users WHERE LOWER(email) = :email;"),
                {"email": verified_email},
            ).fetchone()
        except Exception:
            pass

        if not user_row:
            # Fallback for platform/global lookup if permitted
            try:
                g_row = db.execute(
                    text("SELECT id, email, is_active FROM public.global_users WHERE LOWER(email) = :email;"),
                    {"email": verified_email},
                ).fetchone()
                if g_row:
                    user_row = g_row
            except Exception:
                pass

        if not user_row:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"UNREGISTERED_IDP_USER: User with email '{verified_email}' is not registered in MyCA.",
            )

        user_id = str(user_row[0])
        status_val = user_row[2]
        is_active = status_val in (True, "active", "ACTIVE", "true")
        if not is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="SUSPENDED_USER: User account is inactive or suspended.",
            )

        if not verify_and_resolve_tenant_membership(db, user_id, payload.org_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="TENANT_MEMBERSHIP_REQUIRED: User does not possess active membership in the requested organization.",
            )
    finally:
        db.close()

    token = create_access_token(subject=user_id, org_id=payload.org_id)
    return DemoTokenResponse(
        access_token=token,
        user_id=user_id,
        org_id=payload.org_id,
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )
