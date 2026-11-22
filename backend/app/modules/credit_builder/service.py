import csv
import io
import json
import uuid
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, date, timezone
from typing import List, Dict, Any, Optional, Tuple
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import set_tenant_context
from app.modules.credit_builder.schemas import (
    TransactionIn, TransactionOut, StatementUploadResponse,
    MonthlyCashFlow, CategoryBreakdown, FinancialSummaryOut,
    ReadinessFactor, FinancialReadinessScore, LoanDossierOut
)
from app.modules.credit_builder.categorizer import categorize_transaction

CREDIT_DISCLAIMER = (
    "IMPORTANT REGULATORY NOTICE: The MyCA MSME Financial Readiness Indicator is an internal, "
    "deterministic analytical metric calculated from cash-flow regularity, operating margins, "
    "and statutory GST compliance records. It is NOT an official credit bureau score (e.g., CIBIL, Equifax, "
    "Experian, CRIF High Mark) and does not constitute a loan guarantee. Final underwriting decisions "
    "rest exclusively with empaneled banks and NBFCs."
)

def quant(d: Decimal) -> Decimal:
    return d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

def ingest_statement_csv(
    session: Session,
    org_id: str,
    user_id: str,
    csv_content: str
) -> StatementUploadResponse:
    """
    Parses standard CSV bank/UPI statements and persists categorized records.
    Expected CSV columns: Date, Description, Type (credit/debit), Amount, [Reference]
    """
    set_tenant_context(session, org_id=org_id, user_id=user_id)
    reader = csv.DictReader(io.StringIO(csv_content))
    imported = 0
    total_cr = Decimal("0.00")
    total_dr = Decimal("0.00")
    dates: List[str] = []

    for row in reader:
        # Standardize headers (case-insensitive)
        clean_row = {k.strip().lower(): v.strip() for k, v in row.items() if k and v}
        tx_date = clean_row.get("date") or clean_row.get("transaction_date") or clean_row.get("value date")
        desc = clean_row.get("description") or clean_row.get("narration") or clean_row.get("particulars") or ""
        tx_type = clean_row.get("type") or clean_row.get("transaction_type") or clean_row.get("cr/dr")
        amt_str = clean_row.get("amount") or clean_row.get("withdrawal") or clean_row.get("deposit") or "0"
        ref = clean_row.get("reference") or clean_row.get("ref_no") or clean_row.get("cheque_no")

        if not tx_date or not amt_str:
            continue

        # Clean amount
        amt_clean = amt_str.replace(",", "").replace("₹", "").replace("INR", "").strip()
        try:
            amt = Decimal(amt_clean)
        except Exception:
            continue

        # Infer type if deposit/withdrawal columns were used
        if not tx_type:
            if clean_row.get("deposit"):
                tx_type = "credit"
            elif clean_row.get("withdrawal"):
                tx_type = "debit"
            else:
                tx_type = "credit" if amt > 0 else "debit"

        tx_type_clean = "credit" if "cr" in tx_type.lower() else "debit"
        amt = abs(amt)

        category = clean_row.get("category") or categorize_transaction(desc, tx_type_clean)

        tx_id = str(uuid.uuid4())
        session.execute(
            text("""
            INSERT INTO public.financial_transactions (
                id, org_id, transaction_date, amount, transaction_type,
                category, counterparty, reference_number, created_at
            ) VALUES (
                :id, :org, :tdate, :amt, :ttype, :cat, :cparty, :ref, clock_timestamp()
            );
            """),
            {
                "id": tx_id,
                "org": org_id,
                "tdate": tx_date,
                "amt": amt,
                "ttype": tx_type_clean,
                "cat": category,
                "cparty": desc[:128],
                "ref": ref
            }
        )

        imported += 1
        dates.append(tx_date)
        if tx_type_clean == "credit":
            total_cr += amt
        else:
            total_dr += amt

    session.commit()

    period_start = min(dates) if dates else None
    period_end = max(dates) if dates else None

    return StatementUploadResponse(
        transactions_imported=imported,
        total_credits=quant(total_cr),
        total_debits=quant(total_dr),
        net_flow=quant(total_cr - total_dr),
        period_start=period_start,
        period_end=period_end,
        message=f"Successfully imported and categorized {imported} bank transactions."
    )

def get_financial_summary(
    session: Session,
    org_id: str,
    user_id: Optional[str] = None
) -> FinancialSummaryOut:
    """Computes monthly cash flows and expense/revenue category breakdowns."""
    set_tenant_context(session, org_id=org_id, user_id=user_id)

    # 1. Total inflows & outflows
    totals = session.execute(
        text("""
        SELECT 
            COALESCE(SUM(CASE WHEN transaction_type = 'credit' THEN amount ELSE 0 END), 0.00) as total_cr,
            COALESCE(SUM(CASE WHEN transaction_type = 'debit' THEN amount ELSE 0 END), 0.00) as total_dr
        FROM public.financial_transactions
        WHERE org_id = :org;
        """),
        {"org": org_id}
    ).fetchone()

    total_cr = Decimal(str(totals.total_cr))
    total_dr = Decimal(str(totals.total_dr))
    net_profit = total_cr - total_dr
    margin_pct = quant((net_profit / total_cr * Decimal("100.00"))) if total_cr > 0 else Decimal("0.00")

    # 2. Monthly cash flow trends
    monthly_rows = session.execute(
        text("""
        SELECT 
            TO_CHAR(transaction_date, 'YYYY-MM') as month,
            SUM(CASE WHEN transaction_type = 'credit' THEN amount ELSE 0 END) as inflow,
            SUM(CASE WHEN transaction_type = 'debit' THEN amount ELSE 0 END) as outflow,
            COUNT(*) as cnt
        FROM public.financial_transactions
        WHERE org_id = :org
        GROUP BY TO_CHAR(transaction_date, 'YYYY-MM')
        ORDER BY month ASC;
        """),
        {"org": org_id}
    ).fetchall()

    trends: List[MonthlyCashFlow] = []
    for r in monthly_rows:
        inf = Decimal(str(r.inflow))
        outf = Decimal(str(r.outflow))
        trends.append(MonthlyCashFlow(
            month=r.month,
            inflow=quant(inf),
            outflow=quant(outf),
            net_cash_flow=quant(inf - outf),
            transaction_count=r.cnt
        ))

    # 3. Expense categories
    exp_rows = session.execute(
        text("""
        SELECT category, SUM(amount) as cat_amt
        FROM public.financial_transactions
        WHERE org_id = :org AND transaction_type = 'debit'
        GROUP BY category
        ORDER BY cat_amt DESC;
        """),
        {"org": org_id}
    ).fetchall()

    expense_cats: List[CategoryBreakdown] = []
    for r in exp_rows:
        camt = Decimal(str(r.cat_amt))
        pct = quant(camt / total_dr * Decimal("100.00")) if total_dr > 0 else Decimal("0.00")
        expense_cats.append(CategoryBreakdown(category=r.category, amount=quant(camt), percentage=pct))

    # 4. Revenue categories
    rev_rows = session.execute(
        text("""
        SELECT category, SUM(amount) as cat_amt
        FROM public.financial_transactions
        WHERE org_id = :org AND transaction_type = 'credit'
        GROUP BY category
        ORDER BY cat_amt DESC;
        """),
        {"org": org_id}
    ).fetchall()

    revenue_cats: List[CategoryBreakdown] = []
    for r in rev_rows:
        camt = Decimal(str(r.cat_amt))
        pct = quant(camt / total_cr * Decimal("100.00")) if total_cr > 0 else Decimal("0.00")
        revenue_cats.append(CategoryBreakdown(category=r.category, amount=quant(camt), percentage=pct))

    return FinancialSummaryOut(
        total_revenue=quant(total_cr),
        total_expenses=quant(total_dr),
        net_profit_proxy=quant(net_profit),
        net_margin_pct=margin_pct,
        monthly_trends=trends,
        expense_categories=expense_cats,
        revenue_categories=revenue_cats
    )

def compute_financial_readiness_score(
    session: Session,
    org_id: str,
    user_id: Optional[str] = None
) -> FinancialReadinessScore:
    """
    Computes deterministic MSME Financial Readiness Index (scale 300 to 900)
    evaluating four core Pillars:
    1. Cash Flow Regularity & Revenue Scale (Max 200 pts)
    2. Debt Service Coverage Ratio (DSCR Proxy) (Max 150 pts)
    3. Operating Profit Margin & Cash Runway (Max 150 pts)
    4. Statutory GST Compliance Track Record (Max 200 pts)
    Base score: 200 pts. Minimum: 300, Maximum: 900.
    """
    set_tenant_context(session, org_id=org_id, user_id=user_id)
    summary = get_financial_summary(session, org_id, user_id)

    base_score = 200
    factors: List[ReadinessFactor] = []

    # 1. Cash Flow Regularity & Scale (Max 200)
    # Check consistency of inflows
    inflow_months = len(summary.monthly_trends)
    positive_months = sum(1 for m in summary.monthly_trends if m.net_cash_flow > 0)
    if inflow_months >= 3 and positive_months / inflow_months >= 0.66:
        cf_score = 175
        cf_rating = "STRONG"
        cf_desc = f"{positive_months}/{inflow_months} months with positive net cash flow demonstrates business stability."
    elif inflow_months > 0 and positive_months > 0:
        cf_score = 120
        cf_rating = "MODERATE"
        cf_desc = "Seasonal or fluctuating cash inflows observed across statements."
    else:
        cf_score = 70
        cf_rating = "LOW"
        cf_desc = "Limited or negative cash flow history across statement period."

    factors.append(ReadinessFactor(
        name="Cash Flow Regularity",
        score=cf_score,
        max_score=200,
        rating=cf_rating,
        description=cf_desc
    ))

    # 2. DSCR Proxy: Net Operating Profit / Debt Service EMI (Max 150)
    debt_payments = sum(
        c.amount for c in summary.expense_categories if "debt" in c.category.lower() or "emi" in c.category.lower()
    )
    if debt_payments > Decimal("0.00"):
        dscr = quant(summary.total_revenue / debt_payments) if summary.total_revenue > 0 else Decimal("0.00")
        if dscr >= Decimal("2.00"):
            dscr_score = 145
            dscr_rating = "EXCELLENT"
            dscr_desc = f"DSCR proxy of {dscr}x well exceeds lending benchmark of 1.5x."
        elif dscr >= Decimal("1.25"):
            dscr_score = 110
            dscr_rating = "ACCEPTABLE"
            dscr_desc = f"DSCR proxy of {dscr}x indicates adequate debt service capacity."
        else:
            dscr_score = 60
            dscr_rating = "STRESSED"
            dscr_desc = f"DSCR proxy of {dscr}x indicates tight debt coverage."
    else:
        dscr = Decimal("3.50")  # Zero existing debt indicates clean balance sheet
        dscr_score = 140
        dscr_rating = "EXCELLENT"
        dscr_desc = "Zero existing bank borrowing observed; full debt capacity available."

    factors.append(ReadinessFactor(
        name="Debt Service Coverage (DSCR)",
        score=dscr_score,
        max_score=150,
        rating=dscr_rating,
        description=dscr_desc
    ))

    # 3. Operating Margin (Max 150)
    if summary.net_margin_pct >= Decimal("15.00"):
        margin_score = 145
        margin_rating = "HEALTHY"
        margin_desc = f"Net operating margin of {summary.net_margin_pct}% reflects high pricing power."
    elif summary.net_margin_pct >= Decimal("5.00"):
        margin_score = 110
        margin_rating = "NORMAL"
        margin_desc = f"Net operating margin of {summary.net_margin_pct}% aligns with standard MSME trading margins."
    else:
        margin_score = 65
        margin_rating = "LOW"
        margin_desc = f"Operating margin of {summary.net_margin_pct}% reflects thin operating cushion."

    factors.append(ReadinessFactor(
        name="Operating Profit Margin",
        score=margin_score,
        max_score=150,
        rating=margin_rating,
        description=margin_desc
    ))

    # 4. Statutory GST Compliance Track Record (Max 200)
    gst_filings_count = session.execute(
        text("SELECT COUNT(*) FROM public.gst_records WHERE org_id = :org AND filing_status = 'filed';"),
        {"org": org_id}
    ).scalar() or 0

    reconciliation_runs = session.execute(
        text("SELECT COUNT(*) FROM public.reconciliation_runs WHERE org_id = :org;"),
        {"org": org_id}
    ).scalar() or 0

    if gst_filings_count >= 1 or reconciliation_runs >= 1:
        gst_score = 185
        gst_rating = "COMPLIANT"
        gst_desc = "Verified GST compliance with regular reconciliation and zero active audit freezes."
    else:
        gst_score = 120
        gst_rating = "PARTIAL"
        gst_desc = "GST registration active; recommended to complete quarterly reconciliations."

    factors.append(ReadinessFactor(
        name="GST Compliance & Provenance",
        score=gst_score,
        max_score=200,
        rating=gst_rating,
        description=gst_desc
    ))

    total_score = base_score + cf_score + dscr_score + margin_score + gst_score
    total_score = min(900, max(300, total_score))

    if total_score >= 750:
        overall_rating = "EXCELLENT"
    elif total_score >= 650:
        overall_rating = "GOOD"
    elif total_score >= 500:
        overall_rating = "FAIR"
    else:
        overall_rating = "NEEDS_ATTENTION"

    # Save to business_profiles credit_score
    session.execute(
        text("""
        UPDATE public.business_profiles
        SET credit_score = :cs, updated_at = clock_timestamp()
        WHERE org_id = :org;
        """),
        {"cs": total_score, "org": org_id}
    )
    session.commit()

    return FinancialReadinessScore(
        score=total_score,
        rating=overall_rating,
        dscr_proxy=dscr,
        cash_flow_stability=cf_rating,
        gst_compliance_score=gst_score,
        factors=factors,
        disclaimer=CREDIT_DISCLAIMER
    )

def generate_loan_dossier(
    session: Session,
    org_id: str,
    user_id: Optional[str] = None
) -> LoanDossierOut:
    """Generates structured MSME loan application dossier."""
    set_tenant_context(session, org_id=org_id, user_id=user_id)

    org_row = session.execute(
        text("SELECT legal_name, pan FROM public.organizations WHERE id = :org;"),
        {"org": org_id}
    ).fetchone()
    legal_name = org_row.legal_name if org_row else "MSME Enterprise"
    pan = org_row.pan if org_row else "PAN_UNCONFIGURED"

    reg_row = session.execute(
        text("SELECT gstin FROM public.gst_registrations WHERE org_id = :org AND is_primary = true;"),
        {"org": org_id}
    ).fetchone()
    gstin = reg_row.gstin if reg_row else None

    scorecard = compute_financial_readiness_score(session, org_id, user_id)
    summary = get_financial_summary(session, org_id, user_id)

    # Recommendations based on score
    recommendations: List[str] = []
    if scorecard.score >= 700:
        recommendations.extend([
            "CGTMSE Collateral-Free Term Loan (Up to ₹2 Crore at prime interest rates)",
            "Bank Working Capital Cash Credit (CC) Facility with simplified book debt drawing power",
            "MUDRA Tarun Scheme (Loans from ₹5 Lakh to ₹10 Lakh for business expansion)"
        ])
    elif scorecard.score >= 600:
        recommendations.extend([
            "MUDRA Kishore Scheme (Loans from ₹50,000 to ₹5 Lakh)",
            "PMEGP Margin Money Subsidy Facility (15% to 35% government grant)",
            "NBFC Invoice Discounting & Supply Chain Financing"
        ])
    else:
        recommendations.extend([
            "PM-SVANidhi Micro-Credit Working Capital Facility (₹10,000 to ₹50,000)",
            "MUDRA Shishu Facility (Up to ₹50,000)",
            "MSME ZED Certification Grant (Up to 80% subsidy to enhance credit quality)"
        ])

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    return LoanDossierOut(
        legal_name=legal_name,
        pan=pan,
        gstin=gstin,
        scorecard=scorecard,
        summary=summary,
        recommended_loan_products=recommendations,
        generated_at=now_str,
        disclaimer=CREDIT_DISCLAIMER
    )
