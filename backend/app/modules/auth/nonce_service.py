import base64
import hashlib
import os
import secrets
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Dict, Optional

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

from app.config import settings
from app.database import set_tenant_context

NONCE_TTL_SECONDS = 300  # 5 minutes
MAX_ACTIVE_NONCES_PER_ORG = 5
MAX_ISSUANCES_PER_IP_PER_MIN = 20
MAX_ISSUANCES_PER_ORG_PER_MIN = 30


class NonceRateLimiter:
    """Thread-safe in-memory sliding-window rate limiter for unauthenticated nonce issuance."""
    def __init__(self):
        self.ip_window = defaultdict(list)
        self.org_window = defaultdict(list)
        self.lock = Lock()

    def check_and_record(
        self,
        client_ip: str,
        org_id: str,
        max_ip: int = MAX_ISSUANCES_PER_IP_PER_MIN,
        max_org: int = MAX_ISSUANCES_PER_ORG_PER_MIN,
    ) -> None:
        now = time.time()
        window = 60.0
        with self.lock:
            # Clean IP window
            self.ip_window[client_ip] = [t for t in self.ip_window[client_ip] if now - t < window]
            if len(self.ip_window[client_ip]) >= max_ip:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=f"RATE_LIMIT_EXCEEDED: Maximum nonce issuance rate ({max_ip}/min) exceeded for IP.",
                )

            # Clean Org window
            self.org_window[org_id] = [t for t in self.org_window[org_id] if now - t < window]
            if len(self.org_window[org_id]) >= max_org:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=f"RATE_LIMIT_EXCEEDED: Maximum nonce issuance rate ({max_org}/min) exceeded for organization.",
                )

            self.ip_window[client_ip].append(now)
            self.org_window[org_id].append(now)

    def reset(self):
        with self.lock:
            self.ip_window.clear()
            self.org_window.clear()


    def reset(self) -> None:
        """Resets the in-memory sliding window rate limits (for test isolation)."""
        with self.lock:
            self.ip_window.clear()
            self.org_window.clear()


rate_limiter = NonceRateLimiter()


def create_idp_login_nonce(
    db: Session,
    org_id: str,
    idp_provider: str,
    state: Optional[str] = None,
    code_challenge: Optional[str] = None,
    session_token: Optional[str] = None,
    client_ip: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Generates a cryptographically random single-use OIDC nonce bound to an organization,
    Identity Provider, and initiating browser session. Enforces database tenant isolation,
    rate limits, and active-nonce caps.
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

    # 1. Rate Limiting Check
    effective_ip = client_ip or "127.0.0.1"
    rate_limiter.check_and_record(effective_ip, org_id)

    # 2. Establish trusted tenant context for PostgreSQL RLS
    set_tenant_context(db, org_id=org_id, user_id=None)

    # 3. Active Nonce Cap & Expired Nonce Pruning (Abuse defense)
    try:
        # Prune expired unconsumed nonces for this org to keep table bounded
        db.execute(
            text("""
                DELETE FROM public.idp_authentication_nonces
                WHERE org_id = :org_id
                  AND status = 'ACTIVE'
                  AND expires_at < NOW();
            """),
            {"org_id": org_id},
        )

        active_count = db.execute(
            text("""
                SELECT count(*)
                FROM public.idp_authentication_nonces
                WHERE org_id = :org_id
                  AND status = 'ACTIVE'
                  AND expires_at > NOW();
            """),
            {"org_id": org_id},
        ).scalar() or 0

        if active_count >= MAX_ACTIVE_NONCES_PER_ORG:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"ACTIVE_NONCE_LIMIT_EXCEEDED: Maximum active authentication transactions ({MAX_ACTIVE_NONCES_PER_ORG}) reached for organization.",
            )

        # 4. Generate transaction tokens and cryptographic browser session binding
        nonce = secrets.token_urlsafe(32)
        state_val = state or secrets.token_urlsafe(32)
        resolved_session_token = session_token or secrets.token_urlsafe(32)
        session_binding_hash = hashlib.sha256(resolved_session_token.encode("utf-8")).hexdigest()

        clean_code_challenge = (code_challenge or "").strip() or None

        db.execute(
            text("""
                INSERT INTO public.idp_authentication_nonces (
                    nonce, org_id, idp_provider, status, created_at, expires_at,
                    state, code_challenge, session_binding_hash, client_ip
                ) VALUES (
                    :nonce, :org_id, :idp_provider, 'ACTIVE', NOW(), NOW() + INTERVAL '5 minutes',
                    :state, :code_challenge, :session_binding_hash, :client_ip
                );
            """),
            {
                "nonce": nonce,
                "org_id": org_id,
                "idp_provider": provider_clean,
                "state": state_val,
                "code_challenge": clean_code_challenge,
                "session_binding_hash": session_binding_hash,
                "client_ip": effective_ip,
            },
        )
        db.commit()
    except HTTPException:
        db.rollback()
        raise
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
        "state": state_val,
        "session_token": resolved_session_token,
        "code_challenge": clean_code_challenge,
    }


def consume_idp_login_nonce(
    db: Session,
    nonce: str,
    org_id: str,
    idp_provider: str,
    email: str,
    session_token: Optional[str] = None,
    state: Optional[str] = None,
    code_verifier: Optional[str] = None,
) -> bool:
    """
    Atomically consumes a single-use authentication nonce within a database transaction.
    Enforces RLS tenant isolation, browser session binding, and PKCE S256 verification.
    Guarantees that concurrent or repeated attempts to use the same nonce strictly fail closed.
    """
    if not nonce or not str(nonce).strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="MISSING_IDP_NONCE: Authentication nonce is required.",
        )

    nonce_clean = str(nonce).strip()
    provider_clean = (idp_provider or "").strip().lower()

    # 1. Establish trusted tenant context for PostgreSQL RLS before any database access
    set_tenant_context(db, org_id=org_id, user_id=None)

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
                  AND expires_at > NOW()
                RETURNING nonce, state, code_challenge, session_binding_hash;
            """),
            {
                "nonce": nonce_clean,
                "org_id": org_id,
                "idp_provider": provider_clean,
                "email": email.strip().lower(),
            },
        )
        row = result.fetchone()

        if row:
            db_nonce, db_state, db_code_challenge, db_session_hash = row

            # 2. Browser session binding validation (anti-CSRF / session fixation defense)
            if db_session_hash:
                if not session_token or not str(session_token).strip():
                    db.rollback()
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="MISSING_SESSION_BINDING: Authentication transaction requires initiating browser session binding cookie.",
                    )
                computed_session_hash = hashlib.sha256(str(session_token).strip().encode("utf-8")).hexdigest()
                if computed_session_hash != db_session_hash:
                    db.rollback()
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="SESSION_BINDING_MISMATCH: Authentication transaction does not match the initiating browser session.",
                    )

            # 3. PKCE S256 verification
            if db_code_challenge:
                if not code_verifier or not str(code_verifier).strip():
                    db.rollback()
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="MISSING_PKCE_VERIFIER: Authentication transaction requires a PKCE code_verifier.",
                    )
                # S256: BASE64URL-ENCODE(SHA256(ASCII(code_verifier)))
                verifier_bytes = str(code_verifier).strip().encode("ascii")
                digest = hashlib.sha256(verifier_bytes).digest()
                computed_challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
                if computed_challenge != db_code_challenge:
                    db.rollback()
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="INVALID_PKCE_VERIFIER: PKCE code_verifier does not match transaction code_challenge.",
                    )

            # 4. State parameter validation (if provided)
            if db_state and state and str(state).strip() != db_state:
                db.rollback()
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="STATE_MISMATCH: OIDC transaction state does not match expected state.",
                )

            db.commit()
            return True

        # Exactly 0 rows were updated. Investigate failure mode for detailed security rejection.
        # Check if the nonce exists under this tenant context
        status_row = db.execute(
            text("""
                SELECT status, expires_at, org_id, idp_provider
                FROM public.idp_authentication_nonces
                WHERE nonce = :nonce;
            """),
            {"nonce": nonce_clean},
        ).fetchone()

        if not status_row:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="UNKNOWN_IDP_NONCE: Authentication transaction nonce was not issued by this server or does not belong to this organization.",
            )

        curr_status, expires_at, registered_org, registered_provider = status_row
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
