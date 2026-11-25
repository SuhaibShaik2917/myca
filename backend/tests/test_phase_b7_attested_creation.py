"""
Phase B.7 — Server-Authenticated Creation, Legacy NULL Maker Lock, Filing Replay, and Key Sync Tests.

Covers (all SQL attacks executed as least-privilege `app_service_user` with tenant context):
1. P0 - Server-Authenticated Filing Creation:
   - Forge app.current_user via set_config() and attempt direct INSERT of DRAFT as another Maker -> SQLSTATE 42501.
   - Forge app.current_org and attempt cross-organization INSERT -> SQLSTATE 42501.
   - Direct INSERT with fabricated financial snapshot JSON -> SQLSTATE 42501.
   - Attempt to reuse valid creation attestation with modified financial data -> SQLSTATE 42501.
   - Attempt to replay an already consumed creation nonce -> SQLSTATE 42501.
   - Attempt to create with invalid or cross-organization registration -> SQLSTATE 42501.
   - Legitimate prepare_gstr3b_return() succeeds.
2. P1 - Legacy NULL maker_id Protection:
   - Ordinary SQL same-state and transition updates assigning maker_id on a legacy NULL row -> SQLSTATE 42501.
   - Legitimate submit_gstr3b_for_review() claims legacy NULL maker_id via server-attested function and locks it.
3. P1 - Explicit Filing Nonce Replay Test:
   - Executes one valid execute_gstr3b_filing_transition() with known (nonce, sig) -> succeeds.
   - Repeats the exact same call with the same (nonce, sig) -> rejects specifically with 'Replayed filing attestation nonce rejected'.
4. P1 - Dedicated Attestation Key Migration Compatibility (0035 -> 0038):
   - Provisions a disposable DB at 0035 (with old SECRET_KEY trust anchor), enables a dedicated
     GSTR3B_REVIEW_ATTESTATION_KEY, upgrades to 0038, and verifies trust anchor synchronization.
"""

import hashlib
import json
import os
import random
import string
import subprocess
import uuid
from datetime import datetime, timezone
import psycopg
import pytest

from app.database import SessionLocal
from app.modules.gstr3b.service import (
    prepare_gstr3b_return,
    submit_gstr3b_for_review,
    review_gstr3b_decision,
    _compute_gstr3b_payload_digest,
    _compute_gstr3b_meta_digest,
    _sign_gstr3b_creation_attestation,
    _sign_gstr3b_filing_attestation,
)
from tests.test_db_config import DB_ADMIN, DB_APP


@pytest.fixture
def b7_org_setup():
    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            org_id = str(uuid.uuid4())
            other_org_id = str(uuid.uuid4())
            pan_a = f"{''.join(random.choices(string.ascii_uppercase, k=5))}{random.randint(1000, 9999)}A"
            pan_b = f"{''.join(random.choices(string.ascii_uppercase, k=5))}{random.randint(1000, 9999)}B"
            gstin_a = f"27{pan_a}1ZA"
            gstin_b = f"29{pan_b}1ZB"
            reg_id = str(uuid.uuid4())
            other_reg_id = str(uuid.uuid4())

            cur.execute("INSERT INTO public.organizations (id, legal_name, pan) VALUES (%s, 'B7 Org', %s);", (org_id, pan_a))
            cur.execute("INSERT INTO public.organizations (id, legal_name, pan) VALUES (%s, 'B7 Other Org', %s);", (other_org_id, pan_b))

            users = {}
            for label, oid, role in [
                ("maker", org_id, "accountant"),
                ("victim", org_id, "accountant"),
                ("checker_admin", org_id, "admin"),
                ("outsider", other_org_id, "owner"),
            ]:
                uid = str(uuid.uuid4())
                cur.execute(
                    "INSERT INTO public.global_users (id, email, full_name) VALUES (%s, %s, %s);",
                    (uid, f"b7_{label}_{uuid.uuid4().hex}@example.com", f"B7 {label}"),
                )
                cur.execute(
                    "INSERT INTO public.organization_memberships (org_id, user_id, role, status) VALUES (%s, %s, %s, 'active');",
                    (oid, uid, role),
                )
                users[label] = uid

            cur.execute(
                "INSERT INTO public.gst_registrations (id, org_id, gstin, state_code, legal_name, is_primary) VALUES (%s, %s, %s, '27', 'B7 Entity A', true);",
                (reg_id, org_id, gstin_a),
            )
            cur.execute(
                "INSERT INTO public.gst_registrations (id, org_id, gstin, state_code, legal_name, is_primary) VALUES (%s, %s, %s, '29', 'B7 Entity B', true);",
                (other_reg_id, other_org_id, gstin_b),
            )

    return {
        "org_id": org_id,
        "other_org_id": other_org_id,
        "reg_id": reg_id,
        "other_reg_id": other_reg_id,
        **users,
    }


def _app_exec(org_id: str, user_id: str, sql: str, params: tuple = ()):
    with psycopg.connect(DB_APP, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('app.current_org', %s, false);", (org_id,))
            cur.execute("SELECT set_config('app.current_user', %s, false);", (user_id,))
            cur.execute(sql, params)


def test_p0_creation_attestation_and_forgery_defenses(b7_org_setup):
    s = b7_org_setup

    # 1. Forge app.current_user via set_config() and attempt direct INSERT as victim -> 42501
    with pytest.raises(psycopg.errors.InsufficientPrivilege) as exc_forged_user:
        _app_exec(
            s["org_id"],
            s["victim"],
            """
            INSERT INTO public.gstr3b_filings (
                id, org_id, registration_id, tax_period, filing_status,
                table_3_1_data, table_4_data, offset_data, maker_id
            ) VALUES (
                %s, %s, %s, '2026-01', 'DRAFT',
                '{"forged": true}'::jsonb, '{}'::jsonb, '{}'::jsonb, %s
            );
            """,
            (str(uuid.uuid4()), s["org_id"], s["reg_id"], s["victim"]),
        )
    assert exc_forged_user.value.sqlstate == "42501"

    # 2. Forge app.current_org (outsider context targeting org_id) and attempt cross-org INSERT -> 42501
    with pytest.raises(psycopg.errors.InsufficientPrivilege) as exc_cross_org:
        _app_exec(
            s["other_org_id"],
            s["outsider"],
            """
            INSERT INTO public.gstr3b_filings (
                id, org_id, registration_id, tax_period, filing_status,
                table_3_1_data, table_4_data, offset_data, maker_id
            ) VALUES (
                %s, %s, %s, '2026-02', 'DRAFT',
                '{}'::jsonb, '{}'::jsonb, '{}'::jsonb, %s
            );
            """,
            (str(uuid.uuid4()), s["org_id"], s["reg_id"], s["victim"]),
        )
    assert exc_cross_org.value.sqlstate == "42501"

    # 3. Direct INSERT with fabricated financial JSON -> 42501
    with pytest.raises(psycopg.errors.InsufficientPrivilege) as exc_fab:
        _app_exec(
            s["org_id"],
            s["maker"],
            """
            INSERT INTO public.gstr3b_filings (
                id, org_id, registration_id, tax_period, filing_status,
                table_3_1_data, table_4_data, offset_data, maker_id
            ) VALUES (
                %s, %s, %s, '2026-03', 'DRAFT',
                '{"fabricated": 999999}'::jsonb, '{}'::jsonb, '{}'::jsonb, %s
            );
            """,
            (str(uuid.uuid4()), s["org_id"], s["reg_id"], s["maker"]),
        )
    assert exc_fab.value.sqlstate == "42501"

    # 4. Attempt to reuse a valid creation attestation with modified financial data -> 42501
    fid = str(uuid.uuid4())
    t31_orig = '{"a":1}'
    t4_orig = '{"b":2}'
    off_orig = '{"c":3}'
    digest_orig = _compute_gstr3b_payload_digest(t31_orig, t4_orig, off_orig)
    meta_digest_orig = _compute_gstr3b_meta_digest(1, "Valid", None)
    nonce_1 = f"create-{uuid.uuid4().hex}"
    sig_1 = _sign_gstr3b_creation_attestation(
        s["org_id"], fid, s["reg_id"], "2026-04", s["maker"], digest_orig, meta_digest_orig, nonce_1
    )

    with pytest.raises(psycopg.errors.InsufficientPrivilege) as exc_tamper_data:
        _app_exec(
            s["org_id"],
            s["maker"],
            """
            SELECT * FROM public.execute_gstr3b_prepare_upsert(
                %s::uuid, %s::uuid, %s::uuid, '2026-04',
                '{"a":9999}', %s, %s, %s::uuid, 'Valid', 1, NULL, %s, %s, %s, %s
            );
            """,
            (fid, s["org_id"], s["reg_id"], t4_orig, off_orig, s["maker"], digest_orig, meta_digest_orig, nonce_1, sig_1),
        )
    assert exc_tamper_data.value.sqlstate == "42501"

    # 5. Execute valid creation with (nonce_1, sig_1), then replay same nonce_1 -> 42501
    _app_exec(
        s["org_id"],
        s["maker"],
        """
        SELECT * FROM public.execute_gstr3b_prepare_upsert(
            %s::uuid, %s::uuid, %s::uuid, '2026-04',
            %s, %s, %s, %s::uuid, 'Valid', 1, NULL, %s, %s, %s, %s
        );
        """,
        (fid, s["org_id"], s["reg_id"], t31_orig, t4_orig, off_orig, s["maker"], digest_orig, meta_digest_orig, nonce_1, sig_1),
    )

    with pytest.raises(psycopg.errors.InsufficientPrivilege) as exc_replay:
        _app_exec(
            s["org_id"],
            s["maker"],
            """
            SELECT * FROM public.execute_gstr3b_prepare_upsert(
                %s::uuid, %s::uuid, %s::uuid, '2026-04',
                %s, %s, %s, %s::uuid, 'Valid', 1, NULL, %s, %s, %s, %s
            );
            """,
            (fid, s["org_id"], s["reg_id"], t31_orig, t4_orig, off_orig, s["maker"], digest_orig, meta_digest_orig, nonce_1, sig_1),
        )
    assert exc_replay.value.sqlstate == "42501"
    assert "Replayed creation attestation nonce rejected" in str(exc_replay.value)

    # 6. Invalid or cross-organization registration -> 42501
    nonce_2 = f"create-{uuid.uuid4().hex}"
    sig_2 = _sign_gstr3b_creation_attestation(
        s["org_id"], str(uuid.uuid4()), s["other_reg_id"], "2026-05", s["maker"], digest_orig, meta_digest_orig, nonce_2
    )
    with pytest.raises(psycopg.errors.InsufficientPrivilege) as exc_bad_reg:
        _app_exec(
            s["org_id"],
            s["maker"],
            """
            SELECT * FROM public.execute_gstr3b_prepare_upsert(
                %s::uuid, %s::uuid, %s::uuid, '2026-05',
                %s, %s, %s, %s::uuid, 'Valid', 1, NULL, %s, %s, %s, %s
            );
            """,
            (str(uuid.uuid4()), s["org_id"], s["other_reg_id"], t31_orig, t4_orig, off_orig, s["maker"], digest_orig, meta_digest_orig, nonce_2, sig_2),
        )
    assert exc_bad_reg.value.sqlstate == "42501"

    # 7. Legitimate prepare_gstr3b_return() succeeds
    db = SessionLocal()
    try:
        prep = prepare_gstr3b_return(db, s["org_id"], s["reg_id"], "2026-06", user_id=s["maker"])
        assert prep["filing_status"] == "DRAFT"
    finally:
        db.close()


def test_p1_legacy_null_maker_protected_and_claimable_only_via_service(b7_org_setup):
    s = b7_org_setup
    legacy_id = str(uuid.uuid4())

    # Seed legacy filing with maker_id = NULL as DB_ADMIN
    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO public.gstr3b_filings (
                    id, org_id, registration_id, tax_period, filing_status,
                    table_3_1_data, table_4_data, offset_data, maker_id
                ) VALUES (
                    %s, %s, %s, '2025-01', 'DRAFT',
                    '{}'::jsonb, '{}'::jsonb, '{}'::jsonb, NULL
                );
                """,
                (legacy_id, s["org_id"], s["reg_id"]),
            )

    # 1. Ordinary SQL same-state update claiming maker_id -> 42501
    with pytest.raises(psycopg.errors.InsufficientPrivilege) as exc_same:
        _app_exec(
            s["org_id"],
            s["victim"],
            "UPDATE public.gstr3b_filings SET maker_id = %s WHERE id = %s;",
            (s["victim"], legacy_id),
        )
    assert exc_same.value.sqlstate == "42501"

    # 2. Ordinary SQL transition update claiming maker_id -> 42501
    with pytest.raises(psycopg.errors.InsufficientPrivilege) as exc_trans:
        _app_exec(
            s["org_id"],
            s["victim"],
            "UPDATE public.gstr3b_filings SET filing_status = 'SUBMITTED_FOR_REVIEW', maker_id = %s WHERE id = %s;",
            (s["victim"], legacy_id),
        )
    assert exc_trans.value.sqlstate == "42501"

    # 3. Legitimate submit_gstr3b_for_review() claims legacy NULL maker_id via server attestation
    db = SessionLocal()
    try:
        sub = submit_gstr3b_for_review(db, s["org_id"], legacy_id, s["maker"], "Claim and submit legacy")
        assert sub["status"] == "SUBMITTED_FOR_REVIEW"
    finally:
        db.close()


def test_p1_filing_nonce_replay_rejected_by_nonce_registry(b7_org_setup):
    s = b7_org_setup
    db = SessionLocal()
    try:
        prep = prepare_gstr3b_return(db, s["org_id"], s["reg_id"], "2026-07", user_id=s["maker"])
        filing_id = prep["id"]
        submit_gstr3b_for_review(db, s["org_id"], filing_id, s["maker"], "Submit")
        review_gstr3b_decision(db, s["org_id"], filing_id, s["checker_admin"], "APPROVED", "Approved")
    finally:
        db.close()

    arn = "AA270726REPLAY01"
    cin = "CINREPLAY01"
    phash = "a" * 64
    known_nonce = f"filing-replay-{uuid.uuid4().hex}"
    known_sig = _sign_gstr3b_filing_attestation(
        s["org_id"], filing_id, s["checker_admin"], arn, cin, phash, known_nonce
    )

    # 1. Execute one valid filing transition with (known_nonce, known_sig) -> succeeds
    _app_exec(
        s["org_id"],
        s["checker_admin"],
        """
        SELECT public.execute_gstr3b_filing_transition(
            %s::uuid, %s::uuid, %s::uuid,
            %s, %s, clock_timestamp(), %s, %s, %s
        );
        """,
        (s["org_id"], filing_id, s["checker_admin"], arn, cin, phash, known_nonce, known_sig),
    )

    # 2. Repeat the exact same call with the same (known_nonce, known_sig) ->
    #    must fail specifically on duplicate nonce replay BEFORE hitting FILED immutability!
    with pytest.raises(psycopg.errors.InsufficientPrivilege) as exc_replay:
        _app_exec(
            s["org_id"],
            s["checker_admin"],
            """
            SELECT public.execute_gstr3b_filing_transition(
                %s::uuid, %s::uuid, %s::uuid,
                %s, %s, clock_timestamp(), %s, %s, %s
            );
            """,
            (s["org_id"], filing_id, s["checker_admin"], arn, cin, phash, known_nonce, known_sig),
        )
    assert exc_replay.value.sqlstate == "42501"
    assert "Replayed filing attestation nonce rejected" in str(exc_replay.value)


def test_p1_dedicated_attestation_key_upgrade_from_0035_to_0038():
    """
    Provisions a disposable DB at 0035 (with legacy SECRET_KEY trust anchor),
    enables a dedicated GSTR3B_REVIEW_ATTESTATION_KEY, upgrades to 0038,
    and proves gstr3b_review_trust_anchors is synchronized to the dedicated key.
    """
    base_url = DB_ADMIN.rsplit("/", 1)[0]
    disp_db = "myca_b7_key_sync_test"
    disp_admin = f"{base_url}/{disp_db}"

    with psycopg.connect(f"{base_url}/postgres", autocommit=True) as conn:
        conn.execute(f"DROP DATABASE IF EXISTS {disp_db};")
        conn.execute(f"CREATE DATABASE {disp_db};")

    dedicated_key = "MYCA_DEDICATED_GSTR3B_ATTESTATION_KEY_PHASE_B7_MIN32CHARS"
    expected_hash = hashlib.sha256(dedicated_key.encode("utf-8")).hexdigest()

    env = os.environ.copy()
    env["DB_ADMIN_URL"] = disp_admin
    env["DATABASE_URL"] = disp_admin.replace("postgresql://", "postgresql+psycopg://", 1)

    try:
        # Step 1: Migrate to 0035 WITHOUT dedicated key (seeds SECRET_KEY fallback)
        env.pop("GSTR3B_REVIEW_ATTESTATION_KEY", None)
        subprocess.run(["python", "-m", "alembic", "upgrade", "0035_gstr3b_reviewer_binding"], env=env, check=True)

        # Step 2: Enable dedicated key and upgrade to 0038
        env["GSTR3B_REVIEW_ATTESTATION_KEY"] = dedicated_key
        subprocess.run(["python", "-m", "alembic", "upgrade", "0038_gstr3b_attested_creation"], env=env, check=True)

        with psycopg.connect(disp_admin, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT hmac_secret FROM public.gstr3b_review_trust_anchors WHERE id = 'default';")
                actual_hash = cur.fetchone()[0]
                assert actual_hash == expected_hash
    finally:
        with psycopg.connect(f"{base_url}/postgres", autocommit=True) as conn:
            conn.execute(f"DROP DATABASE IF EXISTS {disp_db};")
