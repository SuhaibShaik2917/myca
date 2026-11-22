from typing import Optional, List
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.dependencies import get_tenant_db
from app.modules.scheme_navigator.schemas import (
    GovernmentSchemeOut, BusinessProfileIn, BusinessProfileOut,
    SchemeMatchOut, SchemeChecklistOut
)
from app.modules.scheme_navigator.service import (
    list_government_schemes, get_or_create_business_profile,
    upsert_business_profile, evaluate_and_persist_matches,
    get_checklist_for_scheme
)

router = APIRouter(prefix="/scheme-navigator", tags=["Milestone 5: Scheme Navigator"])

@router.get("/schemes", response_model=List[GovernmentSchemeOut])
def list_schemes_endpoint(
    category: Optional[str] = Query(None, description="Filter by scheme_category"),
    db: Session = Depends(get_tenant_db)
):
    """Lists all active Indian government MSME schemes and eligibility criteria."""
    try:
        return list_government_schemes(db, category)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/evaluate", response_model=List[SchemeMatchOut])
def evaluate_eligibility_endpoint(
    profile_data: Optional[BusinessProfileIn] = None,
    x_org_id: Optional[str] = Header(None, alias="X-Org-Id"),
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
    db: Session = Depends(get_tenant_db)
):
    """
    Evaluates enterprise profile against all government schemes deterministically.
    Updates or inserts match records in scheme_matches.
    """
    effective_org = x_org_id or db.info.get("current_org")
    effective_user = x_user_id or db.info.get("current_user")
    try:
        return evaluate_and_persist_matches(
            session=db,
            org_id=effective_org,
            user_id=effective_user,
            profile_override=profile_data
        )
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Scheme evaluation failed: {str(e)}")

@router.get("/checklist/{scheme_code}", response_model=SchemeChecklistOut)
def get_checklist_endpoint(scheme_code: str):
    """Retrieves required documents, step-by-step instructions, and portal links."""
    checklist = get_checklist_for_scheme(scheme_code)
    if not checklist:
        raise HTTPException(status_code=404, detail=f"Scheme with code '{scheme_code}' not found.")
    return checklist

@router.get("/profile", response_model=BusinessProfileOut)
def get_profile_endpoint(
    x_org_id: Optional[str] = Header(None, alias="X-Org-Id"),
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
    db: Session = Depends(get_tenant_db)
):
    """Fetches the current MSME profile for scheme evaluation."""
    effective_org = x_org_id or db.info.get("current_org")
    effective_user = x_user_id or db.info.get("current_user")
    try:
        return get_or_create_business_profile(db, effective_org, effective_user)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/profile", response_model=BusinessProfileOut)
def update_profile_endpoint(
    profile_in: BusinessProfileIn,
    x_org_id: Optional[str] = Header(None, alias="X-Org-Id"),
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
    db: Session = Depends(get_tenant_db)
):
    """Updates business profile (turnover, sector, Udyam registration)."""
    effective_org = x_org_id or db.info.get("current_org")
    effective_user = x_user_id or db.info.get("current_user")
    try:
        return upsert_business_profile(db, effective_org, effective_user, profile_in)
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
