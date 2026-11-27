import uuid
import json
from decimal import Decimal
from typing import Optional, List, Dict, Any
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import set_tenant_context
from app.modules.ai_assistant.schemas import GroundingContextSummary, QuickInsightOut

def get_tenant_grounding_context(
    session: Session,
    org_id: str,
    user_id: Optional[str] = None
) -> GroundingContextSummary:
    """
    Extracts tenant-specific accounting, GST compliance, and credit readiness metrics
    to ground AI reasoning without hallucinatory leakage.
    """
    set_tenant_context(session, org_id=org_id, user_id=user_id)

    # 1. Org Legal Name & GSTIN
    org_row = session.execute(
        text("SELECT legal_name, pan FROM public.organizations WHERE id = :org;"),
        {"org": org_id}
    ).fetchone()
    legal_name = org_row.legal_name if org_row else "MSME Client"

    reg_row = session.execute(
        text("SELECT gstin FROM public.gst_registrations WHERE org_id = :org AND is_primary = true;"),
        {"org": org_id}
    ).fetchone()
    gstin = reg_row.gstin if reg_row else None

    # 2. Latest GSTR-3B Filing or Draft
    filing_row = session.execute(
        text("""
        SELECT tax_period, filing_status, offset_data
        FROM public.gstr3b_filings
        WHERE org_id = :org
        ORDER BY created_at DESC
        LIMIT 1;
        """),
        {"org": org_id}
    ).fetchone()

    latest_period = filing_row.tax_period if filing_row else None
    net_tax = Decimal("0.00")
    if filing_row and filing_row.offset_data:
        try:
            offset_dict = filing_row.offset_data if isinstance(filing_row.offset_data, dict) else json.loads(filing_row.offset_data)
            if "total_challan_required" in offset_dict:
                net_tax = Decimal(str(offset_dict.get("total_challan_required", "0.00")))
            else:
                pmt = offset_dict.get("pmt06_cash_required") or {}
                net_tax = Decimal(str(pmt.get("total", "0.00"))) if isinstance(pmt, dict) else Decimal(str(pmt or "0.00"))
        except Exception:
            net_tax = Decimal("0.00")

    # 3. Pending purchase documents requiring reconciliation
    pending_count = session.execute(
        text("""
        SELECT COUNT(*)
        FROM public.purchase_documents
        WHERE org_id = :org AND status IN ('draft', 'pending_approval');
        """),
        {"org": org_id}
    ).scalar() or 0

    # 4. Total ITC eligible from approved purchase documents
    itc_balance = session.execute(
        text("""
        SELECT COALESCE(SUM(COALESCE(cgst_amount, 0) + COALESCE(sgst_amount, 0) + COALESCE(igst_amount, 0) + COALESCE(cess_amount, 0)), 0.00)
        FROM public.purchase_documents
        WHERE org_id = :org AND status = 'approved';
        """),
        {"org": org_id}
    ).scalar() or Decimal("0.00")

    # 5. MSME Credit Readiness Score
    prof_row = session.execute(
        text("""
        SELECT credit_score FROM public.business_profiles
        WHERE org_id = :org
        ORDER BY updated_at DESC NULLS LAST, created_at DESC NULLS LAST
        LIMIT 1;
        """),
        {"org": org_id}
    ).fetchone()
    credit_score = prof_row.credit_score if prof_row and prof_row.credit_score else None

    # 6. Top recommended scheme
    scheme_match_row = session.execute(
        text("""
        SELECT s.name
        FROM public.scheme_matches sm
        JOIN public.government_schemes s ON sm.scheme_id = s.id
        WHERE sm.org_id = :org AND sm.status = 'recommended'
        ORDER BY sm.match_score DESC
        LIMIT 1;
        """),
        {"org": org_id}
    ).fetchone()
    top_scheme = scheme_match_row.name if scheme_match_row else None

    statutory_citations = [
        "CGST Act Sec 16(2) — Four conditions for valid ITC entitlement",
        "CGST Act Sec 16(4) — Annual deadline (30th Nov) to claim preceding year ITC",
        "CGST Rule 37 — Proportionate ITC reversal for unpaid supplier invoices exceeding 180 days",
        "CGST Rule 37A — Mandatory reversal where supplier defaulted on GSTR-3B filing",
        "CGST Rule 88A & Sec 49/49A/49B — Order of utilization of Input Tax Credit"
    ]

    return GroundingContextSummary(
        org_legal_name=legal_name,
        gstin=gstin,
        latest_return_period=latest_period,
        net_tax_liability=net_tax,
        eligible_itc=Decimal(str(itc_balance)),
        pending_vendor_invoices=pending_count,
        msme_readiness_score=credit_score,
        top_recommended_scheme=top_scheme,
        statutory_citations=statutory_citations
    )

def generate_quick_insights(
    session: Session,
    org_id: str,
    user_id: Optional[str] = None
) -> List[QuickInsightOut]:
    """
    Generates proactive statutory alerts and financial advisory insights.
    """
    ctx = get_tenant_grounding_context(session, org_id, user_id)
    insights: List[QuickInsightOut] = []

    # 1. Rule 88A Optimization
    insights.append(QuickInsightOut(
        id=f"ins-{uuid.uuid4().hex[:8]}",
        category="Statutory GST",
        title="Rule 88A Credit Utilization Applied",
        summary="Your IGST credit balance is prioritized for full exhaustion before cross-utilizing CGST or SGST, strictly preventing cross-head offsetting.",
        statutory_reference="CGST Rules, Rule 88A & Section 49(5)",
        severity="info",
        action_link="/gstr3b"
    ))

    # 2. Rule 37 Supplier Payment Warning
    if ctx.pending_vendor_invoices > 0:
        insights.append(QuickInsightOut(
            id=f"ins-{uuid.uuid4().hex[:8]}",
            category="Compliance Risk",
            title=f"{ctx.pending_vendor_invoices} Vendor Invoices Pending Payment Reconciliations",
            summary="Ensure payments to suppliers are settled within 180 days of invoice date to avoid mandatory Rule 37 ITC reversals and 18% annual interest under Section 50.",
            statutory_reference="CGST Act 2nd Proviso to Sec 16(2) read with Rule 37",
            severity="warning",
            action_link="/reconciliation"
        ))

    # 3. Credit Readiness & Loan Scheme
    if ctx.msme_readiness_score is not None:
        scheme_note = f"unlocks eligibility for {ctx.top_recommended_scheme}" if ctx.top_recommended_scheme else "supports MSME lending readiness"
        insights.append(QuickInsightOut(
            id=f"ins-{uuid.uuid4().hex[:8]}",
            category="Credit & Financing",
            title=f"MSME Readiness Index: {ctx.msme_readiness_score}/900",
            summary=f"Your verified cash flow regularity and GST compliance {scheme_note}.",
            statutory_reference="RBI MSME Priority Sector Lending Guidelines",
            severity="success" if ctx.msme_readiness_score >= 650 else "info",
            action_link="/credit-builder"
        ))
    else:
        insights.append(QuickInsightOut(
            id=f"ins-{uuid.uuid4().hex[:8]}",
            category="Credit & Financing",
            title="MSME Readiness Index: Evaluation Pending",
            summary="Upload bank/UPI statements in Credit Builder and run Scheme Navigator to compute your live readiness score.",
            statutory_reference="RBI MSME Priority Sector Lending Guidelines",
            severity="info",
            action_link="/credit-builder"
        ))

    # 4. Section 16(4) Filing Deadline Reminder
    insights.append(QuickInsightOut(
        id=f"ins-{uuid.uuid4().hex[:8]}",
        category="Statutory Deadline",
        title="Section 16(4) Annual ITC Claim Cut-Off",
        summary="Input tax credit for invoices of previous financial year must be availed in GSTR-3B filed on or before 30th November. Late claims are permanently extinguished.",
        statutory_reference="CGST Act, Section 16(4)",
        severity="info",
        action_link="/gstr3b"
    ))

    return insights
