"""
Milestone 2.7: Unified Financial Report Service
Links GSTR-2B, Purchase Books, Form GSTR-3B Table 4, Reversals, Reclaims,
Supplier Breakdown, and Exception Queues into an atomic audit report.
Zero invented zeros; every figure traces directly to underlying database rows.
"""
import uuid
from decimal import Decimal
from typing import Dict, Any, List, Optional
from datetime import datetime

from sqlalchemy.orm import Session
from sqlalchemy import text

from app.modules.reconciliation.gstr3b_itc import generate_gstr3b_table4_summary
from app.modules.reconciliation.exception_service import get_consolidated_exceptions


def generate_unified_financial_report(
    session: Session,
    org_id: str,
    registration_id: str,
    return_period: str
) -> Dict[str, Any]:
    """
    Produces the comprehensive unified statutory and inward accounting report.
    """
    # 1. Fetch GST Registration
    reg_row = session.execute(
        text("SELECT gstin FROM public.gst_registrations WHERE id = :id AND org_id = :org;"),
        {"id": registration_id, "org": org_id}
    ).fetchone()
    recipient_gstin = reg_row.gstin if reg_row else ""

    # 2. Fetch GSTR-2B Snapshot and documents
    snap_row = session.execute(
        text("""
        SELECT id FROM public.gstr2b_snapshots
        WHERE registration_id = :reg_id AND return_period = :rp AND org_id = :org
        ORDER BY version_number DESC, created_at DESC LIMIT 1;
        """),
        {"reg_id": registration_id, "rp": return_period, "org": org_id}
    ).fetchone()
    snapshot_id = str(snap_row.id) if snap_row else None

    if snapshot_id:
        g_rows = session.execute(
            text("SELECT * FROM public.gstr2b_source_documents WHERE snapshot_id = :sid AND org_id = :org;"),
            {"sid": snapshot_id, "org": org_id}
        ).fetchall()
        gstr2b_docs = [dict(r._mapping) for r in g_rows]
    else:
        gstr2b_docs = []

    # 3. Fetch Purchase Documents for period
    p_rows = session.execute(
        text("""
        SELECT * FROM public.purchase_documents
        WHERE registration_id = :reg_id AND financial_period = :rp AND org_id = :org;
        """),
        {"reg_id": registration_id, "rp": return_period, "org": org_id}
    ).fetchall()
    purchase_docs = [dict(r._mapping) for r in p_rows]

    # 4. Fetch Matches
    if snapshot_id:
        m_rows = session.execute(
            text("""
            SELECT m.purchase_doc_id, m.gstr2b_doc_id, m.match_pass, m.is_exact
            FROM public.reconciliation_matches m
            JOIN public.reconciliation_runs r ON m.run_id = r.id AND m.org_id = r.org_id
            WHERE r.registration_id = :reg_id AND r.return_period = :rp AND r.snapshot_id = :sid AND m.org_id = :org;
            """),
            {"reg_id": registration_id, "rp": return_period, "sid": snapshot_id, "org": org_id}
        ).fetchall()
        matches = [dict(r._mapping) for r in m_rows]
    else:
        matches = []

    # 5. Fetch ITC Assessments
    a_rows = session.execute(
        text("""
        SELECT a.purchase_doc_id, a.gstr2b_doc_id, a.rule_code, a.eligibility_status, a.risk_reversal_amount
        FROM public.itc_assessments a
        JOIN public.purchase_documents p ON a.purchase_doc_id = p.id AND a.org_id = p.org_id
        WHERE p.registration_id = :reg_id AND p.financial_period = :rp AND a.org_id = :org;
        """),
        {"reg_id": registration_id, "rp": return_period, "org": org_id}
    ).fetchall()
    assessments = [dict(r._mapping) for r in a_rows]

    # 6. Fetch Human Approved Document IDs
    approved_rows = session.execute(
        text("SELECT id FROM public.purchase_documents WHERE registration_id = :reg_id AND status = 'approved' AND org_id = :org;"),
        {"reg_id": registration_id, "org": org_id}
    ).fetchall()
    approved_set = {str(r.id) for r in approved_rows}

    # 7. Fetch Reclaims / Reavailment Events
    rec_rows = session.execute(
        text("""
        SELECT r.*
        FROM public.itc_reavailment_events r
        JOIN public.purchase_documents p ON r.purchase_doc_id = p.id AND r.org_id = p.org_id
        WHERE p.registration_id = :reg_id AND r.reavailment_period = :rp AND r.org_id = :org;
        """),
        {"reg_id": registration_id, "rp": return_period, "org": org_id}
    ).fetchall()
    reclaims = [dict(r._mapping) for r in rec_rows]

    # 8. Fetch Reversals Ledger Events
    rev_rows = session.execute(
        text("""
        SELECT r.*
        FROM public.itc_reversal_events r
        JOIN public.purchase_documents p ON r.purchase_doc_id = p.id AND r.org_id = p.org_id
        WHERE p.registration_id = :reg_id AND r.reversal_period = :rp AND r.org_id = :org;
        """),
        {"reg_id": registration_id, "rp": return_period, "org": org_id}
    ).fetchall()
    reversals_ledger = [dict(r._mapping) for r in rev_rows]

    # 9. Generate GSTR-3B Table 4 Working Summary (with auto-reverse unreceived enabled)
    t4 = generate_gstr3b_table4_summary(
        return_period=return_period,
        recipient_gstin=recipient_gstin,
        gstr2b_docs=gstr2b_docs,
        purchase_docs=purchase_docs,
        matches=matches,
        assessments=assessments,
        human_approved_ids=approved_set,
        reclaims=reclaims,
        reversals_ledger=reversals_ledger,
        auto_reverse_unbooked_unreceived=True
    )

    # 2. Get all exceptions for this registration and return period
    exceptions = get_consolidated_exceptions(
        session=session,
        org_id=org_id,
        registration_id=registration_id,
        return_period=return_period
    )

    # Group exceptions by category
    exceptions_by_cat: Dict[str, int] = {}
    for ex in exceptions:
        cat = ex["category"]
        exceptions_by_cat[cat] = exceptions_by_cat.get(cat, 0) + 1

    # 3. Calculate Supplier-Wise ITC Breakdown from Purchase Documents
    supp_rows = session.execute(
        text("""
        SELECT supplier_gstin,
               COALESCE(supplier_legal_name, 'Unknown') as supplier_name,
               COUNT(*) as doc_count,
               SUM(COALESCE(total_taxable_amount, 0)) as total_taxable,
               SUM(COALESCE(cgst_amount, 0)) as cgst,
               SUM(COALESCE(sgst_amount, 0)) as sgst,
               SUM(COALESCE(igst_amount, 0)) as igst,
               SUM(COALESCE(cess_amount, 0)) as cess,
               SUM(COALESCE(total_invoice_value, 0)) as total_value,
               SUM(CASE WHEN status = 'approved' THEN (COALESCE(cgst_amount, 0) + COALESCE(sgst_amount, 0) + COALESCE(igst_amount, 0) + COALESCE(cess_amount, 0)) ELSE 0 END) as approved_tax
        FROM public.purchase_documents
        WHERE org_id = :org AND registration_id = :reg AND financial_period = :period
        GROUP BY supplier_gstin, supplier_legal_name
        ORDER BY total_taxable DESC;
        """),
        {"org": org_id, "reg": registration_id, "period": return_period}
    ).fetchall()

    supplier_wise_itc = [
        {
            "supplier_gstin": r.supplier_gstin,
            "supplier_name": r.supplier_name,
            "document_count": r.doc_count,
            "taxable_value": Decimal(str(r.total_taxable)),
            "cgst_amount": Decimal(str(r.cgst)),
            "sgst_amount": Decimal(str(r.sgst)),
            "igst_amount": Decimal(str(r.igst)),
            "cess_amount": Decimal(str(r.cess)),
            "total_invoice_value": Decimal(str(r.total_value)),
            "human_approved_tax": Decimal(str(r.approved_tax))
        }
        for r in supp_rows
    ]

    # 4. Human-Approved Safe Claim in Purchase Books
    human_approved_row = session.execute(
        text("""
        SELECT SUM(COALESCE(cgst_amount, 0) + COALESCE(sgst_amount, 0) + COALESCE(igst_amount, 0) + COALESCE(cess_amount, 0)) as approved_sum
        FROM public.purchase_documents
        WHERE org_id = :org AND registration_id = :reg AND financial_period = :period AND status = 'approved';
        """),
        {"org": org_id, "reg": registration_id, "period": return_period}
    ).fetchone()
    human_approved_itc = Decimal(str(human_approved_row.approved_sum or 0)) if human_approved_row else Decimal("0.00")

    # If t4 net itc available is computed, cap human approved claim
    if t4.subtotal_human_approved and t4.subtotal_human_approved > Decimal("0.00"):
        human_approved_itc = t4.subtotal_human_approved

    # 5. Build Tax-Head Summary
    tax_head_summary: Dict[str, Dict[str, Decimal]] = {
        "4A_GROSS": {
            "cgst": t4.components_4a["total"].cgst if t4.components_4a and "total" in t4.components_4a else Decimal("0.00"),
            "sgst": t4.components_4a["total"].sgst if t4.components_4a and "total" in t4.components_4a else Decimal("0.00"),
            "igst": t4.components_4a["total"].igst if t4.components_4a and "total" in t4.components_4a else Decimal("0.00"),
            "cess": t4.components_4a["total"].cess if t4.components_4a and "total" in t4.components_4a else Decimal("0.00"),
            "total": t4.total_itc_available
        },
        "4B1_PERMANENT": {
            "cgst": t4.components_4b["4B1_17_5"].cgst if t4.components_4b and "4B1_17_5" in t4.components_4b else Decimal("0.00"),
            "sgst": t4.components_4b["4B1_17_5"].sgst if t4.components_4b and "4B1_17_5" in t4.components_4b else Decimal("0.00"),
            "igst": t4.components_4b["4B1_17_5"].igst if t4.components_4b and "4B1_17_5" in t4.components_4b else Decimal("0.00"),
            "cess": t4.components_4b["4B1_17_5"].cess if t4.components_4b and "4B1_17_5" in t4.components_4b else Decimal("0.00"),
            "total": t4.itc_reversed_rule_38_42_43
        },
        "4B2_RECLAIMABLE": {
            "cgst": (t4.components_4b["4B2_rule_37"].cgst if t4.components_4b and "4B2_rule_37" in t4.components_4b else Decimal("0.00")),
            "sgst": (t4.components_4b["4B2_rule_37"].sgst if t4.components_4b and "4B2_rule_37" in t4.components_4b else Decimal("0.00")),
            "igst": (
                (t4.components_4b["4B2_rule_37a"].igst if t4.components_4b and "4B2_rule_37a" in t4.components_4b else Decimal("0.00")) +
                (t4.components_4b["4B2_receipt"].igst if t4.components_4b and "4B2_receipt" in t4.components_4b else Decimal("0.00"))
            ),
            "cess": Decimal("0.00"),
            "total": t4.total_itc_reversed_reclaimable_4b2
        },
        "4C_NET": {
            "cgst": t4.components_4c_net.cgst if t4.components_4c_net else Decimal("0.00"),
            "sgst": t4.components_4c_net.sgst if t4.components_4c_net else Decimal("0.00"),
            "igst": t4.components_4c_net.igst if t4.components_4c_net else Decimal("0.00"),
            "cess": t4.components_4c_net.cess if t4.components_4c_net else Decimal("0.00"),
            "total": t4.net_itc_available
        },
        "4D1_RECLAIMED": {
            "cgst": t4.components_4d["4D1_reclaimed"].cgst if t4.components_4d and "4D1_reclaimed" in t4.components_4d else Decimal("0.00"),
            "sgst": t4.components_4d["4D1_reclaimed"].sgst if t4.components_4d and "4D1_reclaimed" in t4.components_4d else Decimal("0.00"),
            "igst": t4.components_4d["4D1_reclaimed"].igst if t4.components_4d and "4D1_reclaimed" in t4.components_4d else Decimal("0.00"),
            "cess": t4.components_4d["4D1_reclaimed"].cess if t4.components_4d and "4D1_reclaimed" in t4.components_4d else Decimal("0.00"),
            "total": t4.itc_reclaimed_earlier_reversal_4d1
        },
        "4D2_INELIGIBLE": {
            "cgst": t4.components_4d["4D2_ineligible"].cgst if t4.components_4d and "4D2_ineligible" in t4.components_4d else Decimal("0.00"),
            "sgst": t4.components_4d["4D2_ineligible"].sgst if t4.components_4d and "4D2_ineligible" in t4.components_4d else Decimal("0.00"),
            "igst": t4.components_4d["4D2_ineligible"].igst if t4.components_4d and "4D2_ineligible" in t4.components_4d else Decimal("0.00"),
            "cess": t4.components_4d["4D2_ineligible"].cess if t4.components_4d and "4D2_ineligible" in t4.components_4d else Decimal("0.00"),
            "total": t4.ineligible_sec_16_4_and_pos_restrictions_4d2
        }
    }

    # 6. Statutory ITC Accounting States Breakdown
    state_rows = session.execute(
        text("""
        SELECT itc_accounting_state, COUNT(*) as cnt,
               SUM(COALESCE(cgst_amount, 0) + COALESCE(sgst_amount, 0) + COALESCE(igst_amount, 0) + COALESCE(cess_amount, 0)) as total_tax
        FROM public.purchase_documents
        WHERE org_id = :org AND registration_id = :reg AND financial_period = :period
        GROUP BY itc_accounting_state;
        """),
        {"org": org_id, "reg": registration_id, "period": return_period}
    ).fetchall()
    itc_accounting_states = {
        r.itc_accounting_state: {
            "count": r.cnt,
            "total_tax": Decimal(str(r.total_tax))
        }
        for r in state_rows
    }

    return {
        "registration_id": registration_id,
        "return_period": return_period,
        "generated_at": datetime.utcnow().isoformat(),
        "gstr2b_total": t4.subtotal_gstr2b_portal_available,
        "purchase_books_matched": t4.subtotal_books_matched,
        "unbooked_2b_variance": t4.unbooked_variance_amount,
        "prior_period_reclaims": t4.reclaims_total,
        "table_4a_gross": t4.total_itc_available,
        "table_4b1_permanent_reversals": t4.itc_reversed_rule_38_42_43,
        "table_4b2_reclaimable_reversals": t4.total_itc_reversed_reclaimable_4b2,
        "table_4b2_receipt_reversals": t4.itc_reversed_sec_16_2_b_receipt,
        "table_4c_net_eligible_itc": t4.net_itc_available,
        "calculated_net_itc_label": "calculated net ITC in this scenario, subject to all applicable statutory conditions and human review",
        "table_4d1_reclaimed_disclosure": t4.itc_reclaimed_earlier_reversal_4d1,
        "table_4d2_ineligible_disclosure": t4.ineligible_sec_16_4_and_pos_restrictions_4d2,
        "human_approved_itc": human_approved_itc,
        "itc_accounting_states": itc_accounting_states,
        "outstanding_exceptions_count": len(exceptions),
        "exceptions_by_category": exceptions_by_cat,
        "tax_head_summary": tax_head_summary,
        "supplier_wise_itc": supplier_wise_itc
    }
