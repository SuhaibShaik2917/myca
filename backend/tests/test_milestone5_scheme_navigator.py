import os
import uuid
import random
from decimal import Decimal
import pytest
import psycopg
from fastapi.testclient import TestClient

from app.main import app
from app.database import SessionLocal, set_tenant_context
from app.modules.scheme_navigator.schemas import BusinessProfileIn
from app.modules.scheme_navigator.eligibility_engine import evaluate_single_rule, evaluate_scheme
from app.modules.scheme_navigator.service import (
    seed_schemes_catalog_if_needed, list_government_schemes,
    evaluate_and_persist_matches, get_checklist_for_scheme,
    get_or_create_business_profile, upsert_business_profile
)

from tests.test_db_config import DB_ADMIN

@pytest.fixture
def m5_tenant():
    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            org_id = str(uuid.uuid4())
            user_id = str(uuid.uuid4())
            letters = "".join(random.choices("ABCDEFGHIJKLMNOPQRSTUVWXYZ", k=5))
            digits = f"{random.randint(1000, 9999)}"
            last = random.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
            pan = f"{letters}{digits}{last}"
            gstin = f"27{pan}1ZT"
            reg_id = str(uuid.uuid4())
            profile_id = str(uuid.uuid4())

            cur.execute("INSERT INTO public.organizations (id, legal_name, pan) VALUES (%s, 'M5 Scheme Enterprise', %s);", (org_id, pan))
            cur.execute("INSERT INTO public.global_users (id, email, full_name) VALUES (%s, %s, 'Scheme Tester');", (user_id, f"m5_{uuid.uuid4().hex}@example.com"))
            cur.execute("INSERT INTO public.organization_memberships (org_id, user_id, role, status) VALUES (%s, %s, 'owner', 'active');", (org_id, user_id))
            cur.execute("""
                INSERT INTO public.gst_registrations (id, org_id, gstin, state_code, legal_name, is_primary)
                VALUES (%s, %s, %s, '27', 'M5 Legal Entity', true);
            """, (reg_id, org_id, gstin))
            udyam = f"UDYAM-MH-12-{random.randint(1000000, 9999999)}"
            cur.execute("""
                INSERT INTO public.business_profiles (id, org_id, business_type, industry_sector, turnover_bracket, annual_turnover, udyam_number, created_at, updated_at)
                VALUES (%s, %s, 'Proprietorship', 'Manufacturing', 'Micro (< 5 Cr)', 2500000.00, %s, clock_timestamp(), clock_timestamp());
            """, (profile_id, org_id, udyam))

    return {"org_id": org_id, "user_id": user_id, "reg_id": reg_id, "gstin": gstin, "pan": pan}

def test_rule_evaluator_operators():
    profile = {
        "annual_turnover": Decimal("2500000.00"),
        "industry_sector": "Manufacturing",
        "has_udyam": True,
        "social_category": "Women"
    }

    # Operator: <=
    passed, _ = evaluate_single_rule("annual_turnover", "<=", "50000000.00", profile)
    assert passed is True

    # Operator: IN
    passed, _ = evaluate_single_rule("industry_sector", "IN", "Manufacturing, Services", profile)
    assert passed is True

    # Operator: =
    passed, _ = evaluate_single_rule("has_udyam", "=", "true", profile)
    assert passed is True

    # Operator: CONTAINS
    passed, _ = evaluate_single_rule("social_category", "CONTAINS", "Women", profile)
    assert passed is True

    # Failing case
    passed, _ = evaluate_single_rule("annual_turnover", "<=", "1000000.00", profile)
    assert passed is False

def test_schemes_catalog_seeding_and_retrieval(m5_tenant):
    with SessionLocal() as session:
        seed_schemes_catalog_if_needed(session)
        schemes = list_government_schemes(session)
        assert len(schemes) >= 6

        codes = [s.scheme_code for s in schemes]
        assert "PMMY-MUDRA" in codes
        assert "CGTMSE" in codes
        assert "PMEGP" in codes
        assert "PM-SVANIDHI" in codes
        assert "STAND-UP-INDIA" in codes
        assert "MSME-ZED" in codes

        # Filter by category
        credit_schemes = list_government_schemes(session, category="credit_guarantee")
        assert len(credit_schemes) >= 3

def test_scheme_checklist():
    chk = get_checklist_for_scheme("PMMY-MUDRA")
    assert chk is not None
    assert "Udyam Registration" in chk.required_documents[0] or "Udyam" in chk.required_documents[0]
    assert "udyamimitra.in" in chk.portal_url

    cgtmse_chk = get_checklist_for_scheme("CGTMSE")
    assert cgtmse_chk is not None
    assert "cgtmse.in" in cgtmse_chk.portal_url

def test_msme_scheme_evaluation_and_matching(m5_tenant):
    org_id = m5_tenant["org_id"]
    user_id = m5_tenant["user_id"]

    with SessionLocal() as session:
        # Evaluate default seeded manufacturing profile with Udyam and turnover 25L
        matches = evaluate_and_persist_matches(session, org_id, user_id)
        assert len(matches) >= 6

        match_dict = {m.scheme_code: m for m in matches}
        # Micro manufacturing with Udyam is fully eligible for CGTMSE & PMMY & ZED
        assert match_dict["CGTMSE"].match_score == Decimal("100.00")
        assert match_dict["CGTMSE"].status == "recommended"
        assert match_dict["PMMY-MUDRA"].match_score == Decimal("100.00")
        assert match_dict["MSME-ZED"].match_score == Decimal("100.00")

def test_scheme_navigator_api_endpoints(m5_tenant):
    org_id = m5_tenant["org_id"]
    user_id = m5_tenant["user_id"]
    client = TestClient(app)

    # 1. GET /schemes
    res = client.get(
        "/api/v1/scheme-navigator/schemes",
        headers={"X-Org-Id": org_id, "X-User-Id": user_id}
    )
    assert res.status_code == 200
    schemes = res.json()
    assert len(schemes) >= 6

    # 2. GET /profile
    res_prof = client.get(
        "/api/v1/scheme-navigator/profile",
        headers={"X-Org-Id": org_id, "X-User-Id": user_id}
    )
    assert res_prof.status_code == 200
    assert res_prof.json()["industry_sector"] == "Manufacturing"

    # 3. POST /profile (update to Street Vendor / Retail)
    new_udyam = f"UDYAM-MH-12-{random.randint(1000000, 9999999)}"
    res_up = client.post(
        "/api/v1/scheme-navigator/profile",
        json={
            "business_type": "Proprietorship",
            "industry_sector": "Retail",
            "turnover_bracket": "Micro (< 5 Cr)",
            "annual_turnover": 800000.00,
            "udyam_number": new_udyam,
            "vintage_years": 1,
            "social_category": "General",
            "has_udyam": True
        },
        headers={"X-Org-Id": org_id, "X-User-Id": user_id}
    )
    assert res_up.status_code == 200
    assert res_up.json()["industry_sector"] == "Retail"

    # 4. POST /evaluate
    res_eval = client.post(
        "/api/v1/scheme-navigator/evaluate",
        headers={"X-Org-Id": org_id, "X-User-Id": user_id}
    )
    assert res_eval.status_code == 200
    matches = res_eval.json()
    assert len(matches) >= 6
    svanidhi = next(m for m in matches if m["scheme_code"] == "PM-SVANIDHI")
    assert float(svanidhi["match_score"]) == 100.0
    assert svanidhi["status"] == "recommended"

    # 5. GET /checklist/PM-SVANIDHI
    res_chk = client.get("/api/v1/scheme-navigator/checklist/PM-SVANIDHI")
    assert res_chk.status_code == 200
    assert "pmsvanidhi.mohua.gov.in" in res_chk.json()["portal_url"]
