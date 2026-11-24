import uuid
from typing import Optional, List
from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException, Header, Query
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.core.dependencies import get_tenant_db, validate_tenant_identity
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
    InvoiceRejectionRequest, ValidationReport,
    GSTR1DraftResponse, GSTR3BDraftResponse
)

router = APIRouter(prefix="", tags=["Invoice Brain"])

@router.post("/invoices/upload", response_model=InvoiceUploadResponse)
def upload_invoice(
    profile_id: str = Form(...),
    file: UploadFile = File(...),
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
    x_org_id: Optional[str] = Header(None, alias="X-Org-Id"),
    db: Session = Depends(get_tenant_db)
):
    """
    Stage 1: Multipart streaming invoice upload, SHA-256 deduplication,
    initial invoice record generation, and task queueing.
    """
    effective_org, effective_user = validate_tenant_identity(db, x_org_id, x_user_id)
    try:
        # Validate profile belongs to this org
        prof_stmt = text("SELECT id FROM public.business_profiles WHERE id = :id AND org_id = :org;")
        prof = db.execute(prof_stmt, {"id": profile_id, "org": effective_org}).fetchone()
        if not prof:
            raise HTTPException(status_code=404, detail="Business profile not found for this organization")

        response = upload_and_enqueue_invoice(
            session=db,
            org_id=effective_org,
            user_id=effective_user,
            profile_id=profile_id,
            file_stream=file.file,
            file_name=file.filename or "uploaded_invoice.pdf"
        )
        db.commit()
        return response

    except DuplicateDocumentError as e:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"Upload failed: {str(e)}")


@router.get("/invoices", response_model=List[InvoiceOut])
def list_invoices(
    status: Optional[str] = Query(None),
    x_org_id: Optional[str] = Header(None, alias="X-Org-Id"),
    db: Session = Depends(get_tenant_db)
):
    """
    List invoices for the tenant organization.
    """
    effective_org, _ = validate_tenant_identity(db, x_org_id, None)
    query = "SELECT id, org_id, profile_id, invoice_number, invoice_date, due_date, counterparty_name, counterparty_gstin, total_taxable_amount, cgst_amount, sgst_amount, igst_amount, total_amount, currency, status, rejection_reason, document_id, approved_by, approved_at, export_ready_at, created_at, updated_at FROM public.invoices WHERE org_id = :org_id"
    params = {"org_id": effective_org}
    if status:
        query += " AND status = :status"
        params["status"] = status
    query += " ORDER BY created_at DESC;"

    rows = db.execute(text(query), params).mappings().fetchall()
    results = []
    for r in rows:
        results.append(InvoiceOut(**dict(r), line_items=[]))
    return results


@router.get("/invoices/{invoice_id}", response_model=InvoiceOut)
def get_invoice_details(
    invoice_id: str,
    x_org_id: Optional[str] = Header(None, alias="X-Org-Id"),
    db: Session = Depends(get_tenant_db)
):
    """
    Stage 4: Review endpoint returning extracted fields, line items, and validation report side-by-side.
    """
    effective_org, _ = validate_tenant_identity(db, x_org_id, None)
    inv_stmt = text("""
        SELECT 
            i.id, i.org_id, i.profile_id, i.invoice_number, i.invoice_date,
            i.due_date, i.counterparty_name, i.counterparty_gstin,
            i.total_taxable_amount, i.cgst_amount, i.sgst_amount,
            i.igst_amount, i.total_amount, i.currency, i.status,
            i.rejection_reason, i.document_id, i.approved_by,
            i.approved_at, i.export_ready_at, i.created_at, i.updated_at,
            bp.gstin as supplier_gstin
        FROM public.invoices i
        JOIN public.business_profiles bp ON bp.id = i.profile_id
        WHERE i.id = :id AND i.org_id = :org_id;
    """)
    inv = db.execute(inv_stmt, {"id": invoice_id, "org_id": effective_org}).mappings().fetchone()
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")

    lines_stmt = text("""
        SELECT id, invoice_id, org_id, item_description, hsn_sac_code,
               quantity, unit_price, taxable_amount, gst_rate,
               cgst_amount, sgst_amount, igst_amount, total_amount, created_at
        FROM public.invoice_line_items
        WHERE invoice_id = :id AND org_id = :org_id
        ORDER BY created_at ASC;
    """)
    lines = db.execute(lines_stmt, {"id": invoice_id, "org_id": effective_org}).mappings().fetchall()
    line_models = [LineItemOut(**dict(line)) for line in lines]

    # Evaluate validation report
    val_report = validate_invoice_financials(
        invoice=dict(inv),
        line_items=[dict(l) for l in lines],
        supplier_gstin=inv["supplier_gstin"]
    )

    inv_dict = dict(inv)
    inv_dict.pop("supplier_gstin", None)
    return InvoiceOut(**inv_dict, line_items=line_models, validation_report=val_report)


@router.patch("/invoices/{invoice_id}")
def patch_invoice(
    invoice_id: str,
    patch: InvoicePatch,
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
    x_org_id: Optional[str] = Header(None, alias="X-Org-Id"),
    db: Session = Depends(get_tenant_db)
):
    """
    Stage 4: Field-level correction endpoint for human reviewers.
    Only permissible prior to approval.
    """
    effective_org, effective_user = validate_tenant_identity(db, x_org_id, x_user_id)
    try:
        result = update_invoice_fields(
            session=db,
            org_id=effective_org,
            user_id=effective_user,
            invoice_id=invoice_id,
            patch=patch
        )
        db.commit()
        return result
    except InvoiceStatusError as e:
        db.rollback()
        raise HTTPException(status_code=403, detail=str(e))
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
    db: Session = Depends(get_tenant_db)
):
    """
    Stage 4: Line item correction before approval.
    Re-runs automated GST validation and updates invoice status accordingly.
    """
    effective_org, effective_user = validate_tenant_identity(db, x_org_id, x_user_id)
    try:
        result = update_line_item(
            session=db,
            org_id=effective_org,
            user_id=effective_user,
            invoice_id=invoice_id,
            item_id=item_id,
            patch=patch
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
    db: Session = Depends(get_tenant_db)
):
    """
    Stage 4: Authenticated human approval of invoice.
    Transitions pending_review -> approved and makes invoice completely immutable.
    """
    effective_org, effective_user = validate_tenant_identity(db, x_org_id, x_user_id)
    try:
        result = approve_invoice(
            session=db,
            org_id=effective_org,
            user_id=effective_user,
            invoice_id=invoice_id
        )
        db.commit()
        return result
    except InvoiceStatusError as e:
        # Commit revalidation status update (e.g. pending_review -> validation_failed)
        db.commit()
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=403, detail=f"Approval failed: {str(e)}")


@router.post("/invoices/{invoice_id}/reject")
def reject_invoice_endpoint(
    invoice_id: str,
    request: InvoiceRejectionRequest,
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
    x_org_id: Optional[str] = Header(None, alias="X-Org-Id"),
    db: Session = Depends(get_tenant_db)
):
    """
    Stage 4: Authenticated human rejection of invoice with explicit reason.
    """
    effective_org, effective_user = validate_tenant_identity(db, x_org_id, x_user_id)
    try:
        result = reject_invoice(
            session=db,
            org_id=effective_org,
            user_id=effective_user,
            invoice_id=invoice_id,
            reason=request.reason
        )
        db.commit()
        return result
    except InvoiceStatusError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=403, detail=f"Rejection failed: {str(e)}")


@router.get("/invoices/tasks/{task_id}")
def get_task_status_endpoint(
    task_id: str,
    x_org_id: Optional[str] = Header(None, alias="X-Org-Id"),
    db: Session = Depends(get_tenant_db)
):
    """
    Check status of queued/in-progress OCR or reconciliation worker task.
    Reads strictly from restricted tenant view (v_tenant_task_status).
    Protects internal worker tokens, hashes, and execution payloads.
    """
    effective_org, _ = validate_tenant_identity(db, x_org_id, None)
    stmt = text("""
        SELECT id, task_type, status, created_at, updated_at
        FROM public.v_tenant_task_status
        WHERE id = :id AND org_id = :org;
    """)
    task = db.execute(stmt, {"id": task_id, "org": effective_org}).mappings().fetchone()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return dict(task)


# GST Working Outputs Endpoints
@router.get("/gst/gstr1-draft", response_model=GSTR1DraftResponse)
def get_gstr1_draft(
    period: str = Query(..., pattern=r"^\d{4}-\d{2}$", description="Tax period YYYY-MM"),
    x_org_id: Optional[str] = Header(None, alias="X-Org-Id"),
    db: Session = Depends(get_tenant_db)
):
    """
    Stage 5: Generate GSTR-1 Working Draft from approved invoices.
    Prominently displays legal working draft disclaimer.
    """
    effective_org, _ = validate_tenant_identity(db, x_org_id, None)
    draft = prepare_gstr1_draft(session=db, org_id=effective_org, tax_period=period)
    return draft


@router.get("/gst/gstr3b-draft", response_model=GSTR3BDraftResponse)
def get_gstr3b_draft(
    period: str = Query(..., pattern=r"^\d{4}-\d{2}$", description="Tax period YYYY-MM"),
    x_org_id: Optional[str] = Header(None, alias="X-Org-Id"),
    db: Session = Depends(get_tenant_db)
):
    """
    Stage 5: Generate GSTR-3B Working Draft with Table 3.1 & 4 from approved invoices.
    Prominently displays legal working draft disclaimer.
    """
    effective_org, _ = validate_tenant_identity(db, x_org_id, None)
    draft = prepare_gstr3b_draft(session=db, org_id=effective_org, tax_period=period)
    return draft
