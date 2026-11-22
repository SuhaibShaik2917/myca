import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

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


@router.post("/demo-token", response_model=DemoTokenResponse)
def issue_demo_token(payload: DemoTokenRequest) -> DemoTokenResponse:
    """
    Hackathon/demo token issuer.

    Verifies active organization membership, then issues a short-lived signed
    access token. This is not a password login; production should use a real
    identity provider and remove the development header fallback.
    """
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
