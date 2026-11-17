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
    human_approved_ids: Optional[set] = None
) -> GSTR3BTable4WorkingOutput:
    """
    Generates statutory GSTR-3B Table 4 Inward ITC Working Output.
    Strictly differentiates:
    1. GSTR-2B Statement Total Available
    2. Book Matched Total
    3. Legally Eligible Total (after statutory rules)
    4. Human Approved Total
    """
    approved_set = human_approved_ids or set()

    itc_avail_impg = Decimal("0.00")
    itc_avail_rcm = Decimal("0.00")
    itc_avail_isd = Decimal("0.00")
    itc_avail_all_other = Decimal("0.00")

    subtotal_portal_avail = Decimal("0.00")
    subtotal_books_matched = Decimal("0.00")
    subtotal_legally_eligible = Decimal("0.00")
    subtotal_human_approved = Decimal("0.00")

    # 1. Tally GSTR-2B components
    for doc in gstr2b_docs:
        itc_status = doc.get("itc_availability", "available")
        tax = Decimal(str((doc.get("integrated_tax") or 0) + (doc.get("central_tax") or 0) + (doc.get("state_ut_tax") or 0) + (doc.get("cess_amount") or 0)))
        sec = (doc.get("section_name") or "").upper()

        if itc_status == "available":
            subtotal_portal_avail += tax
            if sec in ("IMPG", "IMPGA", "IMPGSEZ", "IMPGSEZA"):
                itc_avail_impg += tax
            elif sec in ("ISD", "ISDA"):
                itc_avail_isd += tax
            elif doc.get("supply_attract_reverse_charge") or sec == "RCM":
                itc_avail_rcm += tax
            else:
                itc_avail_all_other += tax

    # 2. Tally Book Matched
    matched_purchase_map = {m["purchase_doc_id"]: m for m in matches}
    for p_doc in purchase_docs:
        p_id = str(p_doc["id"])
        tax = Decimal(str((p_doc.get("cgst_amount") or 0) + (p_doc.get("sgst_amount") or 0) + (p_doc.get("igst_amount") or 0) + (p_doc.get("cess_amount") or 0)))
        if p_id in matched_purchase_map:
            subtotal_books_matched += tax

    # 3. Tally Assessments (Statutory Risk Reversals and Ineligible Credits)
    reversed_rule_37 = Decimal("0.00")
    reversed_rule_37a = Decimal("0.00")
    reversed_other = Decimal("0.00")
    blocked_17_5 = Decimal("0.00")

    doc_highest_risk = {}
    for a in assessments:
        p_id = a["purchase_doc_id"]
        status = a["eligibility_status"]
        rev_amt = Decimal(str(a.get("risk_reversal_amount") or 0))

        if status == ITCEligibilityEnum.PAYMENT_180_DAY_RISK:
            reversed_rule_37 += rev_amt
            doc_highest_risk[p_id] = "reversal"
        elif status == ITCEligibilityEnum.RULE_37A_RISK:
            reversed_rule_37a += rev_amt
            doc_highest_risk[p_id] = "reversal"
        elif status == ITCEligibilityEnum.BLOCKED_CREDIT_INDICATOR:
            blocked_17_5 += rev_amt
            doc_highest_risk[p_id] = "blocked"
        elif status in (ITCEligibilityEnum.TIME_LIMIT_RISK, ITCEligibilityEnum.REVERSED):
            reversed_other += rev_amt
            doc_highest_risk[p_id] = "reversal"

    total_reversed = reversed_rule_37 + reversed_rule_37a + reversed_other
    total_4a = itc_avail_impg + itc_avail_rcm + itc_avail_isd + itc_avail_all_other
    net_itc = max(Decimal("0.00"), total_4a - total_reversed)

    # Legally Eligible: Matched in books minus statutory reversals and blocked items
    subtotal_legally_eligible = max(Decimal("0.00"), subtotal_books_matched - total_reversed - blocked_17_5)

    # Human Approved
    for p_doc in purchase_docs:
        p_id = str(p_doc["id"])
        tax = Decimal(str((p_doc.get("cgst_amount") or 0) + (p_doc.get("sgst_amount") or 0) + (p_doc.get("igst_amount") or 0) + (p_doc.get("cess_amount") or 0)))
        if p_id in approved_set or p_doc.get("status") == "approved":
            subtotal_human_approved += tax

    return GSTR3BTable4WorkingOutput(
        return_period=return_period,
        recipient_gstin=recipient_gstin,
        itc_available_import_goods=itc_avail_impg,
        itc_available_import_services=Decimal("0.00"),
        itc_available_inward_reverse_charge=itc_avail_rcm,
        itc_available_isd=itc_avail_isd,
        itc_available_all_other=itc_avail_all_other,
        total_itc_available=total_4a,
        itc_reversed_rule_38_42_43=Decimal("0.00"),
        itc_reversed_rule_37_180_days=reversed_rule_37,
        itc_reversed_rule_37a=reversed_rule_37a,
        itc_reversed_other=reversed_other,
        total_itc_reversed=total_reversed,
        net_itc_available=net_itc,
        ineligible_sec_17_5_blocked=blocked_17_5,
        ineligible_other=Decimal("0.00"),
        subtotal_gstr2b_portal_available=subtotal_portal_avail,
        subtotal_books_matched=subtotal_books_matched,
        subtotal_legally_eligible=subtotal_legally_eligible,
        subtotal_human_approved=subtotal_human_approved
    )
