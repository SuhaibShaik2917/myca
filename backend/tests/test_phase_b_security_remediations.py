"""
Phase B Critical Product Security and Financial Integrity Remediation Tests.

Covers:
1. P0 - Cross-Tenant Authorization:
   - Header tampering detection (conflicting X-Org-Id / X-User-Id rejected with HTTP 403).
   - Pure Bearer token identity derivation without caller-supplied headers.
   - Active membership validation preventing unauthorized tenant access.
   - Database session context locking preventing cross-tenant context switching.
2. P0 - Filed GSTR-3B Immutability:
   - Application-level rejection of /prepare when target return is FILED.
   - Database-level trigger trg_prevent_gstr3b_filed_mutations raising SQLSTATE 42501 on UPDATE.
   - Database-level trigger trg_prevent_gstr3b_filed_mutations raising SQLSTATE 42501 on DELETE.
3. P1 - Maker-Checker Segregation of Duties:
   - Application-level rejection of self-approval (Maker == Checker) raising GSTR3BServiceError.
   - Legitimate approval by distinct Checker succeeding.
   - Database check constraint chk_gstr3b_maker_checker_distinct raising SQLSTATE 23514.
"""

import json
import random
import string
import uuid
from decimal import Decimal
import psycopg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.config import settings
from app.core.security import create_access_token
from app.database import SessionLocal, set_tenant_context
from app.main import app
from app.modules.gstr3b.service import (
    prepare_gstr3b_return,
    submit_gstr3b_for_review,
    review_gstr3b_decision,
    file_gstr3b_to_gsp_sandbox,
    GSTR3BServiceError,
)
from tests.test_db_config import DB_ADMIN


@pytest.fixture
def dual_tenant_setup():
    """
    Sets up two isolated tenant organizations (Org Alpha, Org Beta)
    with their own users, active memberships, and primary GST registrations.
    """
    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            # Tenant Alpha
            org_a = str(uuid.uuid4())
            user_a = str(uuid.uuid4())
            checker_a = str(uuid.uuid4())
            pan_a = f"{''.join(random.choices(string.ascii_uppercase, k=5))}{random.randint(1000, 9999)}A"
            gstin_a = f"27{pan_a}1ZA"
            reg_a = str(uuid.uuid4())

            cur.execute("INSERT INTO public.organizations (id, legal_name, pan) VALUES (%s, 'Alpha Corp', %s);", (org_a, pan_a))
            cur.execute("INSERT INTO public.global_users (id, email, full_name) VALUES (%s, %s, 'User Alpha');", (user_a, f"alpha_{uuid.uuid4().hex}@example.com"))
            cur.execute("INSERT INTO public.global_users (id, email, full_name) VALUES (%s, %s, 'Checker Alpha');", (checker_a, f"checker_a_{uuid.uuid4().hex}@example.com"))
            cur.execute("INSERT INTO public.organization_memberships (org_id, user_id, role, status) VALUES (%s, %s, 'accountant', 'active');", (org_a, user_a))
            cur.execute("INSERT INTO public.organization_memberships (org_id, user_id, role, status) VALUES (%s, %s, 'admin', 'active');", (org_a, checker_a))
            cur.execute("""
                INSERT INTO public.gst_registrations (id, org_id, gstin, state_code, legal_name, is_primary)
                VALUES (%s, %s, %s, '27', 'Alpha Corp GST', true);
            """, (reg_a, org_a, gstin_a))

            # Tenant Beta
            org_b = str(uuid.uuid4())
            user_b = str(uuid.uuid4())
            pan_b = f"{''.join(random.choices(string.ascii_uppercase, k=5))}{random.randint(1000, 9999)}B"
            gstin_b = f"29{pan_b}1ZB"
            reg_b = str(uuid.uuid4())

            cur.execute("INSERT INTO public.organizations (id, legal_name, pan) VALUES (%s, 'Beta Industries', %s);", (org_b, pan_b))
            cur.execute("INSERT INTO public.global_users (id, email, full_name) VALUES (%s, %s, 'User Beta');", (user_b, f"beta_{uuid.uuid4().hex}@example.com"))
            cur.execute("INSERT INTO public.organization_memberships (org_id, user_id, role, status) VALUES (%s, %s, 'owner', 'active');", (org_b, user_b))
            cur.execute("""
                INSERT INTO public.gst_registrations (id, org_id, gstin, state_code, legal_name, is_primary)
                VALUES (%s, %s, %s, '29', 'Beta Industries GST', true);
            """, (reg_b, org_b, gstin_b))

    return {
        "org_a": org_a,
        "user_a": user_a,
        "checker_a": checker_a,
        "reg_a": reg_a,
        "gstin_a": gstin_a,
        "org_b": org_b,
        "user_b": user_b,
        "reg_b": reg_b,
        "gstin_b": gstin_b,
    }


# =============================================================================
# PART 1: P0 - CROSS-TENANT AUTHORIZATION TESTS
# =============================================================================

def test_cross_tenant_token_tampering_header_rejected(dual_tenant_setup):
    """
    Caller with valid Bearer token for Tenant Alpha passes X-Org-Id header for Tenant Beta.
    Server MUST detect cross-tenant header tampering and reject immediately with HTTP 403.
    """
    setup = dual_tenant_setup
    token_a = create_access_token(subject=setup["user_a"], org_id=setup["org_a"])
    client = TestClient(app)

    # 1. Scheme Navigator
    resp = client.get(
        "/api/v1/scheme-navigator/profile",
        headers={"Authorization": f"Bearer {token_a}", "X-Org-Id": setup["org_b"]}
    )
    assert resp.status_code == 403
    assert "Cross-tenant header tampering detected" in resp.json()["detail"]

    # 2. Credit Builder
    resp = client.get(
        "/api/v1/credit-builder/summary",
        headers={"Authorization": f"Bearer {token_a}", "X-Org-Id": setup["org_b"]}
    )
    assert resp.status_code == 403
    assert "Cross-tenant header tampering detected" in resp.json()["detail"]

    # 3. GSTR-3B Prepare
    resp = client.post(
        "/api/v1/gstr3b/prepare",
        json={"registration_id": setup["reg_a"], "tax_period": "2026-11"},
        headers={"Authorization": f"Bearer {token_a}", "X-Org-Id": setup["org_b"]}
    )
    assert resp.status_code == 403
    assert "Cross-tenant header tampering detected" in resp.json()["detail"]

    # 4. Invoice Brain List Invoices
    resp = client.get(
        "/api/v1/invoices",
        headers={"Authorization": f"Bearer {token_a}", "X-Org-Id": setup["org_b"]}
    )
    assert resp.status_code == 403
    assert "Cross-tenant header tampering detected" in resp.json()["detail"]

    # 5. Reconciliation GST Registrations
    resp = client.get(
        "/api/v1/reconciliation/gst-registrations",
        headers={"Authorization": f"Bearer {token_a}", "X-Org-Id": setup["org_b"]}
    )
    assert resp.status_code == 403
    assert "Cross-tenant header tampering detected" in resp.json()["detail"]


def test_cross_user_token_tampering_header_rejected(dual_tenant_setup):
    """
    Caller with valid Bearer token for User Alpha passes X-User-Id header for User Beta.
    Server MUST detect cross-user header tampering and reject with HTTP 403.
    """
    setup = dual_tenant_setup
    token_a = create_access_token(subject=setup["user_a"], org_id=setup["org_a"])
    client = TestClient(app)

    resp = client.get(
        "/api/v1/scheme-navigator/profile",
        headers={"Authorization": f"Bearer {token_a}", "X-User-Id": setup["user_b"]}
    )
    assert resp.status_code == 403
    assert "Cross-user header tampering detected" in resp.json()["detail"]


def test_pure_bearer_token_without_headers_derives_identity(dual_tenant_setup):
    """
    Caller passes ONLY Bearer token with no X-Org-Id or X-User-Id headers.
    Identity is derived strictly from verified token claims.
    """
    setup = dual_tenant_setup
    token_a = create_access_token(subject=setup["user_a"], org_id=setup["org_a"])
    client = TestClient(app)

    # Scheme navigator profile returns successfully for Org Alpha
    resp = client.get(
        "/api/v1/scheme-navigator/profile",
        headers={"Authorization": f"Bearer {token_a}"}
    )
    assert resp.status_code == 200
    assert resp.json()["org_id"] == setup["org_a"]

    # Reconciliation GST registrations returns Org Alpha registration
    resp = client.get(
        "/api/v1/reconciliation/gst-registrations",
        headers={"Authorization": f"Bearer {token_a}"}
    )
    assert resp.status_code == 200
    regs = resp.json()
    assert len(regs) >= 1
    assert any(r["gstin"] == setup["gstin_a"] for r in regs)
    assert not any(r["gstin"] == setup["gstin_b"] for r in regs)


def test_unauthorized_organization_membership_rejected(dual_tenant_setup):
    """
    Caller provides X-Org-Id and X-User-Id where user does not have active membership in the org.
    Server verifies membership in database and rejects with HTTP 403.
    """
    setup = dual_tenant_setup
    client = TestClient(app)

    # User Alpha attempting to access Org Beta via dev/test headers
    resp = client.get(
        "/api/v1/reconciliation/gst-registrations",
        headers={"X-Org-Id": setup["org_b"], "X-User-Id": setup["user_a"]}
    )
    assert resp.status_code == 403
    assert "active membership" in resp.json()["detail"].lower()


def test_database_session_cross_tenant_locking():
    """
    Attempting to switch tenant context on an already-bound session raises PermissionError.
    """
    org1 = str(uuid.uuid4())
    org2 = str(uuid.uuid4())
    user_id = str(uuid.uuid4())

    db = SessionLocal()
    try:
        db.info["current_org"] = org1
        set_tenant_context(db, org_id=org1, user_id=user_id)

        # Attempt cross-tenant switch
        with pytest.raises(PermissionError) as exc_info:
            set_tenant_context(db, org_id=org2, user_id=user_id)
        assert "CRITICAL SECURITY VIOLATION: Attempted cross-tenant context switch" in str(exc_info.value)
    finally:
        db.close()


# =============================================================================
# PART 2: P0 - FILED GSTR-3B IMMUTABILITY TESTS
# =============================================================================

def test_filed_gstr3b_prepare_application_level_rejection(dual_tenant_setup):
    """
    Application-level gate: prepare_gstr3b_return rejects preparation
    when the target return is already in 'FILED' status.
    """
    setup = dual_tenant_setup
    org_id = setup["org_a"]
    reg_id = setup["reg_a"]
    maker_id = setup["user_a"]
    checker_id = setup["checker_a"]
    tax_period = "2026-11"

    db = SessionLocal()
    try:
        db.info["current_org"] = org_id
        db.info["current_user"] = maker_id

        # 1. Prepare draft
        prep_res = prepare_gstr3b_return(
            session=db,
            org_id=org_id,
            registration_id=reg_id,
            tax_period=tax_period,
            user_id=maker_id
        )
        filing_id = prep_res["id"]

        # 2. Submit for review
        submit_gstr3b_for_review(
            session=db,
            org_id=org_id,
            filing_id=filing_id,
            user_id=maker_id
        )

        # 3. Checker approves
        review_gstr3b_decision(
            session=db,
            org_id=org_id,
            filing_id=filing_id,
            checker_id=checker_id,
            decision="APPROVED",
            remarks="Legitimate review"
        )

        # 4. File return to GSP sandbox -> status becomes 'FILED'
        file_res = file_gstr3b_to_gsp_sandbox(
            session=db,
            org_id=org_id,
            filing_id=filing_id,
            user_id=checker_id
        )
        assert file_res.status == "FILED"

        # 5. Application gate: prepare_gstr3b_return MUST raise GSTR3BServiceError
        with pytest.raises(GSTR3BServiceError) as exc_info:
            prepare_gstr3b_return(
                session=db,
                org_id=org_id,
                registration_id=reg_id,
                tax_period=tax_period,
                user_id=maker_id
            )
        assert "strictly immutable" in str(exc_info.value)

        # 6. API endpoint also returns HTTP 400
        client = TestClient(app)
        token_a = create_access_token(subject=maker_id, org_id=org_id)
        resp = client.post(
            "/api/v1/gstr3b/prepare",
            json={"registration_id": reg_id, "tax_period": tax_period},
            headers={"Authorization": f"Bearer {token_a}"}
        )
        assert resp.status_code == 400
        assert "strictly immutable" in resp.json()["detail"]

    finally:
        db.close()


def test_filed_gstr3b_database_trigger_blocks_update(dual_tenant_setup):
    """
    Database-level trigger: trg_prevent_gstr3b_filed_mutations prevents
    any direct SQL or ORM update on a FILED return, raising SQLSTATE 42501.
    """
    setup = dual_tenant_setup
    org_id = setup["org_a"]
    reg_id = setup["reg_a"]
    maker_id = setup["user_a"]
    checker_id = setup["checker_a"]
    tax_period = "2026-10"

    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            filing_id = str(uuid.uuid4())
            # Directly insert a FILED return
            cur.execute("""
                INSERT INTO public.gstr3b_filings (
                    id, org_id, registration_id, tax_period, filing_status,
                    table_3_1_data, table_4_data, offset_data, maker_id, checker_id,
                    arn, cin, filing_date
                ) VALUES (
                    %s, %s, %s, %s, 'FILED',
                    '{}', '{}', '{}', %s, %s,
                    'ARNTEST12345', 'CINTEST12345', clock_timestamp()
                );
            """, (filing_id, org_id, reg_id, tax_period, maker_id, checker_id))

            # Attempt direct SQL UPDATE on the filed return
            with pytest.raises(psycopg.errors.InsufficientPrivilege) as exc_info:
                cur.execute("""
                    UPDATE public.gstr3b_filings
                    SET table_3_1_data = '{"tampered": true}'::jsonb
                    WHERE id = %s;
                """, (filing_id,))
            assert exc_info.value.sqlstate == "42501"
            assert "strictly immutable. Modifications prohibited" in str(exc_info.value)


def test_filed_gstr3b_database_trigger_blocks_delete(dual_tenant_setup):
    """
    Database-level trigger: trg_prevent_gstr3b_filed_mutations prevents
    any direct SQL deletion of a FILED return, raising SQLSTATE 42501.
    """
    setup = dual_tenant_setup
    org_id = setup["org_a"]
    reg_id = setup["reg_a"]
    maker_id = setup["user_a"]
    checker_id = setup["checker_a"]
    tax_period = "2026-09"

    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            filing_id = str(uuid.uuid4())
            cur.execute("""
                INSERT INTO public.gstr3b_filings (
                    id, org_id, registration_id, tax_period, filing_status,
                    table_3_1_data, table_4_data, offset_data, maker_id, checker_id,
                    arn, cin, filing_date
                ) VALUES (
                    %s, %s, %s, %s, 'FILED',
                    '{}', '{}', '{}', %s, %s,
                    'ARNTEST99999', 'CINTEST99999', clock_timestamp()
                );
            """, (filing_id, org_id, reg_id, tax_period, maker_id, checker_id))

            # Attempt direct SQL DELETE on the filed return
            with pytest.raises(psycopg.errors.InsufficientPrivilege) as exc_info:
                cur.execute("DELETE FROM public.gstr3b_filings WHERE id = %s;", (filing_id,))
            assert exc_info.value.sqlstate == "42501"
            assert "strictly immutable. Deletion prohibited" in str(exc_info.value)


# =============================================================================
# PART 3: P1 - MAKER-CHECKER SEGREGATION OF DUTIES TESTS
# =============================================================================

def test_maker_checker_self_approval_rejected_application_level(dual_tenant_setup):
    """
    Application-level: Maker cannot approve their own submission.
    When checker_id == maker_id, review_gstr3b_decision raises GSTR3BServiceError.
    """
    setup = dual_tenant_setup
    org_id = setup["org_a"]
    reg_id = setup["reg_a"]
    maker_id = setup["user_a"]
    tax_period = "2026-08"

    db = SessionLocal()
    try:
        db.info["current_org"] = org_id
        db.info["current_user"] = maker_id

        # 1. Prepare draft
        prep_res = prepare_gstr3b_return(
            session=db,
            org_id=org_id,
            registration_id=reg_id,
            tax_period=tax_period,
            user_id=maker_id
        )
        filing_id = prep_res["id"]

        # 2. Maker submits for review
        submit_gstr3b_for_review(
            session=db,
            org_id=org_id,
            filing_id=filing_id,
            user_id=maker_id
        )

        # 3. Maker attempts self-approval -> MUST FAIL
        with pytest.raises(GSTR3BServiceError) as exc_info:
            review_gstr3b_decision(
                session=db,
                org_id=org_id,
                filing_id=filing_id,
                checker_id=maker_id,
                decision="APPROVED",
                remarks="Self-approving my own return"
            )
        assert "Segregation of duties violation: Maker cannot act as Checker" in str(exc_info.value)

        # 4. API endpoint also rejects self-approval with HTTP 400
        client = TestClient(app)
        token_maker = create_access_token(subject=maker_id, org_id=org_id)
        resp = client.post(
            f"/api/v1/gstr3b/{filing_id}/review-decision",
            json={"decision": "APPROVED", "remarks": "Self-approving via API"},
            headers={"Authorization": f"Bearer {token_maker}"}
        )
        assert resp.status_code == 400
        assert "Segregation of duties violation" in resp.json()["detail"]

    finally:
        db.close()


def test_maker_checker_distinct_checker_approval_succeeds(dual_tenant_setup):
    """
    Legitimate workflow: Distinct Checker approves Maker's return.
    Transitions return to APPROVED_BY_CHECKER with checker_id recorded.
    """
    setup = dual_tenant_setup
    org_id = setup["org_a"]
    reg_id = setup["reg_a"]
    maker_id = setup["user_a"]
    checker_id = setup["checker_a"]
    tax_period = "2026-07"

    db = SessionLocal()
    try:
        db.info["current_org"] = org_id
        db.info["current_user"] = maker_id

        prep_res = prepare_gstr3b_return(
            session=db,
            org_id=org_id,
            registration_id=reg_id,
            tax_period=tax_period,
            user_id=maker_id
        )
        filing_id = prep_res["id"]

        submit_gstr3b_for_review(
            session=db,
            org_id=org_id,
            filing_id=filing_id,
            user_id=maker_id
        )

        # Distinct checker approves via API
        client = TestClient(app)
        token_checker = create_access_token(subject=checker_id, org_id=org_id)
        resp = client.post(
            f"/api/v1/gstr3b/{filing_id}/review-decision",
            json={"decision": "APPROVED", "remarks": "Audited and approved by CA"},
            headers={"Authorization": f"Bearer {token_checker}"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "APPROVED_BY_CHECKER"
        assert data["decision"] == "APPROVED"

    finally:
        db.close()


def test_maker_checker_database_check_constraint_blocks_self_approval(dual_tenant_setup):
    """
    Database-level check constraint: chk_gstr3b_maker_checker_distinct
    prevents any row with filing_status IN ('APPROVED_BY_CHECKER', 'FILED')
    where maker_id == checker_id, raising SQLSTATE 23514.
    """
    setup = dual_tenant_setup
    org_id = setup["org_a"]
    reg_id = setup["reg_a"]
    user_id = setup["user_a"]
    tax_period = "2026-06"

    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            filing_id = str(uuid.uuid4())
            # Insert a draft with maker_id
            cur.execute("""
                INSERT INTO public.gstr3b_filings (
                    id, org_id, registration_id, tax_period, filing_status,
                    table_3_1_data, table_4_data, offset_data, maker_id
                ) VALUES (
                    %s, %s, %s, %s, 'DRAFT',
                    '{}', '{}', '{}', %s
                );
            """, (filing_id, org_id, reg_id, tax_period, user_id))

            # Attempt direct SQL UPDATE setting APPROVED_BY_CHECKER with maker_id == checker_id
            with pytest.raises(psycopg.errors.CheckViolation) as exc_info:
                cur.execute("""
                    UPDATE public.gstr3b_filings
                    SET filing_status = 'APPROVED_BY_CHECKER',
                        checker_id = %s
                    WHERE id = %s;
                """, (user_id, filing_id))
            assert exc_info.value.sqlstate == "23514"
            assert "chk_gstr3b_maker_checker_distinct" in str(exc_info.value)

            # Attempt direct SQL UPDATE setting FILED with maker_id == checker_id
            with pytest.raises(psycopg.errors.CheckViolation) as exc_info:
                cur.execute("""
                    UPDATE public.gstr3b_filings
                    SET filing_status = 'FILED',
                        checker_id = %s
                    WHERE id = %s;
                """, (user_id, filing_id))
            assert exc_info.value.sqlstate == "23514"
            assert "chk_gstr3b_maker_checker_distinct" in str(exc_info.value)
