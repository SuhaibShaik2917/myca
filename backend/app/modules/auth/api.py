import ipaddress
import logging
import os
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.config import settings
from app.core.security import create_access_token
from app.database import SessionLocal, verify_and_resolve_tenant_membership, set_tenant_context
from app.modules.auth.idp_verifier import validate_idp_token
from app.modules.auth.nonce_service import create_idp_login_nonce, consume_idp_login_nonce

logger = logging.getLogger(__name__)

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
    state: str = Field(..., min_length=16, max_length=128, description="OIDC transaction state")
    code_challenge: str = Field(..., min_length=43, max_length=128, pattern=r"^[A-Za-z0-9_-]{43,128}$", description="Base64url-encoded SHA-256 PKCE code challenge")


class IdpNonceResponse(BaseModel):
    nonce: str
    expires_in: int
    org_id: str
    idp_provider: str
    state: str
    code_challenge: str


class IdpTokenExchangeRequest(BaseModel):
    idp_provider: str
    idp_token: str
    org_id: str
    nonce: Optional[str] = None
    state: str = Field(..., min_length=16, max_length=128, description="OIDC transaction state")
    code_verifier: str = Field(..., min_length=43, max_length=128, pattern=r"^[A-Za-z0-9_.~-]{43,128}$", description="RFC 7636 PKCE code verifier (43-128 unreserved chars)")
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


def extract_client_ip(request: Request) -> str:
    """
    Safely resolves client IP address while defending against header spoofing.
    X-Forwarded-For is only trusted if the direct connection originates from a configured TRUSTED_PROXIES peer.
    Direct connections from untrusted peers strictly use request.client.host.
    Validates that extracted IP addresses conform to RFC IPv4/IPv6 address syntax.
    """
    peer_ip = request.client.host if request.client and request.client.host else "127.0.0.1"

    trusted_proxies = getattr(settings, "TRUSTED_PROXIES", ["127.0.0.1", "::1"])
    if isinstance(trusted_proxies, str):
        trusted_proxies = [p.strip() for p in trusted_proxies.split(",") if p.strip()]
    trusted_set = set(trusted_proxies)

    if peer_ip in trusted_set:
        xff = request.headers.get("X-Forwarded-For")
        if xff:
            for hop in xff.split(","):
                candidate = hop.strip()
                try:
                    ip_obj = ipaddress.ip_address(candidate)
                    return str(ip_obj)
                except ValueError:
                    continue

    try:
        ip_obj = ipaddress.ip_address(peer_ip)
        return str(ip_obj)
    except ValueError:
        return "127.0.0.1"


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

    inbound_session_token = request.cookies.get("myca_auth_session")
    client_ip = extract_client_ip(request)

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
            path="/api/v1/auth",
        )

        return IdpNonceResponse(
            nonce=result["nonce"],
            expires_in=result["expires_in"],
            org_id=result["org_id"],
            idp_provider=result["idp_provider"],
            state=result["state"],
            code_challenge=result["code_challenge"],
        )
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

    # Resolve browser session token: strictly from HttpOnly cookie only (no headers or body fallback)
    session_token = request.cookies.get("myca_auth_session")
    if not session_token or not str(session_token).strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="MISSING_SESSION_COOKIE: Browser authentication requires myca_auth_session HttpOnly cookie.",
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
        response.delete_cookie("myca_auth_session", path="/api/v1/auth")

        # Set tenant org context so security_barrier view v_org_users allows reading org users
        set_tenant_context(db, org_id=payload.org_id, user_id=None)

        # Query user identity and membership via canonical secure view public.v_org_users
        user_row = None
        try:
            user_row = db.execute(
                text("SELECT id, email, membership_status FROM public.v_org_users WHERE LOWER(email) = :email;"),
                {"email": verified_email},
            ).fetchone()
        except Exception as exc:
            logger.debug("User lookup via v_org_users encountered exception: %s", exc)

        if not user_row:
            # Fallback for platform/global lookup if permitted
            try:
                g_row = db.execute(
                    text("SELECT id, email, is_active FROM public.global_users WHERE LOWER(email) = :email;"),
                    {"email": verified_email},
                ).fetchone()
                if g_row:
                    user_row = g_row
            except Exception as exc:
                logger.debug("User lookup via global_users encountered exception: %s", exc)

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
