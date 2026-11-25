"""
Phase B.9 Verification Tests:
1. Single-revision migration 0039 <-> 0038 downgrade/upgrade reversibility & provenance preservation
2. Three-state Maker provenance in get_gstr3b_status() & schema default
3. Database claim_legacy_null_maker() row-level locking & precondition enforcement
4. Production/staging missing GSTR3B_REVIEW_ATTESTATION_KEY fail-fast & trust anchor sync check
5. Explicit RLS & least-privilege verification for public.gstr3b_legacy_maker_claims
"""

import hashlib
import hmac as _hmac
import os
import re
import subprocess
import threading
import time
import uuid
import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from app.config import Settings, settings
from app.modules.gstr3b.schemas import GSTR3BStatusResponse
from app.modules.gstr3b.service import (
    _compute_gstr3b_payload_digest,
    _sign_gstr3b_legacy_maker_claim,
    get_gstr3b_status,
    prepare_gstr3b_return,
    verify_gstr3b_trust_anchor_sync,
)
from tests.test_db_config import DB_ADMIN, DB_APP


_admin_engine = create_engine(DB_ADMIN, pool_pre_ping=True)
_AdminSession = sessionmaker(bind=_admin_engine, autocommit=False, autoflush=False)

_app_engine = create_engine(DB_APP, pool_pre_ping=True)
_AppSession = sessionmaker(bind=_app_engine, autocommit=False, autoflush=False)


def _set_app_context(session, org_id: str, user_id: str, role: str = "ACCOUNTANT"):
    session.execute(
        text(
            """
            SELECT
                set_config('app.current_org', :org_id, true),
                set_config('app.current_user_id', :user_id, true),
                set_config('app.current_role', :role, true)
            """
        ),
        {"org_id": str(org_id), "user_id": str(user_id), "role": role},
    )


def _sqlstate(exc_info) -> str:
    err = exc_info.value
    orig = getattr(err, "orig", err)
    return getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None) or ""


@pytest.fixture
def b9_two_orgs():
    orgs = {}
    admin = _AdminSession()
    try:
        for label in ("a", "b"):
            org_id = str(uuid.uuid4())
            reg_id = str(uuid.uuid4())
            maker_id = str(uuid.uuid4())
            pan = f"AABCB{int(uuid.uuid4().int % 9000) + 1000}Z"
            gstin = f"27{pan}1Z5"
            admin.execute(
                text("INSERT INTO public.organizations (id, legal_name, pan) VALUES (:id, :ln, :pan)"),
                {"id": org_id, "ln": f"B9 Org {label.upper()}", "pan": pan},
            )
            admin.execute(
                text(
                    """
                    INSERT INTO public.gst_registrations (
                        id, org_id, gstin, state_code, legal_name, registration_type, is_primary
                    ) VALUES (:rid, :oid, :gstin, '27', 'B9 Legal', 'REGULAR', true)
                    """
                ),
                {"rid": reg_id, "oid": org_id, "gstin": gstin},
            )
            admin.execute(
                text(
                    """
                    INSERT INTO public.global_users (id, email, full_name, is_active)
                    VALUES (:uid, :email, 'B9 Maker', true)
                    """
                ),
                {"uid": maker_id, "email": f"maker-{maker_id[:8]}@b9.test"},
            )
            admin.execute(
                text(
                    """
                    INSERT INTO public.organization_memberships (id, org_id, user_id, role, status)
                    VALUES (:mid, :oid, :uid, 'accountant', 'active')
                    """
                ),
                {"mid": str(uuid.uuid4()), "oid": org_id, "uid": maker_id},
            )
            orgs[label] = {"org_id": org_id, "reg_id": reg_id, "maker_id": maker_id}
        admin.commit()
    finally:
        admin.close()

    yield orgs

    cleanup = _AdminSession()
    try:
        cleanup.execute(text("SET LOCAL session_replication_role = 'replica'"))
        for label in ("a", "b"):
            oid = orgs[label]["org_id"]
            uid = orgs[label]["maker_id"]
            cleanup.execute(text("DELETE FROM public.gstr3b_legacy_maker_claims WHERE org_id = :oid"), {"oid": oid})
            cleanup.execute(text("DELETE FROM public.gstr3b_revision_history WHERE org_id = :oid"), {"oid": oid})
            cleanup.execute(text("DELETE FROM public.gstr3b_filings WHERE org_id = :oid"), {"oid": oid})
            cleanup.execute(text("DELETE FROM public.gst_registrations WHERE org_id = :oid"), {"oid": oid})
            cleanup.execute(text("DELETE FROM public.organization_memberships WHERE org_id = :oid"), {"oid": oid})
            cleanup.execute(text("DELETE FROM public.global_users WHERE id = :uid"), {"uid": uid})
            cleanup.execute(text("DELETE FROM public.organizations WHERE id = :oid"), {"oid": oid})
        cleanup.commit()
    finally:
        cleanup.close()


def _seed_filing(org_id: str, reg_id: str, tax_period: str, status: str, maker_id: str | None) -> str:
    filing_id = str(uuid.uuid4())
    admin = _AdminSession()
    try:
        admin.execute(text("SET LOCAL session_replication_role = 'replica'"))
        admin.execute(
            text(
                """
                INSERT INTO public.gstr3b_filings (
                    id, org_id, registration_id, tax_period,
                    table_3_1_data, table_4_data, offset_data,
                    filing_status, maker_id, revision_number
                ) VALUES (
                    :id, :oid, :rid, :tp,
                    '{"txval": 1000}'::jsonb, '{"itc_avl": 100}'::jsonb, '{"cash": 900}'::jsonb,
                    :status, :maker_id, 1
                )
                """
            ),
            {"id": filing_id, "oid": org_id, "rid": reg_id, "tp": tax_period, "status": status, "maker_id": maker_id},
        )
        admin.commit()
    finally:
        admin.close()
    return filing_id


def test_three_state_maker_provenance_and_schema_default(b9_two_orgs):
    # Schema default must not falsely claim ESTABLISHED_AT_CREATION
    assert GSTR3BStatusResponse.model_fields["maker_provenance"].default == "UNKNOWN_LEGACY_NULL"

    a = b9_two_orgs["a"]
    _seed_filing(a["org_id"], a["reg_id"], "012025", "DRAFT", None)
    _seed_filing(a["org_id"], a["reg_id"], "022025", "DRAFT", None)

    app = _AppSession()
    try:
        # 1. Unclaimed NULL-maker filing -> UNKNOWN_LEGACY_NULL
        _set_app_context(app, a["org_id"], a["maker_id"])
        unclaimed = get_gstr3b_status(app, a["org_id"], a["reg_id"], "012025")
        assert unclaimed is not None
        assert unclaimed["maker_id"] is None
        assert unclaimed["maker_provenance"] == "UNKNOWN_LEGACY_NULL"
        assert unclaimed["legacy_maker_claim"] is None

        # 2. Claimed legacy filing -> LEGACY_CLAIMED with claimant details
        _set_app_context(app, a["org_id"], a["maker_id"])
        prepare_gstr3b_return(app, a["org_id"], a["reg_id"], "022025", user_id=a["maker_id"])
        app.commit()
        _set_app_context(app, a["org_id"], a["maker_id"])
        claimed = get_gstr3b_status(app, a["org_id"], a["reg_id"], "022025")
        assert claimed is not None
        assert claimed["maker_id"] == a["maker_id"]
        assert claimed["maker_provenance"] == "LEGACY_CLAIMED"
        assert claimed["legacy_maker_claim"]["original_maker_state"] == "UNKNOWN_LEGACY_NULL"
        assert claimed["legacy_maker_claim"]["claimed_by_user_id"] == a["maker_id"]
        assert claimed["legacy_maker_claim"]["claimed_at"] is not None

        # 3. Newly created filing -> ESTABLISHED_AT_CREATION
        _set_app_context(app, a["org_id"], a["maker_id"])
        prepare_gstr3b_return(app, a["org_id"], a["reg_id"], "032025", user_id=a["maker_id"])
        app.commit()
        _set_app_context(app, a["org_id"], a["maker_id"])
        established = get_gstr3b_status(app, a["org_id"], a["reg_id"], "032025")
        assert established is not None
        assert established["maker_id"] == a["maker_id"]
        assert established["maker_provenance"] == "ESTABLISHED_AT_CREATION"
        assert established["legacy_maker_claim"] is None
    finally:
        app.close()


def test_claim_legacy_null_maker_preconditions_and_atomicity(b9_two_orgs):
    a = b9_two_orgs["a"]
    b = b9_two_orgs["b"]

    established_id = _seed_filing(a["org_id"], a["reg_id"], "042025", "DRAFT", a["maker_id"])
    other_org_id_filing = _seed_filing(b["org_id"], b["reg_id"], "042025", "DRAFT", None)
    submitted_id = _seed_filing(a["org_id"], a["reg_id"], "052025", "SUBMITTED_FOR_REVIEW", None)
    valid_null_id = _seed_filing(a["org_id"], a["reg_id"], "062025", "DRAFT", None)
    nonexistent_id = str(uuid.uuid4())

    app = _AppSession()
    try:
        invalid_cases = [
            ("established_maker", a["org_id"], established_id, a["maker_id"]),
            ("nonexistent", a["org_id"], nonexistent_id, a["maker_id"]),
            ("wrong_org", a["org_id"], other_org_id_filing, a["maker_id"]),
            ("unsupported_status", a["org_id"], submitted_id, a["maker_id"]),
        ]
        for label, oid, fid, mid in invalid_cases:
            nonce = f"b9-{label}-{uuid.uuid4().hex[:12]}"
            reason = "LEGACY_SUBMISSION_CLAIM"
            sig = _sign_gstr3b_legacy_maker_claim(oid, fid, mid, reason, nonce)
            _set_app_context(app, oid, mid)
            with pytest.raises(DBAPIError) as exc:
                app.execute(
                    text(
                        """
                        SELECT public.claim_legacy_null_maker(
                            CAST(:oid AS uuid), CAST(:fid AS uuid), CAST(:mid AS uuid),
                            :reason, :nonce, :sig
                        )
                        """
                    ),
                    {"oid": oid, "fid": fid, "mid": mid, "reason": reason, "nonce": nonce, "sig": sig},
                )
            assert _sqlstate(exc) == "42501"
            app.rollback()

            # Verify neither provenance row nor consumed nonce was left behind
            admin = _AdminSession()
            try:
                claim_cnt = admin.execute(
                    text("SELECT count(*) FROM public.gstr3b_legacy_maker_claims WHERE filing_id = :fid"),
                    {"fid": fid},
                ).scalar()
                nonce_cnt = admin.execute(
                    text("SELECT count(*) FROM public.gstr3b_review_attestation_nonces WHERE nonce = :n"),
                    {"n": nonce},
                ).scalar()
                assert claim_cnt == 0, f"{label} left provenance row"
                assert nonce_cnt == 0, f"{label} left consumed nonce"
            finally:
                admin.close()

        # Valid legacy NULL-maker claim succeeds
        ok_nonce = f"b9-valid-{uuid.uuid4().hex[:12]}"
        ok_sig = _sign_gstr3b_legacy_maker_claim(a["org_id"], valid_null_id, a["maker_id"], "LEGACY_SUBMISSION_CLAIM", ok_nonce)
        _set_app_context(app, a["org_id"], a["maker_id"])
        app.execute(
            text(
                """
                SELECT public.claim_legacy_null_maker(
                    CAST(:oid AS uuid), CAST(:fid AS uuid), CAST(:mid AS uuid),
                    'LEGACY_SUBMISSION_CLAIM', :nonce, :sig
                )
                """
            ),
            {"oid": a["org_id"], "fid": valid_null_id, "mid": a["maker_id"], "nonce": ok_nonce, "sig": ok_sig},
        )
        app.commit()

        # Duplicate claim on the same filing fails with 42501 and does not consume new nonce
        dup_nonce = f"b9-dup-{uuid.uuid4().hex[:12]}"
        dup_sig = _sign_gstr3b_legacy_maker_claim(a["org_id"], valid_null_id, a["maker_id"], "LEGACY_SUBMISSION_CLAIM", dup_nonce)
        _set_app_context(app, a["org_id"], a["maker_id"])
        with pytest.raises(DBAPIError) as exc_dup:
            app.execute(
                text(
                    """
                    SELECT public.claim_legacy_null_maker(
                        CAST(:oid AS uuid), CAST(:fid AS uuid), CAST(:mid AS uuid),
                        'LEGACY_SUBMISSION_CLAIM', :nonce, :sig
                    )
                    """
                ),
                {"oid": a["org_id"], "fid": valid_null_id, "mid": a["maker_id"], "nonce": dup_nonce, "sig": dup_sig},
            )
        assert _sqlstate(exc_dup) == "42501"
        app.rollback()

        admin = _AdminSession()
        try:
            assert admin.execute(
                text("SELECT count(*) FROM public.gstr3b_review_attestation_nonces WHERE nonce = :n"),
                {"n": dup_nonce},
            ).scalar() == 0
        finally:
            admin.close()
    finally:
        app.close()


def test_rls_and_least_privilege_on_gstr3b_legacy_maker_claims(b9_two_orgs):
    a = b9_two_orgs["a"]
    b = b9_two_orgs["b"]

    filing_a = _seed_filing(a["org_id"], a["reg_id"], "072025", "DRAFT", None)
    app = _AppSession()
    try:
        _set_app_context(app, a["org_id"], a["maker_id"])
        prepare_gstr3b_return(app, a["org_id"], a["reg_id"], "072025", user_id=a["maker_id"])
        app.commit()

        # Org B cannot read Org A's claim record
        _set_app_context(app, b["org_id"], b["maker_id"])
        rows_b = app.execute(
            text("SELECT id FROM public.gstr3b_legacy_maker_claims WHERE filing_id = :fid"),
            {"fid": filing_a},
        ).fetchall()
        assert rows_b == []

        # Manipulated context (empty/null) sees 0 rows
        app.execute(text("SELECT set_config('app.current_org', '', true)"))
        rows_null = app.execute(
            text("SELECT id FROM public.gstr3b_legacy_maker_claims WHERE filing_id = :fid"),
            {"fid": filing_a},
        ).fetchall()
        assert rows_null == []

        # Direct INSERT/UPDATE/DELETE by app_service_user are denied (42501)
        _set_app_context(app, a["org_id"], a["maker_id"])
        for stmt in (
            "INSERT INTO public.gstr3b_legacy_maker_claims (filing_id, org_id, claimed_by_user_id, claim_reason, attestation_nonce) VALUES (:fid, :oid, :uid, 'x', 'n')",
            "UPDATE public.gstr3b_legacy_maker_claims SET claim_reason = 'x' WHERE filing_id = :fid",
            "DELETE FROM public.gstr3b_legacy_maker_claims WHERE filing_id = :fid",
        ):
            with pytest.raises(DBAPIError) as exc_priv:
                app.execute(text(stmt), {"fid": filing_a, "oid": a["org_id"], "uid": a["maker_id"]})
            assert _sqlstate(exc_priv) == "42501"
            app.rollback()
            _set_app_context(app, a["org_id"], a["maker_id"])
    finally:
        app.close()

    # Verify privileges and RLS policy definitions in pg_catalog
    admin = _AdminSession()
    try:
        privs = admin.execute(
            text(
                """
                SELECT privilege_type
                FROM information_schema.table_privileges
                WHERE table_schema = 'public'
                  AND table_name = 'gstr3b_legacy_maker_claims'
                  AND grantee = 'app_service_user'
                """
            )
        ).scalars().all()
        assert privs == ["SELECT"]

        pol = admin.execute(
            text(
                """
                SELECT qual, with_check
                FROM pg_policies
                WHERE schemaname = 'public'
                  AND tablename = 'gstr3b_legacy_maker_claims'
                  AND policyname = 'gstr3b_legacy_maker_claims_tenant'
                """
            )
        ).mappings().one()
        assert "app.current_org" in pol["qual"]
        assert "app.current_org" in pol["with_check"]
    finally:
        admin.close()


def test_missing_prod_staging_key_fails_fast_and_trust_anchor_sync(monkeypatch):
    strong_secret = "PROD_SECRET_KEY_VALUE_32_CHARS_MINIMUM_OK_1234"
    strong_attest = "PROD_ATTEST_KEY_DISTINCT_32_CHARS_MINIMUM_OK_5678"
    prod_base = dict(
        SECRET_KEY=strong_secret,
        ALLOW_DEMO_TOKENS=False,
        ALLOW_DEV_HEADER_AUTH=False,
        DATABASE_URL="postgresql+psycopg://app:prod_pwd@prod-db.internal:5432/myca",
        DATABASE_WORKER_URL="postgresql+psycopg://worker:prod_pwd@prod-db.internal:5432/myca",
        _env_file=None,
    )

    for env in ("production", "staging"):
        monkeypatch.setenv("ENVIRONMENT", env)
        monkeypatch.delenv("GSTR3B_REVIEW_ATTESTATION_KEY", raising=False)
        # Omitted (None) key must fail fast at Settings instantiation
        with pytest.raises(ValidationError) as exc_missing:
            Settings(ENVIRONMENT=env, **prod_base)
        assert "GSTR3B_REVIEW_ATTESTATION_KEY" in str(exc_missing.value)

        with pytest.raises(ValidationError):
            Settings(ENVIRONMENT=env, GSTR3B_REVIEW_ATTESTATION_KEY="short", **prod_base)
        with pytest.raises(ValidationError):
            Settings(ENVIRONMENT=env, GSTR3B_REVIEW_ATTESTATION_KEY="dev-test-secret-key-at-least-32-characters-long", **prod_base)
        with pytest.raises(ValidationError):
            Settings(ENVIRONMENT=env, GSTR3B_REVIEW_ATTESTATION_KEY=strong_secret, **prod_base)

        ok_cfg = Settings(ENVIRONMENT=env, GSTR3B_REVIEW_ATTESTATION_KEY=strong_attest, **prod_base)
        assert ok_cfg.GSTR3B_REVIEW_ATTESTATION_KEY == strong_attest

    # Trust anchor sync helper verifies match/mismatch without mutating DB trust anchor
    app = _AppSession()
    try:
        assert verify_gstr3b_trust_anchor_sync(app) is True
        monkeypatch.setattr(settings, "GSTR3B_REVIEW_ATTESTATION_KEY", "WRONG_KEY_THAT_DOES_NOT_MATCH_DB_TRUST_ANCHOR_32CH")
        assert verify_gstr3b_trust_anchor_sync(app) is False
    finally:
        app.close()


def _new_disposable_db(prefix: str):
    db_name = f"{prefix}_{uuid.uuid4().hex[:12]}"
    if not re.fullmatch(r"[a-z][a-z0-9_]*", db_name):
        raise ValueError(f"Unsafe disposable database name: {db_name}")
    admin_root_url = make_url(DB_ADMIN).set(database="postgres")
    b9_admin_url = make_url(DB_ADMIN).set(database=db_name)
    b9_app_url = make_url(DB_APP).set(database=db_name)
    b9_admin_str = b9_admin_url.render_as_string(hide_password=False)
    b9_app_str = b9_app_url.render_as_string(hide_password=False)

    admin_root = create_engine(admin_root_url, isolation_level="AUTOCOMMIT")
    with admin_root.connect() as c:
        c.execute(text(f'CREATE DATABASE "{db_name}"'))
    return db_name, admin_root, b9_admin_str, b9_app_str


def test_single_revision_downgrade_0039_to_0038_and_claim_preservation():
    db_name, admin_root, b9_admin_str, b9_app_str = _new_disposable_db("myca_b9_rollback")
    created_db = True

    env = os.environ.copy()
    env["ENVIRONMENT"] = "test"
    env["DATABASE_ADMIN_URL"] = b9_admin_str
    env["DB_ADMIN_URL"] = b9_admin_str
    env["TEST_DATABASE_URL"] = b9_admin_str
    env["DATABASE_URL"] = b9_app_str
    env["ALLOW_DISPOSABLE_DATABASE_ROLLBACK"] = "true"

    backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    e_db = None
    try:
        subprocess.run(["python", "-m", "alembic", "upgrade", "head"], cwd=backend_dir, env=env, check=True)

        # Single-revision downgrade 0039 -> 0038 when no claims exist
        subprocess.run(["python", "-m", "alembic", "downgrade", "0038_gstr3b_attested_creation"], cwd=backend_dir, env=env, check=True)

        e_db = create_engine(b9_admin_str)
        with e_db.connect() as c:
            funcs = c.execute(
                text(
                    """
                    SELECT p.proname, pg_get_function_identity_arguments(p.oid) AS args,
                           p.prosecdef, p.proconfig, pg_get_userbyid(p.proowner) AS owner
                    FROM pg_proc p
                    JOIN pg_namespace n ON n.oid = p.pronamespace
                    WHERE n.nspname = 'public'
                      AND p.proname IN ('claim_legacy_null_maker', 'execute_gstr3b_prepare_upsert')
                    ORDER BY p.proname
                    """
                )
            ).mappings().all()
            assert len(funcs) == 2
            by_name = {f["proname"]: f for f in funcs}
            assert by_name["claim_legacy_null_maker"]["args"] == "p_org_id uuid, p_filing_id uuid, p_maker_id uuid, p_nonce text, p_signature text"
            assert "p_now timestamp with time zone" in by_name["execute_gstr3b_prepare_upsert"]["args"]
            for f in funcs:
                assert f["prosecdef"] is True
                assert f["owner"] == "postgres"
                assert "search_path=public, pg_catalog" in (f["proconfig"] or [])

            # Verify EXECUTE grants: PUBLIC denied, app_service_user granted
            for sig in (
                "public.claim_legacy_null_maker(uuid, uuid, uuid, text, text)",
                "public.execute_gstr3b_prepare_upsert(uuid, uuid, uuid, text, text, text, text, uuid, text, integer, text, timestamp with time zone, text, text, text)",
            ):
                assert c.execute(text(f"SELECT has_function_privilege('public', '{sig}', 'EXECUTE')")).scalar() is False
                assert c.execute(text(f"SELECT has_function_privilege('app_service_user', '{sig}', 'EXECUTE')")).scalar() is True

        # Exercise B.7 (0038) application call contract
        org_id = str(uuid.uuid4())
        reg_id = str(uuid.uuid4())
        maker_id = str(uuid.uuid4())
        filing_id = str(uuid.uuid4())
        with e_db.begin() as c:
            c.execute(text("INSERT INTO public.organizations (id, legal_name, pan) VALUES (:id, 'B7 Contract', 'AABCB1234Z')"), {"id": org_id})
            c.execute(text("INSERT INTO public.gst_registrations (id, org_id, gstin, state_code, legal_name, registration_type, is_primary) VALUES (:rid, :oid, '27AABCB1234Z1Z5', '27', 'B7', 'REGULAR', true)"), {"rid": reg_id, "oid": org_id})
            c.execute(text("INSERT INTO public.global_users (id, email, full_name, is_active) VALUES (:uid, 'b7@test.local', 'B7', true)"), {"uid": maker_id})
            c.execute(text("INSERT INTO public.organization_memberships (id, org_id, user_id, role, status) VALUES (:mid, :oid, :uid, 'accountant', 'active')"), {"mid": str(uuid.uuid4()), "oid": org_id, "uid": maker_id})

        app_b7 = create_engine(b9_app_str)
        raw_key = settings.GSTR3B_REVIEW_ATTESTATION_KEY or f"MYCA_GSTR3B_REVIEW_TRUST_V1:{settings.SECRET_KEY}"
        derived = hashlib.sha256(raw_key.encode("utf-8")).hexdigest().encode("utf-8")
        import hmac as _hmac
        t31, t4, off = '{"txval": 100}', '{"itc": 10}', '{"cash": 90}'
        pdig = _compute_gstr3b_payload_digest(t31, t4, off)
        nonce_b7 = uuid.uuid4().hex
        msg_b7 = f"{org_id}:{filing_id}:{reg_id}:082025:{maker_id}:DRAFT:{pdig}:{nonce_b7}".encode("utf-8")
        sig_b7 = _hmac.new(derived, msg_b7, hashlib.sha256).hexdigest()
        with app_b7.begin() as c:
            c.execute(text("SELECT set_config('app.current_org', :o, true)"), {"o": org_id})
            res = c.execute(
                text(
                    """
                    SELECT * FROM public.execute_gstr3b_prepare_upsert(
                        CAST(:id AS uuid), CAST(:oid AS uuid), CAST(:rid AS uuid),
                        '082025', :t31, :t4, :off, CAST(:mid AS uuid), NULL, 1, NULL,
                        clock_timestamp(), :pdig, :nonce, :sig
                    )
                    """
                ),
                {"id": filing_id, "oid": org_id, "rid": reg_id, "t31": t31, "t4": t4, "off": off, "mid": maker_id, "pdig": pdig, "nonce": nonce_b7, "sig": sig_b7},
            ).fetchone()
            assert str(res[0]) == filing_id
        app_b7.dispose()

        # Verify rollback-window gate technically rejects 5-arg legacy claim (prevents provenance loss)
        rollback_legacy_fid = str(uuid.uuid4())
        with e_db.begin() as c:
            c.execute(text("SET LOCAL session_replication_role = 'replica'"))
            c.execute(
                text(
                    """
                    INSERT INTO public.gstr3b_filings (
                        id, org_id, registration_id, tax_period,
                        table_3_1_data, table_4_data, offset_data,
                        filing_status, maker_id, revision_number
                    ) VALUES (:fid, :oid, :rid, '102025', '{}'::jsonb, '{}'::jsonb, '{}'::jsonb, 'DRAFT', NULL, 1)
                    """
                ),
                {"fid": rollback_legacy_fid, "oid": org_id, "rid": reg_id},
            )
        app_rw = create_engine(b9_app_str)
        rw_nonce = uuid.uuid4().hex
        rw_msg = f"{org_id}:{rollback_legacy_fid}:{maker_id}:LEGACY_MAKER_CLAIM:{rw_nonce}".encode("utf-8")
        rw_sig = _hmac.new(derived, rw_msg, hashlib.sha256).hexdigest()
        with pytest.raises(DBAPIError) as exc_rw:
            with app_rw.begin() as c:
                c.execute(text("SELECT set_config('app.current_org', :o, true)"), {"o": org_id})
                c.execute(
                    text("SELECT public.claim_legacy_null_maker(CAST(:o AS uuid), CAST(:f AS uuid), CAST(:m AS uuid), :n, :s)"),
                    {"o": org_id, "f": rollback_legacy_fid, "m": maker_id, "n": rw_nonce, "s": rw_sig},
                )
        assert _sqlstate(exc_rw) == "42501"
        app_rw.dispose()

        # Upgrade back to B.8/B.9 (0039)
        subprocess.run(["python", "-m", "alembic", "upgrade", "head"], cwd=backend_dir, env=env, check=True)

        # Verify rollback_legacy_fid remained UNKNOWN_LEGACY_NULL (not misreported as ESTABLISHED_AT_CREATION)
        app_check = create_engine(b9_app_str)
        AppCheckSession = sessionmaker(bind=app_check, autocommit=False, autoflush=False)
        s_check = AppCheckSession()
        try:
            _set_app_context(s_check, org_id, maker_id)
            st_before = get_gstr3b_status(s_check, org_id, reg_id, "102025")
            assert st_before is not None
            assert st_before["maker_id"] is None
            assert st_before["maker_provenance"] == "UNKNOWN_LEGACY_NULL"

            _set_app_context(s_check, org_id, maker_id)
            prepare_gstr3b_return(s_check, org_id, reg_id, "102025", user_id=maker_id)
            s_check.commit()
            _set_app_context(s_check, org_id, maker_id)
            st_after = get_gstr3b_status(s_check, org_id, reg_id, "102025")
            assert st_after["maker_provenance"] == "LEGACY_CLAIMED"
            assert st_after["legacy_maker_claim"]["claimed_by_user_id"] == maker_id
            assert st_after["legacy_maker_claim"]["claimed_at"] is not None
        finally:
            s_check.close()
        app_check.dispose()

        # Record a legacy claim and verify downgrade refuses to silently delete audit evidence
        legacy_fid = str(uuid.uuid4())
        with e_db.begin() as c:
            c.execute(text("SET LOCAL session_replication_role = 'replica'"))
            c.execute(
                text(
                    """
                    INSERT INTO public.gstr3b_filings (
                        id, org_id, registration_id, tax_period,
                        table_3_1_data, table_4_data, offset_data,
                        filing_status, maker_id, revision_number
                    ) VALUES (:fid, :oid, :rid, '092025', '{}'::jsonb, '{}'::jsonb, '{}'::jsonb, 'DRAFT', NULL, 1)
                    """
                ),
                {"fid": legacy_fid, "oid": org_id, "rid": reg_id},
            )
        app_b9 = create_engine(b9_app_str)
        c_nonce = uuid.uuid4().hex
        c_sig = _sign_gstr3b_legacy_maker_claim(org_id, legacy_fid, maker_id, "LEGACY_SUBMISSION_CLAIM", c_nonce)
        with app_b9.begin() as c:
            c.execute(text("SELECT set_config('app.current_org', :o, true)"), {"o": org_id})
            c.execute(
                text("SELECT public.claim_legacy_null_maker(CAST(:o AS uuid), CAST(:f AS uuid), CAST(:m AS uuid), 'LEGACY_SUBMISSION_CLAIM', :n, :s)"),
                {"o": org_id, "f": legacy_fid, "m": maker_id, "n": c_nonce, "s": c_sig},
            )
        app_b9.dispose()

        proc_refuse = subprocess.run(
            ["python", "-m", "alembic", "downgrade", "0038_gstr3b_attested_creation"],
            cwd=backend_dir,
            env=env,
            capture_output=True,
            text=True,
        )
        assert proc_refuse.returncode != 0
        assert "Refusing to downgrade migration 0039" in (proc_refuse.stderr + proc_refuse.stdout)
        with e_db.connect() as c:
            assert c.execute(text("SELECT count(*) FROM public.gstr3b_legacy_maker_claims WHERE filing_id = :f"), {"f": legacy_fid}).scalar() == 1
        e_db.dispose()
    finally:
        if e_db is not None:
            e_db.dispose()
        if created_db:
            with admin_root.connect() as c:
                c.execute(text(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)'))
        admin_root.dispose()


class DowngradeTeardownSafetyError(RuntimeError):
    """Raised when downgrade process/backend teardown cannot confirm safe termination."""

    def __init__(self, message: str, outcome: dict):
        super().__init__(message)
        self.outcome = outcome


def _poll_alembic_lock_wait(e_db, db_name: str, alembic_app_name: str, claim_pid: int, t: threading.Thread):
    """Polls pg_stat_activity (AUTOCOMMIT) until Alembic waits on LOCK TABLE blocked by claim_pid."""
    observed_lock_wait = False
    last_rows = []
    deadline = time.monotonic() + 20.0
    with e_db.connect().execution_options(isolation_level="AUTOCOMMIT") as obs_conn:
        while time.monotonic() < deadline:
            rows = obs_conn.execute(
                text(
                    """
                    SELECT pid, application_name, state, wait_event_type, wait_event, query,
                           pg_blocking_pids(pid) AS blockers
                    FROM pg_stat_activity
                    WHERE datname = :db
                      AND pid != pg_backend_pid()
                      AND pid != :claim_pid
                    """
                ),
                {"db": db_name, "claim_pid": claim_pid},
            ).mappings().all()
            last_rows = [dict(r) for r in rows]
            for row in rows:
                if (
                    row["application_name"] == alembic_app_name
                    and row["state"] == "active"
                    and row["wait_event_type"] == "Lock"
                    and claim_pid in (row["blockers"] or [])
                    and "LOCK TABLE public.gstr3b_legacy_maker_claims IN ACCESS EXCLUSIVE MODE" in (row["query"] or "")
                    and "DROP TABLE" not in (row["query"] or "")
                ):
                    observed_lock_wait = True
                    break
            if observed_lock_wait or not t.is_alive():
                break
            time.sleep(0.05)
    return observed_lock_wait, last_rows


def _terminate_alembic_db_backends(
    admin_db,
    db_name: str,
    alembic_app_name: str,
    claim_pid: int | None,
    errors: list[str],
    wait_seconds: float = 5.0,
) -> tuple[bool, bool]:
    """
    Emergency DB-level safeguard: uses admin_db (never claim_pid) to terminate any
    live PostgreSQL backends matching alembic_app_name, then polls until absent.
    Returns (attempted_terminate, backend_absent).
    """
    attempted = False
    try:
        with admin_db.connect().execution_options(isolation_level="AUTOCOMMIT") as c:
            pids = c.execute(
                text(
                    """
                    SELECT pid FROM pg_stat_activity
                    WHERE datname = :db
                      AND application_name = :app_name
                      AND pid != pg_backend_pid()
                      AND (:claim_pid IS NULL OR pid != :claim_pid)
                    """
                ),
                {"db": db_name, "app_name": alembic_app_name, "claim_pid": claim_pid},
            ).scalars().all()
            for pid in pids:
                attempted = True
                term_ok = c.execute(text("SELECT pg_terminate_backend(:pid)"), {"pid": pid}).scalar()
                if not term_ok:
                    errors.append(f"pg_terminate_backend({pid}) returned false")

            deadline = time.monotonic() + wait_seconds
            while time.monotonic() < deadline:
                remaining = c.execute(
                    text(
                        """
                        SELECT count(*) FROM pg_stat_activity
                        WHERE datname = :db
                          AND application_name = :app_name
                          AND pid != pg_backend_pid()
                          AND (:claim_pid IS NULL OR pid != :claim_pid)
                        """
                    ),
                    {"db": db_name, "app_name": alembic_app_name, "claim_pid": claim_pid},
                ).scalar()
                if remaining == 0:
                    return attempted, True
                time.sleep(0.05)
            errors.append(f"Altered backend(s) for {alembic_app_name} remained in pg_stat_activity")
            return attempted, False
    except Exception as exc:
        errors.append(f"DB backend termination failed: {exc}")
        return attempted, False


def _teardown_downgrade_process_before_rollback(
    popen,
    tx,
    t,
    *,
    e_db=None,
    db_name: str | None = None,
    alembic_app_name: str | None = None,
    claim_pid: int | None = None,
    outcome_sink: dict | None = None,
) -> dict:
    """
    Fail-closed teardown helper shared by all concurrent downgrade tests.
    While tx holds its lock:
      1. Gracefully terminates popen, waits, and escalates to kill() on timeout.
      2. If OS termination fails or DB backend persists, invokes pg_terminate_backend()
         on admin e_db (excluding claim_pid) and waits for backend/process exit.
      3. Only when both OS process and DB backend are confirmed unable to continue does
         it roll back tx, join/verify worker thread t, and allow DB cleanup.
      4. If termination still cannot be confirmed, NEVER rolls back tx: emergency-commits
         the already-attested claim tx so the waiting migration sees durable provenance,
         marks the DB contaminated so outer cleanup will not drop it, and raises
         DowngradeTeardownSafetyError.
    """
    outcome = {
        "os_process_exited": popen is None,
        "kill_escalated": False,
        "db_backend_terminated": False,
        "db_backend_absent": True,
        "popen_exited_while_tx_active": False,
        "emergency_committed": False,
        "contaminated_db": False,
        "errors": [],
    }
    if outcome_sink is not None:
        outcome_sink.clear()
        outcome_sink.update(outcome)

    def _sync_sink():
        if outcome_sink is not None:
            outcome_sink.update(outcome)

    # 1. OS-level termination while claim tx still holds its lock
    if popen is not None:
        try:
            if popen.poll() is None:
                try:
                    popen.terminate()
                except (ProcessLookupError, OSError) as exc:
                    outcome["errors"].append(f"terminate failed: {exc}")
                try:
                    popen.wait(timeout=5)
                except (subprocess.TimeoutExpired, OSError) as exc:
                    outcome["errors"].append(f"wait after terminate failed: {exc}")

                if popen.poll() is None:
                    outcome["kill_escalated"] = True
                    try:
                        popen.kill()
                    except (ProcessLookupError, OSError) as exc:
                        outcome["errors"].append(f"kill failed: {exc}")
                    try:
                        popen.wait(timeout=5)
                    except (subprocess.TimeoutExpired, OSError) as exc:
                        outcome["errors"].append(f"wait after kill failed: {exc}")
            outcome["os_process_exited"] = popen.poll() is not None
        except Exception as exc:
            outcome["errors"].append(f"unexpected OS poll/wait failure: {exc}")
            outcome["os_process_exited"] = False

    # 2. Database-level emergency safeguard via unique PGAPPNAME (never claim_pid)
    if e_db is not None and db_name and alembic_app_name:
        if not outcome["os_process_exited"]:
            attempted, backend_absent = _terminate_alembic_db_backends(
                e_db, db_name, alembic_app_name, claim_pid, outcome["errors"]
            )
            outcome["db_backend_terminated"] = attempted and backend_absent
            outcome["db_backend_absent"] = backend_absent
            if backend_absent and popen is not None and not outcome["os_process_exited"]:
                try:
                    popen.wait(timeout=5)
                    outcome["os_process_exited"] = popen.poll() is not None
                except (subprocess.TimeoutExpired, OSError) as exc:
                    outcome["errors"].append(f"wait after DB termination failed: {exc}")
        else:
            # Verify no orphan Alembic backend lingers in pg_stat_activity
            _, backend_absent = _terminate_alembic_db_backends(
                e_db, db_name, alembic_app_name, claim_pid, outcome["errors"], wait_seconds=1.0
            )
            outcome["db_backend_absent"] = backend_absent
    elif not outcome["os_process_exited"]:
        outcome["db_backend_absent"] = False

    confirmed_stopped = outcome["os_process_exited"] and outcome["db_backend_absent"]
    outcome["popen_exited_while_tx_active"] = confirmed_stopped and tx.is_active

    # 3. Absolute fail-safe: NEVER roll back tx if subprocess/backend cannot be confirmed stopped
    if not confirmed_stopped:
        outcome["contaminated_db"] = True
        if tx.is_active:
            try:
                tx.commit()
                outcome["emergency_committed"] = True
            except Exception as exc:
                outcome["errors"].append(f"emergency tx commit failed: {exc}")
        if t is not None and t.is_alive():
            t.join(timeout=1)
        _sync_sink()
        raise DowngradeTeardownSafetyError(
            "FAIL-SAFE TRIGGERED: Unable to confirm Alembic downgrade stop; "
            f"tx rollback blocked, emergency_committed={outcome['emergency_committed']}, "
            f"db_contaminated=True, errors={outcome['errors']}",
            outcome,
        )

    # 4. Safe path: process & DB backend confirmed stopped -> roll back active claim tx
    if tx.is_active:
        tx.rollback()

    if t is not None and t.is_alive():
        t.join(timeout=5)
    if t is not None and t.is_alive():
        outcome["contaminated_db"] = True
        outcome["errors"].append("worker thread still alive after join(timeout=5)")
        _sync_sink()
        raise DowngradeTeardownSafetyError(
            "FAIL-SAFE TRIGGERED: Alembic downgrade worker thread did not terminate",
            outcome,
        )

    _sync_sink()
    return outcome


def _setup_concurrent_downgrade_case(prefix: str):
    """Provisions an isolated disposable DB at 0039 head with one seeded legacy NULL-Maker filing."""
    db_name, admin_root, b9_admin_str, b9_app_str = _new_disposable_db(prefix)
    env = os.environ.copy()
    env["ENVIRONMENT"] = "test"
    env["DATABASE_ADMIN_URL"] = b9_admin_str
    env["DB_ADMIN_URL"] = b9_admin_str
    env["TEST_DATABASE_URL"] = b9_admin_str
    env["DATABASE_URL"] = b9_app_str
    env["ALLOW_DISPOSABLE_DATABASE_ROLLBACK"] = "true"

    backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    subprocess.run(["python", "-m", "alembic", "upgrade", "head"], cwd=backend_dir, env=env, check=True)
    e_db = create_engine(b9_admin_str)

    org_id = str(uuid.uuid4())
    reg_id = str(uuid.uuid4())
    maker_id = str(uuid.uuid4())
    legacy_fid = str(uuid.uuid4())
    with e_db.begin() as c:
        c.execute(text("INSERT INTO public.organizations (id, legal_name, pan) VALUES (:id, 'B11 Conc', 'AABCB5678Z')"), {"id": org_id})
        c.execute(text("INSERT INTO public.gst_registrations (id, org_id, gstin, state_code, legal_name, registration_type, is_primary) VALUES (:rid, :oid, '27AABCB5678Z1Z5', '27', 'B11', 'REGULAR', true)"), {"rid": reg_id, "oid": org_id})
        c.execute(text("INSERT INTO public.global_users (id, email, full_name, is_active) VALUES (:uid, :em, 'B11', true)"), {"uid": maker_id, "em": f"b11-{maker_id[:8]}@test.local"})
        c.execute(text("INSERT INTO public.organization_memberships (id, org_id, user_id, role, status) VALUES (:mid, :oid, :uid, 'accountant', 'active')"), {"mid": str(uuid.uuid4()), "oid": org_id, "uid": maker_id})
        c.execute(text("SET LOCAL session_replication_role = 'replica'"))
        c.execute(
            text(
                """
                INSERT INTO public.gstr3b_filings (
                    id, org_id, registration_id, tax_period,
                    table_3_1_data, table_4_data, offset_data,
                    filing_status, maker_id, revision_number
                ) VALUES (:fid, :oid, :rid, '112025', '{}'::jsonb, '{}'::jsonb, '{}'::jsonb, 'DRAFT', NULL, 1)
                """
            ),
            {"fid": legacy_fid, "oid": org_id, "rid": reg_id},
        )
    return {
        "db_name": db_name,
        "admin_root": admin_root,
        "b9_admin_str": b9_admin_str,
        "b9_app_str": b9_app_str,
        "env": env,
        "backend_dir": backend_dir,
        "e_db": e_db,
        "org_id": org_id,
        "reg_id": reg_id,
        "maker_id": maker_id,
        "legacy_fid": legacy_fid,
    }


def _launch_downgrade_locked_session(ctx: dict, *, app_suffix: str, wrap_popen=None):
    """Starts claim tx + blocked Alembic downgrade subprocess and waits for real LOCK TABLE contention."""
    app_eng = create_engine(ctx["b9_app_str"])
    nonce = uuid.uuid4().hex
    sig = _sign_gstr3b_legacy_maker_claim(ctx["org_id"], ctx["legacy_fid"], ctx["maker_id"], "LEGACY_SUBMISSION_CLAIM", nonce)
    alembic_app_name = f"myca_b11_{app_suffix}_{uuid.uuid4().hex[:8]}"
    downgrade_env = ctx["env"].copy()
    downgrade_env["PGAPPNAME"] = alembic_app_name
    downgrade_result = {}
    return app_eng, nonce, sig, alembic_app_name, downgrade_env, downgrade_result


def test_concurrent_downgrade_evidence_check_blocks_race():
    ctx = _setup_concurrent_downgrade_case("myca_b10_conc")
    e_db = ctx["e_db"]
    admin_root = ctx["admin_root"]
    db_name = ctx["db_name"]
    contaminated_db = False
    try:
        app_eng, nonce, sig, alembic_app_name, downgrade_env, downgrade_result = _launch_downgrade_locked_session(
            ctx, app_suffix="ok"
        )
        popen = None
        t = None
        outcome_sink = {}
        try:
            with app_eng.connect() as claim_conn:
                tx = claim_conn.begin()
                claim_pid = claim_conn.execute(text("SELECT pg_backend_pid()")).scalar()
                try:
                    claim_conn.execute(text("SELECT set_config('app.current_org', :o, true)"), {"o": ctx["org_id"]})
                    claim_conn.execute(
                        text("SELECT public.claim_legacy_null_maker(CAST(:o AS uuid), CAST(:f AS uuid), CAST(:m AS uuid), 'LEGACY_SUBMISSION_CLAIM', :n, :s)"),
                        {"o": ctx["org_id"], "f": ctx["legacy_fid"], "m": ctx["maker_id"], "n": nonce, "s": sig},
                    )
                    popen = subprocess.Popen(
                        ["python", "-m", "alembic", "downgrade", "0038_gstr3b_attested_creation"],
                        cwd=ctx["backend_dir"],
                        env=downgrade_env,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                    )

                    def _communicate():
                        try:
                            stdout, stderr = popen.communicate()
                            downgrade_result["returncode"] = popen.returncode
                            downgrade_result["stdout"] = stdout
                            downgrade_result["stderr"] = stderr
                        except Exception as exc:
                            downgrade_result["error"] = exc

                    t = threading.Thread(target=_communicate)
                    t.start()

                    observed_lock_wait, last_rows = _poll_alembic_lock_wait(
                        e_db, db_name, alembic_app_name, claim_pid, t
                    )
                    assert observed_lock_wait, (
                        "Did not observe Alembic waiting on LOCK TABLE public.gstr3b_legacy_maker_claims "
                        f"IN ACCESS EXCLUSIVE MODE blocked by claim_pid={claim_pid}; last_rows={last_rows}"
                    )
                    tx.commit()
                    t.join(timeout=30)
                    assert not t.is_alive(), "Alembic downgrade worker thread did not terminate within 30s after claim commit"
                finally:
                    try:
                        _teardown_downgrade_process_before_rollback(
                            popen, tx, t, e_db=e_db, db_name=db_name, alembic_app_name=alembic_app_name, claim_pid=claim_pid, outcome_sink=outcome_sink
                        )
                    except DowngradeTeardownSafetyError:
                        contaminated_db = outcome_sink.get("contaminated_db", True)
                        raise
        finally:
            app_eng.dispose()

        assert "error" not in downgrade_result, f"Subprocess communication failed: {downgrade_result.get('error')}"
        assert downgrade_result.get("returncode") not in (None, 0)
        assert "Refusing to downgrade migration 0039" in (downgrade_result["stderr"] + downgrade_result["stdout"])

        rev = subprocess.run(
            ["python", "-m", "alembic", "current"],
            cwd=ctx["backend_dir"],
            env=ctx["env"],
            capture_output=True,
            text=True,
            check=True,
        )
        assert "0039_gstr3b_legacy_claim_meta" in rev.stdout
        with e_db.connect() as c:
            assert c.execute(text("SELECT count(*) FROM public.gstr3b_legacy_maker_claims WHERE filing_id = :f"), {"f": ctx["legacy_fid"]}).scalar() == 1
    finally:
        e_db.dispose()
        if not contaminated_db:
            with admin_root.connect() as c:
                c.execute(text(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)'))
        admin_root.dispose()


def _run_failure_path_teardown_case(prefix: str, *, popen_wrapper=None, override_e_db=None):
    """Shared driver for failure-path teardown scenarios exercising _teardown_downgrade_process_before_rollback."""
    ctx = _setup_concurrent_downgrade_case(prefix)
    e_db = ctx["e_db"]
    admin_root = ctx["admin_root"]
    db_name = ctx["db_name"]
    real_popen = None
    wrapped_popen = None
    t = None
    downgrade_result = {}
    outcome_sink = {}
    db_dropped_by_teardown = False
    contaminated_db = False

    try:
        app_eng, nonce, sig, alembic_app_name, downgrade_env, downgrade_result = _launch_downgrade_locked_session(
            ctx, app_suffix="fail"
        )
        try:
            with pytest.raises((RuntimeError, DowngradeTeardownSafetyError)):
                try:
                    with app_eng.connect() as claim_conn:
                        tx = claim_conn.begin()
                        claim_pid = claim_conn.execute(text("SELECT pg_backend_pid()")).scalar()
                        try:
                            claim_conn.execute(text("SELECT set_config('app.current_org', :o, true)"), {"o": ctx["org_id"]})
                            claim_conn.execute(
                                text("SELECT public.claim_legacy_null_maker(CAST(:o AS uuid), CAST(:f AS uuid), CAST(:m AS uuid), 'LEGACY_SUBMISSION_CLAIM', :n, :s)"),
                                {"o": ctx["org_id"], "f": ctx["legacy_fid"], "m": ctx["maker_id"], "n": nonce, "s": sig},
                            )
                            real_popen = subprocess.Popen(
                                ["python", "-m", "alembic", "downgrade", "0038_gstr3b_attested_creation"],
                                cwd=ctx["backend_dir"],
                                env=downgrade_env,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE,
                                text=True,
                            )
                            wrapped_popen = popen_wrapper(real_popen) if popen_wrapper else real_popen

                            def _communicate():
                                try:
                                    stdout, stderr = real_popen.communicate()
                                    downgrade_result["returncode"] = real_popen.returncode
                                    downgrade_result["stdout"] = stdout
                                    downgrade_result["stderr"] = stderr
                                except Exception as exc:
                                    downgrade_result["error"] = exc

                            t = threading.Thread(target=_communicate)
                            t.start()

                            observed_lock_wait, _ = _poll_alembic_lock_wait(
                                e_db, db_name, alembic_app_name, claim_pid, t
                            )
                            assert observed_lock_wait
                            raise RuntimeError("Controlled failure before claim commit")
                        finally:
                            try:
                                _teardown_downgrade_process_before_rollback(
                                    wrapped_popen,
                                    tx,
                                    t,
                                    e_db=override_e_db if override_e_db is not None else e_db,
                                    db_name=db_name,
                                    alembic_app_name=alembic_app_name,
                                    claim_pid=claim_pid,
                                    outcome_sink=outcome_sink,
                                )
                            except DowngradeTeardownSafetyError:
                                contaminated_db = outcome_sink.get("contaminated_db", True)
                                raise
                finally:
                    app_eng.dispose()

            # Check DB state while contaminated guard is evaluated
            if not contaminated_db:
                with e_db.connect() as c:
                    assert c.execute(text("SELECT to_regclass('public.gstr3b_legacy_maker_claims')")).scalar() is not None
                    assert c.execute(text("SELECT count(*) FROM public.gstr3b_legacy_maker_claims WHERE filing_id = :f"), {"f": ctx["legacy_fid"]}).scalar() == 0
                rev = subprocess.run(
                    ["python", "-m", "alembic", "current"],
                    cwd=ctx["backend_dir"],
                    env=ctx["env"],
                    capture_output=True,
                    text=True,
                    check=True,
                )
                assert "0039_gstr3b_legacy_claim_meta" in rev.stdout
                db_dropped_by_teardown = True
        finally:
            # Ensure real_popen and t are always reaped before final test-harness DB cleanup
            if real_popen is not None and real_popen.poll() is None:
                real_popen.wait(timeout=15)
            if t is not None and t.is_alive():
                t.join(timeout=15)
            assert real_popen is None or real_popen.poll() is not None
            assert t is None or not t.is_alive()

        # Verify fail-safe database invariants AFTER real_popen exited
        if contaminated_db:
            with e_db.connect() as c:
                assert c.execute(text("SELECT to_regclass('public.gstr3b_legacy_maker_claims')")).scalar() is not None
                assert c.execute(text("SELECT count(*) FROM public.gstr3b_legacy_maker_claims WHERE filing_id = :f"), {"f": ctx["legacy_fid"]}).scalar() == 1
            rev = subprocess.run(
                ["python", "-m", "alembic", "current"],
                cwd=ctx["backend_dir"],
                env=ctx["env"],
                capture_output=True,
                text=True,
                check=True,
            )
            assert "0039_gstr3b_legacy_claim_meta" in rev.stdout

        return outcome_sink, downgrade_result, db_dropped_by_teardown
    finally:
        e_db.dispose()
        if not contaminated_db or (real_popen is None or real_popen.poll() is not None):
            with admin_root.connect() as c:
                c.execute(text(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)'))
        admin_root.dispose()


def test_concurrent_downgrade_failure_path_terminates_before_rollback():
    outcome, _, db_dropped = _run_failure_path_teardown_case("myca_b11_normfail")
    assert outcome["os_process_exited"] is True
    assert outcome["kill_escalated"] is False
    assert outcome["popen_exited_while_tx_active"] is True
    assert outcome["emergency_committed"] is False
    assert outcome["contaminated_db"] is False
    assert db_dropped is True


def test_teardown_terminate_timeout_escalates_to_kill():
    class _TerminateTimeoutPopen:
        def __init__(self, inner):
            self._inner = inner
            self._waits = 0

        def poll(self):
            return self._inner.poll()

        def terminate(self):
            # Ignore graceful terminate to simulate hung child forcing kill() escalation
            return None

        def kill(self):
            return self._inner.kill()

        def wait(self, timeout=None):
            self._waits += 1
            if self._waits == 1:
                raise subprocess.TimeoutExpired(cmd="alembic", timeout=timeout or 5)
            return self._inner.wait(timeout=timeout)

    outcome, _, db_dropped = _run_failure_path_teardown_case(
        "myca_b11_kill", popen_wrapper=_TerminateTimeoutPopen
    )
    assert outcome["kill_escalated"] is True
    assert outcome["os_process_exited"] is True
    assert outcome["popen_exited_while_tx_active"] is True
    assert outcome["emergency_committed"] is False
    assert outcome["contaminated_db"] is False
    assert db_dropped is True


def test_teardown_os_failure_falls_back_to_db_backend_termination():
    class _OsKillBlockedPopen:
        def __init__(self, inner):
            self._inner = inner

        def poll(self):
            return self._inner.poll()

        def terminate(self):
            raise OSError("Simulated OS terminate blocked")

        def kill(self):
            raise OSError("Simulated OS kill blocked")

        def wait(self, timeout=None):
            return self._inner.wait(timeout=timeout)

    outcome, _, db_dropped = _run_failure_path_teardown_case(
        "myca_b11_dbfallback", popen_wrapper=_OsKillBlockedPopen
    )
    assert outcome["kill_escalated"] is True
    assert outcome["db_backend_terminated"] is True
    assert outcome["db_backend_absent"] is True
    assert outcome["os_process_exited"] is True
    assert outcome["popen_exited_while_tx_active"] is True
    assert outcome["emergency_committed"] is False
    assert outcome["contaminated_db"] is False
    assert db_dropped is True


def test_teardown_all_termination_failures_preserves_provenance_and_marks_contaminated():
    class _UnkillablePopen:
        def __init__(self, inner):
            self._inner = inner

        def poll(self):
            return None

        def terminate(self):
            raise OSError("Simulated OS terminate blocked")

        def kill(self):
            raise OSError("Simulated OS kill blocked")

        def wait(self, timeout=None):
            raise subprocess.TimeoutExpired(cmd="alembic", timeout=timeout or 5)

    class _FailingAdminDb:
        def connect(self):
            raise RuntimeError("Simulated unauthorized DB admin connection")

    outcome, downgrade_result, db_dropped = _run_failure_path_teardown_case(
        "myca_b11_allfail",
        popen_wrapper=_UnkillablePopen,
        override_e_db=_FailingAdminDb(),
    )
    assert outcome["os_process_exited"] is False
    assert outcome["db_backend_absent"] is False
    assert outcome["popen_exited_while_tx_active"] is False
    assert outcome["emergency_committed"] is True
    assert outcome["contaminated_db"] is True
    assert db_dropped is False
    # Unblocked by emergency commit, the real migration observed 1 claim and refused downgrade!
    assert downgrade_result["returncode"] != 0
    assert "Refusing to downgrade migration 0039" in (downgrade_result["stderr"] + downgrade_result["stdout"])
