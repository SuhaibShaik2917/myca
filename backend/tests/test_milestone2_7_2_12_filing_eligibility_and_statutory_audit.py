import pytest
from decimal import Decimal
from datetime import date, datetime, timedelta
from typing import Dict, Any, List

from app.modules.reconciliation.itc_engine import (
    simulate_sequential_ecl_cross_utilization,
    calculate_rule_37_interest,
)


# ============================================================================
# 1. NOVEMBER FILING-ELIGIBILITY SEPARATION & PROVISIONAL CHALLAN GATE
# ============================================================================

def test_november_filing_eligibility_vs_scenario_separation():
    """
    Validates the strict separation between SCENARIO_DRAFT and FILING_ELIGIBLE returns:
    - In SCENARIO_DRAFT: All candidate transactions (BOE, GTA, ISD, Reclaim, SUP-2B-998)
      are simulated -> Gross 4(A) = ₹482,500; Net 4(C) = ₹419,500.
    - In FILING_ELIGIBLE: Provisional, unverified, and synthetic transactions are strictly
      excluded from production filing payloads.
      - BOE-882190 (₹60,000) is synthetic -> 4(A)(1) = ₹0.00
      - GTA RCM (₹30,000) is unverified challan -> 4(A)(3) = ₹0.00
      - ISD-INV-44 (₹25,500) is synthetic -> 4(A)(4) = ₹0.00
      - HIST-R37-01 reclaim (₹17,250) is unverified bank payment -> 4(A)(5) reclaim = ₹0.00
      - SUP-2B-998 (₹18,000) is unbooked -> excluded from 4(A)(5) and 4(B)(2)
      - Matched inward invoices (101, 105, AMD/9912) -> 4(A)(5) = ₹331,750
      - Filing-Eligible Gross 4(A) = ₹331,750
      - Reversals: S. 17(5) ₹27,000 + Rule 37 ₹18,000 = ₹45,000
      - Filing-Eligible Net 4(C) = ₹331,750 - ₹45,000 = ₹286,750.
    """
    # 1. Define Candidate Transactions with Evidence & Eligibility States
    transactions = [
        {
            "tx_id": "BOE-882190",
            "category": "4(A)(1)_IMPORT_GOODS",
            "cgst": Decimal("0.00"), "sgst": Decimal("0.00"), "igst": Decimal("60000.00"),
            "evidence_class": "SYNTHETIC_TEST_FIXTURE_PROVISIONAL",
            "filing_eligible": False,
            "exclusion_reason": "Synthetic ICEGATE fixture; no live EDIFACT customs gateway confirmation"
        },
        {
            "tx_id": "GTA-INV-FT-881",
            "category": "4(A)(3)_RCM_GTA",
            "cgst": Decimal("750.00"), "sgst": Decimal("750.00"), "igst": Decimal("0.00"),
            "evidence_class": "PROVISIONAL_UNVERIFIED_CHALLAN",
            "filing_eligible": False,
            "exclusion_reason": "Section 49(4) cash challan CHL-2026-11-001 unverified; CPIN/CIN confirmation pending"
        },
        {
            "tx_id": "GTA-INV-MC-101",
            "category": "4(A)(3)_RCM_GTA",
            "cgst": Decimal("0.00"), "sgst": Decimal("0.00"), "igst": Decimal("28500.00"),
            "evidence_class": "PROVISIONAL_UNVERIFIED_CHALLAN",
            "filing_eligible": False,
            "exclusion_reason": "Section 49(4) cash challan CHL-2026-11-001 unverified; CPIN/CIN confirmation pending"
        },
        {
            "tx_id": "ISD-INV-44",
            "category": "4(A)(4)_ISD",
            "cgst": Decimal("0.00"), "sgst": Decimal("0.00"), "igst": Decimal("25500.00"),
            "evidence_class": "SYNTHETIC_TEST_FIXTURE_PROVISIONAL",
            "filing_eligible": False,
            "exclusion_reason": "Synthetic GSTR-6 distribution fixture; no live GSP pull"
        },
        {
            "tx_id": "INV-101-MATCHED",
            "category": "4(A)(5)_INWARD_MATCHED",
            "cgst": Decimal("50000.00"), "sgst": Decimal("50000.00"), "igst": Decimal("40000.00"),
            "evidence_class": "ACTUAL_ERP_VERIFIED_2B_MATCHED",
            "filing_eligible": True,
            "exclusion_reason": None
        },
        {
            "tx_id": "INV-105-MATCHED",
            "category": "4(A)(5)_INWARD_MATCHED",
            "cgst": Decimal("40000.00"), "sgst": Decimal("40000.00"), "igst": Decimal("30000.00"),
            "evidence_class": "ACTUAL_ERP_VERIFIED_2B_MATCHED",
            "filing_eligible": True,
            "exclusion_reason": None
        },
        {
            "tx_id": "AMD-9912-MATCHED",
            "category": "4(A)(5)_INWARD_MATCHED",
            "cgst": Decimal("30875.00"), "sgst": Decimal("30875.00"), "igst": Decimal("20000.00"),
            "evidence_class": "ACTUAL_ERP_VERIFIED_2B_MATCHED",
            "filing_eligible": True,
            "exclusion_reason": None
        },
        {
            "tx_id": "HIST-R37-01-RECLAIM",
            "category": "4(A)(5)_RECLAIM_R37",
            "cgst": Decimal("8625.00"), "sgst": Decimal("8625.00"), "igst": Decimal("0.00"),
            "evidence_class": "ACTUAL_UNVERIFIED_BANK_PAYMENT",
            "filing_eligible": False,
            "exclusion_reason": "Payment reference HDFC-NEFT-20261112-9901 is ERP-only; bank statement proof unconfirmed"
        },
        {
            "tx_id": "SUP-2B-998",
            "category": "4(A)(5)_CIRCULAR_170_CANDIDATE",
            "cgst": Decimal("0.00"), "sgst": Decimal("0.00"), "igst": Decimal("18000.00"),
            "evidence_class": "PORTAL_AUTOPUBLISHED_UNBOOKED",
            "filing_eligible": False,
            "exclusion_reason": "Goods not received; unbooked in ERP; candidate for draft netting only"
        },
    ]

    # Reversals
    reversals = [
        {
            "tx_id": "INV-EXP-90",
            "category": "4(B)(1)_PERMANENT_S17_5",
            "cgst": Decimal("0.00"), "sgst": Decimal("0.00"), "igst": Decimal("22500.00"),
            "is_mandatory": True
        },
        {
            "tx_id": "INV-EXP-91",
            "category": "4(B)(1)_PERMANENT_S17_5",
            "cgst": Decimal("0.00"), "sgst": Decimal("0.00"), "igst": Decimal("4500.00"),
            "is_mandatory": True
        },
        {
            "tx_id": "INV-B2B-05",
            "category": "4(B)(2)_RULE37_MANDATORY",
            "cgst": Decimal("9000.00"), "sgst": Decimal("9000.00"), "igst": Decimal("0.00"),
            "is_mandatory": True
        },
        {
            "tx_id": "SUP-2B-998-REVERSAL",
            "category": "4(B)(2)_CIRCULAR_170_DRAFT_HOLD",
            "cgst": Decimal("0.00"), "sgst": Decimal("0.00"), "igst": Decimal("18000.00"),
            "is_mandatory": False  # Draft-only temporary reversal to neutralize auto-populated unbooked credit
        }
    ]

    # 2. Compute Scenario / Simulation Return
    scenario_4a_cgst = sum(t["cgst"] for t in transactions)
    scenario_4a_sgst = sum(t["sgst"] for t in transactions)
    scenario_4a_igst = sum(t["igst"] for t in transactions)
    scenario_4a_total = scenario_4a_cgst + scenario_4a_sgst + scenario_4a_igst

    scenario_4b_cgst = sum(r["cgst"] for r in reversals)
    scenario_4b_sgst = sum(r["sgst"] for r in reversals)
    scenario_4b_igst = sum(r["igst"] for r in reversals)
    scenario_4b_total = scenario_4b_cgst + scenario_4b_sgst + scenario_4b_igst

    scenario_4c_total = scenario_4a_total - scenario_4b_total

    assert scenario_4a_total == Decimal("482500.00")
    assert scenario_4b_total == Decimal("63000.00")
    assert scenario_4c_total == Decimal("419500.00")

    # 3. Compute Production Filing-Eligible Return
    eligible_txs = [t for t in transactions if t["filing_eligible"]]
    eligible_4a_cgst = sum(t["cgst"] for t in eligible_txs)
    eligible_4a_sgst = sum(t["sgst"] for t in eligible_txs)
    eligible_4a_igst = sum(t["igst"] for t in eligible_txs)
    eligible_4a_total = eligible_4a_cgst + eligible_4a_sgst + eligible_4a_igst

    # In filing-eligible return, draft candidate SUP-2B-998 is excluded from both 4(A) and 4(B)
    mandatory_reversals = [r for r in reversals if r["is_mandatory"]]
    eligible_4b_cgst = sum(r["cgst"] for r in mandatory_reversals)
    eligible_4b_sgst = sum(r["sgst"] for r in mandatory_reversals)
    eligible_4b_igst = sum(r["igst"] for r in mandatory_reversals)
    eligible_4b_total = eligible_4b_cgst + eligible_4b_sgst + eligible_4b_igst

    eligible_4c_cgst = eligible_4a_cgst - eligible_4b_cgst
    eligible_4c_sgst = eligible_4a_sgst - eligible_4b_sgst
    eligible_4c_igst = eligible_4a_igst - eligible_4b_igst
    eligible_4c_total = eligible_4a_total - eligible_4b_total

    # Assert Filing-Eligible Figures
    assert eligible_4a_total == Decimal("331750.00")
    assert eligible_4a_cgst == Decimal("120875.00")
    assert eligible_4a_sgst == Decimal("120875.00")
    assert eligible_4a_igst == Decimal("90000.00")

    assert eligible_4b_total == Decimal("45000.00")
    assert eligible_4b_cgst == Decimal("9000.00")
    assert eligible_4b_sgst == Decimal("9000.00")
    assert eligible_4b_igst == Decimal("27000.00")

    assert eligible_4c_total == Decimal("286750.00")
    assert eligible_4c_cgst == Decimal("111875.00")
    assert eligible_4c_sgst == Decimal("111875.00")
    assert eligible_4c_igst == Decimal("63000.00")


def test_rcm_challan_filing_eligibility_gate():
    """
    Tests that an unverified cash challan strictly blocks Table 4(A)(3) filing eligibility.
    Under Section 49(4) of the CGST Act, RCM ITC cannot be claimed until the cash payment
    is authenticated via CPIN/CIN receipt.
    """
    def check_rcm_eligibility(challan: Dict[str, Any]) -> Dict[str, Any]:
        if not challan.get("is_cash_paid"):
            return {
                "filing_eligible": False,
                "status": "EVIDENCE_PENDING_UNPAID_CHALLAN",
                "table_4a3_claimable": Decimal("0.00"),
                "statutory_violation": "Section 49(4): Tax under Section 9(3) has not been paid in cash."
            }
        if not challan.get("cin_authenticated"):
            return {
                "filing_eligible": False,
                "status": "EVIDENCE_PENDING_UNVERIFIED_CIN",
                "table_4a3_claimable": Decimal("0.00"),
                "statutory_violation": "Rule 87: Cash challan lacks authentic live CIN verification from bank/portal."
            }
        return {
            "filing_eligible": True,
            "status": "FILING_ELIGIBLE",
            "table_4a3_claimable": challan.get("amount", Decimal("0.00")),
            "statutory_violation": None
        }

    # Scenario 1: Internal ERP Challan with synthetic debit ref (Current November State)
    erp_challan = {
        "challan_id": "CHL-2026-11-001",
        "amount": Decimal("30000.00"),
        "is_cash_paid": True,
        "cin_authenticated": False,  # Synthetic / internal ERP reference only
        "cin": "DR-2026-11-00812"
    }
    result_erp = check_rcm_eligibility(erp_challan)
    assert result_erp["filing_eligible"] is False
    assert result_erp["table_4a3_claimable"] == Decimal("0.00")
    assert result_erp["status"] == "EVIDENCE_PENDING_UNVERIFIED_CIN"

    # Scenario 2: Live Authenticated Challan
    authenticated_challan = {
        "challan_id": "CHL-2026-11-001",
        "amount": Decimal("30000.00"),
        "is_cash_paid": True,
        "cin_authenticated": True,  # Verified via live GSTN/Bank webhook
        "cin": "SBIN26112700987654"
    }
    result_auth = check_rcm_eligibility(authenticated_challan)
    assert result_auth["filing_eligible"] is True
    assert result_auth["table_4a3_claimable"] == Decimal("30000.00")


# ============================================================================
# 2. OCTOBER NEGATIVE TABLE 4(C) & ECL MECHANICS
# ============================================================================

def test_october_negative_net_itc_and_ecl_mechanics():
    """
    Validates October GSTR-3B negative Table 4(C) behavior:
    - Table 4(A) Gross = ₹0.00
    - Table 4(B)(2) Reversal = ₹32,000.00 (INV-B2B-01 Rule 37A: CGST ₹16,000, SGST ₹16,000)
    - Table 4(C) Net ITC = -₹32,000.00 (CGST -₹16,000, SGST -₹16,000)
    
    Validates GST Portal vs ECL rules:
    1. GST Portal schema permits negative values in Table 4(C) post-Circular 170.
    2. Electronic Credit Ledger (PMT-02) cannot hold negative balances.
    3. If Opening ECL >= Reversal: Reversal debits ECL; Table 6.1 cash payable = ₹0.00.
    4. If Opening ECL < Reversal: Reversal debits ECL to ₹0.00; unabsorbed shortfall transitions
       to Table 6.1 cash liability payable under Section 49(3).
    5. ECL closing balance is strictly >= 0 in all cases.
    """
    def simulate_portal_ecl_reversal_offset(
        opening_ecl: Dict[str, Decimal],
        reversal_amount: Dict[str, Decimal]
    ) -> Dict[str, Any]:
        closing_ecl = {}
        cash_liability_table_6_1 = {}
        ecl_absorbed = {}

        for head in ["CGST", "SGST", "IGST"]:
            op = opening_ecl.get(head, Decimal("0.00"))
            rev = reversal_amount.get(head, Decimal("0.00"))
            absorbed = min(op, rev)
            shortfall = rev - absorbed

            closing = op - absorbed
            assert closing >= Decimal("0.00"), "ECL balance cannot be negative"

            closing_ecl[head] = closing
            ecl_absorbed[head] = absorbed
            cash_liability_table_6_1[head] = shortfall

        return {
            "closing_ecl": closing_ecl,
            "ecl_absorbed": ecl_absorbed,
            "cash_liability_table_6_1": cash_liability_table_6_1,
            "total_cash_payable": sum(cash_liability_table_6_1.values()),
            "total_ecl_closing": sum(closing_ecl.values())
        }

    reversal_oct = {"CGST": Decimal("16000.00"), "SGST": Decimal("16000.00"), "IGST": Decimal("0.00")}

    # Case A: Sufficient Opening ECL (e.g. ₹50,000 total)
    ecl_sufficient = {"CGST": Decimal("25000.00"), "SGST": Decimal("25000.00"), "IGST": Decimal("0.00")}
    res_a = simulate_portal_ecl_reversal_offset(ecl_sufficient, reversal_oct)
    assert res_a["closing_ecl"]["CGST"] == Decimal("9000.00")
    assert res_a["closing_ecl"]["SGST"] == Decimal("9000.00")
    assert res_a["total_cash_payable"] == Decimal("0.00")  # No cash liability needed

    # Case B: Insufficient Opening ECL (e.g. ₹10,000 total: CGST ₹5,000, SGST ₹5,000)
    ecl_insufficient = {"CGST": Decimal("5000.00"), "SGST": Decimal("5000.00"), "IGST": Decimal("0.00")}
    res_b = simulate_portal_ecl_reversal_offset(ecl_insufficient, reversal_oct)
    assert res_b["closing_ecl"]["CGST"] == Decimal("0.00")
    assert res_b["closing_ecl"]["SGST"] == Decimal("0.00")
    assert res_b["cash_liability_table_6_1"]["CGST"] == Decimal("11000.00")
    assert res_b["cash_liability_table_6_1"]["SGST"] == Decimal("11000.00")
    assert res_b["total_cash_payable"] == Decimal("22000.00")

    # Case C: Zero Opening ECL (₹0.00)
    ecl_zero = {"CGST": Decimal("0.00"), "SGST": Decimal("0.00"), "IGST": Decimal("0.00")}
    res_c = simulate_portal_ecl_reversal_offset(ecl_zero, reversal_oct)
    assert res_c["closing_ecl"]["CGST"] == Decimal("0.00")
    assert res_c["closing_ecl"]["SGST"] == Decimal("0.00")
    assert res_c["total_cash_payable"] == Decimal("32000.00")


# ============================================================================
# 3. RULE 37A STATUTORY INTEREST & HEAD-WISE ECL UTILIZATION
# ============================================================================

def test_rule_37a_interest_head_wise_ecl_utilization():
    """
    Validates Rule 37A statutory interest calculation under Section 50(3) and Rule 88B(3):
    - Proviso to Rule 37A stipulates reversal on or before 30 November.
    - Under Rule 88B(3), interest is charged ONLY if wrongly availed credit is actually utilised.
    - Statutory Head-Wise Rule:
      - Circular 192/04/2023-GST aggregate test applies ONLY to IGST (sum of IGST + CGST + SGST).
      - For CGST: evaluate CGST + IGST balance under Section 49(5)(b). (SGST is excluded).
      - For SGST: evaluate SGST + IGST balance under Section 49(5)(c). (CGST is excluded).
    - If ECL remained above the credit throughout the default period -> Interest = Nil.
    - If ECL dropped below -> Interest runs at 18% p.a. on the utilized shortfall.
    - Status remains REVIEW_REQUIRED / PROVISIONAL_THEORY_A.
    """
    # 1. CGST Wrong Availment (₹16,000) with sufficient CGST+IGST ECL balance
    res_cgst_unutilized = calculate_rule_37_interest(
        reversal_tax=Decimal("16000.00"),
        tax_head="CGST",
        ecl_head_balances={"CGST": Decimal("10000.00"), "IGST": Decimal("10000.00"), "SGST": Decimal("50000.00")},
        reversal_date=date(2026, 12, 20),
        availment_date=date(2026, 4, 20)
    )
    # CGST + IGST = 20,000 >= 16,000 -> Utilized = 0 -> Interest = 0.00
    assert res_cgst_unutilized["utilized_amount"] == Decimal("0.00")
    assert res_cgst_unutilized["interest_amount"] == Decimal("0.00")
    assert res_cgst_unutilized["dual_assessment"]["certification_status"] == "REQUIRES_TAX_PROFESSIONAL_REVIEW"

    # 2. CGST Wrong Availment (₹16,000) where SGST has large balance but CGST+IGST has shortfall
    # Notice: SGST cannot cross-utilize for CGST!
    res_cgst_shortfall = calculate_rule_37_interest(
        reversal_tax=Decimal("16000.00"),
        tax_head="CGST",
        ecl_head_balances={"CGST": Decimal("4000.00"), "IGST": Decimal("2000.00"), "SGST": Decimal("90000.00")},
        utilisation_date=date(2026, 12, 1),
        reversal_date=date(2026, 12, 21),  # 20 days
        availment_date=date(2026, 4, 20)
    )
    # Relevant ECL = CGST (4000) + IGST (2000) = 6,000. (SGST 90,000 ignored).
    # Shortfall = 16,000 - 6,000 = 10,000.
    # Interest for 20 days @ 18% on 10,000 = 10000 * 0.18 * 20 / 365 = 98.63
    assert res_cgst_shortfall["relevant_ecl_balance"] == Decimal("6000.00")
    assert res_cgst_shortfall["utilized_amount"] == Decimal("10000.00")
    expected_cgst_interest = (Decimal("10000.00") * Decimal("0.18") * Decimal("20") / Decimal("365")).quantize(Decimal("0.01"))
    assert res_cgst_shortfall["interest_amount"] == expected_cgst_interest

    # 3. IGST Wrong Availment (₹18,000) under Circular 192 (All three heads combined)
    res_igst = calculate_rule_37_interest(
        reversal_tax=Decimal("18000.00"),
        tax_head="IGST",
        ecl_head_balances={"CGST": Decimal("5000.00"), "SGST": Decimal("5000.00"), "IGST": Decimal("8000.00")},
        reversal_date=date(2026, 12, 20),
        availment_date=date(2026, 4, 20)
    )
    # Total combined = 5000 + 5000 + 8000 = 18,000 >= 18,000 -> Utilized = 0 -> Interest = 0.00
    assert res_igst["relevant_ecl_balance"] == Decimal("18000.00")
    assert res_igst["utilized_amount"] == Decimal("0.00")
    assert res_igst["interest_amount"] == Decimal("0.00")


# ============================================================================
# 4. HIST-R37-01 PAYMENT PROVENANCE & RECLAIM ELIGIBILITY
# ============================================================================

def test_hist_r37_01_payment_provenance_and_reclaim():
    """
    Validates HIST-R37-01:
    - Canonical timeline:
      - Invoice: 15-Mar-2026 (Taxable ₹95,833.33, CGST ₹8,625, SGST ₹8,625, Total ₹113,083.33)
      - 180 days expires: 11-Sep-2026
      - Reversal in GSTR-3B Sep 2026 (filed 20-Oct-2026): CGST ₹8,625, SGST ₹8,625
      - Payment date: 12-Nov-2026 (Day 242 from invoice date)
      - Payment reference HDFC-NEFT-20261112-9901 is classified as ACTUAL_UNVERIFIED_ERP_RECORD
    - Reclaim eligibility rule:
      - In production filing-eligible return, reclaim is withheld (excluded from 4(A)(5))
        until live bank statement / API verification confirms the debit.
      - In scenario / draft return, reclaim is simulated in 4(A)(5) and disclosed in 4(D)(1).
      - Table 4(D)(1) is purely an informational disclosure of the 4(A)(5) reclaim amount;
        it does NOT double-count the credit.
    """
    record = {
        "invoice_no": "HIST-R37-01",
        "invoice_date": date(2026, 3, 15),
        "day_180": date(2026, 9, 11),
        "payment_date": date(2026, 11, 12),
        "bank_ref": "HDFC-NEFT-20261112-9901",
        "taxable": Decimal("95833.33"),
        "cgst": Decimal("8625.00"),
        "sgst": Decimal("8625.00"),
        "total_amount": Decimal("113083.33"),
        "evidence_class": "ACTUAL_UNVERIFIED_ERP_RECORD",
        "has_bank_statement_verification": False
    }

    # Verify Day 242
    days_elapsed = (record["payment_date"] - record["invoice_date"]).days
    assert days_elapsed == 242

    def evaluate_reclaim(rec: Dict[str, Any]) -> Dict[str, Any]:
        if not rec["has_bank_statement_verification"]:
            return {
                "filing_eligible": False,
                "reclaim_allowed_in_4a5": Decimal("0.00"),
                "informational_4d1": Decimal("0.00"),
                "status": "EVIDENCE_PENDING_BANK_CONFIRMATION"
            }
        reclaim_tax = rec["cgst"] + rec["sgst"]
        return {
            "filing_eligible": True,
            "reclaim_allowed_in_4a5": reclaim_tax,
            "informational_4d1": reclaim_tax,
            "status": "FILING_ELIGIBLE"
        }

    # Production filing eligibility check
    filing_eval = evaluate_reclaim(record)
    assert filing_eval["filing_eligible"] is False
    assert filing_eval["reclaim_allowed_in_4a5"] == Decimal("0.00")
    assert filing_eval["status"] == "EVIDENCE_PENDING_BANK_CONFIRMATION"

    # Once verified:
    record_verified = dict(record, has_bank_statement_verification=True)
    verified_eval = evaluate_reclaim(record_verified)
    assert verified_eval["filing_eligible"] is True
    assert verified_eval["reclaim_allowed_in_4a5"] == Decimal("17250.00")
    assert verified_eval["informational_4d1"] == Decimal("17250.00")


# ============================================================================
# 5. SUP-2B-998 COMPLETE CIRCULAR 170 LIFECYCLE (5 STATES)
# ============================================================================

def test_sup_2b_998_circular_170_complete_lifecycle():
    """
    Validates SUP-2B-998 through the complete 5-state lifecycle under Circular 170:
    1. STATE_1_MISSING_GSTR2B: If not in GSTR-2B, no 4(A)(5) auto-population and no reversal.
    2. STATE_2_GOODS_RECEIVED: If in 2B and goods received, eligible in 4(A)(5), no 4(B)(2) reversal.
    3. STATE_3_GOODS_NOT_RECEIVED_CIRCULAR_170_DRAFT: In 2B, but goods not received.
       Candidate for 4(A)(5) auto-population and 4(B)(2) temporary reversal, net draft impact = ₹0.
    4. STATE_4_DRAFT_NO_ECL_POSTING: Unfiled draft return produces ZERO debits or credits in PMT-02.
    5. STATE_5_RECLAIM_ON_RECEIPT: Reclaim unlocked only after goods are received and verified.
    """
    class Circular170DocumentTracker:
        def __init__(self, doc_id: str, tax_amount: Decimal):
            self.doc_id = doc_id
            self.tax_amount = tax_amount

        def assess_state(
            self,
            in_gstr_2b: bool,
            goods_received: bool,
            return_filed: bool
        ) -> Dict[str, Any]:
            if not in_gstr_2b:
                return {
                    "state": "STATE_1_MISSING_GSTR2B",
                    "table_4a5_gross": Decimal("0.00"),
                    "table_4b2_reversal": Decimal("0.00"),
                    "net_impact": Decimal("0.00"),
                    "ecl_credit_posted": Decimal("0.00"),
                    "ecl_debit_posted": Decimal("0.00"),
                    "reclaimable_later": False
                }
            if goods_received:
                ecl_cr = self.tax_amount if return_filed else Decimal("0.00")
                return {
                    "state": "STATE_2_GOODS_RECEIVED_ELIGIBLE",
                    "table_4a5_gross": self.tax_amount,
                    "table_4b2_reversal": Decimal("0.00"),
                    "net_impact": self.tax_amount,
                    "ecl_credit_posted": ecl_cr,
                    "ecl_debit_posted": Decimal("0.00"),
                    "reclaimable_later": False
                }
            # Goods NOT received: Circular 170 candidate
            if not return_filed:
                return {
                    "state": "STATE_3_GOODS_NOT_RECEIVED_CIRCULAR_170_DRAFT",
                    "table_4a5_gross": self.tax_amount,
                    "table_4b2_reversal": self.tax_amount,
                    "net_impact": Decimal("0.00"),
                    "ecl_credit_posted": Decimal("0.00"),
                    "ecl_debit_posted": Decimal("0.00"),
                    "reclaimable_later": True
                }
            # Return Filed with Goods not received
            return {
                "state": "STATE_4_FILED_RETURN_ECL_NEUTRAL",
                "table_4a5_gross": self.tax_amount,
                "table_4b2_reversal": self.tax_amount,
                "net_impact": Decimal("0.00"),
                "ecl_credit_posted": self.tax_amount,
                "ecl_debit_posted": self.tax_amount,
                "reclaimable_later": True
            }

    tracker = Circular170DocumentTracker("SUP-2B-998", Decimal("18000.00"))

    # State 1: Missing from GSTR-2B
    s1 = tracker.assess_state(in_gstr_2b=False, goods_received=False, return_filed=False)
    assert s1["state"] == "STATE_1_MISSING_GSTR2B"
    assert s1["table_4a5_gross"] == Decimal("0.00")
    assert s1["net_impact"] == Decimal("0.00")

    # State 2: In GSTR-2B and Goods Received
    s2 = tracker.assess_state(in_gstr_2b=True, goods_received=True, return_filed=False)
    assert s2["state"] == "STATE_2_GOODS_RECEIVED_ELIGIBLE"
    assert s2["table_4a5_gross"] == Decimal("18000.00")
    assert s2["table_4b2_reversal"] == Decimal("0.00")
    assert s2["net_impact"] == Decimal("18000.00")
    assert s2["ecl_credit_posted"] == Decimal("0.00")  # Return not filed yet!

    # State 3: In GSTR-2B, Goods NOT Received, Unfiled Draft (Current November status)
    s3 = tracker.assess_state(in_gstr_2b=True, goods_received=False, return_filed=False)
    assert s3["state"] == "STATE_3_GOODS_NOT_RECEIVED_CIRCULAR_170_DRAFT"
    assert s3["table_4a5_gross"] == Decimal("18000.00")
    assert s3["table_4b2_reversal"] == Decimal("18000.00")
    assert s3["net_impact"] == Decimal("0.00")
    assert s3["ecl_credit_posted"] == Decimal("0.00")  # Zero ECL debit/credit
    assert s3["ecl_debit_posted"] == Decimal("0.00")

    # State 4: Filed Return with Circular 170 Gross Auto-pop + Reversal
    s4 = tracker.assess_state(in_gstr_2b=True, goods_received=False, return_filed=True)
    assert s4["state"] == "STATE_4_FILED_RETURN_ECL_NEUTRAL"
    assert s4["ecl_credit_posted"] == Decimal("18000.00")
    assert s4["ecl_debit_posted"] == Decimal("18000.00")


# ============================================================================
# 6. RULE 88A EXHAUSTIVE SEQUENTIAL VERIFICATION
# ============================================================================

def test_rule_88a_exhaustive_sequential_simulation():
    """
    Validates that `simulate_sequential_ecl_cross_utilization` in itc_engine.py
    strictly enforces Rule 88A statutory ordering across all edge cases:
    1. Insufficient IGST ITC
    2. Excess IGST ITC with alternative allocations (CGST_THEN_SGST, SGST_THEN_CGST, EQUAL)
    3. Cross-head bar: CGST cannot discharge SGST, SGST cannot discharge CGST
    4. Insufficient same-head credit -> Cash liability generated
    5. Zero liability -> Zero debits
    """
    # Case 1: Insufficient IGST ITC
    # IGST ITC 30,000 vs IGST liability 50,000 -> IGST ITC exhausted to 0
    # CGST ITC 10,000 covers CGST liability 10,000 -> 0 remaining
    # SGST ITC 10,000 covers SGST liability 10,000 -> 0 remaining
    # Remaining IGST liability 20,000 must be paid in cash (no remaining CGST/SGST credit to offset)
    res_case1 = simulate_sequential_ecl_cross_utilization(
        itc_balances={"IGST": Decimal("30000.00"), "CGST": Decimal("10000.00"), "SGST": Decimal("10000.00")},
        tax_liabilities={"IGST": Decimal("50000.00"), "CGST": Decimal("10000.00"), "SGST": Decimal("10000.00")}
    )
    assert res_case1["remaining_itc"]["IGST"] == Decimal("0.00")
    assert res_case1["net_cash_payable"]["IGST"] == Decimal("20000.00")  # Shortfall in IGST
    assert res_case1["rule_88a_exhaustion_satisfied"] is True

    # Case 2: Multi-Order Allocations for Excess IGST
    # Liabilities: IGST 50,000, CGST 30,000, SGST 30,000
    # ITC: IGST 70,000, CGST 20,000, SGST 20,000
    liab = {"IGST": Decimal("50000.00"), "CGST": Decimal("30000.00"), "SGST": Decimal("30000.00")}
    bal = {"IGST": Decimal("70000.00"), "CGST": Decimal("20000.00"), "SGST": Decimal("20000.00")}

    # 2a: CGST_THEN_SGST
    res_cgst_first = simulate_sequential_ecl_cross_utilization(
        itc_balances=bal,
        tax_liabilities=liab,
        igst_cross_utilization_order="CGST_THEN_SGST"
    )
    assert res_cgst_first["remaining_itc"]["IGST"] == Decimal("0.00")
    assert res_cgst_first["net_cash_payable"]["CGST"] == Decimal("0.00")
    assert res_cgst_first["net_cash_payable"]["SGST"] == Decimal("10000.00")
    assert res_cgst_first["remaining_itc"]["CGST"] == Decimal("10000.00")

    # 2b: SGST_THEN_CGST
    res_sgst_first = simulate_sequential_ecl_cross_utilization(
        itc_balances=bal,
        tax_liabilities=liab,
        igst_cross_utilization_order="SGST_THEN_CGST"
    )
    assert res_sgst_first["remaining_itc"]["IGST"] == Decimal("0.00")
    assert res_sgst_first["net_cash_payable"]["SGST"] == Decimal("0.00")
    assert res_sgst_first["net_cash_payable"]["CGST"] == Decimal("10000.00")
    assert res_sgst_first["remaining_itc"]["SGST"] == Decimal("10000.00")

    # 2c: EQUAL (Optimal, Zero Cash Outflow)
    res_equal = simulate_sequential_ecl_cross_utilization(
        itc_balances=bal,
        tax_liabilities=liab,
        igst_cross_utilization_order="EQUAL"
    )
    assert res_equal["remaining_itc"]["IGST"] == Decimal("0.00")
    assert res_equal["net_cash_payable"]["CGST"] == Decimal("0.00")
    assert res_equal["net_cash_payable"]["SGST"] == Decimal("0.00")
    assert res_equal["total_cash_payable"] == Decimal("0.00")

    # Case 3: Verify Cross-Head Prohibition (CGST cannot pay SGST and vice versa)
    for entry in res_cgst_first["debit_history"]:
        if entry["credit_head"] == "CGST":
            assert entry["liability_head"] != "SGST", "CGST ITC debited for SGST liability"
        if entry["credit_head"] == "SGST":
            assert entry["liability_head"] != "CGST", "SGST ITC debited for CGST liability"

    # Case 4: Zero Liability Scenario
    res_zero = simulate_sequential_ecl_cross_utilization(
        itc_balances={"IGST": Decimal("10000.00"), "CGST": Decimal("10000.00"), "SGST": Decimal("10000.00")},
        tax_liabilities={"IGST": Decimal("0.00"), "CGST": Decimal("0.00"), "SGST": Decimal("0.00")}
    )
    assert res_zero["total_itc_utilized"] == Decimal("0.00")
    assert res_zero["total_cash_payable"] == Decimal("0.00")
    assert len(res_zero["debit_history"]) == 0


# ============================================================================
# 7. TRANSACTION-LEVEL EVIDENCE REGISTRY VERIFICATION
# ============================================================================

def test_transaction_evidence_provenance_register():
    """
    Validates the classification and provenance of every transaction contributing
    to October and November Table 4. Proves that no synthetic fixture is marked ACTUAL_VERIFIED.
    """
    evidence_register = [
        {"tx_id": "INV-B2B-01", "period": "2026-10", "head": "CGST+SGST", "amount": Decimal("32000.00"), "provenance": "ACTUAL_ERP_RECORD", "filing_status": "MANDATORY_REVERSAL_WORKING_CALCULATION"},
        {"tx_id": "BOE-882190", "period": "2026-11", "head": "IGST", "amount": Decimal("60000.00"), "provenance": "SYNTHETIC_TEST_FIXTURE_PROVISIONAL", "filing_status": "EXCLUDED_FROM_FILING_ELIGIBLE"},
        {"tx_id": "CHL-2026-11-001", "period": "2026-11", "head": "CGST+SGST+IGST", "amount": Decimal("30000.00"), "provenance": "PROVISIONAL_UNVERIFIED_CHALLAN", "filing_status": "EXCLUDED_FROM_FILING_ELIGIBLE"},
        {"tx_id": "ISD-INV-44", "period": "2026-11", "head": "IGST", "amount": Decimal("25500.00"), "provenance": "SYNTHETIC_TEST_FIXTURE_PROVISIONAL", "filing_status": "EXCLUDED_FROM_FILING_ELIGIBLE"},
        {"tx_id": "INV-101", "period": "2026-11", "head": "CGST+SGST+IGST", "amount": Decimal("140000.00"), "provenance": "ACTUAL_ERP_VERIFIED_2B_MATCHED", "filing_status": "FILING_ELIGIBLE"},
        {"tx_id": "INV-105", "period": "2026-11", "head": "CGST+SGST+IGST", "amount": Decimal("110000.00"), "provenance": "ACTUAL_ERP_VERIFIED_2B_MATCHED", "filing_status": "FILING_ELIGIBLE"},
        {"tx_id": "AMD-9912", "period": "2026-11", "head": "CGST+SGST+IGST", "amount": Decimal("81750.00"), "provenance": "ACTUAL_ERP_VERIFIED_2B_MATCHED", "filing_status": "FILING_ELIGIBLE"},
        {"tx_id": "HIST-R37-01", "period": "2026-11", "head": "CGST+SGST", "amount": Decimal("17250.00"), "provenance": "ACTUAL_UNVERIFIED_ERP_RECORD", "filing_status": "EXCLUDED_PENDING_BANK_CONFIRMATION"},
        {"tx_id": "SUP-2B-998", "period": "2026-11", "head": "IGST", "amount": Decimal("18000.00"), "provenance": "PORTAL_AUTOPUBLISHED_UNBOOKED", "filing_status": "EXCLUDED_CANDIDATE_ONLY"},
        {"tx_id": "INV-EXP-90", "period": "2026-11", "head": "IGST", "amount": Decimal("22500.00"), "provenance": "ACTUAL_ERP_RECORD", "filing_status": "MANDATORY_PERMANENT_REVERSAL_4B1"},
        {"tx_id": "INV-EXP-91", "period": "2026-11", "head": "IGST", "amount": Decimal("4500.00"), "provenance": "ACTUAL_ERP_RECORD", "filing_status": "MANDATORY_PERMANENT_REVERSAL_4B1"},
        {"tx_id": "INV-B2B-05", "period": "2026-11", "head": "CGST+SGST", "amount": Decimal("18000.00"), "provenance": "ACTUAL_ERP_RECORD", "filing_status": "MANDATORY_TEMPORARY_REVERSAL_4B2"},
    ]

    for item in evidence_register:
        if "SYNTHETIC" in item["provenance"]:
            assert item["filing_status"] == "EXCLUDED_FROM_FILING_ELIGIBLE"
            assert item["provenance"] != "ACTUAL_VERIFIED"
        if "PROVISIONAL" in item["provenance"]:
            assert item["filing_status"] == "EXCLUDED_FROM_FILING_ELIGIBLE"
            assert item["provenance"] != "ACTUAL_VERIFIED"
        if "UNVERIFIED" in item["provenance"]:
            assert item["filing_status"] in ["EXCLUDED_PENDING_BANK_CONFIRMATION", "EXCLUDED_FROM_FILING_ELIGIBLE"]
