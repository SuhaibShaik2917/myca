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
from app.modules.reconciliation.purchase_service import (
    create_purchase_document, DuplicatePurchaseDocumentError, PurchaseServiceError,
    mark_purchase_doc_availed_in_3b
)
from app.modules.reconciliation.matching_engine import DeterministicReconciliationEngine

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
def setup_m2_1_tenant():
    org_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    pan = random_pan()
    gstin_mh = random_gstin("27", pan)
    reg_mh = str(uuid.uuid4())

    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO public.organizations (id, legal_name, pan) VALUES (%s, 'Milestone 2.1 Test Org', %s);", (org_id, pan))
            cur.execute("INSERT INTO public.global_users (id, email, full_name) VALUES (%s, %s, 'M2.1 Owner');", (user_id, f"user_{uuid.uuid4().hex}@example.com"))
            cur.execute("INSERT INTO public.organization_memberships (org_id, user_id, role, status) VALUES (%s, %s, 'owner', 'active');", (org_id, user_id))
            cur.execute("""
                INSERT INTO public.gst_registrations (id, org_id, gstin, state_code, trade_name, legal_name, is_primary)
                VALUES (%s, %s, %s, '27', 'M2.1 MH Primary', 'M2.1 Legal', true);
            """, (reg_mh, org_id, gstin_mh))

    return {
        "org_id": org_id,
        "user_id": user_id,
        "pan": pan,
        "gstin_mh": gstin_mh,
        "reg_mh": reg_mh
    }


# =============================================================================
# 1. Primary GST Registration Partial Unique Index & PAN Consistency Trigger
# =============================================================================

def test_primary_registration_partial_unique_index(setup_m2_1_tenant):
    t = setup_m2_1_tenant
    second_gstin_gj = random_gstin("24", t["pan"])
    second_reg_id = str(uuid.uuid4())

    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            # Secondary registration with is_primary = false must succeed
            cur.execute("""
                INSERT INTO public.gst_registrations (id, org_id, gstin, state_code, trade_name, legal_name, is_primary)
                VALUES (%s, %s, %s, '24', 'M2.1 GJ Secondary', 'M2.1 Legal', false);
            """, (second_reg_id, t["org_id"], second_gstin_gj))

            # Attempting to insert a second primary registration must fail with UniqueViolation on partial index
            third_gstin_ka = random_gstin("29", t["pan"])
            with pytest.raises(psycopg.errors.UniqueViolation):
                cur.execute("""
                    INSERT INTO public.gst_registrations (id, org_id, gstin, state_code, trade_name, legal_name, is_primary)
                    VALUES (%s, %s, %s, '29', 'Second Primary Fail', 'M2.1 Legal', true);
                """, (str(uuid.uuid4()), t["org_id"], third_gstin_ka))


def test_pan_gstin_consistency_trigger(setup_m2_1_tenant):
    t = setup_m2_1_tenant
    mismatched_pan = "ZZZZZ9999Z"
    mismatched_gstin = random_gstin("27", mismatched_pan)

    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            # Inserting a GSTIN whose PAN does not match the organization PAN must be rejected by trigger with SQLSTATE 23514 (CheckViolation)
            with pytest.raises(psycopg.errors.CheckViolation) as exc_info:
                cur.execute("""
                    INSERT INTO public.gst_registrations (id, org_id, gstin, state_code, trade_name, legal_name, is_primary)
                    VALUES (%s, %s, %s, '27', 'Mismatched PAN', 'M2.1 Legal', false);
                """, (str(uuid.uuid4()), t["org_id"], mismatched_gstin))
            assert exc_info.value.sqlstate == "23514"
            assert "does not match Organization PAN" in str(exc_info.value)


# =============================================================================
# 2. Purchase Document Uniqueness with Registration & Document Type
# =============================================================================

def test_purchase_document_uniqueness_with_reg_and_doc_type(setup_m2_1_tenant):
    t = setup_m2_1_tenant
    supplier_gstin = random_gstin("27", "SUPPL1234S")
    doc_num = f"INV-{uuid.uuid4().hex[:6]}"
    doc_date = "2026-11-01"

    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            # 1. Insert tax_invoice under reg_mh
            cur.execute("""
                INSERT INTO public.purchase_documents (
                    id, org_id, registration_id, document_type, document_number,
                    document_date, financial_period, supplier_gstin, supplier_legal_name,
                    recipient_gstin, place_of_supply, status
                ) VALUES (
                    %s, %s, %s, 'tax_invoice', %s, %s, '2026-11', %s, 'Supplier X',
                    %s, '27', 'draft'
                );
            """, (str(uuid.uuid4()), t["org_id"], t["reg_mh"], doc_num, doc_date, supplier_gstin, t["gstin_mh"]))

            # 2. Same doc_num but document_type = credit_note under same reg must SUCCEED
            cur.execute("""
                INSERT INTO public.purchase_documents (
                    id, org_id, registration_id, document_type, document_number,
                    document_date, financial_period, supplier_gstin, supplier_legal_name,
                    recipient_gstin, place_of_supply, status
                ) VALUES (
                    %s, %s, %s, 'credit_note', %s, %s, '2026-11', %s, 'Supplier X',
                    %s, '27', 'draft'
                );
            """, (str(uuid.uuid4()), t["org_id"], t["reg_mh"], doc_num, doc_date, supplier_gstin, t["gstin_mh"]))

            # 3. Exact duplicate tax_invoice under same reg must FAIL
            with pytest.raises(psycopg.errors.UniqueViolation):
                cur.execute("""
                    INSERT INTO public.purchase_documents (
                        id, org_id, registration_id, document_type, document_number,
                        document_date, financial_period, supplier_gstin, supplier_legal_name,
                        recipient_gstin, place_of_supply, status
                    ) VALUES (
                        %s, %s, %s, 'tax_invoice', %s, %s, '2026-11', %s, 'Supplier X',
                        %s, '27', 'draft'
                    );
                """, (str(uuid.uuid4()), t["org_id"], t["reg_mh"], doc_num, doc_date, supplier_gstin, t["gstin_mh"]))


# =============================================================================
# 3. Rule 37A Statutory Deadlines, Un-availed Exclusion & 18% Interest
# =============================================================================

def test_rule_37a_statutory_dual_deadlines_and_interest():
    engine = StatutoryITCRiskEngine()
    tax_amt = Decimal("36000.00")

    # Invoice dated in FY 2024-25 (10-May-2024)
    # FY 2024-25 end: 31-Mar-2025
    # Statutory supplier filing cut-off: 30-Sep-2025
    # Statutory recipient reversal deadline: 30-Nov-2025

    p_doc_unavailed = {
        "id": "p_r37a_unavailed",
        "supplier_gstin": "27SUPPL1234S1Z1",
        "document_number": "INV-2024-001",
        "document_date": "2024-05-10",
        "igst_amount": tax_amt,
        "total_invoice_value": Decimal("236000.00"),
        "is_availed_in_3b": False  # Not availed in GSTR-3B
    }
    g_doc_not_filed = {
        "id": "g_r37a",
        "itc_availability": "available",
        "section_name": "B2B",
        "raw_payload": {"gstr3b_status": "NOT_FILED"}
    }

    # Case A: Not availed in 3B -> Rule 37A reversal must NOT trigger
    eval_nov = date(2025, 11, 15)
    evals_a = engine.evaluate_itc_eligibility(p_doc_unavailed, g_doc_not_filed, evaluation_date=eval_nov)
    assert not any(e.rule_code == "RULE_37A_NON_FILING" for e in evals_a)

    # Case B: Availed in 3B, portal status UNKNOWN -> No reversal, marked SUPPLIER_STATUS_UNKNOWN
    p_doc_availed = dict(p_doc_unavailed, is_availed_in_3b=True)
    g_doc_unknown = {"id": "g_unk", "itc_availability": "available", "section_name": "B2B", "raw_payload": {}}
    evals_b = engine.evaluate_itc_eligibility(p_doc_availed, g_doc_unknown, evaluation_date=eval_nov)
    r37a_unk = next(e for e in evals_b if e.rule_code == "RULE_37A_NON_FILING")
    assert r37a_unk.eligibility_status == ITCEligibilityEnum.SUPPLIER_STATUS_UNKNOWN
    assert r37a_unk.risk_reversal_amount == Decimal("0.00")

    # Case C: Evaluation before 30th September cut-off -> Supplier still has time, marked POTENTIALLY_ELIGIBLE
    eval_aug = date(2025, 8, 15)
    evals_c = engine.evaluate_itc_eligibility(p_doc_availed, g_doc_not_filed, evaluation_date=eval_aug)
    r37a_aug = next(e for e in evals_c if e.rule_code == "RULE_37A_NON_FILING")
    assert r37a_aug.eligibility_status == ITCEligibilityEnum.POTENTIALLY_ELIGIBLE
    assert r37a_aug.risk_reversal_amount == Decimal("0.00")

    # Case D: Evaluation between 30th Sept and 30th Nov -> Reversal required, but NO interest liability
    evals_d = engine.evaluate_itc_eligibility(p_doc_availed, g_doc_not_filed, evaluation_date=date(2025, 10, 20))
    r37a_oct = next(e for e in evals_d if e.rule_code == "RULE_37A_NON_FILING")
    assert r37a_oct.eligibility_status == ITCEligibilityEnum.RULE_37A_RISK
    assert r37a_oct.risk_reversal_amount == tax_amt
    assert "No interest payable under Rule 37A proviso" in r37a_oct.findings_explanation

    # Case E: Evaluation past 30th Nov (15 days late: 15-Dec-2025) -> Reversal + 18% p.a. interest under Section 50
    eval_late = date(2025, 12, 15)
    evals_e = engine.evaluate_itc_eligibility(p_doc_availed, g_doc_not_filed, evaluation_date=eval_late)
    r37a_late = next(e for e in evals_e if e.rule_code == "RULE_37A_NON_FILING")
    assert r37a_late.eligibility_status == ITCEligibilityEnum.RULE_37A_RISK
    assert r37a_late.risk_reversal_amount == tax_amt
    # 15 days interest: 36,000 * 0.18 * 15 / 365 = 266.30
    assert "Section 50 interest at 18% p.a. (estimated ₹266.30)" in r37a_late.findings_explanation

    # Case F: Detailed evaluation check helper
    detail_check = engine.evaluate_rule_37a_details(p_doc_availed, g_doc_not_filed, evaluation_date=eval_late)
    assert detail_check.supplier_filing_deadline == "2025-09-30"
    assert detail_check.recipient_reversal_deadline == "2025-11-30"
    assert detail_check.reversal_required is True
    assert detail_check.interest_liability == Decimal("266.30")


# =============================================================================
# 4. IMS Deemed Acceptance, Credit Note Restriction & Snapshot Recomputation
# =============================================================================

def test_ims_lifecycle_deemed_acceptance_and_recomputation(setup_m2_1_tenant):
    t = setup_m2_1_tenant

    headers = {
        "X-User-Id": t["user_id"],
        "X-Org-Id": t["org_id"]
    }

    # 1. Upload sample GSTR-2B JSON
    gstr2b_data = {
        "gstin": t["gstin_mh"],
        "fp": "2026-11",
        "b2b": [
            {
                "ctin": "27SUPPA0000A1Z5",
                "inv": [
                    {
                        "inum": "INV-IMS-01",
                        "idt": "01-11-2026",
                        "val": 118000.0,
                        "pos": "27",
                        "rchrg": "N",
                        "itcavl": "Y",
                        "items": [{"num": 1, "txval": 100000.0, "rt": 18.0, "camt": 9000.0, "samt": 9000.0, "csamt": 0.0}]
                    },
                    {
                        "inum": "INV-IMS-02",
                        "idt": "02-11-2026",
                        "val": 59000.0,
                        "pos": "27",
                        "rchrg": "N",
                        "itcavl": "Y",
                        "items": [{"num": 1, "txval": 50000.0, "rt": 18.0, "camt": 4500.0, "samt": 4500.0, "csamt": 0.0}]
                    }
                ]
            }
        ],
        "cdnr": [
            {
                "ctin": "27SUPPB0000B1Z6",
                "nt": [
                    {
                        "ntnum": "CRN-IMS-01",
                        "ntdt": "05-11-2026",
                        "typ": "C",
                        "val": 23600.0,
                        "pos": "27",
                        "rchrg": "N",
                        "itcavl": "Y",
                        "items": [{"num": 1, "txval": 20000.0, "rt": 18.0, "camt": 1800.0, "samt": 1800.0, "csamt": 0.0}]
                    }
                ]
            }
        ]
    }

    import io
    import json
    file_bytes = json.dumps(gstr2b_data).encode("utf-8")

    res = client.post(
        "/api/v1/reconciliation/gstr2b/upload",
        data={"registration_id": t["reg_mh"], "return_period": "2026-11"},
        files={"file": ("gstr2b_ims_test.json", io.BytesIO(file_bytes), "application/json")},
        headers=headers
    )
    assert res.status_code == 200, res.text
    snap_data = res.json()
    snapshot_v1_id = snap_data["snapshot_id"]
    assert snap_data["version_number"] == 1
    # Total Part A: 18,000 + 9,000 + 3,600 = 30,600
    assert Decimal(str(snap_data["total_itc_available"])) == Decimal("30600.00")

    # 2. Verify all B2B records were created with deemed_accepted_at_generation = true
    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT a.current_action, a.deemed_accepted_at_generation, d.document_number, d.document_type
                FROM public.ims_document_actions a
                JOIN public.gstr2b_source_documents d ON a.gstr2b_doc_id = d.id
                WHERE d.snapshot_id = %s;
            """, (snapshot_v1_id,))
            ims_actions = cur.fetchall()
            assert len(ims_actions) == 3
            for act, deemed, dnum, dtype in ims_actions:
                assert act == "NO_ACTION"
                assert deemed is True

    # 3. Test statutory Credit Note Pending restriction: Credit note CANNOT be marked PENDING
    crn_doc = next(r for r in ims_actions if "CRN" in r[2])
    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM public.gstr2b_source_documents WHERE snapshot_id = %s AND document_number = 'CRN-IMS-01';", (snapshot_v1_id,))
            crn_doc_id = str(cur.fetchone()[0])
            cur.execute("SELECT id FROM public.gstr2b_source_documents WHERE snapshot_id = %s AND document_number = 'INV-IMS-02';", (snapshot_v1_id,))
            inv2_doc_id = str(cur.fetchone()[0])

    res_crn_pending = client.post(
        "/api/v1/reconciliation/ims/action",
        json={"gstr2b_doc_id": crn_doc_id, "action": "PENDING"},
        headers=headers
    )
    assert res_crn_pending.status_code == 400
    assert "Credit Notes cannot be marked as PENDING in IMS" in res_crn_pending.text

    # 4. Mark INV-IMS-02 as PENDING (allowed for regular invoices)
    res_inv_pending = client.post(
        "/api/v1/reconciliation/ims/action",
        json={"gstr2b_doc_id": inv2_doc_id, "action": "PENDING"},
        headers=headers
    )
    assert res_inv_pending.status_code == 200
    assert res_inv_pending.json()["current_action"] == "PENDING"

    # 5. Recompute GSTR-2B Statement
    res_recompute = client.post(
        "/api/v1/reconciliation/gstr2b/recompute",
        json={"registration_id": t["reg_mh"], "return_period": "2026-11", "snapshot_id": snapshot_v1_id},
        headers=headers
    )
    assert res_recompute.status_code == 200, res_recompute.text
    recomp_data = res_recompute.json()
    assert recomp_data["version_number"] == 2
    assert recomp_data["previous_snapshot_id"] == snapshot_v1_id
    assert recomp_data["pending_docs_held_count"] == 1

    # Total available in newly recomputed Snapshot v2:
    # 30,600 - 9,000 (INV-02 is excluded because it is PENDING in IMS) = 21,600
    assert Decimal(str(recomp_data["total_itc_available"])) == Decimal("21600.00")

    # 6. Test GSTR-3B Filing Freeze
    freeze_res = client.post(
        f"/api/v1/reconciliation/gstr3b/file-freeze?registration_id={t['reg_mh']}&return_period=2026-11",
        headers=headers
    )
    assert freeze_res.status_code == 200
    assert freeze_res.json()["status"] == "frozen"

    # Subsequent IMS action update must be REJECTED with 403
    res_frozen_edit = client.post(
        "/api/v1/reconciliation/ims/action",
        json={"gstr2b_doc_id": inv2_doc_id, "action": "ACCEPTED"},
        headers=headers
    )
    assert res_frozen_edit.status_code == 403
    assert "frozen because GSTR-3B has been filed" in res_frozen_edit.text

    # Subsequent recomputation must be REJECTED with 403
    res_frozen_recomp = client.post(
        "/api/v1/reconciliation/gstr2b/recompute",
        json={"registration_id": t["reg_mh"], "return_period": "2026-11", "snapshot_id": recomp_data["new_snapshot_id"]},
        headers=headers
    )
    assert res_frozen_recomp.status_code == 403
    assert "blocked because GSTR-3B has already been filed" in res_frozen_recomp.text


# =============================================================================
# 5. GSTR-3B Table 4 Exact Statutory Calculation (Illustrative Net ₹3,54,600)
# =============================================================================

def test_gstr3b_table4_exact_statutory_mapping_and_illustrative_calculation():
    """
    Validates exact GSTR-3B Table 4 calculation from formal review:
    4(A) Available: ₹4,82,500
    4(B)(1) Rules 38, 42, 43 & Section 17(5) Reversal: ₹77,500 (Non-reclaimable)
    4(B)(2) Rule 37 (₹18,000) + Rule 37A (₹32,400) Reversal: ₹50,400 (Reclaimable)
    4(C) Net ITC Available = 4(A) - 4(B)(1) - 4(B)(2) = ₹3,54,600
    4(D)(1) Reclaimed ITC: ₹0.00 (Never Section 17(5))
    4(D)(2) Section 16(4) / PoS Restrictions: ₹12,400.00
    """
    gstr2b_docs = [
        # 4(A)(1) Import of Goods: 60,000
        {"id": "g_impg", "section_name": "IMPG", "itc_availability": "available", "integrated_tax": Decimal("60000.00")},
        # 4(A)(3) Reverse Charge: 30,000
        {"id": "g_rcm", "section_name": "RCM", "supply_attract_reverse_charge": True, "itc_availability": "available", "central_tax": Decimal("15000.00"), "state_ut_tax": Decimal("15000.00")},
        # 4(A)(4) ISD: 25,500
        {"id": "g_isd", "section_name": "ISD", "itc_availability": "available", "integrated_tax": Decimal("25500.00")},
        # 4(A)(5) All Other ITC (B2B): 367,000 (includes goods/services subject to Sec 17(5) reversal)
        {"id": "g_b2b", "section_name": "B2B", "itc_availability": "available", "integrated_tax": Decimal("100000.00"), "central_tax": Decimal("133500.00"), "state_ut_tax": Decimal("133500.00")},
        # 4(D)(2) Ineligible in GSTR-2B Table 4 (Section 16(4) / PoS): 12,400
        {"id": "g_inelig", "section_name": "B2B", "itc_availability": "not_available", "integrated_tax": Decimal("12400.00")}
    ]

    purchase_docs = [
        # Matched eligible: 336,600
        {"id": "p_clean", "cgst_amount": Decimal("100000.00"), "sgst_amount": Decimal("100000.00"), "igst_amount": Decimal("136600.00"), "status": "approved"},
        # Subject to Section 17(5) blocked credit (Motor vehicle + catering): 77,500
        {"id": "p_17_5", "cgst_amount": Decimal("6250.00"), "sgst_amount": Decimal("6250.00"), "igst_amount": Decimal("65000.00"), "status": "approved"},
        # Subject to Rule 37 180-day non-payment: 18,000
        {"id": "p_r37", "cgst_amount": Decimal("9000.00"), "sgst_amount": Decimal("9000.00"), "igst_amount": Decimal("0.00"), "status": "draft"},
        # Subject to Rule 37A supplier 3B non-filing: 32,400
        {"id": "p_r37a", "cgst_amount": Decimal("0.00"), "sgst_amount": Decimal("0.00"), "igst_amount": Decimal("32400.00"), "status": "draft"}
    ]

    matches = [
        {"purchase_doc_id": "p_clean", "gstr2b_doc_id": "g_b2b", "match_pass": 1},
        {"purchase_doc_id": "p_17_5", "gstr2b_doc_id": "g_b2b", "match_pass": 1},
        {"purchase_doc_id": "p_r37", "gstr2b_doc_id": "g_b2b", "match_pass": 1},
        {"purchase_doc_id": "p_r37a", "gstr2b_doc_id": "g_b2b", "match_pass": 1}
    ]

    assessments = [
        # 4(B)(1) Section 17(5) reversal: 77,500
        {
            "purchase_doc_id": "p_17_5",
            "rule_code": "SEC_17_5_MOTOR",
            "eligibility_status": ITCEligibilityEnum.BLOCKED_CREDIT_INDICATOR,
            "risk_reversal_amount": Decimal("77500.00")
        },
        # 4(B)(2) Rule 37 reversal: 18,000
        {
            "purchase_doc_id": "p_r37",
            "rule_code": "RULE_37_180_DAYS",
            "eligibility_status": ITCEligibilityEnum.PAYMENT_180_DAY_RISK,
            "risk_reversal_amount": Decimal("18000.00")
        },
        # 4(B)(2) Rule 37A reversal: 32,400
        {
            "purchase_doc_id": "p_r37a",
            "rule_code": "RULE_37A_NON_FILING",
            "eligibility_status": ITCEligibilityEnum.RULE_37A_RISK,
            "risk_reversal_amount": Decimal("32400.00")
        }
    ]

    summary = generate_gstr3b_table4_summary(
        return_period="2026-11",
        recipient_gstin="27AABCB1234M1ZT",
        gstr2b_docs=gstr2b_docs,
        purchase_docs=purchase_docs,
        matches=matches,
        assessments=assessments,
        human_approved_ids={"p_clean", "p_17_5"}
    )

    # 1. Verify Table 4(A) Gross Available = 482,500.00
    assert summary.itc_available_import_goods == Decimal("60000.00")
    assert summary.itc_available_inward_reverse_charge == Decimal("30000.00")
    assert summary.itc_available_isd == Decimal("25500.00")
    assert summary.itc_available_all_other == Decimal("367000.00")
    assert summary.total_itc_available == Decimal("482500.00")

    # 2. Verify Table 4(B)(1) Non-Reclaimable Section 17(5) Reversal = 77,500.00
    assert summary.itc_reversed_rule_38_42_43 == Decimal("77500.00")

    # 3. Verify Table 4(B)(2) Reclaimable Reversals (Rule 37 + Rule 37A) = 50,400.00
    assert summary.itc_reversed_rule_37_180_days == Decimal("18000.00")
    assert summary.itc_reversed_rule_37a == Decimal("32400.00")
    assert summary.total_itc_reversed_reclaimable_4b2 == Decimal("50400.00")
    assert summary.total_itc_reversed == Decimal("127900.00")

    # 4. Verify Table 4(C) Net ITC Available = 4(A) - 4(B)(1) - 4(B)(2) = ₹3,54,600.00
    assert summary.net_itc_available == Decimal("354600.00")

    # 5. Verify Table 4(D) Other Details
    assert summary.itc_reclaimed_earlier_reversal_4d1 == Decimal("0.00")
    assert summary.ineligible_sec_16_4_and_pos_restrictions_4d2 == Decimal("12400.00")

    # 6. Verify 4 Distinct Calculation Provenance Subtotals
    assert summary.subtotal_gstr2b_portal_available == Decimal("482500.00")
    assert summary.subtotal_books_matched == Decimal("464500.00")  # 336.6k + 77.5k + 18k + 32.4k
    assert summary.subtotal_legally_eligible == Decimal("336600.00")  # Matched 464.5k - 77.5k - 50.4k
    assert summary.subtotal_human_approved == Decimal("414100.00")   # p_clean (336.6k) + p_17_5 (77.5k)


# =============================================================================
# 6. Expanded Statutory Rule Catalogue Seed Data
# =============================================================================

def test_itc_rule_catalogue_expanded_seed_data():
    with psycopg.connect(DB_APP, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT rule_code, statutory_section, is_blocking
                FROM public.itc_rule_catalogue
                WHERE rule_code IN ('SEC_16_2_D', 'RULE_38', 'RULE_42', 'RULE_43', 'SEC_17_5_EXCEPT')
                ORDER BY rule_code;
            """)
            rows = cur.fetchall()
            codes = {r[0]: (r[1], r[2]) for r in rows}

            assert "SEC_16_2_D" in codes
            assert codes["SEC_16_2_D"][0] == "Section 16(2)(d)"
            assert codes["SEC_16_2_D"][1] is True

            assert "RULE_38" in codes
            assert "RULE_42" in codes
            assert "RULE_43" in codes
            assert "SEC_17_5_EXCEPT" in codes
            assert codes["SEC_17_5_EXCEPT"][1] is False  # Non-blocking exception
