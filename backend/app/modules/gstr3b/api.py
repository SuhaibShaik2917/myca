from typing import Optional, Dict, Any
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.dependencies import get_tenant_db, validate_tenant_identity, require_roles
from app.modules.gstr3b.schemas import (
    GSTR3BPrepareRequest, MakerCheckerSubmitRequest, MakerCheckerDecisionRequest,
    GSTR3BFilingResponse, GSTR3BStatusResponse
)
from app.modules.gstr3b.service import (
    prepare_gstr3b_return, submit_gstr3b_for_review, review_gstr3b_decision,
    file_gstr3b_to_gsp_sandbox, get_gstr3b_status, get_gstr3b_revision_history,
    GSTR3BServiceError, GSPIntegrationUnavailableError,
)

router = APIRouter(prefix="/gstr3b", tags=["Milestone 3: GSTR-3B Filing & Offset Engine"])

@router.post("/prepare")
def prepare_return_endpoint(
    payload: GSTR3BPrepareRequest,
    x_org_id: Optional[str] = Header(None, alias="X-Org-Id"),
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
    db: Session = Depends(require_roles("owner", "admin", "accountant"))
):
    """
    Stage 1: Prepares GSTR-3B Table 3.1 & Table 4 and executes Rule 88A tax offset.
    Persists DRAFT filing record in public.gstr3b_filings.
    """
    effective_org, effective_user = validate_tenant_identity(db, x_org_id, x_user_id)
    try:
        res = prepare_gstr3b_return(
            session=db,
            org_id=effective_org,
            registration_id=payload.registration_id,
            tax_period=payload.tax_period,
            user_id=effective_user,
            snapshot_id=payload.snapshot_id
        )
        return res
    except GSPIntegrationUnavailableError as e:
        db.rollback()
        raise HTTPException(status_code=503, detail=str(e))
    except GSTR3BServiceError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to prepare GSTR-3B: {str(e)}")

@router.get("/status")
def get_return_status_endpoint(
    registration_id: str = Query(...),
    tax_period: str = Query(...),
    x_org_id: Optional[str] = Header(None, alias="X-Org-Id"),
    db: Session = Depends(require_roles("owner", "admin", "accountant", "viewer"))
):
    """Retrieves current return state for a registration and tax period."""
    effective_org, _ = validate_tenant_identity(db, x_org_id, None)
    try:
        res = get_gstr3b_status(
            session=db,
            org_id=effective_org,
            registration_id=registration_id,
            tax_period=tax_period
        )
        if not res:
            raise HTTPException(status_code=404, detail="No GSTR-3B return prepared for this period.")
        return res
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to retrieve GSTR-3B status: {str(e)}")

@router.post("/{filing_id}/submit-review")
def submit_for_review_endpoint(
    filing_id: str,
    payload: MakerCheckerSubmitRequest = MakerCheckerSubmitRequest(),
    x_org_id: Optional[str] = Header(None, alias="X-Org-Id"),
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
    db: Session = Depends(require_roles("owner", "admin", "accountant"))
):
    """
    Maker-Checker Stage 2: Maker submits return for checker statutory review.
    Transitions DRAFT -> SUBMITTED_FOR_REVIEW.
    """
    effective_org, effective_user = validate_tenant_identity(db, x_org_id, x_user_id)
    try:
        return submit_gstr3b_for_review(
            session=db,
            org_id=effective_org,
            filing_id=filing_id,
            user_id=effective_user,
            remarks=payload.remarks
        )
    except GSPIntegrationUnavailableError as e:
        db.rollback()
        raise HTTPException(status_code=503, detail=str(e))
    except GSTR3BServiceError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/{filing_id}/review-decision")
def review_decision_endpoint(
    filing_id: str,
    payload: MakerCheckerDecisionRequest,
    x_org_id: Optional[str] = Header(None, alias="X-Org-Id"),
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
    db: Session = Depends(require_roles("owner", "admin", "accountant"))
):
    """
    Maker-Checker Stage 3: Checker / CA approves or rejects the return.
    Transitions SUBMITTED_FOR_REVIEW -> APPROVED_BY_CHECKER or REJECTED_BY_CHECKER.
    """
    effective_org, effective_user = validate_tenant_identity(db, x_org_id, x_user_id)
    try:
        return review_gstr3b_decision(
            session=db,
            org_id=effective_org,
            filing_id=filing_id,
            checker_id=effective_user,
            decision=payload.decision,
            remarks=payload.remarks
        )
    except GSPIntegrationUnavailableError as e:
        db.rollback()
        raise HTTPException(status_code=503, detail=str(e))
    except GSTR3BServiceError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/{filing_id}/file", response_model=GSTR3BFilingResponse)
def file_to_gsp_sandbox_endpoint(
    filing_id: str,
    x_org_id: Optional[str] = Header(None, alias="X-Org-Id"),
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
    db: Session = Depends(require_roles("owner", "admin"))
):
    """
    Maker-Checker Stage 4: Submits checker-approved return to GSP/GSTN Sandbox.
    Generates realistic ARN (Application Reference Number) and CIN (Challan ID Number).
    Locks return as FILED and creates compliance entry in public.gst_records.
    """
    effective_org, effective_user = validate_tenant_identity(db, x_org_id, x_user_id)
    try:
        res = file_gstr3b_to_gsp_sandbox(
            session=db,
            org_id=effective_org,
            filing_id=filing_id,
            user_id=effective_user
        )
        return res
    except GSPIntegrationUnavailableError as e:
        db.rollback()
        raise HTTPException(status_code=503, detail=str(e))
    except GSTR3BServiceError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{filing_id}/revision-history")
def get_revision_history_endpoint(
    filing_id: str,
    x_org_id: Optional[str] = Header(None, alias="X-Org-Id"),
    db: Session = Depends(require_roles("owner", "admin", "accountant", "viewer"))
):
    """Retrieves durable append-only revision history for a GSTR-3B filing."""
    effective_org, _ = validate_tenant_identity(db, x_org_id, None)
    try:
        return get_gstr3b_revision_history(
            session=db,
            org_id=effective_org,
            filing_id=filing_id
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to retrieve revision history: {str(e)}")
