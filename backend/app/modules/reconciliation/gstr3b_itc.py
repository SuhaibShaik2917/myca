import logging
from decimal import Decimal
from typing import List, Dict, Any, Optional
from .schemas import GSTR3BTable4WorkingOutput, ITCEligibilityEnum

logger = logging.getLogger("reconciliation.gstr3b_itc")


def generate_gstr3b_table4_summary(
    return_period: str,
    recipient_gstin: str,
    gstr2b_docs: List[Dict[str, Any]],
    purchase_docs: List[Dict[str, Any]],
    matches: List[Dict[str, Any]],
    assessments: List[Dict[str, Any]],
    human_approved_ids: Optional[set] = None,
    reclaims: Optional[List[Dict[str, Any]]] = None
) -> GSTR3BTable4WorkingOutput:
    """
    Generates statutory GSTR-3B Table 4 Inward ITC Working Output strictly compliant with
    CBIC Circular No. 170/02/2022-GST, Notification No. 14/2022-CT, and GST Portal Specifications.

    Statutory Mapping:
    - 4(A)(1): Import of Goods (IMPG / IMPGSEZ from ICEGATE)
    - 4(A)(2): Import of Services
    - 4(A)(3): Inward supplies liable to reverse charge (RCM)
    - 4(A)(4): Inward supplies from ISD
    - 4(A)(5): All other ITC (Registered B2B, includes amounts to be reversed under 4(B)(1))
    - 4(B)(1): Permanent / Non-reclaimable reversals under Rules 38, 42, 43 and Section 17(5)
    - 4(B)(2): Temporary / Reclaimable reversals (Rule 37 180 days, Rule 37A supplier non-filing, etc.)
    - 4(C):    Net ITC Available = 4(A) - 4(B)(1) - 4(B)(2)
    - 4(D)(1): Reclaimed ITC in current period after earlier reversal under Table 4(B)(2) (NEVER Section 17(5))
    - 4(D)(2): Ineligible credit under Section 16(4) & PoS restrictions (from GSTR-2B unavailable, not in 4(A))

    Distinct Subtotals (Calculation Provenance):
    1. GSTR-2B Statement Total Available
    2. Book Matched Total
    3. Legally Assessed Working Total
    4. Human Approved Total
    """
    approved_set = human_approved_ids or set()

    itc_avail_impg = Decimal("0.00")
    itc_avail_imps = Decimal("0.00")
    itc_avail_rcm = Decimal("0.00")
    itc_avail_isd = Decimal("0.00")
    itc_avail_all_other = Decimal("0.00")

    subtotal_portal_avail = Decimal("0.00")
    subtotal_books_matched = Decimal("0.00")
    ineligible_portal_4d2 = Decimal("0.00")

    # 1. Tally GSTR-2B statement components into Table 4(A) and Table 4(D)(2)
    for doc in gstr2b_docs:
        itc_status = doc.get("itc_availability", "available")
        tax = Decimal(str(
            (doc.get("integrated_tax") or 0) +
            (doc.get("central_tax") or 0) +
            (doc.get("state_ut_tax") or 0) +
            (doc.get("cess_amount") or 0)
        ))
        sec = (doc.get("section_name") or "").upper()

        if itc_status == "available":
            subtotal_portal_avail += tax
            if sec in ("IMPG", "IMPGA", "IMPGSEZ", "IMPGSEZA"):
                itc_avail_impg += tax
            elif sec in ("IMPS", "IMPSA") or sec == "IMPORT_SERVICES":
                itc_avail_imps += tax
            elif doc.get("supply_attract_reverse_charge") or sec == "RCM":
                itc_avail_rcm += tax
            elif sec in ("ISD", "ISDA"):
                itc_avail_isd += tax
            else:
                itc_avail_all_other += tax
        else:
            # GSTR-2B Table 4 'ITC Not Available' (Section 16(4) time-barred, PoS state mismatches)
            ineligible_portal_4d2 += tax

    total_4a = itc_avail_impg + itc_avail_imps + itc_avail_rcm + itc_avail_isd + itc_avail_all_other

    # 2. Tally Book Matched Total (inward purchase documents reconciled against GSTR-2B)
    matched_purchase_map = {m["purchase_doc_id"]: m for m in matches}
    for p_doc in purchase_docs:
        p_id = str(p_doc["id"])
        tax = Decimal(str(
            (p_doc.get("cgst_amount") or 0) +
            (p_doc.get("sgst_amount") or 0) +
            (p_doc.get("igst_amount") or 0) +
            (p_doc.get("cess_amount") or 0)
        ))
        if p_id in matched_purchase_map:
            subtotal_books_matched += tax

    # 3. Tally Statutory Risk Reversals strictly into 4(B)(1) and 4(B)(2)
    reversed_sec_17_5_and_rules = Decimal("0.00")  # 4(B)(1) Non-reclaimable
    reversed_rule_37 = Decimal("0.00")             # 4(B)(2) Reclaimable
    reversed_rule_37a = Decimal("0.00")            # 4(B)(2) Reclaimable
    reversed_other = Decimal("0.00")               # 4(B)(2) Reclaimable

    for a in assessments:
        rule = a.get("rule_code", "")
        status = a.get("eligibility_status")
        rev_amt = Decimal(str(a.get("risk_reversal_amount") or 0))

        if rule.startswith("SEC_17_5") or rule in ("RULE_38", "RULE_42", "RULE_43") or status == ITCEligibilityEnum.BLOCKED_CREDIT_INDICATOR:
            # Table 4(B)(1): Rules 38, 42, 43 and Section 17(5) Permanent Reversals
            reversed_sec_17_5_and_rules += rev_amt
        elif rule == "RULE_37_180_DAYS" or status == ITCEligibilityEnum.PAYMENT_180_DAY_RISK:
            # Table 4(B)(2): Rule 37 180-Day Vendor Payment Reversal (Reclaimable upon payment)
            reversed_rule_37 += rev_amt
        elif rule == "RULE_37A_NON_FILING" or status == ITCEligibilityEnum.RULE_37A_RISK:
            # Table 4(B)(2): Rule 37A Supplier Non-Filing Reversal (Reclaimable upon supplier 3B filing)
            reversed_rule_37a += rev_amt
        elif status in (ITCEligibilityEnum.TIME_LIMIT_RISK, ITCEligibilityEnum.REVERSED):
            reversed_other += rev_amt

    total_4b2 = reversed_rule_37 + reversed_rule_37a + reversed_other
    total_4b = reversed_sec_17_5_and_rules + total_4b2

    # 4(C) Net ITC Available = 4(A) - 4(B)(1) - 4(B)(2)
    net_itc_4c = max(Decimal("0.00"), total_4a - total_4b)

    # 4. Table 4(D)(1) Reclaimed Credit from earlier 4(B)(2) reversals (NEVER Section 17(5))
    reclaimed_4d1 = Decimal("0.00")
    if reclaims:
        for r in reclaims:
            reclaimed_4d1 += Decimal(str(
                (r.get("reavailed_cgst") or 0) +
                (r.get("reavailed_sgst") or 0) +
                (r.get("reavailed_igst") or 0) +
                (r.get("reavailed_cess") or 0)
            ))

    # 5. Provenance Subtotals:
    # Legally Eligible = Books Matched minus all statutory reversals (4(B)(1) permanent + 4(B)(2) temporary)
    subtotal_legally_eligible = max(Decimal("0.00"), subtotal_books_matched - total_4b)

    # Human Approved Total = Sum of tax on approved purchase documents
    subtotal_human_approved = Decimal("0.00")
    for p_doc in purchase_docs:
        p_id = str(p_doc["id"])
        tax = Decimal(str(
            (p_doc.get("cgst_amount") or 0) +
            (p_doc.get("sgst_amount") or 0) +
            (p_doc.get("igst_amount") or 0) +
            (p_doc.get("cess_amount") or 0)
        ))
        if p_id in approved_set or p_doc.get("status") == "approved":
            subtotal_human_approved += tax

    return GSTR3BTable4WorkingOutput(
        return_period=return_period,
        recipient_gstin=recipient_gstin,
        # Table 4(A) ITC Available
        itc_available_import_goods=itc_avail_impg,
        itc_available_import_services=itc_avail_imps,
        itc_available_inward_reverse_charge=itc_avail_rcm,
        itc_available_isd=itc_avail_isd,
        itc_available_all_other=itc_avail_all_other,
        total_itc_available=total_4a,
        # Table 4(B) ITC Reversed
        itc_reversed_rule_38_42_43=reversed_sec_17_5_and_rules,
        itc_reversed_rule_37_180_days=reversed_rule_37,
        itc_reversed_rule_37a=reversed_rule_37a,
        itc_reversed_other=reversed_other,
        total_itc_reversed_reclaimable_4b2=total_4b2,
        total_itc_reversed=total_4b,
        # Table 4(C) Net ITC Available
        net_itc_available=net_itc_4c,
        # Table 4(D) Other Details
        itc_reclaimed_earlier_reversal_4d1=reclaimed_4d1,
        ineligible_sec_16_4_and_pos_restrictions_4d2=ineligible_portal_4d2,
        ineligible_sec_17_5_blocked=reversed_sec_17_5_and_rules,
        ineligible_other=Decimal("0.00"),
        # Calculation Provenance Subtotals
        subtotal_gstr2b_portal_available=subtotal_portal_avail,
        subtotal_books_matched=subtotal_books_matched,
        subtotal_legally_eligible=subtotal_legally_eligible,
        subtotal_human_approved=subtotal_human_approved
    )
