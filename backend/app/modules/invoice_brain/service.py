import json
import uuid
import logging
from typing import Optional, Dict, Any
from decimal import Decimal
from datetime import date, datetime
from sqlalchemy import text
from sqlalchemy.orm import Session
import psycopg

from app.modules.invoice_brain.storage import (
    compute_stream_sha256_and_save,
    delete_stored_document,
)
from app.modules.invoice_brain.ocr import extract_invoice_with_gemini
from app.modules.invoice_brain.validator import validate_invoice_financials
from app.schemas.invoice_brain import InvoiceUploadResponse, InvoicePatch, LineItemUpdate, ValidationReport

logger = logging.getLogger("invoice_brain.service")


class InvoiceBrainError(Exception):
    pass


class DuplicateDocumentError(InvoiceBrainError):
    def __init__(self, message: str, existing_document_id: Optional[str] = None):
        super().__init__(message)
        self.existing_document_id = existing_document_id


class InvoiceStatusError(InvoiceBrainError):
    pass


class InvoiceValidationError(InvoiceBrainError):
    pass


def _coalesce_decimal(val: Optional[Any]) -> Decimal:
    if val is None:
        return Decimal("0.00")
    try:
        return Decimal(str(val))
    except Exception:
        return Decimal("0.00")


def _safe_iso_date(val: Optional[str], fallback_today: bool = False) -> Optional[str]:
    if not val:
        return date.today().isoformat() if fallback_today else None
    try:
        return date.fromisoformat(str(val).strip()[:10]).isoformat()
    except ValueError:
        return date.today().isoformat() if fallback_today else None


def upload_and_enqueue_invoice(
    session: Session,
    org_id: str,
    user_id: str,
    profile_id: str,
    file_stream,
    file_name: str,
) -> InvoiceUploadResponse:
    """
    Stage 1: Multipart upload, SHA-256 deduplication, storage isolation,
    initial invoice creation, and controlled task enqueuing.
    """
    doc_id = str(uuid.uuid4())
    inv_id = str(uuid.uuid4())

    file_path, file_size, sha256_checksum, mime_type = compute_stream_sha256_and_save(
        file_stream=file_stream,
        org_id=org_id,
        doc_id=doc_id,
    )

    try:
        dup_stmt = text("""
            SELECT id FROM public.documents
            WHERE org_id = :org_id AND sha256_checksum = :sha
            LIMIT 1;
        """)
        existing_doc = session.execute(dup_stmt, {"org_id": org_id, "sha": sha256_checksum}).fetchone()
        if existing_doc:
            raise DuplicateDocumentError(
                f"Duplicate document detected (matches existing document {existing_doc[0]})",
                existing_document_id=str(existing_doc[0]),
            )

        doc_insert_stmt = text("""
            INSERT INTO public.documents (
                id, org_id, doc_type, file_name, file_path,
                mime_type, file_size_bytes, sha256_checksum, uploaded_by
            ) VALUES (
                :id, :org_id, 'invoice', :file_name, :file_path,
                :mime_type, :file_size, :sha, :uploaded_by
            );
        """)
        session.execute(
            doc_insert_stmt,
            {
                "id": doc_id,
                "org_id": org_id,
                "file_name": file_name,
                "file_path": file_path,
                "mime_type": mime_type,
                "file_size": file_size,
                "sha": sha256_checksum,
                "uploaded_by": user_id,
            },
        )

        inv_insert_stmt = text("""
            INSERT INTO public.invoices (
                id, org_id, profile_id, invoice_number, invoice_date,
                counterparty_name, total_taxable_amount, cgst_amount,
                sgst_amount, igst_amount, total_amount, currency,
                status, document_id
            ) VALUES (
                :id, :org_id, :profile_id, :inv_num, CURRENT_DATE,
                'Pending OCR Extraction', 0.00, 0.00,
                0.00, 0.00, 0.00, 'INR',
                'uploaded', :doc_id
            );
        """)
        session.execute(
            inv_insert_stmt,
            {
                "id": inv_id,
                "org_id": org_id,
                "profile_id": profile_id,
                "inv_num": f"TEMP-{inv_id[:8].upper()}",
                "doc_id": doc_id,
            },
        )

        task_stmt = text("""
            SELECT public.enqueue_task(
                'invoice_ocr',
                CAST(:payload AS jsonb),
                CAST(:org_id AS uuid)
            );
        """)
        task_id = session.execute(
            task_stmt,
            {
                "payload": json.dumps(
                    {
                        "invoice_id": inv_id,
                        "document_id": doc_id,
                        "file_path": file_path,
                        "mime_type": mime_type,
                    }
                ),
                "org_id": org_id,
            },
        ).scalar()

        audit_stmt = text("""
            SELECT audit.log_audit_event(
                CAST(:org_id AS uuid),
                'invoices',
                CAST(:inv_id AS text),
                'CREATE'::public.audit_action,
                NULL,
                CAST(:details AS jsonb),
                '{"source": "invoice_brain"}'::jsonb
            );
        """)
        session.execute(
            audit_stmt,
            {
                "inv_id": inv_id,
                "details": json.dumps({"document_id": doc_id, "file_name": file_name, "size": file_size}),
                "org_id": org_id,
            },
        )
        session.flush()

    except Exception as e:
        try:
            delete_stored_document(file_path, expected_org_id=org_id)
        except Exception:
            pass

        if isinstance(e, DuplicateDocumentError) or "uq_documents_org_sha256" in str(e) or "unique constraint" in str(e).lower():
            raise DuplicateDocumentError(
                f"Duplicate document detected (matches existing document SHA-256 {sha256_checksum})"
            ) from e
        raise e

    return InvoiceUploadResponse(
        invoice_id=uuid.UUID(inv_id),
        document_id=uuid.UUID(doc_id),
        task_id=task_id,
        status="uploaded",
        file_name=file_name,
        sha256_checksum=sha256_checksum,
        message="Invoice uploaded successfully and queued for OCR extraction.",
    )


def process_invoice_ocr_task(
    db_conn,
    task_id: str,
    invoice_id: str,
    document_id: str,
    org_id: str,
    file_path: str,
    mime_type: str,
    lease_token: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Stage 2 & 3: Worker execution of OCR extraction and GST validation.
    Protects human edits, safely handles null OCR totals and duplicate invoice numbers,
    and updates line items atomically using replace_ocr_line_items().
    """
    with db_conn.cursor() as cur:
        cur.execute("SELECT set_config('app.current_org', %s, false);", (org_id,))

        cur.execute(
            """
            SELECT bp.gstin, bp.preceding_fy_aato
            FROM public.invoices i
            JOIN public.business_profiles bp ON bp.id = i.profile_id
            WHERE i.id = %s;
            """,
            (invoice_id,),
        )
        profile_row = cur.fetchone()
        supplier_gstin = profile_row[0] if profile_row else None
        raw_aato = profile_row[1] if profile_row else None
        aato_crores = (Decimal(str(raw_aato)) / Decimal("10000000")) if raw_aato is not None else Decimal("5.0")

        cur.execute("SELECT status FROM public.invoices WHERE id = %s;", (invoice_id,))
        status_row = cur.fetchone()
        if not status_row:
            raise InvoiceBrainError(f"Invoice {invoice_id} not found")
        curr_status = status_row[0]

        if curr_status in ("pending_review", "validation_failed", "approved", "rejected", "export_ready"):
            logger.warning(
                "Task %s attempted OCR processing on invoice %s with protected status '%s'. Skipping.",
                task_id,
                invoice_id,
                curr_status,
            )
            return {
                "invoice_id": invoice_id,
                "status": curr_status,
                "skipped": True,
                "reason": f"Protected status '{curr_status}' preserved against worker retry",
            }

        if curr_status == "uploaded":
            cur.execute("UPDATE public.invoices SET status = 'queued' WHERE id = %s;", (invoice_id,))
            cur.execute("UPDATE public.invoices SET status = 'processing' WHERE id = %s;", (invoice_id,))
        elif curr_status == "queued":
            cur.execute("UPDATE public.invoices SET status = 'processing' WHERE id = %s;", (invoice_id,))
        elif curr_status in ("processing", "extracted"):
            pass
        else:
            raise InvoiceStatusError(f"Invoice {invoice_id} in invalid status '{curr_status}' for OCR processing")

        extracted = extract_invoice_with_gemini(file_path, mime_type, expected_org_id=org_id)

        inv_date_iso = _safe_iso_date(extracted.invoice_date, fallback_today=True)
        due_date_iso = _safe_iso_date(extracted.due_date, fallback_today=False)
        inv_num = (extracted.invoice_number or f"OCR-{invoice_id[:8].upper()}").strip()

        taxable_val = _coalesce_decimal(extracted.total_taxable_amount)
        cgst_val = _coalesce_decimal(extracted.cgst_amount)
        sgst_val = _coalesce_decimal(extracted.sgst_amount)
        igst_val = _coalesce_decimal(extracted.igst_amount)
        total_val = _coalesce_decimal(extracted.total_amount)

        try:
            cur.execute(
                """
                UPDATE public.invoices SET
                    invoice_number = %s,
                    invoice_date = %s::date,
                    due_date = %s::date,
                    counterparty_name = %s,
                    counterparty_gstin = %s,
                    total_taxable_amount = %s,
                    cgst_amount = %s,
                    sgst_amount = %s,
                    igst_amount = %s,
                    total_amount = %s,
                    ocr_raw_response = %s::jsonb,
                    ocr_confidence = %s,
                    status = 'extracted',
                    updated_at = clock_timestamp()
                WHERE id = %s;
                """,
                (
                    inv_num,
                    inv_date_iso,
                    due_date_iso,
                    extracted.counterparty_name or "Unknown Counterparty",
                    extracted.counterparty_gstin,
                    taxable_val,
                    cgst_val,
                    sgst_val,
                    igst_val,
                    total_val,
                    json.dumps(extracted.raw_ocr_response or {}),
                    extracted.confidence_score,
                    invoice_id,
                ),
            )
        except psycopg.errors.UniqueViolation:
            db_conn.rollback()
            cur.execute("SELECT set_config('app.current_org', %s, false);", (org_id,))
            cur.execute(
                """
                UPDATE public.invoices
                SET status = 'validation_failed',
                    rejection_reason = %s,
                    ocr_raw_response = %s::jsonb,
                    updated_at = clock_timestamp()
                WHERE id = %s;
                """,
                (
                    f"Duplicate invoice number '{inv_num}' already exists in this organization.",
                    json.dumps(extracted.raw_ocr_response or {}),
                    invoice_id,
                ),
            )
            return {
                "invoice_id": invoice_id,
                "status": "validation_failed",
                "extracted_invoice_number": inv_num,
                "confidence_score": extracted.confidence_score,
                "validation_report": None,
                "line_items_count": 0,
            }

        if not lease_token:
            raise ValueError(f"Active lease_token is required to invoke replace_ocr_line_items for task {task_id}")

        raw_lines = [line.model_dump() for line in extracted.line_items]
        cur.execute(
            """
            SELECT public.replace_ocr_line_items(%s, %s, %s, %s::jsonb);
            """,
            (task_id, lease_token, invoice_id, json.dumps(raw_lines, default=str)),
        )

        validation_input = extracted.model_dump()
        validation_input["invoice_date"] = inv_date_iso
        validation_input["total_taxable_amount"] = taxable_val
        validation_input["cgst_amount"] = cgst_val
        validation_input["sgst_amount"] = sgst_val
        validation_input["igst_amount"] = igst_val
        validation_input["total_amount"] = total_val

        validation = validate_invoice_financials(
            invoice=validation_input,
            line_items=raw_lines,
            supplier_gstin=supplier_gstin,
            aato_crores=aato_crores,
        )

        final_status = "pending_review" if validation.is_valid else "validation_failed"
        rejection_reason = None
        if not validation.is_valid:
            rejection_reason = f"Automated GST validation flagged {validation.discrepancy_count} discrepancy issues."

        cur.execute(
            """
            UPDATE public.invoices SET
                status = %s,
                rejection_reason = %s,
                updated_at = clock_timestamp()
            WHERE id = %s;
            """,
            (final_status, rejection_reason, invoice_id),
        )

        return {
            "invoice_id": invoice_id,
            "status": final_status,
            "extracted_invoice_number": inv_num,
            "confidence_score": extracted.confidence_score,
            "validation_report": validation.model_dump(),
            "line_items_count": len(extracted.line_items),
        }


def revalidate_invoice(session: Session, org_id: str, invoice_id: str) -> Optional[ValidationReport]:
    """
    Re-evaluates automated GST validation after any invoice or line item modification.
    Automatically transitions invoice between 'pending_review' and 'validation_failed'.
    """
    inv_stmt = text("""
        SELECT
            i.id, i.org_id, i.profile_id, i.invoice_number, i.invoice_date,
            i.due_date, i.counterparty_name, i.counterparty_gstin,
            i.total_taxable_amount, i.cgst_amount, i.sgst_amount,
            i.igst_amount, i.total_amount, i.status,
            bp.gstin as supplier_gstin,
            bp.preceding_fy_aato as preceding_fy_aato
        FROM public.invoices i
        JOIN public.business_profiles bp ON bp.id = i.profile_id
        WHERE i.id = :id AND i.org_id = :org_id;
    """)
    inv = session.execute(inv_stmt, {"id": invoice_id, "org_id": org_id}).mappings().fetchone()
    if not inv:
        return None

    lines_stmt = text("""
        SELECT id, invoice_id, org_id, item_description, hsn_sac_code,
               quantity, unit_price, taxable_amount, gst_rate,
               cgst_amount, sgst_amount, igst_amount, total_amount
        FROM public.invoice_line_items
        WHERE invoice_id = :id AND org_id = :org_id
        ORDER BY created_at ASC;
    """)
    lines = session.execute(lines_stmt, {"id": invoice_id, "org_id": org_id}).mappings().fetchall()

    raw_aato = inv.get("preceding_fy_aato")
    aato_crores = (Decimal(str(raw_aato)) / Decimal("10000000")) if raw_aato is not None else Decimal("5.0")

    validation = validate_invoice_financials(
        invoice=dict(inv),
        line_items=[dict(l) for l in lines],
        supplier_gstin=inv["supplier_gstin"],
        aato_crores=aato_crores,
    )

    if inv["status"] in ("pending_review", "validation_failed", "extracted", "queued", "uploaded"):
        new_status = "pending_review" if validation.is_valid else "validation_failed"
        new_reason = None if validation.is_valid else f"Automated GST validation flagged {validation.discrepancy_count} discrepancy issues."

        session.execute(
            text("""
                UPDATE public.invoices
                SET status = :status,
                    rejection_reason = :reason,
                    updated_at = clock_timestamp()
                WHERE id = :id AND org_id = :org_id;
            """),
            {"status": new_status, "reason": new_reason, "id": invoice_id, "org_id": org_id},
        )

    return validation


def update_invoice_fields(
    session: Session,
    org_id: str,
    user_id: str,
    invoice_id: str,
    patch: InvoicePatch,
) -> Dict[str, Any]:
    cur_stmt = text("SELECT status, total_amount FROM public.invoices WHERE id = :id AND org_id = :org_id;")
    inv = session.execute(cur_stmt, {"id": invoice_id, "org_id": org_id}).fetchone()
    if not inv:
        raise InvoiceBrainError("Invoice not found")
    if inv[0] in ("approved", "export_ready"):
        raise InvoiceStatusError(f"Cannot edit invoice in {inv[0]} state. Approved invoices are immutable.")

    updates = []
    params = {"id": invoice_id, "org_id": org_id}
    data = patch.model_dump(exclude_unset=True)

    for field, val in data.items():
        updates.append(f"{field} = :{field}")
        params[field] = val

    if updates:
        sql = f"UPDATE public.invoices SET {', '.join(updates)}, updated_at = clock_timestamp() WHERE id = :id AND org_id = :org_id;"
        session.execute(text(sql), params)

        session.execute(
            text("""
                SELECT audit.log_audit_event(
                    CAST(:org_id AS uuid),
                    'invoices',
                    CAST(:id AS text),
                    'UPDATE'::public.audit_action,
                    NULL,
                    CAST(:patch_data AS jsonb),
                    '{"source": "invoice_brain"}'::jsonb
                );
            """),
            {"id": invoice_id, "patch_data": json.dumps(data, default=str), "org_id": org_id},
        )

    validation = revalidate_invoice(session, org_id, invoice_id)

    return {
        "status": "success",
        "modified_fields": list(data.keys()),
        "validation_report": validation.model_dump() if validation else None,
    }


def update_line_item(
    session: Session,
    org_id: str,
    user_id: str,
    invoice_id: str,
    item_id: str,
    patch: LineItemUpdate,
) -> Dict[str, Any]:
    inv = session.execute(
        text("SELECT status FROM public.invoices WHERE id = :id AND org_id = :org;"),
        {"id": invoice_id, "org": org_id},
    ).fetchone()
    if not inv:
        raise InvoiceBrainError("Invoice not found")
    if inv[0] in ("approved", "export_ready"):
        raise InvoiceStatusError(f"Cannot modify line items on invoice in '{inv[0]}' state. Approved invoices are immutable.")

    data = patch.model_dump(exclude_unset=True)
    if not data:
        return {"status": "no_changes"}

    updates = [f"{k} = :{k}" for k in data.keys()]
    params = {"item_id": item_id, "inv_id": invoice_id, "org_id": org_id, **data}
    sql = f"UPDATE public.invoice_line_items SET {', '.join(updates)} WHERE id = :item_id AND invoice_id = :inv_id AND org_id = :org_id;"
    res = session.execute(text(sql), params)
    if res.rowcount == 0:
        raise InvoiceBrainError("Invoice line item not found")

    validation = revalidate_invoice(session, org_id, invoice_id)

    session.execute(
        text("""
            SELECT audit.log_audit_event(
                CAST(:org_id AS uuid),
                'invoice_line_items',
                CAST(:item_id AS text),
                'UPDATE'::public.audit_action,
                NULL,
                CAST(:patch_data AS jsonb),
                CAST(:metadata AS jsonb)
            );
        """),
        {
            "item_id": item_id,
            "patch_data": json.dumps(data, default=str),
            "metadata": json.dumps({"source": "invoice_brain", "invoice_id": invoice_id}),
            "org_id": org_id,
        },
    )

    return {
        "status": "success",
        "modified_fields": list(data.keys()),
        "validation_report": validation.model_dump() if validation else None,
    }


def approve_invoice(session: Session, org_id: str, user_id: str, invoice_id: str) -> Dict[str, Any]:
    cur_stmt = text("SELECT status, total_amount, invoice_number FROM public.invoices WHERE id = :id AND org_id = :org_id;")
    inv = session.execute(cur_stmt, {"id": invoice_id, "org_id": org_id}).fetchone()
    if not inv:
        raise InvoiceBrainError("Invoice not found")
    if inv[0] != "pending_review":
        raise InvoiceStatusError(f"Cannot approve invoice in status '{inv[0]}'. Only 'pending_review' invoices can be approved.")

    validation = revalidate_invoice(session, org_id, invoice_id)
    if validation and (not validation.is_valid or validation.has_critical_errors):
        raise InvoiceStatusError(
            f"Cannot approve invoice: invoice failed validation with {validation.discrepancy_count} discrepancy issues. "
            "All critical validation errors must be resolved prior to approval."
        )

    check_lines_stmt = text("""
        SELECT COUNT(*) FROM public.invoice_line_items
        WHERE invoice_id = :id AND org_id = :org_id
          AND (quantity IS NULL OR unit_price IS NULL OR taxable_amount IS NULL OR gst_rate IS NULL OR total_amount IS NULL);
    """)
    missing_count = session.execute(check_lines_stmt, {"id": invoice_id, "org_id": org_id}).scalar()
    if missing_count and missing_count > 0:
        raise InvoiceStatusError("Cannot approve invoice with missing or unextracted financial fields")

    approve_stmt = text("""
        UPDATE public.invoices
        SET status = 'approved',
            approved_by = :user_id,
            approved_at = clock_timestamp()
        WHERE id = :id AND org_id = :org_id;
    """)
    session.execute(approve_stmt, {"id": invoice_id, "user_id": user_id, "org_id": org_id})

    session.execute(
        text("""
            SELECT audit.log_audit_event(
                CAST(:org_id AS uuid),
                'invoices',
                CAST(:id AS text),
                'APPROVE'::public.audit_action,
                CAST(:old_state AS jsonb),
                CAST(:new_state AS jsonb),
                '{"source": "invoice_brain"}'::jsonb
            );
        """),
        {
            "id": invoice_id,
            "old_state": json.dumps({"status": "pending_review"}),
            "new_state": json.dumps(
                {
                    "status": "approved",
                    "approved_by": user_id,
                    "total_amount": str(inv[1]),
                    "invoice_number": inv[2],
                }
            ),
            "org_id": org_id,
        },
    )

    return {"status": "approved", "invoice_id": invoice_id, "approved_by": user_id}


def reject_invoice(session: Session, org_id: str, user_id: str, invoice_id: str, reason: str) -> Dict[str, Any]:
    cur_stmt = text("SELECT status FROM public.invoices WHERE id = :id AND org_id = :org_id;")
    inv = session.execute(cur_stmt, {"id": invoice_id, "org_id": org_id}).fetchone()
    if not inv:
        raise InvoiceBrainError("Invoice not found")
    if inv[0] != "pending_review":
        raise InvoiceStatusError(f"Cannot reject invoice in status '{inv[0]}'. Only 'pending_review' invoices can be rejected.")

    reject_stmt = text("""
        UPDATE public.invoices
        SET status = 'rejected',
            rejection_reason = :reason,
            updated_at = clock_timestamp()
        WHERE id = :id AND org_id = :org_id;
    """)
    session.execute(reject_stmt, {"id": invoice_id, "reason": reason, "org_id": org_id})

    session.execute(
        text("""
            SELECT audit.log_audit_event(
                CAST(:org_id AS uuid),
                'invoices',
                CAST(:id AS text),
                'REJECT'::public.audit_action,
                CAST(:old_state AS jsonb),
                CAST(:new_state AS jsonb),
                '{"source": "invoice_brain"}'::jsonb
            );
        """),
        {
            "id": invoice_id,
            "old_state": json.dumps({"status": "pending_review"}),
            "new_state": json.dumps({"status": "rejected", "rejected_by": user_id, "reason": reason}),
            "org_id": org_id,
        },
    )

    return {"status": "rejected", "invoice_id": invoice_id, "reason": reason}
