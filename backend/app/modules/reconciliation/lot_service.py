"""
Milestone 2.7: Database-Enforced Goods Receipt Lot Ledger Service
Enforces Section 16(2)(b) First Proviso statutory last-lot condition:
- Immutable goods receipt lot ledger
- Concurrency protection via row-level locking (SELECT ... FOR UPDATE)
- Database-derived final lot calculation (no client boolean trusted)
- Duplicate lot detection and over-receipt bounds
"""
import uuid
from decimal import Decimal
from typing import Dict, Any, List, Optional
from datetime import datetime

from sqlalchemy.orm import Session
from sqlalchemy import text
from fastapi import HTTPException

from app.modules.reconciliation.schemas import LotLedgerEntryCreate, LotLedgerEntryOut


def record_lot_to_ledger(
    session: Session,
    org_id: str,
    purchase_doc_id: str,
    payload: LotLedgerEntryCreate,
    user_id: str
) -> Dict[str, Any]:
    """
    Records a verified lot delivery in public.goods_receipt_lot_ledger.
    Calculates final lot status strictly from database records and verified quantities.
    Enforces Section 16(2)(b) First Proviso: full ITC remains held (PARTIALLY_RECEIVED)
    until the final expected lot is verified.
    """
    # 1. Acquire row lock on target purchase document to serialize concurrent lot operations
    doc = session.execute(
        text("""
        SELECT id, goods_services_type, total_lots_expected, lots_received_count,
               quantity_expected, quantity_received, remaining_quantity,
               receipt_status, is_last_lot_received
        FROM public.purchase_documents
        WHERE id = :id AND org_id = :org
        FOR UPDATE;
        """),
        {"id": purchase_doc_id, "org": org_id}
    ).fetchone()

    if not doc:
        raise HTTPException(status_code=404, detail="Purchase document not found")

    gst_type = (doc.goods_services_type or "goods").lower()
    if gst_type == "services":
        raise HTTPException(
            status_code=400,
            detail="Lot delivery tracking applies exclusively to goods. Inward services require completion date and acceptance certificate."
        )

    expected_lots = payload.expected_lots_count or doc.total_lots_expected or 1
    expected_qty = doc.quantity_expected or Decimal("0.00")

    # 2. Acquire lock and fetch all existing verified lots from immutable ledger
    existing_lots = session.execute(
        text("""
        SELECT id, lot_number, lot_reference, received_quantity, verification_status, is_final_lot
        FROM public.goods_receipt_lot_ledger
        WHERE purchase_doc_id = :pid AND org_id = :org AND verification_status = 'VERIFIED'
        ORDER BY lot_number ASC
        FOR UPDATE;
        """),
        {"pid": purchase_doc_id, "org": org_id}
    ).fetchall()

    # 3. Duplicate checks
    for ex in existing_lots:
        if ex.lot_number == payload.lot_number:
            raise HTTPException(
                status_code=409,
                detail=f"Duplicate lot rejected: lot #{payload.lot_number} has already been verified and recorded in the lot ledger."
            )
        if ex.lot_reference.strip().upper() == payload.lot_reference.strip().upper():
            raise HTTPException(
                status_code=409,
                detail=f"Duplicate lot reference rejected: lot reference '{payload.lot_reference}' already exists in ledger."
            )

    # 4. Sequential verification check
    expected_next_lot = len(existing_lots) + 1
    if payload.lot_number != expected_next_lot:
        raise HTTPException(
            status_code=400,
            detail=f"Sequential lot receipt required: expected lot #{expected_next_lot}, but received lot #{payload.lot_number}."
        )

    if payload.lot_number > expected_lots:
        raise HTTPException(
            status_code=400,
            detail=f"Lot number {payload.lot_number} exceeds total expected lots ({expected_lots})."
        )

    # 5. Quantity calculations & Over-receipt validation
    cumulative_qty = sum(Decimal(str(l.received_quantity)) for l in existing_lots) + payload.received_quantity

    if expected_qty > Decimal("0.00"):
        max_tolerable = expected_qty * Decimal("1.10")  # 10% maximum allowable commercial over-receipt
        if cumulative_qty > max_tolerable:
            raise HTTPException(
                status_code=400,
                detail=f"Over-receipt rejected: cumulative received quantity {cumulative_qty} exceeds expected order quantity {expected_qty} beyond allowable tolerance."
            )

    remaining_qty = max(Decimal("0.00"), expected_qty - cumulative_qty) if expected_qty > Decimal("0.00") else Decimal("0.00")

    # 6. Database-derived Last-Lot Determination (Section 16(2)(b) First Proviso)
    # The document achieves FULL receipt IF AND ONLY IF:
    # - Lot number equals expected total lots, AND
    # - Cumulative received quantity meets at least 95% of expected quantity (if expected quantity is set)
    is_final_lot = (payload.lot_number == expected_lots)
    if expected_qty > Decimal("0.00") and cumulative_qty < (expected_qty * Decimal("0.95")):
        # If order quantity is still significantly incomplete, do not finalize even if lot count matches
        is_final_lot = False

    derived_receipt_status = "RECEIVED" if is_final_lot else "PARTIALLY_RECEIVED"

    # 7. Insert immutable entry into public.goods_receipt_lot_ledger
    lot_id = str(uuid.uuid4())
    now_ts = datetime.utcnow()

    session.execute(
        text("""
        INSERT INTO public.goods_receipt_lot_ledger (
            id, org_id, purchase_doc_id, lot_number, expected_lots_count,
            expected_quantity, received_quantity, cumulative_received_quantity,
            lot_reference, receipt_date, goods_receipt_note_ref,
            carrier_name, vehicle_number, eway_bill_number,
            verifier_id, evidence_location, verification_status,
            is_final_lot, created_at
        ) VALUES (
            :id, :org, :pid, :lot_num, :exp_lots,
            :exp_qty, :rec_qty, :cum_qty,
            :lot_ref, :rcpt_dt, :grn_ref,
            :carrier, :veh, :eway,
            :uid, :evidence, 'VERIFIED',
            :is_final, :ts
        );
        """),
        {
            "id": lot_id,
            "org": org_id,
            "pid": purchase_doc_id,
            "lot_num": payload.lot_number,
            "exp_lots": expected_lots,
            "exp_qty": expected_qty,
            "rec_qty": payload.received_quantity,
            "cum_qty": cumulative_qty,
            "lot_ref": payload.lot_reference,
            "rcpt_dt": payload.receipt_date,
            "grn_ref": payload.goods_receipt_note_ref,
            "carrier": payload.carrier_name,
            "veh": payload.vehicle_number,
            "eway": payload.eway_bill_number,
            "uid": user_id,
            "evidence": payload.evidence_location,
            "is_final": is_final_lot,
            "ts": now_ts
        }
    )

    # 8. Update parent purchase_documents with derived state
    session.execute(
        text("""
        UPDATE public.purchase_documents
        SET total_lots_expected = :exp_lots,
            lots_received_count = :cnt,
            quantity_received = :cum_qty,
            remaining_quantity = :rem_qty,
            is_last_lot_received = :is_final,
            receipt_status = :st,
            delivery_challan_ref = :lot_ref,
            goods_receipt_note_ref = :grn_ref,
            goods_receipt_date = :rcpt_dt,
            receipt_verified_by = :uid,
            receipt_verified_at = :ts,
            updated_at = :ts
        WHERE id = :pid AND org_id = :org;
        """),
        {
            "exp_lots": expected_lots,
            "cnt": payload.lot_number,
            "cum_qty": cumulative_qty,
            "rem_qty": remaining_qty,
            "is_final": is_final_lot,
            "st": derived_receipt_status,
            "lot_ref": payload.lot_reference,
            "grn_ref": payload.goods_receipt_note_ref,
            "rcpt_dt": payload.receipt_date,
            "uid": user_id,
            "ts": now_ts,
            "pid": purchase_doc_id,
            "org": org_id
        }
    )

    return {
        "lot_ledger_entry_id": lot_id,
        "purchase_doc_id": purchase_doc_id,
        "lot_number": payload.lot_number,
        "expected_lots_count": expected_lots,
        "received_quantity": str(payload.received_quantity),
        "cumulative_received_quantity": str(cumulative_qty),
        "remaining_quantity": str(remaining_qty),
        "is_final_lot": is_final_lot,
        "is_last_lot_received": is_final_lot,
        "lots_received_count": payload.lot_number,
        "receipt_status": derived_receipt_status,
        "section_16_2_b_proviso_condition_met": is_final_lot,
        "verified_at": now_ts.isoformat()
    }


def list_document_lots(
    session: Session,
    org_id: str,
    purchase_doc_id: str
) -> List[Dict[str, Any]]:
    """Returns all verified lot ledger entries for a purchase document."""
    rows = session.execute(
        text("""
        SELECT * FROM public.goods_receipt_lot_ledger
        WHERE purchase_doc_id = :pid AND org_id = :org
        ORDER BY lot_number ASC;
        """),
        {"pid": purchase_doc_id, "org": org_id}
    ).fetchall()

    return [
        {
            "id": str(r.id),
            "purchase_doc_id": str(r.purchase_doc_id),
            "lot_number": r.lot_number,
            "expected_lots_count": r.expected_lots_count,
            "expected_quantity": str(r.expected_quantity),
            "received_quantity": str(r.received_quantity),
            "cumulative_received_quantity": str(r.cumulative_received_quantity),
            "lot_reference": r.lot_reference,
            "receipt_date": str(r.receipt_date),
            "goods_receipt_note_ref": r.goods_receipt_note_ref,
            "carrier_name": r.carrier_name,
            "vehicle_number": r.vehicle_number,
            "eway_bill_number": r.eway_bill_number,
            "verifier_id": str(r.verifier_id),
            "verification_status": r.verification_status,
            "is_final_lot": r.is_final_lot,
            "created_at": r.created_at.isoformat()
        }
        for r in rows
    ]
