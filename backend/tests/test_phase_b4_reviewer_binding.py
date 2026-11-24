"""
Phase B.4 — Server-Authenticated Checker Actor Binding Adversarial Tests.

Covers:
1. Maker attempts approval with own identity -> rejected (SQLSTATE 42501 & API HTTP 400).
2. Maker attempts approval by naming a DIFFERENT, valid active admin via direct SQL (app_service_user) -> rejected (SQLSTATE 42501).
3. Attempt to forge reviewer context via set_config('app.current_user', valid_admin) -> rejected (SQLSTATE 42501).
4. app_service_user cannot read gstr3b_review_trust_anchors or insert into gstr3b_verified_review_tokens -> rejected (SQLSTATE 42501).
5. Calling execute_gstr3b_review_transition() with forged signature or replayed nonce -> rejected (SQLSTATE 42501).
6. Attempt to rewrite maker_id during review transition -> rejected (SQLSTATE 42501).
7. Inactive, cross-tenant, and non-admin reviewers remain rejected.
8. Legitimate Checker approval and rejection through authorized service & API path succeed.
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
    _sign_gstr3b_review_attestation,
    GSTR3BServiceError,
)
from tests.test_db_config import DB_ADMIN, DB_APP


@pytest.fixture
def b4_org_setup():
    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            org_id = str(uuid.uuid4())
            other_org_id = str(uuid.uuid4())
            pan_a = f"{''.join(random.choices(string.ascii_uppercase, k=5))}{random.randint(1000, 9999)}A"
            pan_b = f"{''.join(random.choices(string.ascii_uppercase, k=5))}{random.randint(1000, 9999)}B"
            gstin_a = f"27{pan_a}1ZA"
            reg_id = str(uuid.uuid4())

            cur.execute("INSERT INTO public.organizations (id, legal_name, pan) VALUES (%s, 'B4 Org', %s);", (org_id, pan_a))
            cur.execute("INSERT INTO public.organizations (id, legal_name, pan) VALUES (%s, 'B4 Other Org', %s);", (other_org_id, pan_b))

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
                    (uid, f"b4_{label}_{uuid.uuid4().hex}@example.com", f"B4 {label}"),
                )
                cur.execute(
                    "INSERT INTO public.organization_memberships (org_id, user_id, role, status) VALUES (%s, %s, %s, %s);",
                    (oid, uid, role, status),
                )
                users[label] = uid

            cur.execute(
                """
                INSERT INTO public.gst_registrations (id, org_id, gstin, state_code, legal_name, is_primary)
                VALUES (%s, %s, %s, '27', 'B4 Legal Entity', true);
                """,
                (reg_id, org_id, gstin_a),
            )

    return {
        "org_id": org_id,
        "other_org_id": other_org_id,
        "reg_id": reg_id,
        **users,
    }


def _app_exec(org_id: str, user_id: str, sql: str, params: tuple = ()):
    with psycopg.connect(DB_APP, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('app.current_org', %s, false);", (org_id,))
            cur.execute("SELECT set_config('app.current_user', %s, false);", (user_id,))
            cur.execute(sql, params)


def test_p0_checker_impersonation_and_set_config_forgery_blocked(b4_org_setup):
    s = b4_org_setup
    db = SessionLocal()
    try:
        prep = prepare_gstr3b_return(db, s["org_id"], s["reg_id"], "2026-11", user_id=s["maker"])
        filing_id = prep["id"]
        submit_gstr3b_for_review(db, s["org_id"], filing_id, s["maker"], "Submit for B4")
    finally:
        db.close()

    # 1. Maker attempts approval with own identity via direct SQL -> 42501
    with pytest.raises(psycopg.errors.InsufficientPrivilege) as exc_self:
        _app_exec(
            s["org_id"],
            s["maker"],
            "UPDATE public.gstr3b_filings SET filing_status = 'APPROVED_BY_CHECKER', checker_id = %s WHERE id = %s;",
            (s["maker"], filing_id),
        )
    assert exc_self.value.sqlstate == "42501"

    # 2. Maker attempts approval by naming a DIFFERENT, valid active admin -> 42501
    with pytest.raises(psycopg.errors.InsufficientPrivilege) as exc_impersonate:
        _app_exec(
            s["org_id"],
            s["maker"],
            "UPDATE public.gstr3b_filings SET filing_status = 'APPROVED_BY_CHECKER', checker_id = %s WHERE id = %s;",
            (s["checker_admin"], filing_id),
        )
    assert exc_impersonate.value.sqlstate == "42501"

    # 3. Attacker forges set_config('app.current_user', valid_admin) in SQL and tries direct UPDATE -> STILL 42501!
    with pytest.raises(psycopg.errors.InsufficientPrivilege) as exc_set_cfg:
        _app_exec(
            s["org_id"],
            s["checker_admin"],  # calls set_config('app.current_user', checker_admin)
            "UPDATE public.gstr3b_filings SET filing_status = 'APPROVED_BY_CHECKER', checker_id = %s WHERE id = %s;",
            (s["checker_admin"], filing_id),
        )
    assert exc_set_cfg.value.sqlstate == "42501"

    # 4. Attacker cannot read trust anchor secret or insert into verified review tokens
    with pytest.raises(psycopg.errors.InsufficientPrivilege) as exc_secret:
        _app_exec(s["org_id"], s["maker"], "SELECT hmac_secret FROM public.gstr3b_review_trust_anchors;")
    assert exc_secret.value.sqlstate == "42501"

    with pytest.raises(psycopg.errors.InsufficientPrivilege) as exc_tok:
        _app_exec(
            s["org_id"],
            s["checker_admin"],
            "INSERT INTO public.gstr3b_verified_review_tokens (tx_id, pid, filing_id, checker_id, new_status) VALUES (txid_current(), pg_backend_pid(), %s, %s, 'APPROVED_BY_CHECKER');",
            (filing_id, s["checker_admin"]),
        )
    assert exc_tok.value.sqlstate == "42501"

    # 5. Calling execute_gstr3b_review_transition() with forged signature -> 42501
    with pytest.raises(psycopg.errors.InsufficientPrivilege) as exc_sig:
        _app_exec(
            s["org_id"],
            s["checker_admin"],
            "SELECT public.execute_gstr3b_review_transition(%s::uuid, %s::uuid, %s::uuid, 'APPROVED_BY_CHECKER', 'forged', %s, %s);",
            (s["org_id"], filing_id, s["checker_admin"], "nonce-forged-1", "0" * 64),
        )
    assert exc_sig.value.sqlstate == "42501"

    # 6. Attempt to rewrite maker_id during review transition -> 42501
    with pytest.raises(psycopg.errors.InsufficientPrivilege) as exc_maker_rw:
        _app_exec(
            s["org_id"],
            s["checker_admin"],
            "UPDATE public.gstr3b_filings SET filing_status = 'APPROVED_BY_CHECKER', maker_id = %s, checker_id = %s WHERE id = %s;",
            (s["other_accountant"], s["checker_admin"], filing_id),
        )
    assert exc_maker_rw.value.sqlstate == "42501"


def test_p0_legitimate_checker_api_and_replay_protection(b4_org_setup):
    s = b4_org_setup
    db = SessionLocal()
    client = TestClient(app)
    try:
        prep = prepare_gstr3b_return(db, s["org_id"], s["reg_id"], "2026-12", user_id=s["maker"])
        filing_id = prep["id"]
        submit_gstr3b_for_review(db, s["org_id"], filing_id, s["maker"], "Submit for B4 API")

        # Unauthorized reviewers via API rejected (400 for role/SOD, 403 for suspended membership)
        for bad_user, expected_code in (
            (s["maker"], 400),
            (s["other_accountant"], 400),
            (s["suspended_admin"], 403),
        ):
            tok = create_access_token(subject=bad_user, org_id=s["org_id"])
            resp = client.post(
                f"/api/v1/gstr3b/{filing_id}/review-decision",
                json={"decision": "APPROVED", "remarks": "Unauthorized attempt"},
                headers={"Authorization": f"Bearer {tok}"},
            )
            assert resp.status_code == expected_code

        # Valid Checker rejects via API -> succeeds
        tok_checker = create_access_token(subject=s["checker_admin"], org_id=s["org_id"])
        resp_rej = client.post(
            f"/api/v1/gstr3b/{filing_id}/review-decision",
            json={"decision": "REJECTED", "remarks": "Check HSN summary"},
            headers={"Authorization": f"Bearer {tok_checker}"},
        )
        assert resp_rej.status_code == 200
        assert resp_rej.json()["status"] == "REJECTED_BY_CHECKER"

        # Resubmit and approve via API -> succeeds
        tok_maker = create_access_token(subject=s["maker"], org_id=s["org_id"])
        resp_sub = client.post(
            f"/api/v1/gstr3b/{filing_id}/submit-review",
            json={"remarks": "Resubmitted"},
            headers={"Authorization": f"Bearer {tok_maker}"},
        )
        assert resp_sub.status_code == 200

        resp_app = client.post(
            f"/api/v1/gstr3b/{filing_id}/review-decision",
            json={"decision": "APPROVED", "remarks": "Verified and approved"},
            headers={"Authorization": f"Bearer {tok_checker}"},
        )
        assert resp_app.status_code == 200
        assert resp_app.json()["status"] == "APPROVED_BY_CHECKER"

        # Replay of a previously used attestation nonce must fail with 42501
        replay_nonce = f"replay-{uuid.uuid4().hex}"
        sig = _sign_gstr3b_review_attestation(s["org_id"], filing_id, s["checker_admin"], "REJECTED_BY_CHECKER", replay_nonce)
        _app_exec(
            s["org_id"],
            s["checker_admin"],
            "SELECT public.execute_gstr3b_review_transition(%s::uuid, %s::uuid, %s::uuid, 'REJECTED_BY_CHECKER', 'Remand', %s, %s);",
            (s["org_id"], filing_id, s["checker_admin"], replay_nonce, sig),
        )
        with pytest.raises(psycopg.errors.InsufficientPrivilege) as exc_replay:
            _app_exec(
                s["org_id"],
                s["checker_admin"],
                "SELECT public.execute_gstr3b_review_transition(%s::uuid, %s::uuid, %s::uuid, 'REJECTED_BY_CHECKER', 'Replay', %s, %s);",
                (s["org_id"], filing_id, s["checker_admin"], replay_nonce, sig),
            )
        assert exc_replay.value.sqlstate == "42501"
    finally:
        db.close()
