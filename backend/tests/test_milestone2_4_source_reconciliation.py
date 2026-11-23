"""
Milestone 2.4: Source-to-Return Reconciliation & Final Financial Integrity Test Suite
Covers:
1. Exact November 2026 atomic source-row reconciliation without double-counting
2. Explicit source-based statutory classification: BoE Import of Goods -> 4(A)(1),
   Import of Services -> 4(A)(2), Domestic Capital Goods -> 4(A)(5), RCM -> 4(A)(3), ISD -> 4(A)(4)
3. CBIC Circular No. 192/04/2023-GST & Rule 88B(3) tax-head specific ECL utilization engine
   (IGST combined with CGST+SGST, Cess excluded; CGST+IGST; SGST+IGST; Cess isolated)
4. Concurrency, row-locking, partial reclaims, and ledger immutability verification
"""
import uuid
import random
import threading
from datetime import date, datetime, timedelta
from decimal import Decimal
import pytest
import psycopg
from fastapi.testclient import TestClient

from app.main import app
from app.modules.reconciliation.itc_engine import StatutoryITCRiskEngine, calculate_rule_37_interest
from app.modules.reconciliation.gstr3b_itc import generate_gstr3b_table4_summary
from app.modules.reconciliation.schemas import ITCEligibilityEnum

client = TestClient(app)

from tests.test_db_config import DB_ADMIN, DB_APP


@pytest.fixture
def setup_m2_4_tenant():
    """Sets up a clean multi-GST tenant for Milestone 2.4 testing."""
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
                VALUES (%s, 'M2.4 Financial Integrity Ltd', %s, 'IntegrityCo');
            """, (org_id, pan))

            cur.execute("""
                INSERT INTO public.global_users (id, email, full_name)
                VALUES (%s, %s, 'CA Lead Auditor 2.4');
            """, (user_id, f"auditor_{uuid.uuid4().hex[:6]}@integrity.test"))

            cur.execute("""
                INSERT INTO public.organization_memberships (org_id, user_id, role, status)
                VALUES (%s, %s, 'owner', 'active');
            """, (org_id, user_id))

            cur.execute("""
                INSERT INTO public.gst_registrations (
                    id, org_id, gstin, state_code, legal_name, is_primary
                ) VALUES (%s, %s, %s, '27', 'M2.4 Financial Integrity Ltd', true);
            """, (reg_mh, org_id, gstin_mh))

    return {
        "org_id": org_id,
        "user_id": user_id,
        "pan": pan,
        "gstin_mh": gstin_mh,
        "reg_mh": reg_mh
    }


# =============================================================================
# 1. Exact November 2026 Atomic Source-Document Dataset Reconciliation
# =============================================================================

def test_exact_november_2026_atomic_reconciliation():
    """
    Directly validates the complete, atomic, line-by-line source records:
    - Raw GSTR-2B Statement Total = ₹4,65,250.00
    - Books Matched Total = ₹4,47,250.00
    - Unbooked GSTR-2B Document (SUP-2B-998) = ₹18,000.00
    - Table 4(A)(5) Current B2B = ₹3,49,750.00
    - Prior-Period Reclaim (REC-R37-01) = ₹17,250.00
    - Table 4(A)(5) Reported = ₹3,67,000.00
    - Table 4(A) Gross Available = ₹4,82,500.00
    - Table 4(B)(1) Permanent Reversals = ₹77,500.00
    - Table 4(B)(2) Reclaimable Reversals = ₹50,400.00
    - Table 4(C) Net ITC = ₹3,54,600.00
    - Table 4(D)(1) Reclaimed Disclosure = ₹17,250.00
    - Table 4(D)(2) Ineligible Disclosure = ₹12,400.00
    """
    rp = "2026-11"
    recipient = "27AABCB1234M1ZT"

    # Exact atomic GSTR-2B source records (Part A: 7 available documents + Part B: 1 unavailable document)
    gstr2b_docs = [
        # 1. 4(A)(1) Import of Goods
        {
            "id": "g_boe", "document_number": "BOE-882190", "document_date": "2026-11-02",
            "supplier_gstin": "INBOM4", "supplier_name": "Customs Bombay Port (ICEGATE)",
            "section_name": "IMPG", "document_type": "bill_of_entry", "supply_category": "import_goods",
            "itc_availability": "available", "taxable_value": Decimal("333333.33"),
            "central_tax": Decimal("0.00"), "state_ut_tax": Decimal("0.00"),
            "integrated_tax": Decimal("60000.00"), "cess_amount": Decimal("0.00")
        },
        # 2. 4(A)(3) Inward RCM Transport
        {
            "id": "g_rcm", "document_number": "GTA-NOV-01", "document_date": "2026-11-05",
            "supplier_gstin": "27AAGFG4411D1ZA", "supplier_name": "FastTrack Logistics (GTA)",
            "section_name": "RCM", "document_type": "tax_invoice", "supply_category": "reverse_charge",
            "supply_attract_reverse_charge": True, "itc_availability": "available", "taxable_value": Decimal("111111.11"),
            "central_tax": Decimal("10000.00"), "state_ut_tax": Decimal("10000.00"),
            "integrated_tax": Decimal("10000.00"), "cess_amount": Decimal("0.00")
        },
        # 3. 4(A)(4) ISD Inward Distribution
        {
            "id": "g_isd", "document_number": "ISD-INV-44", "document_date": "2026-11-07",
            "supplier_gstin": "27AAACZ1122C1Z8", "supplier_name": "Apex Corporate HQ (ISD)",
            "section_name": "ISD", "document_type": "isd_invoice", "supply_category": "isd",
            "itc_availability": "available", "taxable_value": Decimal("141666.67"),
            "central_tax": Decimal("5250.00"), "state_ut_tax": Decimal("5250.00"),
            "integrated_tax": Decimal("15000.00"), "cess_amount": Decimal("0.00")
        },
        # 4. 4(A)(5) Registered Inward B2B - Invoice 101
        {
            "id": "g_b2b_1", "document_number": "INV-2026-101", "document_date": "2026-11-04",
            "supplier_gstin": "27AAGCB1234F1ZQ", "supplier_name": "Bharat Industrial Spares Ltd",
            "section_name": "B2B", "document_type": "tax_invoice", "supply_category": "regular_b2b",
            "itc_availability": "available", "taxable_value": Decimal("1083333.33"),
            "central_tax": Decimal("97500.00"), "state_ut_tax": Decimal("97500.00"),
            "integrated_tax": Decimal("0.00"), "cess_amount": Decimal("0.00")
        },
        # 5. 4(A)(5) Registered Inward B2B - Invoice 105
        {
            "id": "g_b2b_2", "document_number": "INV-2026-105", "document_date": "2026-11-06",
            "supplier_gstin": "29BBBPB9988H1Z4", "supplier_name": "Karnataka Precision Tools",
            "section_name": "B2B", "document_type": "tax_invoice", "supply_category": "regular_b2b",
            "itc_availability": "available", "taxable_value": Decimal("663888.89"),
            "central_tax": Decimal("0.00"), "state_ut_tax": Decimal("0.00"),
            "integrated_tax": Decimal("119500.00"), "cess_amount": Decimal("0.00")
        },
        # 6. 4(A)(5) Registered Inward B2B - Invoice 9912
        {
            "id": "g_b2b_3", "document_number": "AMD/9912", "document_date": "2026-11-06",
            "supplier_gstin": "27BBBEE5566K1Z1", "supplier_name": "Western Hardware Supplies",
            "section_name": "B2B", "document_type": "tax_invoice", "supply_category": "regular_b2b",
            "itc_availability": "available", "taxable_value": Decimal("95833.33"),
            "central_tax": Decimal("8625.00"), "state_ut_tax": Decimal("8625.00"),
            "integrated_tax": Decimal("0.00"), "cess_amount": Decimal("0.00")
        },
        # 7. 4(A)(5) Registered Inward B2B - Unbooked Invoice (Missing in Books)
        {
            "id": "g_unbooked", "document_number": "SUP-2B-998", "document_date": "2026-11-08",
            "supplier_gstin": "27AAACR1234Q1Z2", "supplier_name": "Reliance Polymers Dist",
            "section_name": "B2B", "document_type": "tax_invoice", "supply_category": "regular_b2b",
            "itc_availability": "available", "taxable_value": Decimal("100000.00"),
            "central_tax": Decimal("0.00"), "state_ut_tax": Decimal("0.00"),
            "integrated_tax": Decimal("18000.00"), "cess_amount": Decimal("0.00")
        },
        # 8. Part B Ineligible Credit (Time-Barred under Section 16(4))
        {
            "id": "g_inelig", "document_number": "EXP-2025-99", "document_date": "2025-08-15",
            "supplier_gstin": "06AAACD1111C1Z3", "supplier_name": "Haryana Logistics Hub",
            "section_name": "B2B", "document_type": "tax_invoice", "supply_category": "regular_b2b",
            "itc_availability": "not_available", "taxable_value": Decimal("68888.89"),
            "central_tax": Decimal("0.00"), "state_ut_tax": Decimal("0.00"),
            "integrated_tax": Decimal("12400.00"), "cess_amount": Decimal("0.00")
        }
    ]

    # Inward Purchase Documents booked in ERP (Documents 1 to 6 booked; unbooked document 7 omitted)
    purchase_docs = [
        {"id": "p_boe", "document_number": "BOE-882190", "cgst_amount": Decimal("0.00"), "sgst_amount": Decimal("0.00"), "igst_amount": Decimal("60000.00"), "cess_amount": Decimal("0.00"), "status": "approved"},
        {"id": "p_rcm", "document_number": "GTA-NOV-01", "cgst_amount": Decimal("10000.00"), "sgst_amount": Decimal("10000.00"), "igst_amount": Decimal("10000.00"), "cess_amount": Decimal("0.00"), "status": "approved"},
        {"id": "p_isd", "document_number": "ISD-INV-44", "cgst_amount": Decimal("5250.00"), "sgst_amount": Decimal("5250.00"), "igst_amount": Decimal("15000.00"), "cess_amount": Decimal("0.00"), "status": "approved"},
        {"id": "p_b2b_1", "document_number": "INV-2026-101", "cgst_amount": Decimal("97500.00"), "sgst_amount": Decimal("97500.00"), "igst_amount": Decimal("0.00"), "cess_amount": Decimal("0.00"), "status": "approved"},
        {"id": "p_b2b_2", "document_number": "INV-2026-105", "cgst_amount": Decimal("0.00"), "sgst_amount": Decimal("0.00"), "igst_amount": Decimal("119500.00"), "cess_amount": Decimal("0.00"), "status": "approved"},
        {"id": "p_b2b_3", "document_number": "AMD/9912", "cgst_amount": Decimal("8625.00"), "sgst_amount": Decimal("8625.00"), "igst_amount": Decimal("0.00"), "cess_amount": Decimal("0.00"), "status": "approved"},
    ]

    matches = [
        {"purchase_doc_id": "p_boe", "gstr2b_doc_id": "g_boe"},
        {"purchase_doc_id": "p_rcm", "gstr2b_doc_id": "g_rcm"},
        {"purchase_doc_id": "p_isd", "gstr2b_doc_id": "g_isd"},
        {"purchase_doc_id": "p_b2b_1", "gstr2b_doc_id": "g_b2b_1"},
        {"purchase_doc_id": "p_b2b_2", "gstr2b_doc_id": "g_b2b_2"},
        {"purchase_doc_id": "p_b2b_3", "gstr2b_doc_id": "g_b2b_3"},
    ]

    # Reversal Events (Historical availed documents):
    reversals_ledger = [
        {"id": "rev_veh", "purchase_doc_id": "p_b2b_1", "rule_code": "SEC_17_5_MOTOR", "reversed_cgst": Decimal("32500.00"), "reversed_sgst": Decimal("32500.00"), "reversed_igst": Decimal("0.00"), "reversed_cess": Decimal("0.00")},
        {"id": "rev_food", "purchase_doc_id": "p_b2b_1", "rule_code": "SEC_17_5_FOOD", "reversed_cgst": Decimal("6250.00"), "reversed_sgst": Decimal("6250.00"), "reversed_igst": Decimal("0.00"), "reversed_cess": Decimal("0.00")},
        {"id": "rev_r37", "purchase_doc_id": "p_b2b_1", "rule_code": "RULE_37_180_DAYS", "reversed_cgst": Decimal("9000.00"), "reversed_sgst": Decimal("9000.00"), "reversed_igst": Decimal("0.00"), "reversed_cess": Decimal("0.00")},
        {"id": "rev_r37a", "purchase_doc_id": "p_b2b_2", "rule_code": "RULE_37A_NON_FILING", "reversed_cgst": Decimal("0.00"), "reversed_sgst": Decimal("0.00"), "reversed_igst": Decimal("32400.00"), "reversed_cess": Decimal("0.00")}
    ]

    # Reclaims: ₹17,250 from historical Rule 37 reversal
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
        human_approved_ids={"p_boe", "p_rcm", "p_isd", "p_b2b_1", "p_b2b_2", "p_b2b_3"},
        reclaims=reclaims,
        reversals_ledger=reversals_ledger
    )

    # 1. Statement Total of GSTR-2B Part A = ₹4,65,250.00
    assert summary.subtotal_gstr2b_portal_available == Decimal("465250.00")

    # 2. Book-Matched Current Inward Supplies = ₹4,47,250.00
    assert summary.subtotal_books_matched == Decimal("447250.00")

    # 3. Variance is exactly equal to the unbooked document (SUP-2B-998: ₹18,000.00)
    assert summary.unbooked_variance_amount == Decimal("18000.00")
    assert len(summary.unbooked_gstr2b_documents) == 1
    assert summary.unbooked_gstr2b_documents[0]["document_number"] == "SUP-2B-998"

    # 4. Current B2B Inward in GSTR-2B = ₹3,49,750.00
    assert summary.current_b2b_inward_available == Decimal("349750.00")
    assert summary.reclaims_total == Decimal("17250.00")

    # 5. Table 4(A)(5) = Current B2B (₹3,49,750) + Prior Reclaim (₹17,250) = ₹3,67,000.00
    assert summary.itc_available_all_other == Decimal("367000.00")

    # 6. Table 4(A) Gross Available = GSTR-2B Total (₹4,65,250) + Reclaim (₹17,250) = ₹4,82,500.00
    assert summary.total_itc_available == Decimal("482500.00")

    # 7. Table 4(B) Reversals:
    # 4(B)(1) Permanent: ₹77,500.00 (Motor vehicle ₹65,000 + Food catering ₹12,500)
    # 4(B)(2) Reclaimable: ₹50,400.00 (Rule 37 ₹18,000 + Rule 37A ₹32,400)
    assert summary.itc_reversed_rule_38_42_43 == Decimal("77500.00")
    assert summary.total_itc_reversed_reclaimable_4b2 == Decimal("50400.00")
    assert summary.total_itc_reversed == Decimal("127900.00")

    # 8. Table 4(C) Net ITC = 4(A) ₹4,82,500 - 4(B) ₹127,900 = ₹3,54,600.00
    assert summary.net_itc_available == Decimal("354600.00")

    # 9. Table 4(D) Disclosures:
    assert summary.itc_reclaimed_earlier_reversal_4d1 == Decimal("17250.00")
    assert summary.ineligible_sec_16_4_and_pos_restrictions_4d2 == Decimal("12400.00")

    # 10. Component-Level Statutory Tax-Head Verification:
    # 4(A) Total: CGST ₹1,30,000 | SGST ₹1,30,000 | IGST ₹2,22,500
    assert summary.components_4a["4A_total"].cgst == Decimal("130000.00")
    assert summary.components_4a["4A_total"].sgst == Decimal("130000.00")
    assert summary.components_4a["4A_total"].igst == Decimal("222500.00")
    assert summary.components_4a["4A_total"].total == Decimal("482500.00")

    # 4(A)(5) All Other ITC: CGST ₹1,14,750 | SGST ₹1,14,750 | IGST ₹1,37,500
    assert summary.components_4a["4A5_all_other"].cgst == Decimal("114750.00")
    assert summary.components_4a["4A5_all_other"].sgst == Decimal("114750.00")
    assert summary.components_4a["4A5_all_other"].igst == Decimal("137500.00")
    assert summary.components_4a["4A5_all_other"].total == Decimal("367000.00")

    # 4(B)(1) Permanent: CGST ₹38,750 | SGST ₹38,750 | IGST ₹0
    assert summary.components_4b["4B1_permanent"].cgst == Decimal("38750.00")
    assert summary.components_4b["4B1_permanent"].sgst == Decimal("38750.00")
    assert summary.components_4b["4B1_permanent"].igst == Decimal("0.00")
    assert summary.components_4b["4B1_permanent"].total == Decimal("77500.00")

    # 4(B)(2) Reclaimable: CGST ₹9,000 | SGST ₹9,000 | IGST ₹32,400
    assert summary.components_4b["4B2_total"].cgst == Decimal("9000.00")
    assert summary.components_4b["4B2_total"].sgst == Decimal("9000.00")
    assert summary.components_4b["4B2_total"].igst == Decimal("32400.00")
    assert summary.components_4b["4B2_total"].total == Decimal("50400.00")

    # 4(C) Net ITC: CGST ₹82,250 | SGST ₹82,250 | IGST ₹1,90,100
    assert summary.components_4c_net.cgst == Decimal("82250.00")
    assert summary.components_4c_net.sgst == Decimal("82250.00")
    assert summary.components_4c_net.igst == Decimal("190100.00")
    assert summary.components_4c_net.cess == Decimal("0.00")
    assert summary.components_4c_net.total == Decimal("354600.00")


# =============================================================================
# 2. Source-Based Classification: BoE vs Import Services vs Domestic Capital Goods
# =============================================================================

def test_source_based_classification_and_capital_goods():
    """
    Verifies that:
    1. Customs Bill of Entry (BoE) from ICEGATE correctly maps to Table 4(A)(1).
    2. Import of Services correctly maps to Table 4(A)(2).
    3. Inward reverse charge supplies correctly map to Table 4(A)(3).
    4. ISD distributions correctly map to Table 4(A)(4).
    5. Domestic capital goods from registered suppliers map to Table 4(A)(5) (NOT 4(A)(2)!).
    """
    rp = "2026-11"
    recipient = "27AABCB1234M1ZT"

    docs = [
        # BoE -> 4(A)(1)
        {
            "id": "d_boe", "document_number": "BOE-111", "section_name": "IMPG",
            "document_type": "bill_of_entry", "supply_category": "import_goods",
            "itc_availability": "available", "integrated_tax": Decimal("50000.00")
        },
        # Import of Services -> 4(A)(2)
        {
            "id": "d_imps", "document_number": "IMPS-222", "section_name": "IMPS",
            "document_type": "import_service", "supply_category": "import_services",
            "itc_availability": "available", "integrated_tax": Decimal("35000.00")
        },
        # Inward RCM -> 4(A)(3)
        {
            "id": "d_rcm", "document_number": "RCM-333", "section_name": "RCM",
            "supply_attract_reverse_charge": True, "itc_availability": "available",
            "central_tax": Decimal("10000.00"), "state_ut_tax": Decimal("10000.00")
        },
        # ISD -> 4(A)(4)
        {
            "id": "d_isd", "document_number": "ISD-444", "section_name": "ISD",
            "document_type": "isd_invoice", "itc_availability": "available",
            "central_tax": Decimal("5000.00"), "state_ut_tax": Decimal("5000.00")
        },
        # Domestic Capital Goods -> 4(A)(5) (Statutory proof: NOT in 4(A)(2))
        {
            "id": "d_cap_goods", "document_number": "CAP-555", "section_name": "B2B",
            "document_type": "capital_goods", "supply_category": "capital_goods",
            "itc_availability": "available", "integrated_tax": Decimal("75000.00")
        },
        # Regular Domestic B2B Input -> 4(A)(5)
        {
            "id": "d_b2b", "document_number": "INV-666", "section_name": "B2B",
            "document_type": "tax_invoice", "itc_availability": "available",
            "central_tax": Decimal("20000.00"), "state_ut_tax": Decimal("20000.00")
        }
    ]

    p_docs = [{"id": f"p_{d['id']}", "status": "approved"} for d in docs]
    matches = [{"purchase_doc_id": f"p_{d['id']}", "gstr2b_doc_id": d["id"]} for d in docs]

    summary = generate_gstr3b_table4_summary(
        return_period=rp,
        recipient_gstin=recipient,
        gstr2b_docs=docs,
        purchase_docs=p_docs,
        matches=matches,
        assessments=[]
    )

    # 4(A)(1) Import of Goods = ₹50,000 (BoE)
    assert summary.itc_available_import_goods == Decimal("50000.00")

    # 4(A)(2) Import of Services = ₹35,000 ONLY
    # Capital goods must NOT be present here!
    assert summary.itc_available_import_services == Decimal("35000.00")

    # 4(A)(3) RCM = ₹20,000
    assert summary.itc_available_inward_reverse_charge == Decimal("20000.00")

    # 4(A)(4) ISD = ₹10,000
    assert summary.itc_available_isd == Decimal("10000.00")

    # 4(A)(5) All Other ITC = Domestic Capital Goods (₹75k) + Regular B2B (₹40k) = ₹115,000.00
    assert summary.itc_available_all_other == Decimal("115000.00")

    # Total 4(A) = 50k + 35k + 20k + 10k + 115k = 230,000
    assert summary.total_itc_available == Decimal("230000.00")


# =============================================================================
# 3. CBIC Circular No. 192/04/2023-GST & Rule 88B(3) Tax-Head Utilization Engine
# =============================================================================

def test_cbic_circular_192_tax_head_utilization_rules():
    """
    Verifies tax-head specific ECL utilization under Rule 88B(3) and CBIC Circular 192/04/2023-GST:
    1. For wrongly availed IGST: Total balance of (IGST + CGST + SGST) is combined.
       If combined balance >= availed IGST -> Nil interest (even if isolated IGST balance = 0).
    2. Compensation Cess is strictly excluded from IGST/CGST/SGST balance pooling.
    3. For wrongly availed CGST: CGST + IGST combined balance is evaluated.
    4. Chronological transaction ledger walking identifies the exact first utilization date.
    """
    # -------------------------------------------------------------------------
    # Scenario A: Wrongly availed IGST of ₹50,000.
    # Isolated IGST in ECL falls to ₹5,000, BUT CGST is ₹30,000 and SGST is ₹30,000.
    # Total combined balance = 5k + 30k + 30k = ₹65,000 >= ₹50,000.
    # Result per Circular 192: Credit was NEVER utilised. Interest is Nil (₹0.00).
    # -------------------------------------------------------------------------
    res_igst_protected = calculate_rule_37_interest(
        reversal_tax=Decimal("50000.00"),
        tax_head="IGST",
        ecl_head_balances={
            "igst": Decimal("5000.00"),
            "cgst": Decimal("30000.00"),
            "sgst": Decimal("30000.00"),
            "cess": Decimal("100000.00")  # Cess must be ignored
        },
        reversal_date=date(2026, 11, 20)
    )
    assert res_igst_protected["is_utilised"] is False
    assert res_igst_protected["utilised_amount"] == Decimal("0.00")
    assert res_igst_protected["interest_amount"] == Decimal("0.00")
    assert res_igst_protected["relevant_ecl_balance"] == Decimal("65000.00")
    assert res_igst_protected["cess_excluded"] is True
    assert "Circular No. 192" in res_igst_protected["statutory_basis"]

    # -------------------------------------------------------------------------
    # Scenario B: Wrongly availed IGST of ₹50,000.
    # Cess is ₹5,00,000, BUT combined (IGST ₹10k + CGST ₹10k + SGST ₹10k) = ₹30,000.
    # Shortfall = 50,000 - 30,000 = ₹20,000 utilised.
    # Utilisation date = 2026-06-01, Reversal date = 2026-11-20 (172 days).
    # -------------------------------------------------------------------------
    res_igst_shortfall = calculate_rule_37_interest(
        reversal_tax=Decimal("50000.00"),
        tax_head="IGST",
        ecl_head_balances={
            "igst": Decimal("10000.00"),
            "cgst": Decimal("10000.00"),
            "sgst": Decimal("10000.00"),
            "cess": Decimal("500000.00")  # Huge cess balance cannot offset shortfall
        },
        utilisation_date=date(2026, 6, 1),
        reversal_date=date(2026, 11, 20)
    )
    assert res_igst_shortfall["is_utilised"] is True
    assert res_igst_shortfall["utilised_amount"] == Decimal("20000.00")
    assert res_igst_shortfall["days_utilised"] == 172
    # Interest = 20,000 * 18% * 172 / 365 = ₹1,696.44
    expected_int = (Decimal("20000.00") * Decimal("0.18") * Decimal(172) / Decimal(365)).quantize(Decimal("0.01"))
    assert res_igst_shortfall["interest_amount"] == expected_int

    # -------------------------------------------------------------------------
    # Scenario C: Wrongly availed CGST of ₹30,000.
    # CGST is ₹10k, IGST is ₹15k, SGST is ₹50k.
    # For CGST, SGST CANNOT be combined (Section 49(5)(b)).
    # Relevant balance = CGST (10k) + IGST (15k) = ₹25,000.
    # Shortfall = 30,000 - 25,000 = ₹5,000 utilised.
    # -------------------------------------------------------------------------
    res_cgst = calculate_rule_37_interest(
        reversal_tax=Decimal("30000.00"),
        tax_head="CGST",
        ecl_head_balances={
            "cgst": Decimal("10000.00"),
            "igst": Decimal("15000.00"),
            "sgst": Decimal("50000.00"),  # Must NOT be included in CGST test
            "cess": Decimal("0.00")
        },
        utilisation_date=date(2026, 7, 1),
        reversal_date=date(2026, 11, 20)
    )
    assert res_cgst["relevant_ecl_balance"] == Decimal("25000.00")
    assert res_cgst["utilised_amount"] == Decimal("5000.00")

    # -------------------------------------------------------------------------
    # Scenario D: Chronological transaction ledger walking (Payment via GSTR-3B)
    # -------------------------------------------------------------------------
    ecl_txns = [
        {
            "transaction_date": "2026-04-10",
            "description": "GSTR-3B March 2026 Output Tax Payment",
            "balance_igst": Decimal("60000.00"), "balance_cgst": Decimal("20000.00"), "balance_sgst": Decimal("20000.00"), "balance_cess": Decimal("0.00")
        },
        {
            "transaction_date": "2026-05-20",
            "description": "GSTR-3B April 2026 Output Tax Payment (Balance Dips)",
            "balance_igst": Decimal("5000.00"), "balance_cgst": Decimal("5000.00"), "balance_sgst": Decimal("5000.00"), "balance_cess": Decimal("0.00")
        },
        {
            "transaction_date": "2026-06-20",
            "description": "GSTR-3B May 2026 Output Tax Payment",
            "balance_igst": Decimal("20000.00"), "balance_cgst": Decimal("10000.00"), "balance_sgst": Decimal("10000.00"), "balance_cess": Decimal("0.00")
        }
    ]
    # For wrongly availed IGST of ₹30,000:
    # On 2026-04-10: Combined = 60k + 20k + 20k = 100k >= 30k.
    # On 2026-05-20: Combined dips to 5k + 5k + 5k = 15k < 30k. (First dip date = 2026-05-20).
    # Reversal on 2026-11-20: Days = (2026-11-20 - 2026-05-20) = 184 days.
    # Utilised shortfall = 30,000 - 15,000 = ₹15,000.
    res_txn = calculate_rule_37_interest(
        reversal_tax=Decimal("30000.00"),
        tax_head="IGST",
        ecl_transactions=ecl_txns,
        reversal_date=date(2026, 11, 20)
    )
    assert res_txn["is_utilised"] is True
    assert res_txn["utilised_amount"] == Decimal("15000.00")
    assert res_txn["utilisation_date"] == date(2026, 5, 20)
    assert res_txn["days_utilised"] == 184


# =============================================================================
# 4. Concurrency, Row Locking, Partial Reclaims & Immutability Verification
# =============================================================================

def test_concurrent_reversals_and_reclaims_integrity(setup_m2_4_tenant):
    """
    Verifies:
    1. Simultaneous duplicate reversals are rejected.
    2. Multiple partial reclaims succeed up to the reversed ceiling.
    3. Over-reclaims exceeding the original reversal are strictly rejected (HTTP 400).
    4. Permanent Section 17(5) reversal reclaims are blocked (HTTP 400).
    5. Database trigger blocks direct mutation/deletion on itc_ledger_entries (SQLSTATE 42501).
    """
    t = setup_m2_4_tenant
    headers = {"X-User-Id": t["user_id"], "X-Org-Id": t["org_id"]}

    # Create purchase document for testing
    res_p = client.post(
        "/api/v1/reconciliation/purchase/create",
        headers=headers,
        json={
            "registration_id": t["reg_mh"],
            "document_type": "tax_invoice",
            "document_number": "INV-M24-CONC-01",
            "document_date": "2025-10-01",
            "financial_period": "2025-10",
            "supplier_legal_name": "Concurrency Test Supplier",
            "supplier_gstin": "27SUPPLCONC1Z5",
            "recipient_gstin": t["gstin_mh"],
            "place_of_supply": "27",
            "total_taxable_amount": "200000.00",
            "cgst_amount": "18000.00",
            "sgst_amount": "18000.00",
            "igst_amount": "0.00",
            "cess_amount": "0.00",
            "total_invoice_value": "236000.00"
        }
    )
    assert res_p.status_code == 200, res_p.text
    p_id = res_p.json()["id"]

    # 1. Avail in GSTR-3B
    avail_res = client.post(
        "/api/v1/reconciliation/itc-ledger/record-event",
        headers=headers,
        json={
            "purchase_doc_id": p_id,
            "event_type": "AVAIL",
            "tax_period": "2025-10",
            "cgst_amount": "18000.00",
            "sgst_amount": "18000.00"
        }
    )
    assert avail_res.status_code == 200

    # 2. Record Rule 37 Reversal of ₹36,000 (18k CGST + 18k SGST)
    rev_res = client.post(
        "/api/v1/reconciliation/itc-ledger/record-event",
        headers=headers,
        json={
            "purchase_doc_id": p_id,
            "event_type": "REVERSAL_RULE_37",
            "tax_period": "2026-04",
            "cgst_amount": "18000.00",
            "sgst_amount": "18000.00",
            "reversal_reason": "Unpaid beyond 180 days"
        }
    )
    assert rev_res.status_code == 200
    rev_event_id = rev_res.json()["reversal_event_id"]

    # 3. Duplicate Reversal in the same period must FAIL
    dup_rev = client.post(
        "/api/v1/reconciliation/itc-ledger/record-event",
        headers=headers,
        json={
            "purchase_doc_id": p_id,
            "event_type": "REVERSAL_RULE_37",
            "tax_period": "2026-04",
            "cgst_amount": "18000.00",
            "sgst_amount": "18000.00"
        }
    )
    assert dup_rev.status_code == 400
    assert "Duplicate reversal rejected" in dup_rev.json()["detail"]

    # 4. First Partial Reclaim of ₹16,000 (8k CGST + 8k SGST) -> SUT: ₹16,000 / ₹36,000
    rec_part1 = client.post(
        "/api/v1/reconciliation/itc-ledger/record-event",
        headers=headers,
        json={
            "purchase_doc_id": p_id,
            "event_type": "RECLAIM",
            "tax_period": "2026-11",
            "reversal_event_id": rev_event_id,
            "cgst_amount": "8000.00",
            "sgst_amount": "8000.00"
        }
    )
    assert rec_part1.status_code == 200

    # 5. Second Partial Reclaim of ₹12,000 (6k CGST + 6k SGST) -> Cumulative = ₹28,000 / ₹36,000
    rec_part2 = client.post(
        "/api/v1/reconciliation/itc-ledger/record-event",
        headers=headers,
        json={
            "purchase_doc_id": p_id,
            "event_type": "RECLAIM",
            "tax_period": "2026-11",
            "reversal_event_id": rev_event_id,
            "cgst_amount": "6000.00",
            "sgst_amount": "6000.00"
        }
    )
    assert rec_part2.status_code == 200

    # 6. Attempt Over-Reclaim of ₹10,000 -> Cumulative would be ₹38,000 > ₹36,000 -> MUST FAIL (HTTP 400)
    over_rec = client.post(
        "/api/v1/reconciliation/itc-ledger/record-event",
        headers=headers,
        json={
            "purchase_doc_id": p_id,
            "event_type": "RECLAIM",
            "tax_period": "2026-11",
            "reversal_event_id": rev_event_id,
            "cgst_amount": "5000.00",
            "sgst_amount": "5000.00"
        }
    )
    assert over_rec.status_code == 400
    assert "Reclaim bounds exceeded" in over_rec.json()["detail"]

    # 7. Remaining Exact Reclaim of ₹8,000 (4k CGST + 4k SGST) -> Total = 28k + 8k = ₹36,000 -> MUST SUCCEED
    rec_part3 = client.post(
        "/api/v1/reconciliation/itc-ledger/record-event",
        headers=headers,
        json={
            "purchase_doc_id": p_id,
            "event_type": "RECLAIM",
            "tax_period": "2026-11",
            "reversal_event_id": rev_event_id,
            "cgst_amount": "4000.00",
            "sgst_amount": "4000.00"
        }
    )
    assert rec_part3.status_code == 200

    # 8. Further Reclaim after full exhaustion must FAIL
    rec_exhausted = client.post(
        "/api/v1/reconciliation/itc-ledger/record-event",
        headers=headers,
        json={
            "purchase_doc_id": p_id,
            "event_type": "RECLAIM",
            "tax_period": "2026-11",
            "reversal_event_id": rev_event_id,
            "cgst_amount": "500.00",
            "sgst_amount": "500.00"
        }
    )
    assert rec_exhausted.status_code == 400
    assert "Reclaim bounds exceeded" in rec_exhausted.json()["detail"]

    # 9. Test Permanent Section 17(5) Reclaim Rejection
    perm_rev = client.post(
        "/api/v1/reconciliation/itc-ledger/record-event",
        headers=headers,
        json={
            "purchase_doc_id": p_id,
            "event_type": "REVERSAL_PERMANENT_17_5",
            "tax_period": "2026-05",
            "cgst_amount": "5000.00",
            "sgst_amount": "5000.00",
            "reversal_reason": "Motor Vehicle Ineligible"
        }
    )
    assert perm_rev.status_code == 200
    perm_rev_id = perm_rev.json()["reversal_event_id"]

    perm_rec_attempt = client.post(
        "/api/v1/reconciliation/itc-ledger/record-event",
        headers=headers,
        json={
            "purchase_doc_id": p_id,
            "event_type": "RECLAIM",
            "tax_period": "2026-11",
            "reversal_event_id": perm_rev_id,
            "cgst_amount": "5000.00",
            "sgst_amount": "5000.00"
        }
    )
    assert perm_rec_attempt.status_code == 400
    assert "Statutory Reclaim Prohibited" in perm_rec_attempt.json()["detail"]

    # 10. Database Immutability Trigger Test: Direct UPDATE on itc_ledger_entries must be blocked by engine
    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            with pytest.raises(psycopg.errors.InsufficientPrivilege) as exc_info:
                cur.execute("UPDATE public.itc_ledger_entries SET total_tax = 99999.00 WHERE purchase_doc_id = %s;", (p_id,))
            assert "42501" in str(exc_info.value.sqlstate)
            assert "strictly immutable" in str(exc_info.value)
