"""
Phase B.2 — GSTR-3B Trigger Bypass Closure and Durable Append-Only Revision History Tests.

Covers:
1. P0 - Adversarial Database Trigger Bypass Tests:
   - SUBMITTED_FOR_REVIEW -> APPROVED_BY_CHECKER while modifying table_3_1_data -> SQLSTATE 42501.
   - SUBMITTED_FOR_REVIEW -> REJECTED_BY_CHECKER while modifying table_4_data -> SQLSTATE 42501.
   - APPROVED_BY_CHECKER -> REJECTED_BY_CHECKER while modifying offset_data -> SQLSTATE 42501.
   - Simultaneous modification of multiple financial fields and filing_status -> SQLSTATE 42501.
   - Legitimate service-layer approval and rejection continue to succeed.
2. P1 - Durable Append-Only Revision History:
   - Three consecutive rejection/re-preparation cycles preserve Revisions 1, 2, and 3 in public.gstr3b_revision_history.
   - All fields (filing_id, org_id, revision_number, snapshots, maker_id, checker_id, checker_remarks, archived_at) preserved unchanged.
   - Direct SQL UPDATE and DELETE on public.gstr3b_revision_history fail with SQLSTATE 42501.
   - Tenant isolation (RLS) prevents cross-org reads of revision history.
"""

import random
import string
import uuid
import psycopg
import pytest
from sqlalchemy import text

from app.database import SessionLocal, set_tenant_context
from app.modules.gstr3b.service import (
    prepare_gstr3b_return,
    submit_gstr3b_for_review,
    review_gstr3b_decision,
    get_gstr3b_revision_history,
    get_gstr3b_status,
)
from tests.test_db_config import DB_ADMIN, DB_APP


@pytest.fixture
def b2_org_setup():
    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            org_id = str(uuid.uuid4())
            other_org_id = str(uuid.uuid4())
            pan_a = f"{''.join(random.choices(string.ascii_uppercase, k=5))}{random.randint(1000, 9999)}A"
            pan_b = f"{''.join(random.choices(string.ascii_uppercase, k=5))}{random.randint(1000, 9999)}B"
            gstin_a = f"27{pan_a}1ZA"
            reg_id = str(uuid.uuid4())

            cur.execute("INSERT INTO public.organizations (id, legal_name, pan) VALUES (%s, 'B2 Org', %s);", (org_id, pan_a))
            cur.execute("INSERT INTO public.organizations (id, legal_name, pan) VALUES (%s, 'B2 Other Org', %s);", (other_org_id, pan_b))

            maker_id = str(uuid.uuid4())
            checker_id = str(uuid.uuid4())
            cur.execute("INSERT INTO public.global_users (id, email, full_name) VALUES (%s, %s, 'B2 Maker');", (maker_id, f"b2_maker_{uuid.uuid4().hex}@example.com"))
            cur.execute("INSERT INTO public.global_users (id, email, full_name) VALUES (%s, %s, 'B2 Checker');", (checker_id, f"b2_checker_{uuid.uuid4().hex}@example.com"))
            cur.execute("INSERT INTO public.organization_memberships (org_id, user_id, role, status) VALUES (%s, %s, 'accountant', 'active');", (org_id, maker_id))
            cur.execute("INSERT INTO public.organization_memberships (org_id, user_id, role, status) VALUES (%s, %s, 'admin', 'active');", (org_id, checker_id))
            cur.execute(
                """
                INSERT INTO public.gst_registrations (id, org_id, gstin, state_code, legal_name, is_primary)
                VALUES (%s, %s, %s, '27', 'B2 Legal Entity', true);
                """,
                (reg_id, org_id, gstin_a),
            )

    return {
        "org_id": org_id,
        "other_org_id": other_org_id,
        "reg_id": reg_id,
        "maker_id": maker_id,
        "checker_id": checker_id,
    }


def test_p0_trigger_blocks_transition_financial_mutation_bypasses(b2_org_setup):
    s = b2_org_setup
    db = SessionLocal()
    try:
        prep = prepare_gstr3b_return(db, s["org_id"], s["reg_id"], "2026-08", user_id=s["maker_id"])
        filing_id = prep["id"]
        submit_gstr3b_for_review(db, s["org_id"], filing_id, s["maker_id"])
    finally:
        db.close()

    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            # 1. SUBMITTED_FOR_REVIEW -> APPROVED_BY_CHECKER while modifying table_3_1_data
            with pytest.raises(psycopg.errors.InsufficientPrivilege) as exc1:
                cur.execute(
                    """
                    UPDATE public.gstr3b_filings
                    SET filing_status = 'APPROVED_BY_CHECKER',
                        checker_id = %s,
                        table_3_1_data = '{"tampered": 1}'::jsonb
                    WHERE id = %s;
                    """,
                    (s["checker_id"], filing_id),
                )
            assert exc1.value.sqlstate == "42501"

            # 2. SUBMITTED_FOR_REVIEW -> REJECTED_BY_CHECKER while modifying table_4_data
            with pytest.raises(psycopg.errors.InsufficientPrivilege) as exc2:
                cur.execute(
                    """
                    UPDATE public.gstr3b_filings
                    SET filing_status = 'REJECTED_BY_CHECKER',
                        checker_id = %s,
                        table_4_data = '{"tampered": 2}'::jsonb
                    WHERE id = %s;
                    """,
                    (s["checker_id"], filing_id),
                )
            assert exc2.value.sqlstate == "42501"

            # 3. Simultaneous multi-field financial mutation + status transition
            with pytest.raises(psycopg.errors.InsufficientPrivilege) as exc3:
                cur.execute(
                    """
                    UPDATE public.gstr3b_filings
                    SET filing_status = 'APPROVED_BY_CHECKER',
                        checker_id = %s,
                        table_3_1_data = '{"tampered": 3}'::jsonb,
                        table_4_data = '{"tampered": 3}'::jsonb,
                        offset_data = '{"tampered": 3}'::jsonb
                    WHERE id = %s;
                    """,
                    (s["checker_id"], filing_id),
                )
            assert exc3.value.sqlstate == "42501"

    # Legitimate service-layer approval must still succeed
    db2 = SessionLocal()
    try:
        app_res = review_gstr3b_decision(db2, s["org_id"], filing_id, s["checker_id"], "APPROVED", "Legitimately approved")
        assert app_res["status"] == "APPROVED_BY_CHECKER"
    finally:
        db2.close()

    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            # 4. APPROVED_BY_CHECKER -> REJECTED_BY_CHECKER while modifying offset_data
            with pytest.raises(psycopg.errors.InsufficientPrivilege) as exc4:
                cur.execute(
                    """
                    UPDATE public.gstr3b_filings
                    SET filing_status = 'REJECTED_BY_CHECKER',
                        offset_data = '{"tampered": 4}'::jsonb
                    WHERE id = %s;
                    """,
                    (filing_id,),
                )
            assert exc4.value.sqlstate == "42501"


def test_p1_durable_append_only_revision_history_across_three_revisions(b2_org_setup):
    s = b2_org_setup
    db = SessionLocal()
    try:
        # Revision 1 -> Submit -> Reject #1
        p1 = prepare_gstr3b_return(db, s["org_id"], s["reg_id"], "2026-09", user_id=s["maker_id"])
        filing_id = p1["id"]
        assert p1["revision_number"] == 1
        submit_gstr3b_for_review(db, s["org_id"], filing_id, s["maker_id"], "Submit Rev 1")
        review_gstr3b_decision(db, s["org_id"], filing_id, s["checker_id"], "REJECTED", "Reject reason 1")

        # Revision 2 -> Submit -> Reject #2
        p2 = prepare_gstr3b_return(db, s["org_id"], s["reg_id"], "2026-09", user_id=s["maker_id"])
        assert p2["revision_number"] == 2
        submit_gstr3b_for_review(db, s["org_id"], filing_id, s["maker_id"], "Submit Rev 2")
        review_gstr3b_decision(db, s["org_id"], filing_id, s["checker_id"], "REJECTED", "Reject reason 2")

        # Revision 3 -> Submit -> Reject #3
        p3 = prepare_gstr3b_return(db, s["org_id"], s["reg_id"], "2026-09", user_id=s["maker_id"])
        assert p3["revision_number"] == 3
        submit_gstr3b_for_review(db, s["org_id"], filing_id, s["maker_id"], "Submit Rev 3")
        review_gstr3b_decision(db, s["org_id"], filing_id, s["checker_id"], "REJECTED", "Reject reason 3")

        # Revision 4 prepared
        p4 = prepare_gstr3b_return(db, s["org_id"], s["reg_id"], "2026-09", user_id=s["maker_id"])
        assert p4["revision_number"] == 4

        # Verify ALL 3 prior revisions (1, 2, 3) are preserved in gstr3b_revision_history
        history = get_gstr3b_revision_history(db, s["org_id"], filing_id)
        assert len(history) == 3
        assert [h["revision_number"] for h in history] == [1, 2, 3]
        assert [h["checker_remarks"] for h in history] == [
            "Reject reason 1",
            "Reject reason 2",
            "Reject reason 3",
        ]
        assert [h["maker_remarks"] for h in history] == [
            "Submit Rev 1",
            "Submit Rev 2",
            "Submit Rev 3",
        ]
        for h in history:
            assert h["filing_id"] == filing_id
            assert h["org_id"] == s["org_id"]
            assert h["maker_id"] == s["maker_id"]
            assert h["checker_id"] == s["checker_id"]
            assert h["archived_at"] is not None
            assert h["table_3_1"] is not None
            assert h["table_4"] is not None
            assert h["offset"] is not None

        # Cross-tenant isolation: other_org_id sees 0 revision history rows
        db_other = SessionLocal()
        try:
            other_history = get_gstr3b_revision_history(db_other, s["other_org_id"], filing_id)
            assert other_history == []
        finally:
            db_other.close()
    finally:
        db.close()

    # Append-only DB trigger blocks UPDATE and DELETE on gstr3b_revision_history
    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM public.gstr3b_revision_history WHERE filing_id = %s AND revision_number = 1;", (filing_id,))
            hist_id = cur.fetchone()[0]

            with pytest.raises(psycopg.errors.InsufficientPrivilege) as exc_upd:
                cur.execute(
                    "UPDATE public.gstr3b_revision_history SET checker_remarks = 'Tampered' WHERE id = %s;",
                    (hist_id,),
                )
            assert exc_upd.value.sqlstate == "42501"

            with pytest.raises(psycopg.errors.InsufficientPrivilege) as exc_del:
                cur.execute("DELETE FROM public.gstr3b_revision_history WHERE id = %s;", (hist_id,))
            assert exc_del.value.sqlstate == "42501"
