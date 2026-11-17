import logging
from decimal import Decimal
from typing import List, Dict, Any, Optional
from datetime import date, datetime
from .schemas import ITCEligibilityEnum, ITCAssessmentOut, Rule37PaymentCheck, Rule37AFilingCheck

logger = logging.getLogger("reconciliation.itc_engine")


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
        evaluation_date: Optional[date] = None
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

        # Rule 37 applies only to forward charge supplies
        if not is_reverse_charge and days_elapsed > 180 and unpaid > Decimal("0.00"):
            # Proportionate reversal
            prop_ratio = unpaid / total_inv_val if total_inv_val > 0 else Decimal("1.00")
            reversal_tax = (total_tax * prop_ratio).quantize(Decimal("0.01"))
            # Estimated interest at 18% p.a.
            days_over = days_elapsed - 180
            interest_estimate = (reversal_tax * Decimal("0.18") * Decimal(days_over) / Decimal("365")).quantize(Decimal("0.01"))

            assessments.append(ITCAssessmentOut(
                purchase_doc_id=p_id,
                gstr2b_doc_id=g_id,
                rule_code="RULE_37_180_DAYS",
                statutory_section="Rule 37",
                eligibility_status=ITCEligibilityEnum.PAYMENT_180_DAY_RISK,
                potential_itc=total_tax,
                risk_reversal_amount=reversal_tax,
                findings_explanation=f"Vendor invoice unpaid after {days_elapsed} days ({days_over} days past 180-day limit). Proportionate reversal required: ₹{reversal_tax}. Estimated interest at 18% p.a.: ₹{interest_estimate}.",
                missing_evidence_required="Record payment voucher, bank settlement, or formal credit adjustment to re-avail credit.",
                evaluation_date=str(eval_dt)
            ))

        # -------------------------------------------------------------
        # 5. Rule 37A: Supplier GSTR-3B Non-Filing Assessment
        # -------------------------------------------------------------
        if gstr2b_doc:
            # Check if supplier filing details are provided in raw payload or status
            source_payload = gstr2b_doc.get("raw_payload") or {}
            gstr3b_status = source_payload.get("gstr3b_status", "FILED")
            if str(gstr3b_status).upper() == "NOT_FILED":
                assessments.append(ITCAssessmentOut(
                    purchase_doc_id=p_id,
                    gstr2b_doc_id=g_id,
                    rule_code="RULE_37A_NON_FILING",
                    statutory_section="Rule 37A",
                    eligibility_status=ITCEligibilityEnum.RULE_37A_RISK,
                    potential_itc=total_tax,
                    risk_reversal_amount=total_tax,
                    findings_explanation="Supplier reported invoice in GSTR-1 but has not furnished GSTR-3B return. ITC must be reversed by 30th November if return remains unfiled.",
                    missing_evidence_required="Obtain supplier GSTR-3B filing acknowledgement or ARN.",
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
