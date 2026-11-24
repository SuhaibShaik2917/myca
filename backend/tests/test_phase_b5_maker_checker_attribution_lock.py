"""
Phase B.5 — Same-State Maker & Checker Attribution Immutability Tests.

Covers (all SQL attacks executed as least-privilege `app_service_user` with tenant context):
1. Change `maker_id` on `SUBMITTED_FOR_REVIEW` without changing status -> SQLSTATE 42501.
2. Change `maker_id` on `APPROVED_BY_CHECKER` without changing status -> SQLSTATE 42501.
3. Change `checker_id` on `APPROVED_BY_CHECKER` without changing status -> SQLSTATE 42501.
4. Change `checker_remarks` on `APPROVED_BY_CHECKER` without changing status -> SQLSTATE 42501.
5. Change `maker_id` on `REJECTED_BY_CHECKER` without using the permitted revision workflow -> SQLSTATE 42501.
6. Admin who prepared/submitted their own return cannot rewrite `maker_id` and approve via legitimate API.
7. Legitimate preparation, rejection, revision (`REJECTED_BY_CHECKER -> DRAFT`), resubmission, and Checker approval
   succeed, preserving historical Maker/Checker identities in `gstr3b_revision_history`.
8. Verifies `app_service_user` has zero privileges on private trust-anchor/token tables and append-only grants on history.
"""

import random
import string
import uuid
import psycopg
import pytest
from fastapi.testclient import TestClient

from app.core.security import create_access_token
from app.database import SessionLocal
from app.main import app
from app.modules.gstr3b.service import (
    prepare_gstr3b_return,
    submit_gstr3b_for_review,
    review_gstr3b_decision,
    get_gstr3b_revision_history,
    get_gstr3b_status,
)
from tests.test_db_config import DB_ADMIN, DB_APP


@pytest.fixture
def b5_org_setup():
    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            org_id = str(uuid.uuid4())
            pan = f"{''.join(random.choices(string.ascii_uppercase, k=5))}{random.randint(1000, 9999)}A"
            gstin = f"27{pan}1ZA"
            reg_id = str(uuid.uuid4())

            cur.execute("INSERT INTO public.organizations (id, legal_name, pan) VALUES (%s, 'B5 Org', %s);", (org_id, pan))

            users = {}
            for label, role in [
                ("maker", "accountant"),
                ("admin_maker", "admin"),
                ("checker_admin", "admin"),
                ("bystander", "accountant"),
            ]:
                uid = str(uuid.uuid4())
                cur.execute(
                    "INSERT INTO public.global_users (id, email, full_name) VALUES (%s, %s, %s);",
                    (uid, f"b5_{label}_{uuid.uuid4().hex}@example.com", f"B5 {label}"),
                )
                cur.execute(
                    "INSERT INTO public.organization_memberships (org_id, user_id, role, status) VALUES (%s, %s, %s, 'active');",
                    (org_id, uid, role),
                )
                users[label] = uid

            cur.execute(
                """
                INSERT INTO public.gst_registrations (id, org_id, gstin, state_code, legal_name, is_primary)
                VALUES (%s, %s, %s, '27', 'B5 Legal Entity', true);
                """,
                (reg_id, org_id, gstin),
            )

    return {"org_id": org_id, "reg_id": reg_id, **users}


def _app_exec(org_id: str, user_id: str, sql: str, params: tuple = ()):
    with psycopg.connect(DB_APP, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('app.current_org', %s, false);", (org_id,))
            cur.execute("SELECT set_config('app.current_user', %s, false);", (user_id,))
            cur.execute(sql, params)


def test_p0_same_state_maker_and_checker_attribution_immutable(b5_org_setup):
    s = b5_org_setup
    db = SessionLocal()
    try:
        prep = prepare_gstr3b_return(db, s["org_id"], s["reg_id"], "2026-01", user_id=s["maker"])
        filing_id = prep["id"]
        submit_gstr3b_for_review(db, s["org_id"], filing_id, s["maker"], "Submit Rev 1")
    finally:
        db.close()

    # 1. Change maker_id on SUBMITTED_FOR_REVIEW without changing status -> 42501
    with pytest.raises(psycopg.errors.InsufficientPrivilege) as exc_sub_maker:
        _app_exec(
            s["org_id"],
            s["maker"],
            "UPDATE public.gstr3b_filings SET maker_id = %s WHERE id = %s;",
            (s["bystander"], filing_id),
        )
    assert exc_sub_maker.value.sqlstate == "42501"

    # Reject -> verify maker_id cannot be changed on REJECTED_BY_CHECKER without permitted revision workflow
    db2 = SessionLocal()
    try:
        review_gstr3b_decision(db2, s["org_id"], filing_id, s["checker_admin"], "REJECTED", "Reject Rev 1")
    finally:
        db2.close()

    # 2. Change maker_id on REJECTED_BY_CHECKER without changing status -> 42501
    with pytest.raises(psycopg.errors.InsufficientPrivilege) as exc_rej_maker:
        _app_exec(
            s["org_id"],
            s["maker"],
            "UPDATE public.gstr3b_filings SET maker_id = %s WHERE id = %s;",
            (s["bystander"], filing_id),
        )
    assert exc_rej_maker.value.sqlstate == "42501"

    # 3. Change checker_id / checker_remarks on REJECTED_BY_CHECKER without changing status -> 42501
    with pytest.raises(psycopg.errors.InsufficientPrivilege) as exc_rej_chk:
        _app_exec(
            s["org_id"],
            s["checker_admin"],
            "UPDATE public.gstr3b_filings SET checker_remarks = 'Tampered remark' WHERE id = %s;",
            (filing_id,),
        )
    assert exc_rej_chk.value.sqlstate == "42501"

    # Legitimate revision -> resubmit -> approve
    db3 = SessionLocal()
    try:
        p2 = prepare_gstr3b_return(db3, s["org_id"], s["reg_id"], "2026-01", user_id=s["maker"])
        assert p2["revision_number"] == 2
        st = get_gstr3b_status(db3, s["org_id"], s["reg_id"], "2026-01")
        assert st["checker_id"] is None
        assert st["checker_remarks"] is None

        submit_gstr3b_for_review(db3, s["org_id"], filing_id, s["maker"], "Resubmit Rev 2")
        review_gstr3b_decision(db3, s["org_id"], filing_id, s["checker_admin"], "APPROVED", "Approved Rev 2")

        # Verify historical Maker/Checker identities preserved in revision history
        hist = get_gstr3b_revision_history(db3, s["org_id"], filing_id)
        assert len(hist) == 1
        assert hist[0]["revision_number"] == 1
        assert hist[0]["maker_id"] == s["maker"]
        assert hist[0]["checker_id"] == s["checker_admin"]
        assert hist[0]["checker_remarks"] == "Reject Rev 1"
    finally:
        db3.close()

    # 4. Change maker_id on APPROVED_BY_CHECKER without changing status -> 42501
    with pytest.raises(psycopg.errors.InsufficientPrivilege) as exc_app_maker:
        _app_exec(
            s["org_id"],
            s["checker_admin"],
            "UPDATE public.gstr3b_filings SET maker_id = %s WHERE id = %s;",
            (s["bystander"], filing_id),
        )
    assert exc_app_maker.value.sqlstate == "42501"

    # 5. Change checker_id on APPROVED_BY_CHECKER without changing status -> 42501
    with pytest.raises(psycopg.errors.InsufficientPrivilege) as exc_app_chk:
        _app_exec(
            s["org_id"],
            s["checker_admin"],
            "UPDATE public.gstr3b_filings SET checker_id = %s WHERE id = %s;",
            (s["admin_maker"], filing_id),
        )
    assert exc_app_chk.value.sqlstate == "42501"

    # 6. Change checker_remarks on APPROVED_BY_CHECKER without changing status -> 42501
    with pytest.raises(psycopg.errors.InsufficientPrivilege) as exc_app_rem:
        _app_exec(
            s["org_id"],
            s["checker_admin"],
            "UPDATE public.gstr3b_filings SET checker_remarks = 'Falsified approval note' WHERE id = %s;",
            (filing_id,),
        )
    assert exc_app_rem.value.sqlstate == "42501"


def test_p0_admin_maker_cannot_rewrite_maker_id_and_self_approve_via_api(b5_org_setup):
    s = b5_org_setup
    db = SessionLocal()
    client = TestClient(app)
    try:
        # Admin prepares & submits their own return
        prep = prepare_gstr3b_return(db, s["org_id"], s["reg_id"], "2026-02", user_id=s["admin_maker"])
        filing_id = prep["id"]
        submit_gstr3b_for_review(db, s["org_id"], filing_id, s["admin_maker"], "Admin self-submit")

        # Admin attempts direct SQL rewrite of maker_id to bystander -> blocked (42501)
        with pytest.raises(psycopg.errors.InsufficientPrivilege) as exc_rw:
            _app_exec(
                s["org_id"],
                s["admin_maker"],
                "UPDATE public.gstr3b_filings SET maker_id = %s WHERE id = %s;",
                (s["bystander"], filing_id),
            )
        assert exc_rw.value.sqlstate == "42501"

        # Calling legitimate review API as same admin still fails Segregation of Duties (HTTP 400)
        tok_admin = create_access_token(subject=s["admin_maker"], org_id=s["org_id"])
        resp = client.post(
            f"/api/v1/gstr3b/{filing_id}/review-decision",
            json={"decision": "APPROVED", "remarks": "Self-approval attempt"},
            headers={"Authorization": f"Bearer {tok_admin}"},
        )
        assert resp.status_code == 400
        assert "Segregation of duties violation" in resp.json()["detail"]
    finally:
        db.close()


def test_app_service_user_privilege_boundaries_verified():
    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            # 1. Zero table privileges on internal trust-anchor, nonce, and verified token tables
            for tbl in (
                "gstr3b_review_trust_anchors",
                "gstr3b_review_attestation_nonces",
                "gstr3b_verified_review_tokens",
            ):
                cur.execute(
                    """
                    SELECT privilege_type
                    FROM information_schema.role_table_grants
                    WHERE grantee = 'app_service_user' AND table_name = %s;
                    """,
                    (tbl,),
                )
                assert cur.fetchall() == [], f"app_service_user must have zero privileges on {tbl}"

            # 2. Strictly append-only (SELECT, INSERT) on gstr3b_revision_history
            cur.execute(
                """
                SELECT privilege_type
                FROM information_schema.role_table_grants
                WHERE grantee = 'app_service_user' AND table_name = 'gstr3b_revision_history';
                """
            )
            privs = {r[0] for r in cur.fetchall()}
            assert privs == {"SELECT", "INSERT"}
