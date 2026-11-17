from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Literal, Dict, Any
from datetime import date, datetime
import uuid
from decimal import Decimal

class LineItemBase(BaseModel):
    item_description: str = Field(..., min_length=1, max_length=500)
    hsn_sac_code: Optional[str] = Field(None, max_length=16)
    quantity: Optional[Decimal] = None
    unit_price: Optional[Decimal] = None
    taxable_amount: Optional[Decimal] = None
    gst_rate: Optional[Decimal] = None
    cgst_amount: Optional[Decimal] = None
    sgst_amount: Optional[Decimal] = None
    igst_amount: Optional[Decimal] = None
    total_amount: Optional[Decimal] = None
    extraction_confidence: Optional[Decimal] = None

class LineItemCreate(LineItemBase):
    pass

class LineItemUpdate(BaseModel):
    item_description: Optional[str] = None
    hsn_sac_code: Optional[str] = None
    quantity: Optional[Decimal] = None
    unit_price: Optional[Decimal] = None
    taxable_amount: Optional[Decimal] = None
    gst_rate: Optional[Decimal] = None
    cgst_amount: Optional[Decimal] = None
    sgst_amount: Optional[Decimal] = None
    igst_amount: Optional[Decimal] = None
    total_amount: Optional[Decimal] = None

class LineItemOut(LineItemBase):
    id: uuid.UUID
    invoice_id: uuid.UUID
    org_id: uuid.UUID
    created_at: datetime

    class Config:
        from_attributes = True

class InvoiceUploadResponse(BaseModel):
    invoice_id: uuid.UUID
    document_id: uuid.UUID
    task_id: uuid.UUID
    status: str
    file_name: str
    sha256_checksum: str
    message: str

class InvoicePatch(BaseModel):
    invoice_number: Optional[str] = None
    invoice_date: Optional[date] = None
    due_date: Optional[date] = None
    counterparty_name: Optional[str] = None
    counterparty_gstin: Optional[str] = None
    total_taxable_amount: Optional[Decimal] = None
    cgst_amount: Optional[Decimal] = None
    sgst_amount: Optional[Decimal] = None
    igst_amount: Optional[Decimal] = None
    total_amount: Optional[Decimal] = None

class InvoiceApprovalRequest(BaseModel):
    notes: Optional[str] = None

class InvoiceRejectionRequest(BaseModel):
    reason: str = Field(..., min_length=3, max_length=1000)

class ValidationFlag(BaseModel):
    code: str
    severity: Literal["error", "warning", "info"]
    field: Optional[str] = None
    message: str

class ValidationReport(BaseModel):
    is_valid: bool
    has_critical_errors: bool
    arithmetic_valid: bool
    gstin_valid: bool
    hsn_valid: bool
    itc_eligible: bool
    flags: List[ValidationFlag] = []
    discrepancy_count: int = 0

class InvoiceOut(BaseModel):
    id: uuid.UUID
    org_id: uuid.UUID
    profile_id: uuid.UUID
    invoice_number: str
    invoice_date: date
    due_date: Optional[date] = None
    counterparty_name: str
    counterparty_gstin: Optional[str] = None
    total_taxable_amount: Decimal
    cgst_amount: Decimal
    sgst_amount: Decimal
    igst_amount: Decimal
    total_amount: Decimal
    currency: str
    status: str
    rejection_reason: Optional[str] = None
    document_id: Optional[uuid.UUID] = None
    approved_by: Optional[uuid.UUID] = None
    approved_at: Optional[datetime] = None
    export_ready_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    line_items: List[LineItemOut] = []
    validation_report: Optional[ValidationReport] = None

    class Config:
        from_attributes = True

# GST Working Output Schemas
GSTR_WORKING_DRAFT_DISCLAIMER = (
    "WORKING DRAFT ONLY — NOT AN OFFICIAL FILING. "
    "This working draft is generated for internal reconciliation and review purposes only. "
    "It must be reviewed and verified by an authorized person and filed through the official GST Portal (gst.gov.in) "
    "or an authorized GST Suvidha Provider (GSP)."
)

GSTR3B_WORKING_SUMMARY_DISCLAIMER = (
    "OUTWARD-SUPPLY-DERIVED WORKING SUMMARY ONLY — NOT A COMPLETE GSTR-3B RETURN. "
    "This summary reflects outward supplies extracted from approved sales invoices. "
    "It DOES NOT calculate authoritative Input Tax Credit (ITC), reverse-charge liabilities, "
    "tax-ledger offsets, or net cash payable, which require reconciled purchase ledgers, "
    "GSTR-2B data, and electronic cash/credit ledger balances."
)

class GSTR1Table4Entry(BaseModel):
    recipient_gstin: Optional[str] = None
    recipient_name: str
    invoice_number: str
    invoice_date: str
    invoice_value: Decimal
    place_of_supply: str
    reverse_charge: str = "N"
    invoice_type: str = "Regular"
    rate: Decimal
    taxable_value: Decimal
    cgst_amount: Decimal
    sgst_amount: Decimal
    igst_amount: Decimal

class GSTR1Table12Entry(BaseModel):
    hsn_sac_code: str
    description: str
    uqc: str = "OTH"
    total_quantity: Decimal
    total_value: Decimal
    taxable_value: Decimal
    gst_rate: Decimal
    cgst_amount: Decimal
    sgst_amount: Decimal
    igst_amount: Decimal
    cess_amount: Decimal = Decimal("0.00")

class GSTR1DraftResponse(BaseModel):
    disclaimer: str = GSTR_WORKING_DRAFT_DISCLAIMER
    tax_period: str
    gstin: str
    legal_name: str
    preceding_fy_aato: Optional[Decimal] = None
    aato_unverified: bool = False
    applicable_b2c_large_threshold: Decimal
    table_4_b2b: List[GSTR1Table4Entry] = []
    table_5_b2c_large: List[Dict[str, Any]] = []
    table_7_b2c_small: List[Dict[str, Any]] = []
    table_12_hsn_b2b: List[GSTR1Table12Entry] = []
    table_12_hsn_b2c: List[GSTR1Table12Entry] = []
    table_12_hsn_summary: List[GSTR1Table12Entry] = []  # Aggregate compatibility
    table_12_b2b_mandatory: bool = False
    table_12_b2c_mandatory: bool = False
    table_12_warnings: List[str] = []
    table_13_doc_summary: Dict[str, Any] = {}
    total_taxable_value: Decimal
    total_tax_liability: Decimal

class GSTR3BDraftResponse(BaseModel):
    disclaimer: str = GSTR3B_WORKING_SUMMARY_DISCLAIMER
    document_type: str = "Outward-Supply-Derived GSTR-3B Working Summary"
    tax_period: str
    gstin: str
    legal_name: str
    field_provenance: Dict[str, str] = {
        "table_3_1_a_outward_taxable": "calculated_from_approved_sales_invoices",
        "table_3_1_d_inward_reverse_charge": "missing_requires_purchase_register",
        "table_3_1_1_ecommerce": "missing_not_in_scope",
        "table_4_a_itc_available": "external_requires_gstr2b_reconciliation",
        "table_4_b_itc_reversed": "external_requires_rules_42_43_calculation",
        "table_6_1_payment_of_tax": "unsupported_requires_gst_portal_cash_ledger"
    }
    authoritative_itc_calculated: bool = False
    authoritative_net_cash_calculated: bool = False
    table_3_1_outward_supplies: Dict[str, Any] = {}
    table_4_eligible_itc: Dict[str, Any] = {}
    tax_payable_summary: Dict[str, Any] = {}
