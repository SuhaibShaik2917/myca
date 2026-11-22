from decimal import Decimal
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field

class TransactionIn(BaseModel):
    transaction_date: str  # YYYY-MM-DD
    amount: Decimal = Field(..., gt=0)
    transaction_type: str = Field(..., pattern="^(credit|debit)$")
    category: Optional[str] = None
    counterparty: Optional[str] = None
    reference_number: Optional[str] = None

class TransactionOut(BaseModel):
    id: str
    transaction_date: str
    amount: Decimal
    transaction_type: str
    category: str
    counterparty: Optional[str]
    reference_number: Optional[str]
    created_at: str

class StatementUploadResponse(BaseModel):
    transactions_imported: int
    total_credits: Decimal
    total_debits: Decimal
    net_flow: Decimal
    period_start: Optional[str]
    period_end: Optional[str]
    message: str

class MonthlyCashFlow(BaseModel):
    month: str  # YYYY-MM
    inflow: Decimal
    outflow: Decimal
    net_cash_flow: Decimal
    transaction_count: int

class CategoryBreakdown(BaseModel):
    category: str
    amount: Decimal
    percentage: Decimal

class FinancialSummaryOut(BaseModel):
    total_revenue: Decimal
    total_expenses: Decimal
    net_profit_proxy: Decimal
    net_margin_pct: Decimal
    monthly_trends: List[MonthlyCashFlow]
    expense_categories: List[CategoryBreakdown]
    revenue_categories: List[CategoryBreakdown]

class ReadinessFactor(BaseModel):
    name: str
    score: int
    max_score: int
    rating: str
    description: str

class FinancialReadinessScore(BaseModel):
    score: int = Field(..., ge=300, le=900)
    rating: str  # EXCELLENT, GOOD, FAIR, NEEDS_ATTENTION
    dscr_proxy: Decimal
    cash_flow_stability: str
    gst_compliance_score: int
    factors: List[ReadinessFactor]
    disclaimer: str

class LoanDossierOut(BaseModel):
    legal_name: str
    pan: str
    gstin: Optional[str]
    scorecard: FinancialReadinessScore
    summary: FinancialSummaryOut
    recommended_loan_products: List[str]
    generated_at: str
    disclaimer: str
