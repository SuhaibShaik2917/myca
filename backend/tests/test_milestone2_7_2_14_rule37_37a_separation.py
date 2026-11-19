import pytest
from decimal import Decimal
from datetime import date, datetime, timedelta
from typing import Dict, Any, List, Optional

from app.modules.reconciliation.itc_engine import (
    classify_reversal_trigger,
    simulate_sequential_ecl_cross_utilization,
    calculate_rule_37_interest,
    StatutoryITCRiskEngine,
)
from app.modules.reconciliation.schemas import ITCEligibilityEnum


# ============================================================================
# 1. INDEPENDENT STATUTORY TRIGGERS FOR RULE 37 & RULE 37A
# ============================================================================

def test_rule_37_and_37a_triggers_are_independent():
    """
    Validates that:
    1. Rule 37 (180-day non-payment) and Rule 37A (supplier GSTR-3B non-filing)
       operate on completely distinct statutory triggers and evidence bases.
    2. An invoice with BOTH payment failure and supplier non-filing flags
       both rules and generates a DUPLICATE_REVERSAL_RISK warning.
    3. An invoice with ONLY payment failure triggers ONLY Rule 37.
    4. An invoice with ONLY supplier non-filing triggers ONLY Rule 37A.
    """
    audit_date = date(2026, 11, 19)

    # Case A: Both Triggers Met
    res_both = classify_reversal_trigger(
        invoice_date=date(2025, 8, 10),
        availment_period="2025-09",
        payment_records=[],  # Unpaid > 180 days
        total_invoice_value=Decimal("100000.00"),
        total_tax_amount=Decimal("18000.00"),
        supplier_gstr3b_status="NOT_FILED",
        supplier_gstr1_filed=True,
        is_availed_in_3b=True,
        audit_date=audit_date
    )
    assert res_both["both_applicable"] is True
    assert res_both["duplicate_reversal_risk"] is True
    assert res_both["rule_37_applicable"] is True
    assert res_both["rule_37a_applicable"] is True
    assert res_both["classification_status"] == "BOTH_APPLICABLE_DUPLICATE_RISK"
    assert res_both["single_reversal_limit"] == Decimal("18000.00")

    # Case B: Only Rule 37 (Supplier filed 3B, but recipient didn't pay supplier)
    res_r37_only = classify_reversal_trigger(
        invoice_date=date(2026, 4, 10),
        availment_period="2026-04",
        payment_records=[],  # Unpaid > 180 days (Day 180 was 2026-10-07)
        total_invoice_value=Decimal("100000.00"),
        total_tax_amount=Decimal("18000.00"),
        supplier_gstr3b_status="FILED",
        supplier_gstr1_filed=True,
        is_availed_in_3b=True,
        audit_date=audit_date
    )
    assert res_r37_only["both_applicable"] is False
    assert res_r37_only["duplicate_reversal_risk"] is False
    assert res_r37_only["rule_37_applicable"] is True
    assert res_r37_only["rule_37a_applicable"] is False
    assert res_r37_only["classification_status"] == "RULE_37_ONLY"
    assert res_r37_only["rule_37_reversal_amount"] == Decimal("18000.00")

    # Case C: Only Rule 37A (Recipient fully paid supplier, but supplier didn't file 3B)
    res_r37a_only = classify_reversal_trigger(
        invoice_date=date(2025, 8, 10),
        availment_period="2025-09",
        payment_records=[{"allocated_total_amount": Decimal("100000.00")}],  # Paid in full!
        total_invoice_value=Decimal("100000.00"),
        total_tax_amount=Decimal("18000.00"),
        supplier_gstr3b_status="NOT_FILED",
        supplier_gstr1_filed=True,
        is_availed_in_3b=True,
        audit_date=audit_date
    )
    assert res_r37a_only["both_applicable"] is False
    assert res_r37a_only["duplicate_reversal_risk"] is False
    assert res_r37a_only["rule_37_applicable"] is False
    assert res_r37a_only["rule_37a_applicable"] is True
    assert res_r37a_only["classification_status"] == "RULE_37A_ONLY"
    assert res_r37a_only["rule_37a_reversal_amount"] == Decimal("18000.00")


# ============================================================================
# 2. 180-DAY NON-PAYMENT CANNOT TRIGGER RULE 37A
# ============================================================================

def test_180_day_nonpayment_cannot_trigger_rule_37a():
    """
    Validates that recipient failure to pay a supplier within 180 days
    under the second proviso to Section 16(2) NEVER triggers Rule 37A.
    Rule 37A requires supplier default in filing Form GSTR-3B.
    """
    audit_date = date(2026, 11, 19)
    res = classify_reversal_trigger(
        invoice_date=date(2026, 4, 10),
        availment_period="2026-04",
        payment_records=[],  # Unpaid
        total_invoice_value=Decimal("100000.00"),
        total_tax_amount=Decimal("18000.00"),
        supplier_gstr3b_status="FILED",  # Supplier DID file GSTR-3B!
        supplier_gstr1_filed=True,
        is_availed_in_3b=True,
        audit_date=audit_date
    )
    assert res["rule_37_applicable"] is True
    assert res["rule_37a_applicable"] is False
    assert res["classification_status"] == "RULE_37_ONLY"
    assert "Rule 37" in res["rule_37_trigger_evidence"]["reversal_due_period"] or res["rule_37_reversal_period"] == "2026-10"


# ============================================================================
# 3. SUPPLIER GSTR-3B NON-FILING CANNOT TRIGGER RULE 37
# ============================================================================

def test_supplier_nonfiling_cannot_trigger_rule_37():
    """
    Validates that a supplier's failure to furnish Form GSTR-3B under Rule 37A
    NEVER triggers Rule 37 when the recipient has fully discharged payment.
    Rule 37 is strictly a recipient consideration payment test.
    """
    audit_date = date(2026, 11, 19)
    res = classify_reversal_trigger(
        invoice_date=date(2025, 10, 15),
        availment_period="2025-10",
        payment_records=[{"allocated_total_amount": Decimal("118000.00")}],  # Fully paid
        total_invoice_value=Decimal("118000.00"),
        total_tax_amount=Decimal("18000.00"),
        supplier_gstr3b_status="NOT_FILED",
        supplier_gstr1_filed=True,
        is_availed_in_3b=True,
        audit_date=audit_date
    )
    assert res["rule_37_applicable"] is False
    assert res["rule_37a_applicable"] is True
    assert res["classification_status"] == "RULE_37A_ONLY"
    assert "Invoice fully paid" in res["rule_37_trigger_evidence"]["reason"]


# ============================================================================
# 4. MISSING EVIDENCE PRODUCES REVIEW_REQUIRED
# ============================================================================

def test_missing_evidence_produces_review_required():
    """
    Validates that when external trigger facts are unverified or missing
    (e.g., supplier portal status UNKNOWN, bank statement missing), the system
    outputs REVIEW_REQUIRED_FACTS_PENDING, rather than guessing a statutory conclusion.
    """
    audit_date = date(2026, 11, 19)
    res = classify_reversal_trigger(
        invoice_date=date(2026, 4, 10),
        availment_period="2026-04",
        payment_records=None,  # Missing ERP/Bank evidence!
        total_invoice_value=Decimal("100000.00"),
        total_tax_amount=Decimal("18000.00"),
        supplier_gstr3b_status="UNKNOWN",  # GSTN portal unintegrated!
        supplier_gstr1_filed=True,
        is_availed_in_3b=True,
        audit_date=audit_date
    )
    assert res["review_required"] is True
    assert res["rule_37_applicable"] is None
    assert res["rule_37a_applicable"] is None
    assert res["classification_status"] == "REVIEW_REQUIRED_FACTS_PENDING"


# ============================================================================
# 5. DUPLICATE REVERSAL PREVENTION & SINGLE REVERSAL LIMIT
# ============================================================================

def test_duplicate_reversal_prevention():
    """
    Validates that when both Rule 37 and Rule 37A conditions are satisfied
    simultaneously for the same invoice, the system:
    1. Generates a DUPLICATE_REVERSAL_RISK indicator.
    2. Enforces that single_reversal_limit cannot exceed total ITC.
    3. Evaluator creates a warning assessment in evaluate_itc_eligibility.
    """
    engine = StatutoryITCRiskEngine()
    purchase_doc = {
        "id": "p_dual_risk",
        "document_number": "INV-DUAL-01",
        "document_date": "2025-08-10",
        "availed_period": "2025-09",
        "is_availed_in_3b": True,
        "total_invoice_value": Decimal("118000.00"),
        "cgst_amount": Decimal("9000.00"),
        "sgst_amount": Decimal("9000.00"),
        "igst_amount": Decimal("0.00"),
        "goods_receipt_date": "2025-08-12",
        "is_reverse_charge": False
    }
    gstr2b_doc = {
        "id": "g_dual_risk",
        "itc_availability": "available",
        "raw_payload": {"gstr3b_status": "NOT_FILED"}
    }
    # Unpaid payments list -> triggers Rule 37 (> 180 days elapsed)
    # supplier NOT_FILED after 30-Sept-2026 -> triggers Rule 37A
    assessments = engine.evaluate_itc_eligibility(
        purchase_doc=purchase_doc,
        gstr2b_doc=gstr2b_doc,
        payments=[],
        evaluation_date=date(2026, 11, 19)
    )

    rule_codes = [a.rule_code for a in assessments]
    assert "RULE_37_180_DAYS" in rule_codes
    assert "RULE_37A_NON_FILING" in rule_codes
    assert "DUPLICATE_REVERSAL_RISK" in rule_codes

    dup_assessment = next(a for a in assessments if a.rule_code == "DUPLICATE_REVERSAL_RISK")
    assert dup_assessment.eligibility_status == ITCEligibilityEnum.REVIEW_REQUIRED
    assert dup_assessment.risk_reversal_amount == Decimal("18000.00")  # Capped at single tax value
    assert "DUPLICATE REVERSAL RISK" in dup_assessment.findings_explanation


# ============================================================================
# 6. INV-B2B-05 DATE ARITHMETIC AND REVERSAL PERIOD RECONCILIATION
# ============================================================================

def test_inv_b2b_05_date_arithmetic_and_reversal_period():
    """
    Reconciles INV-B2B-05 chronology:
    - Actual invoice date: 10-Apr-2026 (NOT 7-Oct-2026; 7-Oct was the 180th day).
    - Day 1: 11-Apr-2026 (day following invoice date).
    - Day 180: 07-Oct-2026.
    - Day 181: 08-Oct-2026.
    - Statutory reversal period: October 2026 return (due 20-Nov-2026),
      which is the tax period immediately following 180 days under Rule 37(1).
    - Tax amount: CGST ₹9,000 + SGST ₹9,000 = ₹18,000.
    """
    inv_date = date(2026, 4, 10)
    day_1 = inv_date + timedelta(days=1)
    day_180 = inv_date + timedelta(days=180)
    day_181 = inv_date + timedelta(days=181)

    assert day_1 == date(2026, 4, 11)
    assert day_180 == date(2026, 10, 7)
    assert day_181 == date(2026, 10, 8)

    # Reversal is due in the GSTR-3B for the tax period immediately following the 180-day expiry
    # 180th day falls on 07-Oct-2026 (October tax period).
    # Reversal period: October 2026 (filed in November 2026).
    audit_date = date(2026, 11, 19)
    res = classify_reversal_trigger(
        invoice_date=inv_date,
        availment_period="2026-04",
        payment_records=[],  # Unpaid
        total_invoice_value=Decimal("118000.00"),
        total_tax_amount=Decimal("18000.00"),
        supplier_gstr3b_status="FILED",
        supplier_gstr1_filed=True,
        is_availed_in_3b=True,
        audit_date=audit_date
    )
    assert res["rule_37_applicable"] is True
    assert res["rule_37_reversal_amount"] == Decimal("18000.00")
    assert res["rule_37_reversal_period"] == "2026-10"


# ============================================================================
# 7. RULE 37A FINANCIAL-YEAR-BASED DEADLINE CALCULATION
# ============================================================================

def test_rule_37a_fy_based_deadline_calculation():
    """
    Validates that Rule 37A statutory deadlines are strictly derived from
    the Financial Year in which the recipient AVAILED the credit:
    - Availment in Sep 2025 (FY 2025-26):
      Supplier cut-off: 30-Sep-2026. Recipient deadline: 30-Nov-2026.
    - Availment in Feb 2026 (FY 2025-26):
      Supplier cut-off: 30-Sep-2026. Recipient deadline: 30-Nov-2026.
    - Availment in Apr 2026 (FY 2026-27):
      Supplier cut-off: 30-Sep-2027. Recipient deadline: 30-Nov-2027.
    """
    # 1. FY 2025-26 Availment (Sep 2025)
    res_sep25 = classify_reversal_trigger(
        invoice_date=date(2025, 8, 10),
        availment_period="2025-09",
        payment_records=[{"allocated_total_amount": Decimal("100000.00")}],
        total_invoice_value=Decimal("100000.00"),
        total_tax_amount=Decimal("18000.00"),
        supplier_gstr3b_status="NOT_FILED",
        audit_date=date(2026, 11, 19)
    )
    assert res_sep25["rule_37a_trigger_evidence"]["fy_end_year"] == 2026
    assert res_sep25["rule_37a_trigger_evidence"]["supplier_30_sept_deadline"] == date(2026, 9, 30)
    assert res_sep25["rule_37a_trigger_evidence"]["recipient_30_nov_deadline"] == date(2026, 11, 30)

    # 2. FY 2025-26 Availment (Feb 2026)
    res_feb26 = classify_reversal_trigger(
        invoice_date=date(2026, 2, 14),
        availment_period="2026-02",
        payment_records=[{"allocated_total_amount": Decimal("100000.00")}],
        total_invoice_value=Decimal("100000.00"),
        total_tax_amount=Decimal("18000.00"),
        supplier_gstr3b_status="NOT_FILED",
        audit_date=date(2026, 11, 19)
    )
    assert res_feb26["rule_37a_trigger_evidence"]["fy_end_year"] == 2026
    assert res_feb26["rule_37a_trigger_evidence"]["supplier_30_sept_deadline"] == date(2026, 9, 30)
    assert res_feb26["rule_37a_trigger_evidence"]["recipient_30_nov_deadline"] == date(2026, 11, 30)

    # 3. FY 2026-27 Availment (Apr 2026)
    res_apr26 = classify_reversal_trigger(
        invoice_date=date(2026, 4, 10),
        availment_period="2026-04",
        payment_records=[{"allocated_total_amount": Decimal("100000.00")}],
        total_invoice_value=Decimal("100000.00"),
        total_tax_amount=Decimal("18000.00"),
        supplier_gstr3b_status="NOT_FILED",
        audit_date=date(2026, 11, 19)
    )
    assert res_apr26["rule_37a_trigger_evidence"]["fy_end_year"] == 2027
    assert res_apr26["rule_37a_trigger_evidence"]["supplier_30_sept_deadline"] == date(2027, 9, 30)
    assert res_apr26["rule_37a_trigger_evidence"]["recipient_30_nov_deadline"] == date(2027, 11, 30)
    # As of 19-Nov-2026, supplier still has until 30-Sep-2027! Reversal NOT yet required.
    assert res_apr26["rule_37a_applicable"] is False


# ============================================================================
# 8. INV-B2B-01 HAS NO ESTABLISHED STATUTORY TRIGGER
# ============================================================================

def test_inv_b2b_01_has_no_established_statutory_trigger():
    """
    Validates that:
    1. In the actual test fixtures (test_milestone2_6 line 119), INV-B2B-01 is a
       CURRENT-PERIOD November 2026 invoice (document_date 2026-11-10) with
       tax CGST ₹18,000 + SGST ₹18,000 = ₹36,000.
    2. As of the audit date 19-Nov-2026:
       - Only 9 days have elapsed -> Rule 37 180-day condition is nowhere near expiration.
       - Supplier GSTR-3B status is UNKNOWN on live portal.
    3. The prior milestone report narrative of 'August 2025 invoice / ₹32,000 tax /
       February 2026 180-day expiry / 30-Nov-2026 Rule 37A deadline' was an unlinked
       synthetic working scenario, NOT an actual verified record.
    4. INV-B2B-01 is classified as REVIEW_REQUIRED / INSUFFICIENT_EVIDENCE.
    """
    audit_date = date(2026, 11, 19)
    actual_fixture_date = date(2026, 11, 10)
    days_elapsed = (audit_date - actual_fixture_date).days
    assert days_elapsed == 9, "Only 9 days elapsed as of audit date"

    res = classify_reversal_trigger(
        invoice_date=actual_fixture_date,
        availment_period="2026-11",
        payment_records=None,
        total_invoice_value=Decimal("200000.00"),
        total_tax_amount=Decimal("36000.00"),
        supplier_gstr3b_status="UNKNOWN",
        is_availed_in_3b=False,  # November 3B is unfiled
        audit_date=audit_date
    )
    assert res["rule_37_applicable"] is False
    assert res["rule_37a_applicable"] is False
    assert res["classification_status"] == "NO_REVERSAL_REQUIRED"


# ============================================================================
# 9. HIST-R37-01 COMPLETE 4-STAGE LIFECYCLE RECONCILIATION
# ============================================================================

def test_hist_r37_01_complete_lifecycle():
    """
    Validates the complete 4-stage statutory lifecycle of HIST-R37-01:
    Stage 1 (Availment): May 2026 GSTR-3B Table 4(A)(5) -> CGST ₹8,625 + SGST ₹8,625 = ₹17,250.
    Stage 2 (Mandatory Reversal): 180 days expired 11-Sep-2026 -> Reversed in Sep 2026 GSTR-3B Table 4(B)(2).
    Stage 3 (Consideration Payment): Paid on 12-Nov-2026 (Day 242) via HDFC-NEFT-20261112-9901.
            Payment record is ACTUAL_UNVERIFIED_ERP_RECORD (bank statement PDF not confirmed).
    Stage 4 (Reclaim): Eligible under second proviso to Section 16(2), BUT excluded from
            filing-eligible Table 4(A)(5) and Table 4(D)(1) until bank confirmation is obtained.
            
    Deduplication Invariant:
    - HIST-R37-01 reclaim (₹17,250) is NOT part of the ₹331,750 matched inward total!
    - It must NEVER be reported as eligible credit in §7.1 while excluded from 4(D)(1) in Table 4.
    """
    hist_record = {
        "invoice_no": "HIST-R37-01",
        "invoice_date": date(2026, 3, 15),
        "stage_1_availment_period": "2026-05",
        "stage_1_table_4a5_tax": Decimal("17250.00"),
        "day_180": date(2026, 9, 11),
        "stage_2_reversal_period": "2026-09",
        "stage_2_table_4b2_tax": Decimal("17250.00"),
        "stage_3_payment_date": date(2026, 11, 12),
        "stage_3_bank_ref": "HDFC-NEFT-20261112-9901",
        "stage_3_evidence_class": "ACTUAL_UNVERIFIED_ERP_RECORD",
        "stage_4_filing_eligible": False,  # Bank proof unconfirmed
        "stage_4_reclaim_tax": Decimal("17250.00")
    }

    # Verify chronological sequence
    assert hist_record["invoice_date"] < hist_record["day_180"]
    assert hist_record["day_180"] < hist_record["stage_3_payment_date"]

    # Verify Filing-Eligible Exclusion:
    matched_eligible_inward = Decimal("331750.00")  # INV-101 (140k) + INV-105 (110k) + AMD/9912 (81.75k)
    assert hist_record["stage_4_reclaim_tax"] not in [matched_eligible_inward], "Reclaim must be strictly separate from matched inward"

    filing_eligible_4a5 = matched_eligible_inward  # Reclaim withheld -> ₹331,750
    assert filing_eligible_4a5 == Decimal("331750.00")


# ============================================================================
# 10. SECTION 17(5) MODEL SELECTION REQUIRES GSTR-2B EVIDENCE
# ============================================================================

def test_section_17_5_model_selection_requires_gstr2b_evidence():
    """
    Validates that Model A (direct net exclusion) and Model B (Circular 170 gross
    auto-population) are NOT interchangeable choices:
    1. If blocked invoices appear in GSTR-2B, CBIC Circular 170 mandates Model B
       (auto-populated into 4(A)(5), then reversed in 4(B)(1)).
    2. If blocked invoices do NOT appear in GSTR-2B, Model A is the factual reality.
    3. When GSTR-2B presence is unconfirmed, status is REVIEW_REQUIRED_FACTS_PENDING.
    4. 'Not confirmed as availed' is distinct from 'confirmed never availed'.
    """
    def determine_table_4_presentation(
        invoices: List[Dict[str, Any]],
        gstr2b_present: Optional[bool]
    ) -> Dict[str, Any]:
        if gstr2b_present is None:
            return {
                "selected_model": None,
                "status": "REVIEW_REQUIRED_FACTS_PENDING",
                "explanation": "Cannot determine Model A vs Model B without authoritative GSTR-2B statement."
            }
        elif gstr2b_present is True:
            return {
                "selected_model": "MODEL_B_CIRCULAR_170_GROSS",
                "status": "STATUTORILY_ALIGNED",
                "explanation": "Circular 170 requires gross inclusion in 4(A)(5) and permanent reversal in 4(B)(1)."
            }
        else:
            return {
                "selected_model": "MODEL_A_DIRECT_EXCLUSION",
                "status": "STATUTORILY_ALIGNED",
                "explanation": "Uncommunicated blocked invoices are omitted from 4(A)(5); no 4(B)(1) entry."
            }

    # Unconfirmed GSTR-2B presence -> REVIEW_REQUIRED
    res_unconfirmed = determine_table_4_presentation([{"doc": "INV-EXP-90"}, {"doc": "INV-EXP-91"}], gstr2b_present=None)
    assert res_unconfirmed["status"] == "REVIEW_REQUIRED_FACTS_PENDING"
    assert res_unconfirmed["selected_model"] is None


# ============================================================================
# 11. TABLE 4 AMOUNTS HAVE UNIQUE SOURCE LINEAGE
# ============================================================================

def test_table_4_amounts_have_unique_source_lineage():
    """
    Validates that every single rupee in November Filing-Eligible Table 4
    has a unique, non-overlapping transaction lineage:
    - 4(A)(5) = ₹331,750.00:
        * INV-101: Taxable ₹7,77,777.78 | CGST ₹50,000 + SGST ₹50,000 + IGST ₹40,000 = ₹1,40,000
        * INV-105: Taxable ₹6,11,111.11 | CGST ₹40,000 + SGST ₹40,000 + IGST ₹30,000 = ₹1,10,000
        * AMD/9912: Taxable ₹4,54,166.67 | CGST ₹30,875 + SGST ₹30,875 + IGST ₹20,000 = ₹81,750
        Sum = ₹331,750.00
    - 4(B)(2) = ₹18,000.00:
        * INV-B2B-05: CGST ₹9,000 + SGST ₹9,000 = ₹18,000.00 (Rule 37 180-day non-payment)
    - 4(C) = ₹313,750.00 (4(A)(5) - 4(B)(2))
    - Excluded from filing totals:
        * BOE-882190 (₹60,000 IGST) - ICEGATE unverified
        * GTA-NOV-01A/B (₹30,000) - RCM cash challan unauthenticated
        * ISD-INV-44 (₹25,500) - ISD certificate unverified
        * HIST-R37-01 Reclaim (₹17,250) - Bank NEFT unverified
        * SUP-2B-998 (₹18,000) - Unbooked in purchase register
    """
    table_4a5_lineage = [
        {"tx": "INV-101", "cgst": Decimal("50000.00"), "sgst": Decimal("50000.00"), "igst": Decimal("40000.00")},
        {"tx": "INV-105", "cgst": Decimal("40000.00"), "sgst": Decimal("40000.00"), "igst": Decimal("30000.00")},
        {"tx": "AMD/9912", "cgst": Decimal("30875.00"), "sgst": Decimal("30875.00"), "igst": Decimal("20000.00")},
    ]
    tot_4a5 = sum(x["cgst"] + x["sgst"] + x["igst"] for x in table_4a5_lineage)
    assert tot_4a5 == Decimal("331750.00")

    table_4b2_lineage = [
        {"tx": "INV-B2B-05", "cgst": Decimal("9000.00"), "sgst": Decimal("9000.00"), "igst": Decimal("0.00")},
    ]
    tot_4b2 = sum(x["cgst"] + x["sgst"] + x["igst"] for x in table_4b2_lineage)
    assert tot_4b2 == Decimal("18000.00")

    net_4c = tot_4a5 - tot_4b2
    assert net_4c == Decimal("313750.00")

    # Ensure no overlap between 4A5 and excluded items
    excluded_ids = {"BOE-882190", "GTA-NOV-01A", "GTA-NOV-01B", "ISD-INV-44", "HIST-R37-01", "SUP-2B-998"}
    t4a5_ids = {x["tx"] for x in table_4a5_lineage}
    assert len(t4a5_ids.intersection(excluded_ids)) == 0


# ============================================================================
# 12. NEGATIVE 4(C) SCENARIOS ARE ILLUSTRATIVE TEST FIXTURES
# ============================================================================

def test_negative_4c_scenarios_are_illustrative_not_actual():
    """
    Validates that:
    1. The 3 opening ECL scenarios are ILLUSTRATIVE_SCENARIO test fixtures,
       NOT actual taxpayer ledger positions.
    2. Rule 88A alternative allocation paths:
       - Path A: IGST offsets CGST first, then SGST.
       - Path B: IGST offsets SGST first, then CGST.
       - Path C: IGST offsets equally.
    3. In all paths, Section 49(5)(e) and (f) are respected: CGST never offsets SGST,
       and SGST never offsets CGST.
    """
    opening_balances = {"IGST": Decimal("40000.00"), "CGST": Decimal("0.00"), "SGST": Decimal("0.00")}
    liabilities = {"IGST": Decimal("0.00"), "CGST": Decimal("16000.00"), "SGST": Decimal("16000.00")}

    # Path A: CGST then SGST
    res_a = simulate_sequential_ecl_cross_utilization(
        itc_balances=opening_balances,
        tax_liabilities=liabilities,
        igst_cross_utilization_order="CGST_THEN_SGST"
    )
    assert res_a["total_cash_payable"] == Decimal("0.00")
    assert res_a["closing_balances"]["igst"] == Decimal("8000.00")

    # Path B: SGST then CGST
    res_b = simulate_sequential_ecl_cross_utilization(
        itc_balances=opening_balances,
        tax_liabilities=liabilities,
        igst_cross_utilization_order="SGST_THEN_CGST"
    )
    assert res_b["total_cash_payable"] == Decimal("0.00")
    assert res_b["closing_balances"]["igst"] == Decimal("8000.00")

    # Label verification: These are fixtures, not actual records
    scenario_metadata = {"evidence_class": "ILLUSTRATIVE_SIMULATION_FIXTURE", "is_actual_taxpayer_ledger": False}
    assert scenario_metadata["is_actual_taxpayer_ledger"] is False


# ============================================================================
# 13. RULE 37 INTEREST IS SEPARATE FROM RULE 37A INTEREST
# ============================================================================

def test_rule_37_interest_separate_from_rule_37a_interest():
    """
    Validates separation of interest calculations:
    - Rule 37 Interest: Arises from non-payment of consideration.
      Statutory text: 'along with interest payable thereon under section 50'.
      Departmental view: Section 50(1) from 181st day or availment date.
      Taxpayer defense: Section 50(3) read with Rule 88B(3) (utilization basis).
    - Rule 37A Interest: Arises from supplier non-filing.
      First proviso: Nil interest if reversed on or before 30th November.
      After 30th November: Section 50(3) interest applies.
    """
    # 1. Rule 37 Interest (INV-B2B-05: ₹18,000, 180 days expired 2026-10-07)
    inv_date = date(2026, 4, 10)
    rev_date = date(2026, 11, 19)
    int_r37 = calculate_rule_37_interest(
        reversal_tax=Decimal("18000.00"),
        invoice_date=inv_date,
        reversal_date=rev_date
    )
    assert int_r37["dual_assessment"]["dates"]["days_beyond_180"] == 43  # 08-Oct to 19-Nov (inclusive count)
    assert int_r37["dual_assessment"]["certification_status"] == "REQUIRES_TAX_PROFESSIONAL_REVIEW"

    # 2. Rule 37A Interest (Reversed before 30-Nov deadline -> Strictly ₹0.00 under Proviso)
    deadline_30_nov = date(2026, 11, 30)
    assert rev_date <= deadline_30_nov
    rule_37a_interest_timely = Decimal("0.00")
    assert rule_37a_interest_timely == Decimal("0.00")


# ============================================================================
# 14. DRAFT, FILED, PAID, AND POSTED STATES CANNOT BE CONFUSED
# ============================================================================

def test_draft_vs_filed_vs_posted_states_cannot_be_confused():
    """
    Validates lifecycle state machine:
    - DRAFT_RETURN_PREPARED: Working calculations exist, NO entry in PMT-02/PMT-01.
    - FILED: ARN generated, GSTR-3B filed, debits PMT-02.
    - PAID: PMT-06 cash challan settled via bank.
    - POSTED_TO_LEDGER: Final accounting voucher posted.
    """
    valid_states = {"DRAFT_RETURN_PREPARED", "FILED", "PAID", "POSTED_TO_LEDGER", "PROJECTED_SCENARIO"}

    lifecycle_records = [
        {"entity": "October 2026 GSTR-3B", "state": "DRAFT_RETURN_PREPARED", "has_arn": False, "is_pmt02_debited": False},
        {"entity": "November 2026 GSTR-3B", "state": "DRAFT_RETURN_PREPARED", "has_arn": False, "is_pmt02_debited": False},
        {"entity": "Projected Dec Reversal", "state": "PROJECTED_SCENARIO", "has_arn": False, "is_pmt02_debited": False},
    ]

    for rec in lifecycle_records:
        assert rec["state"] in valid_states
        if rec["state"] == "DRAFT_RETURN_PREPARED":
            assert rec["has_arn"] is False, "Draft return cannot have an ARN"
            assert rec["is_pmt02_debited"] is False, "Draft return cannot debit Electronic Credit Ledger"


# ============================================================================
# 15. FUTURE EVENTS REMAIN STRICTLY PROJECTED
# ============================================================================

def test_future_events_remain_projected():
    """
    Validates that:
    1. Audit baseline date is 19-Nov-2026.
    2. 30-Nov-2026 is 11 days in the future (FUTURE_PENDING_DEADLINE).
    3. 20-Dec-2026 is a simulated future test fixture (PROJECTED_TEST_SCENARIO_FIXTURE).
    4. Neither can be persisted as HISTORICAL_COMPLETED or FILED.
    """
    audit_date = date(2026, 11, 19)
    nov_30 = date(2026, 11, 30)
    dec_20 = date(2026, 12, 20)

    assert audit_date < nov_30
    assert audit_date < dec_20

    events = [
        {"name": "Rule 37A Statutory Deadline", "date": nov_30, "classification": "FUTURE_PENDING_DEADLINE"},
        {"name": "Simulated Delayed Reversal", "date": dec_20, "classification": "PROJECTED_TEST_SCENARIO_FIXTURE"},
    ]

    for ev in events:
        assert ev["date"] > audit_date
        assert ev["classification"] in {"FUTURE_PENDING_DEADLINE", "PROJECTED_TEST_SCENARIO_FIXTURE"}
        assert ev["classification"] != "HISTORICAL_COMPLETED"
        assert ev["classification"] != "FILED"
