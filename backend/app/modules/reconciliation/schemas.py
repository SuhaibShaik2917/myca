from typing import List, Dict, Any, Optional
from decimal import Decimal
from pydantic import BaseModel, Field
from datetime import date, datetime
from enum import Enum


class IMSActionEnum(str, Enum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    PENDING = "PENDING"
    NO_ACTION = "NO_ACTION"
    DEEMED_ACCEPTED = "DEEMED_ACCEPTED"
    NOT_AVAILABLE_IN_IMS = "NOT_AVAILABLE_IN_IMS"


class MatchCategoryEnum(str, Enum):
    MATCHED = "MATCHED"
    MISSING_IN_GSTR2B = "MISSING_IN_GSTR2B"
    MISSING_IN_BOOKS = "MISSING_IN_BOOKS"
    TAX_MISMATCH = "TAX_MISMATCH"
    DATE_MISMATCH = "DATE_MISMATCH"
    COUNTERPARTY_MISMATCH = "COUNTERPARTY_MISMATCH"
    AMENDED_BY_SUPPLIER = "AMENDED_BY_SUPPLIER"
    DUPLICATE_DOCUMENT = "DUPLICATE_DOCUMENT"
    POSSIBLE_MATCH_REQUIRES_REVIEW = "POSSIBLE_MATCH_REQUIRES_REVIEW"
    IMPORT_OR_ISD_MISMATCH = "IMPORT_OR_ISD_MISMATCH"
    RCM_MISMATCH = "RCM_MISMATCH"


class ITCEligibilityEnum(str, Enum):
    ELIGIBLE_EVIDENCE_COMPLETE = "ELIGIBLE_EVIDENCE_COMPLETE"
    POTENTIALLY_ELIGIBLE = "POTENTIALLY_ELIGIBLE"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    BLOCKED_CREDIT_INDICATOR = "BLOCKED_CREDIT_INDICATOR"
    PENDING_GOODS_OR_SERVICE_RECEIPT = "PENDING_GOODS_OR_SERVICE_RECEIPT"
    PAYMENT_180_DAY_RISK = "PAYMENT_180_DAY_RISK"
    RULE_37A_RISK = "RULE_37A_RISK"
    TIME_LIMIT_RISK = "TIME_LIMIT_RISK"
    SUPPLIER_STATUS_UNKNOWN = "SUPPLIER_STATUS_UNKNOWN"
    REVERSED = "REVERSED"
    REAVAILMENT_REVIEW_REQUIRED = "REAVAILMENT_REVIEW_REQUIRED"


class GSTRegistrationCreate(BaseModel):
    gstin: str
    state_code: str
    trade_name: Optional[str] = None
    legal_name: str
    registration_type: str = "regular"
    is_primary: bool = False


class GSTRegistrationOut(BaseModel):
    id: str
    org_id: str
    gstin: str
    state_code: str
    trade_name: Optional[str] = None
    legal_name: str
    registration_type: str
    is_primary: bool
    created_at: str


# --- GSTR-2B Ingest Models ---

class GSTR2BDocumentItem(BaseModel):
    section_name: str  # B2B, B2BA, CDNR, CDNRA, ISD, ISDA, RCM, IMPG, IMPGA, IMPGSEZ, IMPGSEZA, ITC_NOT_AVAILABLE
    itc_availability: str  # available, not_available
    source_form: str  # GSTR-1, GSTR-1A, IFF, GSTR-5, GSTR-6, ICEGATE
    supplier_gstin: Optional[str] = None
    supplier_name: Optional[str] = None
    document_type: str  # INV, CRN, DBN, ISD_INV, BOE
    document_number: str
    document_date: str  # YYYY-MM-DD
    invoice_value: Decimal = Decimal("0.00")
    place_of_supply: str
    supply_attract_reverse_charge: bool = False
    taxable_value: Decimal = Decimal("0.00")
    integrated_tax: Decimal = Decimal("0.00")
    central_tax: Decimal = Decimal("0.00")
    state_ut_tax: Decimal = Decimal("0.00")
    cess_amount: Decimal = Decimal("0.00")
    applicable_percentage_rate: Optional[Decimal] = None
    source_gstr1_filing_date: Optional[str] = None
    source_gstr1_filing_period: Optional[str] = None
    boe_number: Optional[str] = None
    boe_date: Optional[str] = None
    port_code: Optional[str] = None
    is_amended: bool = False
    original_document_number: Optional[str] = None
    original_document_date: Optional[str] = None
    raw_payload: Optional[Dict[str, Any]] = None


class GSTR2BParsedStatement(BaseModel):
    recipient_gstin: str
    return_period: str  # YYYY-MM
    financial_year: str
    generation_date: Optional[str] = None
    total_itc_available: Decimal = Decimal("0.00")
    total_itc_not_available: Decimal = Decimal("0.00")
    documents: List[GSTR2BDocumentItem] = []
    parser_version: str = "v2.1"


# --- Purchase Register Models ---

class PurchaseLineItemIn(BaseModel):
    item_description: str
    hsn_sac_code: Optional[str] = None
    quantity: Optional[Decimal] = None
    unit_price: Optional[Decimal] = None
    taxable_amount: Optional[Decimal] = None
    gst_rate: Optional[Decimal] = None
    cgst_amount: Optional[Decimal] = None
    sgst_amount: Optional[Decimal] = None
    igst_amount: Optional[Decimal] = None
    cess_amount: Optional[Decimal] = Decimal("0.00")
    total_amount: Optional[Decimal] = None


class PurchaseDocumentCreate(BaseModel):
    registration_id: str
    document_type: str = "tax_invoice"  # tax_invoice, debit_note, credit_note, bill_of_entry, isd_document, rcm_self_invoice
    document_number: str
    document_date: str  # YYYY-MM-DD
    financial_period: str  # YYYY-MM
    supplier_gstin: Optional[str] = None
    supplier_legal_name: str
    recipient_gstin: str
    place_of_supply: str
    is_reverse_charge: bool = False
    supply_category: str = "B2B_REGULAR"
    total_taxable_amount: Optional[Decimal] = None
    cgst_amount: Optional[Decimal] = None
    sgst_amount: Optional[Decimal] = None
    igst_amount: Optional[Decimal] = None
    cess_amount: Optional[Decimal] = Decimal("0.00")
    total_invoice_value: Optional[Decimal] = None
    goods_services_type: Optional[str] = "goods"  # goods, services, both, capital_goods
    goods_receipt_date: Optional[str] = None
    goods_receipt_note_ref: Optional[str] = None
    purchase_ledger_code: Optional[str] = None
    is_availed_in_3b: bool = False
    availed_period: Optional[str] = None
    line_items: List[PurchaseLineItemIn] = []


# --- IMS Action Tracking ---

class IMSActionRequest(BaseModel):
    gstr2b_doc_id: str
    action: IMSActionEnum
    reason: Optional[str] = None


class IMSActionResponse(BaseModel):
    gstr2b_doc_id: str
    current_action: str
    previous_action: Optional[str] = None
    action_timestamp: str
    is_frozen_by_gstr3b: bool = False


# --- Reconciliation Configuration & Results ---

class ReconciliationRunConfig(BaseModel):
    registration_id: str
    return_period: str
    snapshot_id: str
    taxable_tolerance: Decimal = Decimal("1.00")
    tax_tolerance: Decimal = Decimal("1.00")
    total_tolerance: Decimal = Decimal("2.00")


class MatchDetailOut(BaseModel):
    purchase_doc_id: str
    gstr2b_doc_id: str
    match_pass: int
    match_score: Decimal
    is_exact: bool
    taxable_difference: Decimal
    tax_difference: Decimal
    total_difference: Decimal
    match_evidence: Dict[str, Any]


class VarianceDetailOut(BaseModel):
    variance_category: MatchCategoryEnum
    purchase_doc_id: Optional[str] = None
    gstr2b_doc_id: Optional[str] = None
    difference_details: Dict[str, Any]


class ReconciliationSummaryOut(BaseModel):
    run_id: str
    return_period: str
    matched_count: int
    variance_count: int
    missing_in_gstr2b_count: int
    missing_in_books_count: int
    matches: List[MatchDetailOut] = []
    variances: List[VarianceDetailOut] = []


# --- ITC Risk Engine Models ---

class ITCAssessmentOut(BaseModel):
    purchase_doc_id: str
    gstr2b_doc_id: Optional[str] = None
    rule_code: str
    statutory_section: str
    eligibility_status: ITCEligibilityEnum
    potential_itc: Decimal
    risk_reversal_amount: Decimal
    findings_explanation: str
    missing_evidence_required: Optional[str] = None
    evaluation_date: str


class Rule37PaymentCheck(BaseModel):
    purchase_doc_id: str
    invoice_date: str
    days_elapsed: int
    total_invoice_value: Decimal
    total_paid: Decimal
    unpaid_amount: Decimal
    is_180_days_exceeded: bool
    reversal_required_amount: Decimal
    interest_exposure_estimate: Decimal


class Rule37AFilingCheck(BaseModel):
    purchase_doc_id: str
    supplier_gstin: str
    invoice_date: str
    gstr1_reported_period: str
    supplier_gstr3b_status: str  # FILED, NOT_FILED, UNKNOWN
    supplier_filing_deadline: str  # 30th September of following FY
    recipient_reversal_deadline: str  # 30th November of following FY
    is_availed_in_3b: bool = False
    reversal_required: bool = False
    reversal_amount: Decimal = Decimal("0.00")
    interest_liability: Decimal = Decimal("0.00")  # 18% p.a. under Sec 50 if past 30 Nov
    reavailment_eligible: bool = False  # True if previously reversed and supplier subsequently filed
    risk_status: ITCEligibilityEnum


class ComponentTaxAmount(BaseModel):
    cgst: Decimal = Decimal("0.00")
    sgst: Decimal = Decimal("0.00")
    igst: Decimal = Decimal("0.00")
    cess: Decimal = Decimal("0.00")
    total: Decimal = Decimal("0.00")


# --- GSTR-3B Working Output ---

class GSTR3BTable4WorkingOutput(BaseModel):
    disclaimer: str = "Outward/Inward Reconciled GSTR-3B Table 4 Working Summary. Not official tax advice."
    return_period: str
    recipient_gstin: str
    # 4(A) ITC Available (whether in full or in part)
    itc_available_import_goods: Decimal = Decimal("0.00")              # 4(A)(1)
    itc_available_import_services: Decimal = Decimal("0.00")           # 4(A)(2)
    itc_available_inward_reverse_charge: Decimal = Decimal("0.00")     # 4(A)(3)
    itc_available_isd: Decimal = Decimal("0.00")                       # 4(A)(4)
    itc_available_all_other: Decimal = Decimal("0.00")                 # 4(A)(5) (B2B, includes amounts to be reversed under 4(B)(1))
    total_itc_available: Decimal = Decimal("0.00")                     # Sum of 4(A)(1)..(5)
    # 4(B) ITC Reversed
    itc_reversed_rule_38_42_43: Decimal = Decimal("0.00")              # 4(B)(1) Non-reclaimable: Rules 38, 42, 43 and Section 17(5)
    itc_reversed_rule_37_180_days: Decimal = Decimal("0.00")           # Part of 4(B)(2) Reclaimable
    itc_reversed_rule_37a: Decimal = Decimal("0.00")                   # Part of 4(B)(2) Reclaimable
    itc_reversed_other: Decimal = Decimal("0.00")                      # Part of 4(B)(2) Reclaimable
    total_itc_reversed_reclaimable_4b2: Decimal = Decimal("0.00")     # Total 4(B)(2)
    total_itc_reversed: Decimal = Decimal("0.00")                     # Total 4(B) = 4(B)(1) + 4(B)(2)
    # 4(C) Net ITC Available
    net_itc_available: Decimal = Decimal("0.00")                       # 4(C) = 4(A) - 4(B)
    # 4(D) Other Details (Reporting & Ineligible)
    itc_reclaimed_earlier_reversal_4d1: Decimal = Decimal("0.00")      # 4(D)(1) Reclaimed ITC previously reversed under 4(B)(2) (NEVER 17(5))
    ineligible_sec_16_4_and_pos_restrictions_4d2: Decimal = Decimal("0.00") # 4(D)(2) Ineligible from GSTR-2B Table 4 unavailable
    ineligible_sec_17_5_blocked: Decimal = Decimal("0.00")             # Section 17(5) blocked credit (included in 4(B)(1))
    ineligible_other: Decimal = Decimal("0.00")
    # Distinct Subtotals (Statutory Provenance Non-Confused Totals)
    subtotal_gstr2b_portal_available: Decimal = Decimal("0.00")
    subtotal_books_matched: Decimal = Decimal("0.00")
    current_b2b_inward_available: Decimal = Decimal("0.00")
    reclaims_total: Decimal = Decimal("0.00")
    subtotal_legally_eligible: Decimal = Decimal("0.00")
    subtotal_human_approved: Decimal = Decimal("0.00")
    # Component Breakdown for Tax Columns (CGST, SGST, IGST, Cess)
    components_4a: Optional[Dict[str, ComponentTaxAmount]] = None
    components_4b: Optional[Dict[str, ComponentTaxAmount]] = None
    components_4c_net: Optional[ComponentTaxAmount] = None
    components_4d: Optional[Dict[str, ComponentTaxAmount]] = None
    # Detailed Traceability & Variance Explanation
    unbooked_gstr2b_documents: List[Dict[str, Any]] = []
    unbooked_variance_amount: Decimal = Decimal("0.00")
    variance_explanation: Optional[str] = None
    reversals_lineage: List[Dict[str, Any]] = []
    interest_summary: Dict[str, Any] = {}


# --- ITC Ledger & Event Tracking Models ---

class ITCLedgerEventCreate(BaseModel):
    purchase_doc_id: str
    gstr2b_doc_id: Optional[str] = None
    event_type: str  # AVAIL, REVERSAL_RULE_37, REVERSAL_RULE_37A, REVERSAL_PERMANENT_17_5, REVERSAL_RULE_38, REVERSAL_RULE_42_43, REVERSAL_OTHER_TEMP, RECLAIM
    tax_period: str  # YYYY-MM
    cgst_amount: Decimal = Decimal("0.00")
    sgst_amount: Decimal = Decimal("0.00")
    igst_amount: Decimal = Decimal("0.00")
    cess_amount: Decimal = Decimal("0.00")
    reversal_event_id: Optional[str] = None
    reversal_reason: Optional[str] = None
    interest_amount: Decimal = Decimal("0.00")
    interest_basis: Optional[str] = None
    provenance: Optional[Dict[str, Any]] = None


class ITCLedgerEntryOut(BaseModel):
    id: str
    purchase_doc_id: str
    gstr2b_doc_id: Optional[str] = None
    event_type: str
    tax_period: str
    cgst_amount: Decimal
    sgst_amount: Decimal
    igst_amount: Decimal
    cess_amount: Decimal
    total_tax: Decimal
    reversal_event_id: Optional[str] = None
    interest_amount: Decimal
    interest_basis: Optional[str] = None
    provenance: Dict[str, Any]
    created_at: str


class ITCLedgerSummaryOut(BaseModel):
    total_availed: ComponentTaxAmount
    total_reversed_permanent: ComponentTaxAmount
    total_reversed_temporary: ComponentTaxAmount
    total_reavailed: ComponentTaxAmount
    net_active_itc_balance: ComponentTaxAmount
    total_interest_assessed: Decimal = Decimal("0.00")
    entries_count: int = 0


class IMSActionHistoryOut(BaseModel):
    id: str
    ims_action_id: str
    previous_action: Optional[str] = None
    new_action: str
    action_reason: Optional[str] = None
    actor_id: Optional[str] = None
    created_at: str


class GSTR3BFilingFreezeRequest(BaseModel):
    registration_id: str
    return_period: str
    filing_status: str = "FILED"  # FILED, DRAFT, SUBMITTED
    filing_arn: Optional[str] = None


# --- Recomputation Models ---

class GSTR2BRecomputeRequest(BaseModel):
    registration_id: str
    return_period: str
    snapshot_id: str


class GSTR2BRecomputeResponse(BaseModel):
    new_snapshot_id: str
    previous_snapshot_id: str
    version_number: int
    return_period: str
    recipient_gstin: str
    total_itc_available: Decimal
    total_itc_not_available: Decimal
    accepted_docs_count: int
    pending_docs_held_count: int
    rejected_docs_count: int
    recomputed_at: str


# --- Human Resolution & Payment Requests ---

class HumanResolutionRequest(BaseModel):
    variance_id: Optional[str] = None
    purchase_doc_id: Optional[str] = None
    gstr2b_doc_id: Optional[str] = None
    resolution_action: str  # FORCE_MATCH, FORCE_UNLINK, ACCEPT_BOOK_FIGURE, ACCEPT_PORTAL_FIGURE, MARK_BLOCKED_CREDIT, MARK_ELIGIBLE_WITH_REASON, DEFER_TO_NEXT_PERIOD, REQUEST_SUPPLIER_AMENDMENT
    resolution_notes: str
    supporting_document_id: Optional[str] = None


class HumanResolutionResponse(BaseModel):
    id: str
    resolution_action: str
    resolved_by: str
    created_at: str


class PurchasePaymentAllocationIn(BaseModel):
    purchase_doc_id: str
    allocated_taxable_amount: Decimal = Decimal("0.00")
    allocated_tax_amount: Decimal = Decimal("0.00")
    allocated_total_amount: Decimal = Decimal("0.00")


class PurchasePaymentRequest(BaseModel):
    payment_reference: str
    payment_date: str  # YYYY-MM-DD
    payment_mode: str  # NEFT, RTGS, UPI, CHEQUE, CASH
    bank_reference: Optional[str] = None
    total_amount_paid: Decimal
    allocations: List[PurchasePaymentAllocationIn] = []


class GSTR2BUploadResponse(BaseModel):
    import_id: str
    snapshot_id: str
    version_number: int
    recipient_gstin: str
    return_period: str
    financial_year: str
    total_documents: int
    total_itc_available: Decimal
    total_itc_not_available: Decimal
    file_hash: str


class ITCRiskEvaluateRequest(BaseModel):
    purchase_doc_id: str
    gstr2b_doc_id: Optional[str] = None
    evaluation_date: Optional[str] = None


class Rule37InterestCalculationRequest(BaseModel):
    purchase_doc_id: str
    reversal_amount: Decimal
    ecl_minimum_balance: Optional[Decimal] = None
    utilisation_date: Optional[str] = None  # YYYY-MM-DD
    reversal_date: Optional[str] = None     # YYYY-MM-DD
    interest_rate_pct: Decimal = Decimal("18.00")


class Rule37InterestCalculationResponse(BaseModel):
    purchase_doc_id: str
    reversal_amount: Decimal
    ecl_minimum_balance: Optional[Decimal] = None
    utilised_amount: Decimal = Decimal("0.00")
    utilisation_date: Optional[str] = None
    reversal_date: Optional[str] = None
    days_utilised: int = 0
    interest_rate_pct: Decimal = Decimal("18.00")
    interest_amount: Decimal = Decimal("0.00")
    is_utilised: bool = False
    statutory_basis: str
    explanation: str


