from typing import Optional, Dict, Any, List
from fastapi import APIRouter, Depends, Header, HTTPException, UploadFile, File, Form
from sqlalchemy.orm import Session

from app.core.dependencies import get_tenant_db, validate_tenant_identity, require_roles
from app.modules.credit_builder.schemas import (
    StatementUploadResponse, FinancialSummaryOut,
    FinancialReadinessScore, LoanDossierOut
)
from app.modules.credit_builder.service import (
    ingest_statement_csv, get_financial_summary,
    compute_financial_readiness_score, generate_loan_dossier
)

router = APIRouter(prefix="/credit-builder", tags=["Milestone 4: Credit Builder"])

MAX_CSV_UPLOAD_BYTES = 10 * 1024 * 1024


@router.post("/statements/upload", response_model=StatementUploadResponse)
async def upload_bank_statement_endpoint(
    file: Optional[UploadFile] = File(None),
    csv_text: Optional[str] = Form(None),
    x_org_id: Optional[str] = Header(None, alias="X-Org-Id"),
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
    db: Session = Depends(require_roles("owner", "admin", "accountant"))
):
    """
    Ingests and auto-categorizes bank or UPI statement CSV.
    Supports file upload or direct text paste.
    """
    effective_org, effective_user = validate_tenant_identity(db, x_org_id, x_user_id)

    content = ""
    if file:
        content_bytes = await file.read(MAX_CSV_UPLOAD_BYTES + 1)
        if len(content_bytes) > MAX_CSV_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="Statement CSV file exceeds 10 MB limit.")
        content = content_bytes.decode("utf-8", errors="replace")
    elif csv_text:
        if len(csv_text.encode("utf-8", errors="ignore")) > MAX_CSV_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="Statement CSV text exceeds 10 MB limit.")
        content = csv_text
    else:
        raise HTTPException(status_code=400, detail="Must provide either a statement file or csv_text.")

    try:
        return ingest_statement_csv(
            session=db,
            org_id=effective_org,
            user_id=effective_user,
            csv_content=content
        )
    except ValueError as ve:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Statement ingestion failed: {str(e)}")

@router.get("/summary", response_model=FinancialSummaryOut)
def get_summary_endpoint(
    x_org_id: Optional[str] = Header(None, alias="X-Org-Id"),
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
    db: Session = Depends(require_roles("owner", "admin", "accountant", "viewer"))
):
    """Retrieves monthly cash flow trends, revenue, and categorized expenditure."""
    effective_org, effective_user = validate_tenant_identity(db, x_org_id, x_user_id)
    try:
        return get_financial_summary(db, effective_org, effective_user)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/readiness-score", response_model=FinancialReadinessScore)
def get_readiness_score_endpoint(
    x_org_id: Optional[str] = Header(None, alias="X-Org-Id"),
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
    db: Session = Depends(require_roles("owner", "admin", "accountant", "viewer"))
):
    """
    Calculates deterministic MSME Financial Readiness Index (scale 300-900)
    with factor-by-factor breakdown.
    """
    effective_org, effective_user = validate_tenant_identity(db, x_org_id, x_user_id)
    try:
        return compute_financial_readiness_score(db, effective_org, effective_user)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/loan-dossier", response_model=LoanDossierOut)
def get_loan_dossier_endpoint(
    x_org_id: Optional[str] = Header(None, alias="X-Org-Id"),
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
    db: Session = Depends(require_roles("owner", "admin", "accountant", "viewer"))
):
    """Generates structured loan readiness dossier for bank and NBFC submissions."""
    effective_org, effective_user = validate_tenant_identity(db, x_org_id, x_user_id)
    try:
        return generate_loan_dossier(db, effective_org, effective_user)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
