import uuid
from typing import Optional, List
from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException, Header, Query
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.core.dependencies import get_tenant_db, require_roles, validate_tenant_identity
from app.modules.invoice_brain.service import (
    upload_and_enqueue_invoice, update_invoice_fields, update_line_item,
    approve_invoice, reject_invoice, DuplicateDocumentError,
    InvoiceStatusError, InvoiceBrainError
)
from app.modules.invoice_brain.validator import validate_invoice_financials
from app.modules.invoice_brain.gstr import prepare_gstr1_draft, prepare_gstr3b_draft
from app.schemas.invoice_brain import (
    InvoiceUploadResponse, InvoiceOut, InvoicePatch,
    LineItemUpdate, LineItemOut, InvoiceApprovalRequest,
    InvoiceRejectionRequest, GSTR1DraftResponse, GSTR3BDraftResponse
)

router = APIRouter(prefix="", tags=["Invoice Brain"])


def _parse_uuid(value: str, field_name: str = "id") -> str:
    try:
        return str(uuid.UUID(str(value).strip()))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=400, detail=f"INVALID_UUID: '{field_name}' must be a valid UUID.")


@router.post("/invoices/upload", response_model=InvoiceUploadResponse)
def upload_invoice(
    profile_id: str = Form(...),
    file: UploadFile = File(...),
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
    x_org_id: Optional[str] = Header(None, alias="X-Org-Id"),
    db: Session = Depends(require_roles("owner", "admin", "accountant")),
):
    effective_org, effective_user = validate_tenant_identity(db, x_org_id, x_user_id)
    clean_profile_id = _parse_uuid(profile_id, "profile_id")
    try:
        prof_stmt = text("SELECT id FROM public.business_profiles WHERE id = :id AND org_id = :org;")
        prof = db.execute(prof_stmt, {"id": clean_profile_id, "org": effective_org}).fetchone()
        if not prof:
            raise HTTPException(status_code=404, detail="Business profile not found for this organization")

        response = upload_and_enqueue_invoice(
            session=db,
            org_id=effective_org,
            user_id=effective_user,
            profile_id=clean_profile_id,
            file_stream=file.file,
            file_name=file.filename or "uploaded_invoice.pdf",
        )
        db.commit()
        return response

    except DuplicateDocumentError as e:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(e))
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"Upload failed: {str(e)}")


@router.get("/invoices", response_model=List[InvoiceOut])
def list_invoices(
    status: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    x_org_id: Optional[str] = Header(None, alias="X-Org-Id"),
    db: Session = Depends(require_roles("owner", "admin", "accountant", "viewer")),
):
    effective_org, _ = validate_tenant_identity(db, x_org_id, None)
    query = (
        "SELECT id, org_id, profile_id, invoice_number, invoice_date, due_date, "
        "counterparty_name, counterparty_gstin, total_taxable_amount, cgst_amount, "
        "sgst_amount, igst_amount, total_amount, currency, status, rejection_reason, "
        "document_id, approved_by, approved_at, export_ready_at, created_at, updated_at "
        "FROM public.invoices WHERE org_id = :org_id"
    )
    params = {"org_id": effective_org, "limit": limit, "offset": offset}
    if status:
        query += " AND status = :status"
        params["status"] = status
    query += " ORDER BY created_at DESC LIMIT :limit OFFSET :offset;"

    rows = db.execute(text(query), params).mappings().fetchall()
    return [InvoiceOut(**dict(r), line_items=[]) for r in rows]


@router.get("/invoices/{invoice_id}", response_model=InvoiceOut)
def get_invoice_details(
    invoice_id: str,
    x_org_id: Optional[str] = Header(None, alias="X-Org-Id"),
    db: Session = Depends(require_roles("owner", "admin", "accountant", "viewer")),
):
    effective_org, _ = validate_tenant_identity(db, x_org_id, None)
    clean_id = _parse_uuid(invoice_id, "invoice_id")
    inv_stmt = text("""
        SELECT
            i.id, i.org_id, i.profile_id, i.invoice_number, i.invoice_date,
            i.due_date, i.counterparty_name, i.counterparty_gstin,
            i.total_taxable_amount, i.cgst_amount, i.sgst_amount,
            i.igst_amount, i.total_amount, i.currency, i.status,
            i.rejection_reason, i.document_id, i.approved_by,
            i.approved_at, i.export_ready_at, i.created_at, i.updated_at,
            bp.gstin as supplier_gstin,
            bp.preceding_fy_aato as preceding_fy_aato
        FROM public.invoices i
        JOIN public.business_profiles bp ON bp.id = i.profile_id
        WHERE i.id = :id AND i.org_id = :org_id;
    """)
    inv = db.execute(inv_stmt, {"id": clean_id, "org_id": effective_org}).mappings().fetchone()
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")

    lines_stmt = text("""
        SELECT id, invoice_id, org_id, item_description, hsn_sac_code,
               quantity, unit_price, taxable_amount, gst_rate,
               cgst_amount, sgst_amount, igst_amount, total_amount,
               extraction_confidence, created_at
        FROM public.invoice_line_items
        WHERE invoice_id = :id AND org_id = :org_id
        ORDER BY created_at ASC;
    """)
    lines = db.execute(lines_stmt, {"id": clean_id, "org_id": effective_org}).mappings().fetchall()
    line_models = [LineItemOut(**dict(line)) for line in lines]

    from decimal import Decimal
    raw_aato = inv.get("preceding_fy_aato")
    aato_crores = (Decimal(str(raw_aato)) / Decimal("10000000")) if raw_aato is not None else Decimal("5.0")

    val_report = validate_invoice_financials(
        invoice=dict(inv),
        line_items=[dict(l) for l in lines],
        supplier_gstin=inv["supplier_gstin"],
        aato_crores=aato_crores,
    )

    inv_dict = dict(inv)
    inv_dict.pop("supplier_gstin", None)
    inv_dict.pop("preceding_fy_aato", None)
    return InvoiceOut(**inv_dict, line_items=line_models, validation_report=val_report)


@router.patch("/invoices/{invoice_id}")
def patch_invoice(
    invoice_id: str,
    patch: InvoicePatch,
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
    x_org_id: Optional[str] = Header(None, alias="X-Org-Id"),
    db: Session = Depends(require_roles("owner", "admin", "accountant")),
):
    effective_org, effective_user = validate_tenant_identity(db, x_org_id, x_user_id)
    clean_id = _parse_uuid(invoice_id, "invoice_id")
    try:
        result = update_invoice_fields(
            session=db,
            org_id=effective_org,
            user_id=effective_user,
            invoice_id=clean_id,
            patch=patch,
        )
        db.commit()
        return result
    except InvoiceStatusError as e:
        db.rollback()
        raise HTTPException(status_code=403, detail=str(e))
    except InvoiceBrainError as e:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))


@router.patch("/invoices/{invoice_id}/line-items/{item_id}")
def patch_line_item(
    invoice_id: str,
    item_id: str,
    patch: LineItemUpdate,
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
    x_org_id: Optional[str] = Header(None, alias="X-Org-Id"),
    db: Session = Depends(require_roles("owner", "admin", "accountant")),
):
    effective_org, effective_user = validate_tenant_identity(db, x_org_id, x_user_id)
    clean_inv_id = _parse_uuid(invoice_id, "invoice_id")
    clean_item_id = _parse_uuid(item_id, "item_id")
    try:
        result = update_line_item(
            session=db,
            org_id=effective_org,
            user_id=effective_user,
            invoice_id=clean_inv_id,
            item_id=clean_item_id,
            patch=patch,
        )
        db.commit()
        return result
    except InvoiceStatusError as e:
        db.rollback()
        raise HTTPException(status_code=403, detail=str(e))
    except InvoiceBrainError as e:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/invoices/{invoice_id}/approve")
def approve_invoice_endpoint(
    invoice_id: str,
    request: InvoiceApprovalRequest = InvoiceApprovalRequest(),
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
    x_org_id: Optional[str] = Header(None, alias="X-Org-Id"),
    db: Session = Depends(require_roles("owner", "admin")),
):
    effective_org, effective_user = validate_tenant_identity(db, x_org_id, x_user_id)
    clean_id = _parse_uuid(invoice_id, "invoice_id")
    try:
        result = approve_invoice(
            session=db,
            org_id=effective_org,
            user_id=effective_user,
            invoice_id=clean_id,
        )
        db.commit()
        return result
    except InvoiceStatusError as e:
        db.commit()
        raise HTTPException(status_code=400, detail=str(e))
    except InvoiceBrainError as e:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"Approval failed: {str(e)}")


@router.post("/invoices/{invoice_id}/reject")
def reject_invoice_endpoint(
    invoice_id: str,
    request: InvoiceRejectionRequest,
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
    x_org_id: Optional[str] = Header(None, alias="X-Org-Id"),
    db: Session = Depends(require_roles("owner", "admin")),
):
    effective_org, effective_user = validate_tenant_identity(db, x_org_id, x_user_id)
    clean_id = _parse_uuid(invoice_id, "invoice_id")
    try:
        result = reject_invoice(
            session=db,
            org_id=effective_org,
            user_id=effective_user,
            invoice_id=clean_id,
            reason=request.reason,
        )
        db.commit()
        return result
    except InvoiceStatusError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    except InvoiceBrainError as e:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"Rejection failed: {str(e)}")


@router.get("/invoices/tasks/{task_id}")
def get_task_status_endpoint(
    task_id: str,
    x_org_id: Optional[str] = Header(None, alias="X-Org-Id"),
    db: Session = Depends(require_roles("owner", "admin", "accountant", "viewer")),
):
    effective_org, _ = validate_tenant_identity(db, x_org_id, None)
    clean_task_id = _parse_uuid(task_id, "task_id")
    stmt = text("""
        SELECT id, task_type, status, created_at, updated_at
        FROM public.v_tenant_task_status
        WHERE id = :id AND org_id = :org;
    """)
    task = db.execute(stmt, {"id": clean_task_id, "org": effective_org}).mappings().fetchone()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return dict(task)


@router.get("/gst/gstr1-draft", response_model=GSTR1DraftResponse)
def get_gstr1_draft(
    period: str = Query(..., pattern=r"^\d{4}-\d{2}$", description="Tax period YYYY-MM"),
    profile_id: Optional[str] = Query(None, description="Optional business profile ID to scope GSTIN"),
    x_org_id: Optional[str] = Header(None, alias="X-Org-Id"),
    db: Session = Depends(require_roles("owner", "admin", "accountant", "viewer")),
):
    effective_org, _ = validate_tenant_identity(db, x_org_id, None)
    clean_profile = _parse_uuid(profile_id, "profile_id") if profile_id else None
    return prepare_gstr1_draft(session=db, org_id=effective_org, tax_period=period, profile_id=clean_profile)


@router.get("/gst/gstr3b-draft", response_model=GSTR3BDraftResponse)
def get_gstr3b_draft(
    period: str = Query(..., pattern=r"^\d{4}-\d{2}$", description="Tax period YYYY-MM"),
    profile_id: Optional[str] = Query(None, description="Optional business profile ID to scope GSTIN"),
    x_org_id: Optional[str] = Header(None, alias="X-Org-Id"),
    db: Session = Depends(require_roles("owner", "admin", "accountant", "viewer")),
):
    effective_org, _ = validate_tenant_identity(db, x_org_id, None)
    clean_profile = _parse_uuid(profile_id, "profile_id") if profile_id else None
    return prepare_gstr3b_draft(session=db, org_id=effective_org, tax_period=period, profile_id=clean_profile)
