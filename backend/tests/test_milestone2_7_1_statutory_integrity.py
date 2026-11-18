"""
Milestone 2.7.1: Last-Lot Statutory Integrity, Accounting States & Production Data Closure Test Suite
Validates:
1. Complete removal of the 95% last-lot release rule (First Proviso to Section 16(2)(b)).
   - Tests 94%, 95%, 99% partial delivery: ITC strictly held (PARTIALLY_RECEIVED).
   - Tests 100% full delivery with verified final lot: ITC released (RECEIVED).
2. Removal of unsafe over-receipt tolerance (no expected_quantity * 1.05 / 1.10 acceptance).
   - Excess quantities are rejected with HTTP 400 and cannot generate ITC without authorized PO amendment.
3. Missing final lot: Full quantity in Lot 1 when 2 lots expected strictly keeps ITC held.
4. Duplicate lot (409) and out-of-order lot (400) rejections.
5. Production date validation:
   - Future receipt dates (> date.today()) rejected with HTTP 400.
   - Receipt dates preceding document date rejected with HTTP 400.
   - Impossible future invoice dates rejected on creation.
6. Separation of Non-Availment from Reversal:
   - Explicit statutory accounting states: NOT_YET_ELIGIBLE, ELIGIBLE_NOT_AVAILED, AVAILED, REVERSED, RECLAIMABLE, RECLAIMED.
   - Reversal strictly prohibited on unavailed documents (HTTP 400).
   - Reversal on availed document transitions state and records reversal event.
   - Reclaim must reference original reversal event and transitions state to RECLAIMED.
7. Rule 37A statutory deadlines tied to Financial Year of AVAILMENT, not invoice date.
8. Concurrent final-lot verification serialization via row-level locking (SELECT FOR UPDATE).
9. Cross-tenant RLS isolation on lot ledger and accounting states.
"""
import uuid
import random
import concurrent.futures
from datetime import date, datetime, timedelta
from decimal import Decimal
import pytest
import psycopg
from fastapi.testclient import TestClient

from app.main import app
from app.modules.reconciliation.itc_engine import StatutoryITCRiskEngine
from app.modules.reconciliation.schemas import ITCEligibilityEnum

client = TestClient(app)

DB_ADMIN = "postgresql://postgres:postgres@localhost:5432/myca"


@pytest.fixture
def setup_m2_7_1_tenant():
    """
    Provisions a clean, isolated multi-tenant environment with registered PAN,
    two GST registrations, and active memberships for testing Milestone 2.7.1.
    """
    org_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    user_id_2 = str(uuid.uuid4())
    reg_id_mh = str(uuid.uuid4())
    reg_id_gj = str(uuid.uuid4())

    rand_prefix = "".join(random.choices("ABCDEFGHIJKLMNOPQRSTUVWXYZ", k=3))
    rand_digits = "".join(random.choices("0123456789", k=4))
    pan = f"{rand_prefix}CB{rand_digits}K"
    gstin_mh = f"27{pan}1ZT"
    gstin_gj = f"24{pan}1ZR"

    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO public.organizations (id, legal_name, trade_name, pan)
                VALUES (%s, 'Milestone 2.7.1 Integrity Corp', 'M271 Corp', %s);
            """, (org_id, pan))

            cur.execute("""
                INSERT INTO public.global_users (id, email, full_name)
                VALUES (%s, %s, 'Chief Accountant M271'),
                       (%s, %s, 'Auditor M271');
            """, (user_id, f"acct_{rand_digits}@m271.test",
                  user_id_2, f"auditor_{rand_digits}@m271.test"))

            cur.execute("""
                INSERT INTO public.organization_memberships (org_id, user_id, role, status)
                VALUES (%s, %s, 'owner', 'active'),
                       (%s, %s, 'admin', 'active');
            """, (org_id, user_id, org_id, user_id_2))

            cur.execute("""
                INSERT INTO public.gst_registrations (id, org_id, gstin, legal_name, state_code, is_primary)
                VALUES (%s, %s, %s, 'Milestone 2.7.1 Integrity Corp MH', '27', true),
                       (%s, %s, %s, 'Milestone 2.7.1 Integrity Corp GJ', '24', false);
            """, (reg_id_mh, org_id, gstin_mh, reg_id_gj, org_id, gstin_gj))

    return {
        "org_id": org_id,
        "user_id": user_id,
        "user_id_2": user_id_2,
        "reg_mh": reg_id_mh,
        "reg_gj": reg_id_gj,
        "pan": pan,
        "gstin_mh": gstin_mh,
        "gstin_gj": gstin_gj
    }


# =============================================================================
# 1. Critical: Removal of 95% Threshold (94%, 95%, 99% Blocked; 100% Released)
# =============================================================================

def test_first_proviso_percentage_removal_94_95_99_blocked_100_released(setup_m2_7_1_tenant):
    """
    Verifies Section 16(2)(b) First Proviso statutory last-lot condition:
    Full receipt is derived strictly when cumulative quantity equals 100% of expected
    and the final required lot is physically verified.
    Releasing ITC at 94%, 95%, or 99% is strictly prohibited.
    """
    ctx = setup_m2_7_1_tenant
    headers = {"X-User-Id": ctx["user_id"], "X-Org-Id": ctx["org_id"]}

    # Create document expecting 1500 units in 3 lots
    p_id = str(uuid.uuid4())
    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO public.purchase_documents (
                    id, org_id, registration_id, document_type, document_number,
                    document_date, financial_period, supplier_gstin, supplier_legal_name,
                    recipient_gstin, place_of_supply, goods_services_type, receipt_status,
                    total_lots_expected, quantity_expected, remaining_quantity,
                    itc_accounting_state, status
                ) VALUES (%s, %s, %s, 'tax_invoice', 'INV-STAT-LOTS-1500', '2026-11-01', '2026-11',
                          '27AAACR1234K1ZA', 'Precision Industrial Alloys', %s, '27', 'goods', 'UNKNOWN',
                          3, 1500.00, 1500.00, 'NOT_YET_ELIGIBLE', 'approved');
            """, (p_id, ctx["org_id"], ctx["reg_mh"], ctx["gstin_mh"]))

    # Lot 1: Delivery of 500 units (33.3%)
    resp_l1 = client.post(
        f"/api/v1/reconciliation/purchase/{p_id}/record-lot",
        headers=headers,
        json={
            "lot_number": 1,
            "expected_lots_count": 3,
            "lot_reference": "DC-PIA-01",
            "receipt_date": "2026-11-03",
            "quantity_received": "500.00"
        }
    )
    assert resp_l1.status_code == 200
    assert resp_l1.json()["receipt_status"] == "PARTIALLY_RECEIVED"
    assert resp_l1.json()["is_final_lot"] is False
    assert resp_l1.json()["itc_accounting_state"] == "NOT_YET_ELIGIBLE"

    # Lot 2: Delivery of 910 units (Cumulative 1410 / 1500 = 94.0%)
    resp_l2 = client.post(
        f"/api/v1/reconciliation/purchase/{p_id}/record-lot",
        headers=headers,
        json={
            "lot_number": 2,
            "expected_lots_count": 3,
            "lot_reference": "DC-PIA-02",
            "receipt_date": "2026-11-05",
            "quantity_received": "910.00"
        }
    )
    assert resp_l2.status_code == 200
    assert Decimal(resp_l2.json()["cumulative_received_quantity"]) == Decimal("1410.00")
    assert resp_l2.json()["receipt_status"] == "PARTIALLY_RECEIVED"
    assert resp_l2.json()["is_final_lot"] is False
    assert resp_l2.json()["itc_accounting_state"] == "NOT_YET_ELIGIBLE"

    # Scenario A: If Lot 3 delivered only 15 units (cumulative 1425 / 1500 = 95.0%)
    # Even though it's lot #3 (expected lots count), 5% remains undelivered.
    # The old flawed rule would have released ITC here! The corrected statutory engine MUST block it.
    p_id_95 = str(uuid.uuid4())
    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO public.purchase_documents (
                    id, org_id, registration_id, document_type, document_number,
                    document_date, financial_period, supplier_gstin, supplier_legal_name,
                    recipient_gstin, place_of_supply, goods_services_type, receipt_status,
                    total_lots_expected, quantity_expected, remaining_quantity,
                    itc_accounting_state, status
                ) VALUES (%s, %s, %s, 'tax_invoice', 'INV-95-TEST', '2026-11-01', '2026-11',
                          '27AAACR1234K1ZA', 'Precision Industrial Alloys', %s, '27', 'goods', 'UNKNOWN',
                          2, 1500.00, 1500.00, 'NOT_YET_ELIGIBLE', 'approved');
            """, (p_id_95, ctx["org_id"], ctx["reg_mh"], ctx["gstin_mh"]))

    # Lot 1: 1000 units
    client.post(
        f"/api/v1/reconciliation/purchase/{p_id_95}/record-lot",
        headers=headers,
        json={"lot_number": 1, "expected_lots_count": 2, "lot_reference": "DC-95-01", "receipt_date": "2026-11-03", "quantity_received": "1000.00"}
    )
    # Lot 2 (final lot count): 425 units -> Total 1425 (Exactly 95.0%)
    resp_95 = client.post(
        f"/api/v1/reconciliation/purchase/{p_id_95}/record-lot",
        headers=headers,
        json={"lot_number": 2, "expected_lots_count": 2, "lot_reference": "DC-95-02", "receipt_date": "2026-11-06", "quantity_received": "425.00"}
    )
    assert resp_95.status_code == 200
    assert Decimal(resp_95.json()["cumulative_received_quantity"]) == Decimal("1425.00")
    assert resp_95.json()["is_final_lot"] is False  # CRITICAL: 95% threshold rejected!
    assert resp_95.json()["receipt_status"] == "PARTIALLY_RECEIVED"
    assert resp_95.json()["section_16_2_b_proviso_condition_met"] is False
    assert resp_95.json()["itc_accounting_state"] == "NOT_YET_ELIGIBLE"

    # Scenario B: 99.0% delivery (1485 / 1500 units)
    p_id_99 = str(uuid.uuid4())
    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO public.purchase_documents (
                    id, org_id, registration_id, document_type, document_number,
                    document_date, financial_period, supplier_gstin, supplier_legal_name,
                    recipient_gstin, place_of_supply, goods_services_type, receipt_status,
                    total_lots_expected, quantity_expected, remaining_quantity,
                    itc_accounting_state, status
                ) VALUES (%s, %s, %s, 'tax_invoice', 'INV-99-TEST', '2026-11-01', '2026-11',
                          '27AAACR1234K1ZA', 'Precision Industrial Alloys', %s, '27', 'goods', 'UNKNOWN',
                          2, 1500.00, 1500.00, 'NOT_YET_ELIGIBLE', 'approved');
            """, (p_id_99, ctx["org_id"], ctx["reg_mh"], ctx["gstin_mh"]))

    client.post(
        f"/api/v1/reconciliation/purchase/{p_id_99}/record-lot",
        headers=headers,
        json={"lot_number": 1, "expected_lots_count": 2, "lot_reference": "DC-99-01", "receipt_date": "2026-11-03", "quantity_received": "1000.00"}
    )
    # Lot 2: 485 units -> Total 1485 (99.0%)
    resp_99 = client.post(
        f"/api/v1/reconciliation/purchase/{p_id_99}/record-lot",
        headers=headers,
        json={"lot_number": 2, "expected_lots_count": 2, "lot_reference": "DC-99-02", "receipt_date": "2026-11-07", "quantity_received": "485.00"}
    )
    assert resp_99.status_code == 200
    assert Decimal(resp_99.json()["cumulative_received_quantity"]) == Decimal("1485.00")
    assert resp_99.json()["is_final_lot"] is False  # 99% still blocked!
    assert resp_99.json()["receipt_status"] == "PARTIALLY_RECEIVED"
    assert resp_99.json()["section_16_2_b_proviso_condition_met"] is False

    # Scenario C: Completing the original document with 100% quantity (remaining 90 units)
    # Total reaches 1500.00 units on lot 3 of 3 -> Full receipt released!
    resp_l3 = client.post(
        f"/api/v1/reconciliation/purchase/{p_id}/record-lot",
        headers=headers,
        json={
            "lot_number": 3,
            "expected_lots_count": 3,
            "lot_reference": "DC-PIA-03-FINAL",
            "receipt_date": "2026-11-08",
            "quantity_received": "90.00"
        }
    )
    assert resp_l3.status_code == 200
    assert Decimal(resp_l3.json()["cumulative_received_quantity"]) == Decimal("1500.00")
    assert Decimal(resp_l3.json()["remaining_quantity"]) == Decimal("0.00")
    assert resp_l3.json()["is_final_lot"] is True
    assert resp_l3.json()["receipt_status"] == "RECEIVED"
    assert resp_l3.json()["section_16_2_b_proviso_condition_met"] is True
    assert resp_l3.json()["itc_accounting_state"] == "ELIGIBLE_NOT_AVAILED"


# =============================================================================
# 2. Critical: Removal of Unsafe Over-Receipt Tolerance
# =============================================================================

def test_over_receipt_strict_rejection_no_unsafe_tolerance(setup_m2_7_1_tenant):
    """
    Verifies that unsafe over-receipt tolerances (e.g. 1.05 or 1.10) are removed.
    Any delivery where cumulative_qty > expected_qty is rejected with HTTP 400.
    Excess receipts must be quarantined and cannot generate additional ITC without an authorized PO amendment.
    """
    ctx = setup_m2_7_1_tenant
    headers = {"X-User-Id": ctx["user_id"], "X-Org-Id": ctx["org_id"]}

    p_id = str(uuid.uuid4())
    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO public.purchase_documents (
                    id, org_id, registration_id, document_type, document_number,
                    document_date, financial_period, supplier_gstin, supplier_legal_name,
                    recipient_gstin, place_of_supply, goods_services_type, receipt_status,
                    total_lots_expected, quantity_expected, remaining_quantity
                ) VALUES (%s, %s, %s, 'tax_invoice', 'INV-OVER-RECEIPT', '2026-11-01', '2026-11',
                          '27AAACR1234K1ZA', 'Steel Mills Corp', %s, '27', 'goods', 'UNKNOWN',
                          2, 1000.00, 1000.00);
            """, (p_id, ctx["org_id"], ctx["reg_mh"], ctx["gstin_mh"]))

    # Lot 1: 500 units
    client.post(
        f"/api/v1/reconciliation/purchase/{p_id}/record-lot",
        headers=headers,
        json={"lot_number": 1, "expected_lots_count": 2, "lot_reference": "DC-OVER-01", "receipt_date": "2026-11-03", "quantity_received": "500.00"}
    )

    # Lot 2: 501 units (Cumulative 1001.00, exceeding expected 1000.00 by 1 unit)
    # Under old 10% tolerance, this would pass. Under Milestone 2.7.1, it MUST be rejected with HTTP 400.
    resp_over = client.post(
        f"/api/v1/reconciliation/purchase/{p_id}/record-lot",
        headers=headers,
        json={
            "lot_number": 2,
            "expected_lots_count": 2,
            "lot_reference": "DC-OVER-02",
            "receipt_date": "2026-11-06",
            "quantity_received": "501.00"
        }
    )
    assert resp_over.status_code == 400
    assert "Over-receipt rejected" in resp_over.json()["detail"]
    assert "exceeds contracted order/invoice quantity" in resp_over.json()["detail"]


# =============================================================================
# 3. Missing Final Lot with Full Quantity Keeps ITC Blocked
# =============================================================================

def test_missing_final_lot_full_quantity_still_holds_itc(setup_m2_7_1_tenant):
    """
    If an order is contracted in 2 lots, delivering 100% of quantity in Lot 1
    does NOT fulfill the First Proviso until the final required lot is verified or amended.
    """
    ctx = setup_m2_7_1_tenant
    headers = {"X-User-Id": ctx["user_id"], "X-Org-Id": ctx["org_id"]}

    p_id = str(uuid.uuid4())
    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO public.purchase_documents (
                    id, org_id, registration_id, document_type, document_number,
                    document_date, financial_period, supplier_gstin, supplier_legal_name,
                    recipient_gstin, place_of_supply, goods_services_type, receipt_status,
                    total_lots_expected, quantity_expected, remaining_quantity
                ) VALUES (%s, %s, %s, 'tax_invoice', 'INV-2LOT-SINGLE-DELIVERY', '2026-11-01', '2026-11',
                          '27AAACR1234K1ZA', 'Industrial Supplies Ltd', %s, '27', 'goods', 'UNKNOWN',
                          2, 1000.00, 1000.00);
            """, (p_id, ctx["org_id"], ctx["reg_mh"], ctx["gstin_mh"]))

    # Lot 1 delivers 1000.00 units (100% of quantity), but expected_lots = 2
    resp_l1 = client.post(
        f"/api/v1/reconciliation/purchase/{p_id}/record-lot",
        headers=headers,
        json={
            "lot_number": 1,
            "expected_lots_count": 2,
            "lot_reference": "DC-FULL-IN-LOT1",
            "receipt_date": "2026-11-04",
            "quantity_received": "1000.00"
        }
    )
    assert resp_l1.status_code == 200
    assert resp_l1.json()["is_final_lot"] is False
    assert resp_l1.json()["receipt_status"] == "PARTIALLY_RECEIVED"
    assert resp_l1.json()["section_16_2_b_proviso_condition_met"] is False


# =============================================================================
# 4. Duplicate and Out-of-Order Lot Rejection
# =============================================================================

def test_duplicate_and_out_of_order_lot_rejection(setup_m2_7_1_tenant):
    """
    Verifies that duplicate lot numbers / references are rejected with HTTP 409
    and non-sequential lot submissions are rejected with HTTP 400.
    """
    ctx = setup_m2_7_1_tenant
    headers = {"X-User-Id": ctx["user_id"], "X-Org-Id": ctx["org_id"]}

    p_id = str(uuid.uuid4())
    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO public.purchase_documents (
                    id, org_id, registration_id, document_type, document_number,
                    document_date, financial_period, supplier_gstin, supplier_legal_name,
                    recipient_gstin, place_of_supply, goods_services_type, receipt_status,
                    total_lots_expected, quantity_expected, remaining_quantity
                ) VALUES (%s, %s, %s, 'tax_invoice', 'INV-LOT-ORDER-TEST', '2026-11-01', '2026-11',
                          '27AAACR1234K1ZA', 'Industrial Supplies Ltd', %s, '27', 'goods', 'UNKNOWN',
                          3, 900.00, 900.00);
            """, (p_id, ctx["org_id"], ctx["reg_mh"], ctx["gstin_mh"]))

    # 1. Record Lot #1
    resp_l1 = client.post(
        f"/api/v1/reconciliation/purchase/{p_id}/record-lot",
        headers=headers,
        json={"lot_number": 1, "expected_lots_count": 3, "lot_reference": "DC-SEQ-01", "receipt_date": "2026-11-03", "quantity_received": "300.00"}
    )
    assert resp_l1.status_code == 200

    # 2. Duplicate Lot #1 -> HTTP 409 Conflict
    resp_dup = client.post(
        f"/api/v1/reconciliation/purchase/{p_id}/record-lot",
        headers=headers,
        json={"lot_number": 1, "expected_lots_count": 3, "lot_reference": "DC-SEQ-01-DUP", "receipt_date": "2026-11-04", "quantity_received": "300.00"}
    )
    assert resp_dup.status_code == 409
    assert "Duplicate lot rejected" in resp_dup.json()["detail"]

    # 3. Non-sequential: Submitting Lot #3 while Lot #2 is missing -> HTTP 400
    resp_skip = client.post(
        f"/api/v1/reconciliation/purchase/{p_id}/record-lot",
        headers=headers,
        json={"lot_number": 3, "expected_lots_count": 3, "lot_reference": "DC-SEQ-03", "receipt_date": "2026-11-05", "quantity_received": "300.00"}
    )
    assert resp_skip.status_code == 400
    assert "Sequential lot receipt required" in resp_skip.json()["detail"]


# =============================================================================
# 5. Production Date Validation (Future Dates & Inverted Chronology Rejected)
# =============================================================================

def test_production_date_validation_future_and_preceding_dates_rejected(setup_m2_7_1_tenant):
    """
    Rejects impossible future-dated invoice and receipt dates in production (> date.today()).
    Rejects receipt dates preceding invoice document date.
    """
    ctx = setup_m2_7_1_tenant
    headers = {"X-User-Id": ctx["user_id"], "X-Org-Id": ctx["org_id"]}

    future_dt = (date.today() + timedelta(days=2)).isoformat()
    today_dt = date.today().isoformat()

    # 1. Invoice creation with future document date -> Rejected
    resp_future_inv = client.post(
        "/api/v1/reconciliation/purchase/create",
        headers=headers,
        json={
            "registration_id": ctx["reg_mh"],
            "document_type": "tax_invoice",
            "document_number": "INV-FUTURE-TEST",
            "document_date": future_dt,
            "financial_period": "2026-11",
            "supplier_gstin": "27AAACR1234K1ZA",
            "supplier_legal_name": "Future Vendor Ltd",
            "recipient_gstin": ctx["gstin_mh"],
            "place_of_supply": "27",
            "total_taxable_amount": "50000.00",
            "cgst_amount": "4500.00",
            "sgst_amount": "4500.00",
            "igst_amount": "0.00",
            "total_invoice_value": "59000.00",
            "line_items": []
        }
    )
    assert resp_future_inv.status_code == 400  # HTTP 400 Bad Request
    assert "cannot be later than current date" in resp_future_inv.json()["detail"]

    # 2. Create valid document dated 2026-11-05
    p_id = str(uuid.uuid4())
    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO public.purchase_documents (
                    id, org_id, registration_id, document_type, document_number,
                    document_date, financial_period, supplier_gstin, supplier_legal_name,
                    recipient_gstin, place_of_supply, goods_services_type, receipt_status,
                    total_lots_expected, quantity_expected, remaining_quantity
                ) VALUES (%s, %s, %s, 'tax_invoice', 'INV-DATE-CHRONO', '2026-11-05', '2026-11',
                          '27AAACR1234K1ZA', 'Future Vendor Ltd', %s, '27', 'goods', 'UNKNOWN',
                          2, 100.00, 100.00);
            """, (p_id, ctx["org_id"], ctx["reg_mh"], ctx["gstin_mh"]))

    # 3. Lot receipt date in the future -> HTTP 400
    resp_future_rcpt = client.post(
        f"/api/v1/reconciliation/purchase/{p_id}/record-lot",
        headers=headers,
        json={"lot_number": 1, "expected_lots_count": 2, "lot_reference": "DC-FUT-01", "receipt_date": future_dt, "quantity_received": "50.00"}
    )
    assert resp_future_rcpt.status_code == 400
    assert "Future receipt date rejected" in resp_future_rcpt.json()["detail"]

    # 4. Lot receipt date preceding invoice date (e.g. 2026-11-01 when invoice is 2026-11-05) -> HTTP 400
    resp_preceding = client.post(
        f"/api/v1/reconciliation/purchase/{p_id}/record-lot",
        headers=headers,
        json={"lot_number": 1, "expected_lots_count": 2, "lot_reference": "DC-PREC-01", "receipt_date": "2026-11-01", "quantity_received": "50.00"}
    )
    assert resp_preceding.status_code == 400
    assert "cannot precede invoice/document date" in resp_preceding.json()["detail"]


# =============================================================================
# 6. Separation of Non-Availment from Reversal
# =============================================================================

def test_non_availment_versus_reversal_state_separation(setup_m2_7_1_tenant):
    """
    Verifies statutory separation between non-availment and reversals:
    - An invoice that has not been availed in GSTR-3B cannot generate a reversal event.
    - Transitions through explicit statutory accounting states:
      NOT_YET_ELIGIBLE -> ELIGIBLE_NOT_AVAILED -> AVAILED -> REVERSED / RECLAIMABLE -> RECLAIMED.
    """
    ctx = setup_m2_7_1_tenant
    headers = {"X-User-Id": ctx["user_id"], "X-Org-Id": ctx["org_id"]}

    # 1. Create document in purchase register
    p_id = str(uuid.uuid4())
    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO public.purchase_documents (
                    id, org_id, registration_id, document_type, document_number,
                    document_date, financial_period, supplier_gstin, supplier_legal_name,
                    recipient_gstin, place_of_supply, goods_services_type, receipt_status,
                    total_taxable_amount, cgst_amount, sgst_amount, igst_amount, cess_amount,
                    total_invoice_value, is_availed_in_3b, itc_accounting_state, status
                ) VALUES (%s, %s, %s, 'tax_invoice', 'INV-STATE-SEPARATION', '2026-11-02', '2026-11',
                          '27AAACR1234K1ZA', 'Reversal Test Supplier', %s, '27', 'goods', 'UNKNOWN',
                          100000.00, 9000.00, 9000.00, 0.00, 0.00,
                          118000.00, false, 'NOT_YET_ELIGIBLE', 'draft');
            """, (p_id, ctx["org_id"], ctx["reg_mh"], ctx["gstin_mh"]))

    # 2. Attempt to record reversal event on unavailed invoice -> Strictly rejected with HTTP 400
    resp_bad_rev = client.post(
        "/api/v1/reconciliation/itc-ledger/record-event",
        headers=headers,
        json={
            "purchase_doc_id": p_id,
            "event_type": "REVERSAL_RULE_37",
            "tax_period": "2026-11",
            "cgst_amount": "9000.00",
            "sgst_amount": "9000.00",
            "igst_amount": "0.00",
            "cess_amount": "0.00"
        }
    )
    assert resp_bad_rev.status_code == 400
    assert "Statutory Accounting Violation" in resp_bad_rev.json()["detail"]
    assert "Cannot record reversal for an invoice that has not been availed in GSTR-3B" in resp_bad_rev.json()["detail"]

    # 3. Complete receipt evidence -> transitions to ELIGIBLE_NOT_AVAILED once approved
    client.post(
        f"/api/v1/reconciliation/purchase/{p_id}/verify-receipt",
        headers=headers,
        json={"receipt_status": "RECEIVED", "goods_receipt_date": "2026-11-04", "goods_receipt_note_ref": "GRN-SEP-01"}
    )
    # Approve
    client.post(f"/api/v1/reconciliation/purchase/{p_id}/approve", headers=headers)

    with psycopg.connect(DB_ADMIN) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT itc_accounting_state, status FROM public.purchase_documents WHERE id = %s;", (p_id,))
            row = cur.fetchone()
            assert row[0] == "ELIGIBLE_NOT_AVAILED"
            assert row[1] == "approved"

    # 4. Avail in GSTR-3B -> transitions to AVAILED
    resp_avail = client.post(
        "/api/v1/reconciliation/itc-ledger/record-event",
        headers=headers,
        json={
            "purchase_doc_id": p_id,
            "event_type": "AVAIL",
            "tax_period": "2026-11",
            "cgst_amount": "9000.00",
            "sgst_amount": "9000.00",
            "igst_amount": "0.00",
            "cess_amount": "0.00"
        }
    )
    assert resp_avail.status_code == 200

    with psycopg.connect(DB_ADMIN) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT itc_accounting_state, is_availed_in_3b FROM public.purchase_documents WHERE id = %s;", (p_id,))
            row = cur.fetchone()
            assert row[0] == "AVAILED"
            assert row[1] is True

    # 5. Record reclaimable reversal under Rule 37 -> transitions to RECLAIMABLE
    resp_rev = client.post(
        "/api/v1/reconciliation/itc-ledger/record-event",
        headers=headers,
        json={
            "purchase_doc_id": p_id,
            "event_type": "REVERSAL_RULE_37",
            "tax_period": "2026-11",
            "cgst_amount": "9000.00",
            "sgst_amount": "9000.00",
            "igst_amount": "0.00",
            "cess_amount": "0.00",
            "reversal_reason": "Vendor unpaid beyond 180 days"
        }
    )
    assert resp_rev.status_code == 200
    rev_id = resp_rev.json()["reversal_event_id"]
    assert rev_id is not None

    with psycopg.connect(DB_ADMIN) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT itc_accounting_state FROM public.purchase_documents WHERE id = %s;", (p_id,))
            row = cur.fetchone()
            assert row[0] == "RECLAIMABLE"

    # 6. Reclaim upon payment -> transitions to RECLAIMED
    resp_reclaim = client.post(
        "/api/v1/reconciliation/itc-ledger/record-event",
        headers=headers,
        json={
            "purchase_doc_id": p_id,
            "event_type": "RECLAIM",
            "tax_period": "2026-11",
            "cgst_amount": "9000.00",
            "sgst_amount": "9000.00",
            "igst_amount": "0.00",
            "cess_amount": "0.00",
            "reversal_event_id": rev_id,
            "reversal_reason": "Vendor paid in full"
        }
    )
    assert resp_reclaim.status_code == 200

    with psycopg.connect(DB_ADMIN) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT itc_accounting_state FROM public.purchase_documents WHERE id = %s;", (p_id,))
            row = cur.fetchone()
            assert row[0] == "RECLAIMED"


# =============================================================================
# 7. Rule 37A Statutory Deadlines Tied to FY of Availment
# =============================================================================

def test_rule_37a_financial_year_alignment_with_availment_period():
    """
    Verifies Rule 37A statutory text:
    - Deadlines (30-Sep supplier cut-off and 30-Nov recipient reversal) are tied to
      the Financial Year of recipient AVAILMENT (availed_period), not document date.
    - An unavailed invoice NEVER triggers a Rule 37A reversal.
    """
    engine = StatutoryITCRiskEngine()

    # Case A: Invoice dated 2025-08-15 (FY 2025-26), but availed in 2026-05 (FY 2026-27)
    # The end of FY of availment is 31st March 2027.
    # Therefore, supplier cut-off is 30-Sep-2027, recipient reversal deadline is 30-Nov-2027!
    doc_availed_fy27 = {
        "id": "doc-fy27",
        "supplier_gstin": "27AAACR1234K1ZA",
        "document_date": "2025-08-15",
        "financial_period": "2025-08",
        "availed_period": "2026-05",  # FY 2026-27
        "is_availed_in_3b": True,
        "cgst_amount": Decimal("10000.00"),
        "sgst_amount": Decimal("10000.00"),
        "igst_amount": Decimal("0.00"),
        "cess_amount": Decimal("0.00")
    }

    gstr2b_unfiled = {
        "id": "g2b-1",
        "raw_payload": {"gstr3b_status": "NOT_FILED"}
    }

    # Evaluation on 2026-11-15 (after 2026-09-30, but before 2027-09-30):
    # Under old logic (tied to invoice date FY 2025-26), this would trigger a reversal because 2026-09-30 passed!
    # Under corrected statutory logic (tied to availed_period FY 2026-27), supplier still has until 30-Sep-2027!
    eval_2026 = date(2026, 11, 15)
    check_2026 = engine.evaluate_rule_37a_details(doc_availed_fy27, gstr2b_unfiled, evaluation_date=eval_2026)
    assert check_2026.risk_status == ITCEligibilityEnum.POTENTIALLY_ELIGIBLE
    assert check_2026.reversal_required is False
    assert str(check_2026.supplier_filing_deadline) == "2027-09-30"
    assert str(check_2026.recipient_reversal_deadline) == "2027-11-30"

    # Case B: Invoice NOT availed in GSTR-3B
    doc_unavailed = {
        "id": "doc-unavailed",
        "supplier_gstin": "27AAACR1234K1ZA",
        "document_date": "2024-04-10",
        "financial_period": "2024-04",
        "is_availed_in_3b": False,
        "cgst_amount": Decimal("10000.00"),
        "sgst_amount": Decimal("10000.00"),
        "igst_amount": Decimal("0.00"),
        "cess_amount": Decimal("0.00")
    }
    check_unavailed = engine.evaluate_rule_37a_details(doc_unavailed, gstr2b_unfiled, evaluation_date=date(2026, 11, 18))
    assert check_unavailed.reversal_required is False
    assert check_unavailed.reversal_amount == Decimal("0.00")


# =============================================================================
# 8. Concurrent Final-Lot Verification Serialization via Row-Level Locking
# =============================================================================

def test_concurrent_final_lot_verification_serialization(setup_m2_7_1_tenant):
    """
    Verifies that concurrent requests attempting to verify the final lot simultaneously
    are properly serialized by SELECT FOR UPDATE row locking:
    Exactly one succeeds as the verified final lot, and the other is safely rejected (409 Conflict).
    """
    ctx = setup_m2_7_1_tenant
    headers = {"X-User-Id": ctx["user_id"], "X-Org-Id": ctx["org_id"]}

    p_id = str(uuid.uuid4())
    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO public.purchase_documents (
                    id, org_id, registration_id, document_type, document_number,
                    document_date, financial_period, supplier_gstin, supplier_legal_name,
                    recipient_gstin, place_of_supply, goods_services_type, receipt_status,
                    total_lots_expected, quantity_expected, remaining_quantity
                ) VALUES (%s, %s, %s, 'tax_invoice', 'INV-RACE-LOT', '2026-11-01', '2026-11',
                          '27AAACR1234K1ZA', 'Race Metal Corp', %s, '27', 'goods', 'UNKNOWN',
                          2, 1000.00, 1000.00);
            """, (p_id, ctx["org_id"], ctx["reg_mh"], ctx["gstin_mh"]))

    # Lot 1 recorded
    client.post(
        f"/api/v1/reconciliation/purchase/{p_id}/record-lot",
        headers=headers,
        json={"lot_number": 1, "expected_lots_count": 2, "lot_reference": "DC-RACE-01", "receipt_date": "2026-11-03", "quantity_received": "500.00"}
    )

    # 2 concurrent threads attempt to record Lot #2 (final lot) simultaneously
    def submit_final_lot(ref_code):
        c = TestClient(app)
        return c.post(
            f"/api/v1/reconciliation/purchase/{p_id}/record-lot",
            headers=headers,
            json={
                "lot_number": 2,
                "expected_lots_count": 2,
                "lot_reference": ref_code,
                "receipt_date": "2026-11-06",
                "quantity_received": "500.00"
            }
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(submit_final_lot, "DC-FINAL-THREAD-A")
        f2 = executor.submit(submit_final_lot, "DC-FINAL-THREAD-B")
        r1 = f1.result()
        r2 = f2.result()

    statuses = [r1.status_code, r2.status_code]
    assert 200 in statuses, f"Expected one 200 OK, got {statuses}"
    assert 409 in statuses, f"Expected one 409 Conflict, got {statuses}"

    # Verify exactly one lot #2 was recorded in DB
    with psycopg.connect(DB_ADMIN) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(*) FROM public.goods_receipt_lot_ledger
                WHERE purchase_doc_id = %s AND lot_number = 2;
            """, (p_id,))
            assert cur.fetchone()[0] == 1


# =============================================================================
# 9. Cross-Tenant RLS Isolation on Lot Ledger
# =============================================================================

def test_cross_tenant_lot_ledger_rls_isolation(setup_m2_7_1_tenant):
    """
    Verifies that goods_receipt_lot_ledger strictly isolates records between tenants
    under PostgreSQL Row Level Security.
    """
    ctx_a = setup_m2_7_1_tenant

    # Provision Tenant B
    org_b = str(uuid.uuid4())
    user_b = str(uuid.uuid4())
    reg_b = str(uuid.uuid4())
    rand_digits_b = "".join(random.choices("0123456789", k=4))
    pan_b = f"BBBCB{rand_digits_b}K"
    gstin_b = f"27{pan_b}1ZT"

    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO public.organizations (id, legal_name, trade_name, pan) VALUES (%s, 'Tenant B', 'TB', %s);", (org_b, pan_b))
            cur.execute("INSERT INTO public.global_users (id, email, full_name) VALUES (%s, %s, 'User B');", (user_b, f"user_b_{rand_digits_b}@tb.test"))
            cur.execute("INSERT INTO public.organization_memberships (org_id, user_id, role, status) VALUES (%s, %s, 'owner', 'active');", (org_b, user_b))
            cur.execute("INSERT INTO public.gst_registrations (id, org_id, gstin, legal_name, state_code, is_primary) VALUES (%s, %s, %s, 'Tenant B', '27', true);", (reg_b, org_b, gstin_b))

    headers_a = {"X-User-Id": ctx_a["user_id"], "X-Org-Id": ctx_a["org_id"]}
    headers_b = {"X-User-Id": user_b, "X-Org-Id": org_b}

    # Tenant A creates doc and lot
    p_id_a = str(uuid.uuid4())
    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO public.purchase_documents (
                    id, org_id, registration_id, document_type, document_number,
                    document_date, financial_period, supplier_gstin, supplier_legal_name,
                    recipient_gstin, place_of_supply, goods_services_type, receipt_status,
                    total_lots_expected, quantity_expected, remaining_quantity
                ) VALUES (%s, %s, %s, 'tax_invoice', 'INV-TENANT-A-DOC', '2026-11-01', '2026-11',
                          '27AAACR1234K1ZA', 'Supplier A', %s, '27', 'goods', 'UNKNOWN',
                          2, 100.00, 100.00);
            """, (p_id_a, ctx_a["org_id"], ctx_a["reg_mh"], ctx_a["gstin_mh"]))

    resp_a = client.post(
        f"/api/v1/reconciliation/purchase/{p_id_a}/record-lot",
        headers=headers_a,
        json={"lot_number": 1, "expected_lots_count": 2, "lot_reference": "DC-TENANT-A-01", "receipt_date": "2026-11-03", "quantity_received": "50.00"}
    )
    assert resp_a.status_code == 200

    # Tenant B attempts to read Tenant A's lots -> Returns empty list (404/empty due to RLS)
    resp_b_read = client.get(f"/api/v1/reconciliation/purchase/{p_id_a}/lots", headers=headers_b)
    assert resp_b_read.status_code == 200
    assert len(resp_b_read.json()) == 0

    # Tenant B attempts to record a lot against Tenant A's purchase document -> 404 Purchase document not found
    resp_b_write = client.post(
        f"/api/v1/reconciliation/purchase/{p_id_a}/record-lot",
        headers=headers_b,
        json={"lot_number": 2, "expected_lots_count": 2, "lot_reference": "DC-ATTACK-02", "receipt_date": "2026-11-04", "quantity_received": "50.00"}
    )
    assert resp_b_write.status_code == 404
