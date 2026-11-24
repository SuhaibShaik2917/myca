import base64
import hashlib
import hmac
import logging
import os
import re
import secrets
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

from app.config import settings
from app.database import set_tenant_context

logger = logging.getLogger(__name__)

NONCE_TTL_SECONDS = 300  # 5 minutes
MAX_ACTIVE_NONCES_PER_ORG = 5
MAX_ISSUANCES_PER_IP_PER_MIN = 20
MAX_ISSUANCES_PER_ORG_PER_MIN = 30
CONSUMED_NONCE_RETENTION_HOURS = 24


def check_distributed_rate_limit(
    db: Session,
    client_ip: str,
    org_id: str,
    max_ip: int = MAX_ISSUANCES_PER_IP_PER_MIN,
    max_org: int = MAX_ISSUANCES_PER_ORG_PER_MIN,
    current_time: Optional[datetime] = None,
) -> None:
    """
    Atomically enforces distributed rolling 60-second rate limits using PostgreSQL row locking on public.auth_rate_limits.
    Unlike calendar-minute fixed windows, this sliding window tracks exact request timestamps over a 60-second rolling window,
    completely preventing boundary bursts across minute transitions.
    Shared across all worker processes, threads, and instances without memory synchronization gaps.
    """
    now = current_time or datetime.now(timezone.utc)

    # 1. IP rolling 60-second rate limit check
    ip_key = f"ip:{client_ip.strip()}"
    db.execute(
        text("""
            INSERT INTO public.auth_rate_limits (rate_key, timestamps, updated_at)
            VALUES (:key, '{}'::timestamptz[], :now)
            ON CONFLICT (rate_key) DO NOTHING;
        """),
        {"key": ip_key, "now": now},
    )
    ip_result = db.execute(
        text("""
            UPDATE public.auth_rate_limits
            SET timestamps = CASE
                    WHEN (
                        SELECT count(*)
                        FROM unnest(public.auth_rate_limits.timestamps) AS t_item
                        WHERE t_item > :now - INTERVAL '60 seconds'
                    ) < :max_ip
                    THEN (
                        SELECT coalesce(array_agg(t_item ORDER BY t_item), '{}'::timestamptz[]) || :now
                        FROM unnest(public.auth_rate_limits.timestamps) AS t_item
                        WHERE t_item > :now - INTERVAL '60 seconds'
                    )
                    ELSE (
                        SELECT coalesce(array_agg(t_item ORDER BY t_item), '{}'::timestamptz[])
                        FROM unnest(public.auth_rate_limits.timestamps) AS t_item
                        WHERE t_item > :now - INTERVAL '60 seconds'
                    )
                END,
                updated_at = :now
            WHERE rate_key = :key
            RETURNING 
                (public.auth_rate_limits.timestamps @> ARRAY[:now]) AS is_allowed,
                (
                    SELECT count(*)
                    FROM unnest(public.auth_rate_limits.timestamps) AS t_item
                    WHERE t_item > :now - INTERVAL '60 seconds'
                ) AS active_count;
        """),
        {"key": ip_key, "now": now, "max_ip": max_ip},
    ).fetchone()

    ip_allowed = ip_result[0] if ip_result else False
    if not ip_allowed:
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"RATE_LIMIT_EXCEEDED: Maximum nonce issuance rate ({max_ip}/min) exceeded for IP.",
        )

    # 2. Org rolling 60-second rate limit check
    org_key = f"org:{str(org_id).strip()}"
    db.execute(
        text("""
            INSERT INTO public.auth_rate_limits (rate_key, timestamps, updated_at)
            VALUES (:key, '{}'::timestamptz[], :now)
            ON CONFLICT (rate_key) DO NOTHING;
        """),
        {"key": org_key, "now": now},
    )
    org_result = db.execute(
        text("""
            UPDATE public.auth_rate_limits
            SET timestamps = CASE
                    WHEN (
                        SELECT count(*)
                        FROM unnest(public.auth_rate_limits.timestamps) AS t_item
                        WHERE t_item > :now - INTERVAL '60 seconds'
                    ) < :max_org
                    THEN (
                        SELECT coalesce(array_agg(t_item ORDER BY t_item), '{}'::timestamptz[]) || :now
                        FROM unnest(public.auth_rate_limits.timestamps) AS t_item
                        WHERE t_item > :now - INTERVAL '60 seconds'
                    )
                    ELSE (
                        SELECT coalesce(array_agg(t_item ORDER BY t_item), '{}'::timestamptz[])
                        FROM unnest(public.auth_rate_limits.timestamps) AS t_item
                        WHERE t_item > :now - INTERVAL '60 seconds'
                    )
                END,
                updated_at = :now
            WHERE rate_key = :key
            RETURNING 
                (public.auth_rate_limits.timestamps @> ARRAY[:now]) AS is_allowed,
                (
                    SELECT count(*)
                    FROM unnest(public.auth_rate_limits.timestamps) AS t_item
                    WHERE t_item > :now - INTERVAL '60 seconds'
                ) AS active_count;
        """),
        {"key": org_key, "now": now, "max_org": max_org},
    ).fetchone()

    org_allowed = org_result[0] if org_result else False
    if not org_allowed:
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"RATE_LIMIT_EXCEEDED: Maximum nonce issuance rate ({max_org}/min) exceeded for organization.",
        )
    db.commit()


def cleanup_expired_auth_rate_limits(db: Session, retention_hours: int = 1) -> int:
    """
    Safely prunes inactive rate-limit records where no requests have occurred
    within the retention window (default 1 hour).
    Because active rate limiting operates strictly on a rolling 60-second window,
    pruning records older than 1 hour cannot interfere with active enforcement.
    """
    res = db.execute(
        text("""
            DELETE FROM public.auth_rate_limits
            WHERE updated_at < NOW() - make_interval(hours => :hours);
        """),
        {"hours": retention_hours},
    )
    db.commit()
    return res.rowcount or 0


def reset_auth_rate_limits(db: Session) -> None:
    """Resets the persistent distributed rate limit table (for test isolation)."""
    db.execute(text("DELETE FROM public.auth_rate_limits;"))
    db.commit()


# Backward compatibility alias for test suites
class RateLimiterAdapter:
    def reset(self, db: Optional[Session] = None) -> None:
        if db is not None:
            reset_auth_rate_limits(db)
        else:
            from app.database import SessionLocal
            with SessionLocal() as s:
                reset_auth_rate_limits(s)

    def check_and_record(self, client_ip: str, org_id: str, max_ip: int = MAX_ISSUANCES_PER_IP_PER_MIN, max_org: int = MAX_ISSUANCES_PER_ORG_PER_MIN) -> None:
        from app.database import SessionLocal
        with SessionLocal() as s:
            check_distributed_rate_limit(s, client_ip, org_id, max_ip, max_org)


rate_limiter = RateLimiterAdapter()


def create_idp_login_nonce(
    db: Session,
    org_id: str,
    idp_provider: str,
    state: str,
    code_challenge: str,
    session_token: Optional[str] = None,
    client_ip: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Generates a cryptographically random single-use OIDC nonce bound to an organization,
    Identity Provider, and initiating browser session.
    Enforces mandatory state, mandatory PKCE S256 code_challenge, distributed rate limiting,
    and concurrency-safe active-nonce quotas under PostgreSQL advisory locks.
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

    # 1. Mandatory State Parameter Verification
    if not state or not str(state).strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="MISSING_STATE: OIDC transaction state is mandatory for browser authentication.",
        )
    clean_state = str(state).strip()
    if len(clean_state) < 16 or len(clean_state) > 128:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="INVALID_STATE: OIDC transaction state must be between 16 and 128 characters.",
        )

    # 2. Mandatory PKCE S256 code_challenge Verification
    if not code_challenge or not str(code_challenge).strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="MISSING_CODE_CHALLENGE: PKCE code_challenge is mandatory for browser authentication.",
        )
    clean_code_challenge = str(code_challenge).strip()
    if not re.match(r"^[A-Za-z0-9_-]{43,128}$", clean_code_challenge):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="INVALID_CODE_CHALLENGE: PKCE code_challenge must be a valid base64url-encoded string (S256).",
        )

    # 3. Distributed Rate Limiting Check (Shared PostgreSQL enforcement)
    effective_ip = client_ip or "127.0.0.1"
    check_distributed_rate_limit(db, effective_ip, org_id)

    # 4. Establish trusted tenant context for PostgreSQL RLS
    set_tenant_context(db, org_id=org_id, user_id=None)

    # 5. Concurrency-Safe Active Nonce Quota & Safe Bounded Pruning
    try:
        # Acquire transaction-scoped advisory lock on org_id to serialize active nonce allocation
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:org_id));"), {"org_id": str(org_id)})

        # Bounded Prune:
        # a) Expired unconsumed nonces for this org to keep active records bounded
        db.execute(
            text("""
                DELETE FROM public.idp_authentication_nonces
                WHERE org_id = :org_id
                  AND status = 'ACTIVE'
                  AND expires_at < NOW();
            """),
            {"org_id": org_id},
        )

        # b) Bounded purge of consumed nonces older than retention threshold (24 hours).
        # Strictly preserves consumed nonces for the entire 24h replay defense window.
        db.execute(
            text("""
                DELETE FROM public.idp_authentication_nonces
                WHERE status = 'CONSUMED'
                  AND consumed_at < NOW() - INTERVAL '24 hours';
            """)
        )

        # Count active nonces under advisory lock
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

        # 6. Generate transaction tokens and cryptographic browser session binding
        nonce = secrets.token_urlsafe(32)
        resolved_session_token = session_token or secrets.token_urlsafe(32)
        session_binding_hash = hashlib.sha256(resolved_session_token.encode("utf-8")).hexdigest()

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
                "state": clean_state,
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
        logger.error(f"Database error during nonce persistence: {exc}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="IDP_AUTH_SERVICE_UNAVAILABLE: Database operation failed during nonce persistence.",
        )

    return {
        "nonce": nonce,
        "expires_in": NONCE_TTL_SECONDS,
        "org_id": org_id,
        "idp_provider": provider_clean,
        "state": clean_state,
        "session_token": resolved_session_token,
        "code_challenge": clean_code_challenge,
    }


def consume_idp_login_nonce(
    db: Session,
    nonce: str,
    org_id: str,
    idp_provider: str,
    email: str,
    session_token: str,
    state: str,
    code_verifier: str,
) -> bool:
    """
    Atomically consumes a single-use authentication nonce within a database transaction.
    Enforces RLS tenant isolation, HttpOnly browser session binding, and constant-time PKCE S256 verification.
    Guarantees that missing, mismatched, expired, or duplicate nonces strictly fail closed.
    """
    if not nonce or not str(nonce).strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="MISSING_IDP_NONCE: Authentication nonce is required.",
        )

    # 1. Validate mandatory State parameter
    if not state or not str(state).strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="MISSING_STATE: Authentication transaction requires state parameter.",
        )
    clean_state = str(state).strip()

    # 2. Validate mandatory PKCE code_verifier (RFC 7636 unreserved: 43-128 chars)
    if not code_verifier or not str(code_verifier).strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="MISSING_PKCE_VERIFIER: Authentication transaction requires a PKCE code_verifier.",
        )
    clean_code_verifier = str(code_verifier).strip()
    if len(clean_code_verifier) < 43 or len(clean_code_verifier) > 128:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="INVALID_PKCE_VERIFIER: PKCE code_verifier length must be between 43 and 128 characters.",
        )
    if not re.match(r"^[A-Za-z0-9_.~-]{43,128}$", clean_code_verifier):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="INVALID_PKCE_VERIFIER: PKCE code_verifier contains invalid characters. Must contain only RFC 7636 unreserved characters.",
        )

    # 3. Validate mandatory HttpOnly Session Token
    if not session_token or not str(session_token).strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="MISSING_SESSION_BINDING: Authentication transaction requires initiating browser session binding cookie.",
        )
    clean_session_token = str(session_token).strip()

    nonce_clean = str(nonce).strip()
    provider_clean = (idp_provider or "").strip().lower()

    # 4. Establish trusted tenant context for PostgreSQL RLS before any database access
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

            # 5. Constant-time Browser session binding validation (anti-CSRF / session fixation defense)
            if not db_session_hash:
                db.rollback()
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="CORRUPTED_NONCE_RECORD: Nonce record lacks session binding hash.",
                )
            computed_session_hash = hashlib.sha256(clean_session_token.encode("utf-8")).hexdigest()
            if not hmac.compare_digest(computed_session_hash, db_session_hash):
                db.rollback()
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="SESSION_BINDING_MISMATCH: Authentication transaction does not match the initiating browser session.",
                )

            # 6. Constant-time PKCE S256 verification
            if not db_code_challenge:
                db.rollback()
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="CORRUPTED_NONCE_RECORD: Nonce record lacks PKCE code_challenge.",
                )
            # S256: BASE64URL-ENCODE(SHA256(ASCII(code_verifier)))
            try:
                verifier_bytes = clean_code_verifier.encode("ascii")
            except UnicodeEncodeError:
                db.rollback()
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="INVALID_PKCE_VERIFIER: PKCE code_verifier contains invalid characters.",
                )
            digest = hashlib.sha256(verifier_bytes).digest()
            computed_challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
            if not hmac.compare_digest(computed_challenge, db_code_challenge):
                db.rollback()
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="INVALID_PKCE_VERIFIER: PKCE code_verifier does not match transaction code_challenge.",
                )

            # 7. Constant-time State parameter validation
            if not db_state or not hmac.compare_digest(clean_state, db_state):
                db.rollback()
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="STATE_MISMATCH: OIDC transaction state does not match expected state.",
                )

            db.commit()
            return True

        # Exactly 0 rows were updated. Investigate failure mode for security audit.
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
