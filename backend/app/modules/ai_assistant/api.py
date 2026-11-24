from typing import Optional, List
from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.core.dependencies import get_tenant_db, validate_tenant_identity
from app.modules.ai_assistant.schemas import (
    ChatConversationIn, ChatResponseOut, QuickInsightOut
)
from app.modules.ai_assistant.grounding_service import (
    get_tenant_grounding_context, generate_quick_insights
)
from app.modules.ai_assistant.gemini_client import answer_ai_query

router = APIRouter(prefix="/ai", tags=["AI Advisor"])

@router.post("/chat", response_model=ChatResponseOut)
def chat_endpoint(
    conversation: ChatConversationIn,
    x_org_id: Optional[str] = Header(None, alias="X-Org-Id"),
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
    db: Session = Depends(get_tenant_db)
):
    """
    Multi-turn AI Assistant grounded in tenant's real-time GST and financial records.
    Provides statutory compliance guidance with full CGST Act / Rules grounding.
    """
    effective_org, effective_user = validate_tenant_identity(db, x_org_id, x_user_id)

    try:
        ctx = None
        if conversation.include_gst_grounding:
            ctx = get_tenant_grounding_context(db, effective_org, effective_user)

        return answer_ai_query(conversation.messages, ctx)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI inference failed: {str(e)}")

@router.get("/quick-insights", response_model=List[QuickInsightOut])
def quick_insights_endpoint(
    x_org_id: Optional[str] = Header(None, alias="X-Org-Id"),
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
    db: Session = Depends(get_tenant_db)
):
    """
    Returns proactive statutory compliance alerts and financial advisory insights.
    """
    effective_org, effective_user = validate_tenant_identity(db, x_org_id, x_user_id)

    try:
        return generate_quick_insights(db, effective_org, effective_user)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
