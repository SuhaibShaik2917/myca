import uuid
from typing import Dict, Any, List, Optional
from decimal import Decimal
from datetime import date, datetime
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.modules.reconciliation.schemas import PurchaseDocumentCreate, PurchaseLineItemIn


class PurchaseServiceError(Exception):
    pass


class DuplicatePurchaseDocumentError(PurchaseServiceError):
    pass


class PurchaseStatusError(PurchaseServiceError):
    pass


def create_purchase_document(
    session: Session,
    org_id: str,
    payload: PurchaseDocumentCreate,
    user_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Creates an inward purchase document in the purchase register.
    Validates GST registration ownership, enforces unique constraint on (org_id, supplier_gstin, doc_num, doc_date),
    and persists line items.
    """
    # 1. Verify registration exists for this org
    reg_check = session.execute(
        text("SELECT id, gstin FROM public.gst_registrations WHERE id = :reg_id AND org_id = :org_id;"),
        {"reg_id": payload.registration_id, "org_id": org_id}
    ).fetchone()
    if not reg_check:
        raise PurchaseServiceError(f"GST registration {payload.registration_id} not found for this organization")

    # 2. Check duplicate
    dup_check = session.execute(
        text("""
        SELECT id FROM public.purchase_documents
        WHERE org_id = :org_id
          AND supplier_gstin = :supplier_gstin
          AND document_number = :doc_num
          AND document_date = :doc_date;
        """),
        {
            "org_id": org_id,
            "supplier_gstin": payload.supplier_gstin,
            "doc_num": payload.document_number,
            "doc_date": payload.document_date
        }
    ).fetchone()
    if dup_check:
        raise DuplicatePurchaseDocumentError(
            f"Purchase document {payload.document_number} dated {payload.document_date} "
            f"from supplier {payload.supplier_gstin} already exists."
        )

    # Calculate financial totals from lines if omitted
    calc_taxable = payload.total_taxable_amount
    calc_cgst = payload.cgst_amount
    calc_sgst = payload.sgst_amount
    calc_igst = payload.igst_amount
    calc_cess = payload.cess_amount or Decimal("0.00")
    calc_total = payload.total_invoice_value

    if calc_taxable is None and payload.line_items:
        calc_taxable = sum((item.taxable_amount or Decimal("0.00")) for item in payload.line_items)
        calc_cgst = sum((item.cgst_amount or Decimal("0.00")) for item in payload.line_items)
        calc_sgst = sum((item.sgst_amount or Decimal("0.00")) for item in payload.line_items)
        calc_igst = sum((item.igst_amount or Decimal("0.00")) for item in payload.line_items)
        calc_cess = sum((item.cess_amount or Decimal("0.00")) for item in payload.line_items)
        calc_total = calc_taxable + calc_cgst + calc_sgst + calc_igst + calc_cess

    doc_id = str(uuid.uuid4())
    insert_doc = session.execute(
        text("""
        INSERT INTO public.purchase_documents (
            id, org_id, registration_id, document_type, document_number,
            document_date, financial_period, supplier_gstin, supplier_legal_name,
            recipient_gstin, place_of_supply, is_reverse_charge, supply_category,
            total_taxable_amount, cgst_amount, sgst_amount, igst_amount, cess_amount,
            total_invoice_value, goods_services_type, goods_receipt_date,
            goods_receipt_note_ref, purchase_ledger_code, status
        ) VALUES (
            :id, :org_id, :registration_id, :document_type, :document_number,
            :document_date, :financial_period, :supplier_gstin, :supplier_legal_name,
            :recipient_gstin, :place_of_supply, :is_reverse_charge, :supply_category,
            :total_taxable_amount, :cgst_amount, :sgst_amount, :igst_amount, :cess_amount,
            :total_invoice_value, :goods_services_type, :goods_receipt_date,
            :goods_receipt_note_ref, :purchase_ledger_code, 'draft'
        ) RETURNING id, status, created_at;
        """),
        {
            "id": doc_id,
            "org_id": org_id,
            "registration_id": payload.registration_id,
            "document_type": payload.document_type,
            "document_number": payload.document_number,
            "document_date": payload.document_date,
            "financial_period": payload.financial_period,
            "supplier_gstin": payload.supplier_gstin,
            "supplier_legal_name": payload.supplier_legal_name,
            "recipient_gstin": payload.recipient_gstin,
            "place_of_supply": payload.place_of_supply,
            "is_reverse_charge": payload.is_reverse_charge,
            "supply_category": payload.supply_category,
            "total_taxable_amount": calc_taxable,
            "cgst_amount": calc_cgst,
            "sgst_amount": calc_sgst,
            "igst_amount": calc_igst,
            "cess_amount": calc_cess,
            "total_invoice_value": calc_total,
            "goods_services_type": payload.goods_services_type,
            "goods_receipt_date": payload.goods_receipt_date,
            "goods_receipt_note_ref": payload.goods_receipt_note_ref,
            "purchase_ledger_code": payload.purchase_ledger_code,
        }
    ).fetchone()

    # Insert line items
    inserted_lines = []
    for item in payload.line_items:
        line_id = str(uuid.uuid4())
        session.execute(
            text("""
            INSERT INTO public.purchase_line_items (
                id, org_id, purchase_doc_id, item_description, hsn_sac_code,
                quantity, unit_price, taxable_amount, gst_rate, cgst_amount,
                sgst_amount, igst_amount, cess_amount, total_amount
            ) VALUES (
                :id, :org_id, :purchase_doc_id, :item_description, :hsn_sac_code,
                :quantity, :unit_price, :taxable_amount, :gst_rate, :cgst_amount,
                :sgst_amount, :igst_amount, :cess_amount, :total_amount
            );
            """),
            {
                "id": line_id,
                "org_id": org_id,
                "purchase_doc_id": doc_id,
                "item_description": item.item_description,
                "hsn_sac_code": item.hsn_sac_code,
                "quantity": item.quantity,
                "unit_price": item.unit_price,
                "taxable_amount": item.taxable_amount,
                "gst_rate": item.gst_rate,
                "cgst_amount": item.cgst_amount,
                "sgst_amount": item.sgst_amount,
                "igst_amount": item.igst_amount,
                "cess_amount": item.cess_amount or Decimal("0.00"),
                "total_amount": item.total_amount,
            }
        )
        inserted_lines.append(line_id)

    return {
        "id": doc_id,
        "status": "draft",
        "document_number": payload.document_number,
        "total_invoice_value": str(calc_total) if calc_total is not None else None,
        "line_item_count": len(inserted_lines),
        "created_at": insert_doc.created_at.isoformat() if insert_doc else None
    }


def approve_purchase_document(
    session: Session,
    org_id: str,
    purchase_doc_id: str,
    user_id: str
) -> Dict[str, Any]:
    """
    Transitions purchase document to approved state after human review.
    """
    row = session.execute(
        text("SELECT id, status FROM public.purchase_documents WHERE id = :id AND org_id = :org_id;"),
        {"id": purchase_doc_id, "org_id": org_id}
    ).fetchone()
    if not row:
        raise PurchaseServiceError(f"Purchase document {purchase_doc_id} not found.")

    if row.status == "approved":
        return {"id": purchase_doc_id, "status": "approved", "message": "Already approved"}

    session.execute(
        text("""
        UPDATE public.purchase_documents
        SET status = 'approved',
            approved_by = :user_id,
            approved_at = clock_timestamp(),
            updated_at = clock_timestamp()
        WHERE id = :id AND org_id = :org_id;
        """),
        {"id": purchase_doc_id, "org_id": org_id, "user_id": user_id}
    )
    return {"id": purchase_doc_id, "status": "approved", "approved_by": user_id}


def record_purchase_payment(
    session: Session,
    org_id: str,
    payment_reference: str,
    payment_date: str,
    payment_mode: str,
    total_amount_paid: Decimal,
    bank_reference: Optional[str] = None,
    allocations: Optional[List[Dict[str, Any]]] = None
) -> Dict[str, Any]:
    """
    Records a payment made to a vendor and allocates it to one or more purchase documents.
    """
    payment_id = str(uuid.uuid4())
    session.execute(
        text("""
        INSERT INTO public.purchase_payments (
            id, org_id, payment_reference, payment_date, payment_mode,
            bank_reference, total_amount_paid
        ) VALUES (
            :id, :org_id, :payment_reference, :payment_date, :payment_mode,
            :bank_reference, :total_amount_paid
        );
        """),
        {
            "id": payment_id,
            "org_id": org_id,
            "payment_reference": payment_reference,
            "payment_date": payment_date,
            "payment_mode": payment_mode,
            "bank_reference": bank_reference,
            "total_amount_paid": total_amount_paid
        }
    )

    created_allocations = []
    if allocations:
        for alloc in allocations:
            alloc_id = str(uuid.uuid4())
            purchase_doc_id = alloc["purchase_doc_id"]
            alloc_taxable = Decimal(str(alloc.get("allocated_taxable_amount", 0)))
            alloc_tax = Decimal(str(alloc.get("allocated_tax_amount", 0)))
            alloc_total = Decimal(str(alloc.get("allocated_total_amount", alloc_taxable + alloc_tax)))

            session.execute(
                text("""
                INSERT INTO public.purchase_payment_allocations (
                    id, org_id, payment_id, purchase_doc_id,
                    allocated_taxable_amount, allocated_tax_amount,
                    allocated_total_amount
                ) VALUES (
                    :id, :org_id, :payment_id, :purchase_doc_id,
                    :allocated_taxable_amount, :allocated_tax_amount,
                    :allocated_total_amount
                );
                """),
                {
                    "id": alloc_id,
                    "org_id": org_id,
                    "payment_id": payment_id,
                    "purchase_doc_id": purchase_doc_id,
                    "allocated_taxable_amount": alloc_taxable,
                    "allocated_tax_amount": alloc_tax,
                    "allocated_total_amount": alloc_total
                }
            )
            created_allocations.append(alloc_id)

    return {
        "payment_id": payment_id,
        "payment_reference": payment_reference,
        "total_amount_paid": str(total_amount_paid),
        "allocation_count": len(created_allocations)
    }
