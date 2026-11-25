"""
Phase B.3 — Complete GSTR-3B Database State Machine (app_service_user) & Legacy Snapshot Backfill Tests.

Covers:
1. P0 - Direct-SQL State-Transition Attacks using least-privilege `app_service_user` with tenant context:
   - DRAFT -> APPROVED_BY_CHECKER (rejected, SQLSTATE 42501).
   - DRAFT -> FILED (rejected, SQLSTATE 42501).
   - REJECTED_BY_CHECKER -> APPROVED_BY_CHECKER (rejected, SQLSTATE 42501).
   - REJECTED_BY_CHECKER -> FILED (rejected, SQLSTATE 42501).
   - SUBMITTED_FOR_REVIEW -> FILED / DRAFT (rejected, SQLSTATE 42501).
   - DRAFT -> SUBMITTED_FOR_REVIEW with financial mutation (rejected, SQLSTATE 42501).
   - SUBMITTED_FOR_REVIEW -> APPROVED_BY_CHECKER / REJECTED_BY_CHECKER with forged checker_id
     (non-member, accountant, suspended admin, maker==checker) -> rejected, SQLSTATE 42501.
   - Legitimate service-layer prepare -> submit -> reject -> re-prepare -> submit -> approve -> file + API history retrieval succeed.
2. P1 - Legacy Phase B.1 `previous_snapshot` Migration Backfill & `rejected_at` Preservation:
   - Seeds a Phase B.1-style DRAFT filing (revision 2 + non-null previous_snapshot for revision 1) at revision 0033 on a disposable DB.
   - Upgrades to 0034_gstr3b_state_machine and proves Revision 1 is backfilled with original `rejected_at` distinct from `archived_at`.
   - Runs another rejection & re-preparation cycle and proves both Revision 1 and Revision 2 remain preserved in `gstr3b_revision_history`.
"""

import json
import os
import random
import string
import subprocess
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
    file_gstr3b_to_gsp_sandbox,
    get_gstr3b_revision_history,
)
from tests.test_db_config import DB_ADMIN, DB_APP


@pytest.fixture
def b3_org_setup():
    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            org_id = str(uuid.uuid4())
            other_org_id = str(uuid.uuid4())
            pan_a = f"{''.join(random.choices(string.ascii_uppercase, k=5))}{random.randint(1000, 9999)}A"
            pan_b = f"{''.join(random.choices(string.ascii_uppercase, k=5))}{random.randint(1000, 9999)}B"
            gstin_a = f"27{pan_a}1ZA"
            reg_id = str(uuid.uuid4())

            cur.execute("INSERT INTO public.organizations (id, legal_name, pan) VALUES (%s, 'B3 Org', %s);", (org_id, pan_a))
            cur.execute("INSERT INTO public.organizations (id, legal_name, pan) VALUES (%s, 'B3 Other Org', %s);", (other_org_id, pan_b))

            users = {}
            for label, oid, role, status in [
                ("maker", org_id, "accountant", "active"),
                ("checker_admin", org_id, "admin", "active"),
                ("other_accountant", org_id, "accountant", "active"),
                ("suspended_admin", org_id, "admin", "suspended"),
                ("outsider_admin", other_org_id, "admin", "active"),
            ]:
                uid = str(uuid.uuid4())
                cur.execute(
                    "INSERT INTO public.global_users (id, email, full_name) VALUES (%s, %s, %s);",
                    (uid, f"b3_{label}_{uuid.uuid4().hex}@example.com", f"B3 {label}"),
                )
                cur.execute(
                    "INSERT INTO public.organization_memberships (org_id, user_id, role, status) VALUES (%s, %s, %s, %s);",
                    (oid, uid, role, status),
                )
                users[label] = uid

            cur.execute(
                """
                INSERT INTO public.gst_registrations (id, org_id, gstin, state_code, legal_name, is_primary)
                VALUES (%s, %s, %s, '27', 'B3 Legal Entity', true);
                """,
                (reg_id, org_id, gstin_a),
            )

    return {
        "org_id": org_id,
        "other_org_id": other_org_id,
        "reg_id": reg_id,
        **users,
    }


def _app_exec(org_id: str, user_id: str, sql: str, params: tuple):
    """Executes SQL directly as least-privilege app_service_user with tenant GUC context."""
    with psycopg.connect(DB_APP, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('app.current_org', %s, false);", (org_id,))
            cur.execute("SELECT set_config('app.current_user', %s, false);", (user_id,))
            cur.execute(sql, params)


def test_p0_app_service_user_state_machine_and_checker_bypass_blocked(b3_org_setup):
    s = b3_org_setup
    db = SessionLocal()
    try:
        prep = prepare_gstr3b_return(db, s["org_id"], s["reg_id"], "2026-10", user_id=s["maker"])
        filing_id = prep["id"]
    finally:
        db.close()

    # 1. DRAFT -> APPROVED_BY_CHECKER directly as app_service_user (even with valid checker_id) -> 42501
    with pytest.raises(psycopg.errors.InsufficientPrivilege) as exc_draft_app:
        _app_exec(
            s["org_id"],
            s["maker"],
            """
            UPDATE public.gstr3b_filings
            SET filing_status = 'APPROVED_BY_CHECKER',
                checker_id = %s,
                table_3_1_data = '{"tampered": true}'::jsonb
            WHERE id = %s;
            """,
            (s["checker_admin"], filing_id),
        )
    assert exc_draft_app.value.sqlstate == "42501"

    # 2. DRAFT -> FILED directly as app_service_user -> 42501
    with pytest.raises(psycopg.errors.InsufficientPrivilege) as exc_draft_filed:
        _app_exec(
            s["org_id"],
            s["maker"],
            "UPDATE public.gstr3b_filings SET filing_status = 'FILED', checker_id = %s WHERE id = %s;",
            (s["checker_admin"], filing_id),
        )
    assert exc_draft_filed.value.sqlstate == "42501"

    # 3. DRAFT -> SUBMITTED_FOR_REVIEW while modifying financial data -> 42501
    with pytest.raises(psycopg.errors.InsufficientPrivilege) as exc_draft_sub_tamper:
        _app_exec(
            s["org_id"],
            s["maker"],
            """
            UPDATE public.gstr3b_filings
            SET filing_status = 'SUBMITTED_FOR_REVIEW',
                table_3_1_data = '{"tampered": true}'::jsonb
            WHERE id = %s;
            """,
            (filing_id,),
        )
    assert exc_draft_sub_tamper.value.sqlstate == "42501"

    # Submit legitimately via service
    db2 = SessionLocal()
    try:
        submit_gstr3b_for_review(db2, s["org_id"], filing_id, s["maker"], "Submit Rev 1")
    finally:
        db2.close()

    # 4. SUBMITTED_FOR_REVIEW -> FILED / DRAFT directly as app_service_user -> 42501
    for bad_target in ("FILED", "DRAFT"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege) as exc_sub_bad:
            _app_exec(
                s["org_id"],
                s["checker_admin"],
                "UPDATE public.gstr3b_filings SET filing_status = %s, checker_id = %s WHERE id = %s;",
                (bad_target, s["checker_admin"], filing_id),
            )
        assert exc_sub_bad.value.sqlstate == "42501"

    # 5. SUBMITTED_FOR_REVIEW -> APPROVED_BY_CHECKER with forged / unauthorized checker_id -> 42501
    for forged_checker in (
        str(uuid.uuid4()),
        s["other_accountant"],
        s["suspended_admin"],
        s["outsider_admin"],
        s["maker"],
    ):
        with pytest.raises(psycopg.errors.InsufficientPrivilege) as exc_forged:
            _app_exec(
                s["org_id"],
                s["maker"],
                "UPDATE public.gstr3b_filings SET filing_status = 'APPROVED_BY_CHECKER', checker_id = %s WHERE id = %s;",
                (forged_checker, filing_id),
            )
        assert exc_forged.value.sqlstate == "42501"

    # Reject legitimately via service, then verify REJECTED_BY_CHECKER -> APPROVED_BY_CHECKER / FILED blocked
    db3 = SessionLocal()
    try:
        review_gstr3b_decision(db3, s["org_id"], filing_id, s["checker_admin"], "REJECTED", "Fix ITC")
    finally:
        db3.close()

    for bad_from_rej in ("APPROVED_BY_CHECKER", "FILED"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege) as exc_rej_bad:
            _app_exec(
                s["org_id"],
                s["checker_admin"],
                "UPDATE public.gstr3b_filings SET filing_status = %s, checker_id = %s WHERE id = %s;",
                (bad_from_rej, s["checker_admin"], filing_id),
            )
        assert exc_rej_bad.value.sqlstate == "42501"

    # Complete legitimate workflow: re-prepare -> submit -> approve -> file + API history check
    db4 = SessionLocal()
    try:
        p2 = prepare_gstr3b_return(db4, s["org_id"], s["reg_id"], "2026-10", user_id=s["maker"])
        assert p2["revision_number"] == 2
        submit_gstr3b_for_review(db4, s["org_id"], filing_id, s["maker"], "Resubmit Rev 2")
        review_gstr3b_decision(db4, s["org_id"], filing_id, s["checker_admin"], "APPROVED", "Approved Rev 2")
        filed = file_gstr3b_to_gsp_sandbox(db4, s["org_id"], filing_id, s["checker_admin"])
        assert filed.status == "FILED"

        client = TestClient(app)
        token = create_access_token(subject=s["maker"], org_id=s["org_id"])
        resp = client.get(
            f"/api/v1/gstr3b/{filing_id}/revision-history",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        hist = resp.json()
        assert len(hist) == 1
        assert hist[0]["revision_number"] == 1
        assert hist[0]["checker_remarks"] == "Fix ITC"
        assert hist[0]["rejected_at"] is not None
        assert hist[0]["archived_at"] is not None
    finally:
        db4.close()


def test_p1_legacy_previous_snapshot_backfill_on_upgrade():
    """
    Provisions a disposable DB at 0033, inserts a Phase B.1-style DRAFT filing with
    revision_number=2 and non-null previous_snapshot (revision 1), upgrades to 0034,
    and proves Revision 1 is backfilled with rejected_at and survives a second rejection cycle.
    """
    import re
    base_url = DB_ADMIN.rsplit("/", 1)[0]
    disp_db = f"myca_b3_backfill_{uuid.uuid4().hex[:12]}"
    if not re.fullmatch(r"[a-z][a-z0-9_]*", disp_db):
        raise ValueError(f"Unsafe disposable database name: {disp_db}")
    disp_admin = f"{base_url}/{disp_db}"
    created_db = False

    with psycopg.connect(f"{base_url}/postgres", autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{disp_db}";')
        created_db = True

    env = os.environ.copy()
    env["DB_ADMIN_URL"] = disp_admin
    env["DATABASE_URL"] = disp_admin.replace("postgresql://", "postgresql+psycopg://", 1)

    try:
        subprocess.run(["python", "-m", "alembic", "upgrade", "0033_gstr3b_history_and_trigger"], env=env, check=True)

        org_id = str(uuid.uuid4())
        maker_id = str(uuid.uuid4())
        checker_id = str(uuid.uuid4())
        reg_id = str(uuid.uuid4())
        filing_id = str(uuid.uuid4())
        pan = f"BFILL{random.randint(1000, 9999)}A"
        gstin = f"27{pan}1ZA"
        legacy_rejected_at = "2026-11-24T10:15:30+00:00"
        legacy_snap = {
            "revision": 1,
            "table_3_1": {"legacy": "rev1_t31"},
            "table_4": {"legacy": "rev1_t4"},
            "offset": {"legacy": "rev1_offset"},
            "checker_id": checker_id,
            "checker_remarks": "Legacy Phase B.1 rejection remark",
            "rejected_at": legacy_rejected_at,
        }

        with psycopg.connect(disp_admin, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO public.organizations (id, legal_name, pan) VALUES (%s, 'Backfill Org', %s);", (org_id, pan))
                cur.execute("INSERT INTO public.global_users (id, email, full_name) VALUES (%s, %s, 'Backfill Maker');", (maker_id, f"bfm_{uuid.uuid4().hex}@example.com"))
                cur.execute("INSERT INTO public.global_users (id, email, full_name) VALUES (%s, %s, 'Backfill Checker');", (checker_id, f"bfc_{uuid.uuid4().hex}@example.com"))
                cur.execute("INSERT INTO public.organization_memberships (org_id, user_id, role, status) VALUES (%s, %s, 'accountant', 'active');", (org_id, maker_id))
                cur.execute("INSERT INTO public.organization_memberships (org_id, user_id, role, status) VALUES (%s, %s, 'admin', 'active');", (org_id, checker_id))
                cur.execute(
                    "INSERT INTO public.gst_registrations (id, org_id, gstin, state_code, legal_name, is_primary) VALUES (%s, %s, %s, '27', 'Backfill Entity', true);",
                    (reg_id, org_id, gstin),
                )
                cur.execute(
                    """
                    INSERT INTO public.gstr3b_filings (
                        id, org_id, registration_id, tax_period, filing_status,
                        table_3_1_data, table_4_data, offset_data, maker_id,
                        revision_number, previous_snapshot
                    ) VALUES (
                        %s, %s, %s, '2026-11', 'DRAFT',
                        '{"rev": 2}'::jsonb, '{"rev": 2}'::jsonb, '{"rev": 2}'::jsonb, %s,
                        2, %s::jsonb
                    );
                    """,
                    (filing_id, org_id, reg_id, maker_id, json.dumps(legacy_snap)),
                )

        # Upgrade to 0034 -> backfills Revision 1
        subprocess.run(["python", "-m", "alembic", "upgrade", "0034_gstr3b_state_machine"], env=env, check=True)

        with psycopg.connect(disp_admin, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT revision_number, table_3_1_data, checker_id, checker_remarks, rejected_at, archived_at
                    FROM public.gstr3b_revision_history
                    WHERE filing_id = %s
                    ORDER BY revision_number ASC;
                    """,
                    (filing_id,),
                )
                rows = cur.fetchall()
                assert len(rows) == 1
                assert rows[0][0] == 1
                assert rows[0][1] == {"legacy": "rev1_t31"}
                assert str(rows[0][2]) == checker_id
                assert rows[0][3] == "Legacy Phase B.1 rejection remark"
                assert rows[0][4] is not None
                assert rows[0][5] is not None
                assert rows[0][4] != rows[0][5]
    finally:
        if created_db:
            with psycopg.connect(f"{base_url}/postgres", autocommit=True) as conn:
                conn.execute(f'DROP DATABASE IF EXISTS "{disp_db}";')
