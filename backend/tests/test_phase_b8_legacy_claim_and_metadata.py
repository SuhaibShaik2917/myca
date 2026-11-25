"""
Phase B.8 Security & Workflow Regression Tests:
1. Legacy NULL maker_id preparation regression (DRAFT and REJECTED_BY_CHECKER)
2. Append-only legacy maker claim provenance table & API status exposure
3. Metadata digest binding (p_rev, p_maker_remarks, p_prev_snap_text) & server clock_timestamp()
4. Production/staging GSTR3B_REVIEW_ATTESTATION_KEY enforcement
"""

import uuid
import pytest
from datetime import datetime, timezone
from pydantic import ValidationError
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from app.config import Settings, settings
from app.modules.gstr3b.service import (
    GSTR3BServiceError,
    _compute_gstr3b_meta_digest,
    _compute_gstr3b_payload_digest,
    _get_gstr3b_attestation_key,
    _sign_gstr3b_creation_attestation,
    get_gstr3b_status,
    prepare_gstr3b_return,
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
def b8_org_setup():
    org_id = str(uuid.uuid4())
    reg_id = str(uuid.uuid4())
    maker_id = str(uuid.uuid4())
    checker_id = str(uuid.uuid4())
    pan = f"AABCB{int(uuid.uuid4().int % 9000) + 1000}Z"
    gstin = f"27{pan}1Z5"

    admin = _AdminSession()
    try:
        admin.execute(
            text(
                """
                INSERT INTO public.organizations (id, legal_name, pan)
                VALUES (:org_id, :legal_name, :pan)
                """
            ),
            {"org_id": org_id, "legal_name": f"B8 Org {org_id[:8]}", "pan": pan},
        )
        admin.execute(
            text(
                """
                INSERT INTO public.gst_registrations (
                    id, org_id, gstin, state_code, legal_name, registration_type, is_primary
                )
                VALUES (:reg_id, :org_id, :gstin, '27', 'B8 Legal Entity', 'REGULAR', true)
                """
            ),
            {"reg_id": reg_id, "org_id": org_id, "gstin": gstin},
        )
        for uid, email, role in [
            (maker_id, f"maker-{maker_id[:8]}@b8.test", "accountant"),
            (checker_id, f"checker-{checker_id[:8]}@b8.test", "owner"),
        ]:
            admin.execute(
                text(
                    """
                    INSERT INTO public.global_users (id, email, full_name, is_active)
                    VALUES (:uid, :email, 'B8 User', true)
                    """
                ),
                {"uid": uid, "email": email},
            )
            admin.execute(
                text(
                    """
                    INSERT INTO public.organization_memberships (id, org_id, user_id, role, status)
                    VALUES (:mid, :org_id, :uid, :role, 'active')
                    """
                ),
                {"mid": str(uuid.uuid4()), "org_id": org_id, "uid": uid, "role": role},
            )
        admin.commit()
    finally:
        admin.close()

    yield {
        "org_id": org_id,
        "reg_id": reg_id,
        "maker_id": maker_id,
        "checker_id": checker_id,
    }

    cleanup = _AdminSession()
    try:
        cleanup.execute(text("SET LOCAL session_replication_role = 'replica'"))
        cleanup.execute(text("DELETE FROM public.gstr3b_legacy_maker_claims WHERE org_id = :org_id"), {"org_id": org_id})
        cleanup.execute(text("DELETE FROM public.gstr3b_revision_history WHERE org_id = :org_id"), {"org_id": org_id})
        cleanup.execute(text("DELETE FROM public.gstr3b_filings WHERE org_id = :org_id"), {"org_id": org_id})
        cleanup.execute(text("DELETE FROM public.gst_registrations WHERE org_id = :org_id"), {"org_id": org_id})
        cleanup.execute(text("DELETE FROM public.organization_memberships WHERE org_id = :org_id"), {"org_id": org_id})
        cleanup.execute(text("DELETE FROM public.global_users WHERE id IN (:m, :c)"), {"m": maker_id, "c": checker_id})
        cleanup.execute(text("DELETE FROM public.organizations WHERE id = :org_id"), {"org_id": org_id})
        cleanup.commit()
    finally:
        cleanup.close()


def _insert_legacy_null_maker_filing(org_id: str, reg_id: str, tax_period: str, status: str) -> str:
    filing_id = str(uuid.uuid4())
    admin = _AdminSession()
    try:
        # Simulate pre-Phase-B.7 legacy row with maker_id IS NULL
        admin.execute(text("SET LOCAL session_replication_role = 'replica'"))
        admin.execute(
            text(
                """
                INSERT INTO public.gstr3b_filings (
                    id, org_id, registration_id, tax_period,
                    table_3_1_data, table_4_data, offset_data,
                    filing_status, maker_id, revision_number
                ) VALUES (
                    :id, :org_id, :reg_id, :tax_period,
                    '{"txval": 1000}'::jsonb, '{"itc_avl": 100}'::jsonb, '{"cash": 900}'::jsonb,
                    :status, NULL, 1
                )
                """
            ),
            {"id": filing_id, "org_id": org_id, "reg_id": reg_id, "tax_period": tax_period, "status": status},
        )
        admin.commit()
    finally:
        admin.close()
    return filing_id


def test_legacy_null_maker_draft_and_rejected_prepare_with_provenance(b8_org_setup):
    org_id = b8_org_setup["org_id"]
    reg_id = b8_org_setup["reg_id"]
    maker_id = b8_org_setup["maker_id"]

    draft_id = _insert_legacy_null_maker_filing(org_id, reg_id, "042025", "DRAFT")
    rejected_id = _insert_legacy_null_maker_filing(org_id, reg_id, "052025", "REJECTED_BY_CHECKER")
    unclaimed_id = _insert_legacy_null_maker_filing(org_id, reg_id, "062025", "DRAFT")

    app = _AppSession()
    try:
        # 1. Direct SQL maker_id assignment on maker_id IS NULL must still fail with 42501
        _set_app_context(app, org_id, maker_id)
        with pytest.raises(DBAPIError) as exc_direct:
            app.execute(
                text("UPDATE public.gstr3b_filings SET maker_id = :m WHERE id = :fid"),
                {"m": maker_id, "fid": unclaimed_id},
            )
            app.flush()
        assert _sqlstate(exc_direct) == "42501"
        app.rollback()

        # 2. Prepare legacy DRAFT with maker_id = NULL -> succeeds via claim_legacy_null_maker + upsert
        _set_app_context(app, org_id, maker_id)
        res_draft = prepare_gstr3b_return(app, org_id, reg_id, "042025", user_id=maker_id)
        app.commit()
        assert res_draft["id"] == draft_id
        assert res_draft["filing_status"] == "DRAFT"
        assert res_draft["revision_number"] == 1

        # 3. Prepare legacy REJECTED_BY_CHECKER with maker_id = NULL -> succeeds and resets to DRAFT
        _set_app_context(app, org_id, maker_id)
        res_rej = prepare_gstr3b_return(app, org_id, reg_id, "052025", user_id=maker_id)
        app.commit()
        assert res_rej["id"] == rejected_id
        assert res_rej["filing_status"] == "DRAFT"
        assert res_rej["revision_number"] == 2

        # 4. Verify provenance recorded in gstr3b_legacy_maker_claims & exposed in get_gstr3b_status()
        _set_app_context(app, org_id, maker_id)
        claim_row = app.execute(
            text(
                """
                SELECT filing_id, org_id, original_maker_state, provenance_type,
                       claimed_by_user_id, claim_reason, attestation_nonce
                FROM public.gstr3b_legacy_maker_claims
                WHERE filing_id = :fid
                """
            ),
            {"fid": draft_id},
        ).mappings().one()
        assert str(claim_row["filing_id"]) == draft_id
        assert str(claim_row["org_id"]) == org_id
        assert claim_row["original_maker_state"] == "UNKNOWN_LEGACY_NULL"
        assert claim_row["provenance_type"] == "LEGACY_SUBMISSION_CLAIM"
        assert str(claim_row["claimed_by_user_id"]) == maker_id
        assert "LEGACY" in claim_row["claim_reason"].upper()
        assert len(claim_row["attestation_nonce"]) >= 16

        status_draft = get_gstr3b_status(app, org_id, reg_id, "042025")
        assert status_draft is not None
        assert status_draft["maker_provenance"] == "LEGACY_CLAIMED"
        assert status_draft["legacy_maker_claim"] is not None
        assert status_draft["legacy_maker_claim"]["original_maker_state"] == "UNKNOWN_LEGACY_NULL"
        assert status_draft["legacy_maker_claim"]["provenance_type"] == "LEGACY_SUBMISSION_CLAIM"
        assert status_draft["legacy_maker_claim"]["claimed_by_user_id"] == maker_id

        # 5. Subsequent revision preserves LEGACY_CLAIMED provenance
        _set_app_context(app, org_id, maker_id)
        res_draft_rev2 = prepare_gstr3b_return(app, org_id, reg_id, "042025", user_id=maker_id)
        app.commit()
        assert res_draft_rev2["revision_number"] == 1
        _set_app_context(app, org_id, maker_id)
        status_rev3 = get_gstr3b_status(app, org_id, reg_id, "042025")
        assert status_rev3["maker_provenance"] == "LEGACY_CLAIMED"
        assert status_rev3["legacy_maker_claim"]["claimed_by_user_id"] == maker_id
    finally:
        app.close()

    # 6. Append-only enforcement on gstr3b_legacy_maker_claims (UPDATE/DELETE fail with 42501)
    admin = _AdminSession()
    try:
        with pytest.raises(DBAPIError) as exc_upd:
            admin.execute(
                text("UPDATE public.gstr3b_legacy_maker_claims SET claim_reason = 'tampered' WHERE filing_id = :fid"),
                {"fid": draft_id},
            )
            admin.flush()
        assert _sqlstate(exc_upd) == "42501"
        admin.rollback()

        with pytest.raises(DBAPIError) as exc_del:
            admin.execute(
                text("DELETE FROM public.gstr3b_legacy_maker_claims WHERE filing_id = :fid"),
                {"fid": draft_id},
            )
            admin.flush()
        assert _sqlstate(exc_del) == "42501"
        admin.rollback()
    finally:
        admin.close()


def test_prepare_upsert_binds_all_metadata_and_uses_server_clock(b8_org_setup):
    org_id = b8_org_setup["org_id"]
    reg_id = b8_org_setup["reg_id"]
    maker_id = b8_org_setup["maker_id"]

    t31_text = '{"txval": 100}'
    t4_text = '{"itc": 10}'
    offset_text = '{"cash": 90}'
    payload_digest = _compute_gstr3b_payload_digest(t31_text, t4_text, offset_text)

    app = _AppSession()
    try:
        for field_name in ("p_rev", "p_maker_remarks", "p_prev_snap_text"):
            filing_id = str(uuid.uuid4())
            tax_period = f"0{1 + ('p_rev', 'p_maker_remarks', 'p_prev_snap_text').index(field_name)}2026"
            nonce = uuid.uuid4().hex
            rev = 1
            remarks = "Original remarks"
            prev_snap = '{"snap": 1}'
            meta_digest = _compute_gstr3b_meta_digest(rev, remarks, prev_snap)
            sig = _sign_gstr3b_creation_attestation(
                org_id, filing_id, reg_id, tax_period, maker_id, payload_digest, meta_digest, nonce
            )

            tampered_rev = rev + 1 if field_name == "p_rev" else rev
            tampered_remarks = "Tampered remarks" if field_name == "p_maker_remarks" else remarks
            tampered_snap = '{"snap": "tampered"}' if field_name == "p_prev_snap_text" else prev_snap

            _set_app_context(app, org_id, maker_id)
            with pytest.raises(DBAPIError) as exc_tamper:
                app.execute(
                    text(
                        """
                        SELECT * FROM public.execute_gstr3b_prepare_upsert(
                            CAST(:p_id AS uuid), CAST(:p_org_id AS uuid), CAST(:p_reg_id AS uuid),
                            :p_tax_period, :p_t31_text, :p_t4_text, :p_offset_text,
                            CAST(:p_maker_id AS uuid), :p_maker_remarks, :p_rev, :p_prev_snap_text,
                            :p_payload_digest, :p_meta_digest, :p_nonce, :p_signature
                        )
                        """
                    ),
                    {
                        "p_id": filing_id,
                        "p_org_id": org_id,
                        "p_reg_id": reg_id,
                        "p_tax_period": tax_period,
                        "p_t31_text": t31_text,
                        "p_t4_text": t4_text,
                        "p_offset_text": offset_text,
                        "p_maker_id": maker_id,
                        "p_maker_remarks": tampered_remarks,
                        "p_rev": tampered_rev,
                        "p_prev_snap_text": tampered_snap,
                        "p_payload_digest": payload_digest,
                        "p_meta_digest": meta_digest,
                        "p_nonce": nonce,
                        "p_signature": sig,
                    },
                )
            assert _sqlstate(exc_tamper) == "42501"
            app.rollback()

        # Valid call succeeds and sets server clock_timestamp() for updated_at
        valid_id = str(uuid.uuid4())
        valid_nonce = uuid.uuid4().hex
        valid_meta_digest = _compute_gstr3b_meta_digest(1, "Valid remarks", '{"snap": "valid"}')
        valid_sig = _sign_gstr3b_creation_attestation(
            org_id, valid_id, reg_id, "072026", maker_id, payload_digest, valid_meta_digest, valid_nonce
        )
        _set_app_context(app, org_id, maker_id)
        before_ts = app.execute(text("SELECT clock_timestamp()")).scalar()
        app.execute(
            text(
                """
                SELECT * FROM public.execute_gstr3b_prepare_upsert(
                    CAST(:p_id AS uuid), CAST(:p_org_id AS uuid), CAST(:p_reg_id AS uuid),
                    :p_tax_period, :p_t31_text, :p_t4_text, :p_offset_text,
                    CAST(:p_maker_id AS uuid), :p_maker_remarks, :p_rev, :p_prev_snap_text,
                    :p_payload_digest, :p_meta_digest, :p_nonce, :p_signature
                )
                """
            ),
            {
                "p_id": valid_id,
                "p_org_id": org_id,
                "p_reg_id": reg_id,
                "p_tax_period": "072026",
                "p_t31_text": t31_text,
                "p_t4_text": t4_text,
                "p_offset_text": offset_text,
                "p_maker_id": maker_id,
                "p_maker_remarks": "Valid remarks",
                "p_rev": 1,
                "p_prev_snap_text": '{"snap": "valid"}',
                "p_payload_digest": payload_digest,
                "p_meta_digest": valid_meta_digest,
                "p_nonce": valid_nonce,
                "p_signature": valid_sig,
            },
        )
        after_ts = app.execute(text("SELECT clock_timestamp()")).scalar()
        updated_at = app.execute(
            text("SELECT updated_at FROM public.gstr3b_filings WHERE id = :fid"),
            {"fid": valid_id},
        ).scalar()
        app.commit()
        assert updated_at is not None
        assert before_ts <= updated_at <= after_ts
    finally:
        app.close()


def test_production_and_staging_require_strong_distinct_attestation_key(monkeypatch):
    strong_secret = "PROD_SECRET_KEY_VALUE_32_CHARS_MINIMUM_OK_1234"
    strong_attest = "PROD_ATTEST_KEY_DISTINCT_32_CHARS_MINIMUM_OK_5678"

    prod_base = dict(
        SECRET_KEY=strong_secret,
        ALLOW_DEMO_TOKENS=False,
        ALLOW_DEV_HEADER_AUTH=False,
        DATABASE_URL="postgresql+psycopg://app:prod_pwd@prod-db.internal:5432/myca",
        DATABASE_WORKER_URL="postgresql+psycopg://worker:prod_pwd@prod-db.internal:5432/myca",
    )

    for env in ("production", "staging"):
        monkeypatch.setenv("ENVIRONMENT", env)
        # Settings validator rejects weak, default/test-marker, or matching SECRET_KEY
        with pytest.raises(ValidationError) as exc_short:
            Settings(ENVIRONMENT=env, GSTR3B_REVIEW_ATTESTATION_KEY="short_key", **prod_base)
        assert "GSTR3B_REVIEW_ATTESTATION_KEY" in str(exc_short.value)

        with pytest.raises(ValidationError) as exc_marker:
            Settings(
                ENVIRONMENT=env,
                GSTR3B_REVIEW_ATTESTATION_KEY="dev-test-secret-key-at-least-32-characters-long",
                **prod_base,
            )
        assert "GSTR3B_REVIEW_ATTESTATION_KEY" in str(exc_marker.value)

        with pytest.raises(ValidationError) as exc_same:
            Settings(ENVIRONMENT=env, GSTR3B_REVIEW_ATTESTATION_KEY=strong_secret, **prod_base)
        assert "GSTR3B_REVIEW_ATTESTATION_KEY" in str(exc_same.value)

        valid_cfg = Settings(
            ENVIRONMENT=env,
            GSTR3B_REVIEW_ATTESTATION_KEY=strong_attest,
            **prod_base,
        )
        assert valid_cfg.GSTR3B_REVIEW_ATTESTATION_KEY == strong_attest

        # Runtime helper _get_gstr3b_attestation_key() fails closed when missing/weak/matching
        monkeypatch.setattr(settings, "ENVIRONMENT", env)
        monkeypatch.setattr(settings, "SECRET_KEY", strong_secret)
        monkeypatch.setattr(settings, "GSTR3B_REVIEW_ATTESTATION_KEY", None)
        with pytest.raises(GSTR3BServiceError):
            _get_gstr3b_attestation_key()

        monkeypatch.setattr(settings, "GSTR3B_REVIEW_ATTESTATION_KEY", strong_secret)
        with pytest.raises(GSTR3BServiceError):
            _get_gstr3b_attestation_key()

        monkeypatch.setattr(settings, "GSTR3B_REVIEW_ATTESTATION_KEY", strong_attest)
        key_bytes = _get_gstr3b_attestation_key()
        assert isinstance(key_bytes, bytes) and len(key_bytes) == 64
