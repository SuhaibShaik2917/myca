import logging
from decimal import Decimal
from typing import List, Dict, Any, Optional
from .schemas import GSTR3BTable4WorkingOutput, ComponentTaxAmount, ITCEligibilityEnum

logger = logging.getLogger("reconciliation.gstr3b_itc")


def _zero_component() -> ComponentTaxAmount:
    return ComponentTaxAmount(
        cgst=Decimal("0.00"),
        sgst=Decimal("0.00"),
        igst=Decimal("0.00"),
        cess=Decimal("0.00"),
        total=Decimal("0.00")
    )


def _add_component(comp: ComponentTaxAmount, cgst: Any, sgst: Any, igst: Any, cess: Any) -> ComponentTaxAmount:
    c = Decimal(str(cgst or 0))
    s = Decimal(str(sgst or 0))
    i = Decimal(str(igst or 0))
    cs = Decimal(str(cess or 0))
    tot = c + s + i + cs
    return ComponentTaxAmount(
        cgst=comp.cgst + c,
        sgst=comp.sgst + s,
        igst=comp.igst + i,
        cess=comp.cess + cs,
        total=comp.total + tot
    )


def generate_gstr3b_table4_summary(
    return_period: str,
    recipient_gstin: str,
    gstr2b_docs: List[Dict[str, Any]],
    purchase_docs: List[Dict[str, Any]],
    matches: List[Dict[str, Any]],
    assessments: List[Dict[str, Any]],
    human_approved_ids: Optional[set] = None,
    reclaims: Optional[List[Dict[str, Any]]] = None,
    reversals_ledger: Optional[List[Dict[str, Any]]] = None,
    auto_reverse_unbooked_unreceived: bool = False
) -> GSTR3BTable4WorkingOutput:
    """
    Generates statutory GSTR-3B Table 4 Inward ITC Working Output strictly compliant with
    CBIC Circular No. 170/02/2022-GST, Notification No. 14/2022-CT, and GST Portal Specifications.

    Statutory Mapping:
    - 4(A)(1): Import of Goods (IMPG / IMPGSEZ from ICEGATE)
    - 4(A)(2): Import of Services
    - 4(A)(3): Inward supplies liable to reverse charge (RCM)
    - 4(A)(4): Inward supplies from ISD
    - 4(A)(5): All other ITC (Registered B2B, includes amounts to be reversed under 4(B)(1)
               PLUS any qualifying reclaims under Table 4(D)(1))
    - 4(B)(1): Permanent / Non-reclaimable reversals under Rules 38, 42, 43 and Section 17(5)
    - 4(B)(2): Temporary / Reclaimable reversals (Rule 37 180 days, Rule 37A supplier non-filing, etc.)
    - 4(C):    Net ITC Available = 4(A) - 4(B)(1) - 4(B)(2)
    - 4(D)(1): Reclaimed ITC in current period after earlier reversal under Table 4(B)(2) (NEVER Section 17(5))
    - 4(D)(2): Ineligible credit under Section 16(4) & PoS restrictions (from GSTR-2B unavailable, not in 4(A))

    Traceability:
    - Component breakdown (CGST, SGST, IGST, Cess) for all rows
    - Explicit itemization of unbooked GSTR-2B documents explaining (GSTR-2B - Books Matched)
    - Explicit document-level lineage of 4(B)(1) and 4(B)(2) reversals against original availed documents
    """
    approved_set = human_approved_ids or set()

    comp_4a1 = _zero_component()
    comp_4a2 = _zero_component()
    comp_4a3 = _zero_component()
    comp_4a4 = _zero_component()
    comp_4a5 = _zero_component()

    subtotal_portal_avail = Decimal("0.00")
    subtotal_books_matched = Decimal("0.00")
    comp_4d2 = _zero_component()

    matched_gstr2b_ids = {str(m.get("gstr2b_doc_id")) for m in matches if m.get("gstr2b_doc_id")}
    unbooked_docs: List[Dict[str, Any]] = []

    # 1. Tally GSTR-2B statement components into Table 4(A) and Table 4(D)(2)
    for doc in gstr2b_docs:
        itc_status = doc.get("itc_availability", "available")
        c = Decimal(str(doc.get("central_tax") or 0))
        s = Decimal(str(doc.get("state_ut_tax") or 0))
        i = Decimal(str(doc.get("integrated_tax") or 0))
        cs = Decimal(str(doc.get("cess_amount") or 0))
        tax = c + s + i + cs
        sec = (doc.get("section_name") or "").upper()
        doc_id = str(doc.get("id"))

        if itc_status == "available":
            subtotal_portal_avail += tax
            doc_type = (doc.get("document_type") or "").lower()
            supply_cat = (doc.get("supply_category") or "").lower()

            # Explicit source-based statutory classification per GST Portal & CBIC Circular 170:
            # 1. Table 4(A)(1): Import of Goods (via ICEGATE Bill of Entry or SEZ inward goods)
            if (sec in ("IMPG", "IMPGA", "IMPGSEZ", "IMPGSEZA") or
                doc_type in ("bill_of_entry", "boe", "import_goods") or
                supply_cat in ("import_goods", "boe")):
                comp_4a1 = _add_component(comp_4a1, c, s, i, cs)

            # 2. Table 4(A)(2): Import of Services (foreign service procurement under RCM)
            elif (sec in ("IMPS", "IMPSA", "IMPORT_SERVICES") or
                  doc_type in ("import_service", "import_services") or
                  supply_cat in ("import_service", "import_services")):
                comp_4a2 = _add_component(comp_4a2, c, s, i, cs)

            # 3. Table 4(A)(3): Inward supplies liable to reverse charge (domestic RCM)
            elif (doc.get("supply_attract_reverse_charge") or
                  sec in ("RCM", "B2B_RCM") or
                  supply_cat in ("reverse_charge", "rcm")):
                comp_4a3 = _add_component(comp_4a3, c, s, i, cs)

            # 4. Table 4(A)(4): Inward supplies from ISD (GSTR-6 distribution)
            elif (sec in ("ISD", "ISDA") or
                  doc_type in ("isd_invoice", "isd") or
                  supply_cat in ("isd", "isd_distribution")):
                comp_4a4 = _add_component(comp_4a4, c, s, i, cs)

            # 5. Table 4(A)(5): All other eligible ITC
            # Note: Domestic capital goods from registered suppliers belong under 4(A)(5), NOT 4(A)(2)!
            else:
                comp_4a5 = _add_component(comp_4a5, c, s, i, cs)

            # Check if this GSTR-2B document is unbooked (missing in purchase register)
            if doc_id not in matched_gstr2b_ids:
                unbooked_docs.append({
                    "gstr2b_doc_id": doc_id,
                    "supplier_gstin": doc.get("supplier_gstin"),
                    "supplier_name": doc.get("supplier_name"),
                    "document_number": doc.get("document_number"),
                    "document_date": str(doc.get("document_date")),
                    "section_name": sec,
                    "taxable_value": Decimal(str(doc.get("taxable_value") or 0)),
                    "cgst_amount": c,
                    "sgst_amount": s,
                    "igst_amount": i,
                    "cess_amount": cs,
                    "total_tax": tax,
                    "variance_status": "MISSING_IN_BOOKS",
                    "explanation": "Document communicated on GST portal in GSTR-2B, but not entered/approved in recipient purchase register."
                })
        else:
            # GSTR-2B Table 4 'ITC Not Available' (Section 16(4) time-barred, PoS state mismatches)
            comp_4d2 = _add_component(comp_4d2, c, s, i, cs)

    # 2. Tally Book Matched Total (inward purchase documents reconciled against GSTR-2B)
    matched_purchase_map = {str(m["purchase_doc_id"]): m for m in matches}
    for p_doc in purchase_docs:
        p_id = str(p_doc["id"])
        c = Decimal(str(p_doc.get("cgst_amount") or 0))
        s = Decimal(str(p_doc.get("sgst_amount") or 0))
        i = Decimal(str(p_doc.get("igst_amount") or 0))
        cs = Decimal(str(p_doc.get("cess_amount") or 0))
        tax = c + s + i + cs
        if p_id in matched_purchase_map:
            subtotal_books_matched += tax

    # 3. Tally Table 4(D)(1) Reclaimed Credit from earlier 4(B)(2) reversals (NEVER Section 17(5))
    comp_4d1 = _zero_component()
    if reclaims:
        for r in reclaims:
            rc = Decimal(str(r.get("reavailed_cgst") or 0))
            rs = Decimal(str(r.get("reavailed_sgst") or 0))
            ri = Decimal(str(r.get("reavailed_igst") or 0))
            rcs = Decimal(str(r.get("reavailed_cess") or 0))
            comp_4d1 = _add_component(comp_4d1, rc, rs, ri, rcs)

    # Statutory Reconciled Link: Reclaims in 4(D)(1) must also be added to 4(A)(5) in current return
    # so that 4(C) net credit incorporates re-availed ITC
    current_b2b_inward = comp_4a5.total
    comp_4a5 = _add_component(comp_4a5, comp_4d1.cgst, comp_4d1.sgst, comp_4d1.igst, comp_4d1.cess)

    # Total 4(A) across components
    comp_4a_total = ComponentTaxAmount(
        cgst=comp_4a1.cgst + comp_4a2.cgst + comp_4a3.cgst + comp_4a4.cgst + comp_4a5.cgst,
        sgst=comp_4a1.sgst + comp_4a2.sgst + comp_4a3.sgst + comp_4a4.sgst + comp_4a5.sgst,
        igst=comp_4a1.igst + comp_4a2.igst + comp_4a3.igst + comp_4a4.igst + comp_4a5.igst,
        cess=comp_4a1.cess + comp_4a2.cess + comp_4a3.cess + comp_4a4.cess + comp_4a5.cess,
        total=comp_4a1.total + comp_4a2.total + comp_4a3.total + comp_4a4.total + comp_4a5.total
    )

    # 4. Tally Statutory Risk Reversals strictly into 4(B)(1) and 4(B)(2)
    comp_4b1 = _zero_component()  # Non-reclaimable
    comp_4b2_r37 = _zero_component()
    comp_4b2_r37a = _zero_component()
    comp_4b2_receipt = _zero_component()  # Section 16(2)(b) goods/services unreceived temporary reversal
    comp_4b2_other = _zero_component()
    reversals_lineage: List[Dict[str, Any]] = []
    purchase_map = {str(p["id"]): p for p in purchase_docs}

    # If explicit reversal events from ledger are supplied, use them; otherwise use assessments
    if reversals_ledger:
        for rev in reversals_ledger:
            rc = Decimal(str(rev.get("reversed_cgst") or 0))
            rs = Decimal(str(rev.get("reversed_sgst") or 0))
            ri = Decimal(str(rev.get("reversed_igst") or 0))
            rcs = Decimal(str(rev.get("reversed_cess") or 0))
            rule = rev.get("rule_code", "")
            p_id = str(rev.get("purchase_doc_id", ""))
            p_doc = purchase_map.get(p_id, {})

            if rule.startswith("SEC_17_5") or rule in ("RULE_38", "RULE_42", "RULE_43"):
                comp_4b1 = _add_component(comp_4b1, rc, rs, ri, rcs)
            elif rule == "RULE_37_180_DAYS":
                comp_4b2_r37 = _add_component(comp_4b2_r37, rc, rs, ri, rcs)
            elif rule == "RULE_37A_NON_FILING":
                comp_4b2_r37a = _add_component(comp_4b2_r37a, rc, rs, ri, rcs)
            elif rule in ("SEC_16_2_B", "SEC_16_2_B_PENDING_RECEIPT", "RULE_16_2_B", "PENDING_RECEIPT", "UNBOOKED_RECEIPT_REVERSAL", "REVERSAL_SECTION_16_2_B"):
                comp_4b2_receipt = _add_component(comp_4b2_receipt, rc, rs, ri, rcs)
            else:
                comp_4b2_other = _add_component(comp_4b2_other, rc, rs, ri, rcs)

            reversals_lineage.append({
                "reversal_id": str(rev.get("id")),
                "purchase_doc_id": p_id,
                "document_number": p_doc.get("document_number", rev.get("document_number", "N/A")),
                "document_date": str(p_doc.get("document_date", rev.get("document_date", ""))),
                "supplier_gstin": p_doc.get("supplier_gstin", rev.get("supplier_gstin", "N/A")),
                "availed_period": p_doc.get("availed_period", rev.get("reversal_period", "N/A")),
                "is_availed_in_3b": p_doc.get("is_availed_in_3b", True),
                "rule_code": rule,
                "reversal_status": rev.get("reversal_status") or ("DISCHARGED" if rev.get("filing_arn") else "PENDING_RETURN_FILING"),
                "reversal_due_date": rev.get("reversal_due_date"),
                "reversal_scheduled_date": rev.get("reversal_scheduled_date"),
                "filing_arn": rev.get("filing_arn"),
                "reversal_amount": rc + rs + ri + rcs,
                "cgst": rc, "sgst": rs, "igst": ri, "cess": rcs,
                "interest_liability": Decimal(str(rev.get("interest_liability") or 0)),
                "interest_basis": rev.get("interest_basis") or "Section 50(3) conditional on utilization",
                "interest_calculation_inputs": rev.get("interest_calculation_inputs")
            })
    else:
        for a in assessments:
            rule = a.get("rule_code", "")
            status = a.get("eligibility_status")
            rev_amt = Decimal(str(a.get("risk_reversal_amount") or 0))
            p_id = str(a.get("purchase_doc_id", ""))
            p_doc = purchase_map.get(p_id, {})

            # Apportion reversal across components proportionately if individual tax fields omitted
            p_cgst = Decimal(str(p_doc.get("cgst_amount") or 0))
            p_sgst = Decimal(str(p_doc.get("sgst_amount") or 0))
            p_igst = Decimal(str(p_doc.get("igst_amount") or 0))
            p_cess = Decimal(str(p_doc.get("cess_amount") or 0))
            p_tot = p_cgst + p_sgst + p_igst + p_cess

            if p_tot > Decimal("0.00"):
                ratio = rev_amt / p_tot
                rc = (p_cgst * ratio).quantize(Decimal("0.01"))
                rs = (p_sgst * ratio).quantize(Decimal("0.01"))
                ri = (p_igst * ratio).quantize(Decimal("0.01"))
                rcs = (p_cess * ratio).quantize(Decimal("0.01"))
            else:
                rc = Decimal("0.00")
                rs = Decimal("0.00")
                ri = rev_amt
                rcs = Decimal("0.00")

            if status in (ITCEligibilityEnum.REVIEW_REQUIRED, "REVIEW_REQUIRED") or str(rule).endswith("_FACTS_PENDING"):
                # Items under factual review are potential exposures, not confirmed statutory reversals
                pass
            elif rule.startswith("SEC_17_5") or rule in ("RULE_38", "RULE_42", "RULE_43") or status == ITCEligibilityEnum.BLOCKED_CREDIT_INDICATOR:
                comp_4b1 = _add_component(comp_4b1, rc, rs, ri, rcs)
            elif rule == "RULE_37_180_DAYS" or status == ITCEligibilityEnum.PAYMENT_180_DAY_RISK:
                comp_4b2_r37 = _add_component(comp_4b2_r37, rc, rs, ri, rcs)
            elif rule == "RULE_37A_NON_FILING" or status == ITCEligibilityEnum.RULE_37A_RISK:
                comp_4b2_r37a = _add_component(comp_4b2_r37a, rc, rs, ri, rcs)
            elif rule in ("SEC_16_2_B", "SEC_16_2_B_PENDING_RECEIPT") or status == ITCEligibilityEnum.PENDING_GOODS_OR_SERVICE_RECEIPT:
                comp_4b2_receipt = _add_component(comp_4b2_receipt, rc, rs, ri, rcs)
            elif status in (ITCEligibilityEnum.TIME_LIMIT_RISK, ITCEligibilityEnum.REVERSED):
                comp_4b2_other = _add_component(comp_4b2_other, rc, rs, ri, rcs)

            if rev_amt > Decimal("0.00") and status not in (ITCEligibilityEnum.REVIEW_REQUIRED, "REVIEW_REQUIRED") and not str(rule).endswith("_FACTS_PENDING"):
                reversals_lineage.append({
                    "purchase_doc_id": p_id,
                    "document_number": p_doc.get("document_number", "N/A"),
                    "document_date": str(p_doc.get("document_date", "")),
                    "supplier_gstin": p_doc.get("supplier_gstin", "N/A"),
                    "availed_period": p_doc.get("availed_period", "N/A"),
                    "is_availed_in_3b": p_doc.get("is_availed_in_3b", False),
                    "rule_code": rule,
                    "reversal_amount": rev_amt,
                    "cgst": rc, "sgst": rs, "igst": ri, "cess": rcs,
                    "interest_liability": Decimal("0.00"),
                    "interest_basis": "Statutory assessment under Section 50"
                })

    # Auto-reverse unbooked / unreceived documents if auto_reverse_unbooked_unreceived is enabled
    if auto_reverse_unbooked_unreceived:
        for u_doc in unbooked_docs:
            u_gid = u_doc.get("gstr2b_doc_id")
            # If not already recorded in reversals
            already_reversed = any(r.get("purchase_doc_id") == u_gid or r.get("reversal_id") == f"rev_unbooked_{u_gid}" for r in reversals_lineage)
            if not already_reversed:
                uc = u_doc.get("cgst_amount", Decimal("0.00"))
                us = u_doc.get("sgst_amount", Decimal("0.00"))
                ui = u_doc.get("igst_amount", Decimal("0.00"))
                ucs = u_doc.get("cess_amount", Decimal("0.00"))
                comp_4b2_receipt = _add_component(comp_4b2_receipt, uc, us, ui, ucs)
                reversals_lineage.append({
                    "reversal_id": f"rev_unbooked_{u_gid}",
                    "purchase_doc_id": u_gid,
                    "document_number": u_doc.get("document_number", "N/A"),
                    "document_date": str(u_doc.get("document_date", "")),
                    "supplier_gstin": u_doc.get("supplier_gstin", "N/A"),
                    "availed_period": return_period,
                    "is_availed_in_3b": False,
                    "rule_code": "SEC_16_2_B_PENDING_RECEIPT",
                    "reversal_status": "AUTO_NEUTRALIZED_UNBOOKED",
                    "reversal_due_date": None,
                    "reversal_scheduled_date": str(return_period),
                    "filing_arn": None,
                    "reversal_amount": uc + us + ui + ucs,
                    "cgst": uc, "sgst": us, "igst": ui, "cess": ucs,
                    "interest_liability": Decimal("0.00"),
                    "interest_basis": "Section 50(3) Nil - Not utilized in cash offset; neutralizes gross Table 4(A)(5) pending physical receipt per Circular 170/02/2022-GST",
                    "interest_calculation_inputs": {
                        "statutory_basis": "Circular 170/02/2022-GST",
                        "days_in_default": 0,
                        "utilized_amount": "0.00",
                        "interest_rate": "18% p.a."
                    }
                })

    comp_4b2_total = ComponentTaxAmount(
        cgst=comp_4b2_r37.cgst + comp_4b2_r37a.cgst + comp_4b2_receipt.cgst + comp_4b2_other.cgst,
        sgst=comp_4b2_r37.sgst + comp_4b2_r37a.sgst + comp_4b2_receipt.sgst + comp_4b2_other.sgst,
        igst=comp_4b2_r37.igst + comp_4b2_r37a.igst + comp_4b2_receipt.igst + comp_4b2_other.igst,
        cess=comp_4b2_r37.cess + comp_4b2_r37a.cess + comp_4b2_receipt.cess + comp_4b2_other.cess,
        total=comp_4b2_r37.total + comp_4b2_r37a.total + comp_4b2_receipt.total + comp_4b2_other.total
    )

    comp_4b_total = ComponentTaxAmount(
        cgst=comp_4b1.cgst + comp_4b2_total.cgst,
        sgst=comp_4b1.sgst + comp_4b2_total.sgst,
        igst=comp_4b1.igst + comp_4b2_total.igst,
        cess=comp_4b1.cess + comp_4b2_total.cess,
        total=comp_4b1.total + comp_4b2_total.total
    )

    # 5. Component Net ITC Available in 4(C) = 4(A) - 4(B)(1) - 4(B)(2)
    comp_4c_net = ComponentTaxAmount(
        cgst=max(Decimal("0.00"), comp_4a_total.cgst - comp_4b_total.cgst),
        sgst=max(Decimal("0.00"), comp_4a_total.sgst - comp_4b_total.sgst),
        igst=max(Decimal("0.00"), comp_4a_total.igst - comp_4b_total.igst),
        cess=max(Decimal("0.00"), comp_4a_total.cess - comp_4b_total.cess),
        total=max(Decimal("0.00"), comp_4a_total.total - comp_4b_total.total)
    )

    # 6. Provenance Subtotals:
    subtotal_legally_eligible = max(Decimal("0.00"), subtotal_books_matched - comp_4b_total.total)

    subtotal_human_approved = Decimal("0.00")
    for p_doc in purchase_docs:
        p_id = str(p_doc["id"])
        c = Decimal(str(p_doc.get("cgst_amount") or 0))
        s = Decimal(str(p_doc.get("sgst_amount") or 0))
        i = Decimal(str(p_doc.get("igst_amount") or 0))
        cs = Decimal(str(p_doc.get("cess_amount") or 0))
        tax = c + s + i + cs
        if p_id in approved_set or p_doc.get("status") == "approved":
            subtotal_human_approved += tax

    # 7. Variance Explanation between GSTR-2B Statement Total and Books Matched
    unbooked_var = max(Decimal("0.00"), subtotal_portal_avail - subtotal_books_matched)
    var_exp = (
        f"GSTR-2B Statement Available Total ₹{subtotal_portal_avail:,.2f} = Book-matched inward supplies ₹{subtotal_books_matched:,.2f} "
        f"+ Unbooked GSTR-2B documents ₹{unbooked_var:,.2f} ({len(unbooked_docs)} document(s) missing in purchase register). "
        f"Form GSTR-3B Table 4(A) Total ₹{comp_4a_total.total:,.2f} includes current GSTR-2B supplies ₹{subtotal_portal_avail:,.2f} "
        f"plus prior-period reclaims ₹{comp_4d1.total:,.2f} reported under Table 4(D)(1). Separately, Table 4(B)(2) reversals of "
        f"₹{comp_4b2_total.total:,.2f} pertain to previously availed historical purchase documents under Rule 37 / Rule 37A."
    )

    return GSTR3BTable4WorkingOutput(
        return_period=return_period,
        recipient_gstin=recipient_gstin,
        # Table 4(A) ITC Available
        itc_available_import_goods=comp_4a1.total,
        itc_available_import_services=comp_4a2.total,
        itc_available_inward_reverse_charge=comp_4a3.total,
        itc_available_isd=comp_4a4.total,
        itc_available_all_other=comp_4a5.total,
        total_itc_available=comp_4a_total.total,
        # Table 4(B) ITC Reversed
        itc_reversed_rule_38_42_43=comp_4b1.total,
        itc_reversed_rule_37_180_days=comp_4b2_r37.total,
        itc_reversed_rule_37a=comp_4b2_r37a.total,
        itc_reversed_sec_16_2_b_receipt=comp_4b2_receipt.total,
        itc_reversed_other=comp_4b2_other.total,
        total_itc_reversed_reclaimable_4b2=comp_4b2_total.total,
        total_itc_reversed=comp_4b_total.total,
        # Table 4(C) Net ITC Available
        net_itc_available=comp_4c_net.total,
        # Table 4(D) Other Details
        itc_reclaimed_earlier_reversal_4d1=comp_4d1.total,
        ineligible_sec_16_4_and_pos_restrictions_4d2=comp_4d2.total,
        ineligible_sec_17_5_blocked=comp_4b1.total,
        ineligible_other=Decimal("0.00"),
        # Calculation Provenance Subtotals
        subtotal_gstr2b_portal_available=subtotal_portal_avail,
        subtotal_books_matched=subtotal_books_matched,
        current_b2b_inward_available=current_b2b_inward,
        reclaims_total=comp_4d1.total,
        subtotal_legally_eligible=subtotal_legally_eligible,
        subtotal_human_approved=subtotal_human_approved,
        # Detailed Component Breakdown
        components_4a={
            "4A1_impg": comp_4a1,
            "4A2_imps": comp_4a2,
            "4A3_rcm": comp_4a3,
            "4A4_isd": comp_4a4,
            "4A5_all_other": comp_4a5,
            "4A_total": comp_4a_total
        },
        components_4b={
            "4B1_permanent": comp_4b1,
            "4B2_rule37": comp_4b2_r37,
            "4B2_rule37a": comp_4b2_r37a,
            "4B2_receipt": comp_4b2_receipt,
            "4B2_other": comp_4b2_other,
            "4B2_total": comp_4b2_total,
            "4B_total": comp_4b_total
        },
        components_4c_net=comp_4c_net,
        components_4d={
            "4D1_reclaimed": comp_4d1,
            "4D2_ineligible": comp_4d2
        },
        # Lineage & Variance Traceability
        unbooked_gstr2b_documents=unbooked_docs,
        unbooked_variance_amount=unbooked_var,
        variance_explanation=var_exp,
        reversals_lineage=reversals_lineage,
        interest_summary={
            "section_50_3_rate": "18% p.a.",
            "statutory_condition": "Applicable only if wrongly availed ITC was utilized against output tax liability per Rule 88B(3). If kept in ECL balance, interest is Nil."
        }
    )
