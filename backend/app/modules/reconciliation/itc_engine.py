import logging
from decimal import Decimal
from typing import List, Dict, Any, Optional
from datetime import date, datetime
from .schemas import ITCEligibilityEnum, ITCAssessmentOut, Rule37PaymentCheck, Rule37AFilingCheck

logger = logging.getLogger("reconciliation.itc_engine")


def calculate_rule_37_interest(
    reversal_tax: Decimal,
    ecl_minimum_balance: Optional[Decimal] = None,
    utilisation_date: Optional[date] = None,
    reversal_date: Optional[date] = None,
    interest_rate_pct: Decimal = Decimal("18.00"),
    tax_head: str = "TOTAL",
    ecl_head_balances: Optional[Dict[str, Decimal]] = None,
    ecl_transactions: Optional[List[Dict[str, Any]]] = None
) -> Dict[str, Any]:
    """
    Computes statutory interest liability under Section 50(3) read with Rule 88B(3) of CGST Rules
    and CBIC Circular No. 192/04/2023-GST:

    1. Interest is payable ONLY if and to the extent that the wrongly availed credit was actually utilised.
    2. Under Rule 88B(3), credit is treated as utilised only when the balance in the Electronic Credit
       Ledger (ECL) falls below the amount of wrongly availed credit.
    3. Tax-Head Aggregation Rules (Circular 192/04/2023-GST & Section 49(5)):
       - IGST Wrong Availment: Total balance across IGST + CGST + SGST heads taken together is evaluated
         because IGST credit can be cross-utilized towards IGST, CGST, and SGST liabilities.
         *Compensation Cess is strictly excluded* from this combined balance.
       - CGST Wrong Availment: Total balance across CGST + IGST heads is evaluated under Section 49(5)(b).
         (SGST and Cess are excluded).
       - SGST Wrong Availment: Total balance across SGST + IGST heads is evaluated under Section 49(5)(c).
         (CGST and Cess are excluded).
       - Cess Wrong Availment: Only Compensation Cess ledger balance is evaluated (cross-utilization prohibited).
    4. If relevant minimum ECL balance >= reversal_tax, the credit was NEVER utilised -> Utilised Amount = 0,
       and Interest is Nil (₹0.00).
    5. If relevant minimum ECL balance < reversal_tax, Utilised Amount = reversal_tax - max(0, relevant_ecl).
       Interest runs at 18% p.a. on the utilised shortfall from the date of utilisation (debit) to the
       date of reversal in Form GSTR-3B.
    """
    rev_tax = Decimal(str(reversal_tax or 0)).quantize(Decimal("0.01"))
    rate = Decimal(str(interest_rate_pct or "18.00"))
    rev_dt = reversal_date or date.today()
    t_head = (tax_head or "TOTAL").upper()

    computed_u_date = utilisation_date
    relevant_ecl: Optional[Decimal] = None
    cess_excluded = True

    # 1. Evaluate from chronological ECL transaction ledger if provided
    if ecl_transactions:
        min_txn_balance: Optional[Decimal] = None
        first_dip_date: Optional[date] = None

        for txn in ecl_transactions:
            t_dt_raw = txn.get("date") or txn.get("transaction_date")
            if isinstance(t_dt_raw, str):
                t_dt = datetime.strptime(t_dt_raw[:10], "%Y-%m-%d").date()
            elif isinstance(t_dt_raw, date):
                t_dt = t_dt_raw
            else:
                t_dt = None

            b_igst = Decimal(str(txn.get("balance_igst") or 0))
            b_cgst = Decimal(str(txn.get("balance_cgst") or 0))
            b_sgst = Decimal(str(txn.get("balance_sgst") or 0))
            b_cess = Decimal(str(txn.get("balance_cess") or 0))

            if t_head == "IGST":
                txn_rel = b_igst + b_cgst + b_sgst  # Circular 192: Cess strictly excluded
            elif t_head == "CGST":
                txn_rel = b_cgst + b_igst
            elif t_head == "SGST":
                txn_rel = b_sgst + b_igst
            elif t_head == "CESS":
                txn_rel = b_cess
            else:
                txn_rel = b_igst + b_cgst + b_sgst

            if min_txn_balance is None or txn_rel < min_txn_balance:
                min_txn_balance = txn_rel

            if txn_rel < rev_tax and first_dip_date is None:
                first_dip_date = t_dt

        relevant_ecl = min_txn_balance
        if computed_u_date is None and first_dip_date is not None:
            computed_u_date = first_dip_date

    # 2. Evaluate from head balances snapshot if transactions not provided
    elif ecl_head_balances:
        b_igst = Decimal(str(ecl_head_balances.get("igst") or 0))
        b_cgst = Decimal(str(ecl_head_balances.get("cgst") or 0))
        b_sgst = Decimal(str(ecl_head_balances.get("sgst") or 0))
        b_cess = Decimal(str(ecl_head_balances.get("cess") or 0))

        if t_head == "IGST":
            relevant_ecl = b_igst + b_cgst + b_sgst
        elif t_head == "CGST":
            relevant_ecl = b_cgst + b_igst
        elif t_head == "SGST":
            relevant_ecl = b_sgst + b_igst
        elif t_head == "CESS":
            relevant_ecl = b_cess
        else:
            relevant_ecl = b_igst + b_cgst + b_sgst

    # 3. Direct minimum balance fallback
    elif ecl_minimum_balance is not None:
        relevant_ecl = Decimal(str(ecl_minimum_balance))

    # Determine statutory basis string
    if t_head == "IGST":
        statutory_basis = "Section 50(3) read with Rule 88B(3) and CBIC Circular No. 192/04/2023-GST"
    else:
        statutory_basis = "Section 50(3) read with Rule 88B(3)"

    # When relevant ECL balance >= reversal_tax: NEVER utilised -> Nil interest
    if relevant_ecl is not None and relevant_ecl >= rev_tax:
        if t_head == "IGST":
            reason = (
                f"Combined ECL minimum balance across IGST+CGST+SGST (₹{relevant_ecl:,.2f}, excluding Cess) "
                f">= wrongly availed IGST (₹{rev_tax:,.2f}). Per CBIC Circular 192/04/2023-GST, credit was "
                f"never utilised. Statutory interest under Section 50(3) is Nil (₹0.00)."
            )
        else:
            reason = (
                f"ECL relevant minimum balance (₹{relevant_ecl:,.2f}) >= reversal amount (₹{rev_tax:,.2f}). "
                f"Wrongly availed ITC was never utilised. Statutory interest under Section 50(3) is Nil (₹0.00)."
            )

        return {
            "reversal_tax": rev_tax,
            "ecl_minimum_balance": ecl_minimum_balance if ecl_minimum_balance is not None else relevant_ecl,
            "relevant_ecl_balance": relevant_ecl,
            "tax_head": t_head,
            "cess_excluded": cess_excluded,
            "utilised_amount": Decimal("0.00"),
            "utilisation_date": None,
            "reversal_date": rev_dt,
            "days_utilised": 0,
            "interest_rate_pct": rate,
            "interest_amount": Decimal("0.00"),
            "is_utilised": False,
            "statutory_basis": statutory_basis,
            "explanation": reason,
            "source_ecl_transactions": ecl_transactions
        }

    # When utilised or ECL balance fell below reversal amount
    min_bal = max(Decimal("0.00"), relevant_ecl) if relevant_ecl is not None else Decimal("0.00")
    utilised = (rev_tax - min_bal).quantize(Decimal("0.01"))

    if computed_u_date and rev_dt > computed_u_date:
        days = (rev_dt - computed_u_date).days
    else:
        days = 0

    if days > 0 and utilised > Decimal("0.00"):
        interest = (utilised * (rate / Decimal("100.00")) * Decimal(days) / Decimal("365")).quantize(Decimal("0.01"))
    else:
        interest = Decimal("0.00")

    if t_head == "IGST":
        exp = (
            f"Combined ECL balance (IGST+CGST+SGST, excluding Cess) fell to ₹{min_bal:,.2f}, resulting in "
            f"statutory credit utilisation of ₹{utilised:,.2f} for {days} days ({computed_u_date} to {rev_dt}) "
            f"at {rate}% p.a. per Rule 88B(3) and CBIC Circular 192/04/2023-GST."
        )
    else:
        exp = (
            f"Relevant ECL balance fell to ₹{min_bal:,.2f}, resulting in actual credit utilisation of ₹{utilised:,.2f} "
            f"for {days} days ({computed_u_date} to {rev_dt}) at {rate}% p.a. under Section 50(3)."
        )

    return {
        "reversal_tax": rev_tax,
        "ecl_minimum_balance": ecl_minimum_balance if ecl_minimum_balance is not None else relevant_ecl,
        "relevant_ecl_balance": relevant_ecl,
        "tax_head": t_head,
        "cess_excluded": cess_excluded,
        "utilised_amount": utilised,
        "utilisation_date": computed_u_date,
        "reversal_date": rev_dt,
        "days_utilised": days,
        "interest_rate_pct": rate,
        "interest_amount": interest,
        "is_utilised": utilised > Decimal("0.00"),
        "statutory_basis": statutory_basis,
        "explanation": exp,
        "source_ecl_transactions": ecl_transactions
    }


class StatutoryITCRiskEngine:
    """
    Milestone 2 Versioned Statutory ITC Risk & Eligibility Engine.
    Evaluates CGST Section 16, Section 17, Rule 37, Rule 37A, and RCM rules.
    """

    def __init__(self, engine_version: str = "v2.1_statutory"):
        self.engine_version = engine_version

    def evaluate_itc_eligibility(
        self,
        purchase_doc: Dict[str, Any],
        gstr2b_doc: Optional[Dict[str, Any]] = None,
        payments: Optional[List[Dict[str, Any]]] = None,
        evaluation_date: Optional[date] = None,
        ecl_minimum_balance: Optional[Decimal] = None,
        utilisation_date: Optional[date] = None,
        reversal_date: Optional[date] = None
    ) -> List[ITCAssessmentOut]:
        eval_dt = evaluation_date or date.today()
        assessments: List[ITCAssessmentOut] = []

        p_id = str(purchase_doc["id"])
        g_id = str(gstr2b_doc["id"]) if gstr2b_doc else None

        p_date_raw = purchase_doc.get("document_date")
        p_date = datetime.strptime(str(p_date_raw)[:10], "%Y-%m-%d").date() if p_date_raw else eval_dt
        days_elapsed = (eval_dt - p_date).days

        cgst = Decimal(str(purchase_doc.get("cgst_amount") or 0))
        sgst = Decimal(str(purchase_doc.get("sgst_amount") or 0))
        igst = Decimal(str(purchase_doc.get("igst_amount") or 0))
        cess = Decimal(str(purchase_doc.get("cess_amount") or 0))
        total_tax = cgst + sgst + igst + cess
        total_inv_val = Decimal(str(purchase_doc.get("total_invoice_value") or 0))

        # -------------------------------------------------------------
        # 1. Section 16(2)(aa): Supplier Reporting in GSTR-1 & Inclusion in GSTR-2B
        # -------------------------------------------------------------
        if not gstr2b_doc:
            assessments.append(ITCAssessmentOut(
                purchase_doc_id=p_id,
                gstr2b_doc_id=None,
                rule_code="SEC_16_2_AA",
                statutory_section="Section 16(2)(aa)",
                eligibility_status=ITCEligibilityEnum.SUPPLIER_STATUS_UNKNOWN,
                potential_itc=total_tax,
                risk_reversal_amount=total_tax,
                findings_explanation="Document not found in GSTR-2B. ITC cannot be claimed under Section 16(2)(aa) until communicated by supplier.",
                missing_evidence_required="Supplier must upload invoice to GSTR-1/IFF and generate GSTR-2B entry.",
                evaluation_date=str(eval_dt)
            ))
        elif gstr2b_doc.get("itc_availability") == "not_available":
            assessments.append(ITCAssessmentOut(
                purchase_doc_id=p_id,
                gstr2b_doc_id=g_id,
                rule_code="SEC_16_2_AA",
                statutory_section="Section 16(2)(aa)",
                eligibility_status=ITCEligibilityEnum.BLOCKED_CREDIT_INDICATOR,
                potential_itc=total_tax,
                risk_reversal_amount=total_tax,
                findings_explanation="Document is flagged as 'ITC Not Available' in downloaded GSTR-2B statement (e.g. POS restriction or supplier filing limit).",
                missing_evidence_required="Review supplier filing state and place of supply conditions.",
                evaluation_date=str(eval_dt)
            ))

        # -------------------------------------------------------------
        # 2. Section 16(2)(b): Receipt of Goods or Services Evidence
        # -------------------------------------------------------------
        goods_receipt_date = purchase_doc.get("goods_receipt_date")
        if not goods_receipt_date:
            assessments.append(ITCAssessmentOut(
                purchase_doc_id=p_id,
                gstr2b_doc_id=g_id,
                rule_code="SEC_16_2_B",
                statutory_section="Section 16(2)(b)",
                eligibility_status=ITCEligibilityEnum.PENDING_GOODS_OR_SERVICE_RECEIPT,
                potential_itc=total_tax,
                risk_reversal_amount=Decimal("0.00"),
                findings_explanation="Goods Receipt Note (GRN) or proof of delivery is pending.",
                missing_evidence_required="Attach Good Receipt Note, delivery challan, or service completion sign-off.",
                evaluation_date=str(eval_dt)
            ))

        # -------------------------------------------------------------
        # 3. Section 16(4): Statutory Time Limit (30th November following FY)
        # -------------------------------------------------------------
        inv_year = p_date.year
        inv_month = p_date.month
        # FY end is March 31 of current/next year
        fy_end_year = inv_year if inv_month <= 3 else inv_year + 1
        cutoff_date = date(fy_end_year, 11, 30)
        if eval_dt > cutoff_date:
            assessments.append(ITCAssessmentOut(
                purchase_doc_id=p_id,
                gstr2b_doc_id=g_id,
                rule_code="SEC_16_4",
                statutory_section="Section 16(4)",
                eligibility_status=ITCEligibilityEnum.TIME_LIMIT_RISK,
                potential_itc=total_tax,
                risk_reversal_amount=total_tax,
                findings_explanation=f"Invoice date {p_date} exceeds Section 16(4) statutory time limit of 30th November {fy_end_year}.",
                missing_evidence_required="Check whether special statutory extension or Section 16(5)/16(6) retrospective relief applies.",
                evaluation_date=str(eval_dt)
            ))

        # -------------------------------------------------------------
        # 4. Rule 37: 180-Day Vendor Payment Tracking
        # -------------------------------------------------------------
        total_paid = Decimal("0.00")
        if payments:
            for p in payments:
                total_paid += Decimal(str(p.get("allocated_total_amount") or 0))

        unpaid = max(Decimal("0.00"), total_inv_val - total_paid)
        is_reverse_charge = purchase_doc.get("is_reverse_charge", False)

        # Rule 37 applies only to forward charge supplies and where ITC was availed in GSTR-3B
        is_availed_in_3b = purchase_doc.get("is_availed_in_3b")
        if is_availed_in_3b is False:
            logger.debug("Purchase doc %s not availed in GSTR-3B; Rule 37 reversal not triggered.", p_id)
        elif not is_reverse_charge and days_elapsed > 180 and unpaid > Decimal("0.00"):
            # Proportionate reversal
            prop_ratio = unpaid / total_inv_val if total_inv_val > 0 else Decimal("1.00")
            reversal_tax = (total_tax * prop_ratio).quantize(Decimal("0.01"))
            days_over = days_elapsed - 180

            if ecl_minimum_balance is not None:
                int_calc = calculate_rule_37_interest(
                    reversal_tax=reversal_tax,
                    ecl_minimum_balance=ecl_minimum_balance,
                    utilisation_date=utilisation_date,
                    reversal_date=reversal_date or eval_dt
                )
                interest_estimate = int_calc["interest_amount"]
                explanation = (
                    f"Vendor invoice unpaid after {days_elapsed} days ({days_over} days past 180-day limit). "
                    f"Proportionate reversal required: ₹{reversal_tax}. Estimated interest under Section 50(1) at 18% p.a.: ₹{interest_estimate}. "
                    f"ECL utilization basis: {int_calc['explanation']}"
                )
            else:
                interest_estimate = (reversal_tax * Decimal("0.18") * Decimal(days_over) / Decimal("365")).quantize(Decimal("0.01"))
                explanation = f"Vendor invoice unpaid after {days_elapsed} days ({days_over} days past 180-day limit). Proportionate reversal required: ₹{reversal_tax}. Estimated interest under Section 50(1) at 18% p.a.: ₹{interest_estimate}."

            assessments.append(ITCAssessmentOut(
                purchase_doc_id=p_id,
                gstr2b_doc_id=g_id,
                rule_code="RULE_37_180_DAYS",
                statutory_section="Rule 37",
                eligibility_status=ITCEligibilityEnum.PAYMENT_180_DAY_RISK,
                potential_itc=total_tax,
                risk_reversal_amount=reversal_tax,
                findings_explanation=explanation,
                missing_evidence_required="Record payment voucher, bank settlement, or formal credit adjustment to re-avail credit.",
                evaluation_date=str(eval_dt)
            ))

        # -------------------------------------------------------------
        # 5. Rule 37A: Supplier GSTR-3B Non-Filing Statutory Assessment
        # -------------------------------------------------------------
        if gstr2b_doc:
            source_payload = gstr2b_doc.get("raw_payload") or {}
            gstr3b_status_raw = source_payload.get("gstr3b_status")
            gstr3b_status = str(gstr3b_status_raw).upper() if gstr3b_status_raw else "UNKNOWN"
            is_availed_in_3b = purchase_doc.get("is_availed_in_3b", False)

            if not is_availed_in_3b:
                # Rule 37A explicitly applies only where ITC was availed by recipient in GSTR-3B
                logger.debug("Purchase doc %s not availed in GSTR-3B; Rule 37A reversal not triggered.", p_id)
            else:
                # Rule 37A Financial Year determination tied to FY in which credit was AVAILED
                av_period = purchase_doc.get("availed_period") or purchase_doc.get("financial_period")
                if av_period and len(str(av_period)) >= 7:
                    try:
                        av_year = int(str(av_period)[:4])
                        av_month = int(str(av_period)[5:7])
                    except (ValueError, IndexError):
                        av_year, av_month = inv_year, inv_month
                else:
                    av_year, av_month = inv_year, inv_month

                # End of FY in which ITC was availed (e.g. 2026-02 -> March 2026; 2026-05 -> March 2027)
                fy_end_year = av_year if av_month <= 3 else av_year + 1
                supplier_filing_deadline = date(fy_end_year, 9, 30)
                recipient_reversal_deadline = date(fy_end_year, 11, 30)

                if gstr3b_status == "UNKNOWN":
                    # Do not assume non-compliance when authoritative portal data is missing
                    assessments.append(ITCAssessmentOut(
                        purchase_doc_id=p_id,
                        gstr2b_doc_id=g_id,
                        rule_code="RULE_37A_NON_FILING",
                        statutory_section="Rule 37A",
                        eligibility_status=ITCEligibilityEnum.SUPPLIER_STATUS_UNKNOWN,
                        potential_itc=total_tax,
                        risk_reversal_amount=Decimal("0.00"),
                        findings_explanation="Authoritative supplier GSTR-3B filing status is unknown/uncommunicated by GST portal. No reversal assessed pending verification.",
                        missing_evidence_required="Obtain supplier GSTR-3B filing acknowledgement or portal confirmation.",
                        evaluation_date=str(eval_dt)
                    ))
                elif gstr3b_status == "NOT_FILED":
                    if eval_dt <= supplier_filing_deadline:
                        # Before 30th September of following FY: supplier still has statutory time
                        assessments.append(ITCAssessmentOut(
                            purchase_doc_id=p_id,
                            gstr2b_doc_id=g_id,
                            rule_code="RULE_37A_NON_FILING",
                            statutory_section="Rule 37A",
                            eligibility_status=ITCEligibilityEnum.POTENTIALLY_ELIGIBLE,
                            potential_itc=total_tax,
                            risk_reversal_amount=Decimal("0.00"),
                            findings_explanation=f"Supplier GSTR-3B is currently unfiled, but supplier has until statutory cut-off 30th September {fy_end_year} to furnish return.",
                            missing_evidence_required="Monitor supplier GSTR-3B filing until 30th September cut-off.",
                            evaluation_date=str(eval_dt)
                        ))
                    elif eval_dt <= recipient_reversal_deadline:
                        # Between 1st Oct and 30th Nov: reversal required; no interest under proviso
                        assessments.append(ITCAssessmentOut(
                            purchase_doc_id=p_id,
                            gstr2b_doc_id=g_id,
                            rule_code="RULE_37A_NON_FILING",
                            statutory_section="Rule 37A",
                            eligibility_status=ITCEligibilityEnum.RULE_37A_RISK,
                            potential_itc=total_tax,
                            risk_reversal_amount=total_tax,
                            findings_explanation=f"Supplier failed to furnish GSTR-3B by statutory cut-off 30th September {fy_end_year}. Mandatory reversal of ₹{total_tax} required in GSTR-3B on or before 30th November {fy_end_year}. No interest payable under Rule 37A proviso if reversed before deadline.",
                            missing_evidence_required="Reverse credit in Form GSTR-3B Table 4(B)(2) on or before 30th November.",
                            evaluation_date=str(eval_dt)
                        ))
                    else:
                        # After 30th Nov: reversal required along with 18% p.a. interest under Section 50(3)
                        days_delayed = (eval_dt - recipient_reversal_deadline).days
                        interest_liability = (total_tax * Decimal("0.18") * Decimal(days_delayed) / Decimal("365")).quantize(Decimal("0.01"))
                        assessments.append(ITCAssessmentOut(
                            purchase_doc_id=p_id,
                            gstr2b_doc_id=g_id,
                            rule_code="RULE_37A_NON_FILING",
                            statutory_section="Rule 37A",
                            eligibility_status=ITCEligibilityEnum.RULE_37A_RISK,
                            potential_itc=total_tax,
                            risk_reversal_amount=total_tax,
                            findings_explanation=f"Supplier unfiled past 30th September {fy_end_year}. Recipient reversal deadline 30th November {fy_end_year} exceeded by {days_delayed} days. Mandatory reversal of ₹{total_tax} plus Section 50 interest at 18% p.a. (estimated ₹{interest_liability}) under Section 50(3). Note: Under Section 50(3) read with Rule 88B(3), interest applies only if credit was actually utilized against output tax liability.",
                            missing_evidence_required="Pay reversed ITC under Table 4(B)(2) and evaluate Section 50(3) interest based on Electronic Credit Ledger utilization.",
                            evaluation_date=str(eval_dt)
                        ))
                elif gstr3b_status == "FILED":
                    # Supplier filed return; check if previously reversed for re-availment
                    if purchase_doc.get("status") == "reversed" or purchase_doc.get("previously_reversed_under_37a"):
                        assessments.append(ITCAssessmentOut(
                            purchase_doc_id=p_id,
                            gstr2b_doc_id=g_id,
                            rule_code="RULE_37A_NON_FILING",
                            statutory_section="Rule 37A",
                            eligibility_status=ITCEligibilityEnum.REAVAILMENT_REVIEW_REQUIRED,
                            potential_itc=total_tax,
                            risk_reversal_amount=Decimal("0.00"),
                            findings_explanation="Supplier has subsequently furnished GSTR-3B return. Recipient is legally eligible to re-avail previously reversed ITC under Rule 37A second proviso in Form GSTR-3B Table 4(A)(5) and report in 4(D)(1).",
                            missing_evidence_required="Record re-availment in current period GSTR-3B Table 4(A)(5) and 4(D)(1).",
                            evaluation_date=str(eval_dt)
                        ))

        # -------------------------------------------------------------
        # 6. Section 17(5): Blocked Credit Catalogue Checks
        # -------------------------------------------------------------
        desc = (purchase_doc.get("goods_services_type") or "").lower()
        ledger = (purchase_doc.get("purchase_ledger_code") or "").lower()
        top_desc = (purchase_doc.get("item_description") or "").lower()
        items = purchase_doc.get("line_items") or []
        if not items and top_desc:
            items = [{"item_description": top_desc, "hsn_sac_code": purchase_doc.get("hsn_sac_code")}]

        for line in items:
            item_desc = (line.get("item_description") or "").lower()
            hsn = str(line.get("hsn_sac_code") or "")

            # Motor Vehicles 17(5)(a) - HSN 8702, 8703
            if hsn.startswith(("8702", "8703")) or "motor car" in item_desc or "vehicle" in item_desc or "car" in item_desc:
                assessments.append(ITCAssessmentOut(
                    purchase_doc_id=p_id,
                    gstr2b_doc_id=g_id,
                    rule_code="SEC_17_5_MOTOR",
                    statutory_section="Section 17(5)(a)",
                    eligibility_status=ITCEligibilityEnum.BLOCKED_CREDIT_INDICATOR,
                    potential_itc=total_tax,
                    risk_reversal_amount=total_tax,
                    findings_explanation="Motor vehicle with seating capacity <= 13 persons is blocked under Section 17(5)(a).",
                    missing_evidence_required="Provide proof of passenger transport business, driving school, or goods transport use.",
                    evaluation_date=str(eval_dt)
                ))
                break

            # Food & Beverages 17(5)(b) - HSN 9963
            elif hsn.startswith("9963") or "catering" in item_desc or "food" in item_desc:
                assessments.append(ITCAssessmentOut(
                    purchase_doc_id=p_id,
                    gstr2b_doc_id=g_id,
                    rule_code="SEC_17_5_FOOD",
                    statutory_section="Section 17(5)(b)",
                    eligibility_status=ITCEligibilityEnum.BLOCKED_CREDIT_INDICATOR,
                    potential_itc=total_tax,
                    risk_reversal_amount=total_tax,
                    findings_explanation="Food, beverages, and outdoor catering are blocked under Section 17(5)(b).",
                    missing_evidence_required="Provide evidence of outward taxable supply of same category or employer statutory obligation.",
                    evaluation_date=str(eval_dt)
                ))
                break

            # Works Contract 17(5)(c) - HSN 9954
            elif hsn.startswith("9954") or "works contract" in item_desc or "construction" in item_desc:
                assessments.append(ITCAssessmentOut(
                    purchase_doc_id=p_id,
                    gstr2b_doc_id=g_id,
                    rule_code="SEC_17_5_WORKS_CONTR",
                    statutory_section="Section 17(5)(c)",
                    eligibility_status=ITCEligibilityEnum.BLOCKED_CREDIT_INDICATOR,
                    potential_itc=total_tax,
                    risk_reversal_amount=total_tax,
                    findings_explanation="Works contract for construction of immovable property is blocked under Section 17(5)(c).",
                    missing_evidence_required="Proof that supply is for plant and machinery or outward works contract supply.",
                    evaluation_date=str(eval_dt)
                ))
                break

            # Personal Consumption 17(5)(g)
            elif "personal" in item_desc or "gift" in item_desc:
                assessments.append(ITCAssessmentOut(
                    purchase_doc_id=p_id,
                    gstr2b_doc_id=g_id,
                    rule_code="SEC_17_5_PERSONAL",
                    statutory_section="Section 17(5)(g)",
                    eligibility_status=ITCEligibilityEnum.BLOCKED_CREDIT_INDICATOR,
                    potential_itc=total_tax,
                    risk_reversal_amount=total_tax,
                    findings_explanation="Goods or services for personal consumption or gifts are blocked under Section 17(5)(g).",
                    missing_evidence_required="Proof of direct business nexus.",
                    evaluation_date=str(eval_dt)
                ))
                break

        # -------------------------------------------------------------
        # 7. Default to Complete Eligibility if all tests pass
        # -------------------------------------------------------------
        if not assessments:
            assessments.append(ITCAssessmentOut(
                purchase_doc_id=p_id,
                gstr2b_doc_id=g_id,
                rule_code="SEC_16_COMPLETE",
                statutory_section="Section 16(2)",
                eligibility_status=ITCEligibilityEnum.ELIGIBLE_EVIDENCE_COMPLETE,
                potential_itc=total_tax,
                risk_reversal_amount=Decimal("0.00"),
                findings_explanation="All Section 16 statutory conditions satisfied: in GSTR-2B, valid document, and within payment limits.",
                missing_evidence_required=None,
                evaluation_date=str(eval_dt)
            ))

        return assessments

    def evaluate_rule_37a_details(
        self,
        purchase_doc: Dict[str, Any],
        gstr2b_doc: Optional[Dict[str, Any]] = None,
        evaluation_date: Optional[date] = None
    ) -> Rule37AFilingCheck:
        """
        Detailed statutory evaluation of Rule 37A with separate 30-Sept supplier cut-off
        and 30-Nov recipient reversal deadline, un-availed exclusion, and 18% p.a. interest.
        """
        eval_dt = evaluation_date or date.today()
        p_id = str(purchase_doc["id"])
        supplier_gstin = str(purchase_doc.get("supplier_gstin") or "UNKNOWN")
        p_date_raw = purchase_doc.get("document_date")
        p_date = datetime.strptime(str(p_date_raw)[:10], "%Y-%m-%d").date() if p_date_raw else eval_dt

        inv_year = p_date.year
        inv_month = p_date.month

        # Rule 37A statutory deadlines tied to FY of availment:
        av_period = purchase_doc.get("availed_period") or purchase_doc.get("financial_period")
        if av_period and len(str(av_period)) >= 7:
            try:
                av_year = int(str(av_period)[:4])
                av_month = int(str(av_period)[5:7])
            except (ValueError, IndexError):
                av_year, av_month = inv_year, inv_month
        else:
            av_year, av_month = inv_year, inv_month

        fy_end_year = av_year if av_month <= 3 else av_year + 1
        supplier_filing_deadline = date(fy_end_year, 9, 30)
        recipient_reversal_deadline = date(fy_end_year, 11, 30)

        source_payload = (gstr2b_doc.get("raw_payload") or {}) if gstr2b_doc else {}
        gstr3b_status_raw = source_payload.get("gstr3b_status")
        gstr3b_status = str(gstr3b_status_raw).upper() if gstr3b_status_raw else "UNKNOWN"
        is_availed_in_3b = purchase_doc.get("is_availed_in_3b", False)

        cgst = Decimal(str(purchase_doc.get("cgst_amount") or 0))
        sgst = Decimal(str(purchase_doc.get("sgst_amount") or 0))
        igst = Decimal(str(purchase_doc.get("igst_amount") or 0))
        cess = Decimal(str(purchase_doc.get("cess_amount") or 0))
        total_tax = cgst + sgst + igst + cess

        reversal_required = False
        reversal_amount = Decimal("0.00")
        interest_liability = Decimal("0.00")
        reavailment_eligible = False
        risk_status = ITCEligibilityEnum.ELIGIBLE_EVIDENCE_COMPLETE

        if not is_availed_in_3b:
            risk_status = ITCEligibilityEnum.ELIGIBLE_EVIDENCE_COMPLETE
        elif gstr3b_status == "UNKNOWN":
            risk_status = ITCEligibilityEnum.SUPPLIER_STATUS_UNKNOWN
        elif gstr3b_status == "NOT_FILED":
            if eval_dt <= supplier_filing_deadline:
                risk_status = ITCEligibilityEnum.POTENTIALLY_ELIGIBLE
            elif eval_dt <= recipient_reversal_deadline:
                reversal_required = True
                reversal_amount = total_tax
                risk_status = ITCEligibilityEnum.RULE_37A_RISK
            else:
                reversal_required = True
                reversal_amount = total_tax
                days_delayed = (eval_dt - recipient_reversal_deadline).days
                interest_liability = (total_tax * Decimal("0.18") * Decimal(days_delayed) / Decimal("365")).quantize(Decimal("0.01"))
                risk_status = ITCEligibilityEnum.RULE_37A_RISK
        elif gstr3b_status == "FILED":
            if purchase_doc.get("status") == "reversed" or purchase_doc.get("previously_reversed_under_37a"):
                reavailment_eligible = True
                risk_status = ITCEligibilityEnum.REAVAILMENT_REVIEW_REQUIRED

        return Rule37AFilingCheck(
            purchase_doc_id=p_id,
            supplier_gstin=supplier_gstin,
            invoice_date=str(p_date),
            gstr1_reported_period=gstr2b_doc.get("source_gstr1_filing_period", "") if gstr2b_doc else "",
            supplier_gstr3b_status=gstr3b_status,
            supplier_filing_deadline=str(supplier_filing_deadline),
            recipient_reversal_deadline=str(recipient_reversal_deadline),
            is_availed_in_3b=is_availed_in_3b,
            reversal_required=reversal_required,
            reversal_amount=reversal_amount,
            interest_liability=interest_liability,
            reavailment_eligible=reavailment_eligible,
            risk_status=risk_status
        )
