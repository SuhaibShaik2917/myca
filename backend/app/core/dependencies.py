from fastapi import Header, HTTPException, Depends
from sqlalchemy.orm import Session
from typing import Optional, Generator
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
    header_user: Optional[str] = None
) -> tuple[str, Optional[str]]:
    """
    Derives verified tenant identity strictly from session context and rejects
    any conflicting caller-supplied headers.
    """
    effective_org = db.info.get("current_org")
    effective_user = db.info.get("current_user")

    if not effective_org:
        raise HTTPException(
            status_code=401,
            detail="Missing or unverified tenant organization context"
        )

    if header_org and str(header_org).strip() != str(effective_org):
        raise HTTPException(
            status_code=403,
            detail="Cross-tenant header tampering detected: X-Org-Id does not match authenticated organization context."
        )

    if header_user and effective_user and str(header_user).strip() != str(effective_user):
        raise HTTPException(
            status_code=403,
            detail="Cross-user header tampering detected: X-User-Id does not match authenticated user context."
        )

    return str(effective_org), str(effective_user) if effective_user else None


def get_tenant_db(
    authorization: Optional[str] = Header(None, alias="Authorization"),
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
    x_org_id: Optional[str] = Header(None, alias="X-Org-Id"),
) -> Generator[Session, None, None]:
    """
    FastAPI dependency that:
    1. Extracts authenticated user_id and target org_id from request context.
    2. Enforces tamper protection: caller-supplied headers MUST NOT conflict with token claims.
    3. Enforces server-side validation of active membership.
    4. Sets app.current_org and app.current_user in the database session and locks session to tenant.
    5. Enforces least-privilege PostgreSQL Row Level Security.
    """
    raw_user_id = x_user_id
    raw_org_id = x_org_id

    # Preferred path: verified Bearer token.
    if authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ", 1)[1].strip()
        try:
            claims = decode_access_token(token)
            token_user_id = claims["sub"]
            token_org_id = claims["org_id"]
        except ValueError:
            raise HTTPException(status_code=401, detail="Invalid or expired access token")

        # TAMPER DEFENSE: If caller supplied X-Org-Id or X-User-Id, it MUST NOT conflict with token claims
        if raw_org_id and str(raw_org_id).strip() != str(token_org_id):
            raise HTTPException(
                status_code=403,
                detail="Cross-tenant header tampering detected: X-Org-Id does not match Bearer token identity."
            )
        if raw_user_id and str(raw_user_id).strip() != str(token_user_id):
            raise HTTPException(
                status_code=403,
                detail="Cross-user header tampering detected: X-User-Id does not match Bearer token identity."
            )

        x_user_id = token_user_id
        x_org_id = token_org_id
    elif settings.ENVIRONMENT in ("production", "staging"):
        raise HTTPException(status_code=401, detail="Bearer access token required")

    if not x_user_id:
        raise HTTPException(status_code=401, detail="Missing required X-User-Id authentication header")
    if not x_org_id:
        raise HTTPException(status_code=400, detail="Missing required X-Org-Id tenant header")

    try:
        uuid.UUID(x_user_id)
        uuid.UUID(x_org_id)
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=401, detail="Invalid X-User-Id or X-Org-Id header")

    db = SessionLocal()
    try:
        # Server-side verification of active membership before applying GUC
        if not verify_and_resolve_tenant_membership(db, x_user_id, x_org_id):
            raise HTTPException(
                status_code=403,
                detail="User does not possess active membership in the requested organization"
            )

        # Set tenant context and lock session info
        db.info["current_org"] = x_org_id
        db.info["current_user"] = x_user_id
        set_tenant_context(db, org_id=x_org_id, user_id=x_user_id)
        yield db
    finally:
        db.close()
