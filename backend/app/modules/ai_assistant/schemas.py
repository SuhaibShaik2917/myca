from typing import List, Optional, Dict, Any
from decimal import Decimal
from pydantic import BaseModel, Field

class ChatMessageIn(BaseModel):
    role: str = Field(..., description="'user' or 'assistant'")
    content: str = Field(..., description="Message text")

class ChatConversationIn(BaseModel):
    messages: List[ChatMessageIn]
    include_gst_grounding: bool = True

class GroundingContextSummary(BaseModel):
    org_legal_name: str
    gstin: Optional[str] = None
    latest_return_period: Optional[str] = None
    net_tax_liability: Optional[Decimal] = None
    eligible_itc: Optional[Decimal] = None
    pending_vendor_invoices: int = 0
    msme_readiness_score: Optional[int] = None
    top_recommended_scheme: Optional[str] = None
    statutory_citations: List[str] = []

class ChatResponseOut(BaseModel):
    reply: str
    grounding_applied: bool
    grounding_context: Optional[GroundingContextSummary] = None
    model_used: str
    timestamp: str

class QuickInsightOut(BaseModel):
    id: str
    category: str
    title: str
    summary: str
    statutory_reference: Optional[str] = None
    severity: str  # "info", "warning", "critical", "success"
    action_link: Optional[str] = None
