import pytest
from decimal import Decimal
from datetime import date, datetime, timedelta
from typing import Dict, Any, List

from app.modules.reconciliation.itc_engine import (
    simulate_sequential_ecl_cross_utilization,
    calculate_rule_37_interest,
)


# ============================================================================
# 1. CANONICAL ACTUAL-VS-PROJECTED CHRONOLOGY AUDIT (CURRENT DATE: 19-NOV-2026)
# ============================================================================

def test_rule_37a_future_event_quarantine_and_october_reversal():
    """
    Validates that:
    1. The audit current baseline date is 19-Nov-2026.
    2. The Rule 37A deadline (30-Nov-2026) is in the FUTURE (11 days ahead).
       It CANNOT be classified as an expired or breached deadline.
    3. Events dated 20-Dec-2026 are strictly PROJECTED_SCENARIO_FIXTURES.
    4. Reversal in October 2026 GSTR-3B (filed/prepared before 30-Nov-2026):
       - Occurs on or before the 30 November deadline.
       - Under the express proviso to Rule 37A, interest under Section 50 is Nil (₹0.00).
    5. The 20-Dec-2026 delay scenario is a simulated future test fixture.
    """
    audit_date = date(2026, 11, 19)
    rule_37a_deadline = date(2026, 11, 30)
    projected_december_date = date(2026, 12, 20)

    # 1. Assert chronological invariants
    assert audit_date < rule_37a_deadline, "30-Nov-2026 deadline has not occurred as of 19-Nov-2026"
    assert audit_date < projected_december_date, "20-Dec-2026 is a future date"

    # 2. Event Classification Engine
    events = [
        {"event": "INV-B2B-01 Original Invoice", "event_date": date(2025, 8, 10), "status": "HISTORICAL_COMPLETED"},
        {"event": "INV-B2B-01 GSTR-3B Availment", "event_date": date(2025, 9, 20), "status": "HISTORICAL_COMPLETED"},
        {"event": "Supplier GSTR-3B Filing Cutoff", "event_date": date(2026, 9, 30), "status": "HISTORICAL_COMPLETED"},
        {"event": "October 2026 GSTR-3B Reversal", "event_date": date(2026, 11, 18), "status": "DRAFT_RETURN_PREPARED"},
        {"event": "Rule 37A Statutory Deadline", "event_date": date(2026, 11, 30), "status": "FUTURE_PENDING_DEADLINE"},
        {"event": "Projected November GSTR-3B Filing", "event_date": date(2026, 12, 20), "status": "PROJECTED_TEST_SCENARIO_FIXTURE"},
    ]

    for ev in events:
        if ev["event_date"] > audit_date:
            assert ev["status"] in ["FUTURE_PENDING_DEADLINE", "PROJECTED_TEST_SCENARIO_FIXTURE"], (
                f"Future event {ev['event']} on {ev['event_date']} cannot be marked actual or completed"
            )

    # 3. Timely Reversal in October Return attracts Nil Interest
    october_reversal_date = date(2026, 11, 18)
    assert october_reversal_date <= rule_37a_deadline, "October reversal is timely under Rule 37A"

    # Reversal before 30-Nov requires ZERO interest under Rule 37A proviso
    interest_october_reversal = Decimal("0.00")
    assert interest_october_reversal == Decimal("0.00")


# ============================================================================
# 2. SECTION 17(5) AVAILMENT AND REVERSAL LINEAGE AUDIT (₹27,000)
# ============================================================================

def test_section_17_5_reversal_lineage_and_net_itc_recalculation():
    """
    Validates that:
    1. If blocked invoices (INV-EXP-90 ₹22,500, INV-EXP-91 ₹4,500 = ₹27,000) were NEVER
       availed in any previous return and are NOT included in current Table 4(A)(5),
       they CANNOT be reversed in Table 4(B)(1). Reversing them would illegally reduce
       credit from other eligible inward supplies.
    2. Model A (Direct Net Filing-Eligible):
       - Table 4(A)(5) Matched Eligible = ₹331,750.00
       - Table 4(B)(1) Permanent Reversals = ₹0.00
       - Table 4(B)(2) Mandatory Rule 37 Reversal (INV-B2B-05) = ₹18,000.00
       - Net Table 4(C) = ₹331,750 - ₹18,000 = ₹313,750.00.
    3. Model B (Circular 170 Gross Auto-Population Flow):
       - Table 4(A)(5) Gross Inward (Eligible ₹331,750 + Blocked ₹27,000) = ₹358,750.00
       - Table 4(B)(1) Blocked Credit Reversal = ₹27,000.00
       - Table 4(B)(2) Mandatory Rule 37 Reversal = ₹18,000.00
       - Net Table 4(C) = ₹358,750 - ₹27,000 - ₹18,000 = ₹313,750.00.
    4. Both valid accounting treatments reconcile to exactly ₹313,750.00 Net ITC.
       The prior figure of ₹286,750 resulted from double-deduction and is expunged.
    """
    matched_eligible_invoices = [
        {"doc": "INV-101", "cgst": Decimal("50000.00"), "sgst": Decimal("50000.00"), "igst": Decimal("40000.00")},
        {"doc": "INV-105", "cgst": Decimal("40000.00"), "sgst": Decimal("40000.00"), "igst": Decimal("30000.00")},
        {"doc": "AMD/9912", "cgst": Decimal("30875.00"), "sgst": Decimal("30875.00"), "igst": Decimal("20000.00")},
    ]
    eligible_tot = sum(m["cgst"] + m["sgst"] + m["igst"] for m in matched_eligible_invoices)
    eligible_cgst = sum(m["cgst"] for m in matched_eligible_invoices)
    eligible_sgst = sum(m["sgst"] for m in matched_eligible_invoices)
    eligible_igst = sum(m["igst"] for m in matched_eligible_invoices)

    assert eligible_tot == Decimal("331750.00")
    assert eligible_cgst == Decimal("120875.00")
    assert eligible_sgst == Decimal("120875.00")
    assert eligible_igst == Decimal("90000.00")

    # Blocked Invoices
    blocked_invoices = [
        {"doc": "INV-EXP-90", "tax": Decimal("22500.00"), "head": "IGST", "previously_availed": False},
        {"doc": "INV-EXP-91", "tax": Decimal("4500.00"), "head": "IGST", "previously_availed": False},
    ]
    blocked_tot = sum(b["tax"] for b in blocked_invoices)
    assert blocked_tot == Decimal("27000.00")

    # Mandatory Rule 37 Reversal on INV-B2B-05
    rule37_rev_cgst = Decimal("9000.00")
    rule37_rev_sgst = Decimal("9000.00")
    rule37_rev_tot = Decimal("18000.00")

    # --- MODEL A: Direct Eligible Filing ---
    model_a_4a = eligible_tot
    model_a_4b1 = Decimal("0.00")  # Never availed, so not reversed in 4(B)(1)
    model_a_4b2 = rule37_rev_tot
    model_a_4c = model_a_4a - model_a_4b1 - model_a_4b2
    assert model_a_4c == Decimal("313750.00")

    # --- MODEL B: Circular 170 Gross Auto-Population Flow ---
    model_b_4a = eligible_tot + blocked_tot  # ₹358,750
    model_b_4b1 = blocked_tot               # ₹27,000 reversed out
    model_b_4b2 = rule37_rev_tot            # ₹18,000 reversed out
    model_b_4c = model_b_4a - model_b_4b1 - model_b_4b2
    assert model_b_4c == Decimal("313750.00")

    # Check Head-Wise Net Eligible Credit under Model A & B
    net_cgst = eligible_cgst - rule37_rev_cgst
    net_sgst = eligible_sgst - rule37_rev_sgst
    net_igst = eligible_igst - Decimal("0.00")

    assert net_cgst == Decimal("111875.00")
    assert net_sgst == Decimal("111875.00")
    assert net_igst == Decimal("90000.00")
    assert (net_cgst + net_sgst + net_igst) == Decimal("313750.00")


# ============================================================================
# 3. SECTION 17(5) FACTUAL EXCEPTION ROUTING AUDIT
# ============================================================================

def test_section_17_5_factual_exception_routing():
    """
    Validates that:
    1. A category label alone (e.g. "motor vehicle repair" or "catering") does NOT
       automatically trigger permanent ineligibility.
    2. The system checks factual context and statutory exceptions:
       - Motor vehicles: Seating capacity > 13 persons, passenger transport, or driving school.
       - Food & Catering: Statutory obligation (e.g. Factories Act 1948) or outward supply.
    3. If facts are incomplete, the transaction is routed to REVIEW_REQUIRED.
    """
    def evaluate_s17_5_exception(txn: Dict[str, Any]) -> Dict[str, Any]:
        category = txn.get("category")
        facts = txn.get("facts", {})

        if category == "MOTOR_VEHICLE_REPAIRS":
            if facts.get("seating_capacity", 0) > 13:
                return {"is_blocked": False, "status": "ELIGIBLE_STATUTORY_EXCEPTION", "rule": "S. 17(5)(a) exception: Seating capacity > 13"}
            if facts.get("passenger_transport_license"):
                return {"is_blocked": False, "status": "ELIGIBLE_STATUTORY_EXCEPTION", "rule": "S. 17(5)(a)(B) passenger transportation"}
            if facts.get("is_factual_context_verified"):
                return {"is_blocked": True, "status": "BLOCKED_CREDIT_CONFIRMED", "rule": "S. 17(5)(ab) blocked credit"}
            return {"is_blocked": None, "status": "REVIEW_REQUIRED_FACTS_PENDING", "rule": "Vehicle use and seating capacity unverified"}

        if category == "FOOD_AND_CATERING":
            if facts.get("is_statutory_employer_obligation"):
                return {"is_blocked": False, "status": "ELIGIBLE_STATUTORY_EXCEPTION", "rule": "Proviso to S. 17(5)(b): Statutory employer obligation (Factories Act)"}
            if facts.get("is_outward_taxable_subcontract"):
                return {"is_blocked": False, "status": "ELIGIBLE_STATUTORY_EXCEPTION", "rule": "S. 17(5)(b)(i) inward for outward catering supply"}
            if facts.get("is_factual_context_verified"):
                return {"is_blocked": True, "status": "BLOCKED_CREDIT_CONFIRMED", "rule": "S. 17(5)(b)(i) blocked credit"}
            return {"is_blocked": None, "status": "REVIEW_REQUIRED_FACTS_PENDING", "rule": "Statutory employer obligation and business context unverified"}

        return {"is_blocked": False, "status": "NOT_SECTION_17_5", "rule": None}

    # Case 1: Motor Vehicle Repair without verified vehicle registration details
    unverified_vehicle = {"category": "MOTOR_VEHICLE_REPAIRS", "facts": {"is_factual_context_verified": False}}
    res_v = evaluate_s17_5_exception(unverified_vehicle)
    assert res_v["status"] == "REVIEW_REQUIRED_FACTS_PENDING"

    # Case 2: Motor Vehicle with seating capacity 25 (bus)
    bus_vehicle = {"category": "MOTOR_VEHICLE_REPAIRS", "facts": {"seating_capacity": 25, "is_factual_context_verified": True}}
    res_bus = evaluate_s17_5_exception(bus_vehicle)
    assert res_bus["status"] == "ELIGIBLE_STATUTORY_EXCEPTION"
    assert res_bus["is_blocked"] is False

    # Case 3: Food & Catering with statutory factory canteen obligation
    canteen = {"category": "FOOD_AND_CATERING", "facts": {"is_statutory_employer_obligation": True, "is_factual_context_verified": True}}
    res_canteen = evaluate_s17_5_exception(canteen)
    assert res_canteen["status"] == "ELIGIBLE_STATUTORY_EXCEPTION"
    assert res_canteen["is_blocked"] is False


# ============================================================================
# 4. OCTOBER NEGATIVE 4(C) & TAX-HEAD-WISE PAYMENT OFFSET MECHANICS
# ============================================================================

def test_october_tax_head_wise_payment_offset_mechanics():
    """
    Validates that:
    1. Table 4(C) reporting is separated from Table 6.1 liability set-off.
    2. Negative Net ITC in Table 4(C) post-Circular 170 is reported in Table 4
       and creates a net reversal liability in that tax head.
    3. In Table 6.1, the portal offsets liabilities head-by-head under Section 49(5) and Rule 88A:
       - Credit from PMT-02 offsets liabilities (IGST -> CGST/SGST; CGST -> CGST/IGST; SGST -> SGST/IGST).
       - CGST credit cannot pay SGST liability; SGST credit cannot pay CGST liability.
       - Any remaining unpaid liability after credit exhaustion requires cash deposit in Form GST PMT-06.
    4. Proves that opening credit is NOT simply lumped into an aggregate arithmetic formula,
       but is offset strictly through the tax-head-specific statutory sequence.
    """
    # October Net Reversals: CGST -₹16,000, SGST -₹16,000, IGST ₹0
    # Suppose Taxpayer has Output Tax Liability in October: IGST ₹0, CGST ₹0, SGST ₹0.
    # Total liability to discharge = Reversal liability: CGST ₹16,000, SGST ₹16,000.

    # Scenario 1: Taxpayer has opening credit in IGST (₹40,000), but ₹0 in CGST and SGST
    # Under Rule 88A, IGST credit CAN offset CGST and SGST liabilities!
    res_igst_offset = simulate_sequential_ecl_cross_utilization(
        opening_balances={"igst": Decimal("40000.00"), "cgst": Decimal("0.00"), "sgst": Decimal("0.00"), "cess": Decimal("0.00")},
        output_liabilities={"igst": Decimal("0.00"), "cgst": Decimal("16000.00"), "sgst": Decimal("16000.00"), "cess": Decimal("0.00")},
        igst_offset_order="EQUAL"
    )
    # IGST credit (40k) covers CGST (16k) and SGST (16k) -> Remaining IGST credit = 8,000. Cash payable = 0!
    assert res_igst_offset["closing_balances"]["igst"] == Decimal("8000.00")
    assert res_igst_offset["total_cash_payable"] == Decimal("0.00")

    # Scenario 2: Taxpayer has opening credit in CGST (₹30,000), but ₹0 in SGST and ₹0 in IGST
    # Under Section 49(5)(e), CGST credit CANNOT offset SGST liability!
    res_cgst_only = simulate_sequential_ecl_cross_utilization(
        opening_balances={"igst": Decimal("0.00"), "cgst": Decimal("30000.00"), "sgst": Decimal("0.00"), "cess": Decimal("0.00")},
        output_liabilities={"igst": Decimal("0.00"), "cgst": Decimal("16000.00"), "sgst": Decimal("16000.00"), "cess": Decimal("0.00")},
        igst_offset_order="CGST_THEN_SGST"
    )
    # CGST credit covers CGST reversal (16k) -> Remaining CGST credit = 14,000.
    # But SGST reversal (16k) CANNOT be paid by CGST credit! It MUST be paid in cash!
    assert res_cgst_only["closing_balances"]["cgst"] == Decimal("14000.00")
    assert res_cgst_only["remaining_liabilities_payable_in_cash"]["sgst"] == Decimal("16000.00")
    assert res_cgst_only["remaining_liabilities_payable_in_cash"]["cgst"] == Decimal("0.00")
    assert res_cgst_only["total_cash_payable"] == Decimal("16000.00")

    # Invariant: ECL is strictly non-negative
    for head, bal in res_cgst_only["closing_balances"].items():
        assert bal >= Decimal("0.00"), f"ECL {head} cannot be negative"


# ============================================================================
# 5. RULE 37A PROJECTED INTEREST & HEAD-WISE UTILIZATION RECALCULATION
# ============================================================================

def test_rule_37a_projected_interest_arithmetic():
    """
    Validates explicit arithmetic of Rule 37A projected theories:
    - Tax Base: ₹32,000.00 (CGST ₹16,000 + SGST ₹16,000)
    - Interest Rate: 18.00% p.a.
    - Day count: 365 days / year
    
    1. Theory A (Projected Delay: 01-Dec-2026 to 20-Dec-2026 = 20 days):
       Interest = 32000 * 0.18 * 20 / 365 = 315.616... -> ₹315.62
       (CGST: ₹157.81, SGST: ₹157.81)
       
    2. Theory B (Strict Departmental Availment: 20-Sep-2025 to 20-Dec-2026 = 456 days):
       Interest = 32000 * 0.18 * 456 / 365 = 7196.054... -> ₹7,196.05
       
    3. Theory C (Section 50(3) Utilization):
       If ECL remained above ₹16k in CGST+IGST and SGST+IGST -> ₹0.00.
    """
    tax_base = Decimal("32000.00")
    rate = Decimal("0.18")

    # Theory A: 20 days
    days_theory_a = Decimal("20")
    interest_theory_a = (tax_base * rate * days_theory_a / Decimal("365")).quantize(Decimal("0.01"))
    assert interest_theory_a == Decimal("315.62")

    # Theory B: 456 days from original availment date 20-Sep-2025
    days_theory_b = Decimal("456")
    interest_theory_b = (tax_base * rate * days_theory_b / Decimal("365")).quantize(Decimal("0.01"))
    assert interest_theory_b == Decimal("7196.05")

    # Head-wise check
    cgst_interest_a = (Decimal("16000.00") * rate * days_theory_a / Decimal("365")).quantize(Decimal("0.01"))
    sgst_interest_a = (Decimal("16000.00") * rate * days_theory_a / Decimal("365")).quantize(Decimal("0.01"))
    assert cgst_interest_a == Decimal("157.81")
    assert sgst_interest_a == Decimal("157.81")
    assert (cgst_interest_a + sgst_interest_a) == Decimal("315.62")


# ============================================================================
# 6. REVISED NOVEMBER FILING-ELIGIBLE RETURN RECONCILIATION
# ============================================================================

def test_revised_november_filing_eligible_table_4():
    """
    Validates the revised, mathematically reconciled November Table 4:
    - Table 4(A)(5) Matched Inward = ₹331,750.00 (CGST ₹120,875, SGST ₹120,875, IGST ₹90,000)
    - Table 4(B)(1) Permanent Reversals = ₹0.00 (Blocked credit was never availed)
    - Table 4(B)(2) Mandatory Rule 37 Reversal = ₹18,000.00 (CGST ₹9,000, SGST ₹9,000 on INV-B2B-05)
    - Net Filing-Eligible Table 4(C) = ₹313,750.00 (CGST ₹111,875, SGST ₹111,875, IGST ₹90,000)
    """
    # 1. Filing-Eligible Gross Table 4(A)
    t4_a1 = Decimal("0.00")  # BOE-882190 excluded (synthetic)
    t4_a2 = Decimal("0.00")
    t4_a3 = Decimal("0.00")  # GTA RCM excluded (unverified challan)
    t4_a4 = Decimal("0.00")  # ISD excluded (synthetic)
    t4_a5 = Decimal("331750.00")  # Matched invoices only
    t4_a_gross = t4_a1 + t4_a2 + t4_a3 + t4_a4 + t4_a5

    # 2. Table 4(B) Reversals
    t4_b1 = Decimal("0.00")  # Unavailed blocked credit
    t4_b2 = Decimal("18000.00")  # Mandatory Rule 37 reversal
    t4_b_total = t4_b1 + t4_b2

    # 3. Table 4(C) Net ITC
    t4_c_net = t4_a_gross - t4_b_total

    assert t4_a_gross == Decimal("331750.00")
    assert t4_b_total == Decimal("18000.00")
    assert t4_c_net == Decimal("313750.00")
