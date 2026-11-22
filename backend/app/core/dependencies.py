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

def get_tenant_db(
    authorization: Optional[str] = Header(None, alias="Authorization"),
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
    x_org_id: Optional[str] = Header(None, alias="X-Org-Id"),
) -> Generator[Session, None, None]:
    """
    FastAPI dependency that:
    1. Extracts authenticated user_id and target org_id from request context.
    2. Enforces server-side validation of active membership.
    3. Sets app.current_org and app.current_user in the database session.
    4. Enforces least-privilege PostgreSQL Row Level Security.
    """
    # Preferred path: verified Bearer token. Development/test fallback: identity headers.
    if authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ", 1)[1].strip()
        try:
            claims = decode_access_token(token)
            x_user_id = claims["sub"]
            x_org_id = claims["org_id"]
        except ValueError:
            raise HTTPException(status_code=401, detail="Invalid or expired access token")
    elif settings.ENVIRONMENT == "production":
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

        set_tenant_context(db, org_id=x_org_id, user_id=x_user_id)
        db.info["current_org"] = x_org_id
        db.info["current_user"] = x_user_id
        yield db
    finally:
        db.close()
