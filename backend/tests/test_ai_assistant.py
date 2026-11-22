import os
import uuid
import random
import string
from decimal import Decimal
import pytest
import psycopg
from fastapi.testclient import TestClient

from app.main import app
from app.database import SessionLocal, set_tenant_context
from app.modules.ai_assistant.schemas import ChatMessageIn, ChatConversationIn
from app.modules.ai_assistant.grounding_service import (
    get_tenant_grounding_context, generate_quick_insights
)
from app.modules.ai_assistant.gemini_client import answer_ai_query

DB_ADMIN = os.environ.get("TEST_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/myca")

@pytest.fixture
def ai_tenant():
    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            org_id = str(uuid.uuid4())
            user_id = str(uuid.uuid4())
            rand_prefix = "".join(random.choices(string.ascii_uppercase, k=5))
            rand_digits = f"{random.randint(1000, 9999)}"
            pan = f"{rand_prefix}{rand_digits}Z"
            gstin = f"27{pan}1ZT"
            reg_id = str(uuid.uuid4())
            profile_id = str(uuid.uuid4())
            udyam = f"UDYAM-MH-12-{random.randint(1000000, 9999999)}"

            cur.execute("INSERT INTO public.organizations (id, legal_name, pan) VALUES (%s, 'Copilot MSME Private Ltd', %s);", (org_id, pan))
            cur.execute("INSERT INTO public.global_users (id, email, full_name) VALUES (%s, %s, 'AI Tester');", (user_id, f"ai_{uuid.uuid4().hex}@example.com"))
            cur.execute("INSERT INTO public.organization_memberships (org_id, user_id, role, status) VALUES (%s, %s, 'owner', 'active');", (org_id, user_id))
            cur.execute("""
                INSERT INTO public.gst_registrations (id, org_id, gstin, state_code, legal_name, is_primary)
                VALUES (%s, %s, %s, '27', 'Copilot MSME Legal Entity', true);
            """, (reg_id, org_id, gstin))
            cur.execute("""
                INSERT INTO public.business_profiles (id, org_id, business_type, industry_sector, turnover_bracket, annual_turnover, udyam_number, credit_score, created_at, updated_at)
                VALUES (%s, %s, 'Private Limited', 'Manufacturing', 'Micro (< 5 Cr)', 15000000.00, %s, 720, clock_timestamp(), clock_timestamp());
            """, (profile_id, org_id, udyam))

    return {"org_id": org_id, "user_id": user_id, "reg_id": reg_id, "gstin": gstin, "pan": pan}

def test_grounding_context_and_quick_insights(ai_tenant):
    org_id = ai_tenant["org_id"]
    user_id = ai_tenant["user_id"]

    with SessionLocal() as session:
        ctx = get_tenant_grounding_context(session, org_id, user_id)
        assert ctx.org_legal_name == "Copilot MSME Private Ltd"
        assert ctx.gstin == ai_tenant["gstin"]
        assert ctx.msme_readiness_score == 720
        assert len(ctx.statutory_citations) >= 4

        insights = generate_quick_insights(session, org_id, user_id)
        assert len(insights) >= 3
        titles = [i.title for i in insights]
        assert any("Rule 88A" in t for t in titles)
        assert any("MSME Readiness Index" in t for t in titles)

def test_ai_statutory_rule_distinction(ai_tenant):
    org_id = ai_tenant["org_id"]
    user_id = ai_tenant["user_id"]

    with SessionLocal() as session:
        ctx = get_tenant_grounding_context(session, org_id, user_id)

        # 1. Rule 37 Test (180 days, Sec 16(2) proviso, 18% interest)
        res_37 = answer_ai_query([ChatMessageIn(role="user", content="Explain Rule 37 and 180 day payment rule")], ctx)
        assert "180 days" in res_37.reply
        assert "Section 50" in res_37.reply or "18%" in res_37.reply
        assert res_37.grounding_applied is True

        # 2. Rule 37A Test (Supplier non-filing, 30th Sept / 30th Nov)
        res_37a = answer_ai_query([ChatMessageIn(role="user", content="What is Rule 37A reversal deadline?")], ctx)
        assert "Rule 37A" in res_37a.reply
        assert "30th September" in res_37a.reply or "30th Sept" in res_37a.reply
        assert "30th November" in res_37a.reply or "30th Nov" in res_37a.reply

        # 3. Rule 88A Test (Offset ordering, IGST exhaustion)
        res_88a = answer_ai_query([ChatMessageIn(role="user", content="How does Rule 88A credit offset work for GSTR-3B?")], ctx)
        assert "Rule 88A" in res_88a.reply
        assert "IGST" in res_88a.reply
        assert "Cannot offset SGST" in res_88a.reply or "CGST" in res_88a.reply

def test_ai_assistant_api_endpoints(ai_tenant):
    org_id = ai_tenant["org_id"]
    user_id = ai_tenant["user_id"]
    client = TestClient(app)

    # 1. GET /api/v1/ai/quick-insights
    res_insights = client.get(
        "/api/v1/ai/quick-insights",
        headers={"X-Org-Id": org_id, "X-User-Id": user_id}
    )
    assert res_insights.status_code == 200
    insights = res_insights.json()
    assert len(insights) >= 3

    # 2. POST /api/v1/ai/chat
    chat_payload = {
        "messages": [
            {"role": "user", "content": "What is our current financial readiness score and what schemes can we get?"}
        ],
        "include_gst_grounding": True
    }
    res_chat = client.post(
        "/api/v1/ai/chat",
        json=chat_payload,
        headers={"X-Org-Id": org_id, "X-User-Id": user_id}
    )
    assert res_chat.status_code == 200
    data = res_chat.json()
    assert "720" in data["reply"] or "Readiness" in data["reply"]
    assert data["grounding_applied"] is True
    assert data["grounding_context"]["org_legal_name"] == "Copilot MSME Private Ltd"
