import os
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text

from app.config import settings
from app.core.security import create_access_token
from app.database import SessionLocal, verify_and_resolve_tenant_membership
from app.modules.auth.idp_verifier import validate_idp_token

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


class IdpTokenExchangeRequest(BaseModel):
    idp_provider: str
    idp_token: str
    org_id: str
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


@router.post("/idp-exchange", response_model=DemoTokenResponse)
def exchange_idp_token(payload: IdpTokenExchangeRequest) -> DemoTokenResponse:
    """
    Production authenticated login flow via federated Identity Provider (IdP).
    Rigorously validates IdP token signature, algorithm confusion defenses, issuer,
    audience, expiration, and claims.
    
    Matches verified identity claim to an active registered user in public.global_users,
    verifies active tenant membership in public.organization_memberships, and issues
    a cryptographically signed MyCA access token.
    """
    try:
        uuid.UUID(payload.org_id)
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid org_id format")

    raw_env = os.getenv("ENVIRONMENT") or getattr(settings, "ENVIRONMENT", "")
    env = str(raw_env).strip().lower() if raw_env else "development"

    # Cryptographically validate the IdP token and its claims (fails closed on invalid/untrusted tokens)
    verified_claims = validate_idp_token(payload.idp_provider, payload.idp_token, env=env)
    
    # Extract identity strictly from verified token claims (never from unauthenticated payload fields)
    verified_email = (verified_claims.get("email") or verified_claims.get("sub") or "").strip().lower()
    if not verified_email:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="INVALID_IDP_TOKEN: Token did not contain a verified email or subject claim.",
        )

    db = SessionLocal()
    try:
        user_row = db.execute(
            text("SELECT id, email, is_active FROM public.global_users WHERE email = :email;"),
            {"email": verified_email},
        ).fetchone()

        if not user_row:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"UNREGISTERED_IDP_USER: User with email '{verified_email}' is not registered in MyCA.",
            )

        user_id = str(user_row[0])
        is_active = bool(user_row[2])

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
