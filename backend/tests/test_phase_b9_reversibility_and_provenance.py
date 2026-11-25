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


def test_concurrent_downgrade_evidence_check_blocks_race():
    db_name, admin_root, b9_admin_str, b9_app_str = _new_disposable_db("myca_b10_conc")
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
        e_db = create_engine(b9_admin_str)

        org_id = str(uuid.uuid4())
        reg_id = str(uuid.uuid4())
        maker_id = str(uuid.uuid4())
        legacy_fid = str(uuid.uuid4())
        with e_db.begin() as c:
            c.execute(text("INSERT INTO public.organizations (id, legal_name, pan) VALUES (:id, 'B10 Conc', 'AABCB5678Z')"), {"id": org_id})
            c.execute(text("INSERT INTO public.gst_registrations (id, org_id, gstin, state_code, legal_name, registration_type, is_primary) VALUES (:rid, :oid, '27AABCB5678Z1Z5', '27', 'B10', 'REGULAR', true)"), {"rid": reg_id, "oid": org_id})
            c.execute(text("INSERT INTO public.global_users (id, email, full_name, is_active) VALUES (:uid, 'b10@test.local', 'B10', true)"), {"uid": maker_id})
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

        # Hold an in-progress claim transaction open while downgrade starts
        app_eng = create_engine(b9_app_str)
        nonce = uuid.uuid4().hex
        sig = _sign_gstr3b_legacy_maker_claim(org_id, legacy_fid, maker_id, "LEGACY_SUBMISSION_CLAIM", nonce)
        downgrade_result = {}
        alembic_app_name = f"myca_b11_downgrade_{uuid.uuid4().hex[:8]}"
        downgrade_env = env.copy()
        downgrade_env["PGAPPNAME"] = alembic_app_name
        popen = None
        t = None

        try:
            with app_eng.connect() as claim_conn:
                tx = claim_conn.begin()
                try:
                    claim_pid = claim_conn.execute(text("SELECT pg_backend_pid()")).scalar()
                    claim_conn.execute(text("SELECT set_config('app.current_org', :o, true)"), {"o": org_id})
                    claim_conn.execute(
                        text("SELECT public.claim_legacy_null_maker(CAST(:o AS uuid), CAST(:f AS uuid), CAST(:m AS uuid), 'LEGACY_SUBMISSION_CLAIM', :n, :s)"),
                        {"o": org_id, "f": legacy_fid, "m": maker_id, "n": nonce, "s": sig},
                    )

                    # Create Popen in the controlling thread before starting the worker thread
                    # so teardown always has the process handle with zero registration race.
                    popen = subprocess.Popen(
                        ["python", "-m", "alembic", "downgrade", "0038_gstr3b_attested_creation"],
                        cwd=backend_dir,
                        env=downgrade_env,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                    )

                    def _communicate_downgrade():
                        try:
                            stdout, stderr = popen.communicate()
                            downgrade_result["returncode"] = popen.returncode
                            downgrade_result["stdout"] = stdout
                            downgrade_result["stderr"] = stderr
                        except Exception as exc:
                            downgrade_result["error"] = exc

                    t = threading.Thread(target=_communicate_downgrade)
                    t.start()

                    # Deterministically poll pg_stat_activity until the Alembic backend is blocked
                    # on LOCK TABLE public.gstr3b_legacy_maker_claims IN ACCESS EXCLUSIVE MODE by claim_pid
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

                    assert observed_lock_wait, (
                        "Did not observe Alembic waiting on LOCK TABLE public.gstr3b_legacy_maker_claims "
                        f"IN ACCESS EXCLUSIVE MODE blocked by claim_pid={claim_pid}; last_rows={last_rows}"
                    )
                    tx.commit()
                    t.join(timeout=30)
                    assert not t.is_alive(), "Alembic downgrade worker thread did not terminate within 30s after claim commit"
                finally:
                    # 1. Terminate Alembic subprocess FIRST while claim transaction still holds its lock
                    if popen is not None and popen.poll() is None:
                        try:
                            popen.terminate()
                        except ProcessLookupError:
                            pass
                        try:
                            popen.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            try:
                                popen.kill()
                            except ProcessLookupError:
                                pass
                            popen.wait(timeout=5)
                    assert popen is None or popen.poll() is not None, "Alembic subprocess failed to terminate before lock release"
                    # 2. Only after subprocess is confirmed exited, roll back active claim transaction
                    if tx.is_active:
                        tx.rollback()
                    # 3. Join communication worker thread and verify termination
                    if t is not None and t.is_alive():
                        t.join(timeout=5)
                    assert t is None or not t.is_alive(), "Alembic downgrade worker thread failed to terminate during teardown"
        finally:
            app_eng.dispose()

        assert "error" not in downgrade_result, f"Subprocess communication failed: {downgrade_result.get('error')}"
        assert "returncode" in downgrade_result, "Subprocess did not produce a returncode"
        assert downgrade_result["returncode"] != 0
        assert "Refusing to downgrade migration 0039" in (downgrade_result["stderr"] + downgrade_result["stdout"])

        # Verify revision, table, and claim data remain intact after refused downgrade
        rev = subprocess.run(
            ["python", "-m", "alembic", "current"],
            cwd=backend_dir,
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        assert "0039_gstr3b_legacy_claim_meta" in rev.stdout
        with e_db.connect() as c:
            assert c.execute(text("SELECT count(*) FROM public.gstr3b_legacy_maker_claims WHERE filing_id = :f"), {"f": legacy_fid}).scalar() == 1
    finally:
        if e_db is not None:
            e_db.dispose()
        if created_db:
            with admin_root.connect() as c:
                c.execute(text(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)'))
        admin_root.dispose()


def test_concurrent_downgrade_failure_path_terminates_before_rollback():
    """
    Proves that on a failure before claim commit, the blocked Alembic downgrade
    subprocess is terminated and confirmed exited WHILE the claim transaction still
    holds its lock, leaving 0039 and gstr3b_legacy_maker_claims intact after rollback.
    """
    db_name, admin_root, b9_admin_str, b9_app_str = _new_disposable_db("myca_b11_failpath")
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
        e_db = create_engine(b9_admin_str)

        org_id = str(uuid.uuid4())
        reg_id = str(uuid.uuid4())
        maker_id = str(uuid.uuid4())
        legacy_fid = str(uuid.uuid4())
        with e_db.begin() as c:
            c.execute(text("INSERT INTO public.organizations (id, legal_name, pan) VALUES (:id, 'B11 Fail', 'AABCB9012Z')"), {"id": org_id})
            c.execute(text("INSERT INTO public.gst_registrations (id, org_id, gstin, state_code, legal_name, registration_type, is_primary) VALUES (:rid, :oid, '27AABCB9012Z1Z5', '27', 'B11', 'REGULAR', true)"), {"rid": reg_id, "oid": org_id})
            c.execute(text("INSERT INTO public.global_users (id, email, full_name, is_active) VALUES (:uid, 'b11fail@test.local', 'B11', true)"), {"uid": maker_id})
            c.execute(text("INSERT INTO public.organization_memberships (id, org_id, user_id, role, status) VALUES (:mid, :oid, :uid, 'accountant', 'active')"), {"mid": str(uuid.uuid4()), "oid": org_id, "uid": maker_id})
            c.execute(text("SET LOCAL session_replication_role = 'replica'"))
            c.execute(
                text(
                    """
                    INSERT INTO public.gstr3b_filings (
                        id, org_id, registration_id, tax_period,
                        table_3_1_data, table_4_data, offset_data,
                        filing_status, maker_id, revision_number
                    ) VALUES (:fid, :oid, :rid, '122025', '{}'::jsonb, '{}'::jsonb, '{}'::jsonb, 'DRAFT', NULL, 1)
                    """
                ),
                {"fid": legacy_fid, "oid": org_id, "rid": reg_id},
            )

        app_eng = create_engine(b9_app_str)
        nonce = uuid.uuid4().hex
        sig = _sign_gstr3b_legacy_maker_claim(org_id, legacy_fid, maker_id, "LEGACY_SUBMISSION_CLAIM", nonce)
        alembic_app_name = f"myca_b11_fail_{uuid.uuid4().hex[:8]}"
        downgrade_env = env.copy()
        downgrade_env["PGAPPNAME"] = alembic_app_name
        popen = None
        t = None
        downgrade_result = {}
        popen_exited_while_tx_active = False

        with pytest.raises(RuntimeError, match="Controlled failure before claim commit"):
            try:
                with app_eng.connect() as claim_conn:
                    tx = claim_conn.begin()
                    try:
                        claim_pid = claim_conn.execute(text("SELECT pg_backend_pid()")).scalar()
                        claim_conn.execute(text("SELECT set_config('app.current_org', :o, true)"), {"o": org_id})
                        claim_conn.execute(
                            text("SELECT public.claim_legacy_null_maker(CAST(:o AS uuid), CAST(:f AS uuid), CAST(:m AS uuid), 'LEGACY_SUBMISSION_CLAIM', :n, :s)"),
                            {"o": org_id, "f": legacy_fid, "m": maker_id, "n": nonce, "s": sig},
                        )

                        popen = subprocess.Popen(
                            ["python", "-m", "alembic", "downgrade", "0038_gstr3b_attested_creation"],
                            cwd=backend_dir,
                            env=downgrade_env,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            text=True,
                        )

                        def _communicate_downgrade():
                            try:
                                stdout, stderr = popen.communicate()
                                downgrade_result["returncode"] = popen.returncode
                                downgrade_result["stdout"] = stdout
                                downgrade_result["stderr"] = stderr
                            except Exception as exc:
                                downgrade_result["error"] = exc

                        t = threading.Thread(target=_communicate_downgrade)
                        t.start()

                        observed_lock_wait = False
                        deadline = time.monotonic() + 20.0
                        with e_db.connect().execution_options(isolation_level="AUTOCOMMIT") as obs_conn:
                            while time.monotonic() < deadline:
                                rows = obs_conn.execute(
                                    text(
                                        """
                                        SELECT pid, application_name, state, wait_event_type, query,
                                               pg_blocking_pids(pid) AS blockers
                                        FROM pg_stat_activity
                                        WHERE datname = :db
                                          AND pid != pg_backend_pid()
                                          AND pid != :claim_pid
                                        """
                                    ),
                                    {"db": db_name, "claim_pid": claim_pid},
                                ).mappings().all()
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

                        assert observed_lock_wait
                        raise RuntimeError("Controlled failure before claim commit")
                    finally:
                        if popen is not None and popen.poll() is None:
                            try:
                                popen.terminate()
                            except ProcessLookupError:
                                pass
                            try:
                                popen.wait(timeout=5)
                            except subprocess.TimeoutExpired:
                                try:
                                    popen.kill()
                                except ProcessLookupError:
                                    pass
                                popen.wait(timeout=5)
                        assert popen is not None and popen.poll() is not None
                        popen_exited_while_tx_active = popen.poll() is not None and tx.is_active
                        if tx.is_active:
                            tx.rollback()
                        if t is not None and t.is_alive():
                            t.join(timeout=5)
                        assert t is not None and not t.is_alive()
            finally:
                app_eng.dispose()

        assert popen_exited_while_tx_active is True
        assert popen.poll() is not None
        assert not t.is_alive()

        rev = subprocess.run(
            ["python", "-m", "alembic", "current"],
            cwd=backend_dir,
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        assert "0039_gstr3b_legacy_claim_meta" in rev.stdout
        with e_db.connect() as c:
            assert c.execute(text("SELECT to_regclass('public.gstr3b_legacy_maker_claims')")).scalar() is not None
            assert c.execute(text("SELECT count(*) FROM public.gstr3b_legacy_maker_claims WHERE filing_id = :f"), {"f": legacy_fid}).scalar() == 0
    finally:
        if e_db is not None:
            e_db.dispose()
        if created_db:
            with admin_root.connect() as c:
                c.execute(text(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)'))
        admin_root.dispose()
