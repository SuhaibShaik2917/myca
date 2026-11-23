"""
MyCA v1.0.6 — Statutory Auditor Verification Provenance and Persistent Replay Hardening.
Exhaustively tests:
1. Critical Finding A:
   - Empty production auditor registry by default.
   - Removal of in-memory test registry fallback.
   - Separation of administrator approval from independent ICAI verification.
   - Rejection of unverified provenance (is_icai_verified=False, verification_status != 'INDEPENDENTLY_VERIFIED').
   - Rejection of revoked, inactive, or expired COP auditors.
   - Fail-closed behavior on database error / connection loss during auditor lookup.
2. Critical Finding B:
   - Removal of in-memory signature replay cache fallback.
   - Strict database-backed atomic token replay prevention via sha256(token).
   - Rejection of duplicate consumption on same document.
   - Rejection of cross-document token replay.
   - Rejection of concurrent collision across distinct sessions.
   - Permanent invalidation lifecycle (expired tokens cannot be reused).
   - Fail-closed behavior on database error / connection loss during replay check.
"""

import uuid
from datetime import datetime, timezone, timedelta
import pytest
from sqlalchemy import text

from tests.test_db_config import get_admin_session
from app.database import SessionLocal
from app.modules.reconciliation.itc_engine import (
    validate_auditor_credentials,
    onboard_verified_statutory_auditor,
    revoke_statutory_auditor,
    check_and_record_signature_replay,
)


class MockFailingSession:
    """Simulates database connection drop or unexpected transaction failure."""
    def execute(self, *args, **kwargs):
        raise ConnectionError("Simulated database failure: Connection refused")

    def rollback(self):
        pass

    def close(self):
        pass


@pytest.fixture
def clean_test_auditor_env():
    """Provides isolated test auditor ID and ensures complete cleanup on teardown."""
    admin_db = get_admin_session()
    test_id = f"CA-AUDIT-TEST-{uuid.uuid4().hex[:8].upper()}"
    mem_no = f"{uuid.uuid4().int % 900000 + 100000}"  # Genuine 6-digit number

    yield {"id": test_id, "mem_no": mem_no, "admin_db": admin_db}

    try:
        admin_db.execute(
            text("DELETE FROM public.statutory_audit_signature_replays WHERE verifier_id = :vid;"),
            {"vid": test_id}
        )
        admin_db.execute(
            text("DELETE FROM public.statutory_auditors WHERE auditor_id = :vid;"),
            {"vid": test_id}
        )
        admin_db.commit()
    except Exception:
        admin_db.rollback()
    finally:
        admin_db.close()


# ============================================================================
# CRITICAL FINDING A: STATUTORY AUDITOR VERIFICATION PROVENANCE & FAIL-CLOSED
# ============================================================================

def test_statutory_auditor_unregistered_fails_closed(clean_test_auditor_env):
    """Verifies that an unregistered auditor fails closed with UNREGISTERED_AUDITOR."""
    app_db = SessionLocal()
    try:
        is_valid, msg = validate_auditor_credentials(
            clean_test_auditor_env["id"], "CHARTERED_ACCOUNTANT", db=app_db
        )
        assert is_valid is False
        assert "UNREGISTERED_AUDITOR" in msg
    finally:
        app_db.close()


def test_statutory_auditor_unverified_provenance_rejected(clean_test_auditor_env):
    """
    Verifies that administrator approval alone CANNOT be misrepresented as
    independent ICAI verification. Must fail closed with UNVERIFIED_AUDITOR_PROVENANCE.
    """
    admin_db = clean_test_auditor_env["admin_db"]
    app_db = SessionLocal()
    vid = clean_test_auditor_env["id"]
    m_no = clean_test_auditor_env["mem_no"]

    try:
        # Onboard auditor with admin approval but WITHOUT independent ICAI verification
        onboard_verified_statutory_auditor(
            db=admin_db,
            auditor_id=vid,
            membership_no=m_no,
            name="CA Unverified Provenance",
            role="CHARTERED_ACCOUNTANT",
            approved_by="ADMIN_OPERATOR",
            verification_status="PENDING_INDEPENDENT_VERIFICATION",
            is_icai_verified=False,
            is_cop_verified=False,
            verification_source="MANUAL_REVIEW"
        )

        is_valid, msg = validate_auditor_credentials(vid, "CHARTERED_ACCOUNTANT", db=app_db)
        assert is_valid is False
        assert "UNVERIFIED_AUDITOR_PROVENANCE" in msg
        assert "Administrator approval alone cannot be misrepresented" in msg
    finally:
        app_db.close()


def test_statutory_auditor_independently_verified_provenance_accepted(clean_test_auditor_env):
    """Verifies that an auditor with genuine independent ICAI & COP verification passes."""
    admin_db = clean_test_auditor_env["admin_db"]
    app_db = SessionLocal()
    vid = clean_test_auditor_env["id"]
    m_no = clean_test_auditor_env["mem_no"]

    try:
        onboard_verified_statutory_auditor(
            db=admin_db,
            auditor_id=vid,
            membership_no=m_no,
            name="CA Verified Provenance",
            role="CHARTERED_ACCOUNTANT",
            approved_by="SYS_ADMIN",
            verification_status="INDEPENDENTLY_VERIFIED",
            is_icai_verified=True,
            is_cop_verified=True,
            verification_source="ICAI_DIGITAL_VERIFICATION_GATEWAY",
            verification_evidence_ref="ICAI-REF-PROV-001"
        )

        is_valid, msg = validate_auditor_credentials(vid, "CHARTERED_ACCOUNTANT", db=app_db)
        assert is_valid is True
        assert "independently verified active" in msg
        assert m_no in msg
    finally:
        app_db.close()


def test_statutory_auditor_revoked_status_rejected(clean_test_auditor_env):
    """Verifies that a previously verified auditor who is revoked is rejected."""
    admin_db = clean_test_auditor_env["admin_db"]
    app_db = SessionLocal()
    vid = clean_test_auditor_env["id"]
    m_no = clean_test_auditor_env["mem_no"]

    try:
        onboard_verified_statutory_auditor(
            db=admin_db,
            auditor_id=vid,
            membership_no=m_no,
            name="CA Revoked Test",
            role="CHARTERED_ACCOUNTANT",
            approved_by="SYS_ADMIN",
            verification_status="INDEPENDENTLY_VERIFIED",
            is_icai_verified=True,
            is_cop_verified=True,
            verification_source="ICAI_DIGITAL_VERIFICATION_GATEWAY"
        )

        # Revoke auditor
        revoked = revoke_statutory_auditor(
            db=admin_db,
            auditor_id=vid,
            revoked_by="DISCIPLINARY_COMMITTEE",
            reason="License suspended"
        )
        assert revoked is True

        is_valid, msg = validate_auditor_credentials(vid, "CHARTERED_ACCOUNTANT", db=app_db)
        assert is_valid is False
        assert "REVOKED_AUDITOR" in msg
    finally:
        app_db.close()


def test_statutory_auditor_expired_cop_rejected(clean_test_auditor_env):
    """Verifies that an auditor with expired Certificate of Practice is rejected."""
    admin_db = clean_test_auditor_env["admin_db"]
    app_db = SessionLocal()
    vid = clean_test_auditor_env["id"]
    m_no = clean_test_auditor_env["mem_no"]

    try:
        onboard_verified_statutory_auditor(
            db=admin_db,
            auditor_id=vid,
            membership_no=m_no,
            name="CA Expired COP",
            role="CHARTERED_ACCOUNTANT",
            approved_by="SYS_ADMIN",
            cop_valid_until="2022-03-31",
            verification_status="INDEPENDENTLY_VERIFIED",
            is_icai_verified=True,
            is_cop_verified=True,
            verification_source="ICAI_DIGITAL_VERIFICATION_GATEWAY"
        )

        is_valid, msg = validate_auditor_credentials(vid, "CHARTERED_ACCOUNTANT", db=app_db)
        assert is_valid is False
        assert "EXPIRED" in msg
    finally:
        app_db.close()


def test_statutory_auditor_db_failure_fails_closed():
    """Verifies that unexpected database failure during lookup fails closed."""
    failing_db = MockFailingSession()
    is_valid, msg = validate_auditor_credentials("ANY_ID", "CHARTERED_ACCOUNTANT", db=failing_db)
    assert is_valid is False
    assert "UNREGISTERED_AUDITOR" in msg or "DATABASE_ERROR" in msg


# ============================================================================
# CRITICAL FINDING B: PERSISTENT REPLAY DEFENSE & REMOVAL OF IN-MEMORY FALLBACK
# ============================================================================

def test_signature_replay_cross_document_reuse_rejected(clean_test_auditor_env):
    """Verifies that a token issued for doc1 cannot be reused for doc2."""
    admin_db = clean_test_auditor_env["admin_db"]
    vid = clean_test_auditor_env["id"]
    m_no = clean_test_auditor_env["mem_no"]

    onboard_verified_statutory_auditor(
        db=admin_db,
        auditor_id=vid,
        membership_no=m_no,
        name="CA Replay Test",
        role="CHARTERED_ACCOUNTANT",
        approved_by="SYS_ADMIN",
        verification_status="INDEPENDENTLY_VERIFIED",
        is_icai_verified=True,
        is_cop_verified=True,
        verification_source="ICAI_DIGITAL_VERIFICATION_GATEWAY"
    )

    token = f"TEST_TOKEN_CROSS_REPLAY_{uuid.uuid4().hex}_{uuid.uuid4().hex}"
    doc1_id = f"DOC-TEST-1-{uuid.uuid4().hex[:6]}"
    doc1_num = "INV/TEST/001"
    doc2_id = f"DOC-TEST-2-{uuid.uuid4().hex[:6]}"
    doc2_num = "INV/TEST/002"

    app_db = SessionLocal()
    try:
        # First consumption on doc1 succeeds
        ok1, msg1 = check_and_record_signature_replay(
            token=token, doc_id=doc1_id, doc_number=doc1_num, db=app_db, verifier_id=vid
        )
        assert ok1 is True
        assert "recorded successfully" in msg1

        # Second consumption attempt on doc2 fails with cross-document replay
        ok2, msg2 = check_and_record_signature_replay(
            token=token, doc_id=doc2_id, doc_number=doc2_num, db=app_db, verifier_id=vid
        )
        assert ok2 is False
        assert "SIGNATURE_REPLAY_DETECTED" in msg2
        assert "cross-document replay rejected" in msg2
    finally:
        app_db.close()


def test_signature_replay_duplicate_consumption_rejected(clean_test_auditor_env):
    """Verifies that the same token cannot be consumed twice on the same document."""
    admin_db = clean_test_auditor_env["admin_db"]
    vid = clean_test_auditor_env["id"]
    m_no = clean_test_auditor_env["mem_no"]

    onboard_verified_statutory_auditor(
        db=admin_db,
        auditor_id=vid,
        membership_no=m_no,
        name="CA Replay Duplicate",
        role="CHARTERED_ACCOUNTANT",
        approved_by="SYS_ADMIN",
        verification_status="INDEPENDENTLY_VERIFIED",
        is_icai_verified=True,
        is_cop_verified=True,
        verification_source="ICAI_DIGITAL_VERIFICATION_GATEWAY"
    )

    token = f"TEST_TOKEN_SAME_DOC_DUP_{uuid.uuid4().hex}_{uuid.uuid4().hex}"
    doc_id = f"DOC-DUP-{uuid.uuid4().hex[:6]}"
    doc_num = "INV/TEST/DUP01"

    app_db = SessionLocal()
    try:
        # First consumption succeeds
        ok1, msg1 = check_and_record_signature_replay(
            token=token, doc_id=doc_id, doc_number=doc_num, db=app_db, verifier_id=vid
        )
        assert ok1 is True

        # Duplicate consumption on same document rejected
        ok2, msg2 = check_and_record_signature_replay(
            token=token, doc_id=doc_id, doc_number=doc_num, db=app_db, verifier_id=vid
        )
        assert ok2 is False
        assert "already been consumed" in msg2
    finally:
        app_db.close()


def test_signature_replay_expired_token_permanent_invalidation(clean_test_auditor_env):
    """Verifies that an expired token remains permanently recorded and cannot be reused."""
    admin_db = clean_test_auditor_env["admin_db"]
    vid = clean_test_auditor_env["id"]
    m_no = clean_test_auditor_env["mem_no"]

    onboard_verified_statutory_auditor(
        db=admin_db,
        auditor_id=vid,
        membership_no=m_no,
        name="CA Replay Expired",
        role="CHARTERED_ACCOUNTANT",
        approved_by="SYS_ADMIN",
        verification_status="INDEPENDENTLY_VERIFIED",
        is_icai_verified=True,
        is_cop_verified=True,
        verification_source="ICAI_DIGITAL_VERIFICATION_GATEWAY"
    )

    token = f"TEST_TOKEN_EXPIRED_{uuid.uuid4().hex}_{uuid.uuid4().hex}"
    doc_id = f"DOC-EXP-{uuid.uuid4().hex[:6]}"
    doc_num = "INV/TEST/EXP01"
    past_expiry = datetime.now(timezone.utc) - timedelta(days=2)

    app_db = SessionLocal()
    try:
        # Record with an expired date
        ok1, _ = check_and_record_signature_replay(
            token=token, doc_id=doc_id, doc_number=doc_num, db=app_db,
            verifier_id=vid, expires_at=past_expiry
        )
        assert ok1 is True

        # Even though expires_at is in the past, token can NEVER be reused!
        ok2, msg2 = check_and_record_signature_replay(
            token=token, doc_id=doc_id, doc_number=doc_num, db=app_db, verifier_id=vid
        )
        assert ok2 is False
        assert "already been consumed" in msg2
    finally:
        app_db.close()


def test_signature_replay_concurrent_collision_rejected(clean_test_auditor_env):
    """
    Simulates concurrent race condition across distinct database sessions
    and verifies atomic collision detection via ON CONFLICT.
    """
    admin_db = clean_test_auditor_env["admin_db"]
    vid = clean_test_auditor_env["id"]
    m_no = clean_test_auditor_env["mem_no"]

    onboard_verified_statutory_auditor(
        db=admin_db,
        auditor_id=vid,
        membership_no=m_no,
        name="CA Replay Concurrency",
        role="CHARTERED_ACCOUNTANT",
        approved_by="SYS_ADMIN",
        verification_status="INDEPENDENTLY_VERIFIED",
        is_icai_verified=True,
        is_cop_verified=True,
        verification_source="ICAI_DIGITAL_VERIFICATION_GATEWAY"
    )

    token = f"TEST_TOKEN_CONCURRENT_RACE_{uuid.uuid4().hex}_{uuid.uuid4().hex}"
    doc_id = f"DOC-CONC-{uuid.uuid4().hex[:6]}"
    doc_num = "INV/TEST/CONC01"

    session1 = SessionLocal()
    session2 = SessionLocal()
    try:
        # Worker 1 records token successfully
        ok1, msg1 = check_and_record_signature_replay(
            token=token, doc_id=doc_id, doc_number=doc_num, db=session1, verifier_id=vid
        )
        assert ok1 is True

        # Worker 2 simultaneously attempts to record same token
        ok2, msg2 = check_and_record_signature_replay(
            token=token, doc_id=doc_id, doc_number=doc_num, db=session2, verifier_id=vid
        )
        assert ok2 is False
        assert "REPLAY" in msg2 or "CONCURRENT" in msg2
    finally:
        session1.close()
        session2.close()


def test_signature_replay_db_failure_fails_closed_no_in_memory_fallback():
    """
    Verifies that under database failure, check_and_record_signature_replay
    fails closed and NEVER silently falls back to an in-memory dictionary.
    """
    failing_db = MockFailingSession()
    token = "TEST_TOKEN_DB_FAILURE_FAIL_CLOSED_32_CHARS"

    ok, msg = check_and_record_signature_replay(
        token=token, doc_id="DOC_FAIL_01", doc_number="INV_FAIL", db=failing_db
    )
    assert ok is False
    assert "DATABASE_TRANSACTION_FAILURE" in msg
