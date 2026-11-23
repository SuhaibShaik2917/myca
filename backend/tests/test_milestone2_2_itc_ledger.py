import os
import sys
import uuid
from decimal import Decimal
from datetime import date, timedelta
import pytest
import psycopg
from fastapi.testclient import TestClient

# Ensure vendor path is loaded for openpyxl
vendor_path = os.path.join(os.path.dirname(__file__), "..", "vendor")
if os.path.exists(vendor_path) and vendor_path not in sys.path:
    sys.path.insert(0, vendor_path)

from app.main import app
from app.modules.reconciliation.itc_engine import StatutoryITCRiskEngine
from app.modules.reconciliation.schemas import ITCEligibilityEnum
from app.modules.reconciliation.gstr3b_itc import generate_gstr3b_table4_summary

from tests.test_db_config import DB_ADMIN, DB_APP

client = TestClient(app)

import random
import string


def random_pan() -> str:
    letters = ''.join(random.choices(string.ascii_uppercase, k=5))
    digits = ''.join(random.choices(string.digits, k=4))
    last = random.choice(string.ascii_uppercase)
    return f"{letters}{digits}{last}"


def calculate_gstin_checksum(gstin14: str) -> str:
    chars = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    factor = 1
    total = 0
    for c in gstin14:
        code_point = chars.index(c)
        addend = factor * code_point
        factor = 1 if factor == 2 else 2
        addend = (addend // 36) + (addend % 36)
        total += addend
    remainder = total % 36
    check_code_point = (36 - remainder) % 36
    return chars[check_code_point]


def random_gstin(state_code: str = "27", pan: str = "AABCB1234M") -> str:
    base = f"{state_code}{pan}1Z"
    return base + calculate_gstin_checksum(base)


@pytest.fixture(scope="module")
def setup_m2_2_tenant():
    org_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    pan = random_pan()
    gstin_mh = random_gstin("27", pan)
    reg_mh = str(uuid.uuid4())

    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO public.organizations (id, legal_name, pan) VALUES (%s, 'Milestone 2.2 Test Org', %s);", (org_id, pan))
            cur.execute("INSERT INTO public.global_users (id, email, full_name) VALUES (%s, %s, 'M2.2 CA Reviewer');", (user_id, f"ca_{uuid.uuid4().hex}@example.com"))
            cur.execute("INSERT INTO public.organization_memberships (org_id, user_id, role, status) VALUES (%s, %s, 'owner', 'active');", (org_id, user_id))
            cur.execute("""
                INSERT INTO public.gst_registrations (id, org_id, gstin, state_code, trade_name, legal_name, is_primary)
                VALUES (%s, %s, %s, '27', 'M2.2 MH Primary', 'M2.2 Legal Entities Ltd', true);
            """, (reg_mh, org_id, gstin_mh))

    return {
        "org_id": org_id,
        "user_id": user_id,
        "pan": pan,
        "gstin_mh": gstin_mh,
        "reg_mh": reg_mh
    }


# =============================================================================
# 1. PAN Consistency Trigger Error Code (SQLSTATE 23514 CheckViolation)
# =============================================================================

def test_pan_trigger_sqlstate_23514_and_positive_binding(setup_m2_2_tenant):
    t = setup_m2_2_tenant
    mismatched_pan = random_pan()
    mismatched_gstin = random_gstin("27", mismatched_pan)
    valid_secondary_gstin = random_gstin("24", t["pan"])

    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            # Negative test: mismatched PAN raises CheckViolation with SQLSTATE 23514
            with pytest.raises(psycopg.errors.CheckViolation) as exc_info:
                cur.execute("""
                    INSERT INTO public.gst_registrations (id, org_id, gstin, state_code, trade_name, legal_name, is_primary)
                    VALUES (%s, %s, %s, '27', 'Mismatch Reg', 'M2.2 Legal Entities Ltd', false);
                """, (str(uuid.uuid4()), t["org_id"], mismatched_gstin))
            assert exc_info.value.sqlstate == "23514"
            assert "does not match Organization PAN" in str(exc_info.value)

            # Positive test: matching PAN succeeds
            sec_reg_id = str(uuid.uuid4())
            cur.execute("""
                INSERT INTO public.gst_registrations (id, org_id, gstin, state_code, trade_name, legal_name, is_primary)
                VALUES (%s, %s, %s, '24', 'Secondary GJ Branch', 'M2.2 Legal Entities Ltd', false);
            """, (sec_reg_id, t["org_id"], valid_secondary_gstin))


# =============================================================================
# 2. Immutable Event-Based ITC Ledger (Availment, Reversal & Reclaim Bounds)
# =============================================================================

def test_itc_ledger_lifecycle_and_accounting_integrity(setup_m2_2_tenant):
    t = setup_m2_2_tenant
    headers = {"X-User-Id": t["user_id"], "X-Org-Id": t["org_id"]}
    supplier_pan = random_pan()
    supplier_gstin = random_gstin("27", supplier_pan)

    # 1. Create Purchase Document in Purchase Register (Default: is_availed_in_3b = false)
    create_resp = client.post(
        "/api/v1/reconciliation/purchase/create",
        headers=headers,
        json={
            "registration_id": t["reg_mh"],
            "document_type": "tax_invoice",
            "document_number": f"INV-LEDGER-{uuid.uuid4().hex[:6]}",
            "document_date": "2024-04-10",
            "financial_period": "2024-04",
            "supplier_gstin": supplier_gstin,
            "supplier_legal_name": "Ledger Supplier Pvt Ltd",
            "recipient_gstin": t["gstin_mh"],
            "place_of_supply": "27",
            "is_reverse_charge": False,
            "total_taxable_amount": "100000.00",
            "cgst_amount": "9000.00",
            "sgst_amount": "9000.00",
            "igst_amount": "0.00",
            "cess_amount": "0.00",
            "total_invoice_value": "118000.00",
            "is_availed_in_3b": False,
            "line_items": []
        }
    )
    assert create_resp.status_code == 200, create_resp.text
    p_id = create_resp.json()["id"]

    # 2. Statutory Guard: Attempt reversal BEFORE availment -> Must be rejected with HTTP 400
    rev_fail_resp = client.post(
        "/api/v1/reconciliation/itc-ledger/record-event",
        headers=headers,
        json={
            "purchase_doc_id": p_id,
            "event_type": "REVERSAL_RULE_37",
            "tax_period": "2024-10",
            "cgst_amount": "9000.00",
            "sgst_amount": "9000.00",
            "igst_amount": "0.00",
            "cess_amount": "0.00"
        }
    )
    assert rev_fail_resp.status_code == 400
    assert "Cannot record reversal for an invoice that has not been availed in GSTR-3B" in rev_fail_resp.json()["detail"]

    # 3. Record Initial Availment in GSTR-3B (Period 2024-04)
    avail_resp = client.post(
        "/api/v1/reconciliation/itc-ledger/record-event",
        headers=headers,
        json={
            "purchase_doc_id": p_id,
            "event_type": "AVAIL",
            "tax_period": "2024-04",
            "cgst_amount": "9000.00",
            "sgst_amount": "9000.00",
            "igst_amount": "0.00",
            "cess_amount": "0.00"
        }
    )
    assert avail_resp.status_code == 200
    assert avail_resp.json()["event_type"] == "AVAIL"

    # 4. Record Rule 37 Reversal (Vendor unpaid after 180 days in period 2024-10)
    rev_resp = client.post(
        "/api/v1/reconciliation/itc-ledger/record-event",
        headers=headers,
        json={
            "purchase_doc_id": p_id,
            "event_type": "REVERSAL_RULE_37",
            "tax_period": "2024-10",
            "cgst_amount": "9000.00",
            "sgst_amount": "9000.00",
            "igst_amount": "0.00",
            "cess_amount": "0.00",
            "reversal_reason": "Vendor unpaid at 180-day cut-off",
            "interest_amount": "0.00",
            "interest_basis": "Rule 37 180 days non-payment"
        }
    )
    assert rev_resp.status_code == 200
    rev_id = rev_resp.json()["reversal_event_id"]
    assert rev_id is not None

    # 5. Duplicate Reversal Check: Attempt duplicate reversal for same period -> Must be rejected
    dup_resp = client.post(
        "/api/v1/reconciliation/itc-ledger/record-event",
        headers=headers,
        json={
            "purchase_doc_id": p_id,
            "event_type": "REVERSAL_RULE_37",
            "tax_period": "2024-10",
            "cgst_amount": "9000.00",
            "sgst_amount": "9000.00"
        }
    )
    assert dup_resp.status_code == 400
    assert "Duplicate reversal rejected" in dup_resp.json()["detail"]

    # 6. Reclaim Bounds Check: Attempt reclaim exceeding reversed amount (₹20,000 > ₹18,000)
    exceed_reclaim = client.post(
        "/api/v1/reconciliation/itc-ledger/record-event",
        headers=headers,
        json={
            "purchase_doc_id": p_id,
            "event_type": "RECLAIM",
            "tax_period": "2024-11",
            "reversal_event_id": rev_id,
            "cgst_amount": "10000.00",
            "sgst_amount": "10000.00",
            "reversal_reason": "Payment made to vendor"
        }
    )
    assert exceed_reclaim.status_code == 400
    assert "Reclaim bounds exceeded" in exceed_reclaim.json()["detail"]

    # 7. Valid Reclaim: Partial payment (50% paid: ₹9,000 reclaimed)
    reclaim_part1 = client.post(
        "/api/v1/reconciliation/itc-ledger/record-event",
        headers=headers,
        json={
            "purchase_doc_id": p_id,
            "event_type": "RECLAIM",
            "tax_period": "2024-11",
            "reversal_event_id": rev_id,
            "cgst_amount": "4500.00",
            "sgst_amount": "4500.00",
            "reversal_reason": "50% payment made to vendor on 15-Nov-2024"
        }
    )
    assert reclaim_part1.status_code == 200

    # 8. Valid Reclaim: Remaining 50% paid (₹9,000 reclaimed in 2024-12)
    reclaim_part2 = client.post(
        "/api/v1/reconciliation/itc-ledger/record-event",
        headers=headers,
        json={
            "purchase_doc_id": p_id,
            "event_type": "RECLAIM",
            "tax_period": "2024-12",
            "reversal_event_id": rev_id,
            "cgst_amount": "4500.00",
            "sgst_amount": "4500.00",
            "reversal_reason": "Full final settlement made to vendor on 10-Dec-2024"
        }
    )
    assert reclaim_part2.status_code == 200

    # 9. Verify History API for this Document
    history_resp = client.get(f"/api/v1/reconciliation/itc-ledger/{p_id}", headers=headers)
    assert history_resp.status_code == 200
    events = history_resp.json()
    assert len(events) == 4
    assert [e["event_type"] for e in events] == ["AVAIL", "REVERSAL_RULE_37", "RECLAIM", "RECLAIM"]

    # 10. Permanent Section 17(5) Reclaim Rejection Test
    p_blocked_resp = client.post(
        "/api/v1/reconciliation/purchase/create",
        headers=headers,
        json={
            "registration_id": t["reg_mh"],
            "document_type": "tax_invoice",
            "document_number": f"INV-BLOCKED-{uuid.uuid4().hex[:6]}",
            "document_date": "2024-05-10",
            "financial_period": "2024-05",
            "supplier_gstin": supplier_gstin,
            "supplier_legal_name": "Motor Vehicles Ltd",
            "recipient_gstin": t["gstin_mh"],
            "place_of_supply": "27",
            "cgst_amount": "14000.00",
            "sgst_amount": "14000.00",
            "total_invoice_value": "128000.00",
            "is_availed_in_3b": True,
            "line_items": []
        }
    )
    p_blocked_id = p_blocked_resp.json()["id"]

    rev_perm = client.post(
        "/api/v1/reconciliation/itc-ledger/record-event",
        headers=headers,
        json={
            "purchase_doc_id": p_blocked_id,
            "event_type": "REVERSAL_PERMANENT_17_5",
            "tax_period": "2024-05",
            "cgst_amount": "14000.00",
            "sgst_amount": "14000.00",
            "reversal_reason": "Section 17(5)(a) passenger vehicle"
        }
    )
    perm_rev_id = rev_perm.json()["reversal_event_id"]

    reclaim_perm_fail = client.post(
        "/api/v1/reconciliation/itc-ledger/record-event",
        headers=headers,
        json={
            "purchase_doc_id": p_blocked_id,
            "event_type": "RECLAIM",
            "tax_period": "2024-06",
            "reversal_event_id": perm_rev_id,
            "cgst_amount": "14000.00",
            "sgst_amount": "14000.00"
        }
    )
    assert reclaim_perm_fail.status_code == 400
    assert "permanent and non-reclaimable" in reclaim_perm_fail.json()["detail"]


# =============================================================================
# 3. Rule 37 Vendor Payment Exhaustive Test Matrix
# =============================================================================

def test_rule_37_comprehensive_matrix():
    engine = StatutoryITCRiskEngine()
    today = date(2025, 1, 15)
    tax_amt = Decimal("18000.00")
    inv_val = Decimal("118000.00")
    inv_date_old = "2024-05-01"  # >180 days ago

    p_doc_base = {
        "id": "p_r37_base",
        "document_number": "INV-R37-TEST",
        "document_date": inv_date_old,
        "cgst_amount": Decimal("9000.00"),
        "sgst_amount": Decimal("9000.00"),
        "igst_amount": Decimal("0.00"),
        "cess_amount": Decimal("0.00"),
        "total_invoice_value": inv_val,
        "is_reverse_charge": False,
        "is_availed_in_3b": True
    }
    g_doc = {"id": "g_valid", "itc_availability": "available", "section_name": "B2B"}

    # Case 1: Full vendor payment within 180 days -> Reversal = ₹0
    eval_full = engine.evaluate_itc_eligibility(
        p_doc_base, g_doc,
        payments=[{"allocated_total_amount": inv_val, "allocated_tax_amount": tax_amt}],
        evaluation_date=today
    )
    assert not any(e.rule_code == "RULE_37_180_DAYS" for e in eval_full)

    # Case 2: Partial payment before 180 days (50% paid: ₹59,000) -> Reversal = 50% = ₹9,000
    eval_part = engine.evaluate_itc_eligibility(
        p_doc_base, g_doc,
        payments=[{"allocated_total_amount": Decimal("59000.00"), "allocated_tax_amount": Decimal("9000.00")}],
        evaluation_date=today
    )
    r37_part = next(e for e in eval_part if e.rule_code == "RULE_37_180_DAYS")
    assert r37_part.risk_reversal_amount == Decimal("9000.00")

    # Case 3: Reverse charge supply (is_reverse_charge = True) -> Rule 37 does NOT apply
    p_rcm = dict(p_doc_base, is_reverse_charge=True)
    eval_rcm = engine.evaluate_itc_eligibility(p_rcm, g_doc, payments=[], evaluation_date=today)
    assert not any(e.rule_code == "RULE_37_180_DAYS" for e in eval_rcm)

    # Case 4: ITC never availed in GSTR-3B (is_availed_in_3b = False) -> Rule 37 does NOT apply
    p_unavailed = dict(p_doc_base, is_availed_in_3b=False)
    eval_unav = engine.evaluate_itc_eligibility(p_unavailed, g_doc, payments=[], evaluation_date=today)
    assert not any(e.rule_code == "RULE_37_180_DAYS" for e in eval_unav)


# =============================================================================
# 4. GSTR-3B Table 4 Component Math, Unbooked Proof & Reclaim Reconciliation
# =============================================================================

def test_gstr3b_table4_component_reconciliation_and_unbooked_proof():
    """
    Verifies that:
    1. GSTR-2B Statement Total and Books Matched Total are INTENTIONALLY DIFFERENT
       from Rule 37/37A reversal amounts.
    2. The difference is mathematically and document-level proven by unbooked documents.
    3. Reclaims in Table 4(D)(1) are included in Table 4(A)(5) and reconcile.
    4. Table 4(C) Net ITC balances component-by-component (CGST, SGST, IGST, Cess).
    """
    rp = "2024-11"
    recipient = "27AABCB1234M1ZT"

    # GSTR-2B inward documents (Total: ₹5,00,000 tax)
    # - Doc 1: Matched B2B (Tax: ₹3,00,000)
    # - Doc 2: Matched B2B (Tax: ₹1,20,000)
    # - Doc 3: Unbooked Inward B2B Invoice from Supplier X (Tax: ₹50,000) -> Missing in books
    # - Doc 4: Unbooked Inward B2B Invoice from Supplier Y (Tax: ₹30,000) -> Missing in books
    gstr2b_docs = [
        {
            "id": "g1", "document_number": "INV-MATCH-01", "document_date": "2024-11-05",
            "supplier_gstin": "27SUPPL0001A1Z1", "supplier_name": "Supplier One",
            "section_name": "B2B", "itc_availability": "available",
            "central_tax": Decimal("150000.00"), "state_ut_tax": Decimal("150000.00"),
            "integrated_tax": Decimal("0.00"), "cess_amount": Decimal("0.00")
        },
        {
            "id": "g2", "document_number": "INV-MATCH-02", "document_date": "2024-11-08",
            "supplier_gstin": "27SUPPL0002B1Z2", "supplier_name": "Supplier Two",
            "section_name": "B2B", "itc_availability": "available",
            "central_tax": Decimal("60000.00"), "state_ut_tax": Decimal("60000.00"),
            "integrated_tax": Decimal("0.00"), "cess_amount": Decimal("0.00")
        },
        {
            "id": "g3_unbooked", "document_number": "INV-UNBOOKED-88", "document_date": "2024-11-12",
            "supplier_gstin": "27SUPPL0003C1Z3", "supplier_name": "Supplier Three (Unbooked)",
            "section_name": "B2B", "itc_availability": "available",
            "central_tax": Decimal("25000.00"), "state_ut_tax": Decimal("25000.00"),
            "integrated_tax": Decimal("0.00"), "cess_amount": Decimal("0.00")
        },
        {
            "id": "g4_unbooked", "document_number": "INV-UNBOOKED-99", "document_date": "2024-11-15",
            "supplier_gstin": "27SUPPL0004D1Z4", "supplier_name": "Supplier Four (Unbooked)",
            "section_name": "B2B", "itc_availability": "available",
            "central_tax": Decimal("15000.00"), "state_ut_tax": Decimal("15000.00"),
            "integrated_tax": Decimal("0.00"), "cess_amount": Decimal("0.00")
        },
        # Ineligible credit under Section 16(4) from portal: ₹12,000
        {
            "id": "g5_inelig", "document_number": "INV-EXPIRED-05", "document_date": "2023-01-10",
            "supplier_gstin": "27SUPPL0005E1Z5", "supplier_name": "Supplier Five",
            "section_name": "B2B", "itc_availability": "not_available",
            "central_tax": Decimal("6000.00"), "state_ut_tax": Decimal("6000.00"),
            "integrated_tax": Decimal("0.00"), "cess_amount": Decimal("0.00")
        }
    ]

    # Purchase register documents: Only p1 and p2 are booked in this period (Total: ₹4,20,000 tax)
    purchase_docs = [
        {
            "id": "p1", "document_number": "INV-MATCH-01", "document_date": "2024-11-05",
            "cgst_amount": Decimal("150000.00"), "sgst_amount": Decimal("150000.00"),
            "igst_amount": Decimal("0.00"), "cess_amount": Decimal("0.00"),
            "status": "approved", "is_availed_in_3b": True, "availed_period": "2024-11"
        },
        {
            "id": "p2", "document_number": "INV-MATCH-02", "document_date": "2024-11-08",
            "cgst_amount": Decimal("60000.00"), "sgst_amount": Decimal("60000.00"),
            "igst_amount": Decimal("0.00"), "cess_amount": Decimal("0.00"),
            "status": "approved", "is_availed_in_3b": True, "availed_period": "2024-11"
        }
    ]

    matches = [
        {"purchase_doc_id": "p1", "gstr2b_doc_id": "g1"},
        {"purchase_doc_id": "p2", "gstr2b_doc_id": "g2"}
    ]

    # Reversals Ledger:
    # 1. Permanent Section 17(5) Reversal on Director Car: ₹40,000
    # 2. Temporary Rule 37 Reversal on old invoice: ₹24,000
    # 3. Temporary Rule 37A Reversal on non-filing supplier: ₹16,000
    reversals_ledger = [
        {
            "id": "rev_perm", "purchase_doc_id": "p1", "rule_code": "SEC_17_5_MOTOR",
            "reversed_cgst": Decimal("20000.00"), "reversed_sgst": Decimal("20000.00"),
            "reversed_igst": Decimal("0.00"), "reversed_cess": Decimal("0.00"),
            "interest_liability": Decimal("0.00")
        },
        {
            "id": "rev_r37", "purchase_doc_id": "p1", "rule_code": "RULE_37_180_DAYS",
            "reversed_cgst": Decimal("12000.00"), "reversed_sgst": Decimal("12000.00"),
            "reversed_igst": Decimal("0.00"), "reversed_cess": Decimal("0.00"),
            "interest_liability": Decimal("0.00")
        },
        {
            "id": "rev_r37a", "purchase_doc_id": "p2", "rule_code": "RULE_37A_NON_FILING",
            "reversed_cgst": Decimal("8000.00"), "reversed_sgst": Decimal("8000.00"),
            "reversed_igst": Decimal("0.00"), "reversed_cess": Decimal("0.00"),
            "interest_liability": Decimal("0.00")
        }
    ]

    # Reclaim under Table 4(D)(1): ₹10,000 reclaimed from earlier Rule 37 reversal
    reclaims = [
        {
            "id": "rec_01", "purchase_doc_id": "p1", "reversal_event_id": "rev_r37",
            "reavailed_cgst": Decimal("5000.00"), "reavailed_sgst": Decimal("5000.00"),
            "reavailed_igst": Decimal("0.00"), "reavailed_cess": Decimal("0.00")
        }
    ]

    summary = generate_gstr3b_table4_summary(
        return_period=rp,
        recipient_gstin=recipient,
        gstr2b_docs=gstr2b_docs,
        purchase_docs=purchase_docs,
        matches=matches,
        assessments=[],
        human_approved_ids={"p1", "p2"},
        reclaims=reclaims,
        reversals_ledger=reversals_ledger
    )

    # 1. Total GSTR-2B Statement Available = ₹5,00,000 (from 2B docs)
    assert summary.subtotal_gstr2b_portal_available == Decimal("500000.00")
    # Gross 4(A) includes the 500k gross B2B inward plus the 10k reclaimed credit in 4(A)(5) = 510,000
    assert summary.total_itc_available == Decimal("510000.00")

    # 2. Book Matched Total = ₹4,20,000 (p1 + p2)
    assert summary.subtotal_books_matched == Decimal("420000.00")

    # 3. Difference between GSTR-2B and Books Matched:
    # 500,000 - 420,000 = ₹80,000.
    # Must be accounted for by the two unbooked documents (g3_unbooked: 50,000 + g4_unbooked: 30,000 = 80,000)
    assert summary.unbooked_variance_amount == Decimal("80000.00")
    assert len(summary.unbooked_gstr2b_documents) == 2
    unbooked_numbers = [d["document_number"] for d in summary.unbooked_gstr2b_documents]
    assert "INV-UNBOOKED-88" in unbooked_numbers
    assert "INV-UNBOOKED-99" in unbooked_numbers

    # 4. Table 4(B)(1) Permanent Reversals = ₹40,000
    assert summary.itc_reversed_rule_38_42_43 == Decimal("40000.00")

    # 5. Table 4(B)(2) Reclaimable Reversals = ₹24,000 (Rule 37) + ₹16,000 (Rule 37A) = ₹40,000
    assert summary.itc_reversed_rule_37_180_days == Decimal("24000.00")
    assert summary.itc_reversed_rule_37a == Decimal("16000.00")
    assert summary.total_itc_reversed_reclaimable_4b2 == Decimal("40000.00")

    # 6. Total Reversed 4(B) = 4(B)(1) + 4(B)(2) = 40,000 + 40,000 = ₹80,000
    assert summary.total_itc_reversed == Decimal("80000.00")

    # 7. Table 4(C) Net ITC Available = 4(A) [₹510,000] - 4(B) [₹80,000] = ₹4,30,000
    assert summary.net_itc_available == Decimal("430000.00")

    # 8. Component-level reconciliation for 4(C):
    # CGST: 4(A) 255,000 - 4(B) 40,000 = 215,000
    # SGST: 4(A) 255,000 - 4(B) 40,000 = 215,000
    assert summary.components_4c_net.cgst == Decimal("215000.00")
    assert summary.components_4c_net.sgst == Decimal("215000.00")
    assert summary.components_4c_net.total == Decimal("430000.00")

    # 9. Table 4(D)(1) Reclaimed Credit = ₹10,000
    assert summary.itc_reclaimed_earlier_reversal_4d1 == Decimal("10000.00")

    # 10. Table 4(D)(2) Ineligible Credit = ₹12,000 (never entered 4(A))
    assert summary.ineligible_sec_16_4_and_pos_restrictions_4d2 == Decimal("12000.00")


# =============================================================================
# 5. IMS Action Freeze Confirmation & Credit Note Constraints
# =============================================================================

def test_ims_freeze_confirmation_and_credit_note_constraint(setup_m2_2_tenant):
    t = setup_m2_2_tenant
    headers = {"X-User-Id": t["user_id"], "X-Org-Id": t["org_id"]}
    rp = f"2026-{random.randint(10, 12)}"

    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            import_id = str(uuid.uuid4())
            cur.execute("""
                INSERT INTO public.gstr2b_imports (
                    id, org_id, registration_id, recipient_gstin, return_period,
                    financial_year, file_name, file_type, file_hash, file_size_bytes
                ) VALUES (%s, %s, %s, %s, %s, '2026-27', 'test_2b.json', 'json', %s, 1024);
            """, (import_id, t["org_id"], t["reg_mh"], t["gstin_mh"], rp, uuid.uuid4().hex))

            snap_id = str(uuid.uuid4())
            cur.execute("""
                INSERT INTO public.gstr2b_snapshots (
                    id, org_id, import_id, registration_id, recipient_gstin,
                    return_period, version_number, total_itc_available, total_itc_not_available, is_active
                ) VALUES (%s, %s, %s, %s, %s, %s, 1, 10000, 0, true);
            """, (snap_id, t["org_id"], import_id, t["reg_mh"], t["gstin_mh"], rp))

            crn_doc_id = str(uuid.uuid4())
            cur.execute("""
                INSERT INTO public.gstr2b_source_documents (
                    id, org_id, snapshot_id, section_name, itc_availability,
                    source_form, document_type, document_number, document_date, taxable_value,
                    central_tax, state_ut_tax, invoice_value, place_of_supply, is_amended
                ) VALUES (
                    %s, %s, %s, 'CDNR', 'available',
                    'GSTR-1', 'CRN', 'CRN-TEST-01', '2026-11-01', 10000,
                    900, 900, 11800, '27', false
                );
            """, (crn_doc_id, t["org_id"], snap_id))

            ims_id = str(uuid.uuid4())
            cur.execute("""
                INSERT INTO public.ims_document_actions (
                    id, org_id, gstr2b_doc_id, supplier_doc_reference,
                    current_action, gstr2b_return_period
                ) VALUES (%s, %s, %s, 'CRN-TEST-01', 'NO_ACTION', %s);
            """, (ims_id, t["org_id"], crn_doc_id, rp))

    # Test Credit Note Restriction: Attempt PENDING on Credit Note -> Must fail with HTTP 400
    cn_resp = client.post(
        "/api/v1/reconciliation/ims/action",
        headers=headers,
        json={"gstr2b_doc_id": crn_doc_id, "action": "PENDING"}
    )
    assert cn_resp.status_code == 400
    assert "Credit Notes cannot be marked as PENDING" in cn_resp.json()["detail"]

    # Test Freeze Validation: Attempt freeze with status DRAFT -> Must fail with HTTP 400
    draft_freeze = client.post(
        f"/api/v1/reconciliation/gstr3b/file-freeze?registration_id={t['reg_mh']}&return_period={rp}&filing_status=DRAFT",
        headers=headers
    )
    assert draft_freeze.status_code == 400
    assert "only be frozen upon confirmed filing" in draft_freeze.json()["detail"]

    # Test Confirmed Freeze: Status FILED + ARN -> Must succeed and freeze snapshot
    confirm_freeze = client.post(
        f"/api/v1/reconciliation/gstr3b/file-freeze?registration_id={t['reg_mh']}&return_period={rp}&filing_status=FILED&filing_arn=AA2711240001234",
        headers=headers
    )
    assert confirm_freeze.status_code == 200
    assert confirm_freeze.json()["status"] == "frozen"

    # Verify that post-freeze action modification is blocked with HTTP 403
    post_freeze_mod = client.post(
        "/api/v1/reconciliation/ims/action",
        headers=headers,
        json={"gstr2b_doc_id": crn_doc_id, "action": "ACCEPTED"}
    )
    assert post_freeze_mod.status_code == 403
    assert "frozen because GSTR-3B has been filed" in post_freeze_mod.json()["detail"]
