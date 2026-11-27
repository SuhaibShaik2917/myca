import logging
from decimal import Decimal
from typing import List, Dict, Any, Tuple, Optional
from datetime import datetime, date
from .schemas import (
    MatchCategoryEnum, MatchDetailOut, VarianceDetailOut,
    ReconciliationSummaryOut
)
from .gstr2b_parser import normalize_doc_number

logger = logging.getLogger("reconciliation.matching_engine")


def parse_date(date_str: Any) -> Optional[date]:
    if not date_str:
        return None
    if isinstance(date_str, date):
        return date_str
    if isinstance(date_str, datetime):
        return date_str.date()
    try:
        clean = str(date_str).strip()[:10]
        return datetime.strptime(clean, "%Y-%m-%d").date()
    except Exception:
        try:
            return datetime.strptime(str(date_str).strip(), "%d-%m-%Y").date()
        except Exception:
            return None


class DeterministicReconciliationEngine:
    """
    Milestone 2 Deterministic Multi-Pass Reconciliation Engine.
    Executes:
    Pass 1: Exact Document Identity
    Pass 2: Amendment-Aware Matching
    Pass 3: Configurable Amount Tolerances
    Pass 4: Controlled Near-Match Candidate Generation
    """

    def __init__(
        self,
        taxable_tolerance: Decimal = Decimal("1.00"),
        tax_tolerance: Decimal = Decimal("1.00"),
        total_tolerance: Decimal = Decimal("2.00"),
        cgst_tolerance: Optional[Decimal] = None,
        sgst_tolerance: Optional[Decimal] = None,
        igst_tolerance: Optional[Decimal] = None,
        cess_tolerance: Optional[Decimal] = None,
        engine_version: str = "v2.1_deterministic"
    ):
        self.taxable_tolerance = taxable_tolerance
        self.tax_tolerance = tax_tolerance
        self.total_tolerance = total_tolerance
        self.cgst_tolerance = cgst_tolerance if cgst_tolerance is not None else tax_tolerance
        self.sgst_tolerance = sgst_tolerance if sgst_tolerance is not None else tax_tolerance
        self.igst_tolerance = igst_tolerance if igst_tolerance is not None else tax_tolerance
        self.cess_tolerance = cess_tolerance if cess_tolerance is not None else Decimal("1.00")
        self.engine_version = engine_version

    @staticmethod
    def _doc_cls(raw: Any) -> str:
        v = str(raw or "").strip().lower()
        if any(k in v for k in ("credit", "crn", "cdnr")):
            return "crn"
        if any(k in v for k in ("debit", "dbn")):
            return "dbn"
        if "isd" in v:
            return "isd"
        if "boe" in v or "bill_of_entry" in v:
            return "boe"
        return "inv"

    def run_reconciliation(
        self,
        purchase_docs: List[Dict[str, Any]],
        gstr2b_docs: List[Dict[str, Any]]
    ) -> Tuple[List[MatchDetailOut], List[VarianceDetailOut]]:
        matches: List[MatchDetailOut] = []
        variances: List[VarianceDetailOut] = []

        matched_purchase_ids = set()
        matched_gstr2b_ids = set()

        # Index GSTR-2B docs by (normalized_supplier_gstin, normalized_doc_num)
        # Also index amended documents under their original_document_number for Pass 2 resolution
        gstr2b_lookup: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
        for g_doc in gstr2b_docs:
            s_gstin = (g_doc.get("supplier_gstin") or "").strip().upper()
            d_num = normalize_doc_number(str(g_doc.get("document_number") or ""))
            gstr2b_lookup.setdefault((s_gstin, d_num), []).append(g_doc)
            orig_num = normalize_doc_number(str(g_doc.get("original_document_number") or ""))
            if orig_num and orig_num != d_num:
                gstr2b_lookup.setdefault((s_gstin, orig_num), []).append(g_doc)

        # -------------------------------------------------------------
        # PASS 1: Exact Document Identity
        # -------------------------------------------------------------
        for p_doc in purchase_docs:
            p_id = str(p_doc["id"])
            p_gstin = (p_doc.get("supplier_gstin") or "").strip().upper()
            p_num = normalize_doc_number(str(p_doc.get("document_number") or ""))
            p_date = parse_date(p_doc.get("document_date"))
            p_taxable = Decimal(str(p_doc.get("total_taxable_amount") or 0))
            p_tax = Decimal(str((p_doc.get("cgst_amount") or 0) + (p_doc.get("sgst_amount") or 0) + (p_doc.get("igst_amount") or 0)))
            p_total = Decimal(str(p_doc.get("total_invoice_value") or (p_taxable + p_tax)))

            candidates = gstr2b_lookup.get((p_gstin, p_num), [])
            for g_doc in candidates:
                g_id = str(g_doc["id"])
                if g_id in matched_gstr2b_ids:
                    continue

                g_date = parse_date(g_doc.get("document_date"))
                g_taxable = Decimal(str(g_doc.get("taxable_value") or 0))
                g_tax = Decimal(str((g_doc.get("central_tax") or 0) + (g_doc.get("state_ut_tax") or 0) + (g_doc.get("integrated_tax") or 0)))
                g_total = Decimal(str(g_doc.get("invoice_value") or 0))

                diff_taxable = abs(p_taxable - g_taxable)
                diff_tax = abs(p_tax - g_tax)
                diff_total = abs(p_total - g_total)
                diff_cgst = abs(Decimal(str(p_doc.get("cgst_amount") or 0)) - Decimal(str(g_doc.get("central_tax") or 0)))
                diff_sgst = abs(Decimal(str(p_doc.get("sgst_amount") or 0)) - Decimal(str(g_doc.get("state_ut_tax") or 0)))
                diff_igst = abs(Decimal(str(p_doc.get("igst_amount") or 0)) - Decimal(str(g_doc.get("integrated_tax") or 0)))
                diff_cess = abs(Decimal(str(p_doc.get("cess_amount") or 0)) - Decimal(str(g_doc.get("cess_amount") or 0)))

                # Exact match condition: compatible doc type, non-amended, same date & head-level tax identity
                if (
                    self._doc_cls(p_doc.get("document_type")) == self._doc_cls(g_doc.get("document_type"))
                    and not g_doc.get("is_amended")
                    and p_date == g_date
                    and diff_taxable == Decimal("0.00")
                    and diff_tax == Decimal("0.00")
                    and diff_cgst == Decimal("0.00")
                    and diff_sgst == Decimal("0.00")
                    and diff_igst == Decimal("0.00")
                    and diff_cess == Decimal("0.00")
                ):
                    matched_purchase_ids.add(p_id)
                    matched_gstr2b_ids.add(g_id)
                    matches.append(MatchDetailOut(
                        purchase_doc_id=p_id,
                        gstr2b_doc_id=g_id,
                        match_pass=1,
                        match_score=Decimal("1.000"),
                        is_exact=True,
                        taxable_difference=diff_taxable,
                        tax_difference=diff_tax,
                        total_difference=diff_total,
                        match_evidence={
                            "pass": "Exact Identity",
                            "supplier_gstin": p_gstin,
                            "doc_number": p_num,
                            "date": str(p_date),
                            "engine_version": self.engine_version
                        }
                    ))
                    break

        # -------------------------------------------------------------
        # PASS 2: Amendment-Aware Matching
        # -------------------------------------------------------------
        for p_doc in purchase_docs:
            p_id = str(p_doc["id"])
            if p_id in matched_purchase_ids:
                continue

            p_gstin = (p_doc.get("supplier_gstin") or "").strip().upper()
            p_num = normalize_doc_number(str(p_doc.get("document_number") or ""))

            candidates = gstr2b_lookup.get((p_gstin, p_num), [])
            for g_doc in candidates:
                g_id = str(g_doc["id"])
                if g_id in matched_gstr2b_ids:
                    continue
                if self._doc_cls(p_doc.get("document_type")) != self._doc_cls(g_doc.get("document_type")):
                    continue

                if g_doc.get("is_amended") or g_doc.get("section_name") in ("B2BA", "CDNRA", "IMPGA", "IMPGSEZA"):
                    orig_num = g_doc.get("original_document_number")
                    matched_purchase_ids.add(p_id)
                    matched_gstr2b_ids.add(g_id)

                    g_taxable = Decimal(str(g_doc.get("taxable_value") or 0))
                    p_taxable = Decimal(str(p_doc.get("total_taxable_amount") or 0))
                    g_tax = Decimal(str((g_doc.get("central_tax") or 0) + (g_doc.get("state_ut_tax") or 0) + (g_doc.get("integrated_tax") or 0)))
                    p_tax = Decimal(str((p_doc.get("cgst_amount") or 0) + (p_doc.get("sgst_amount") or 0) + (p_doc.get("igst_amount") or 0)))

                    g_total = Decimal(str(g_doc.get("invoice_value") or (g_taxable + g_tax)))
                    p_total = Decimal(str(p_doc.get("total_invoice_value") or (p_taxable + p_tax)))

                    matches.append(MatchDetailOut(
                        purchase_doc_id=p_id,
                        gstr2b_doc_id=g_id,
                        match_pass=2,
                        match_score=Decimal("0.950"),
                        is_exact=False,
                        taxable_difference=abs(p_taxable - g_taxable),
                        tax_difference=abs(p_tax - g_tax),
                        total_difference=abs(p_total - g_total),
                        match_evidence={
                            "pass": "Amendment-Aware",
                            "amendment_section": g_doc.get("section_name"),
                            "original_doc_ref": orig_num
                        }
                    ))
                    variances.append(VarianceDetailOut(
                        variance_category=MatchCategoryEnum.AMENDED_BY_SUPPLIER,
                        purchase_doc_id=p_id,
                        gstr2b_doc_id=g_id,
                        difference_details={
                            "amendment_type": g_doc.get("section_name"),
                            "supplier_filing_period": g_doc.get("source_gstr1_filing_period"),
                            "differential_taxable": str(p_taxable - g_taxable),
                            "differential_tax": str(p_tax - g_tax)
                        }
                    ))
                    break

        # -------------------------------------------------------------
        # PASS 3: Configurable Amount Tolerances
        # -------------------------------------------------------------
        deferred_tax_mismatches: Dict[Tuple[str, str], VarianceDetailOut] = {}
        for p_doc in purchase_docs:
            p_id = str(p_doc["id"])
            if p_id in matched_purchase_ids:
                continue

            p_gstin = (p_doc.get("supplier_gstin") or "").strip().upper()
            p_num = normalize_doc_number(str(p_doc.get("document_number") or ""))
            p_date = parse_date(p_doc.get("document_date"))
            p_taxable = Decimal(str(p_doc.get("total_taxable_amount") or 0))
            p_tax = Decimal(str((p_doc.get("cgst_amount") or 0) + (p_doc.get("sgst_amount") or 0) + (p_doc.get("igst_amount") or 0)))
            p_total = Decimal(str(p_doc.get("total_invoice_value") or (p_taxable + p_tax)))

            candidates = gstr2b_lookup.get((p_gstin, p_num), [])
            for g_doc in candidates:
                g_id = str(g_doc["id"])
                if g_id in matched_gstr2b_ids:
                    continue
                if self._doc_cls(p_doc.get("document_type")) != self._doc_cls(g_doc.get("document_type")):
                    continue

                g_date = parse_date(g_doc.get("document_date"))
                g_taxable = Decimal(str(g_doc.get("taxable_value") or 0))
                g_tax = Decimal(str((g_doc.get("central_tax") or 0) + (g_doc.get("state_ut_tax") or 0) + (g_doc.get("integrated_tax") or 0)))
                g_total = Decimal(str(g_doc.get("invoice_value") or 0))

                diff_taxable = abs(p_taxable - g_taxable)
                diff_tax = abs(p_tax - g_tax)
                diff_total = abs(p_total - g_total)
                diff_cgst = abs(Decimal(str(p_doc.get("cgst_amount") or 0)) - Decimal(str(g_doc.get("central_tax") or 0)))
                diff_sgst = abs(Decimal(str(p_doc.get("sgst_amount") or 0)) - Decimal(str(g_doc.get("state_ut_tax") or 0)))
                diff_igst = abs(Decimal(str(p_doc.get("igst_amount") or 0)) - Decimal(str(g_doc.get("integrated_tax") or 0)))

                # Check component-level and total tolerances
                if (diff_taxable <= self.taxable_tolerance and
                    diff_tax <= self.tax_tolerance and
                    diff_cgst <= self.cgst_tolerance and
                    diff_sgst <= self.sgst_tolerance and
                    diff_igst <= self.igst_tolerance):
                    matched_purchase_ids.add(p_id)
                    matched_gstr2b_ids.add(g_id)
                    matches.append(MatchDetailOut(
                        purchase_doc_id=p_id,
                        gstr2b_doc_id=g_id,
                        match_pass=3,
                        match_score=Decimal("0.900"),
                        is_exact=(diff_taxable == 0 and diff_tax == 0),
                        taxable_difference=diff_taxable,
                        tax_difference=diff_tax,
                        total_difference=diff_total,
                        match_evidence={
                            "pass": "Tolerance Matched",
                            "applied_taxable_tolerance": str(self.taxable_tolerance),
                            "applied_tax_tolerance": str(self.tax_tolerance)
                        }
                    ))
                    if diff_tax > 0 or diff_taxable > 0:
                        variances.append(VarianceDetailOut(
                            variance_category=MatchCategoryEnum.TAX_MISMATCH,
                            purchase_doc_id=p_id,
                            gstr2b_doc_id=g_id,
                            difference_details={
                                "book_taxable": str(p_taxable),
                                "portal_taxable": str(g_taxable),
                                "book_tax": str(p_tax),
                                "portal_tax": str(g_tax),
                                "tolerance_accepted": True
                            }
                        ))
                    break
                elif diff_taxable > self.taxable_tolerance or diff_tax > self.tax_tolerance:
                    # Defer emission until Pass 4 completes so date-shifted or subsequent candidates do not produce duplicate/spurious variances
                    deferred_tax_mismatches.setdefault(
                        (p_id, g_id),
                        VarianceDetailOut(
                            variance_category=MatchCategoryEnum.TAX_MISMATCH,
                            purchase_doc_id=p_id,
                            gstr2b_doc_id=g_id,
                            difference_details={
                                "book_taxable": str(p_taxable),
                                "portal_taxable": str(g_taxable),
                                "taxable_difference": str(diff_taxable),
                                "tax_difference": str(diff_tax),
                                "exceeds_tolerance": True
                            }
                        ),
                    )

        # -------------------------------------------------------------
        # PASS 4: Controlled Near-Match Candidates (e.g. ±3 days date variance)
        # -------------------------------------------------------------
        for p_doc in purchase_docs:
            p_id = str(p_doc["id"])
            if p_id in matched_purchase_ids:
                continue

            p_gstin = (p_doc.get("supplier_gstin") or "").strip().upper()
            p_num = normalize_doc_number(str(p_doc.get("document_number") or ""))
            p_date = parse_date(p_doc.get("document_date"))

            candidates = gstr2b_lookup.get((p_gstin, p_num), [])
            for g_doc in candidates:
                g_id = str(g_doc["id"])
                if g_id in matched_gstr2b_ids:
                    continue
                if self._doc_cls(p_doc.get("document_type")) != self._doc_cls(g_doc.get("document_type")):
                    continue

                g_date = parse_date(g_doc.get("document_date"))
                if p_date and g_date:
                    day_diff = abs((p_date - g_date).days)
                    p_taxable = Decimal(str(p_doc.get("total_taxable_amount") or 0))
                    p_tax = Decimal(str((p_doc.get("cgst_amount") or 0) + (p_doc.get("sgst_amount") or 0) + (p_doc.get("igst_amount") or 0)))
                    p_total = Decimal(str(p_doc.get("total_invoice_value") or (p_taxable + p_tax)))
                    g_taxable = Decimal(str(g_doc.get("taxable_value") or 0))
                    g_tax = Decimal(str((g_doc.get("central_tax") or 0) + (g_doc.get("state_ut_tax") or 0) + (g_doc.get("integrated_tax") or 0)))
                    g_total = Decimal(str(g_doc.get("invoice_value") or (g_taxable + g_tax)))
                    diff_taxable = abs(p_taxable - g_taxable)
                    diff_tax = abs(p_tax - g_tax)
                    diff_total = abs(p_total - g_total)
                    if (
                        0 < day_diff <= 3
                        and diff_taxable <= self.taxable_tolerance
                        and diff_tax <= self.tax_tolerance
                        and diff_total <= self.total_tolerance
                    ):
                        matched_purchase_ids.add(p_id)
                        matched_gstr2b_ids.add(g_id)
                        matches.append(MatchDetailOut(
                            purchase_doc_id=p_id,
                            gstr2b_doc_id=g_id,
                            match_pass=4,
                            match_score=Decimal("0.850"),
                            is_exact=False,
                            taxable_difference=diff_taxable,
                            tax_difference=diff_tax,
                            total_difference=diff_total,
                            match_evidence={
                                "pass": "Date Near-Match Candidate",
                                "book_date": str(p_date),
                                "portal_date": str(g_date),
                                "day_variance": day_diff
                            }
                        ))
                        variances.append(VarianceDetailOut(
                            variance_category=MatchCategoryEnum.DATE_MISMATCH,
                            purchase_doc_id=p_id,
                            gstr2b_doc_id=g_id,
                            difference_details={
                                "book_date": str(p_date),
                                "portal_date": str(g_date),
                                "day_difference": day_diff
                            }
                        ))
                        break

        for (p_id, g_id), var_item in deferred_tax_mismatches.items():
            if p_id not in matched_purchase_ids and g_id not in matched_gstr2b_ids:
                variances.append(var_item)

        # -------------------------------------------------------------
        # Identify Residual Unmatched Documents
        # -------------------------------------------------------------
        for p_doc in purchase_docs:
            p_id = str(p_doc["id"])
            if p_id not in matched_purchase_ids:
                variances.append(VarianceDetailOut(
                    variance_category=MatchCategoryEnum.MISSING_IN_GSTR2B,
                    purchase_doc_id=p_id,
                    gstr2b_doc_id=None,
                    difference_details={
                        "supplier_gstin": p_doc.get("supplier_gstin"),
                        "document_number": p_doc.get("document_number"),
                        "taxable_amount": str(p_doc.get("total_taxable_amount")),
                        "status": "Document present in books but missing in GSTR-2B"
                    }
                ))

        for g_doc in gstr2b_docs:
            g_id = str(g_doc["id"])
            if g_id not in matched_gstr2b_ids:
                variances.append(VarianceDetailOut(
                    variance_category=MatchCategoryEnum.MISSING_IN_BOOKS,
                    purchase_doc_id=None,
                    gstr2b_doc_id=g_id,
                    difference_details={
                        "supplier_gstin": g_doc.get("supplier_gstin"),
                        "document_number": g_doc.get("document_number"),
                        "taxable_value": str(g_doc.get("taxable_value")),
                        "section": g_doc.get("section_name"),
                        "status": "Document present in GSTR-2B but missing in purchase register"
                    }
                ))

        return matches, variances
