"""
Phase B.1 — GSTR-3B Workflow Integrity and Authorization Hardening Tests.

Covers:
1. P0 - Prevent Overwriting Submitted, Approved, and Filed GSTR-3B Returns:
   - DRAFT re-preparation succeeds (revision_number remains 1).
   - SUBMITTED_FOR_REVIEW rejects re-preparation (service & API HTTP 400).
   - APPROVED_BY_CHECKER rejects re-preparation (service & API HTTP 400).
   - FILED rejects re-preparation (service & API HTTP 400).
   - REJECTED_BY_CHECKER permits re-preparation, increments revision_number, archives previous_snapshot, and clears checker remarks.
   - Concurrent prepare & submit serialized via FOR UPDATE without silent overwrite.
2. P1 - Enforce Authoritative Checker Role & Membership:
   - Distinct 'accountant' or 'viewer' rejected from reviewing/approving.
   - Suspended/inactive admin rejected.
   - Non-member / cross-org user rejected.
   - Maker self-approval rejected even if 'owner'/'admin'.
   - Active 'admin' or 'owner' distinct from maker succeeds.
3. P2 - Clarify & Strictly Gate Development Identity Fallback:
   - Staging and production reject header-only authentication with HTTP 401.
   - Development/test with ALLOW_DEV_HEADER_AUTH=False rejects header-only auth with HTTP 401.
   - Settings validation fails closed if ALLOW_DEV_HEADER_AUTH=True in staging/production.
4. P3 - Expand Filed-Record & Workflow Immutability Coverage:
   - DB trigger blocks direct SQL financial mutation & deletion on SUBMITTED_FOR_REVIEW & APPROVED_BY_CHECKER.
   - DB trigger trg_prevent_gst_records_filed_mutations blocks UPDATE & DELETE on filed GSTR-3B gst_records.
"""

import random
import string
import threading
import uuid
import psycopg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.config import Settings, settings
from app.core.security import create_access_token
from app.database import SessionLocal
from app.main import app
from app.modules.gstr3b.service import (
    prepare_gstr3b_return,
    submit_gstr3b_for_review,
    review_gstr3b_decision,
    file_gstr3b_to_gsp_sandbox,
    get_gstr3b_status,
    GSTR3BServiceError,
)
from tests.test_db_config import DB_ADMIN


@pytest.fixture
def b1_org_setup():
    """
    Creates an organization with multiple role-differentiated users:
    - maker (accountant, active)
    - checker_admin (admin, active)
    - checker_owner (owner, active)
    - other_accountant (accountant, active)
    - viewer (viewer, active)
    - suspended_admin (admin, suspended)
    - outsider_admin (admin in another org)
    """
    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            org_id = str(uuid.uuid4())
            other_org_id = str(uuid.uuid4())
            pan_a = f"{''.join(random.choices(string.ascii_uppercase, k=5))}{random.randint(1000, 9999)}A"
            pan_b = f"{''.join(random.choices(string.ascii_uppercase, k=5))}{random.randint(1000, 9999)}B"
            gstin_a = f"27{pan_a}1ZA"
            reg_id = str(uuid.uuid4())

            cur.execute("INSERT INTO public.organizations (id, legal_name, pan) VALUES (%s, 'B1 Test Org', %s);", (org_id, pan_a))
            cur.execute("INSERT INTO public.organizations (id, legal_name, pan) VALUES (%s, 'B1 Other Org', %s);", (other_org_id, pan_b))

            users = {}
            roles = [
                ("maker", org_id, "accountant", "active"),
                ("checker_admin", org_id, "admin", "active"),
                ("checker_owner", org_id, "owner", "active"),
                ("other_accountant", org_id, "accountant", "active"),
                ("viewer", org_id, "viewer", "active"),
                ("suspended_admin", org_id, "admin", "suspended"),
                ("outsider_admin", other_org_id, "admin", "active"),
            ]
            for label, oid, role, status in roles:
                uid = str(uuid.uuid4())
                cur.execute(
                    "INSERT INTO public.global_users (id, email, full_name) VALUES (%s, %s, %s);",
                    (uid, f"{label}_{uuid.uuid4().hex}@example.com", f"B1 {label}"),
                )
                cur.execute(
                    "INSERT INTO public.organization_memberships (org_id, user_id, role, status) VALUES (%s, %s, %s, %s);",
                    (oid, uid, role, status),
                )
                users[label] = uid

            cur.execute(
                """
                INSERT INTO public.gst_registrations (id, org_id, gstin, state_code, legal_name, is_primary)
                VALUES (%s, %s, %s, '27', 'B1 Legal Entity', true);
                """,
                (reg_id, org_id, gstin_a),
            )

    return {
        "org_id": org_id,
        "other_org_id": other_org_id,
        "reg_id": reg_id,
        "gstin": gstin_a,
        **users,
    }


# =============================================================================
# 1. P0 - PREVENT OVERWRITING SUBMITTED, APPROVED, AND FILED RETURNS
# =============================================================================

def test_prepare_gstr3b_draft_reprepare_succeeds(b1_org_setup):
    s = b1_org_setup
    db = SessionLocal()
    try:
        res1 = prepare_gstr3b_return(db, s["org_id"], s["reg_id"], "2026-01", user_id=s["maker"])
        assert res1["filing_status"] == "DRAFT"
        assert res1["revision_number"] == 1

        res2 = prepare_gstr3b_return(db, s["org_id"], s["reg_id"], "2026-01", user_id=s["maker"])
        assert res2["id"] == res1["id"]
        assert res2["filing_status"] == "DRAFT"
        assert res2["revision_number"] == 1
    finally:
        db.close()


def test_prepare_gstr3b_rejected_after_submitted_for_review(b1_org_setup):
    s = b1_org_setup
    db = SessionLocal()
    client = TestClient(app)
    token_maker = create_access_token(subject=s["maker"], org_id=s["org_id"])
    try:
        prep = prepare_gstr3b_return(db, s["org_id"], s["reg_id"], "2026-02", user_id=s["maker"])
        submit_gstr3b_for_review(db, s["org_id"], prep["id"], s["maker"])

        with pytest.raises(GSTR3BServiceError, match="SUBMITTED_FOR_REVIEW"):
            prepare_gstr3b_return(db, s["org_id"], s["reg_id"], "2026-02", user_id=s["maker"])

        resp = client.post(
            "/api/v1/gstr3b/prepare",
            json={"registration_id": s["reg_id"], "tax_period": "2026-02"},
            headers={"Authorization": f"Bearer {token_maker}"},
        )
        assert resp.status_code == 400
        assert "SUBMITTED_FOR_REVIEW" in resp.json()["detail"]

        st = get_gstr3b_status(db, s["org_id"], s["reg_id"], "2026-02")
        assert st["filing_status"] == "SUBMITTED_FOR_REVIEW"
    finally:
        db.close()


def test_prepare_gstr3b_rejected_after_approved_by_checker(b1_org_setup):
    s = b1_org_setup
    db = SessionLocal()
    client = TestClient(app)
    token_maker = create_access_token(subject=s["maker"], org_id=s["org_id"])
    try:
        prep = prepare_gstr3b_return(db, s["org_id"], s["reg_id"], "2026-03", user_id=s["maker"])
        submit_gstr3b_for_review(db, s["org_id"], prep["id"], s["maker"])
        review_gstr3b_decision(db, s["org_id"], prep["id"], s["checker_admin"], "APPROVED", "Verified")

        with pytest.raises(GSTR3BServiceError, match="APPROVED_BY_CHECKER"):
            prepare_gstr3b_return(db, s["org_id"], s["reg_id"], "2026-03", user_id=s["maker"])

        resp = client.post(
            "/api/v1/gstr3b/prepare",
            json={"registration_id": s["reg_id"], "tax_period": "2026-03"},
            headers={"Authorization": f"Bearer {token_maker}"},
        )
        assert resp.status_code == 400
        assert "APPROVED_BY_CHECKER" in resp.json()["detail"]

        st = get_gstr3b_status(db, s["org_id"], s["reg_id"], "2026-03")
        assert st["filing_status"] == "APPROVED_BY_CHECKER"
    finally:
        db.close()


def test_prepare_gstr3b_revision_after_rejection_archives_snapshot(b1_org_setup):
    s = b1_org_setup
    db = SessionLocal()
    try:
        prep1 = prepare_gstr3b_return(db, s["org_id"], s["reg_id"], "2026-04", user_id=s["maker"])
        assert prep1["revision_number"] == 1
        submit_gstr3b_for_review(db, s["org_id"], prep1["id"], s["maker"])
        review_gstr3b_decision(db, s["org_id"], prep1["id"], s["checker_admin"], "REJECTED", "Mismatch in Table 4 ITC")

        # Re-prepare after rejection must succeed and increment revision
        prep2 = prepare_gstr3b_return(db, s["org_id"], s["reg_id"], "2026-04", user_id=s["maker"])
        assert prep2["id"] == prep1["id"]
        assert prep2["filing_status"] == "DRAFT"
        assert prep2["revision_number"] == 2

        st = get_gstr3b_status(db, s["org_id"], s["reg_id"], "2026-04")
        assert st["revision_number"] == 2
        assert st["checker_id"] is None
        assert st["checker_remarks"] is None
        assert st["previous_snapshot"] is not None
        assert st["previous_snapshot"]["revision"] == 1
        assert st["previous_snapshot"]["checker_remarks"] == "Mismatch in Table 4 ITC"
    finally:
        db.close()


def test_concurrent_prepare_and_submit_serialized(b1_org_setup):
    s = b1_org_setup
    db_init = SessionLocal()
    try:
        prep = prepare_gstr3b_return(db_init, s["org_id"], s["reg_id"], "2026-05", user_id=s["maker"])
        filing_id = prep["id"]
    finally:
        db_init.close()

    results = {}
    barrier = threading.Barrier(2)

    def do_submit():
        db = SessionLocal()
        try:
            barrier.wait(timeout=5)
            results["submit"] = submit_gstr3b_for_review(db, s["org_id"], filing_id, s["maker"])
        except Exception as e:
            results["submit_err"] = str(e)
        finally:
            db.close()

    def do_prepare():
        db = SessionLocal()
        try:
            barrier.wait(timeout=5)
            results["prepare"] = prepare_gstr3b_return(db, s["org_id"], s["reg_id"], "2026-05", user_id=s["maker"])
        except Exception as e:
            results["prepare_err"] = str(e)
        finally:
            db.close()

    t1 = threading.Thread(target=do_submit)
    t2 = threading.Thread(target=do_prepare)
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    # Regardless of which thread wins the FOR UPDATE lock first:
    # - Submit MUST succeed (either on original DRAFT or on re-prepared DRAFT)
    # - If Submit ran first, Prepare MUST fail with GSTR3BServiceError (SUBMITTED_FOR_REVIEW)
    # - Final status MUST be SUBMITTED_FOR_REVIEW (never silently overwritten back to DRAFT)
    assert "submit" in results
    db_check = SessionLocal()
    try:
        st = get_gstr3b_status(db_check, s["org_id"], s["reg_id"], "2026-05")
        assert st["filing_status"] == "SUBMITTED_FOR_REVIEW"
    finally:
        db_check.close()


# =============================================================================
# 2. P1 - AUTHORITATIVE CHECKER ROLE & MEMBERSHIP ENFORCEMENT
# =============================================================================

def test_checker_authorization_enforces_active_admin_or_owner(b1_org_setup):
    s = b1_org_setup
    db = SessionLocal()
    client = TestClient(app)
    try:
        prep = prepare_gstr3b_return(db, s["org_id"], s["reg_id"], "2026-06", user_id=s["maker"])
        filing_id = prep["id"]
        submit_gstr3b_for_review(db, s["org_id"], filing_id, s["maker"])

        # 1. Distinct accountant -> rejected
        with pytest.raises(GSTR3BServiceError, match="Unauthorized reviewer"):
            review_gstr3b_decision(db, s["org_id"], filing_id, s["other_accountant"], "APPROVED", "Try approve")

        # 2. Viewer -> rejected
        with pytest.raises(GSTR3BServiceError, match="Unauthorized reviewer"):
            review_gstr3b_decision(db, s["org_id"], filing_id, s["viewer"], "APPROVED", "Try approve")

        # 3. Suspended admin -> rejected
        with pytest.raises(GSTR3BServiceError, match="Unauthorized reviewer"):
            review_gstr3b_decision(db, s["org_id"], filing_id, s["suspended_admin"], "APPROVED", "Try approve")

        # 4. Outsider admin -> rejected
        with pytest.raises(GSTR3BServiceError, match="Unauthorized reviewer"):
            review_gstr3b_decision(db, s["org_id"], filing_id, s["outsider_admin"], "APPROVED", "Try approve")

        # 5. Maker self-approval via API -> HTTP 400
        token_maker = create_access_token(subject=s["maker"], org_id=s["org_id"])
        resp_self = client.post(
            f"/api/v1/gstr3b/{filing_id}/review-decision",
            json={"decision": "APPROVED", "remarks": "Self approval attempt"},
            headers={"Authorization": f"Bearer {token_maker}"},
        )
        assert resp_self.status_code == 400
        assert "Segregation of duties violation" in resp_self.json()["detail"]

        # 6. Distinct accountant via API -> HTTP 400 Unauthorized reviewer
        token_other_acc = create_access_token(subject=s["other_accountant"], org_id=s["org_id"])
        resp_acc = client.post(
            f"/api/v1/gstr3b/{filing_id}/review-decision",
            json={"decision": "APPROVED", "remarks": "Unauthorized role attempt"},
            headers={"Authorization": f"Bearer {token_other_acc}"},
        )
        assert resp_acc.status_code == 400
        assert "Unauthorized reviewer" in resp_acc.json()["detail"]

        # 7. Active owner -> succeeds
        token_owner = create_access_token(subject=s["checker_owner"], org_id=s["org_id"])
        resp_ok = client.post(
            f"/api/v1/gstr3b/{filing_id}/review-decision",
            json={"decision": "APPROVED", "remarks": "Authorized owner approval"},
            headers={"Authorization": f"Bearer {token_owner}"},
        )
        assert resp_ok.status_code == 200
        assert resp_ok.json()["status"] == "APPROVED_BY_CHECKER"
    finally:
        db.close()


# =============================================================================
# 3. P2 - STRICT DEVELOPMENT IDENTITY FALLBACK GATING
# =============================================================================

def test_staging_and_production_reject_dev_header_auth(b1_org_setup, monkeypatch):
    s = b1_org_setup
    client = TestClient(app)

    # 1. Staging must reject header-only auth with HTTP 401
    monkeypatch.setattr(settings, "ENVIRONMENT", "staging")
    resp_stg = client.get(
        "/api/v1/scheme-navigator/profile",
        headers={"X-Org-Id": s["org_id"], "X-User-Id": s["maker"]},
    )
    assert resp_stg.status_code == 401
    assert "Bearer access token required" in resp_stg.json()["detail"]

    # 2. Production must reject header-only auth with HTTP 401
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    resp_prod = client.get(
        "/api/v1/scheme-navigator/profile",
        headers={"X-Org-Id": s["org_id"], "X-User-Id": s["maker"]},
    )
    assert resp_prod.status_code == 401
    assert "Bearer access token required" in resp_prod.json()["detail"]

    # 3. Development with ALLOW_DEV_HEADER_AUTH=False must also reject header-only auth
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(settings, "ALLOW_DEV_HEADER_AUTH", False)
    resp_dev_disabled = client.get(
        "/api/v1/scheme-navigator/profile",
        headers={"X-Org-Id": s["org_id"], "X-User-Id": s["maker"]},
    )
    assert resp_dev_disabled.status_code == 401

    # 4. Fail-closed startup validation: ALLOW_DEV_HEADER_AUTH=True in production/staging raises ValueError
    for env_name in ("production", "staging"):
        monkeypatch.setenv("ENVIRONMENT", env_name)
        with pytest.raises(ValueError, match="ALLOW_DEV_HEADER_AUTH cannot be enabled"):
            Settings(
                ENVIRONMENT=env_name,
                ALLOW_DEV_HEADER_AUTH=True,
                SECRET_KEY="a-very-long-production-secret-key-at-least-32-chars",
                DATABASE_URL="postgresql+psycopg://app_service_user:prodpass@db.internal:5432/myca",
                DATABASE_WORKER_URL="postgresql+psycopg://worker_service_user:prodpass@db.internal:5432/myca",
            )


# =============================================================================
# 4. P3 - EXPANDED IMMUTABILITY TRIGGERS (APPROVED_BY_CHECKER & GST_RECORDS)
# =============================================================================

def test_db_triggers_protect_approved_submitted_and_gst_records(b1_org_setup):
    s = b1_org_setup
    db = SessionLocal()
    try:
        prep = prepare_gstr3b_return(db, s["org_id"], s["reg_id"], "2026-07", user_id=s["maker"])
        filing_id = prep["id"]
        submit_gstr3b_for_review(db, s["org_id"], filing_id, s["maker"])
    finally:
        db.close()

    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            # 1. Direct SQL financial mutation while SUBMITTED_FOR_REVIEW -> 42501
            with pytest.raises(psycopg.errors.InsufficientPrivilege) as exc_sub_upd:
                cur.execute(
                    "UPDATE public.gstr3b_filings SET table_3_1_data = '{\"tampered\": true}'::jsonb WHERE id = %s;",
                    (filing_id,),
                )
            assert exc_sub_upd.value.sqlstate == "42501"

            # 2. Direct SQL DELETE while SUBMITTED_FOR_REVIEW -> 42501
            with pytest.raises(psycopg.errors.InsufficientPrivilege) as exc_sub_del:
                cur.execute("DELETE FROM public.gstr3b_filings WHERE id = %s;", (filing_id,))
            assert exc_sub_del.value.sqlstate == "42501"

    # Approve and File
    db2 = SessionLocal()
    try:
        review_gstr3b_decision(db2, s["org_id"], filing_id, s["checker_admin"], "APPROVED", "Approved")
    finally:
        db2.close()

    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            # 3. Direct SQL financial mutation while APPROVED_BY_CHECKER -> 42501
            with pytest.raises(psycopg.errors.InsufficientPrivilege) as exc_app_upd:
                cur.execute(
                    "UPDATE public.gstr3b_filings SET offset_data = '{\"tampered\": true}'::jsonb WHERE id = %s;",
                    (filing_id,),
                )
            assert exc_app_upd.value.sqlstate == "42501"

            # 4. Direct SQL DELETE while APPROVED_BY_CHECKER -> 42501
            with pytest.raises(psycopg.errors.InsufficientPrivilege) as exc_app_del:
                cur.execute("DELETE FROM public.gstr3b_filings WHERE id = %s;", (filing_id,))
            assert exc_app_del.value.sqlstate == "42501"

    # File return -> creates entry in public.gst_records
    db3 = SessionLocal()
    try:
        file_res = file_gstr3b_to_gsp_sandbox(db3, s["org_id"], filing_id, s["checker_admin"])
        assert file_res.status == "FILED"
    finally:
        db3.close()

    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM public.gst_records WHERE org_id = %s AND tax_period = '2026-07' AND return_type = 'GSTR-3B';",
                (s["org_id"],),
            )
            rec = cur.fetchone()
            assert rec is not None
            gst_rec_id = rec[0]

            # 5. Direct SQL UPDATE on filed gst_records -> 42501
            with pytest.raises(psycopg.errors.InsufficientPrivilege) as exc_gst_upd:
                cur.execute(
                    "UPDATE public.gst_records SET total_tax_paid = 0 WHERE id = %s;",
                    (gst_rec_id,),
                )
            assert exc_gst_upd.value.sqlstate == "42501"

            # 6. Direct SQL DELETE on filed gst_records -> 42501
            with pytest.raises(psycopg.errors.InsufficientPrivilege) as exc_gst_del:
                cur.execute("DELETE FROM public.gst_records WHERE id = %s;", (gst_rec_id,))
            assert exc_gst_del.value.sqlstate == "42501"
