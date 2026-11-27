from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import text
from sqlalchemy.orm import Session
from typing import Optional, Generator
import os
import uuid

from app.config import settings
from app.core.security import decode_access_token
from app.database import SessionLocal, set_tenant_context, verify_and_resolve_tenant_membership


class TenantContext:
    def __init__(self, user_id: str, org_id: str, role: str):
        self.user_id = user_id
        self.org_id = org_id
        self.role = role


def validate_tenant_identity(
    db: Session,
    header_org: Optional[str] = None,
    header_user: Optional[str] = None,
) -> tuple[str, Optional[str]]:
    """
    Derives verified tenant identity strictly from session context and rejects
    any conflicting caller-supplied headers.
    """
    effective_org = db.info.get("current_org")
    effective_user = db.info.get("current_user")

    if not effective_org:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or unverified tenant organization context",
        )

    if header_org and str(header_org).strip() != str(effective_org):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cross-tenant header tampering detected: X-Org-Id does not match authenticated organization context.",
        )

    if header_user and effective_user and str(header_user).strip() != str(effective_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cross-user header tampering detected: X-User-Id does not match authenticated user context.",
        )

    return str(effective_org), str(effective_user) if effective_user else None




    


def get_tenant_db(
    authorization: Optional[str] = Header(None, alias="Authorization"),
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
    x_org_id: Optional[str] = Header(None, alias="X-Org-Id"),
) -> Generator[Session, None, None]:
    """
    FastAPI dependency that:
    1. Extracts authenticated user_id and target org_id from Bearer JWT (case-insensitive scheme),
       or rejects/falls back only when Authorization header is absent in designated local/test mode.
    2. Enforces tamper protection: caller-supplied headers MUST NOT conflict with token claims.
    3. Enforces server-side validation of active user account & active organization membership.
    4. Checks token revocation registry (jti) under tenant RLS.
    5. Sets app.current_org and app.current_user in the database session and locks session to tenant.
    """
    raw_user_id = x_user_id
    raw_org_id = x_org_id
    token_jti: Optional[str] = None
    token_exp = None

    if authorization is not None and authorization.strip():
        parts = authorization.strip().split(None, 1)
        if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid Authorization header scheme; Bearer token required",
            )
        token = parts[1].strip()
        try:
            claims = decode_access_token(token)
            token_user_id = claims["sub"]
            token_org_id = claims["org_id"]
            token_jti = str(claims.get("jti") or "")
            token_exp = claims.get("exp")
        except ValueError:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired access token")

        if raw_org_id and str(raw_org_id).strip() != str(token_org_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Cross-tenant header tampering detected: X-Org-Id does not match Bearer token identity.",
            )
        if raw_user_id and str(raw_user_id).strip() != str(token_user_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Cross-user header tampering detected: X-User-Id does not match Bearer token identity.",
            )

        x_user_id = token_user_id
        x_org_id = token_org_id
    elif (
        (os.getenv("ENVIRONMENT") or "").strip().lower() in ("production", "staging")
        or (getattr(settings, "ENVIRONMENT", None) or "").strip().lower() in ("production", "staging")
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Bearer access token required")
    elif not getattr(settings, "ALLOW_DEV_HEADER_AUTH", False):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Header-based development authentication is disabled. Bearer access token required.",
        )

    if not x_user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing required X-User-Id authentication header")
    if not x_org_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing required X-Org-Id tenant header")

    try:
        uuid.UUID(x_user_id)
        uuid.UUID(x_org_id)
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid X-User-Id or X-Org-Id header")

    db = SessionLocal()
    try:
        if not verify_and_resolve_tenant_membership(db, x_user_id, x_org_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User does not possess active membership in the requested organization",
            )

        db.info["current_org"] = x_org_id
        db.info["current_user"] = x_user_id
        if token_jti:
            db.info["current_jti"] = token_jti
        if token_exp is not None:
            db.info["current_exp"] = token_exp
        set_tenant_context(db, org_id=x_org_id, user_id=x_user_id)

        if token_jti:
            revoked = db.execute(
                text("SELECT 1 FROM public.auth_revoked_tokens WHERE jti = :jti LIMIT 1;"),
                {"jti": token_jti},
            ).scalar()
            if revoked:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="TOKEN_REVOKED: Session token has been revoked.",
                )

        yield db
    finally:
        db.close()



def get_current_tenant_context(db: Session = Depends(get_tenant_db)) -> TenantContext:
    """Typed accessor resolving verified tenant context from active request DB session."""
    return TenantContext(
        user_id=str(db.info.get("current_user") or ""),
        org_id=str(db.info.get("current_org") or ""),
        role=str(db.info.get("current_role") or ""),
    )

def require_roles(*allowed_roles: str):
    """Enforces organization RBAC role authorization derived from verified membership."""
    norm_allowed = {r.strip().lower() for r in allowed_roles if r and r.strip()}

    def _rbac_guard(db: Session = Depends(get_tenant_db)) -> Session:
        role = str(db.info.get("current_role") or "").strip().lower()
        if role not in norm_allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"INSUFFICIENT_ROLE_PERMISSION: Action requires roles {sorted(norm_allowed)}; current role is '{role or 'none'}'.",
            )
        return db

    return _rbac_guard
