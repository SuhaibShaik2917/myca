import os
import json
import uuid
import logging
from typing import Optional, Dict, Any, List
from decimal import Decimal
from datetime import date, datetime
from sqlalchemy import text
from sqlalchemy.orm import Session
import psycopg

from app.modules.invoice_brain.storage import compute_stream_sha256_and_save, StorageError
from app.modules.invoice_brain.ocr import extract_invoice_with_gemini
from app.modules.invoice_brain.validator import validate_invoice_financials
from app.schemas.invoice_brain import (
    InvoiceUploadResponse, InvoicePatch, LineItemUpdate,
    ValidationReport, InvoiceOut, LineItemOut
)

logger = logging.getLogger("invoice_brain.service")

class InvoiceBrainError(Exception):
    pass

class DuplicateDocumentError(InvoiceBrainError):
    pass

class InvoiceStatusError(InvoiceBrainError):
    pass

class InvoiceValidationError(InvoiceBrainError):
    pass

def upload_and_enqueue_invoice(
    session: Session,
    org_id: str,
    user_id: str,
    profile_id: str,
    file_stream,
    file_name: str
) -> InvoiceUploadResponse:
    """
    Stage 1: Multipart upload, SHA-256 deduplication, storage isolation,
    initial invoice creation, and controlled task enqueuing.
    Guarantees no orphaned files on disk if database insertion or task enqueuing fails.
    """
    doc_id = str(uuid.uuid4())
    inv_id = str(uuid.uuid4())

    # 1. Stream, validate magic bytes, compute SHA-256, and save
    file_path, file_size, sha256_checksum, mime_type = compute_stream_sha256_and_save(
        file_stream=file_stream,
        org_id=org_id,
        doc_id=doc_id
    )

    try:
        # 2. Check for duplicate document in this organization (application check)
        dup_stmt = text("""
            SELECT id FROM public.documents
            WHERE org_id = :org_id AND sha256_checksum = :sha
            LIMIT 1;
        """)
        existing_doc = session.execute(dup_stmt, {"org_id": org_id, "sha": sha256_checksum}).fetchone()
        if existing_doc:
            raise DuplicateDocumentError(f"Duplicate document detected (matches existing document {existing_doc[0]})")

        # 3. Insert into public.documents (protected by uq_documents_org_sha256 constraint)
        doc_insert_stmt = text("""
            INSERT INTO public.documents (
                id, org_id, doc_type, file_name, file_path,
                mime_type, file_size_bytes, sha256_checksum, uploaded_by
            ) VALUES (
                :id, :org_id, 'invoice', :file_name, :file_path,
                :mime_type, :file_size, :sha, :uploaded_by
            );
        """)
        session.execute(doc_insert_stmt, {
            "id": doc_id,
            "org_id": org_id,
            "file_name": file_name,
            "file_path": file_path,
            "mime_type": mime_type,
            "file_size": file_size,
            "sha": sha256_checksum,
            "uploaded_by": user_id
        })

        # 4. Insert into public.invoices (MUST start with status='uploaded')
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
        session.execute(inv_insert_stmt, {
            "id": inv_id,
            "org_id": org_id,
            "profile_id": profile_id,
            "inv_num": f"TEMP-{inv_id[:8].upper()}",
            "doc_id": doc_id
        })

        # 5. Enqueue OCR task through controlled enqueue_task function
        task_stmt = text("""
            SELECT public.enqueue_task(
                'invoice_ocr',
                CAST(:payload AS jsonb),
                CAST(:org_id AS uuid)
            );
        """)
        task_id = session.execute(task_stmt, {
            "payload": json.dumps({
                "invoice_id": inv_id,
                "document_id": doc_id,
                "file_path": file_path,
                "mime_type": mime_type
            }),
            "org_id": org_id
        }).scalar()

        # 6. Audit log event
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
        session.execute(audit_stmt, {
            "inv_id": inv_id,
            "details": json.dumps({"document_id": doc_id, "file_name": file_name, "size": file_size}),
            "org_id": org_id
        })

        session.flush()

    except Exception as e:
        # Atomic storage cleanup: delete saved file if DB insert, enqueue, or constraint fails
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
            except OSError:
                pass

        if isinstance(e, DuplicateDocumentError) or "uq_documents_org_sha256" in str(e) or "unique constraint" in str(e).lower():
            raise DuplicateDocumentError(f"Duplicate document detected (matches existing document SHA-256 {sha256_checksum})") from e
        raise e

    return InvoiceUploadResponse(
        invoice_id=uuid.UUID(inv_id),
        document_id=uuid.UUID(doc_id),
        task_id=task_id,
        status="uploaded",
        file_name=file_name,
        sha256_checksum=sha256_checksum,
        message="Invoice uploaded successfully and queued for OCR extraction."
    )


def process_invoice_ocr_task(
    db_conn,
    task_id: str,
    invoice_id: str,
    document_id: str,
    org_id: str,
    file_path: str,
    mime_type: str,
    lease_token: Optional[str] = None
) -> Dict[str, Any]:
    """
    Stage 2 & 3: Worker execution of OCR extraction and GST validation.
    Runs under worker_service_user connection.
    Guarantees that:
    1. Worker line items are replaced ONLY via controlled replace_ocr_line_items() using active lease_token.
    2. Invoices in 'pending_review', 'approved', 'rejected', or 'export_ready' are NEVER overwritten on worker retry.
    3. OCR extraction never silently transitions directly to approved.
    Transitions: uploaded -> queued -> processing -> extracted -> (pending_review OR validation_failed).
    """
    with db_conn.cursor() as cur:
        # Set worker tenant context
        cur.execute("SELECT set_config('app.current_org', %s, false);", (org_id,))

        # Fetch supplier GSTIN for interstate/intrastate determination
        cur.execute("""
            SELECT bp.gstin 
            FROM public.invoices i
            JOIN public.business_profiles bp ON bp.id = i.profile_id
            WHERE i.id = %s;
        """, (invoice_id,))
        profile_row = cur.fetchone()
        supplier_gstin = profile_row[0] if profile_row else None

        # Fetch current invoice status
        cur.execute("SELECT status FROM public.invoices WHERE id = %s;", (invoice_id,))
        status_row = cur.fetchone()
        if not status_row:
            raise InvoiceBrainError(f"Invoice {invoice_id} not found")
        curr_status = status_row[0]

        # CRITICAL INTEGRITY CHECK: Protect human corrections and immutable statuses against worker retries
        if curr_status in ('pending_review', 'validation_failed', 'approved', 'rejected', 'export_ready'):
            logger.warning(
                f"Task {task_id} attempted OCR processing on invoice {invoice_id} with protected status '{curr_status}'. "
                "Skipping to preserve human edits and immutable states."
            )
            return {
                "invoice_id": invoice_id,
                "status": curr_status,
                "skipped": True,
                "reason": f"Protected status '{curr_status}' preserved against worker retry"
            }

        # State transition into processing
        if curr_status == 'uploaded':
            cur.execute("UPDATE public.invoices SET status = 'queued' WHERE id = %s;", (invoice_id,))
            cur.execute("UPDATE public.invoices SET status = 'processing' WHERE id = %s;", (invoice_id,))
        elif curr_status == 'queued':
            cur.execute("UPDATE public.invoices SET status = 'processing' WHERE id = %s;", (invoice_id,))
        elif curr_status == 'processing':
            pass
        else:
            raise InvoiceStatusError(f"Invoice {invoice_id} in invalid status '{curr_status}' for OCR processing")

        # Stage 2: OCR extraction
        extracted = extract_invoice_with_gemini(file_path, mime_type)

        # Update invoice with extracted figures, OCR provenance, and transition to 'extracted'
        cur.execute("""
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
        """, (
            extracted.invoice_number,
            extracted.invoice_date,
            extracted.due_date,
            extracted.counterparty_name,
            extracted.counterparty_gstin,
            extracted.total_taxable_amount,
            extracted.cgst_amount,
            extracted.sgst_amount,
            extracted.igst_amount,
            extracted.total_amount,
            json.dumps(extracted.raw_ocr_response or {}),
            extracted.confidence_score,
            invoice_id
        ))

        # Controlled Atomic Line-Item Replacement via SECURITY DEFINER function
        if not lease_token:
            raise ValueError(f"Active lease_token is required to invoke replace_ocr_line_items for task {task_id}")

        raw_lines = [line.model_dump() for line in extracted.line_items]
        cur.execute("""
            SELECT public.replace_ocr_line_items(%s, %s, %s, %s::jsonb);
        """, (
            task_id,
            lease_token,
            invoice_id,
            json.dumps(raw_lines, default=str)
        ))

        # Stage 3: GST Validation and Reconciliation
        validation = validate_invoice_financials(
            invoice=extracted.model_dump(),
            line_items=raw_lines,
            supplier_gstin=supplier_gstin
        )

        final_status = "pending_review" if validation.is_valid else "validation_failed"
        rejection_reason = None
        if not validation.is_valid:
            rejection_reason = f"Automated GST validation flagged {validation.discrepancy_count} discrepancy issues."

        cur.execute("""
            UPDATE public.invoices SET
                status = %s,
                rejection_reason = %s,
                updated_at = clock_timestamp()
            WHERE id = %s;
        """, (final_status, rejection_reason, invoice_id))

        return {
            "invoice_id": invoice_id,
            "status": final_status,
            "extracted_invoice_number": extracted.invoice_number,
            "confidence_score": extracted.confidence_score,
            "validation_report": validation.model_dump(),
            "line_items_count": len(extracted.line_items)
        }


def revalidate_invoice(
    session: Session,
    org_id: str,
    invoice_id: str
) -> Optional[ValidationReport]:
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
            bp.gstin as supplier_gstin
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

    validation = validate_invoice_financials(
        invoice=dict(inv),
        line_items=[dict(l) for l in lines],
        supplier_gstin=inv["supplier_gstin"]
    )

    if inv["status"] in ("pending_review", "validation_failed", "extracted", "queued", "uploaded"):
        new_status = "pending_review" if validation.is_valid else "validation_failed"
        new_reason = None if validation.is_valid else f"Automated GST validation flagged {validation.discrepancy_count} discrepancy issues."

        session.execute(text("""
            UPDATE public.invoices
            SET status = :status,
                rejection_reason = :reason,
                updated_at = clock_timestamp()
            WHERE id = :id AND org_id = :org_id;
        """), {
            "status": new_status,
            "reason": new_reason,
            "id": invoice_id,
            "org_id": org_id
        })

    return validation


def update_invoice_fields(
    session: Session,
    org_id: str,
    user_id: str,
    invoice_id: str,
    patch: InvoicePatch
) -> Dict[str, Any]:
    """
    Stage 4: Allows human reviewer to correct OCR fields prior to approval.
    Database triggers strictly reject modifications if status is approved/export_ready!
    Automatically triggers validation re-evaluation and updates review status.
    """
    # Fetch current status
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

        # Audit log edit
        session.execute(text("""
            SELECT audit.log_audit_event(
                CAST(:org_id AS uuid),
                'invoices',
                CAST(:id AS text),
                'UPDATE'::public.audit_action,
                NULL,
                CAST(:patch_data AS jsonb),
                '{"source": "invoice_brain"}'::jsonb
            );
        """), {
            "id": invoice_id,
            "patch_data": json.dumps(data, default=str),
            "org_id": org_id
        })

    # Automatically revalidate invoice after field updates
    validation = revalidate_invoice(session, org_id, invoice_id)

    return {
        "status": "success",
        "modified_fields": list(data.keys()),
        "validation_report": validation.model_dump() if validation else None
    }


def update_line_item(
    session: Session,
    org_id: str,
    user_id: str,
    invoice_id: str,
    item_id: str,
    patch: LineItemUpdate
) -> Dict[str, Any]:
    """
    Stage 4: Line item correction before approval.
    Automatically re-evaluates validation and updates review status.
    """
    inv = session.execute(text("SELECT status FROM public.invoices WHERE id = :id AND org_id = :org;"),
                          {"id": invoice_id, "org": org_id}).fetchone()
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
    session.execute(text(sql), params)

    # Revalidate invoice after line item edit
    validation = revalidate_invoice(session, org_id, invoice_id)

    # Audit log
    session.execute(text("""
        SELECT audit.log_audit_event(
            CAST(:org_id AS uuid),
            'invoice_line_items',
            CAST(:item_id AS text),
            'UPDATE'::public.audit_action,
            NULL,
            CAST(:patch_data AS jsonb),
            CAST(:metadata AS jsonb)
        );
    """), {
        "item_id": item_id,
        "patch_data": json.dumps(data, default=str),
        "metadata": json.dumps({"source": "invoice_brain", "invoice_id": invoice_id}),
        "org_id": org_id
    })

    return {
        "status": "success",
        "modified_fields": list(data.keys()),
        "validation_report": validation.model_dump() if validation else None
    }


def approve_invoice(
    session: Session,
    org_id: str,
    user_id: str,
    invoice_id: str
) -> Dict[str, Any]:
    """
    Stage 4: Authenticated human approval of invoice.
    Transitions pending_review -> approved.
    Enforces that invoice must currently pass automated validation with NO critical errors!
    Enforces active owner/admin membership and guarantees immutability afterwards.
    """
    cur_stmt = text("SELECT status, total_amount, invoice_number FROM public.invoices WHERE id = :id AND org_id = :org_id;")
    inv = session.execute(cur_stmt, {"id": invoice_id, "org_id": org_id}).fetchone()
    if not inv:
        raise InvoiceBrainError("Invoice not found")
    if inv[0] != "pending_review":
        raise InvoiceStatusError(f"Cannot approve invoice in status '{inv[0]}'. Only 'pending_review' invoices can be approved.")

    # Re-evaluate validation at the exact moment of approval to ensure integrity
    validation = revalidate_invoice(session, org_id, invoice_id)
    if validation and (not validation.is_valid or validation.has_critical_errors):
        raise InvoiceStatusError(
            f"Cannot approve invoice: invoice failed validation with {validation.discrepancy_count} discrepancy issues. "
            "All critical validation errors must be resolved prior to approval."
        )

    # Verify no line item contains unextracted or missing mandatory financial fields
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

    # Log audit event
    session.execute(text("""
        SELECT audit.log_audit_event(
            CAST(:org_id AS uuid),
            'invoices',
            CAST(:id AS text),
            'APPROVE'::public.audit_action,
            CAST(:old_state AS jsonb),
            CAST(:new_state AS jsonb),
            '{"source": "invoice_brain"}'::jsonb
        );
    """), {
        "id": invoice_id,
        "old_state": json.dumps({"status": "pending_review"}),
        "new_state": json.dumps({"status": "approved", "approved_by": user_id, "total_amount": str(inv[1]), "invoice_number": inv[2]}),
        "org_id": org_id
    })

    return {"status": "approved", "invoice_id": invoice_id, "approved_by": user_id}


def reject_invoice(
    session: Session,
    org_id: str,
    user_id: str,
    invoice_id: str,
    reason: str
) -> Dict[str, Any]:
    """
    Stage 4: Authenticated human rejection of invoice.
    Transitions pending_review -> rejected with explicit rejection reason.
    """
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

    # Log audit event
    session.execute(text("""
        SELECT audit.log_audit_event(
            CAST(:org_id AS uuid),
            'invoices',
            CAST(:id AS text),
            'REJECT'::public.audit_action,
            CAST(:old_state AS jsonb),
            CAST(:new_state AS jsonb),
            '{"source": "invoice_brain"}'::jsonb
        );
    """), {
        "id": invoice_id,
        "old_state": json.dumps({"status": "pending_review"}),
        "new_state": json.dumps({"status": "rejected", "reason": reason}),
        "org_id": org_id
    })

    return {"status": "rejected", "invoice_id": invoice_id, "reason": reason}
