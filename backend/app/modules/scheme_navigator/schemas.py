from typing import Optional, List, Dict, Any
from decimal import Decimal
from pydantic import BaseModel, Field
from enum import Enum

class SchemeCategory(str, Enum):
    SUBSIDY = "subsidy"
    CREDIT_GUARANTEE = "credit_guarantee"
    TECHNOLOGY_UPGRADATION = "technology_upgradation"
    EXPORT_PROMOTION = "export_promotion"
    SKILL_DEVELOPMENT = "skill_development"

class SchemeRuleOut(BaseModel):
    rule_key: str
    operator: str
    target_value: str
    rule_description: str

class GovernmentSchemeOut(BaseModel):
    id: str
    scheme_code: str
    name: str
    ministry: str
    category: SchemeCategory
    description: str
    benefits_summary: str
    max_subsidy_amount: Optional[Decimal] = None
    is_active: bool = True
    rules: List[SchemeRuleOut] = []

class BusinessProfileIn(BaseModel):
    business_type: str = Field(..., description="e.g. Proprietorship, Partnership, Private Limited, LLP")
    industry_sector: str = Field(..., description="e.g. Manufacturing, Services, Trading, Retail")
    turnover_bracket: Optional[str] = Field("Micro (< 5 Cr)", description="Micro (< 5 Cr), Small (5-50 Cr), Medium (50-250 Cr)")
    annual_turnover: Decimal = Field(default=Decimal("0.00"), description="Annual turnover in INR")
    udyam_number: Optional[str] = Field(None, description="e.g. UDYAM-MH-12-0012345")
    vintage_years: Optional[int] = Field(3, description="Operational years in business")
    social_category: Optional[str] = Field("General", description="General, SC, ST, OBC, Women, Minority")
    has_udyam: Optional[bool] = Field(True, description="Whether enterprise has valid Udyam registration")

class BusinessProfileOut(BaseModel):
    id: str
    org_id: str
    business_type: str
    industry_sector: str
    turnover_bracket: Optional[str] = None
    annual_turnover: Optional[Decimal] = None
    credit_score: Optional[int] = None
    udyam_number: Optional[str] = None
    gstin: Optional[str] = None

class EvaluatedCriterion(BaseModel):
    rule_key: str
    description: str
    passed: bool
    expected: str
    actual: str

class SchemeMatchOut(BaseModel):
    id: Optional[str] = None
    org_id: str
    scheme_id: str
    scheme_code: str
    scheme_name: str
    ministry: str
    category: SchemeCategory
    match_score: Decimal
    status: str
    evaluated_criteria: List[EvaluatedCriterion]
    benefits_summary: str
    max_subsidy_amount: Optional[Decimal] = None

class SchemeChecklistOut(BaseModel):
    scheme_code: str
    name: str
    ministry: str
    category: SchemeCategory
    eligibility_summary: str
    required_documents: List[str]
    application_steps: List[str]
    portal_url: str
    official_guidelines_url: str
