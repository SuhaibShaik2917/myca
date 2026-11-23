import json
import os
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException
from jose import JWTError, jwt
from pydantic import BaseModel
from sqlalchemy import text

from app.config import settings
from app.core.security import create_access_token
from app.database import SessionLocal, verify_and_resolve_tenant_membership

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


@router.post("/demo-token", response_model=DemoTokenResponse)
def issue_demo_token(payload: DemoTokenRequest) -> DemoTokenResponse:
    """
    Hackathon/demo token issuer. Strictly prohibited in production.

    Verifies active organization membership, then issues a short-lived signed
    access token. In production (ENVIRONMENT=production), this endpoint is
    disabled unconditionally with HTTP 403 to prevent unauthenticated admin
    token acquisition.
    """
    env = (os.getenv("ENVIRONMENT") or settings.ENVIRONMENT or "development").strip().lower()
    if env == "production":
        raise HTTPException(
            status_code=403,
            detail="DEMO_TOKEN_PROHIBITED_IN_PRODUCTION: /auth/demo-token is strictly disabled in production environments. Production requests must authenticate via an authorized identity provider.",
        )

    try:
        uuid.UUID(payload.user_id)
        uuid.UUID(payload.org_id)
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=401, detail="Invalid user_id or org_id")

    db = SessionLocal()
    try:
        if not verify_and_resolve_tenant_membership(db, payload.user_id, payload.org_id):
            raise HTTPException(
                status_code=403,
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
    Verifies IdP assertion, matches authenticated email to registered active user in
    public.global_users, verifies active tenant membership in public.organization_memberships,
    and returns a cryptographically signed MyCA access token.
    """
    try:
        uuid.UUID(payload.org_id)
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid org_id format")

    env = (os.getenv("ENVIRONMENT") or settings.ENVIRONMENT or "development").strip().lower()
    idp_issuer = os.getenv("MYCA_IDP_ISSUER")
    idp_pubkey = os.getenv("MYCA_IDP_PUBLIC_KEY")

    if env == "production" and not (idp_issuer and idp_pubkey):
        raise HTTPException(
            status_code=503,
            detail="IDP_INTEGRATION_UNAVAILABLE: External Identity Provider trust anchor is not configured in this production environment.",
        )

    # In test/development mode, verify test IdP token signature or structured claims
    verified_email = None
    if idp_pubkey:
        try:
            claims = jwt.decode(payload.idp_token, idp_pubkey, algorithms=["RS256", "ES256", "HS256"])
            verified_email = claims.get("email") or claims.get("sub")
        except JWTError as exc:
            raise HTTPException(status_code=401, detail=f"INVALID_IDP_TOKEN: {str(exc)}")
    elif env in ("development", "test"):
        # Permitted test assertion parser for unit and integration testing
        try:
            claims = jwt.decode(payload.idp_token, settings.SECRET_KEY, algorithms=["HS256"])
            verified_email = claims.get("email") or claims.get("sub")
        except JWTError:
            # Fallback for structured test payloads in non-production
            if payload.user_email and "@" in payload.user_email:
                verified_email = payload.user_email
            else:
                raise HTTPException(status_code=401, detail="INVALID_IDP_TOKEN: Signature verification failed")
    else:
        raise HTTPException(status_code=401, detail="AUTHENTICATION_FAILED: Unverifiable IdP token")

    if not verified_email:
        raise HTTPException(status_code=401, detail="INVALID_IDP_TOKEN: Missing authenticated user email")

    db = SessionLocal()
    try:
        user_row = db.execute(
            text("SELECT id, email, is_active FROM public.global_users WHERE email = :email;"),
            {"email": verified_email.strip().lower()},
        ).fetchone()

        if not user_row:
            raise HTTPException(
                status_code=401,
                detail=f"UNREGISTERED_IDP_USER: User with email '{verified_email}' is not registered in MyCA.",
            )

        user_id = str(user_row[0])
        is_active = bool(user_row[2])

        if not is_active:
            raise HTTPException(
                status_code=403,
                detail="SUSPENDED_USER: User account is inactive or suspended.",
            )

        if not verify_and_resolve_tenant_membership(db, user_id, payload.org_id):
            raise HTTPException(
                status_code=403,
                detail="User does not possess active membership in the requested organization",
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
