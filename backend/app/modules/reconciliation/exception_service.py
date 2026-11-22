"""
Milestone 2.7: Consolidated GSTR-2B & ITC Exception Management Service
Aggregates all 10 inward compliance exception work queues:
1. UNBOOKED_DOCUMENTS
2. GOODS_NOT_RECEIVED
3. SERVICES_NOT_COMPLETED
4. PARTIAL_RECEIPT
5. SUPPLIER_NON_FILING (Rule 37A)
6. RULE_37_OVERDUE (180-Day)
7. DUPLICATE_DOCUMENTS
8. TAX_HEAD_MISMATCH
9. CREDIT_NOTE_DISCREPANCY
10. MISSING_SUPPORTING_EVIDENCE

Core Statutory Invariant: No exception may silently become eligible ITC!
Every exception is either held outside books, retained in draft/pending,
or reversed under Table 4(B)(2).
"""
import uuid
from decimal import Decimal
from typing import Dict, Any, List, Optional
from datetime import date, datetime, timezone

from sqlalchemy.orm import Session
from sqlalchemy import text


def get_consolidated_exceptions(
    session: Session,
    org_id: str,
    registration_id: Optional[str] = None,
    return_period: Optional[str] = None,
    category_filter: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Scans and aggregates inward accounting records across all 10 exception queues.
    """
    exceptions: List[Dict[str, Any]] = []

    # -------------------------------------------------------------------------
    # 1. UNBOOKED_DOCUMENTS (In GSTR-2B Part A, not in purchase books)
    # -------------------------------------------------------------------------
    if not category_filter or category_filter == "UNBOOKED_DOCUMENTS":
        sql_unbooked = """
        SELECT d.id, d.document_number, d.document_date, d.supplier_gstin, d.supplier_name,
               COALESCE(d.taxable_value, 0) as taxable_value,
               COALESCE(d.central_tax, 0) as cgst,
               COALESCE(d.state_ut_tax, 0) as sgst,
               COALESCE(d.integrated_tax, 0) as igst,
               COALESCE(d.cess_amount, 0) as cess,
               s.return_period
        FROM public.gstr2b_source_documents d
        JOIN public.gstr2b_snapshots s ON d.snapshot_id = s.id
        WHERE d.org_id = :org
          AND d.itc_availability = 'available'
          AND NOT EXISTS (
              SELECT 1 FROM public.reconciliation_matches m
              WHERE m.gstr2b_doc_id = d.id AND m.org_id = d.org_id
          )
          AND NOT EXISTS (
              SELECT 1 FROM public.gstr2b_unbooked_candidates c
              WHERE c.gstr2b_doc_id = d.id AND c.org_id = d.org_id AND c.linked_purchase_doc_id IS NOT NULL
          )
        """
        params_u = {"org": org_id}
        if registration_id:
            sql_unbooked += " AND s.registration_id = :reg"
            params_u["reg"] = registration_id
        if return_period:
            sql_unbooked += " AND s.return_period = :period"
            params_u["period"] = return_period

        rows_u = session.execute(text(sql_unbooked), params_u).fetchall()
        for r in rows_u:
            tot_tax = Decimal(str(r.cgst)) + Decimal(str(r.sgst)) + Decimal(str(r.igst)) + Decimal(str(r.cess))
            exceptions.append({
                "exception_id": f"EXC-UNBOOKED-{r.id}",
                "category": "UNBOOKED_DOCUMENTS",
                "document_number": r.document_number,
                "document_date": str(r.document_date) if r.document_date else None,
                "supplier_gstin": r.supplier_gstin,
                "supplier_name": r.supplier_name,
                "taxable_value": Decimal(str(r.taxable_value)),
                "cgst_amount": Decimal(str(r.cgst)),
                "sgst_amount": Decimal(str(r.sgst)),
                "igst_amount": Decimal(str(r.igst)),
                "cess_amount": Decimal(str(r.cess)),
                "total_tax": tot_tax,
                "actionable_explanation": "Communicated by supplier in GSTR-1 / GSTR-2B Part A, but missing in Buyer's Purchase Register.",
                "statutory_citation": "Section 16(2)(a) & (aa) CGST Act: Buyer must hold tax invoice before claiming ITC.",
                "suggested_action": "Verify physical inward delivery, review in Unbooked Candidate workbench, and book entry.",
                "statutory_deadline": f"30th November following FY or Form GSTR-3B due date",
                "resolution_status": "OPEN",
                "source_reference_id": str(r.id),
                "created_at": datetime.now(timezone.utc).isoformat()
            })

    # -------------------------------------------------------------------------
    # 2. GOODS_NOT_RECEIVED (Goods documents with receipt_status UNKNOWN / NOT_RECEIVED)
    # -------------------------------------------------------------------------
    if not category_filter or category_filter == "GOODS_NOT_RECEIVED":
        sql_gnr = """
        SELECT p.id, p.document_number, p.document_date, p.supplier_gstin, p.supplier_legal_name,
               COALESCE(p.total_taxable_amount, 0) as taxable_value,
               COALESCE(p.cgst_amount, 0) as cgst,
               COALESCE(p.sgst_amount, 0) as sgst,
               COALESCE(p.igst_amount, 0) as igst,
               COALESCE(p.cess_amount, 0) as cess,
               p.receipt_status, p.financial_period
        FROM public.purchase_documents p
        WHERE p.org_id = :org
          AND LOWER(COALESCE(p.goods_services_type, 'goods')) = 'goods'
          AND p.receipt_status IN ('UNKNOWN', 'NOT_RECEIVED')
        """
        params_gnr = {"org": org_id}
        if registration_id:
            sql_gnr += " AND p.registration_id = :reg"
            params_gnr["reg"] = registration_id
        if return_period:
            sql_gnr += " AND p.financial_period = :period"
            params_gnr["period"] = return_period

        rows_gnr = session.execute(text(sql_gnr), params_gnr).fetchall()
        for r in rows_gnr:
            tot_tax = Decimal(str(r.cgst)) + Decimal(str(r.sgst)) + Decimal(str(r.igst)) + Decimal(str(r.cess))
            exceptions.append({
                "exception_id": f"EXC-GNR-{r.id}",
                "category": "GOODS_NOT_RECEIVED",
                "document_number": r.document_number,
                "document_date": str(r.document_date) if r.document_date else None,
                "supplier_gstin": r.supplier_gstin,
                "supplier_name": r.supplier_legal_name,
                "taxable_value": Decimal(str(r.taxable_value)),
                "cgst_amount": Decimal(str(r.cgst)),
                "sgst_amount": Decimal(str(r.sgst)),
                "igst_amount": Decimal(str(r.igst)),
                "cess_amount": Decimal(str(r.cess)),
                "total_tax": tot_tax,
                "actionable_explanation": "Physical goods delivery unverified. Section 16(2)(b) receipt gate withholds claim; reversed under Table 4(B)(2).",
                "statutory_citation": "Section 16(2)(b) CGST Act: Registered person must receive goods before claiming ITC.",
                "suggested_action": "Attach Goods Receipt Note (GRN) or Delivery Challan with confirmed receipt date.",
                "statutory_deadline": "Before filing monthly Form GSTR-3B",
                "resolution_status": "OPEN",
                "source_reference_id": str(r.id),
                "created_at": datetime.now(timezone.utc).isoformat()
            })

    # -------------------------------------------------------------------------
    # 3. SERVICES_NOT_COMPLETED (Services missing completion date or cert)
    # -------------------------------------------------------------------------
    if not category_filter or category_filter == "SERVICES_NOT_COMPLETED":
        sql_snc = """
        SELECT p.id, p.document_number, p.document_date, p.supplier_gstin, p.supplier_legal_name,
               COALESCE(p.total_taxable_amount, 0) as taxable_value,
               COALESCE(p.cgst_amount, 0) as cgst,
               COALESCE(p.sgst_amount, 0) as sgst,
               COALESCE(p.igst_amount, 0) as igst,
               COALESCE(p.cess_amount, 0) as cess,
               p.financial_period
        FROM public.purchase_documents p
        WHERE p.org_id = :org
          AND LOWER(p.goods_services_type) = 'services'
          AND (p.service_completion_date IS NULL OR p.service_acceptance_cert_ref IS NULL)
        """
        params_snc = {"org": org_id}
        if registration_id:
            sql_snc += " AND p.registration_id = :reg"
            params_snc["reg"] = registration_id
        if return_period:
            sql_snc += " AND p.financial_period = :period"
            params_snc["period"] = return_period

        rows_snc = session.execute(text(sql_snc), params_snc).fetchall()
        for r in rows_snc:
            tot_tax = Decimal(str(r.cgst)) + Decimal(str(r.sgst)) + Decimal(str(r.igst)) + Decimal(str(r.cess))
            exceptions.append({
                "exception_id": f"EXC-SNC-{r.id}",
                "category": "SERVICES_NOT_COMPLETED",
                "document_number": r.document_number,
                "document_date": str(r.document_date) if r.document_date else None,
                "supplier_gstin": r.supplier_gstin,
                "supplier_name": r.supplier_legal_name,
                "taxable_value": Decimal(str(r.taxable_value)),
                "cgst_amount": Decimal(str(r.cgst)),
                "sgst_amount": Decimal(str(r.sgst)),
                "igst_amount": Decimal(str(r.igst)),
                "cess_amount": Decimal(str(r.cess)),
                "total_tax": tot_tax,
                "actionable_explanation": "Inward service has not recorded completion date or formal client acceptance certificate.",
                "statutory_citation": "Section 16(2)(b) CGST Act: Registered person must receive service before availing ITC.",
                "suggested_action": "Record service milestone completion date and upload signed acceptance certificate.",
                "statutory_deadline": "Before filing monthly Form GSTR-3B",
                "resolution_status": "OPEN",
                "source_reference_id": str(r.id),
                "created_at": datetime.now(timezone.utc).isoformat()
            })

    # -------------------------------------------------------------------------
    # 4. PARTIAL_RECEIPT (Lots/instalments received, final lot pending)
    # -------------------------------------------------------------------------
    if not category_filter or category_filter == "PARTIAL_RECEIPT":
        sql_pr = """
        SELECT p.id, p.document_number, p.document_date, p.supplier_gstin, p.supplier_legal_name,
               COALESCE(p.total_taxable_amount, 0) as taxable_value,
               COALESCE(p.cgst_amount, 0) as cgst,
               COALESCE(p.sgst_amount, 0) as sgst,
               COALESCE(p.igst_amount, 0) as igst,
               COALESCE(p.cess_amount, 0) as cess,
               p.total_lots_expected, p.lots_received_count, p.remaining_quantity,
               p.financial_period
        FROM public.purchase_documents p
        WHERE p.org_id = :org
          AND (p.receipt_status = 'PARTIALLY_RECEIVED' OR (p.total_lots_expected > 1 AND p.is_last_lot_received = FALSE))
        """
        params_pr = {"org": org_id}
        if registration_id:
            sql_pr += " AND p.registration_id = :reg"
            params_pr["reg"] = registration_id
        if return_period:
            sql_pr += " AND p.financial_period = :period"
            params_pr["period"] = return_period

        rows_pr = session.execute(text(sql_pr), params_pr).fetchall()
        for r in rows_pr:
            tot_tax = Decimal(str(r.cgst)) + Decimal(str(r.sgst)) + Decimal(str(r.igst)) + Decimal(str(r.cess))
            exceptions.append({
                "exception_id": f"EXC-LOT-{r.id}",
                "category": "PARTIAL_RECEIPT",
                "document_number": r.document_number,
                "document_date": str(r.document_date) if r.document_date else None,
                "supplier_gstin": r.supplier_gstin,
                "supplier_name": r.supplier_legal_name,
                "taxable_value": Decimal(str(r.taxable_value)),
                "cgst_amount": Decimal(str(r.cgst)),
                "sgst_amount": Decimal(str(r.sgst)),
                "igst_amount": Decimal(str(r.igst)),
                "cess_amount": Decimal(str(r.cess)),
                "total_tax": tot_tax,
                "actionable_explanation": f"Consignment received in instalments ({r.lots_received_count or 0}/{r.total_lots_expected or 1} lots). Section 16(2)(b) First Proviso holds full credit.",
                "statutory_citation": "Section 16(2)(b) First Proviso: Full ITC claim available ONLY upon receipt of the final lot.",
                "suggested_action": "Record final delivery lot in Goods Receipt Lot Ledger once store receives final delivery.",
                "statutory_deadline": "Deferred until final lot delivery",
                "resolution_status": "OPEN",
                "source_reference_id": str(r.id),
                "created_at": datetime.now(timezone.utc).isoformat()
            })

    # -------------------------------------------------------------------------
    # 5. SUPPLIER_NON_FILING (Rule 37A: Supplier missed 30-Sep 3B deadline)
    # -------------------------------------------------------------------------
    if not category_filter or category_filter == "SUPPLIER_NON_FILING":
        sql_37a = """
        SELECT l.id, l.purchase_doc_id, l.tax_period, l.cgst_amount, l.sgst_amount, l.igst_amount,
               p.document_number, p.document_date, p.supplier_gstin, p.supplier_legal_name,
               p.total_taxable_amount
        FROM public.itc_ledger_entries l
        JOIN public.purchase_documents p ON l.purchase_doc_id = p.id
        WHERE l.org_id = :org
          AND l.event_type = 'REVERSAL_RULE_37A'
        """
        params_37a = {"org": org_id}
        if registration_id:
            sql_37a += " AND p.registration_id = :reg"
            params_37a["reg"] = registration_id
        if return_period:
            sql_37a += " AND l.tax_period = :period"
            params_37a["period"] = return_period

        rows_37a = session.execute(text(sql_37a), params_37a).fetchall()
        for r in rows_37a:
            tot_tax = Decimal(str(r.cgst_amount)) + Decimal(str(r.sgst_amount)) + Decimal(str(r.igst_amount))
            exceptions.append({
                "exception_id": f"EXC-37A-{r.id}",
                "category": "SUPPLIER_NON_FILING",
                "document_number": r.document_number,
                "document_date": str(r.document_date) if r.document_date else None,
                "supplier_gstin": r.supplier_gstin,
                "supplier_name": r.supplier_legal_name,
                "taxable_value": Decimal(str(r.total_taxable_amount or 0)),
                "cgst_amount": Decimal(str(r.cgst_amount)),
                "sgst_amount": Decimal(str(r.sgst_amount)),
                "igst_amount": Decimal(str(r.igst_amount)),
                "cess_amount": Decimal("0.00"),
                "total_tax": tot_tax,
                "actionable_explanation": "Supplier reported invoice in GSTR-1 but failed to furnish Form GSTR-3B by 30th September cut-off.",
                "statutory_citation": "Rule 37A CGST Rules: Reversal mandatory on or before 30th November to avoid Section 50(3) interest.",
                "suggested_action": "Reverse under Table 4(B)(2) in November GSTR-3B. Reclaimable under 4(A)(5)+4(D)(1) once vendor files 3B.",
                "statutory_deadline": "30th November (Strict Interest Cut-off)",
                "resolution_status": "OPEN",
                "source_reference_id": str(r.purchase_doc_id),
                "created_at": datetime.now(timezone.utc).isoformat()
            })

    # -------------------------------------------------------------------------
    # 6. RULE_37_OVERDUE (Inward invoices unpaid > 180 days)
    # -------------------------------------------------------------------------
    if not category_filter or category_filter == "RULE_37_OVERDUE":
        sql_37 = """
        SELECT p.id, p.document_number, p.document_date, p.supplier_gstin, p.supplier_legal_name,
               p.total_taxable_amount, p.cgst_amount, p.sgst_amount, p.igst_amount, p.cess_amount,
               p.total_invoice_value, p.financial_period,
               (CURRENT_DATE - p.document_date) as days_elapsed,
               (p.total_invoice_value - COALESCE((
                   SELECT SUM(a.allocated_total_amount)
                   FROM public.purchase_payment_allocations a
                   WHERE a.purchase_doc_id = p.id AND a.org_id = p.org_id
               ), 0)) as unpaid_amount
        FROM public.purchase_documents p
        WHERE p.org_id = :org
          AND p.status = 'approved'
          AND p.is_reverse_charge = FALSE
          AND (CURRENT_DATE - p.document_date) > 180
        """
        params_37 = {"org": org_id}
        if registration_id:
            sql_37 += " AND p.registration_id = :reg"
            params_37["reg"] = registration_id

        rows_37 = session.execute(text(sql_37), params_37).fetchall()
        for r in rows_37:
            unpaid = Decimal(str(r.unpaid_amount or 0))
            if unpaid > Decimal("0.00"):
                inv_val = Decimal(str(r.total_invoice_value or 1))
                tot_tax = (Decimal(str(r.cgst_amount or 0)) + Decimal(str(r.sgst_amount or 0)) +
                           Decimal(str(r.igst_amount or 0)) + Decimal(str(r.cess_amount or 0)))
                reversal_prop = (unpaid / inv_val) * tot_tax
                exceptions.append({
                    "exception_id": f"EXC-R37-{r.id}",
                    "category": "RULE_37_OVERDUE",
                    "document_number": r.document_number,
                    "document_date": str(r.document_date) if r.document_date else None,
                    "supplier_gstin": r.supplier_gstin,
                    "supplier_name": r.supplier_legal_name,
                    "taxable_value": Decimal(str(r.total_taxable_amount or 0)),
                    "cgst_amount": Decimal(str(r.cgst_amount or 0)),
                    "sgst_amount": Decimal(str(r.sgst_amount or 0)),
                    "igst_amount": Decimal(str(r.igst_amount or 0)),
                    "cess_amount": Decimal(str(r.cess_amount or 0)),
                    "total_tax": tot_tax,
                    "actionable_explanation": f"Vendor unpaid for {r.days_elapsed} days (> 180 days). Unpaid consideration: ₹{unpaid:.2f}.",
                    "statutory_citation": "Section 16(2) 2nd Proviso read with Rule 37: Proportionate reversal of ITC required.",
                    "suggested_action": "Discharge payment to vendor or reverse under Table 4(B)(2). Reclaimable upon subsequent payment.",
                    "statutory_deadline": "Immediately upon crossing 180 days",
                    "resolution_status": "OPEN",
                    "source_reference_id": str(r.id),
                    "created_at": datetime.now(timezone.utc).isoformat()
                })

    # -------------------------------------------------------------------------
    # 7. DUPLICATE_DOCUMENTS (Duplicate supplier GSTIN + document_number)
    # -------------------------------------------------------------------------
    if not category_filter or category_filter == "DUPLICATE_DOCUMENTS":
        sql_dup = """
        SELECT p.document_number, p.supplier_gstin, p.supplier_legal_name, COUNT(*) as cnt,
               MAX(p.id::text) as rep_id, SUM(p.total_taxable_amount) as taxable_sum,
               SUM(COALESCE(p.cgst_amount, 0) + COALESCE(p.sgst_amount, 0) + COALESCE(p.igst_amount, 0)) as tax_sum
        FROM public.purchase_documents p
        WHERE p.org_id = :org
        GROUP BY p.document_number, p.supplier_gstin, p.supplier_legal_name
        HAVING COUNT(*) > 1;
        """
        rows_dup = session.execute(text(sql_dup), {"org": org_id}).fetchall()
        for r in rows_dup:
            exceptions.append({
                "exception_id": f"EXC-DUP-{r.document_number}",
                "category": "DUPLICATE_DOCUMENTS",
                "document_number": r.document_number,
                "document_date": None,
                "supplier_gstin": r.supplier_gstin,
                "supplier_name": r.supplier_legal_name,
                "taxable_value": Decimal(str(r.taxable_sum or 0)),
                "cgst_amount": Decimal("0.00"),
                "sgst_amount": Decimal("0.00"),
                "igst_amount": Decimal(str(r.tax_sum or 0)),
                "cess_amount": Decimal("0.00"),
                "total_tax": Decimal(str(r.tax_sum or 0)),
                "actionable_explanation": f"Duplicate document detected ({r.cnt} occurrences in purchase register). Double claiming prohibited.",
                "statutory_citation": "Section 16(1) CGST Act: Taxpayer entitled to credit on single authentic supply document only.",
                "suggested_action": "De-duplicate purchase register; keep one approved entry and archive duplicates.",
                "statutory_deadline": "Before monthly filing",
                "resolution_status": "OPEN",
                "source_reference_id": str(r.rep_id),
                "created_at": datetime.now(timezone.utc).isoformat()
            })

    # -------------------------------------------------------------------------
    # 8. TAX_HEAD_MISMATCH (CGST+SGST vs IGST variance between 2B & books)
    # -------------------------------------------------------------------------
    if not category_filter or category_filter == "TAX_HEAD_MISMATCH":
        sql_thm = """
        SELECT m.id, p.document_number, p.document_date, p.supplier_gstin, p.supplier_legal_name,
               p.total_taxable_amount, p.cgst_amount as book_cgst, p.sgst_amount as book_sgst,
               p.igst_amount as book_igst, g.central_tax as portal_cgst, g.state_ut_tax as portal_sgst,
               g.integrated_tax as portal_igst
        FROM public.reconciliation_matches m
        JOIN public.purchase_documents p ON m.purchase_doc_id = p.id
        JOIN public.gstr2b_source_documents g ON m.gstr2b_doc_id = g.id
        WHERE m.org_id = :org
          AND (
              (COALESCE(p.cgst_amount, 0) + COALESCE(p.sgst_amount, 0) > 0 AND COALESCE(g.integrated_tax, 0) > 0)
              OR (COALESCE(p.igst_amount, 0) > 0 AND COALESCE(g.central_tax, 0) + COALESCE(g.state_ut_tax, 0) > 0)
          )
        """
        rows_thm = session.execute(text(sql_thm), {"org": org_id}).fetchall()
        for r in rows_thm:
            exceptions.append({
                "exception_id": f"EXC-THM-{r.id}",
                "category": "TAX_HEAD_MISMATCH",
                "document_number": r.document_number,
                "document_date": str(r.document_date) if r.document_date else None,
                "supplier_gstin": r.supplier_gstin,
                "supplier_name": r.supplier_legal_name,
                "taxable_value": Decimal(str(r.total_taxable_amount or 0)),
                "cgst_amount": Decimal(str(r.book_cgst or 0)),
                "sgst_amount": Decimal(str(r.book_sgst or 0)),
                "igst_amount": Decimal(str(r.book_igst or 0)),
                "cess_amount": Decimal("0.00"),
                "total_tax": Decimal(str(r.book_cgst or 0)) + Decimal(str(r.book_sgst or 0)) + Decimal(str(r.book_igst or 0)),
                "actionable_explanation": f"Tax-head discrepancy: Portal reported IGST ₹{r.portal_igst} vs Books recorded CGST/SGST ₹{r.book_cgst}/{r.book_sgst}.",
                "statutory_citation": "Section 12 / Section 13 IGST Act: Determination of intra-state vs inter-state supply.",
                "suggested_action": "Request supplier to amend place of supply in GSTR-1 Table 9 or correct purchase ledger classification.",
                "statutory_deadline": "Supplier amendment in subsequent GSTR-1",
                "resolution_status": "OPEN",
                "source_reference_id": str(r.id),
                "created_at": datetime.now(timezone.utc).isoformat()
            })

    # -------------------------------------------------------------------------
    # 9. CREDIT_NOTE_DISCREPANCY (Credit notes in GSTR-2B or books pending resolution)
    # -------------------------------------------------------------------------
    if not category_filter or category_filter == "CREDIT_NOTE_DISCREPANCY":
        sql_cnd = """
        SELECT d.id, d.document_number, d.document_date, d.supplier_gstin, d.supplier_name,
               COALESCE(d.taxable_value, 0) as taxable_value,
               COALESCE(d.central_tax, 0) as cgst, COALESCE(d.state_ut_tax, 0) as sgst,
               COALESCE(d.integrated_tax, 0) as igst, COALESCE(d.cess_amount, 0) as cess,
               a.current_action
        FROM public.gstr2b_source_documents d
        LEFT JOIN public.ims_document_actions a ON a.gstr2b_doc_id = d.id AND a.org_id = d.org_id
        WHERE d.org_id = :org
          AND (d.document_type ILIKE '%credit%' OR d.section_name ILIKE '%cdnr%')
          AND (a.current_action IS NULL OR a.current_action = 'PENDING')
        """
        rows_cnd = session.execute(text(sql_cnd), {"org": org_id}).fetchall()
        for r in rows_cnd:
            tot_tax = Decimal(str(r.cgst)) + Decimal(str(r.sgst)) + Decimal(str(r.igst)) + Decimal(str(r.cess))
            exceptions.append({
                "exception_id": f"EXC-CND-{r.id}",
                "category": "CREDIT_NOTE_DISCREPANCY",
                "document_number": r.document_number,
                "document_date": str(r.document_date) if r.document_date else None,
                "supplier_gstin": r.supplier_gstin,
                "supplier_name": r.supplier_name,
                "taxable_value": Decimal(str(r.taxable_value)),
                "cgst_amount": Decimal(str(r.cgst)),
                "sgst_amount": Decimal(str(r.sgst)),
                "igst_amount": Decimal(str(r.igst)),
                "cess_amount": Decimal(str(r.cess)),
                "total_tax": tot_tax,
                "actionable_explanation": "Credit note communicated in GSTR-2B without ACCEPT/REJECT resolution in IMS.",
                "statutory_citation": "Section 34 CGST Act: Recipient must reduce ITC when credit note is issued.",
                "suggested_action": "Take explicit ACCEPT or REJECT action in IMS Workbench. (Statutory rule: Credit Notes cannot remain PENDING).",
                "statutory_deadline": "Prior to GSTR-3B filing freeze",
                "resolution_status": "OPEN",
                "source_reference_id": str(r.id),
                "created_at": datetime.now(timezone.utc).isoformat()
            })

    # -------------------------------------------------------------------------
    # 10. MISSING_SUPPORTING_EVIDENCE (Remediated historical/new incomplete docs)
    # -------------------------------------------------------------------------
    if not category_filter or category_filter == "MISSING_SUPPORTING_EVIDENCE":
        sql_mse = """
        SELECT a.id, a.purchase_doc_id, a.document_number, a.goods_services_type,
               a.previous_status, a.new_status, a.remediation_reason, a.evidence_snapshot,
               p.supplier_gstin, p.supplier_legal_name, p.total_taxable_amount,
               COALESCE(p.cgst_amount, 0) as cgst, COALESCE(p.sgst_amount, 0) as sgst,
               COALESCE(p.igst_amount, 0) as igst, COALESCE(p.cess_amount, 0) as cess
        FROM public.receipt_remediation_audit a
        JOIN public.purchase_documents p ON a.purchase_doc_id = p.id
        WHERE a.org_id = :org
        """
        rows_mse = session.execute(text(sql_mse), {"org": org_id}).fetchall()
        for r in rows_mse:
            tot_tax = Decimal(str(r.cgst)) + Decimal(str(r.sgst)) + Decimal(str(r.igst)) + Decimal(str(r.cess))
            exceptions.append({
                "exception_id": f"EXC-REMED-{r.id}",
                "category": "MISSING_SUPPORTING_EVIDENCE",
                "document_number": r.document_number,
                "document_date": None,
                "supplier_gstin": r.supplier_gstin,
                "supplier_name": r.supplier_legal_name,
                "taxable_value": Decimal(str(r.total_taxable_amount or 0)),
                "cgst_amount": Decimal(str(r.cgst)),
                "sgst_amount": Decimal(str(r.sgst)),
                "igst_amount": Decimal(str(r.igst)),
                "cess_amount": Decimal(str(r.cess)),
                "total_tax": tot_tax,
                "actionable_explanation": f"Document receipt downgraded from {r.previous_status} to {r.new_status}: {r.remediation_reason}.",
                "statutory_citation": "Section 16(2)(b) CGST Act complete evidence mandate.",
                "suggested_action": "Furnish complete evidence (receipt date, GRN/challan, quantity, or acceptance cert) to restore receipt verification.",
                "statutory_deadline": "Immediate remediation required for credit release",
                "resolution_status": "OPEN",
                "source_reference_id": str(r.purchase_doc_id),
                "created_at": datetime.now(timezone.utc).isoformat()
            })

    return exceptions
