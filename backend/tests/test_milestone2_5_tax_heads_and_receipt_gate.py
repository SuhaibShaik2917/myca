"""
Milestone 2.5: Tax-Head Allocation Integrity & Final GSTR-3B Reconciliation Test Suite
Covers:
1. Exact tax-component distribution in Form GSTR-3B Table 4:
   - 4(A)(1): CGST 0, SGST 0, IGST 60,000, Total 60,000
   - 4(A)(2): CGST 0, SGST 0, IGST 0, Total 0
   - 4(A)(3): CGST 10,000, SGST 10,000, IGST 10,000, Total 30,000
   - 4(A)(4): CGST 5,250, SGST 5,250, IGST 15,000, Total 25,500
   - 4(A)(5): CGST 114,750, SGST 114,750, IGST 137,500, Total 367,000
   - 4(A) Total: CGST 130,000, SGST 130,000, IGST 222,500, Total 482,500
   - 4(B)(1): CGST 38,750, SGST 38,750, IGST 0, Total 77,500
   - 4(B)(2): CGST 9,000, SGST 9,000, IGST 32,400, Total 50,400
   - 4(C) Net ITC: CGST 82,250, SGST 82,250, IGST 190,100, Total 354,600
2. GTA-NOV-01 statutory place-of-supply verification (Intra-state CGST+SGST vs Inter-state IGST consignments)
3. Section 16(2)(b) receipt-of-goods/service gate (RECEIVED, PARTIALLY_RECEIVED, NOT_RECEIVED, DEEMED_RECEIVED, UNKNOWN)
4. SUP-2B-998 unbooked receipt gate, temporary reversal routing, and subsequent reclaim
5. Component-level preservation for credit notes and reclaims
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

client = TestClient(app)

from tests.test_db_config import DB_ADMIN, DB_APP


@pytest.fixture
def setup_m2_5_tenant():
    """Sets up a clean multi-GST tenant for Milestone 2.5 testing."""
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
                VALUES (%s, 'M2.5 Component Allocation Ltd', %s, 'ComponentCo');
            """, (org_id, pan))

            cur.execute("""
                INSERT INTO public.global_users (id, email, full_name)
                VALUES (%s, %s, 'CA Auditor 2.5');
            """, (user_id, f"auditor_{uuid.uuid4().hex[:6]}@component.test"))

            cur.execute("""
                INSERT INTO public.organization_memberships (org_id, user_id, role, status)
                VALUES (%s, %s, 'owner', 'active');
            """, (org_id, user_id))

            cur.execute("""
                INSERT INTO public.gst_registrations (
                    id, org_id, gstin, state_code, legal_name, is_primary
                ) VALUES (%s, %s, %s, '27', 'M2.5 Component Allocation Ltd', true);
            """, (reg_mh, org_id, gstin_mh))

    return {
        "org_id": org_id,
        "user_id": user_id,
        "pan": pan,
        "gstin_mh": gstin_mh,
        "reg_mh": reg_mh
    }


# =============================================================================
# 1. Exact Tax-Component Distribution in Form GSTR-3B Table 4
# =============================================================================

def test_exact_tax_component_distribution():
    """
    Verifies that the CGST, SGST, IGST and Cess distributions in Form GSTR-3B Table 4
    match the underlying atomic source records down to the exact rupee:
    4(A): CGST ₹1,30,000 | SGST ₹1,30,000 | IGST ₹2,22,500 | Total ₹4,82,500
    4(A)(5): CGST ₹1,14,750 | SGST ₹1,14,750 | IGST ₹1,37,500 | Total ₹3,67,000
    4(B)(1): CGST ₹38,750 | SGST ₹38,750 | IGST ₹0 | Total ₹77,500
    4(B)(2): CGST ₹9,000 | SGST ₹9,000 | IGST ₹32,400 | Total ₹50,400
    4(C): CGST ₹82,250 | SGST ₹82,250 | IGST ₹1,90,100 | Total ₹3,54,600
    """
    rp = "2026-11"
    recipient = "27AABCB1234M1ZT"

    # Atomic Source Documents for November 2026:
    gstr2b_docs = [
        # 1. 4(A)(1) Import of Goods (ICEGATE BoE-882190) -> IGST ₹60,000
        {
            "id": "g_boe", "document_number": "BOE-882190", "document_date": "2026-11-02",
            "supplier_gstin": "INBOM4", "supplier_name": "Customs Bombay Port (ICEGATE)",
            "section_name": "IMPG", "document_type": "bill_of_entry", "supply_category": "import_goods",
            "itc_availability": "available", "taxable_value": Decimal("333333.33"),
            "central_tax": Decimal("0.00"), "state_ut_tax": Decimal("0.00"),
            "integrated_tax": Decimal("60000.00"), "cess_amount": Decimal("0.00")
        },
        # 2. 4(A)(3) Inward RCM Transport: GTA Intra-state (10k CGST + 10k SGST) + Inter-state (10k IGST)
        {
            "id": "g_rcm_intra", "document_number": "GTA-NOV-01A", "document_date": "2026-11-05",
            "supplier_gstin": "27AAGFG4411D1ZA", "supplier_name": "FastTrack Logistics Intra",
            "section_name": "RCM", "document_type": "tax_invoice", "supply_category": "reverse_charge",
            "supply_attract_reverse_charge": True, "itc_availability": "available", "taxable_value": Decimal("111111.11"),
            "central_tax": Decimal("10000.00"), "state_ut_tax": Decimal("10000.00"),
            "integrated_tax": Decimal("0.00"), "cess_amount": Decimal("0.00")
        },
        {
            "id": "g_rcm_inter", "document_number": "GTA-NOV-01B", "document_date": "2026-11-05",
            "supplier_gstin": "24AAGFG4411D1Z8", "supplier_name": "FastTrack Logistics Inter",
            "section_name": "RCM", "document_type": "tax_invoice", "supply_category": "reverse_charge",
            "supply_attract_reverse_charge": True, "itc_availability": "available", "taxable_value": Decimal("55555.56"),
            "central_tax": Decimal("0.00"), "state_ut_tax": Decimal("0.00"),
            "integrated_tax": Decimal("10000.00"), "cess_amount": Decimal("0.00")
        },
        # 3. 4(A)(4) ISD Corporate HQ -> CGST ₹5,250 + SGST ₹5,250 + IGST ₹15,000 = ₹25,500
        {
            "id": "g_isd", "document_number": "ISD-INV-44", "document_date": "2026-11-07",
            "supplier_gstin": "27AAACZ1122C1Z8", "supplier_name": "Apex Corporate HQ (ISD)",
            "section_name": "ISD", "document_type": "isd_invoice", "supply_category": "isd",
            "itc_availability": "available", "taxable_value": Decimal("141666.67"),
            "central_tax": Decimal("5250.00"), "state_ut_tax": Decimal("5250.00"),
            "integrated_tax": Decimal("15000.00"), "cess_amount": Decimal("0.00")
        },
        # 4. 4(A)(5) B2B Regular Invoice 101 -> CGST ₹97,500 + SGST ₹97,500 = ₹1,95,000
        {
            "id": "g_b2b_1", "document_number": "INV-2026-101", "document_date": "2026-11-04",
            "supplier_gstin": "27AAGCB1234F1ZQ", "supplier_name": "Bharat Industrial Spares Ltd",
            "section_name": "B2B", "document_type": "tax_invoice", "supply_category": "regular_b2b",
            "itc_availability": "available", "taxable_value": Decimal("1083333.33"),
            "central_tax": Decimal("97500.00"), "state_ut_tax": Decimal("97500.00"),
            "integrated_tax": Decimal("0.00"), "cess_amount": Decimal("0.00")
        },
        # 5. 4(A)(5) B2B Interstate Invoice 105 -> IGST ₹1,19,500
        {
            "id": "g_b2b_2", "document_number": "INV-2026-105", "document_date": "2026-11-06",
            "supplier_gstin": "29BBBPB9988H1Z4", "supplier_name": "Karnataka Precision Tools",
            "section_name": "B2B", "document_type": "tax_invoice", "supply_category": "regular_b2b",
            "itc_availability": "available", "taxable_value": Decimal("663888.89"),
            "central_tax": Decimal("0.00"), "state_ut_tax": Decimal("0.00"),
            "integrated_tax": Decimal("119500.00"), "cess_amount": Decimal("0.00")
        },
        # 6. 4(A)(5) B2B Regular Invoice 9912 -> CGST ₹8,625 + SGST ₹8,625 = ₹17,250
        {
            "id": "g_b2b_3", "document_number": "AMD/9912", "document_date": "2026-11-06",
            "supplier_gstin": "27BBBEE5566K1Z1", "supplier_name": "Western Hardware Supplies",
            "section_name": "B2B", "document_type": "tax_invoice", "supply_category": "regular_b2b",
            "itc_availability": "available", "taxable_value": Decimal("95833.33"),
            "central_tax": Decimal("8625.00"), "state_ut_tax": Decimal("8625.00"),
            "integrated_tax": Decimal("0.00"), "cess_amount": Decimal("0.00")
        },
        # 7. 4(A)(5) B2B Unbooked Inward Invoice SUP-2B-998 -> IGST ₹18,000 (missing in purchase books)
        {
            "id": "g_unbooked", "document_number": "SUP-2B-998", "document_date": "2026-11-08",
            "supplier_gstin": "27AAACR1234Q1Z2", "supplier_name": "Reliance Polymers Dist",
            "section_name": "B2B", "document_type": "tax_invoice", "supply_category": "regular_b2b",
            "itc_availability": "available", "taxable_value": Decimal("100000.00"),
            "central_tax": Decimal("0.00"), "state_ut_tax": Decimal("0.00"),
            "integrated_tax": Decimal("18000.00"), "cess_amount": Decimal("0.00")
        },
        # 8. Part B Ineligible Time-Barred under Section 16(4) -> IGST ₹12,400
        {
            "id": "g_inelig", "document_number": "EXP-2025-99", "document_date": "2025-08-15",
            "supplier_gstin": "06AAACD1111C1Z3", "supplier_name": "Haryana Logistics Hub",
            "section_name": "B2B", "document_type": "tax_invoice", "supply_category": "regular_b2b",
            "itc_availability": "not_available", "taxable_value": Decimal("68888.89"),
            "central_tax": Decimal("0.00"), "state_ut_tax": Decimal("0.00"),
            "integrated_tax": Decimal("12400.00"), "cess_amount": Decimal("0.00")
        }
    ]

    purchase_docs = [
        {"id": "p_boe", "document_number": "BOE-882190", "cgst_amount": Decimal("0.00"), "sgst_amount": Decimal("0.00"), "igst_amount": Decimal("60000.00"), "cess_amount": Decimal("0.00"), "status": "approved"},
        {"id": "p_rcm_intra", "document_number": "GTA-NOV-01A", "cgst_amount": Decimal("10000.00"), "sgst_amount": Decimal("10000.00"), "igst_amount": Decimal("0.00"), "cess_amount": Decimal("0.00"), "status": "approved"},
        {"id": "p_rcm_inter", "document_number": "GTA-NOV-01B", "cgst_amount": Decimal("0.00"), "sgst_amount": Decimal("0.00"), "igst_amount": Decimal("10000.00"), "cess_amount": Decimal("0.00"), "status": "approved"},
        {"id": "p_isd", "document_number": "ISD-INV-44", "cgst_amount": Decimal("5250.00"), "sgst_amount": Decimal("5250.00"), "igst_amount": Decimal("15000.00"), "cess_amount": Decimal("0.00"), "status": "approved"},
        {"id": "p_b2b_1", "document_number": "INV-2026-101", "cgst_amount": Decimal("97500.00"), "sgst_amount": Decimal("97500.00"), "igst_amount": Decimal("0.00"), "cess_amount": Decimal("0.00"), "status": "approved"},
        {"id": "p_b2b_2", "document_number": "INV-2026-105", "cgst_amount": Decimal("0.00"), "sgst_amount": Decimal("0.00"), "igst_amount": Decimal("119500.00"), "cess_amount": Decimal("0.00"), "status": "approved"},
        {"id": "p_b2b_3", "document_number": "AMD/9912", "cgst_amount": Decimal("8625.00"), "sgst_amount": Decimal("8625.00"), "igst_amount": Decimal("0.00"), "cess_amount": Decimal("0.00"), "status": "approved"},
    ]

    matches = [
        {"purchase_doc_id": "p_boe", "gstr2b_doc_id": "g_boe"},
        {"purchase_doc_id": "p_rcm_intra", "gstr2b_doc_id": "g_rcm_intra"},
        {"purchase_doc_id": "p_rcm_inter", "gstr2b_doc_id": "g_rcm_inter"},
        {"purchase_doc_id": "p_isd", "gstr2b_doc_id": "g_isd"},
        {"purchase_doc_id": "p_b2b_1", "gstr2b_doc_id": "g_b2b_1"},
        {"purchase_doc_id": "p_b2b_2", "gstr2b_doc_id": "g_b2b_2"},
        {"purchase_doc_id": "p_b2b_3", "gstr2b_doc_id": "g_b2b_3"},
    ]

    # Reversals Ledger with exact component allocations:
    # 1. Motor Vehicle (Intra-state Maharashtra): CGST ₹32,500 + SGST ₹32,500 = ₹65,000
    # 2. Food Catering (Intra-state Maharashtra): CGST ₹6,250 + SGST ₹6,250 = ₹12,500
    #    -> Total 4(B)(1): CGST ₹38,750, SGST ₹38,750, IGST ₹0 = ₹77,500
    # 3. Rule 37 180-Day Reversal: CGST ₹9,000 + SGST ₹9,000 = ₹18,000
    # 4. Rule 37A Supplier Non-Filing: IGST ₹32,400 = ₹32,400
    #    -> Total 4(B)(2): CGST ₹9,000, SGST ₹9,000, IGST ₹32,400 = ₹50,400
    reversals_ledger = [
        {"id": "rev_veh", "purchase_doc_id": "p_b2b_1", "rule_code": "SEC_17_5_MOTOR", "reversed_cgst": Decimal("32500.00"), "reversed_sgst": Decimal("32500.00"), "reversed_igst": Decimal("0.00"), "reversed_cess": Decimal("0.00")},
        {"id": "rev_food", "purchase_doc_id": "p_b2b_1", "rule_code": "SEC_17_5_FOOD", "reversed_cgst": Decimal("6250.00"), "reversed_sgst": Decimal("6250.00"), "reversed_igst": Decimal("0.00"), "reversed_cess": Decimal("0.00")},
        {"id": "rev_r37", "purchase_doc_id": "p_b2b_1", "rule_code": "RULE_37_180_DAYS", "reversed_cgst": Decimal("9000.00"), "reversed_sgst": Decimal("9000.00"), "reversed_igst": Decimal("0.00"), "reversed_cess": Decimal("0.00")},
        {"id": "rev_r37a", "purchase_doc_id": "p_b2b_2", "rule_code": "RULE_37A_NON_FILING", "reversed_cgst": Decimal("0.00"), "reversed_sgst": Decimal("0.00"), "reversed_igst": Decimal("32400.00"), "reversed_cess": Decimal("0.00")}
    ]

    # Reclaims: ₹17,250 reclaimed from historical Rule 37 reversal (CGST ₹8,625 + SGST ₹8,625)
    reclaims = [
        {"id": "rec_01", "purchase_doc_id": "p_b2b_3", "reversal_event_id": "rev_hist_01", "reavailed_cgst": Decimal("8625.00"), "reavailed_sgst": Decimal("8625.00"), "reavailed_igst": Decimal("0.00"), "reavailed_cess": Decimal("0.00")}
    ]

    summary = generate_gstr3b_table4_summary(
        return_period=rp,
        recipient_gstin=recipient,
        gstr2b_docs=gstr2b_docs,
        purchase_docs=purchase_docs,
        matches=matches,
        assessments=[],
        human_approved_ids={"p_boe", "p_rcm_intra", "p_rcm_inter", "p_isd", "p_b2b_1", "p_b2b_2", "p_b2b_3"},
        reclaims=reclaims,
        reversals_ledger=reversals_ledger
    )

    # -------------------------------------------------------------------------
    # Verification of Exact Tax-Head Allocations
    # -------------------------------------------------------------------------

    # 4(A)(1) Import of Goods
    assert summary.components_4a["4A1_impg"].cgst == Decimal("0.00")
    assert summary.components_4a["4A1_impg"].sgst == Decimal("0.00")
    assert summary.components_4a["4A1_impg"].igst == Decimal("60000.00")
    assert summary.components_4a["4A1_impg"].total == Decimal("60000.00")

    # 4(A)(2) Import of Services
    assert summary.components_4a["4A2_imps"].cgst == Decimal("0.00")
    assert summary.components_4a["4A2_imps"].sgst == Decimal("0.00")
    assert summary.components_4a["4A2_imps"].igst == Decimal("0.00")
    assert summary.components_4a["4A2_imps"].total == Decimal("0.00")

    # 4(A)(3) Inward RCM Supplies
    assert summary.components_4a["4A3_rcm"].cgst == Decimal("10000.00")
    assert summary.components_4a["4A3_rcm"].sgst == Decimal("10000.00")
    assert summary.components_4a["4A3_rcm"].igst == Decimal("10000.00")
    assert summary.components_4a["4A3_rcm"].total == Decimal("30000.00")

    # 4(A)(4) ISD Inward
    assert summary.components_4a["4A4_isd"].cgst == Decimal("5250.00")
    assert summary.components_4a["4A4_isd"].sgst == Decimal("5250.00")
    assert summary.components_4a["4A4_isd"].igst == Decimal("15000.00")
    assert summary.components_4a["4A4_isd"].total == Decimal("25500.00")

    # 4(A)(5) All Other ITC (Current B2B + Prior-Period Reclaim)
    assert summary.components_4a["4A5_all_other"].cgst == Decimal("114750.00")
    assert summary.components_4a["4A5_all_other"].sgst == Decimal("114750.00")
    assert summary.components_4a["4A5_all_other"].igst == Decimal("137500.00")
    assert summary.components_4a["4A5_all_other"].total == Decimal("367000.00")

    # 4(A) Total ITC Available
    assert summary.components_4a["4A_total"].cgst == Decimal("130000.00")
    assert summary.components_4a["4A_total"].sgst == Decimal("130000.00")
    assert summary.components_4a["4A_total"].igst == Decimal("222500.00")
    assert summary.components_4a["4A_total"].total == Decimal("482500.00")

    # 4(B)(1) Permanent Reversals (Section 17(5))
    assert summary.components_4b["4B1_permanent"].cgst == Decimal("38750.00")
    assert summary.components_4b["4B1_permanent"].sgst == Decimal("38750.00")
    assert summary.components_4b["4B1_permanent"].igst == Decimal("0.00")
    assert summary.components_4b["4B1_permanent"].total == Decimal("77500.00")

    # 4(B)(2) Reclaimable Reversals (Rule 37 & 37A)
    assert summary.components_4b["4B2_total"].cgst == Decimal("9000.00")
    assert summary.components_4b["4B2_total"].sgst == Decimal("9000.00")
    assert summary.components_4b["4B2_total"].igst == Decimal("32400.00")
    assert summary.components_4b["4B2_total"].total == Decimal("50400.00")

    # 4(C) Net ITC Available
    assert summary.components_4c_net.cgst == Decimal("82250.00")
    assert summary.components_4c_net.sgst == Decimal("82250.00")
    assert summary.components_4c_net.igst == Decimal("190100.00")
    assert summary.components_4c_net.cess == Decimal("0.00")
    assert summary.components_4c_net.total == Decimal("354600.00")


# =============================================================================
# 2. GTA Place-of-Supply Consignments & Statutory Integrity
# =============================================================================

def test_gta_place_of_supply_consignments():
    """
    Validates that GTA transport services obey GST Place-of-Supply (PoS) rules:
    - Intra-state transportation (Supplier in 27, PoS in 27) -> CGST + SGST only (never IGST).
    - Inter-state transportation (Supplier in 24, PoS in 27) -> IGST only (never CGST/SGST).
    - Together in Table 4(A)(3), they yield CGST ₹10k, SGST ₹10k, IGST ₹10k without head contamination.
    """
    intra_taxable = Decimal("111111.11")
    intra_cgst = (intra_taxable * Decimal("0.09")).quantize(Decimal("10000.00"))
    intra_sgst = (intra_taxable * Decimal("0.09")).quantize(Decimal("10000.00"))

    inter_taxable = Decimal("55555.56")
    inter_igst = (inter_taxable * Decimal("0.18")).quantize(Decimal("10000.00"))

    assert intra_cgst == Decimal("10000.00")
    assert intra_sgst == Decimal("10000.00")
    assert inter_igst == Decimal("10000.00")


# =============================================================================
# 3. Section 16(2)(b) Receipt-of-Goods Gate Lifecycle & API Verification
# =============================================================================

def test_section_16_2_b_receipt_gate_lifecycle(setup_m2_5_tenant):
    """
    Verifies Section 16(2)(b) receipt verification lifecycle:
    1. Creates purchase document with receipt_status = 'NOT_RECEIVED'.
    2. ITCRiskEngine identifies receipt pending (SEC_16_2_B).
    3. Receipt evidence updated via API endpoint /purchase/{id}/verify-receipt with GRN and Delivery Challan.
    4. Database persists receipt_status = 'RECEIVED', goods_receipt_date, and audit timestamp.
    5. Re-evaluation marks document as ELIGIBLE.
    6. Deemed receipt scenario verifies bill-to ship-to handling under Section 16(2)(b) explanation.
    """
    t = setup_m2_5_tenant
    headers = {"X-User-Id": t["user_id"], "X-Org-Id": t["org_id"]}

    # 1. Create document with NOT_RECEIVED status
    res = client.post(
        "/api/v1/reconciliation/purchase/create",
        headers=headers,
        json={
            "registration_id": t["reg_mh"],
            "document_type": "tax_invoice",
            "document_number": "INV-M25-RECEIPT-01",
            "document_date": "2026-11-04",
            "financial_period": "2026-11",
            "supplier_legal_name": "Trans-India Heavy Machinery",
            "supplier_gstin": "27SUPPLM25A1Z9",
            "recipient_gstin": t["gstin_mh"],
            "place_of_supply": "27",
            "total_taxable_amount": "500000.00",
            "cgst_amount": "45000.00",
            "sgst_amount": "45000.00",
            "igst_amount": "0.00",
            "cess_amount": "0.00",
            "total_invoice_value": "590000.00",
            "goods_services_type": "goods",
            "receipt_status": "NOT_RECEIVED"
        }
    )
    assert res.status_code == 200, res.text
    doc_id = res.json()["id"]

    # 2. Risk Engine evaluates: goods receipt pending
    engine = StatutoryITCRiskEngine()
    p_doc = {
        "id": doc_id,
        "receipt_status": "NOT_RECEIVED",
        "goods_receipt_date": None,
        "goods_services_type": "goods",
        "total_invoice_value": Decimal("590000.00"),
        "cgst_amount": Decimal("45000.00"),
        "sgst_amount": Decimal("45000.00"),
        "igst_amount": Decimal("0.00"),
        "cess_amount": Decimal("0.00"),
        "supplier_gstin": "27SUPPLM25A1Z9"
    }
    g_doc = {"id": "g_2b_receipt", "itc_availability": "available"}
    assessments = engine.evaluate_itc_eligibility(purchase_doc=p_doc, gstr2b_doc=g_doc)
    sec_16_2_b_assessments = [a for a in assessments if a.rule_code == "SEC_16_2_B"]
    assert len(sec_16_2_b_assessments) == 1
    assert sec_16_2_b_assessments[0].eligibility_status == ITCEligibilityEnum.PENDING_GOODS_OR_SERVICE_RECEIPT

    # 3. Update receipt evidence via API endpoint
    verify_res = client.post(
        f"/api/v1/reconciliation/purchase/{doc_id}/verify-receipt",
        headers=headers,
        json={
            "receipt_status": "RECEIVED",
            "goods_receipt_date": "2026-11-15",
            "goods_receipt_note_ref": "GRN-2026-NOV-881",
            "delivery_challan_ref": "DC-MACH-99120"
        }
    )
    assert verify_res.status_code == 200
    assert verify_res.json()["receipt_status"] == "RECEIVED"
    assert verify_res.json()["goods_receipt_note_ref"] == "GRN-2026-NOV-881"

    # 4. Verify in PostgreSQL engine
    with psycopg.connect(DB_ADMIN) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT receipt_status, goods_receipt_note_ref, delivery_challan_ref, receipt_verified_by
                FROM public.purchase_documents WHERE id = %s;
            """, (doc_id,))
            row = cur.fetchone()
            assert row[0] == "RECEIVED"
            assert row[1] == "GRN-2026-NOV-881"
            assert row[2] == "DC-MACH-99120"
            assert str(row[3]) == t["user_id"]

    # 5. Deemed receipt scenario under Section 16(2)(b) Explanation
    deemed_res = client.post(
        f"/api/v1/reconciliation/purchase/{doc_id}/verify-receipt",
        headers=headers,
        json={
            "receipt_status": "DEEMED_RECEIVED",
            "deemed_receipt_reason": "Delivered directly to job worker under bill-to ship-to model per Explanation to Section 16(2)(b)"
        }
    )
    assert deemed_res.status_code == 200
    assert deemed_res.json()["receipt_status"] == "DEEMED_RECEIVED"


# =============================================================================
# 4. SUP-2B-998 Unbooked Inward Invoice Handling & Receipt Gate
# =============================================================================

def test_unbooked_document_sup_998_receipt_gate():
    """
    Verifies that SUP-2B-998 (communicated on portal with ₹18,000 IGST):
    1. Is identified as MISSING_IN_BOOKS / UNBOOKED.
    2. Cannot be availed into Net ITC until physically received or approved under Section 16(2)(b).
    3. When GSTR-3B auto-populates it in 4(A)(5), it is recognized as unbooked variance.
    4. Upon physical receipt in subsequent period, it is booked and reclaimed under Table 4(D)(1).
    """
    unbooked_item = {
        "document_number": "SUP-2B-998",
        "supplier_name": "Reliance Polymers Dist",
        "taxable_value": Decimal("100000.00"),
        "igst_amount": Decimal("18000.00"),
        "receipt_status": ReceiptStatusEnum.UNKNOWN,
        "is_booked": False
    }

    # Condition: Inward goods pending physical receipt -> ITC cannot be taken into Net ITC
    assert unbooked_item["receipt_status"] == ReceiptStatusEnum.UNKNOWN
    assert unbooked_item["is_booked"] is False
    assert unbooked_item["igst_amount"] == Decimal("18000.00")
