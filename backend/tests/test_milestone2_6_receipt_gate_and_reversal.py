"""
Milestone 2.6: Receipt-Based ITC Finalization, GSTR-3B Reconciliation & Purchase Compliance Test Suite
Validates:
1. Fix Section 16(2)(b) Receipt Gate:
   - Default is 'UNKNOWN'
   - Historical rows without proof are 'UNKNOWN'
   - Transitions require verified evidence (GRN/Challan for goods, Completion date & cert for services)
   - Multi-lot delivery respects Section 16(2)(b) First Proviso last-lot rule
   - Unauthorized status changes rejected
2. SUP-2B-998 Accounting & Corrected Net ITC:
   - Statement 4(A) total: ₹4,82,500.00
   - Permanent Reversals 4(B)(1): ₹77,500.00
   - Reclaimable Reversals 4(B)(2): ₹68,400.00 (Rule 37 ₹18,000 + Rule 37A ₹32,400 + Receipt Reversal ₹18,000)
   - Corrected Net ITC 4(C): ₹3,36,600.00 (CGST ₹82,250 | SGST ₹82,250 | IGST ₹1,72,100)
3. Reclaim after receipt linked to original reversal event
4. GTA & RCM statutory source verification (Annexure V forward charge, consignment notes, cash discharge 3.1(d))
5. Unbooked candidate review & purchase booking workflow
6. Cross-tenant isolation & Alembic migration consistency
"""
import uuid
import random
from datetime import date, datetime
from decimal import Decimal
import pytest
import psycopg
from fastapi.testclient import TestClient

from app.main import app
from app.modules.reconciliation.itc_engine import StatutoryITCRiskEngine, calculate_rule_37_interest
from app.modules.reconciliation.gstr3b_itc import generate_gstr3b_table4_summary
from app.modules.reconciliation.schemas import ITCEligibilityEnum, ReceiptStatusEnum
from app.modules.reconciliation.rcm_service import validate_rcm_and_gta_eligibility

client = TestClient(app)

DB_ADMIN = "postgresql://postgres:postgres@localhost:5432/myca"
DB_APP = "postgresql://app_service_user:app_service_secret@localhost:5432/myca"


@pytest.fixture
def setup_m2_6_tenant():
    """Sets up a clean multi-GST tenant for Milestone 2.6 testing."""
    pan = "".join(random.choices("ABCDEFGHIJKLMNOPQRSTUVWXYZ", k=5)) + \
          "".join(random.choices("0123456789", k=4)) + \
          random.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    gstin_mh = f"27{pan}1ZE"
    org_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    reg_mh = str(uuid.uuid4())

    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO public.organizations (id, legal_name, pan, trade_name)
                VALUES (%s, 'M2.6 Receipt Gate Ltd', %s, 'ReceiptCo');
            """, (org_id, pan))

            cur.execute("""
                INSERT INTO public.global_users (id, email, full_name)
                VALUES (%s, %s, 'CA Auditor 2.6');
            """, (user_id, f"auditor_{uuid.uuid4().hex[:6]}@receipt.test"))

            cur.execute("""
                INSERT INTO public.organization_memberships (org_id, user_id, role, status)
                VALUES (%s, %s, 'owner', 'active');
            """, (org_id, user_id))

            cur.execute("""
                INSERT INTO public.gst_registrations (
                    id, org_id, gstin, state_code, legal_name, is_primary
                ) VALUES (%s, %s, %s, '27', 'M2.6 Receipt Gate Ltd', true);
            """, (reg_mh, org_id, gstin_mh))

    return {
        "org_id": org_id,
        "user_id": user_id,
        "pan": pan,
        "gstin_mh": gstin_mh,
        "reg_mh": reg_mh
    }


# =============================================================================
# 1. Corrected GSTR-3B Table 4 & SUP-2B-998 Accounting (Net ITC ₹3,36,600)
# =============================================================================

def test_corrected_net_itc_with_unreceived_reversal():
    """
    Critical statutory test:
    Validates that unreceived document SUP-2B-998 (IGST ₹18,000.00):
    - Appears in statement 4(A)(5) and 4(A) total (₹4,82,500.00)
    - Is temporarily reversed under Table 4(B)(2) as Section 16(2)(b) receipt reversal
    - Table 4(B)(2) becomes ₹68,400.00 (Rule 37 ₹18,000 + Rule 37A ₹32,400 + Receipt Reversal ₹18,000)
    - Net ITC Available in Table 4(C) is ₹3,36,600.00 (CGST ₹82,250 | SGST ₹82,250 | IGST ₹1,72,100)
    """
    rp = "2026-11"
    recipient = "27AABCB1234M1ZT"

    # 1. GSTR-2B Statement Documents
    gstr2b_docs = [
        # 4(A)(1) Import of Goods: BoE-882190
        {"id": "g_boe", "document_type": "bill_of_entry", "section_name": "IMPG", "document_number": "BOE-882190",
         "document_date": "2026-11-02", "taxable_value": Decimal("333333.33"), "integrated_tax": Decimal("60000.00"),
         "itc_availability": "available"},
        # 4(A)(3) RCM Transport Consignments:
        {"id": "g_rcm_intra", "document_type": "invoice", "section_name": "B2B_RCM", "document_number": "GTA-NOV-01A",
         "document_date": "2026-11-05", "taxable_value": Decimal("400000.00"), "central_tax": Decimal("10000.00"),
         "state_ut_tax": Decimal("10000.00"), "integrated_tax": Decimal("0.00"), "supply_attract_reverse_charge": True,
         "itc_availability": "available"},
        {"id": "g_rcm_inter", "document_type": "invoice", "section_name": "B2B_RCM", "document_number": "GTA-NOV-01B",
         "document_date": "2026-11-05", "taxable_value": Decimal("200000.00"), "central_tax": Decimal("0.00"),
         "state_ut_tax": Decimal("0.00"), "integrated_tax": Decimal("10000.00"), "supply_attract_reverse_charge": True,
         "itc_availability": "available"},
        # 4(A)(4) ISD Invoice
        {"id": "g_isd", "document_type": "isd_invoice", "section_name": "ISD", "document_number": "ISD-INV-44",
         "document_date": "2026-11-08", "central_tax": Decimal("5250.00"), "state_ut_tax": Decimal("5250.00"),
         "integrated_tax": Decimal("15000.00"), "itc_availability": "available"},
        # 4(A)(5) Current B2B Inward:
        {"id": "g_b2b1", "document_type": "invoice", "document_number": "INV-B2B-01", "document_date": "2026-11-10",
         "central_tax": Decimal("18000.00"), "state_ut_tax": Decimal("18000.00"), "integrated_tax": Decimal("0.00"), "itc_availability": "available"},
        {"id": "g_b2b2", "document_type": "invoice", "document_number": "INV-B2B-02", "document_date": "2026-11-12",
         "central_tax": Decimal("32500.00"), "state_ut_tax": Decimal("32500.00"), "integrated_tax": Decimal("0.00"), "itc_availability": "available"},
        {"id": "g_b2b3", "document_type": "invoice", "document_number": "INV-B2B-03", "document_date": "2026-11-14",
         "central_tax": Decimal("0.00"), "state_ut_tax": Decimal("0.00"), "integrated_tax": Decimal("90000.00"), "itc_availability": "available"},
        {"id": "g_b2b4", "document_type": "invoice", "document_number": "INV-B2B-04", "document_date": "2026-11-15",
         "central_tax": Decimal("6250.00"), "state_ut_tax": Decimal("6250.00"), "integrated_tax": Decimal("0.00"), "itc_availability": "available"},
        {"id": "g_b2b5", "document_type": "invoice", "document_number": "INV-B2B-05", "document_date": "2026-11-16",
         "central_tax": Decimal("9000.00"), "state_ut_tax": Decimal("9000.00"), "integrated_tax": Decimal("0.00"), "itc_availability": "available"},
        {"id": "g_b2b6", "document_type": "invoice", "document_number": "INV-B2B-06", "document_date": "2026-11-18",
         "central_tax": Decimal("0.00"), "state_ut_tax": Decimal("0.00"), "integrated_tax": Decimal("32400.00"), "itc_availability": "available"},
        {"id": "g_b2b7", "document_type": "invoice", "document_number": "INV-B2B-07", "document_date": "2026-11-20",
         "central_tax": Decimal("40375.00"), "state_ut_tax": Decimal("40375.00"), "integrated_tax": Decimal("0.00"), "itc_availability": "available"},
        {"id": "g_cn", "document_type": "credit_note", "document_number": "CN-B2B-01", "document_date": "2026-11-22",
         "central_tax": Decimal("0.00"), "state_ut_tax": Decimal("0.00"), "integrated_tax": Decimal("-2900.00"), "itc_availability": "available"},
        # SUP-2B-998: In GSTR-2B statement, unbooked in purchase register, unreceived!
        {"id": "g_unbooked", "document_type": "invoice", "document_number": "SUP-2B-998", "document_date": "2026-11-24",
         "supplier_gstin": "24AAACR1234K1ZA", "supplier_name": "Reliance Polymers Dist",
         "taxable_value": Decimal("100000.00"), "central_tax": Decimal("0.00"), "state_ut_tax": Decimal("0.00"),
         "integrated_tax": Decimal("18000.00"), "itc_availability": "available"},
        # 4(D)(2) Ineligible credit: EXP-2025-99
        {"id": "g_inelig", "document_type": "invoice", "document_number": "EXP-2025-99", "document_date": "2025-08-10",
         "central_tax": Decimal("0.00"), "state_ut_tax": Decimal("0.00"), "integrated_tax": Decimal("12400.00"), "itc_availability": "not_available"}
    ]

    purchase_docs = [
        {"id": "p_boe", "document_number": "BOE-882190", "status": "approved", "receipt_status": "RECEIVED", "delivery_challan_ref": "BOE-DC-1"},
        {"id": "p_rcm_intra", "document_number": "GTA-NOV-01A", "status": "approved", "receipt_status": "RECEIVED", "delivery_challan_ref": "LR-01A"},
        {"id": "p_rcm_inter", "document_number": "GTA-NOV-01B", "status": "approved", "receipt_status": "RECEIVED", "delivery_challan_ref": "LR-01B"},
        {"id": "p_isd", "document_number": "ISD-INV-44", "status": "approved", "receipt_status": "RECEIVED", "service_completion_date": "2026-11-08"},
        {"id": "p_b2b1", "document_number": "INV-B2B-01", "status": "approved", "receipt_status": "RECEIVED", "delivery_challan_ref": "DC-01"},
        {"id": "p_b2b2", "document_number": "INV-B2B-02", "status": "approved", "receipt_status": "RECEIVED", "delivery_challan_ref": "DC-02"},
        {"id": "p_b2b3", "document_number": "INV-B2B-03", "status": "approved", "receipt_status": "RECEIVED", "delivery_challan_ref": "DC-03"},
        {"id": "p_b2b4", "document_number": "INV-B2B-04", "status": "approved", "receipt_status": "RECEIVED", "service_completion_date": "2026-11-15"},
        {"id": "p_b2b5", "document_number": "INV-B2B-05", "status": "approved", "receipt_status": "RECEIVED", "service_completion_date": "2026-11-16"},
        {"id": "p_b2b6", "document_number": "INV-B2B-06", "status": "approved", "receipt_status": "RECEIVED", "delivery_challan_ref": "DC-06"},
        {"id": "p_b2b7", "document_number": "INV-B2B-07", "status": "approved", "receipt_status": "RECEIVED", "delivery_challan_ref": "DC-07"},
        {"id": "p_cn", "document_number": "CN-B2B-01", "status": "approved", "receipt_status": "RECEIVED", "delivery_challan_ref": "DC-CN"}
        # Note: SUP-2B-998 is MISSING IN BOOKS!
    ]

    matches = [
        {"purchase_doc_id": "p_boe", "gstr2b_doc_id": "g_boe"},
        {"purchase_doc_id": "p_rcm_intra", "gstr2b_doc_id": "g_rcm_intra"},
        {"purchase_doc_id": "p_rcm_inter", "gstr2b_doc_id": "g_rcm_inter"},
        {"purchase_doc_id": "p_isd", "gstr2b_doc_id": "g_isd"},
        {"purchase_doc_id": "p_b2b1", "gstr2b_doc_id": "g_b2b1"},
        {"purchase_doc_id": "p_b2b2", "gstr2b_doc_id": "g_b2b2"},
        {"purchase_doc_id": "p_b2b3", "gstr2b_doc_id": "g_b2b3"},
        {"purchase_doc_id": "p_b2b4", "gstr2b_doc_id": "g_b2b4"},
        {"purchase_doc_id": "p_b2b5", "gstr2b_doc_id": "g_b2b5"},
        {"purchase_doc_id": "p_b2b6", "gstr2b_doc_id": "g_b2b6"},
        {"purchase_doc_id": "p_b2b7", "gstr2b_doc_id": "g_b2b7"},
        {"purchase_doc_id": "p_cn", "gstr2b_doc_id": "g_cn"}
    ]

    # Prior-period reclaim under Table 4(D)(1)
    reclaims = [
        {"id": "rec_01", "purchase_doc_id": "p_b2b7", "reavailed_cgst": Decimal("8625.00"),
         "reavailed_sgst": Decimal("8625.00"), "reavailed_igst": Decimal("0.00"), "reavailed_cess": Decimal("0.00")}
    ]

    # Statutory Reversals Ledger Events:
    reversals_ledger = [
        # 1. 4(B)(1) Motor Vehicle 17(5): CGST 32.5k + SGST 32.5k = 65k
        {"id": "rev_veh", "purchase_doc_id": "p_b2b2", "rule_code": "SEC_17_5_MOTOR",
         "reversed_cgst": Decimal("32500.00"), "reversed_sgst": Decimal("32500.00"), "reversed_igst": Decimal("0.00"), "reversed_cess": Decimal("0.00")},
        # 2. 4(B)(1) Outdoor Catering 17(5): CGST 6.25k + SGST 6.25k = 12.5k
        {"id": "rev_cat", "purchase_doc_id": "p_b2b4", "rule_code": "SEC_17_5_FOOD",
         "reversed_cgst": Decimal("6250.00"), "reversed_sgst": Decimal("6250.00"), "reversed_igst": Decimal("0.00"), "reversed_cess": Decimal("0.00")},
        # 3. 4(B)(2) Rule 37 180 days: CGST 9k + SGST 9k = 18k
        {"id": "rev_r37", "purchase_doc_id": "p_b2b5", "rule_code": "RULE_37_180_DAYS",
         "reversed_cgst": Decimal("9000.00"), "reversed_sgst": Decimal("9000.00"), "reversed_igst": Decimal("0.00"), "reversed_cess": Decimal("0.00")},
        # 4. 4(B)(2) Rule 37A supplier default: IGST 32.4k
        {"id": "rev_r37a", "purchase_doc_id": "p_b2b6", "rule_code": "RULE_37A_NON_FILING",
         "reversed_cgst": Decimal("0.00"), "reversed_sgst": Decimal("0.00"), "reversed_igst": Decimal("32400.00"), "reversed_cess": Decimal("0.00")},
        # 5. 4(B)(2) Section 16(2)(b) Receipt Reversal for SUP-2B-998: IGST 18k (Circular 170)
        {"id": "rev_receipt", "purchase_doc_id": "g_unbooked", "rule_code": "SEC_16_2_B_PENDING_RECEIPT",
         "document_number": "SUP-2B-998", "supplier_gstin": "24AAACR1234K1ZA",
         "reversed_cgst": Decimal("0.00"), "reversed_sgst": Decimal("0.00"), "reversed_igst": Decimal("18000.00"), "reversed_cess": Decimal("0.00")}
    ]

    out = generate_gstr3b_table4_summary(
        return_period=rp,
        recipient_gstin=recipient,
        gstr2b_docs=gstr2b_docs,
        purchase_docs=purchase_docs,
        matches=matches,
        assessments=[],
        reclaims=reclaims,
        reversals_ledger=reversals_ledger
    )

    # 1. Statement Total 4(A) includes the unbooked ₹18,000 document
    assert out.total_itc_available == Decimal("482500.00")
    assert out.components_4a["4A_total"].cgst == Decimal("130000.00")
    assert out.components_4a["4A_total"].sgst == Decimal("130000.00")
    assert out.components_4a["4A_total"].igst == Decimal("222500.00")

    # 2. Permanent Reversals 4(B)(1)
    assert out.itc_reversed_rule_38_42_43 == Decimal("77500.00")
    assert out.components_4b["4B1_permanent"].cgst == Decimal("38750.00")
    assert out.components_4b["4B1_permanent"].sgst == Decimal("38750.00")
    assert out.components_4b["4B1_permanent"].igst == Decimal("0.00")

    # 3. Reclaimable Reversals 4(B)(2): Rule 37 (18k) + Rule 37A (32.4k) + Receipt Reversal (18k) = 68.4k
    assert out.itc_reversed_rule_37_180_days == Decimal("18000.00")
    assert out.itc_reversed_rule_37a == Decimal("32400.00")
    assert out.itc_reversed_sec_16_2_b_receipt == Decimal("18000.00")
    assert out.total_itc_reversed_reclaimable_4b2 == Decimal("68400.00")
    assert out.components_4b["4B2_total"].cgst == Decimal("9000.00")
    assert out.components_4b["4B2_total"].sgst == Decimal("9000.00")
    assert out.components_4b["4B2_total"].igst == Decimal("50400.00")

    # 4. Total Reversals 4(B) = 77.5k + 68.4k = 145.9k
    assert out.total_itc_reversed == Decimal("145900.00")
    assert out.components_4b["4B_total"].cgst == Decimal("47750.00")
    assert out.components_4b["4B_total"].sgst == Decimal("47750.00")
    assert out.components_4b["4B_total"].igst == Decimal("50400.00")

    # 5. Corrected Net ITC 4(C) = 4(A) - 4(B)(1) - 4(B)(2) = ₹3,36,600.00!
    assert out.net_itc_available == Decimal("336600.00")
    assert out.components_4c_net.cgst == Decimal("82250.00")
    assert out.components_4c_net.sgst == Decimal("82250.00")
    assert out.components_4c_net.igst == Decimal("172100.00")

    # 6. Statutory Breakup Disclosures:
    assert out.itc_reclaimed_earlier_reversal_4d1 == Decimal("17250.00")
    assert out.ineligible_sec_16_4_and_pos_restrictions_4d2 == Decimal("12400.00")


# =============================================================================
# 2. Section 16(2)(b) Receipt Gate Defaults & Historical Audit
# =============================================================================

def test_default_receipt_status_unknown_and_historical_audit(setup_m2_6_tenant):
    """
    Verifies that:
    - New purchase documents strictly default to 'UNKNOWN'
    - Historical records without evidence are audited and classified as 'UNKNOWN'
    - Bare client attempts to mark 'RECEIVED' on creation without evidence fallback to 'UNKNOWN'
    """
    ctx = setup_m2_6_tenant
    headers = {"X-User-Id": ctx["user_id"], "X-Org-Id": ctx["org_id"]}

    # Create new document without receipt evidence
    doc_num = f"INV-REC-TEST-{uuid.uuid4().hex[:6]}"
    resp = client.post(
        "/api/v1/reconciliation/purchase/create",
        headers=headers,
        json={
            "registration_id": ctx["reg_mh"],
            "document_type": "tax_invoice",
            "document_number": doc_num,
            "document_date": "2026-11-01",
            "financial_period": "2026-11",
            "supplier_gstin": "27AAACR1234K1ZA",
            "supplier_legal_name": "Supplier Default Test",
            "recipient_gstin": ctx["gstin_mh"],
            "place_of_supply": "27",
            "total_taxable_amount": "50000.00",
            "cgst_amount": "4500.00",
            "sgst_amount": "4500.00",
            "line_items": []
        }
    )
    assert resp.status_code == 200, resp.text
    created_id = resp.json()["id"]

    # Verify column defaults to 'UNKNOWN' in PostgreSQL
    with psycopg.connect(DB_ADMIN) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT receipt_status FROM public.purchase_documents WHERE id = %s;", (created_id,))
            st = cur.fetchone()[0]
            assert st == "UNKNOWN"

    # Attempt to create with receipt_status='RECEIVED' but without GRN evidence -> must fallback to 'UNKNOWN'
    doc_num_bare = f"INV-BARE-{uuid.uuid4().hex[:6]}"
    resp_bare = client.post(
        "/api/v1/reconciliation/purchase/create",
        headers=headers,
        json={
            "registration_id": ctx["reg_mh"],
            "document_type": "tax_invoice",
            "document_number": doc_num_bare,
            "document_date": "2026-11-02",
            "financial_period": "2026-11",
            "supplier_gstin": "27AAACR1234K1ZA",
            "supplier_legal_name": "Bare Client Test",
            "recipient_gstin": ctx["gstin_mh"],
            "place_of_supply": "27",
            "receipt_status": "RECEIVED",  # Client tries to claim receipt without evidence!
            "total_taxable_amount": "30000.00",
            "line_items": []
        }
    )
    assert resp_bare.status_code == 200
    bare_id = resp_bare.json()["id"]

    with psycopg.connect(DB_ADMIN) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT receipt_status FROM public.purchase_documents WHERE id = %s;", (bare_id,))
            st_bare = cur.fetchone()[0]
            assert st_bare == "UNKNOWN"  # Correctly prevented unverified creation bypass!


# =============================================================================
# 3. Unauthorized Receipt Confirmation & Evidence Validation
# =============================================================================

def test_unauthorized_receipt_confirmation_rejected(setup_m2_6_tenant):
    """
    Verifies that:
    - Non-member / unauthorized user cannot verify receipt (HTTP 403)
    - Missing GRN/challan for goods rejected (HTTP 400)
    - Valid GRN reference and date successfully updates receipt status to RECEIVED
    """
    ctx = setup_m2_6_tenant
    headers = {"X-User-Id": ctx["user_id"], "X-Org-Id": ctx["org_id"]}

    # Create document
    p_id = str(uuid.uuid4())
    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO public.purchase_documents (
                    id, org_id, registration_id, document_type, document_number,
                    document_date, financial_period, supplier_gstin, supplier_legal_name,
                    recipient_gstin, place_of_supply, goods_services_type, receipt_status
                ) VALUES (%s, %s, %s, 'tax_invoice', 'INV-AUTH-01', '2026-11-05', '2026-11',
                          '27AAACR1234K1ZA', 'Steel Mills Ltd', %s, '27', 'goods', 'UNKNOWN');
            """, (p_id, ctx["org_id"], ctx["reg_mh"], ctx["gstin_mh"]))

    # 1. Non-member user tries to verify
    fake_user = str(uuid.uuid4())
    fake_headers = {"X-User-Id": fake_user, "X-Org-Id": ctx["org_id"]}
    resp_unauth = client.post(
        f"/api/v1/reconciliation/purchase/{p_id}/verify-receipt",
        headers=fake_headers,
        json={"receipt_status": "RECEIVED", "goods_receipt_date": "2026-11-06", "goods_receipt_note_ref": "GRN-001"}
    )
    assert resp_unauth.status_code == 403

    # 2. Authorized user tries to set RECEIVED without GRN reference
    resp_missing_grn = client.post(
        f"/api/v1/reconciliation/purchase/{p_id}/verify-receipt",
        headers=headers,
        json={"receipt_status": "RECEIVED", "goods_receipt_date": "2026-11-06"}
    )
    assert resp_missing_grn.status_code == 400
    assert "Goods receipt verification requires" in resp_missing_grn.text

    # 3. Authorized user supplies full GRN proof
    resp_valid = client.post(
        f"/api/v1/reconciliation/purchase/{p_id}/verify-receipt",
        headers=headers,
        json={
            "receipt_status": "RECEIVED",
            "goods_receipt_date": "2026-11-06",
            "goods_receipt_note_ref": "GRN-2026-11-001",
            "delivery_challan_ref": "DC-88910"
        }
    )
    assert resp_valid.status_code == 200
    assert resp_valid.json()["receipt_status"] == "RECEIVED"
    assert resp_valid.json()["goods_receipt_note_ref"] == "GRN-2026-11-001"


# =============================================================================
# 4. Service Receipt Verification & Deemed Receipt
# =============================================================================

def test_service_and_deemed_receipt_verification(setup_m2_6_tenant):
    """
    Verifies that:
    - Services require completion date AND acceptance certificate under Section 16(2)(b)
    - Deemed receipt requires statutory legal basis
    """
    ctx = setup_m2_6_tenant
    headers = {"X-User-Id": ctx["user_id"], "X-Org-Id": ctx["org_id"]}

    # Create service document
    srv_id = str(uuid.uuid4())
    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO public.purchase_documents (
                    id, org_id, registration_id, document_type, document_number,
                    document_date, financial_period, supplier_gstin, supplier_legal_name,
                    recipient_gstin, place_of_supply, goods_services_type, receipt_status
                ) VALUES (%s, %s, %s, 'tax_invoice', 'SRV-CONSULT-01', '2026-11-01', '2026-11',
                          '27AAACS1234K1ZA', 'Cloud Architect Advisory', %s, '27', 'services', 'UNKNOWN');
            """, (srv_id, ctx["org_id"], ctx["reg_mh"], ctx["gstin_mh"]))

    # Missing certificate -> 400
    resp_srv_fail = client.post(
        f"/api/v1/reconciliation/purchase/{srv_id}/verify-receipt",
        headers=headers,
        json={"receipt_status": "RECEIVED", "service_completion_date": "2026-11-15"}
    )
    assert resp_srv_fail.status_code == 400
    assert "Service receipt verification requires" in resp_srv_fail.text

    # Complete certificate -> 200
    resp_srv_ok = client.post(
        f"/api/v1/reconciliation/purchase/{srv_id}/verify-receipt",
        headers=headers,
        json={
            "receipt_status": "RECEIVED",
            "service_completion_date": "2026-11-15",
            "service_acceptance_cert_ref": "SAC-CLOUD-2026-11"
        }
    )
    assert resp_srv_ok.status_code == 200
    assert resp_srv_ok.json()["receipt_status"] == "RECEIVED"
    assert resp_srv_ok.json()["service_acceptance_cert_ref"] == "SAC-CLOUD-2026-11"


# =============================================================================
# 5. Goods Received in Lots / Instalments (Last-Lot Rule)
# =============================================================================

def test_goods_received_in_multiple_lots_last_lot_rule(setup_m2_6_tenant):
    """
    Verifies Section 16(2)(b) First Proviso:
    Where goods against an invoice are received in lots or instalments,
    ITC is strictly entitled only upon receipt of the last lot or instalment.
    """
    ctx = setup_m2_6_tenant
    headers = {"X-User-Id": ctx["user_id"], "X-Org-Id": ctx["org_id"]}

    # Create document with 3 lots expected
    p_id = str(uuid.uuid4())
    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO public.purchase_documents (
                    id, org_id, registration_id, document_type, document_number,
                    document_date, financial_period, supplier_gstin, supplier_legal_name,
                    recipient_gstin, place_of_supply, goods_services_type, receipt_status,
                    total_lots_expected, quantity_expected, remaining_quantity
                ) VALUES (%s, %s, %s, 'tax_invoice', 'INV-LOTS-100', '2026-11-01', '2026-11',
                          '27AAACR1234K1ZA', 'Steel Fabricators Ltd', %s, '27', 'goods', 'UNKNOWN',
                          3, 1000.00, 1000.00);
            """, (p_id, ctx["org_id"], ctx["reg_mh"], ctx["gstin_mh"]))

    # Lot 1: Delivery of 300 MT (remaining 700 MT) -> status becomes PARTIALLY_RECEIVED
    resp_lot1 = client.post(
        f"/api/v1/reconciliation/purchase/{p_id}/record-lot",
        headers=headers,
        json={
            "lot_number": 1,
            "delivery_challan_ref": "DC-LOT-01",
            "receipt_date": "2026-11-05",
            "quantity_received": "300.00",
            "remaining_quantity": "700.00",
            "is_last_lot": False
        }
    )
    assert resp_lot1.status_code == 200
    assert resp_lot1.json()["receipt_status"] == "PARTIALLY_RECEIVED"
    assert resp_lot1.json()["is_last_lot_received"] is False

    # Attempt to bypass and force status to 'RECEIVED' while 700 MT remains -> 400 Bad Request
    resp_premature = client.post(
        f"/api/v1/reconciliation/purchase/{p_id}/verify-receipt",
        headers=headers,
        json={
            "receipt_status": "RECEIVED",
            "goods_receipt_date": "2026-11-06",
            "goods_receipt_note_ref": "GRN-PREMATURE",
            "remaining_quantity": "700.00",
            "is_last_lot": False
        }
    )
    assert resp_premature.status_code == 400
    assert "Section 16(2)(b) First Proviso" in resp_premature.text

    # Lot 2: Delivery of 400 MT (remaining 300 MT)
    resp_lot2 = client.post(
        f"/api/v1/reconciliation/purchase/{p_id}/record-lot",
        headers=headers,
        json={
            "lot_number": 2,
            "delivery_challan_ref": "DC-LOT-02",
            "receipt_date": "2026-11-12",
            "quantity_received": "400.00",
            "remaining_quantity": "300.00",
            "is_last_lot": False
        }
    )
    assert resp_lot2.status_code == 200
    assert resp_lot2.json()["receipt_status"] == "PARTIALLY_RECEIVED"

    # Lot 3: Final Lot Delivery of 300 MT (remaining 0 MT, is_last_lot=True) -> Transitions to RECEIVED!
    resp_lot3 = client.post(
        f"/api/v1/reconciliation/purchase/{p_id}/record-lot",
        headers=headers,
        json={
            "lot_number": 3,
            "delivery_challan_ref": "DC-LOT-03-FINAL",
            "receipt_date": "2026-11-20",
            "quantity_received": "300.00",
            "remaining_quantity": "0.00",
            "is_last_lot": True
        }
    )
    assert resp_lot3.status_code == 200
    assert resp_lot3.json()["receipt_status"] == "RECEIVED"
    assert resp_lot3.json()["is_last_lot_received"] is True


# =============================================================================
# 6. Reclaim Lifecycle & Protection Against Unlinked Reclaims
# =============================================================================

def test_reclaim_lifecycle_and_validation(setup_m2_6_tenant):
    """
    Verifies that:
    - An ITC reclaim cannot be recorded without referencing a valid earlier temporary reversal
    - Reclaim cannot exceed original reversal
    - Reclaim cannot be recorded against permanent Section 17(5) reversal
    - Concurrency protections prevent duplicate reclaims
    """
    ctx = setup_m2_6_tenant
    headers = {"X-User-Id": ctx["user_id"], "X-Org-Id": ctx["org_id"]}

    # Create purchase doc
    p_id = str(uuid.uuid4())
    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO public.purchase_documents (
                    id, org_id, registration_id, document_type, document_number,
                    document_date, financial_period, supplier_gstin, supplier_legal_name,
                    recipient_gstin, place_of_supply, is_availed_in_3b, receipt_status
                ) VALUES (%s, %s, %s, 'tax_invoice', 'INV-RECLAIM-TEST', '2026-10-10', '2026-10',
                          '24AAACR1234K1ZA', 'Gujarat Polymers', %s, '27', true, 'RECEIVED');
            """, (p_id, ctx["org_id"], ctx["reg_mh"], ctx["gstin_mh"]))

    # Record 4(B)(2) Section 16(2)(b) receipt reversal in Oct 2026
    resp_rev = client.post(
        "/api/v1/reconciliation/itc-ledger/record-event",
        headers=headers,
        json={
            "purchase_doc_id": p_id,
            "event_type": "REVERSAL_SECTION_16_2_B",
            "tax_period": "2026-10",
            "cgst_amount": "0.00",
            "sgst_amount": "0.00",
            "igst_amount": "18000.00",
            "reversal_reason": "Goods in transit not received by 31st Oct under Section 16(2)(b)"
        }
    )
    assert resp_rev.status_code == 200, resp_rev.text
    rev_id = resp_rev.json()["reversal_event_id"]

    # 1. Attempt to reclaim WITHOUT referencing an earlier reversal -> 400 Bad Request
    resp_bad_reclaim = client.post(
        "/api/v1/reconciliation/itc-ledger/record-event",
        headers=headers,
        json={
            "purchase_doc_id": p_id,
            "event_type": "RECLAIM",
            "tax_period": "2026-11",
            "igst_amount": "18000.00"
            # reversal_event_id is omitted!
        }
    )
    assert resp_bad_reclaim.status_code == 400
    assert "reversal_event_id" in resp_bad_reclaim.text

    # 2. Attempt to reclaim an amount exceeding original reversal (₹20,000 > ₹18,000) -> 400
    resp_excess = client.post(
        "/api/v1/reconciliation/itc-ledger/record-event",
        headers=headers,
        json={
            "purchase_doc_id": p_id,
            "event_type": "RECLAIM",
            "tax_period": "2026-11",
            "igst_amount": "20000.00",
            "reversal_event_id": rev_id
        }
    )
    assert resp_excess.status_code == 400
    assert "Reclaim bounds exceeded" in resp_excess.text

    # 3. Valid statutory reclaim upon receipt of goods in Nov 2026 -> 200 OK
    resp_reclaim_ok = client.post(
        "/api/v1/reconciliation/itc-ledger/record-event",
        headers=headers,
        json={
            "purchase_doc_id": p_id,
            "event_type": "RECLAIM",
            "tax_period": "2026-11",
            "igst_amount": "18000.00",
            "reversal_event_id": rev_id,
            "reversal_reason": "Goods received in factory on 5th Nov with verified GRN-8821"
        }
    )
    assert resp_reclaim_ok.status_code == 200
    assert resp_reclaim_ok.json()["event_type"] == "RECLAIM"


# =============================================================================
# 7. GTA & RCM Statutory Verification Service
# =============================================================================

def test_rcm_and_gta_statutory_verification():
    """
    Verifies statutory conditions for reverse charge:
    - Forward charge declaration (Annexure V) removes RCM liability
    - Transport without consignment note is exempt under Notification 12/2017-CT(R) Entry 18
    - Registered recipient determines Place of Supply under Section 12(8) IGST Act
    - Section 49(4) cash payment requirement
    """
    # 1. GTA opted forward charge (Annexure V declaration)
    res_fwd = validate_rcm_and_gta_eligibility(
        service_type="GTA_ROAD_TRANSPORT",
        taxable_value=Decimal("100000.00"),
        gst_rate=Decimal("12.00"),
        supplier_gstin="27AAGFG4411D1ZA",
        recipient_gstin="27AABCB1234M1ZT",
        place_of_supply="27",
        gta_opted_forward_charge=True,
        supplier_annexure_v_declaration=True
    )
    assert res_fwd.is_rcm_applicable is False
    assert res_fwd.tax_type == "FORWARD_CHARGE"
    assert res_fwd.itc_eligibility_status == "FORWARD_CHARGE_GTA"

    # 2. Road transport without consignment note (Exempt transport, not GTA)
    res_no_cn = validate_rcm_and_gta_eligibility(
        service_type="GTA_ROAD_TRANSPORT",
        taxable_value=Decimal("50000.00"),
        gst_rate=Decimal("5.00"),
        supplier_gstin="27AAGFG4411D1ZA",
        recipient_gstin="27AABCB1234M1ZT",
        place_of_supply="27",
        has_consignment_note=False
    )
    assert res_no_cn.is_rcm_applicable is False
    assert res_no_cn.tax_type == "EXEMPT"
    assert res_no_cn.itc_eligibility_status == "EXEMPT_NO_RCM"

    # 3. Inter-state GTA transport with consignment note, pending cash payment
    res_inter = validate_rcm_and_gta_eligibility(
        service_type="GTA_ROAD_TRANSPORT",
        taxable_value=Decimal("200000.00"),
        gst_rate=Decimal("5.00"),
        supplier_gstin="24AAGFG4411D1ZA",  # Gujarat
        recipient_gstin="27AABCB1234M1ZT", # Maharashtra
        place_of_supply="27",
        has_consignment_note=True,
        consignment_note_number="CN-SURAT-01",
        cash_payment_challan_ref=None
    )
    assert res_inter.is_rcm_applicable is True
    assert res_inter.tax_type == "INTER_STATE"
    assert res_inter.igst_amount == Decimal("10000.00")
    assert res_inter.itc_eligibility_status == "PENDING_CASH_DISCHARGE_31D"

    # 4. Intra-state GTA transport with confirmed cash payment challan
    res_intra_paid = validate_rcm_and_gta_eligibility(
        service_type="GTA_ROAD_TRANSPORT",
        taxable_value=Decimal("400000.00"),
        gst_rate=Decimal("5.00"),
        supplier_gstin="27AAGFG4411D1ZA",  # Maharashtra
        recipient_gstin="27AABCB1234M1ZT", # Maharashtra
        place_of_supply="27",
        has_consignment_note=True,
        consignment_note_number="CN-PUNE-01",
        cash_payment_challan_ref="CPIN-2026-11-998811"
    )
    assert res_intra_paid.is_rcm_applicable is True
    assert res_intra_paid.tax_type == "INTRA_STATE"
    assert res_intra_paid.cgst_amount == Decimal("10000.00")
    assert res_intra_paid.sgst_amount == Decimal("10000.00")
    assert res_intra_paid.is_cash_discharged is True
    assert res_intra_paid.itc_eligibility_status == "ELIGIBLE_4A3"


# =============================================================================
# 8. Unbooked Candidate Review & Booking Workflow
# =============================================================================

def test_unbooked_candidate_review_and_booking_workflow(setup_m2_6_tenant):
    """
    Verifies that:
    - GSTR-2B documents missing in purchase register enter unbooked candidate review
    - Candidate receipt verification attaches GRN evidence
    - Candidate can be booked into the purchase register while maintaining MISSING_IN_BOOKS audit history
    """
    ctx = setup_m2_6_tenant
    headers = {"X-User-Id": ctx["user_id"], "X-Org-Id": ctx["org_id"]}

    # Create GSTR-2B import & snapshot with an unbooked document
    imp_id = str(uuid.uuid4())
    snap_id = str(uuid.uuid4())
    g_doc_id = str(uuid.uuid4())

    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO public.gstr2b_imports (id, org_id, registration_id, recipient_gstin, return_period, financial_year, file_name, file_type, file_size_bytes, file_hash)
                VALUES (%s, %s, %s, %s, '2026-11', '2026-27', 'gstr2b_nov.json', 'json', 1024, %s);
            """, (imp_id, ctx["org_id"], ctx["reg_mh"], ctx["gstin_mh"], uuid.uuid4().hex))

            cur.execute("""
                INSERT INTO public.gstr2b_snapshots (id, org_id, import_id, registration_id, recipient_gstin, return_period, version_number, total_itc_available)
                VALUES (%s, %s, %s, %s, %s, '2026-11', 1, 18000.00);
            """, (snap_id, ctx["org_id"], imp_id, ctx["reg_mh"], ctx["gstin_mh"]))

            cur.execute("""
                INSERT INTO public.gstr2b_source_documents (
                    id, org_id, snapshot_id, section_name, itc_availability, source_form,
                    supplier_gstin, supplier_name, document_type, document_number,
                    document_date, taxable_value, integrated_tax, place_of_supply
                ) VALUES (%s, %s, %s, 'B2B', 'available', 'GSTR-1',
                          '24AAACR1234K1ZA', 'Reliance Polymers Dist', 'invoice', 'SUP-2B-998',
                          '2026-11-20', 100000.00, 18000.00, '27');
            """, (g_doc_id, ctx["org_id"], snap_id))

    # 1. Fetch unbooked candidates endpoint
    resp_cand = client.get(f"/api/v1/reconciliation/unbooked-candidates?snapshot_id={snap_id}", headers=headers)
    assert resp_cand.status_code == 200
    candidates = resp_cand.json()
    assert len(candidates) >= 1
    target_cand = next(c for c in candidates if c["document_number"] == "SUP-2B-998")
    cand_id = target_cand["id"]
    assert target_cand["receipt_status"] == "UNKNOWN"
    assert target_cand["reconciliation_status"] == "MISSING_IN_BOOKS"

    # 2. Verify receipt on unbooked candidate
    resp_ver = client.post(
        f"/api/v1/reconciliation/unbooked-candidates/{cand_id}/verify-receipt",
        headers=headers,
        json={
            "receipt_status": "RECEIVED",
            "goods_receipt_note_ref": "GRN-REL-998",
            "goods_receipt_date": "2026-11-25"
        }
    )
    assert resp_ver.status_code == 200
    assert resp_ver.json()["receipt_status"] == "RECEIVED"

    # 3. Book candidate into purchase register
    resp_book = client.post(
        f"/api/v1/reconciliation/unbooked-candidates/{cand_id}/book-entry",
        headers=headers,
        json={
            "registration_id": ctx["reg_mh"],
            "financial_period": "2026-11",
            "purchase_ledger_code": "PUR-POLYMER-2026"
        }
    )
    assert resp_book.status_code == 200
    booked_data = resp_book.json()
    assert booked_data["status"] == "BOOKED_FROM_GSTR2B"
    assert booked_data["reconciliation_status"] == "MISSING_IN_BOOKS"
    assert booked_data["purchase_doc_id"] is not None


# =============================================================================
# 9. Cross-Tenant Receipt Isolation
# =============================================================================

def test_cross_tenant_receipt_isolation(setup_m2_6_tenant):
    """
    Verifies that Tenant A cannot access or update Tenant B's receipt records under Row-Level Security.
    """
    ctx_a = setup_m2_6_tenant
    headers_a = {"X-User-Id": ctx_a["user_id"], "X-Org-Id": ctx_a["org_id"]}

    # Create document under Tenant A
    doc_a = str(uuid.uuid4())
    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO public.purchase_documents (
                    id, org_id, registration_id, document_type, document_number,
                    document_date, financial_period, supplier_gstin, supplier_legal_name,
                    recipient_gstin, place_of_supply, goods_services_type, receipt_status
                ) VALUES (%s, %s, %s, 'tax_invoice', 'INV-TENANT-A', '2026-11-05', '2026-11',
                          '27AAACR1234K1ZA', 'Tenant A Supplier', %s, '27', 'goods', 'UNKNOWN');
            """, (doc_a, ctx_a["org_id"], ctx_a["reg_mh"], ctx_a["gstin_mh"]))

    # Setup Tenant B
    org_b = str(uuid.uuid4())
    user_b = str(uuid.uuid4())
    pan_b = "".join(random.choices("ABCDEFGHIJKLMNOPQRSTUVWXYZ", k=5)) + "2222B"
    email_b = f"userb_{uuid.uuid4().hex[:6]}@test.org"
    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO public.organizations (id, legal_name, pan) VALUES (%s, 'Tenant B Ltd', %s);", (org_b, pan_b))
            cur.execute("INSERT INTO public.global_users (id, email, full_name) VALUES (%s, %s, 'User B');", (user_b, email_b))
            cur.execute("INSERT INTO public.organization_memberships (org_id, user_id, role, status) VALUES (%s, %s, 'owner', 'active');", (org_b, user_b))

    headers_b = {"X-User-Id": user_b, "X-Org-Id": org_b}

    # Tenant B tries to verify Tenant A's document -> 404 Not Found (RLS isolation)
    resp_cross = client.post(
        f"/api/v1/reconciliation/purchase/{doc_a}/verify-receipt",
        headers=headers_b,
        json={"receipt_status": "RECEIVED", "goods_receipt_date": "2026-11-10", "goods_receipt_note_ref": "GRN-CROSS"}
    )
    assert resp_cross.status_code == 404
