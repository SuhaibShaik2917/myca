from decimal import Decimal
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field

class TaxBreakdown(BaseModel):
    taxable_value: Decimal = Decimal("0.00")
    igst: Decimal = Decimal("0.00")
    cgst: Decimal = Decimal("0.00")
    sgst: Decimal = Decimal("0.00")
    cess: Decimal = Decimal("0.00")
    total: Decimal = Decimal("0.00")

class Table31OutwardSupplies(BaseModel):
    a_taxable_supplies: TaxBreakdown = Field(default_factory=TaxBreakdown)
    b_zero_rated: TaxBreakdown = Field(default_factory=TaxBreakdown)
    c_other_nil_exempt: Decimal = Decimal("0.00")
    d_inward_rcm: TaxBreakdown = Field(default_factory=TaxBreakdown)
    e_non_gst: Decimal = Decimal("0.00")
    total_tax_payable: TaxBreakdown = Field(default_factory=TaxBreakdown)

class Table4ITC(BaseModel):
    a_itc_available: TaxBreakdown = Field(default_factory=TaxBreakdown)
    b_itc_reversed: TaxBreakdown = Field(default_factory=TaxBreakdown)
    c_net_itc: TaxBreakdown = Field(default_factory=TaxBreakdown)
    d_ineligible_other: TaxBreakdown = Field(default_factory=TaxBreakdown)

class OffsetHeadDetail(BaseModel):
    liability_before: Decimal = Decimal("0.00")
    paid_by_igst_credit: Decimal = Decimal("0.00")
    paid_by_cgst_credit: Decimal = Decimal("0.00")
    paid_by_sgst_credit: Decimal = Decimal("0.00")
    paid_by_cess_credit: Decimal = Decimal("0.00")
    total_credit_used: Decimal = Decimal("0.00")
    remaining_liability: Decimal = Decimal("0.00")
    paid_by_cash: Decimal = Decimal("0.00")
    challan_required: Decimal = Decimal("0.00")

class OffsetResult(BaseModel):
    igst: OffsetHeadDetail
    cgst: OffsetHeadDetail
    sgst: OffsetHeadDetail
    cess: OffsetHeadDetail
    total_tax_paid_by_credit: Decimal
    total_tax_paid_by_cash: Decimal
    total_challan_required: Decimal
    remaining_credit_balance: TaxBreakdown
    statutory_ordering_audit: List[str]

class GSTR3BPrepareRequest(BaseModel):
    registration_id: str
    tax_period: str
    snapshot_id: Optional[str] = None

class MakerCheckerSubmitRequest(BaseModel):
    remarks: Optional[str] = "Submitted for CA / Checker statutory review."

class MakerCheckerDecisionRequest(BaseModel):
    decision: str = Field(..., pattern="^(APPROVED|REJECTED)$")
    remarks: str = Field(..., min_length=3)

class GSTR3BFilingResponse(BaseModel):
    filing_id: str
    tax_period: str
    status: str
    arn: str
    cin: Optional[str]
    filing_date: str
    payload_hash: str
    table_3_1: Table31OutwardSupplies
    table_4: Table4ITC
    offset: OffsetResult
    maker_remarks: Optional[str]
    checker_remarks: Optional[str]
    is_live_portal_filing: bool = False
    disclaimer: str

class GSTR3BStatusResponse(BaseModel):
    id: str
    registration_id: str
    tax_period: str
    filing_status: str
    table_3_1: Table31OutwardSupplies
    table_4: Table4ITC
    offset: OffsetResult
    maker_id: Optional[str]
    checker_id: Optional[str]
    maker_remarks: Optional[str]
    checker_remarks: Optional[str]
    arn: Optional[str]
    cin: Optional[str]
    filing_date: Optional[str]
    payload_hash: Optional[str]
    created_at: str
    updated_at: str
    revision_number: int = 1
    previous_snapshot: Optional[Dict[str, Any]] = None
    maker_provenance: str = "ESTABLISHED_AT_CREATION"
    legacy_maker_claim: Optional[Dict[str, Any]] = None
