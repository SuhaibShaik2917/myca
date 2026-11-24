import os
import secrets
import uuid
from typing import Any, Dict
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

from app.config import settings

NONCE_TTL_SECONDS = 300  # 5 minutes


def create_idp_login_nonce(db: Session, org_id: str, idp_provider: str) -> Dict[str, Any]:
    """
    Generates a cryptographically random single-use OIDC nonce bound to an organization
    and expected Identity Provider, persisting it in PostgreSQL with a short TTL.
    """
    try:
        uuid.UUID(org_id)
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="INVALID_ORG_ID: Organization ID must be a valid UUID.",
        )

    provider_clean = (idp_provider or "").strip().lower()
    allowed_providers = set(getattr(settings, "MYCA_IDP_ALLOWED_PROVIDERS", ["google", "azuread", "okta", "auth0"]))
    allowed_providers = {p.lower() for p in allowed_providers}
    env = (os.getenv("ENVIRONMENT") or getattr(settings, "ENVIRONMENT", "")).strip().lower()
    if env in ("development", "test"):
        allowed_providers.add("test-idp")

    if not provider_clean or provider_clean not in allowed_providers:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"UNSUPPORTED_IDP_PROVIDER: Provider '{idp_provider}' is not authorized. Allowed: {sorted(list(allowed_providers))}",
        )

    # Cryptographically random token (256 bits of entropy)
    nonce = secrets.token_urlsafe(32)

    try:
        db.execute(
            text("""
                INSERT INTO public.idp_authentication_nonces (
                    nonce, org_id, idp_provider, status, created_at, expires_at
                ) VALUES (
                    :nonce, :org_id, :idp_provider, 'ACTIVE', NOW(), NOW() + INTERVAL '5 minutes'
                );
            """),
            {
                "nonce": nonce,
                "org_id": org_id,
                "idp_provider": provider_clean,
            },
        )
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"IDP_AUTH_SERVICE_UNAVAILABLE: Failed to persist authentication nonce: {str(exc)}",
        )

    return {
        "nonce": nonce,
        "expires_in": NONCE_TTL_SECONDS,
        "org_id": org_id,
        "idp_provider": provider_clean,
    }


def consume_idp_login_nonce(
    db: Session,
    nonce: str,
    org_id: str,
    idp_provider: str,
    email: str,
) -> bool:
    """
    Atomically consumes a single-use authentication nonce within a database transaction.
    Guarantees that concurrent or repeated attempts to use the same nonce strictly fail closed.
    """
    if not nonce or not str(nonce).strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="MISSING_IDP_NONCE: Authentication nonce is required.",
        )

    nonce_clean = str(nonce).strip()
    provider_clean = (idp_provider or "").strip().lower()

    try:
        # Atomic conditional update enforcing single-use under PostgreSQL row locking
        result = db.execute(
            text("""
                UPDATE public.idp_authentication_nonces
                SET status = 'CONSUMED',
                    consumed_at = NOW(),
                    consumed_by_email = :email
                WHERE nonce = :nonce
                  AND org_id = :org_id
                  AND idp_provider = :idp_provider
                  AND status = 'ACTIVE'
                  AND expires_at > NOW();
            """),
            {
                "nonce": nonce_clean,
                "org_id": org_id,
                "idp_provider": provider_clean,
                "email": email.strip().lower(),
            },
        )

        if result.rowcount == 1:
            db.commit()
            return True

        # Exactly 0 rows were updated. Investigate failure mode for detailed security rejection.
        row = db.execute(
            text("""
                SELECT status, expires_at, org_id, idp_provider
                FROM public.idp_authentication_nonces
                WHERE nonce = :nonce;
            """),
            {"nonce": nonce_clean},
        ).fetchone()

        if not row:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="UNKNOWN_IDP_NONCE: Authentication transaction nonce was not issued by this server.",
            )

        curr_status, expires_at, registered_org, registered_provider = row
        curr_status = str(curr_status).strip().upper()

        if curr_status == "CONSUMED":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="IDP_NONCE_REPLAY: Authentication transaction nonce has already been consumed. Token replay rejected.",
            )

        now = datetime.now(timezone.utc)
        if expires_at and expires_at < now:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="EXPIRED_IDP_NONCE: Authentication transaction nonce has expired.",
            )

        if str(registered_org) != org_id or str(registered_provider).lower() != provider_clean:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="NONCE_CONTEXT_MISMATCH: Authentication transaction does not match requested organization or provider.",
            )

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="INVALID_IDP_NONCE: Authentication transaction nonce could not be validated.",
        )

    except HTTPException:
        db.rollback()
        raise
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"IDP_AUTH_SERVICE_UNAVAILABLE: Database error during atomic nonce consumption: {str(exc)}",
        )
