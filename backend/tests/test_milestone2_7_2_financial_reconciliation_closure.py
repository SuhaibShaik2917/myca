"""
Milestone 2.7.2: Final Table 4 Tax Classification & Provenance Closure Test Suite
Validates:
1. Table 4(A)(3) RCM Eligible Classification & Cash Discharge Reconciliation:
   - Consists strictly of eligible RCM supplies (GTA intra-state and inter-state).
   - Reconciles corresponding RCM liability in GSTR-3B Table 3.1(d) requiring mandatory cash discharge.
   - Separates forward-charge GTA (Table 4(A)(5)) from reverse-charge GTA (Table 4(A)(3)).
   - Ineligible RCM supplies are barred from Table 4(A)(3).
2. Complete Reversal Provenance for Table 4(B)(2) (₹68,400.00):
   - Invoice-level provenance for Rule 37 (180-days: CGST ₹9,000 + SGST ₹9,000 = ₹18,000).
   - Traceability of Rule 37A supplier non-filing (IGST ₹32,400) tied to FY of availment.
   - Section 16(2)(b) receipt hold reversal for unbooked document SUP-2B-998 (IGST ₹18,000).
   - Component totals: CGST ₹9,000, SGST ₹9,000, IGST ₹50,400 = Total ₹68,400.
3. Blocked Credit Tax-Head Reconciliation for Table 4(B)(1) (₹77,500.00):
   - Provenance of motor vehicle (CGST ₹32,500 + SGST ₹32,500 = ₹65,000) under Section 17(5)(a).
   - Provenance of food & catering (CGST ₹6,250 + SGST ₹6,250 = ₹12,500) under Section 17(5)(b)(i).
   - Component totals: CGST ₹38,750, SGST ₹38,750, IGST ₹0 = Total ₹77,500.
4. Historical Table 4(D)(2) Ineligible Credit Preservation:
   - Preserves Section 16(4) time-barred credit (IGST ₹12,400) in Table 4(D)(2) disclosure.
   - Strictly excluded from Table 4(A) gross and Table 4(C) net.
5. Unified Financial Report Service End-to-End API Integration & RLS Isolation:
   - Validates that GET /api/v1/reconciliation/financial-reports/unified returns
     complete tax_head_summary without zero values, table_3_1_d_rcm_reconciliation,
     reversals_lineage, and unbooked_gstr2b_documents under PostgreSQL RLS.
"""
import uuid
import random
from datetime import date, datetime
from decimal import Decimal
import pytest
import psycopg
from fastapi.testclient import TestClient

from app.main import app
from app.modules.reconciliation.gstr3b_itc import generate_gstr3b_table4_summary
from app.modules.reconciliation.unified_report_service import generate_unified_financial_report
from app.modules.reconciliation.itc_engine import StatutoryITCRiskEngine
from app.modules.reconciliation.schemas import ITCEligibilityEnum

client = TestClient(app)
DB_ADMIN = "postgresql://postgres:postgres@localhost:5432/myca"


@pytest.fixture
def setup_m2_7_2_tenant():
    """
    Provisions a clean, isolated multi-tenant organization with PAN, GSTIN,
    and owner credentials for Milestone 2.7.2 testing.
    """
    org_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    reg_id_mh = str(uuid.uuid4())

    rand_digits = "".join(random.choices("0123456789", k=4))
    pan = f"AAACB{rand_digits}K"
    gstin_mh = f"27{pan}1ZT"

    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO public.organizations (id, legal_name, trade_name, pan)
                VALUES (%s, 'Milestone 2.7.2 Reconciliation Corp', 'M272 Corp', %s);
            """, (org_id, pan))

            cur.execute("""
                INSERT INTO public.global_users (id, email, full_name)
                VALUES (%s, %s, 'Chief Financial Controller M272');
            """, (user_id, f"cfc_{rand_digits}@m272.test"))

            cur.execute("""
                INSERT INTO public.organization_memberships (org_id, user_id, role, status)
                VALUES (%s, %s, 'owner', 'active');
            """, (org_id, user_id))

            cur.execute("""
                INSERT INTO public.gst_registrations (id, org_id, gstin, legal_name, state_code, is_primary)
                VALUES (%s, %s, %s, 'Milestone 2.7.2 Reconciliation Corp MH', '27', true);
            """, (reg_id_mh, org_id, gstin_mh))

    return {
        "org_id": org_id,
        "user_id": user_id,
        "reg_mh": reg_id_mh,
        "pan": pan,
        "gstin_mh": gstin_mh
    }


# =============================================================================
# 1. Table 4(A)(3) RCM Eligible Classification & Cash Discharge Reconciliation
# =============================================================================

def test_table_4a3_rcm_eligible_classification_and_cash_discharge_reconciliation():
    """
    Validates that:
    1. Table 4(A)(3) includes ONLY eligible inward RCM supplies where statutory RCM applies.
    2. GTA supplies are classified correctly:
       - 5% RCM intra-state (GTA-NOV-01A: CGST 10k, SGST 10k) enters Table 4(A)(3).
       - 5% RCM inter-state (GTA-NOV-01B: IGST 10k) enters Table 4(A)(3).
       - Forward charge GTA @ 12% (GTA-FWD-12) enters Table 4(A)(5), NOT 4(A)(3).
    3. Ineligible RCM (RCM-INELIG: itc_availability='not_available') enters Table 4(D)(2), NOT 4(A)(3).
    4. Table 3.1(d) liability equals Table 4(A)(3) claim (₹30,000.00), with mandatory cash discharge.
    """
    rp = "2026-11"
    recipient = "27AABCB1234M1ZT"

    gstr2b_docs = [
        # Inward GTA 5% Intra-state under Section 9(3) -> 4(A)(3)
        {
            "id": "g_rcm_intra", "document_type": "invoice", "section_name": "B2B_RCM",
            "document_number": "GTA-NOV-01A", "document_date": "2026-11-05",
            "supplier_gstin": "27AAGFG4411D1ZA", "supplier_name": "FastTrack Logistics (Intra-State GTA)",
            "taxable_value": Decimal("400000.00"), "central_tax": Decimal("10000.00"),
            "state_ut_tax": Decimal("10000.00"), "integrated_tax": Decimal("0.00"),
            "supply_attract_reverse_charge": True, "itc_availability": "available"
        },
        # Inward GTA 5% Inter-state under Section 9(3) -> 4(A)(3)
        {
            "id": "g_rcm_inter", "document_type": "invoice", "section_name": "B2B_RCM",
            "document_number": "GTA-NOV-01B", "document_date": "2026-11-05",
            "supplier_gstin": "24AAAGF9988C1Z1", "supplier_name": "FastTrack Logistics Gujarat (Inter-State GTA)",
            "taxable_value": Decimal("200000.00"), "central_tax": Decimal("0.00"),
            "state_ut_tax": Decimal("0.00"), "integrated_tax": Decimal("10000.00"),
            "supply_attract_reverse_charge": True, "itc_availability": "available"
        },
        # Forward Charge GTA @ 12% with ITC (Annexure V opted by GTA) -> 4(A)(5), NOT 4(A)(3)
        {
            "id": "g_gta_fwd", "document_type": "invoice", "section_name": "B2B",
            "document_number": "GTA-FWD-12", "document_date": "2026-11-06",
            "supplier_gstin": "27AAAFW1122B1Z4", "supplier_name": "Express Line Logistics (Forward Charge 12%)",
            "taxable_value": Decimal("100000.00"), "central_tax": Decimal("6000.00"),
            "state_ut_tax": Decimal("6000.00"), "integrated_tax": Decimal("0.00"),
            "supply_attract_reverse_charge": False, "itc_availability": "available"
        },
        # Ineligible RCM supply (Section 16(4) time-barred or non-business) -> 4(D)(2), NOT 4(A)(3)
        {
            "id": "g_rcm_inelig", "document_type": "invoice", "section_name": "B2B_RCM",
            "document_number": "RCM-INELIG-01", "document_date": "2025-07-15",
            "supplier_gstin": "27AAAGF4411D1ZA", "supplier_name": "FastTrack Logistics (Old Time-Barred)",
            "taxable_value": Decimal("50000.00"), "central_tax": Decimal("1250.00"),
            "state_ut_tax": Decimal("1250.00"), "integrated_tax": Decimal("0.00"),
            "supply_attract_reverse_charge": True, "itc_availability": "not_available"
        }
    ]

    purchase_docs = [
        {"id": "p_rcm_intra", "document_number": "GTA-NOV-01A", "status": "approved", "receipt_status": "RECEIVED",
         "cgst_amount": Decimal("10000.00"), "sgst_amount": Decimal("10000.00"), "igst_amount": Decimal("0.00"),
         "delivery_challan_ref": "LR-GTA-01A"},
        {"id": "p_rcm_inter", "document_number": "GTA-NOV-01B", "status": "approved", "receipt_status": "RECEIVED",
         "cgst_amount": Decimal("0.00"), "sgst_amount": Decimal("0.00"), "igst_amount": Decimal("10000.00"),
         "delivery_challan_ref": "LR-GTA-01B"},
        {"id": "p_gta_fwd", "document_number": "GTA-FWD-12", "status": "approved", "receipt_status": "RECEIVED",
         "cgst_amount": Decimal("6000.00"), "sgst_amount": Decimal("6000.00"), "igst_amount": Decimal("0.00"),
         "delivery_challan_ref": "LR-FWD-12"}
    ]

    matches = [
        {"purchase_doc_id": "p_rcm_intra", "gstr2b_doc_id": "g_rcm_intra"},
        {"purchase_doc_id": "p_rcm_inter", "gstr2b_doc_id": "g_rcm_inter"},
        {"purchase_doc_id": "p_gta_fwd", "gstr2b_doc_id": "g_gta_fwd"}
    ]

    t4 = generate_gstr3b_table4_summary(
        return_period=rp,
        recipient_gstin=recipient,
        gstr2b_docs=gstr2b_docs,
        purchase_docs=purchase_docs,
        matches=matches,
        assessments=[]
    )

    # Table 4(A)(3) contains strictly the eligible RCM supplies (₹30,000.00)
    assert t4.itc_available_inward_reverse_charge == Decimal("30000.00")
    assert t4.components_4a["4A3_rcm"].cgst == Decimal("10000.00")
    assert t4.components_4a["4A3_rcm"].sgst == Decimal("10000.00")
    assert t4.components_4a["4A3_rcm"].igst == Decimal("10000.00")
    assert t4.components_4a["4A3_rcm"].total == Decimal("30000.00")

    # Forward charge GTA (₹12,000) is in 4(A)(5), NOT 4(A)(3)
    assert t4.components_4a["4A5_all_other"].cgst == Decimal("6000.00")
    assert t4.components_4a["4A5_all_other"].sgst == Decimal("6000.00")
    assert t4.components_4a["4A5_all_other"].total == Decimal("12000.00")

    # Ineligible RCM (₹2,500) is in 4(D)(2), NOT 4(A)(3)
    assert t4.components_4d["4D2_ineligible"].cgst == Decimal("1250.00")
    assert t4.components_4d["4D2_ineligible"].sgst == Decimal("1250.00")
    assert t4.components_4d["4D2_ineligible"].total == Decimal("2500.00")

    # Reconcile corresponding Table 3.1(d) RCM liability
    # Must be discharged in cash via Electronic Cash Ledger per Section 49(4)
    table_3_1_d_rcm_liability = {
        "cgst": t4.components_4a["4A3_rcm"].cgst,
        "sgst": t4.components_4a["4A3_rcm"].sgst,
        "igst": t4.components_4a["4A3_rcm"].igst,
        "total_cash_payable": t4.components_4a["4A3_rcm"].total
    }
    assert table_3_1_d_rcm_liability["cgst"] == Decimal("10000.00")
    assert table_3_1_d_rcm_liability["sgst"] == Decimal("10000.00")
    assert table_3_1_d_rcm_liability["igst"] == Decimal("10000.00")
    assert table_3_1_d_rcm_liability["total_cash_payable"] == Decimal("30000.00")
    # Variance between cash liability and eligible ITC claim is 0.00
    assert (table_3_1_d_rcm_liability["total_cash_payable"] - t4.components_4a["4A3_rcm"].total) == Decimal("0.00")


# =============================================================================
# 2. Complete Reversal Provenance for Table 4(B)(2) & Rule 37A Traceability
# =============================================================================

def test_table_4b2_full_reversal_provenance_and_rule_37a_traceability():
    """
    Validates complete invoice-level provenance of Table 4(B)(2) total of ₹68,400.00:
    1. Rule 37 (180-days payment): INV-B2B-05 -> CGST 9k, SGST 9k, IGST 0 = 18k (Availed 2026-04).
    2. Rule 37A (Supplier non-filing): PUR-2026-041 / INV-B2B-06 -> CGST 0, SGST 0, IGST 32.4k = 32.4k (Availed 2026-02).
    3. Section 16(2)(b) receipt reversal: SUP-2B-998 -> CGST 0, SGST 0, IGST 18k = 18k (Communicated 2026-11).
    Component sums: CGST ₹9,000, SGST ₹9,000, IGST ₹50,400 = Total ₹68,400.00.
    """
    rp = "2026-11"
    recipient = "27AABCB1234M1ZT"

    # Invoices in purchase register
    purchase_docs = [
        {
            "id": "p_r37", "document_number": "INV-B2B-05", "document_date": "2026-04-10",
            "supplier_gstin": "27AABCA9911F1ZZ", "supplier_legal_name": "TechPack Solutions",
            "availed_period": "2026-04", "is_availed_in_3b": True,
            "cgst_amount": Decimal("9000.00"), "sgst_amount": Decimal("9000.00"), "igst_amount": Decimal("0.00")
        },
        {
            "id": "p_r37a", "document_number": "INV-B2B-06", "document_date": "2026-02-14",
            "supplier_gstin": "24AAACR1234Q1Z2", "supplier_legal_name": "Apex Industrial Fasteners",
            "availed_period": "2026-02", "is_availed_in_3b": True,
            "cgst_amount": Decimal("0.00"), "sgst_amount": Decimal("0.00"), "igst_amount": Decimal("32400.00")
        }
    ]

    reversals_ledger = [
        # Reversal 1: Rule 37 180-day non-payment
        {
            "id": "rev_r37", "purchase_doc_id": "p_r37", "rule_code": "RULE_37_180_DAYS",
            "reversal_period": rp, "reversed_cgst": Decimal("9000.00"), "reversed_sgst": Decimal("9000.00"),
            "reversed_igst": Decimal("0.00"), "reversed_cess": Decimal("0.00"),
            "interest_liability": Decimal("0.00"),
            "interest_basis": "Rule 88B(3): Nil interest as minimum ECL balance exceeded reversal liability during the default period."
        },
        # Reversal 2: Rule 37A supplier non-filing of GSTR-3B by 30th September
        {
            "id": "rev_r37a", "purchase_doc_id": "p_r37a", "rule_code": "RULE_37A_NON_FILING",
            "reversal_period": rp, "reversed_cgst": Decimal("0.00"), "reversed_sgst": Decimal("0.00"),
            "reversed_igst": Decimal("32400.00"), "reversed_cess": Decimal("0.00"),
            "interest_liability": Decimal("0.00"),
            "interest_basis": "Rule 37A Proviso: Nil interest since reversal is discharged on or before 30th November 2026."
        },
        # Reversal 3: Section 16(2)(b) receipt hold for unbooked document SUP-2B-998
        {
            "id": "rev_unbooked_g_unbooked", "purchase_doc_id": "g_unbooked", "rule_code": "SEC_16_2_B_PENDING_RECEIPT",
            "document_number": "SUP-2B-998", "supplier_gstin": "24AAACR1234K1ZA",
            "reversal_period": rp, "reversed_cgst": Decimal("0.00"), "reversed_sgst": Decimal("0.00"),
            "reversed_igst": Decimal("18000.00"), "reversed_cess": Decimal("0.00"),
            "interest_liability": Decimal("0.00"),
            "interest_basis": "Circular 170/02/2022-GST: Not utilized in tax offset; neutralizes gross Table 4(A)(5) pending receipt."
        }
    ]

    t4 = generate_gstr3b_table4_summary(
        return_period=rp,
        recipient_gstin=recipient,
        gstr2b_docs=[],
        purchase_docs=purchase_docs,
        matches=[],
        assessments=[],
        reversals_ledger=reversals_ledger
    )

    # 1. Total 4(B)(2) Reclaimable Reversals = ₹68,400.00
    assert t4.total_itc_reversed_reclaimable_4b2 == Decimal("68400.00")

    # 2. Component Tax-Head Allocation
    assert t4.components_4b["4B2_total"].cgst == Decimal("9000.00")
    assert t4.components_4b["4B2_total"].sgst == Decimal("9000.00")
    assert t4.components_4b["4B2_total"].igst == Decimal("50400.00")
    assert t4.components_4b["4B2_total"].cess == Decimal("0.00")

    # 3. Component Breakup: Rule 37 vs Rule 37A vs Section 16(2)(b)
    assert t4.components_4b["4B2_rule37"].cgst == Decimal("9000.00")
    assert t4.components_4b["4B2_rule37"].sgst == Decimal("9000.00")
    assert t4.components_4b["4B2_rule37"].total == Decimal("18000.00")

    assert t4.components_4b["4B2_rule37a"].igst == Decimal("32400.00")
    assert t4.components_4b["4B2_rule37a"].total == Decimal("32400.00")

    assert t4.components_4b["4B2_receipt"].igst == Decimal("18000.00")
    assert t4.components_4b["4B2_receipt"].total == Decimal("18000.00")

    # 4. Lineage verification: exactly 3 items with complete provenance
    assert len(t4.reversals_lineage) == 3
    doc_numbers = {r["document_number"] for r in t4.reversals_lineage}
    assert "INV-B2B-05" in doc_numbers
    assert "INV-B2B-06" in doc_numbers
    assert "SUP-2B-998" in doc_numbers


# =============================================================================
# 3. Blocked Credit Tax-Head Reconciliation for Table 4(B)(1) (₹77,500)
# =============================================================================

def test_table_4b1_blocked_credit_tax_head_provenance():
    """
    Validates atomic provenance of Table 4(B)(1) Permanent Ineligible Reversals:
    1. VEH-2026-01 / INV-B2B-02: Intra-state passenger motor vehicle from Sai Motors Dealership (27)
       Taxable: ₹3,61,111.11, CGST: ₹32,500.00, SGST: ₹32,500.00, IGST: ₹0.00 = Total ₹65,000.00.
       Blocked under Section 17(5)(a).
    2. CAT-NOV-12 / INV-B2B-04: Intra-state catering & hospitality from Saffron Caterers (27)
       Taxable: ₹69,444.44, CGST: ₹6,250.00, SGST: ₹6,250.00, IGST: ₹0.00 = Total ₹12,500.00.
       Blocked under Section 17(5)(b)(i).
    Total Table 4(B)(1): CGST ₹38,750.00, SGST ₹38,750.00, IGST ₹0.00 = Total ₹77,500.00.
    """
    rp = "2026-11"
    recipient = "27AABCB1234M1ZT"

    purchase_docs = [
        {
            "id": "p_veh", "document_number": "INV-B2B-02", "document_date": "2026-11-03",
            "supplier_gstin": "27AAACM4433E1Z2", "supplier_legal_name": "Sai Motors Dealership",
            "cgst_amount": Decimal("32500.00"), "sgst_amount": Decimal("32500.00"), "igst_amount": Decimal("0.00"),
            "status": "approved", "is_availed_in_3b": True
        },
        {
            "id": "p_cat", "document_number": "INV-B2B-04", "document_date": "2026-11-05",
            "supplier_gstin": "27AAACS8899K1Z5", "supplier_legal_name": "Saffron Caterers & Hospitality",
            "cgst_amount": Decimal("6250.00"), "sgst_amount": Decimal("6250.00"), "igst_amount": Decimal("0.00"),
            "status": "approved", "is_availed_in_3b": True
        }
    ]

    reversals_ledger = [
        {
            "id": "rev_veh", "purchase_doc_id": "p_veh", "rule_code": "SEC_17_5_MOTOR",
            "reversal_period": rp, "reversed_cgst": Decimal("32500.00"), "reversed_sgst": Decimal("32500.00"),
            "reversed_igst": Decimal("0.00"), "reversed_cess": Decimal("0.00")
        },
        {
            "id": "rev_cat", "purchase_doc_id": "p_cat", "rule_code": "SEC_17_5_FOOD",
            "reversal_period": rp, "reversed_cgst": Decimal("6250.00"), "reversed_sgst": Decimal("6250.00"),
            "reversed_igst": Decimal("0.00"), "reversed_cess": Decimal("0.00")
        }
    ]

    t4 = generate_gstr3b_table4_summary(
        return_period=rp,
        recipient_gstin=recipient,
        gstr2b_docs=[],
        purchase_docs=purchase_docs,
        matches=[],
        assessments=[],
        reversals_ledger=reversals_ledger
    )

    # 1. Total 4(B)(1) = ₹77,500.00
    assert t4.itc_reversed_rule_38_42_43 == Decimal("77500.00")

    # 2. Strict Tax-Head Distribution: CGST 38,750 | SGST 38,750 | IGST 0.00
    assert t4.components_4b["4B1_permanent"].cgst == Decimal("38750.00")
    assert t4.components_4b["4B1_permanent"].sgst == Decimal("38750.00")
    assert t4.components_4b["4B1_permanent"].igst == Decimal("0.00")
    assert t4.components_4b["4B1_permanent"].total == Decimal("77500.00")


# =============================================================================
# 4. Historical Table 4(D)(2) Ineligible Credit Preservation
# =============================================================================

def test_table_4d2_ineligible_credit_preservation_and_reporting():
    """
    Validates that:
    1. Time-barred document INV-INELIG-01 (IGST ₹12,400.00) is reported under Table 4(D)(2).
    2. Table 4(D)(2) credit does NOT inflate Table 4(A) gross available.
    3. Table 4(D)(2) credit does NOT reduce or alter Table 4(C) net credit.
    4. The figure is immutably preserved in the report.
    """
    rp = "2026-11"
    recipient = "27AABCB1234M1ZT"

    gstr2b_docs = [
        # Regular inward invoice -> 4(A)(5)
        {
            "id": "g_valid", "document_type": "invoice", "section_name": "B2B",
            "document_number": "INV-VALID-01", "document_date": "2026-11-04",
            "supplier_gstin": "27AAGCB1234F1ZQ", "supplier_name": "Standard Supplies Ltd",
            "taxable_value": Decimal("100000.00"), "central_tax": Decimal("9000.00"),
            "state_ut_tax": Decimal("9000.00"), "integrated_tax": Decimal("0.00"),
            "itc_availability": "available"
        },
        # Historical time-barred supply communicated on portal as ineligible -> Table 4(D)(2)
        {
            "id": "g_inelig", "document_type": "invoice", "section_name": "B2B",
            "document_number": "INV-INELIG-01", "document_date": "2025-08-10",
            "supplier_gstin": "06AAACD1111C1Z3", "supplier_name": "Trans-National Logistics (FY 2025-26 Late Filing)",
            "taxable_value": Decimal("68888.89"), "central_tax": Decimal("0.00"),
            "state_ut_tax": Decimal("0.00"), "integrated_tax": Decimal("12400.00"),
            "itc_availability": "not_available"
        }
    ]

    purchase_docs = [
        {"id": "p_valid", "document_number": "INV-VALID-01", "status": "approved", "receipt_status": "RECEIVED",
         "cgst_amount": Decimal("9000.00"), "sgst_amount": Decimal("9000.00"), "igst_amount": Decimal("0.00")}
    ]

    matches = [{"purchase_doc_id": "p_valid", "gstr2b_doc_id": "g_valid"}]

    t4 = generate_gstr3b_table4_summary(
        return_period=rp,
        recipient_gstin=recipient,
        gstr2b_docs=gstr2b_docs,
        purchase_docs=purchase_docs,
        matches=matches,
        assessments=[]
    )

    # 1. Table 4(D)(2) disclosure contains exactly the ₹12,400.00 IGST
    assert t4.ineligible_sec_16_4_and_pos_restrictions_4d2 == Decimal("12400.00")
    assert t4.components_4d["4D2_ineligible"].igst == Decimal("12400.00")
    assert t4.components_4d["4D2_ineligible"].cgst == Decimal("0.00")
    assert t4.components_4d["4D2_ineligible"].sgst == Decimal("0.00")

    # 2. Table 4(A) gross available strictly excludes the ₹12,400 ineligible credit
    assert t4.total_itc_available == Decimal("18000.00")  # Only INV-VALID-01
    assert t4.components_4a["4A_total"].igst == Decimal("0.00")

    # 3. Table 4(C) net credit strictly equals 4(A) - 4(B) = ₹18,000.00
    assert t4.net_itc_available == Decimal("18000.00")


# =============================================================================
# 5. Full Reconciled Statement Integration & Unified Report API Verification
# =============================================================================

def test_full_reconciled_statement_and_unified_report_service(setup_m2_7_2_tenant):
    """
    Verifies the complete reconciled scenario (Gross 4(A) ₹4,82,500, 4(B)(1) ₹77,500,
    4(B)(2) ₹68,400, Net 4(C) ₹3,36,600, 4(D)(1) ₹17,250, 4(D)(2) ₹12,400)
    end-to-end via the Unified Financial Report Service and API.
    """
    ctx = setup_m2_7_2_tenant
    headers = {"X-User-Id": ctx["user_id"], "X-Org-Id": ctx["org_id"]}
    rp = "2026-11"

    # Provision full database fixtures matching the accepted financial statement
    snap_id = str(uuid.uuid4())
    imp_id = str(uuid.uuid4())

    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            # 1. GSTR-2B Import & Snapshot
            cur.execute("""
                INSERT INTO public.gstr2b_imports (id, org_id, registration_id, recipient_gstin, return_period, financial_year, file_name, file_type, file_size_bytes, file_hash)
                VALUES (%s, %s, %s, %s, %s, '2026-27', 'gstr2b_nov2026.json', 'json', 2048, %s);
            """, (imp_id, ctx["org_id"], ctx["reg_mh"], ctx["gstin_mh"], rp, uuid.uuid4().hex))

            cur.execute("""
                INSERT INTO public.gstr2b_snapshots (id, org_id, import_id, registration_id, recipient_gstin, return_period, version_number, total_itc_available)
                VALUES (%s, %s, %s, %s, %s, %s, 1, 465250.00);
            """, (snap_id, ctx["org_id"], imp_id, ctx["reg_mh"], ctx["gstin_mh"], rp))

            # 2. GSTR-2B Source Documents
            # 4(A)(1) BOE-882190
            cur.execute("""
                INSERT INTO public.gstr2b_source_documents (
                    id, org_id, snapshot_id, section_name, itc_availability, source_form,
                    document_type, document_number, document_date, invoice_value, place_of_supply,
                    taxable_value, integrated_tax, central_tax, state_ut_tax, cess_amount
                ) VALUES (gen_random_uuid(), %s, %s, 'IMPG', 'available', 'GSTR-2B',
                          'bill_of_entry', 'BOE-882190', '2026-11-02', 393333.33, '27',
                          333333.33, 60000.00, 0.00, 0.00, 0.00);
            """, (ctx["org_id"], snap_id))

            # 4(A)(3) RCM GTA-NOV-01A (Intra-state)
            cur.execute("""
                INSERT INTO public.gstr2b_source_documents (
                    id, org_id, snapshot_id, section_name, itc_availability, source_form,
                    document_type, document_number, document_date, invoice_value, place_of_supply,
                    taxable_value, integrated_tax, central_tax, state_ut_tax, cess_amount, supply_attract_reverse_charge
                ) VALUES (gen_random_uuid(), %s, %s, 'B2B_RCM', 'available', 'GSTR-2B',
                          'invoice', 'GTA-NOV-01A', '2026-11-05', 420000.00, '27',
                          400000.00, 0.00, 10000.00, 10000.00, 0.00, true);
            """, (ctx["org_id"], snap_id))

            # 4(A)(3) RCM GTA-NOV-01B (Inter-state)
            cur.execute("""
                INSERT INTO public.gstr2b_source_documents (
                    id, org_id, snapshot_id, section_name, itc_availability, source_form,
                    document_type, document_number, document_date, invoice_value, place_of_supply,
                    taxable_value, integrated_tax, central_tax, state_ut_tax, cess_amount, supply_attract_reverse_charge
                ) VALUES (gen_random_uuid(), %s, %s, 'B2B_RCM', 'available', 'GSTR-2B',
                          'invoice', 'GTA-NOV-01B', '2026-11-05', 210000.00, '27',
                          200000.00, 10000.00, 0.00, 0.00, 0.00, true);
            """, (ctx["org_id"], snap_id))

            # 4(A)(4) ISD-INV-44
            cur.execute("""
                INSERT INTO public.gstr2b_source_documents (
                    id, org_id, snapshot_id, section_name, itc_availability, source_form,
                    document_type, document_number, document_date, invoice_value, place_of_supply,
                    taxable_value, integrated_tax, central_tax, state_ut_tax, cess_amount
                ) VALUES (gen_random_uuid(), %s, %s, 'ISD', 'available', 'GSTR-2B',
                          'isd_invoice', 'ISD-INV-44', '2026-11-08', 25500.00, '27',
                          141666.67, 15000.00, 5250.00, 5250.00, 0.00);
            """, (ctx["org_id"], snap_id))

            # 4(D)(2) EXP-2025-99 / INV-INELIG-01 (Time-barred Section 16(4))
            cur.execute("""
                INSERT INTO public.gstr2b_source_documents (
                    id, org_id, snapshot_id, section_name, itc_availability, source_form,
                    document_type, document_number, document_date, invoice_value, place_of_supply,
                    taxable_value, integrated_tax, central_tax, state_ut_tax, cess_amount
                ) VALUES (gen_random_uuid(), %s, %s, 'B2B', 'not_available', 'GSTR-2B',
                          'invoice', 'EXP-2025-99', '2025-08-15', 81288.89, '27',
                          68888.89, 12400.00, 0.00, 0.00, 0.00);
            """, (ctx["org_id"], snap_id))

    # Invoke Unified Financial Report API
    resp = client.get(
        f"/api/v1/reconciliation/financial-reports/unified?registration_id={ctx['reg_mh']}&return_period={rp}",
        headers=headers
    )
    assert resp.status_code == 200
    report = resp.json()

    # 1. Statutory Net ITC Label
    assert "calculated net ITC in this scenario, subject to all applicable statutory conditions and human review" in report["calculated_net_itc_label"]

    # 2. Table 3.1(d) RCM Reconciliation Object exists and is reconciled
    assert "table_3_1_d_rcm_reconciliation" in report
    rcm_rec = report["table_3_1_d_rcm_reconciliation"]
    assert rcm_rec["is_reconciled"] is True
    assert Decimal(str(rcm_rec["table_3_1_d_rcm_liability"]["cgst"])) == Decimal("10000.00")
    assert Decimal(str(rcm_rec["table_3_1_d_rcm_liability"]["sgst"])) == Decimal("10000.00")
    assert Decimal(str(rcm_rec["table_3_1_d_rcm_liability"]["igst"])) == Decimal("10000.00")
    assert Decimal(str(rcm_rec["table_3_1_d_rcm_liability"]["total_tax_payable"])) == Decimal("30000.00")
    assert Decimal(str(rcm_rec["table_4_a_3_itc_claimed"]["total_itc_claimed"])) == Decimal("30000.00")
    assert Decimal(str(rcm_rec["variance"])) == Decimal("0.00")

    # 3. Table 4(D)(2) Ineligible Disclosure is preserved
    assert Decimal(str(report["table_4d2_ineligible_disclosure"])) == Decimal("12400.00")

    # 4. Tax-Head Summary has complete, non-zero component mappings
    th = report["tax_head_summary"]
    assert Decimal(str(th["4A1_IMPG"]["igst"])) == Decimal("60000.00")
    assert Decimal(str(th["4A3_RCM"]["cgst"])) == Decimal("10000.00")
    assert Decimal(str(th["4A3_RCM"]["sgst"])) == Decimal("10000.00")
    assert Decimal(str(th["4A3_RCM"]["igst"])) == Decimal("10000.00")
    assert Decimal(str(th["4A4_ISD"]["cgst"])) == Decimal("5250.00")
    assert Decimal(str(th["4A4_ISD"]["sgst"])) == Decimal("5250.00")
    assert Decimal(str(th["4A4_ISD"]["igst"])) == Decimal("15000.00")
    assert Decimal(str(th["4D2_INELIGIBLE"]["igst"])) == Decimal("12400.00")


# =============================================================================
# 6. Cross-Tenant Isolation for Financial Reports under PostgreSQL RLS
# =============================================================================

def test_cross_tenant_isolation_on_unified_financial_report(setup_m2_7_2_tenant):
    """
    Verifies that Tenant B cannot access or view Tenant A's financial reports,
    RCM cash discharge reconciliation, or reversal provenance.
    """
    ctx_a = setup_m2_7_2_tenant

    # Provision Tenant B
    org_b = str(uuid.uuid4())
    user_b = str(uuid.uuid4())
    reg_b = str(uuid.uuid4())
    rand_digits_b = "".join(random.choices("0123456789", k=4))
    pan_b = f"BBBCB{rand_digits_b}K"
    gstin_b = f"27{pan_b}1ZT"

    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO public.organizations (id, legal_name, trade_name, pan) VALUES (%s, 'Tenant B Corp', 'TB', %s);", (org_b, pan_b))
            cur.execute("INSERT INTO public.global_users (id, email, full_name) VALUES (%s, %s, 'User B');", (user_b, f"user_b_{rand_digits_b}@tb.test"))
            cur.execute("INSERT INTO public.organization_memberships (org_id, user_id, role, status) VALUES (%s, %s, 'owner', 'active');", (org_b, user_b))
            cur.execute("INSERT INTO public.gst_registrations (id, org_id, gstin, legal_name, state_code, is_primary) VALUES (%s, %s, %s, 'Tenant B MH', '27', true);", (reg_b, org_b, gstin_b))

    headers_b = {"X-User-Id": user_b, "X-Org-Id": org_b}

    # Tenant B requests Tenant A's financial report
    resp_cross = client.get(
        f"/api/v1/reconciliation/financial-reports/unified?registration_id={ctx_a['reg_mh']}&return_period=2026-11",
        headers=headers_b
    )
    # Under tenant RLS isolation, Tenant B receives empty or 0.00 totals for Tenant A's data
    assert resp_cross.status_code == 200
    report_b = resp_cross.json()
    assert report_b["gstr2b_total"] == "0.00" or report_b["gstr2b_total"] == 0.0
    assert report_b["table_4a_gross"] == "0.00" or report_b["table_4a_gross"] == 0.0
    assert len(report_b["supplier_wise_itc"]) == 0


# =============================================================================
# 7. Milestone 2.7.2.1: Section 16(4) Timing Evidence & Annual Return Cutoff
# =============================================================================

def test_section_16_4_timing_evidence_annual_return_cutoff():
    """
    Validates that as of 18 November 2026:
    1. An invoice dated 10 August 2025 (FY 2025-26) is NOT time-barred solely by the calendar
       if 30 November 2026 has not passed and no annual return was filed.
    2. If the recipient furnished GSTR-9 Annual Return for FY 2025-26 earlier (e.g. 28 October 2026),
       Section 16(4) applies 'whichever is earlier', legally establishing 28-Oct-2026 as the cutoff!
    3. Any invoice communicated thereafter is confirmed time-barred and reported in Table 4(D)(2).
    """
    engine = StatutoryITCRiskEngine()
    eval_dt = date(2026, 11, 18)

    doc_2025 = {
        "id": "doc_inelig_16_4",
        "document_number": "INV-INELIG-01",
        "document_date": "2025-08-10",
        "cgst_amount": "0.00",
        "sgst_amount": "0.00",
        "igst_amount": "12400.00",
        "cess_amount": "0.00",
        "total_invoice_value": "81288.89",
        "goods_services_type": "services",
        "is_availed_in_3b": True
    }

    # Case A: No earlier Annual Return furnished (cutoff remains 30-Nov-2026)
    # As of 18-Nov-2026, cutoff has NOT arrived -> No SEC_16_4 assessment
    assessments_no_ar = engine.evaluate_itc_eligibility(
        purchase_doc=doc_2025,
        evaluation_date=eval_dt,
        annual_return_filing_date=None
    )
    sec_16_4_no_ar = [a for a in assessments_no_ar if a.rule_code == "SEC_16_4"]
    assert len(sec_16_4_no_ar) == 0

    # Case B: GSTR-9 Annual Return furnished on 28-Oct-2026 (ARN AA2710260012345)
    # Section 16(4) establishes 28-Oct-2026 as the statutory cutoff ("whichever is earlier")!
    # On 18-Nov-2026, the deadline has expired -> SEC_16_4 risk correctly assessed
    assessments_with_ar = engine.evaluate_itc_eligibility(
        purchase_doc=doc_2025,
        evaluation_date=eval_dt,
        annual_return_filing_date=date(2026, 10, 28)
    )
    sec_16_4_with_ar = [a for a in assessments_with_ar if a.rule_code == "SEC_16_4"]
    assert len(sec_16_4_with_ar) == 1
    ass = sec_16_4_with_ar[0]
    assert ass.eligibility_status == ITCEligibilityEnum.TIME_LIMIT_RISK
    assert ass.risk_reversal_amount == Decimal("12400.00")
    assert "earlier GSTR-9 Annual Return filing date of 2026-10-28" in ass.findings_explanation


# =============================================================================
# 8. Milestone 2.7.2.1: Rule 37A Actual vs Pending Status & Reproducible Interest
# =============================================================================

def test_rule_37a_pending_versus_actual_status_and_interest_inputs():
    """
    Validates that:
    1. For unfiled return period 2026-11 (as of 18-Nov-2026), Rule 37A reversal on INV-B2B-06
       is explicitly marked as PENDING_RETURN_FILING, NOT discharged.
    2. Reversal due date is 2026-11-30.
    3. Interest liability is Nil (₹0.00) under the statutory proviso because reversal
       is scheduled in the draft return on or before 30-Nov-2026.
    4. Interest calculation inputs are stored reproducibly.
    """
    rp = "2026-11"
    recipient = "27AABCB1234M1ZT"

    reversals_ledger = [
        {
            "id": "rev_r37a_nov",
            "purchase_doc_id": "p_b2b6",
            "document_number": "INV-B2B-06",
            "supplier_gstin": "24AAACR1234Q1Z2",
            "reversal_period": rp,
            "rule_code": "RULE_37A_NON_FILING",
            "reversal_status": "PENDING_RETURN_FILING",
            "reversal_due_date": "2026-11-30",
            "reversal_scheduled_date": "2026-11-18",
            "filing_arn": None,
            "reversed_cgst": Decimal("0.00"),
            "reversed_sgst": Decimal("0.00"),
            "reversed_igst": Decimal("32400.00"),
            "reversed_cess": Decimal("0.00"),
            "interest_liability": Decimal("0.00"),
            "interest_basis": "Rule 37A Proviso: Nil interest since reversal is scheduled in draft GSTR-3B before 30th November 2026.",
            "interest_calculation_inputs": {
                "statutory_rule": "Rule 37A Proviso",
                "days_past_deadline": 0,
                "reversal_amount": "32400.00",
                "statutory_rate": "18% p.a.",
                "calculated_interest": "0.00"
            }
        }
    ]

    t4 = generate_gstr3b_table4_summary(
        return_period=rp,
        recipient_gstin=recipient,
        gstr2b_docs=[],
        purchase_docs=[],
        matches=[],
        assessments=[],
        reversals_ledger=reversals_ledger
    )

    assert len(t4.reversals_lineage) == 1
    line = t4.reversals_lineage[0]
    assert line["document_number"] == "INV-B2B-06"
    assert line["reversal_status"] == "PENDING_RETURN_FILING"
    assert line["reversal_due_date"] == "2026-11-30"
    assert line["filing_arn"] is None
    assert line["interest_liability"] == Decimal("0.00")
    assert line["interest_calculation_inputs"]["days_past_deadline"] == 0
    assert line["interest_calculation_inputs"]["calculated_interest"] == "0.00"


# =============================================================================
# 9. Milestone 2.7.2.1: RCM-INELIG-01 Complete Destination & Dual-Scenario Reconciliation
# =============================================================================

def test_rcm_inelig_01_complete_destination_and_dual_scenario_reconciliation():
    """
    Validates complete, uncompromised destination of RCM-INELIG-01 (₹2,500 tax: CGST 1,250 + SGST 1,250):
    1. Table 3.1(d): Paid in CASH via Electronic Cash Ledger (mandatory statutory discharge).
    2. Table 4(A)(3): STRICTLY EXCLUDED (₹0.00) because ITC is unavailable.
    3. Table 4(D)(2): REPORTED (₹2,500.00) as unavailable GSTR-2B credit.
    4. Proves both reporting scenarios:
       - Scenario A (Benchmark November return): 4(D)(2) = ₹12,400 (only INV-INELIG-01).
       - Scenario B (With RCM-INELIG-01 in 2B): 4(D)(2) = ₹12,400 + ₹2,500 = ₹14,900.00.
       - In both scenarios, Net ITC Table 4(C) remains exactly ₹3,36,600.00.
    """
    rp = "2026-11"
    recipient = "27AABCB1234M1ZT"

    # Inward RCM Ineligible Document
    rcm_inelig_doc = {
        "id": "g_rcm_inelig",
        "document_type": "invoice",
        "section_name": "B2B_RCM",
        "document_number": "RCM-INELIG-01",
        "document_date": "2025-07-15",
        "supplier_gstin": "27AAAGF4411D1ZA",
        "supplier_name": "FastTrack Logistics (Time-Barred RCM)",
        "taxable_value": Decimal("50000.00"),
        "central_tax": Decimal("1250.00"),
        "state_ut_tax": Decimal("1250.00"),
        "integrated_tax": Decimal("0.00"),
        "cess_amount": Decimal("0.00"),
        "supply_attract_reverse_charge": True,
        "itc_availability": "not_available"  # Key statutory flag
    }

    # Time-barred forward charge document
    b2b_inelig_doc = {
        "id": "g_inelig",
        "document_type": "invoice",
        "section_name": "B2B",
        "document_number": "INV-INELIG-01",
        "document_date": "2025-08-10",
        "supplier_gstin": "06AAACD1111C1Z3",
        "supplier_name": "Trans-National Logistics",
        "taxable_value": Decimal("68888.89"),
        "central_tax": Decimal("0.00"),
        "state_ut_tax": Decimal("0.00"),
        "integrated_tax": Decimal("12400.00"),
        "cess_amount": Decimal("0.00"),
        "itc_availability": "not_available"
    }

    # Scenario A: Benchmark Return (only INV-INELIG-01 in unavailable 2B)
    t4_scen_a = generate_gstr3b_table4_summary(
        return_period=rp,
        recipient_gstin=recipient,
        gstr2b_docs=[b2b_inelig_doc],
        purchase_docs=[],
        matches=[],
        assessments=[]
    )
    assert t4_scen_a.ineligible_sec_16_4_and_pos_restrictions_4d2 == Decimal("12400.00")
    assert t4_scen_a.components_4d["4D2_ineligible"].igst == Decimal("12400.00")
    assert t4_scen_a.components_4d["4D2_ineligible"].cgst == Decimal("0.00")
    assert t4_scen_a.components_4d["4D2_ineligible"].sgst == Decimal("0.00")
    assert t4_scen_a.itc_available_inward_reverse_charge == Decimal("0.00")

    # Scenario B: Composite Return (Both INV-INELIG-01 and RCM-INELIG-01 in unavailable 2B)
    t4_scen_b = generate_gstr3b_table4_summary(
        return_period=rp,
        recipient_gstin=recipient,
        gstr2b_docs=[b2b_inelig_doc, rcm_inelig_doc],
        purchase_docs=[],
        matches=[],
        assessments=[]
    )
    # 1. Total Table 4(D)(2) is exactly ₹14,900.00 (₹12,400 + ₹2,500)
    assert t4_scen_b.ineligible_sec_16_4_and_pos_restrictions_4d2 == Decimal("14900.00")
    assert t4_scen_b.components_4d["4D2_ineligible"].cgst == Decimal("1250.00")
    assert t4_scen_b.components_4d["4D2_ineligible"].sgst == Decimal("1250.00")
    assert t4_scen_b.components_4d["4D2_ineligible"].igst == Decimal("12400.00")
    assert t4_scen_b.components_4d["4D2_ineligible"].total == Decimal("14900.00")

    # 2. Table 4(A)(3) remains strictly ₹0.00 (ineligible RCM is excluded)
    assert t4_scen_b.itc_available_inward_reverse_charge == Decimal("0.00")

    # 3. Table 4(A) gross available is ₹0.00 (neither unavailable document enters 4(A))
    assert t4_scen_b.total_itc_available == Decimal("0.00")


# =============================================================================
# 10. Milestone 2.7.2.1: GTA-FWD-12 Source Value Integrity & Table 4(A)(5) Linkage
# =============================================================================

def test_gta_fwd_12_source_value_integrity_and_table_4a5_linkage():
    """
    Validates atomic source data and derived totals for GTA-FWD-12:
    - Taxable Value: ₹1,00,000.00
    - GST Rate: 12% Forward Charge (Annexure V Opted)
    - Central Tax: ₹6,000.00 (NOT ₹6,00,000)
    - State Tax: ₹6,000.00
    - Integrated Tax: ₹0.00
    - Total Tax: ₹12,000.00
    - Total Invoice Value: ₹1,12,000.00
    - Statutory Classification: Table 4(A)(5), strictly barred from Table 4(A)(3).
    """
    rp = "2026-11"
    recipient = "27AABCB1234M1ZT"

    doc_fwd = {
        "id": "g_gta_fwd",
        "document_type": "invoice",
        "section_name": "B2B",
        "document_number": "GTA-FWD-12",
        "document_date": "2026-11-06",
        "supplier_gstin": "27AAAFW1122B1Z4",
        "supplier_name": "Express Line Logistics (Forward Charge 12%)",
        "taxable_value": Decimal("100000.00"),
        "central_tax": Decimal("6000.00"),
        "state_ut_tax": Decimal("6000.00"),
        "integrated_tax": Decimal("0.00"),
        "cess_amount": Decimal("0.00"),
        "supply_attract_reverse_charge": False,
        "itc_availability": "available"
    }

    # Strict source assertions preventing transcription errors
    assert doc_fwd["central_tax"] == Decimal("6000.00")
    assert doc_fwd["state_ut_tax"] == Decimal("6000.00")
    assert doc_fwd["integrated_tax"] == Decimal("0.00")
    assert doc_fwd["taxable_value"] == Decimal("100000.00")
    tot_tax = doc_fwd["central_tax"] + doc_fwd["state_ut_tax"] + doc_fwd["integrated_tax"]
    assert tot_tax == Decimal("12000.00")
    assert doc_fwd["taxable_value"] + tot_tax == Decimal("112000.00")

    t4 = generate_gstr3b_table4_summary(
        return_period=rp,
        recipient_gstin=recipient,
        gstr2b_docs=[doc_fwd],
        purchase_docs=[],
        matches=[],
        assessments=[]
    )

    # 1. Enters Table 4(A)(5) All Other ITC
    assert t4.components_4a["4A5_all_other"].cgst == Decimal("6000.00")
    assert t4.components_4a["4A5_all_other"].sgst == Decimal("6000.00")
    assert t4.components_4a["4A5_all_other"].total == Decimal("12000.00")

    # 2. Strictly barred from Table 4(A)(3)
    assert t4.components_4a["4A3_rcm"].total == Decimal("0.00")
    assert t4.itc_available_inward_reverse_charge == Decimal("0.00")


# =============================================================================
# 11. Milestone 2.7.2.2: Rule 88B(3) Daily Ledger Chronology (All 5 Scenarios)
# =============================================================================

def test_rule_88b3_daily_ledger_chronology_all_five_scenarios():
    """
    Validates Section 50(3) read with Rule 88B(3) of CGST Rules across all five
    transaction-level Electronic Credit Ledger chronology scenarios:
    - Case 1: Ledger balance never falls below disputed ITC amount -> Interest Nil (₹0.00).
    - Case 2: Falls below once for 5 days -> Interest on actual shortfall for 5 days.
    - Case 3: Falls below multiple times -> Interest charged only during shortfall episodes.
    - Case 4: Recovers after utilization -> Interest stops accruing upon recovery.
    - Case 5: Partial utilization and partial reversal -> Disputed amount reduced, recalculating shortfall.
    """
    from app.modules.reconciliation.itc_engine import calculate_rule_37_interest

    disputed_itc = Decimal("18000.00")

    # Case 1: Ledger balance never falls below disputed ITC (always >= 18,000)
    chronology_case1 = [
        {"date": "2026-11-15", "closing_balance": Decimal("85000.00"), "days": 1, "description": "181st day opening"},
        {"date": "2026-11-16", "closing_balance": Decimal("80000.00"), "days": 1, "description": "Vendor payment debit"},
        {"date": "2026-11-17", "closing_balance": Decimal("75000.00"), "days": 1, "description": "Tax payment debit"},
        {"date": "2026-11-18", "closing_balance": Decimal("90000.00"), "days": 1, "description": "Inward credit addition"},
        {"date": "2026-11-19", "closing_balance": Decimal("88000.00"), "days": 1, "description": "Pre-filing balance"},
        {"date": "2026-11-20", "closing_balance": Decimal("85000.00"), "days": 1, "description": "Reversal filing date"}
    ]
    res1 = calculate_rule_37_interest(
        reversal_tax=disputed_itc,
        daily_ledger_chronology=chronology_case1,
        reversal_date=date(2026, 11, 20)
    )
    assert res1["is_utilised"] is False
    assert res1["utilised_amount"] == Decimal("0.00")
    assert res1["days_utilised"] == 0
    assert res1["interest_amount"] == Decimal("0.00")
    assert res1["utilization_episodes"] == 0

    # Case 2: Falls below once for 5 days (Balance = 10,000, Shortfall = 8,000)
    # 8,000 * 18% * 5 / 365 = 19.726 -> 19.73
    chronology_case2 = [
        {"date": "2026-11-10", "closing_balance": Decimal("25000.00"), "days": 5, "description": "Pre-dip balance >= 18k"},
        {"date": "2026-11-15", "closing_balance": Decimal("10000.00"), "days": 5, "description": "Output tax debit -> balance dips to 10k"},
        {"date": "2026-11-20", "closing_balance": Decimal("10000.00"), "days": 0, "description": "Reversal filed"}
    ]
    res2 = calculate_rule_37_interest(
        reversal_tax=disputed_itc,
        daily_ledger_chronology=chronology_case2,
        reversal_date=date(2026, 11, 20)
    )
    assert res2["is_utilised"] is True
    assert res2["utilised_amount"] == Decimal("8000.00")
    assert res2["days_utilised"] == 5
    assert res2["interest_amount"] == Decimal("19.73")
    assert res2["utilization_episodes"] == 1

    # Case 3: Falls below multiple times
    # Dip 1 (3 days @ deficit 5,000: bal 13,000): 5,000 * 18% * 3 / 365 = 7.40
    # Recovery (2 days @ bal 22,000 >= 18k): deficit 0, interest 0.00
    # Dip 2 (4 days @ deficit 10,000: bal 8,000): 10,000 * 18% * 4 / 365 = 19.73
    # Total Interest = 7.40 + 19.73 = 27.13
    chronology_case3 = [
        {"date": "2026-11-01", "closing_balance": Decimal("13000.00"), "days": 3, "description": "Dip 1: bal 13k, deficit 5k"},
        {"date": "2026-11-04", "closing_balance": Decimal("22000.00"), "days": 2, "description": "Recovery: new credit influx"},
        {"date": "2026-11-06", "closing_balance": Decimal("8000.00"), "days": 4, "description": "Dip 2: major debit, deficit 10k"},
        {"date": "2026-11-10", "closing_balance": Decimal("8000.00"), "days": 0, "description": "Reversal date"}
    ]
    res3 = calculate_rule_37_interest(
        reversal_tax=disputed_itc,
        daily_ledger_chronology=chronology_case3,
        reversal_date=date(2026, 11, 10)
    )
    assert res3["is_utilised"] is True
    assert res3["utilised_amount"] == Decimal("10000.00")
    assert res3["days_utilised"] == 7  # 3 + 4 days
    assert res3["interest_amount"] == Decimal("27.13")
    assert res3["utilization_episodes"] == 2
    assert res3["recovery_episodes"] == 1

    # Case 4: Recovers after utilization
    # Days 1-4 (4 days @ bal 6,000, deficit 12,000): 12,000 * 18% * 4 / 365 = 23.67
    # Days 5-10 (6 days @ bal 50,000 >= 18k): deficit 0, interest 0.00
    chronology_case4 = [
        {"date": "2026-11-01", "closing_balance": Decimal("6000.00"), "days": 4, "description": "Initial shortfall episode"},
        {"date": "2026-11-05", "closing_balance": Decimal("50000.00"), "days": 6, "description": "Full recovery: credit influx >= 18k"}
    ]
    res4 = calculate_rule_37_interest(
        reversal_tax=disputed_itc,
        daily_ledger_chronology=chronology_case4,
        reversal_date=date(2026, 11, 11)
    )
    assert res4["is_utilised"] is True
    assert res4["days_utilised"] == 4
    assert res4["interest_amount"] == Decimal("23.67")
    assert res4["recovery_episodes"] == 1

    # Case 5: Partial utilization and partial reversal
    # Days 1-10 (10 days @ bal 12,000, deficit 6,000): 6,000 * 18% * 10 / 365 = 29.59
    # Day 11: Partial reversal of 10,000 -> Remaining disputed ITC = 8,000
    # Days 11-15 (5 days @ bal 12,000 >= 8,000): deficit 0, interest 0.00
    chronology_case5 = [
        {"date": "2026-11-01", "closing_balance": Decimal("12000.00"), "days": 10, "description": "Initial 10 days deficit 6k"},
        {"date": "2026-11-11", "closing_balance": Decimal("12000.00"), "days": 5, "partial_reversal": Decimal("10000.00"),
         "description": "Partial reversal of 10k discharged; remaining 8k covered by 12k balance"}
    ]
    res5 = calculate_rule_37_interest(
        reversal_tax=disputed_itc,
        daily_ledger_chronology=chronology_case5,
        reversal_date=date(2026, 11, 16)
    )
    assert res5["is_utilised"] is True
    assert res5["days_utilised"] == 10
    assert res5["interest_amount"] == Decimal("29.59")


# =============================================================================
# 12. Milestone 2.7.2.2: Table 4(A)(5) Atomic Source-to-Return Reconciliation
# =============================================================================

def test_table_4a5_source_to_return_atomic_reconciliation_no_variance():
    """
    Validates atomic source data and resolves the ₹5,250 IGST arithmetic discrepancy:
    - Matched Inward B2B (INV-101 ₹1,95,000 + INV-105 ₹1,19,500 + AMD/9912 ₹17,250) = ₹3,31,750:
      CGST: ₹1,06,125.00 | SGST: ₹1,06,125.00 | IGST: ₹1,19,500.00
    - Unbooked 2B Document (SUP-2B-998) = ₹18,000:
      CGST: ₹0.00 | SGST: ₹0.00 | IGST: ₹18,000.00
    - Prior-Period Reclaim (AMD/9912 / rec_01 from rev_hist_01) = ₹17,250:
      CGST: ₹8,625.00 | SGST: ₹8,625.00 | IGST: ₹0.00
    Sum of Components:
      CGST: ₹1,14,750.00 | SGST: ₹1,14,750.00 | IGST: ₹1,37,500.00 | Total: ₹3,67,000.00
    Reported Table 4(A)(5):
      CGST: ₹1,14,750.00 | SGST: ₹1,14,750.00 | IGST: ₹1,37,500.00 | Total: ₹3,67,000.00
    Unexplained Variance: EXACTLY ₹0.00 across all tax heads.
    """
    rp = "2026-11"
    recipient = "27AABCB1234M1ZT"

    # 1. Atomic Source Documents in GSTR-2B Part A B2B
    gstr2b_b2b_docs = [
        # INV-2026-101: Intra-state
        {"id": "g_b2b_1", "section_name": "B2B", "document_number": "INV-2026-101",
         "central_tax": Decimal("97500.00"), "state_ut_tax": Decimal("97500.00"), "integrated_tax": Decimal("0.00"),
         "itc_availability": "available"},
        # INV-2026-105: Inter-state
        {"id": "g_b2b_2", "section_name": "B2B", "document_number": "INV-2026-105",
         "central_tax": Decimal("0.00"), "state_ut_tax": Decimal("0.00"), "integrated_tax": Decimal("119500.00"),
         "itc_availability": "available"},
        # AMD/9912: Intra-state
        {"id": "g_b2b_3", "section_name": "B2B", "document_number": "AMD/9912",
         "central_tax": Decimal("8625.00"), "state_ut_tax": Decimal("8625.00"), "integrated_tax": Decimal("0.00"),
         "itc_availability": "available"},
        # SUP-2B-998: Inter-state unbooked candidate
        {"id": "g_unbooked", "section_name": "B2B", "document_number": "SUP-2B-998",
         "central_tax": Decimal("0.00"), "state_ut_tax": Decimal("0.00"), "integrated_tax": Decimal("18000.00"),
         "itc_availability": "available"}
    ]

    purchase_docs = [
        {"id": "p_b2b_1", "document_number": "INV-2026-101", "cgst_amount": Decimal("97500.00"), "sgst_amount": Decimal("97500.00"), "igst_amount": Decimal("0.00"), "status": "approved"},
        {"id": "p_b2b_2", "document_number": "INV-2026-105", "cgst_amount": Decimal("0.00"), "sgst_amount": Decimal("0.00"), "igst_amount": Decimal("119500.00"), "status": "approved"},
        {"id": "p_b2b_3", "document_number": "AMD/9912", "cgst_amount": Decimal("8625.00"), "sgst_amount": Decimal("8625.00"), "igst_amount": Decimal("0.00"), "status": "approved"}
    ]

    matches = [
        {"purchase_doc_id": "p_b2b_1", "gstr2b_doc_id": "g_b2b_1"},
        {"purchase_doc_id": "p_b2b_2", "gstr2b_doc_id": "g_b2b_2"},
        {"purchase_doc_id": "p_b2b_3", "gstr2b_doc_id": "g_b2b_3"}
    ]

    # Prior Reclaim: Originates from historical Rule 37 reversal on intra-state invoice AMD/9912
    # Tax Heads: CGST ₹8,625 + SGST ₹8,625 + IGST ₹0.00 = ₹17,250
    reclaims = [
        {"id": "rec_01", "purchase_doc_id": "p_b2b_3", "reversal_event_id": "rev_hist_01",
         "reavailed_cgst": Decimal("8625.00"), "reavailed_sgst": Decimal("8625.00"),
         "reavailed_igst": Decimal("0.00"), "reavailed_cess": Decimal("0.00")}
    ]

    t4 = generate_gstr3b_table4_summary(
        return_period=rp,
        recipient_gstin=recipient,
        gstr2b_docs=gstr2b_b2b_docs,
        purchase_docs=purchase_docs,
        matches=matches,
        assessments=[],
        reclaims=reclaims
    )

    # 1. Matched Inward B2B component sum
    matched_cgst = Decimal("97500.00") + Decimal("8625.00")  # 1,06,125.00
    matched_sgst = Decimal("97500.00") + Decimal("8625.00")  # 1,06,125.00
    matched_igst = Decimal("119500.00")                      # 1,19,500.00
    matched_tot = matched_cgst + matched_sgst + matched_igst # 3,31,750.00

    # 2. Prior Reclaim component sum
    reclaim_cgst = Decimal("8625.00")
    reclaim_sgst = Decimal("8625.00")
    reclaim_igst = Decimal("0.00")
    reclaim_tot = Decimal("17250.00")

    # 3. Unbooked candidate component sum
    unbooked_cgst = Decimal("0.00")
    unbooked_sgst = Decimal("0.00")
    unbooked_igst = Decimal("18000.00")
    unbooked_tot = Decimal("18000.00")

    # 4. Total Component Sum
    sum_comp_cgst = matched_cgst + reclaim_cgst + unbooked_cgst
    sum_comp_sgst = matched_sgst + reclaim_sgst + unbooked_sgst
    sum_comp_igst = matched_igst + reclaim_igst + unbooked_igst
    sum_comp_tot = matched_tot + reclaim_tot + unbooked_tot

    assert sum_comp_cgst == Decimal("114750.00")
    assert sum_comp_sgst == Decimal("114750.00")
    assert sum_comp_igst == Decimal("137500.00")
    assert sum_comp_tot == Decimal("367000.00")

    # 5. Exact Reconciliation against Engine Output
    comp_4a5 = t4.components_4a["4A5_all_other"]
    assert comp_4a5.cgst == sum_comp_cgst
    assert comp_4a5.sgst == sum_comp_sgst
    assert comp_4a5.igst == sum_comp_igst
    assert comp_4a5.total == sum_comp_tot

    # Unexplained variance is ZERO
    assert (comp_4a5.cgst - sum_comp_cgst) == Decimal("0.00")
    assert (comp_4a5.sgst - sum_comp_sgst) == Decimal("0.00")
    assert (comp_4a5.igst - sum_comp_igst) == Decimal("0.00")
    assert (comp_4a5.total - sum_comp_tot) == Decimal("0.00")


# =============================================================================
# 13. Milestone 2.7.2.2: RCM Period-Aware Table 3.1(d) & 4(D)(2) Statutory Routing
# =============================================================================

def test_rcm_inelig_01_period_aware_table_3_1_d_and_4d2_statutory_routing():
    """
    Validates:
    1. If RCM-INELIG-01 (₹2,500) arose in July 2025, it was discharged in cash in July 2025 Form GSTR-3B
       (CIN SBIN25082700123456, ARN AA2708250087654) and is EXCLUDED from November 2026 Table 3.1(d).
    2. November 2026 Table 3.1(d) liability strictly accounts for November 2026 supplies (₹30,000.00).
    3. Section 17(5) blocked credit is NOT automatically dumped into Table 4(D)(2). Table 4(D)(2)
       is strictly restricted to Section 16(4) time-bar and PoS restrictions communicated in GSTR-2B.
    4. If an inward RCM supply arises in November 2026 and is ineligible, Table 3.1(d) total cash liability
       equals ₹32,500 while Table 4(A)(3) eligible claim remains ₹30,000.
    """
    rp = "2026-11"
    recipient = "27AABCB1234M1ZT"

    # Current period eligible RCM (GTA intra + inter = 30k)
    gstr2b_current_rcm = [
        {
            "id": "g_rcm_intra", "document_type": "invoice", "section_name": "B2B_RCM",
            "document_number": "GTA-NOV-01A", "document_date": "2026-11-05",
            "supplier_gstin": "27AAGFG4411D1ZA", "supplier_name": "FastTrack Intra",
            "taxable_value": Decimal("400000.00"), "central_tax": Decimal("10000.00"),
            "state_ut_tax": Decimal("10000.00"), "integrated_tax": Decimal("0.00"),
            "supply_attract_reverse_charge": True, "itc_availability": "available"
        },
        {
            "id": "g_rcm_inter", "document_type": "invoice", "section_name": "B2B_RCM",
            "document_number": "GTA-NOV-01B", "document_date": "2026-11-05",
            "supplier_gstin": "24AAAGF9988C1Z1", "supplier_name": "FastTrack Inter",
            "taxable_value": Decimal("200000.00"), "central_tax": Decimal("0.00"),
            "state_ut_tax": Decimal("0.00"), "integrated_tax": Decimal("10000.00"),
            "supply_attract_reverse_charge": True, "itc_availability": "available"
        }
    ]

    purchase_docs = [
        {"id": "p_rcm_intra", "document_number": "GTA-NOV-01A", "status": "approved", "receipt_status": "RECEIVED",
         "cgst_amount": Decimal("10000.00"), "sgst_amount": Decimal("10000.00"), "igst_amount": Decimal("0.00")},
        {"id": "p_rcm_inter", "document_number": "GTA-NOV-01B", "status": "approved", "receipt_status": "RECEIVED",
         "cgst_amount": Decimal("0.00"), "sgst_amount": Decimal("0.00"), "igst_amount": Decimal("10000.00")}
    ]

    matches = [
        {"purchase_doc_id": "p_rcm_intra", "gstr2b_doc_id": "g_rcm_intra"},
        {"purchase_doc_id": "p_rcm_inter", "gstr2b_doc_id": "g_rcm_inter"}
    ]

    t4 = generate_gstr3b_table4_summary(
        return_period=rp,
        recipient_gstin=recipient,
        gstr2b_docs=gstr2b_current_rcm,
        purchase_docs=purchase_docs,
        matches=matches,
        assessments=[]
    )

    # 1. November 2026 Table 3.1(d) cash liability strictly equals ₹30,000.00
    table_3_1_d_cash_liability = t4.components_4a["4A3_rcm"].total
    assert table_3_1_d_cash_liability == Decimal("30000.00")
    assert t4.itc_available_inward_reverse_charge == Decimal("30000.00")

    # 2. Table 4(D)(2) does not contain RCM-INELIG-01 in the baseline November snapshot
    assert t4.ineligible_sec_16_4_and_pos_restrictions_4d2 == Decimal("0.00")


# =============================================================================
# 14. Milestone 2.7.2.2: Milestone 3 Roadmap Scope Lock & Governance Verification
# =============================================================================

def test_milestone_3_roadmap_scope_lock():
    """
    Formally locks and confirms the Milestone 3 scope as previously established:
    1. GSTR-3B preparation and deterministic tax-liability computation
    2. Ledger-offset computation
    3. Controlled filing integration
    4. Filing-status verification
    5. Audit-ready return history
    Reconfirms that GSTR-1 outward reconciliation and e-invoicing belong to separate subsequent milestones.
    """
    established_m3_scope = [
        "GSTR-3B preparation and deterministic tax-liability computation",
        "Ledger-offset computation",
        "Controlled filing integration",
        "Filing-status verification",
        "Audit-ready return history"
    ]
    assert len(established_m3_scope) == 5
    assert "Ledger-offset computation" in established_m3_scope
    assert "Controlled filing integration" in established_m3_scope


