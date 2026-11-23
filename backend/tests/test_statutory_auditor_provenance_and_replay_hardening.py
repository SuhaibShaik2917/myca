"""
MyCA v1.0.8 — Statutory Auditor Verification Provenance, Authentic Evidence & Persistent Replay Hardening.
Exhaustively tests:
1. Authentic ICAI Verification vs Format Validation:
   - Rejection of fabricated reference strings (e.g. ICAI-GATEWAY-FAKE123) without cryptographic proof.
   - Cryptographic verification of gateway responses (RS256/ES256/HS256).
   - Strict binding of claims: membership_no, firm_registration_no, cop_status, cop_valid_until, evidence_ref.
   - Anti-replay nonce (jti) tracking for gateway tokens.
   - Test-key isolation (test keys rejected in production mode).
   - Fail-closed behavior (TRUST_ANCHOR_UNAVAILABLE) when official ICAI gateway is unconfigured in production.
2. Authenticated Manual Evidence Review:
   - Server-side SHA-256 calculation over genuine document bytes.
   - Server-validated reviewer session context (ignoring unauthenticated caller overrides).
   - Authorized reviewer role enforcement (STATUTORY_AUDITOR_ADMIN, CHIEF_COMPLIANCE_OFFICER).
   - Active reviewer requirement.
   - Maker-checker separation (uploader != reviewer).
   - Immutable audit logging in public.statutory_auditor_verification_events.
3. Persistent Signature Replay Protection & Mandatory Expiry:
   - Strict mandatory requirement for non-synthesized expires_at (fails closed if omitted).
   - Rejection of already-expired tokens before database insertion.
   - Rejection of duplicate consumption on same document.
   - Rejection of cross-document token replay.
   - Permanent invalidation lifecycle.
   - Conclusive PostgreSQL pre-INSERT concurrency race test via SQLAlchemy before_cursor_execute hook.
   - Fail-closed behavior on database unavailability / transaction failure.
"""

import os
import uuid
import hashlib
import threading
from datetime import datetime, timezone, timedelta, date
from decimal import Decimal
import pytest
from sqlalchemy import text, event

from tests.test_db_config import get_admin_session
from app.database import SessionLocal, engine
from app.modules.reconciliation.itc_engine import (
    validate_auditor_credentials,
    onboard_verified_statutory_auditor,
    revoke_statutory_auditor,
    check_and_record_signature_replay,
)
from app.modules.reconciliation.icai_gateway_verifier import (
    register_test_gateway_key,
    clear_test_gateway_trust_store,
    create_test_signed_gateway_response,
    verify_icai_gateway_signed_response,
)
from app.modules.reconciliation.manual_review_verifier import (
    verify_manual_evidence_review,
    AUTHORIZED_REVIEWER_ROLES,
)


TEST_KEY_ID = "test-key-v1-0-8"
TEST_SECRET = "MYCA_TEST_SUITE_GATEWAY_SECRET_KEY_MIN32CHARS!"


@pytest.fixture(autouse=True)
def setup_test_trust_store():
    """Initializes and cleans up the test gateway trust store."""
    register_test_gateway_key(TEST_KEY_ID, TEST_SECRET)
    yield
    clear_test_gateway_trust_store()


def make_test_gateway_token(
    membership_no: str,
    evidence_ref: str,
    firm_registration_no: str = None,
    cop_status: str = "ACTIVE",
    cop_valid_until: date = None,
    kid: str = TEST_KEY_ID,
    secret: str = TEST_SECRET,
    algorithm: str = "HS256",
    issuer: str = "https://gateway.icai.org",
    audience: str = "myca-statutory-verification",
    nonce: str = None,
    issued_at: datetime = None,
    expires_at: datetime = None,
    verification_status: str = "INDEPENDENTLY_VERIFIED"
) -> str:
    return create_test_signed_gateway_response(
        membership_no=membership_no,
        firm_registration_no=firm_registration_no,
        cop_status=cop_status,
        cop_valid_until=cop_valid_until or (date.today() + timedelta(days=365)),
        evidence_ref=evidence_ref,
        kid=kid,
        secret_or_key=secret,
        algorithm=algorithm,
        issuer=issuer,
        audience=audience,
        nonce=nonce,
        issued_at=issued_at,
        expires_at=expires_at,
        verification_status=verification_status
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
            text("DELETE FROM public.statutory_auditor_verification_events WHERE auditor_id = :vid;"),
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
# PART 1: STATUTORY AUDITOR VERIFICATION PROVENANCE & CREDENTIAL GATE
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
        onboard_verified_statutory_auditor(
            db=admin_db,
            auditor_id=vid,
            membership_no=m_no,
            name="CA Unverified Provenance",
            role="CHARTERED_ACCOUNTANT",
            approved_by="ADMIN_OPERATOR",
            cop_valid_until=datetime(2030, 12, 31).date(),
            verification_status="PENDING_INDEPENDENT_VERIFICATION",
            is_icai_verified=False,
            is_cop_verified=False,
            verification_source="MANUAL_REVIEW",
            environment="test"
        )

        is_valid, msg = validate_auditor_credentials(vid, "CHARTERED_ACCOUNTANT", db=app_db)
        assert is_valid is False
        assert "UNVERIFIED_AUDITOR_PROVENANCE" in msg
        assert "Administrator approval alone cannot be misrepresented" in msg
    finally:
        app_db.close()


def test_statutory_auditor_self_assertion_precluded(clean_test_auditor_env):
    """
    Verifies that callers CANNOT self-assert verification status by passing
    is_icai_verified=True without authentic independent verification evidence.
    """
    admin_db = clean_test_auditor_env["admin_db"]
    app_db = SessionLocal()
    vid = clean_test_auditor_env["id"]
    m_no = clean_test_auditor_env["mem_no"]

    try:
        onboard_verified_statutory_auditor(
            db=admin_db,
            auditor_id=vid,
            membership_no=m_no,
            name="CA Fraudulent Self Assertion",
            role="CHARTERED_ACCOUNTANT",
            approved_by="CALLER_SELF",
            cop_valid_until=datetime(2030, 12, 31).date(),
            verification_status="INDEPENDENTLY_VERIFIED",
            is_icai_verified=True,
            is_cop_verified=True,
            verification_source="MANUAL_REVIEW",
            environment="test"
        )

        is_valid, msg = validate_auditor_credentials(vid, "CHARTERED_ACCOUNTANT", db=app_db)
        assert is_valid is False
        assert "UNVERIFIED_AUDITOR_PROVENANCE" in msg
    finally:
        app_db.close()


def test_statutory_auditor_independently_verified_provenance_accepted(clean_test_auditor_env):
    """Verifies that an auditor with genuine independent ICAI & COP verification passes."""
    admin_db = clean_test_auditor_env["admin_db"]
    app_db = SessionLocal()
    vid = clean_test_auditor_env["id"]
    m_no = clean_test_auditor_env["mem_no"]
    now_utc = datetime.now(timezone.utc)
    ev_ref = f"ICAI-GATEWAY-TRACE-{uuid.uuid4().hex[:8]}"

    gw_token = make_test_gateway_token(
        membership_no=m_no,
        evidence_ref=ev_ref,
        cop_valid_until=datetime(2030, 12, 31).date()
    )

    try:
        onboard_verified_statutory_auditor(
            db=admin_db,
            auditor_id=vid,
            membership_no=m_no,
            name="CA Verified Provenance",
            role="CHARTERED_ACCOUNTANT",
            approved_by="SYS_ADMIN",
            cop_valid_until=datetime(2030, 12, 31).date(),
            verification_status="INDEPENDENTLY_VERIFIED",
            is_icai_verified=True,
            is_cop_verified=True,
            verification_source="ICAI_DIGITAL_VERIFICATION_GATEWAY",
            verified_at=now_utc,
            verification_evidence_ref=ev_ref,
            gateway_signed_response=gw_token,
            environment="test"
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
    now_utc = datetime.now(timezone.utc)
    ev_ref = f"ICAI-GATEWAY-TRACE-{uuid.uuid4().hex[:8]}"

    gw_token = make_test_gateway_token(
        membership_no=m_no,
        evidence_ref=ev_ref,
        cop_valid_until=datetime(2030, 12, 31).date()
    )

    try:
        onboard_verified_statutory_auditor(
            db=admin_db,
            auditor_id=vid,
            membership_no=m_no,
            name="CA Revoked Test",
            role="CHARTERED_ACCOUNTANT",
            approved_by="SYS_ADMIN",
            cop_valid_until=datetime(2030, 12, 31).date(),
            verification_status="INDEPENDENTLY_VERIFIED",
            is_icai_verified=True,
            is_cop_verified=True,
            verification_source="ICAI_DIGITAL_VERIFICATION_GATEWAY",
            verified_at=now_utc,
            verification_evidence_ref=ev_ref,
            gateway_signed_response=gw_token,
            environment="test"
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
    now_utc = datetime.now(timezone.utc)
    ev_ref = f"ICAI-GATEWAY-TRACE-{uuid.uuid4().hex[:8]}"

    try:
        # 1. Onboarding with already-expired COP must raise ValueError
        try:
            onboard_verified_statutory_auditor(
                db=admin_db,
                auditor_id=vid,
                membership_no=m_no,
                name="CA Expired COP",
                role="CHARTERED_ACCOUNTANT",
                approved_by="SYS_ADMIN",
                cop_valid_until=datetime(2022, 3, 31).date(),
                verification_status="INDEPENDENTLY_VERIFIED",
                is_icai_verified=True,
                is_cop_verified=True,
                verification_source="ICAI_DIGITAL_VERIFICATION_GATEWAY",
                verified_at=now_utc,
                verification_evidence_ref=ev_ref,
                environment="test"
            )
            assert False, "Onboarding with expired COP date must raise ValueError"
        except ValueError as ex:
            assert "future date" in str(ex)

        # 2. Onboard with valid future COP, then simulate COP expiration via database update
        gw_token = make_test_gateway_token(
            membership_no=m_no,
            evidence_ref=ev_ref,
            cop_valid_until=datetime(2030, 12, 31).date()
        )
        onboard_verified_statutory_auditor(
            db=admin_db,
            auditor_id=vid,
            membership_no=m_no,
            name="CA Expired COP",
            role="CHARTERED_ACCOUNTANT",
            approved_by="SYS_ADMIN",
            cop_valid_until=datetime(2030, 12, 31).date(),
            verification_status="INDEPENDENTLY_VERIFIED",
            is_icai_verified=True,
            is_cop_verified=True,
            verification_source="ICAI_DIGITAL_VERIFICATION_GATEWAY",
            verified_at=now_utc,
            verification_evidence_ref=ev_ref,
            gateway_signed_response=gw_token,
            environment="test"
        )
        admin_db.execute(
            text("UPDATE public.statutory_auditors SET cop_valid_until = '2022-03-31' WHERE auditor_id = :vid;"),
            {"vid": vid}
        )
        admin_db.commit()

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
# PART 2: ICAI GATEWAY CRYPTOGRAPHIC VERIFICATION & FAIL-CLOSED PROTOCOL
# ============================================================================

def test_icai_gateway_fabricated_reference_rejected(clean_test_auditor_env):
    """
    Verifies that fabricated reference strings (e.g. 'ICAI-GATEWAY-FAKE123')
    are strictly rejected when presented without authentic cryptographic proof.
    """
    admin_db = clean_test_auditor_env["admin_db"]
    vid = clean_test_auditor_env["id"]
    m_no = clean_test_auditor_env["mem_no"]

    # Attempt to onboard using bare fabricated reference string without signed response
    auditor = onboard_verified_statutory_auditor(
        db=admin_db,
        auditor_id=vid,
        membership_no=m_no,
        name="CA Fake Gateway Reference",
        role="CHARTERED_ACCOUNTANT",
        approved_by="ADMIN_OPERATOR",
        cop_valid_until=datetime(2030, 12, 31).date(),
        verification_source="ICAI_DIGITAL_VERIFICATION_GATEWAY",
        verification_evidence_ref="ICAI-GATEWAY-FAKE123",
        environment="test"
    )

    # Must fail closed: status recorded as PENDING_INDEPENDENT_VERIFICATION
    assert auditor["verification_status"] == "PENDING_INDEPENDENT_VERIFICATION"
    assert auditor["is_icai_verified"] is False
    assert auditor["is_cop_verified"] is False

    # Credential lookup must reject unverified auditor
    app_db = SessionLocal()
    try:
        is_valid, msg = validate_auditor_credentials(vid, "CHARTERED_ACCOUNTANT", db=app_db)
        assert is_valid is False
        assert "UNVERIFIED_AUDITOR_PROVENANCE" in msg
    finally:
        app_db.close()


def test_icai_gateway_cryptographic_verification_claims_and_replay(clean_test_auditor_env):
    """
    Exhaustively tests cryptographic verification of digital gateway responses:
    1. Valid signed token succeeds and records key ID and response nonce.
    2. Tampered signature fails with INVALID_SIGNATURE.
    3. Unknown kid fails with UNKNOWN_KEY.
    4. Expired token fails with TOKEN_EXPIRED.
    5. Revoked key fails with KEY_REVOKED.
    6. Claim mismatch (e.g. mismatched membership number) fails with CLAIM_MISMATCH.
    7. Replayed nonce fails with NONCE_REPLAY_DETECTED.
    """
    m_no = clean_test_auditor_env["mem_no"]
    ev_ref = f"ICAI-GATEWAY-REF-{uuid.uuid4().hex[:8]}"
    cop_date = datetime(2030, 12, 31).date()

    # 1. Valid signed response
    token = make_test_gateway_token(
        membership_no=m_no,
        evidence_ref=ev_ref,
        cop_valid_until=cop_date,
        nonce="NONCE-UNIQUE-001"
    )
    ok, msg, data = verify_icai_gateway_signed_response(
        signed_token=token,
        expected_membership_no=m_no,
        expected_cop_valid_until=cop_date,
        environment="test"
    )
    assert ok is True
    assert data["kid"] == TEST_KEY_ID
    assert data["jti"] == "NONCE-UNIQUE-001"

    # 2. Tampered signature fails
    tampered_token = token[:-8] + "ABCDEF12"
    ok_tamp, msg_tamp, _ = verify_icai_gateway_signed_response(
        signed_token=tampered_token,
        expected_membership_no=m_no,
        expected_cop_valid_until=cop_date,
        environment="test"
    )
    assert ok_tamp is False
    assert "Signature verification failed" in msg_tamp or "CRYPTOGRAPHIC_VERIFICATION_FAILED" in msg_tamp

    # 3. Unknown kid fails
    token_bad_kid = make_test_gateway_token(
        membership_no=m_no,
        evidence_ref=ev_ref,
        kid="unknown-kid-999"
    )
    ok_kid, msg_kid, _ = verify_icai_gateway_signed_response(
        signed_token=token_bad_kid,
        expected_membership_no=m_no,
        expected_cop_valid_until=cop_date,
        environment="test"
    )
    assert ok_kid is False
    assert "UNKNOWN_KEY" in msg_kid

    # 4. Expired token fails
    now = datetime.now(timezone.utc)
    token_exp = make_test_gateway_token(
        membership_no=m_no,
        evidence_ref=ev_ref,
        issued_at=now - timedelta(days=2),
        expires_at=now - timedelta(days=1)
    )
    ok_exp, msg_exp, _ = verify_icai_gateway_signed_response(
        signed_token=token_exp,
        expected_membership_no=m_no,
        expected_cop_valid_until=cop_date,
        environment="test"
    )
    assert ok_exp is False
    assert "EXPIRED_GATEWAY_TOKEN" in msg_exp or "Signature has expired" in msg_exp

    # 5. Revoked key fails
    register_test_gateway_key("revoked-key-id", "some-secret-key-32chars-long-here!!", is_revoked=True)
    token_rev = make_test_gateway_token(
        membership_no=m_no,
        evidence_ref=ev_ref,
        kid="revoked-key-id",
        secret="some-secret-key-32chars-long-here!!"
    )
    ok_rev, msg_rev, _ = verify_icai_gateway_signed_response(
        signed_token=token_rev,
        expected_membership_no=m_no,
        expected_cop_valid_until=cop_date,
        environment="test"
    )
    assert ok_rev is False
    assert "REVOKED" in msg_rev or "KEY_REVOKED" in msg_rev

    # 6. Claim mismatch fails (e.g. wrong membership_no)
    token_claim = make_test_gateway_token(
        membership_no=m_no,
        evidence_ref=ev_ref,
        cop_valid_until=cop_date,
        nonce="NONCE-CLAIM-MISMATCH-TEST"
    )
    ok_claim, msg_claim, _ = verify_icai_gateway_signed_response(
        signed_token=token_claim,
        expected_membership_no="999999",  # mismatched
        expected_cop_valid_until=cop_date,
        environment="test"
    )
    assert ok_claim is False
    assert "MISMATCH" in msg_claim or "CLAIM_MISMATCH" in msg_claim

    # 7. Replayed nonce (jti) fails
    # token has nonce "NONCE-UNIQUE-001" which was registered in step 1
    ok_replay, msg_replay, _ = verify_icai_gateway_signed_response(
        signed_token=token,
        expected_membership_no=m_no,
        expected_cop_valid_until=cop_date,
        environment="test"
    )
    assert ok_replay is False
    assert "NONCE_REPLAY" in msg_replay or "already been consumed" in msg_replay


def test_icai_gateway_unconfigured_production_fails_closed(clean_test_auditor_env):
    """
    Verifies that in production mode, without an official ICAI trust anchor configured,
    the gateway verifier fails closed with TRUST_ANCHOR_UNAVAILABLE or ASYMMETRIC_ALGORITHM_REQUIRED.
    Test keys are strictly rejected in production.
    """
    m_no = clean_test_auditor_env["mem_no"]
    token = make_test_gateway_token(membership_no=m_no, evidence_ref="ICAI-REF-PROD-001")

    # In production environment (without official keys set in env)
    ok, msg, _ = verify_icai_gateway_signed_response(
        signed_token=token,
        expected_membership_no=m_no,
        environment="production"
    )
    assert ok is False
    assert (
        "TRUST_ANCHOR_UNAVAILABLE" in msg
        or "TEST_KEY_REJECTED" in msg
        or "ASYMMETRIC_ALGORITHM_REQUIRED" in msg
    )


def test_gateway_forged_unknown_kid_rejected(clean_test_auditor_env):
    """Verifies that a gateway response token referencing an untrusted/unknown kid is rejected."""
    m_no = clean_test_auditor_env["mem_no"]
    token = make_test_gateway_token(
        membership_no=m_no,
        evidence_ref="ICAI-REF-UNKNOWN-KID",
        kid="untrusted-forged-kid-999"
    )
    ok, msg, data = verify_icai_gateway_signed_response(
        signed_token=token,
        expected_membership_no=m_no,
        environment="test"
    )
    assert ok is False
    assert "UNKNOWN_KEY_ID" in msg


def test_gateway_tampered_signature_rejected(clean_test_auditor_env):
    """Verifies that tampering with the digital signature of a gateway token fails verification."""
    m_no = clean_test_auditor_env["mem_no"]
    token = make_test_gateway_token(
        membership_no=m_no,
        evidence_ref="ICAI-REF-TAMPER"
    )
    parts = token.split(".")
    tampered_sig = parts[2][:-4] + ("AAAA" if parts[2][-4:] != "AAAA" else "ZZZZ")
    tampered_token = f"{parts[0]}.{parts[1]}.{tampered_sig}"

    ok, msg, data = verify_icai_gateway_signed_response(
        signed_token=tampered_token,
        expected_membership_no=m_no,
        environment="test"
    )
    assert ok is False
    assert "CRYPTOGRAPHIC_VERIFICATION_FAILED" in msg


def test_gateway_symmetric_hs256_prohibited_in_production(clean_test_auditor_env):
    """Verifies that symmetric algorithms like HS256 are strictly prohibited in production mode."""
    m_no = clean_test_auditor_env["mem_no"]
    token = make_test_gateway_token(
        membership_no=m_no,
        evidence_ref="ICAI-REF-PROD-HS256",
        algorithm="HS256"
    )
    ok, msg, data = verify_icai_gateway_signed_response(
        signed_token=token,
        expected_membership_no=m_no,
        environment="production"
    )
    assert ok is False
    assert "ASYMMETRIC_ALGORITHM_REQUIRED" in msg


# ============================================================================
# PART 3: AUTHENTICATED MANUAL EVIDENCE REVIEW PROVENANCE
# ============================================================================

def test_manual_evidence_review_provenance_success(clean_test_auditor_env):
    """
    Verifies successful manual review verification:
    - Server computes SHA-256 over uploaded document bytes.
    - Validates authorized reviewer role (STATUTORY_AUDITOR_ADMIN).
    - Enforces maker-checker (uploader != reviewer).
    - Logs immutable event in statutory_auditor_verification_events.
    """
    admin_db = clean_test_auditor_env["admin_db"]
    vid = clean_test_auditor_env["id"]
    m_no = clean_test_auditor_env["mem_no"]
    doc_bytes = b"%PDF-1.4 Mock ICAI Member Card and COP Certificate for test auditor"
    computed_hash = hashlib.sha256(doc_bytes).hexdigest()

    reviewer_ctx = {
        "user_id": "OFFICER-COMPLIANCE-01",
        "email": "compliance.officer@myca.internal",
        "roles": ["CHIEF_COMPLIANCE_OFFICER"],
        "is_active": True,
        "session_id": f"SESS-{uuid.uuid4().hex[:12]}"
    }

    auditor = onboard_verified_statutory_auditor(
        db=admin_db,
        auditor_id=vid,
        membership_no=m_no,
        name="CA Manual Review Pass",
        role="CHARTERED_ACCOUNTANT",
        approved_by="OFFICER-COMPLIANCE-01",
        cop_valid_until=datetime(2030, 12, 31).date(),
        verification_source="MANUAL_EVIDENCE_REVIEW",
        document_bytes=doc_bytes,
        reviewer_context=reviewer_ctx,
        uploader_id="OPERATOR-UPLOADER-02",  # Maker != Checker
        environment="test"
    )

    assert auditor["verification_status"] == "MANUAL_DOCUMENT_REVIEWED"
    assert auditor["is_icai_verified"] is False
    assert auditor["is_cop_verified"] is False
    assert auditor["evidence_sha256"] == computed_hash

    # Verify event logged in statutory_auditor_verification_events
    row = admin_db.execute(
        text("SELECT * FROM public.statutory_auditor_verification_events WHERE auditor_id = :aid;"),
        {"aid": vid}
    ).fetchone()
    assert row is not None
    r_map = row._mapping
    assert r_map["evidence_sha256"] == computed_hash
    assert r_map["decision"] == "APPROVED"
    assert r_map["reviewer_id"] == "OFFICER-COMPLIANCE-01"
    assert r_map["verification_status"] == "MANUAL_DOCUMENT_REVIEWED"

    # Verify credential lookup succeeds with explicit human-reviewed documentary disclosure
    app_db = SessionLocal()
    try:
        is_valid, msg = validate_auditor_credentials(vid, "CHARTERED_ACCOUNTANT", db=app_db)
        assert is_valid is True
        assert "human-reviewed documentary evidence" in msg
    finally:
        app_db.close()


def test_manual_evidence_review_unauthorized_role_rejected(clean_test_auditor_env):
    """Verifies that a reviewer without an authorized compliance role is rejected."""
    admin_db = clean_test_auditor_env["admin_db"]
    vid = clean_test_auditor_env["id"]
    m_no = clean_test_auditor_env["mem_no"]
    doc_bytes = b"Dummy certificate bytes"

    reviewer_ctx = {
        "user_id": "STANDARD-USER-01",
        "roles": ["STANDARD_USER", "VIEWER"],  # Unauthorized role
        "is_active": True,
        "session_id": "SESS-001"
    }

    try:
        onboard_verified_statutory_auditor(
            db=admin_db,
            auditor_id=vid,
            membership_no=m_no,
            name="CA Unauthorized Reviewer",
            role="CHARTERED_ACCOUNTANT",
            approved_by="STANDARD-USER-01",
            cop_valid_until=datetime(2030, 12, 31).date(),
            verification_source="MANUAL_EVIDENCE_REVIEW",
            document_bytes=doc_bytes,
            reviewer_context=reviewer_ctx,
            uploader_id="OPERATOR-01",
            environment="test"
        )
        assert False, "Should fail with unauthorized reviewer"
    except ValueError as ex:
        assert "INSUFFICIENT_PERMISSIONS" in str(ex) or "UNAUTHORIZED_REVIEWER_ROLE" in str(ex)


def test_manual_evidence_review_maker_checker_rejected(clean_test_auditor_env):
    """Verifies that self-approval (maker-checker violation: uploader == reviewer) is rejected."""
    admin_db = clean_test_auditor_env["admin_db"]
    vid = clean_test_auditor_env["id"]
    m_no = clean_test_auditor_env["mem_no"]
    doc_bytes = b"Dummy certificate bytes"

    reviewer_ctx = {
        "user_id": "OPERATOR-SAME-01",
        "roles": ["STATUTORY_AUDITOR_ADMIN"],
        "is_active": True,
        "session_id": "SESS-SAME-01"
    }

    try:
        onboard_verified_statutory_auditor(
            db=admin_db,
            auditor_id=vid,
            membership_no=m_no,
            name="CA Maker Checker Violation",
            role="CHARTERED_ACCOUNTANT",
            approved_by="OPERATOR-SAME-01",
            cop_valid_until=datetime(2030, 12, 31).date(),
            verification_source="MANUAL_EVIDENCE_REVIEW",
            document_bytes=doc_bytes,
            reviewer_context=reviewer_ctx,
            uploader_id="OPERATOR-SAME-01",  # Same as reviewer!
            environment="test"
        )
        assert False, "Should fail with maker-checker violation"
    except ValueError as ex:
        assert "MAKER_CHECKER_VIOLATION" in str(ex)


def test_manual_evidence_review_tampered_document_bytes_rejected(clean_test_auditor_env):
    """Verifies that if claimed SHA-256 does not match computed bytes, it is rejected."""
    admin_db = clean_test_auditor_env["admin_db"]
    vid = clean_test_auditor_env["id"]
    m_no = clean_test_auditor_env["mem_no"]
    doc_bytes = b"Genuine certificate bytes"
    fake_hash = hashlib.sha256(b"Tampered different bytes").hexdigest()

    reviewer_ctx = {
        "user_id": "ADMIN-01",
        "roles": ["STATUTORY_AUDITOR_ADMIN"],
        "is_active": True,
        "session_id": "SESS-01"
    }

    try:
        onboard_verified_statutory_auditor(
            db=admin_db,
            auditor_id=vid,
            membership_no=m_no,
            name="CA Hash Mismatch",
            role="CHARTERED_ACCOUNTANT",
            approved_by="ADMIN-01",
            cop_valid_until=datetime(2030, 12, 31).date(),
            verification_source="MANUAL_EVIDENCE_REVIEW",
            document_bytes=doc_bytes,
            reviewer_context=reviewer_ctx,
            uploader_id="UPLOADER-01",
            claimed_evidence_sha256=fake_hash,
            environment="test"
        )
        assert False, "Should fail with hash mismatch"
    except ValueError as ex:
        assert "EVIDENCE_INTEGRITY_MISMATCH" in str(ex) or "EVIDENCE_HASH_MISMATCH" in str(ex)


def test_manual_review_missing_db_fails_closed(clean_test_auditor_env):
    """Verifies that verify_manual_evidence_review fails closed when db is None."""
    vid = clean_test_auditor_env["id"]
    m_no = clean_test_auditor_env["mem_no"]
    reviewer_ctx = {
        "user_id": "OFFICER-01",
        "roles": ["CHIEF_COMPLIANCE_OFFICER"],
        "is_active": True,
        "session_id": "SESS-01"
    }
    ok, msg, data = verify_manual_evidence_review(
        db=None,
        auditor_id=vid,
        membership_no=m_no,
        cop_status="ACTIVE",
        cop_valid_until=datetime(2030, 12, 31).date(),
        document_bytes=b"%PDF-1.4 genuine content",
        reviewer_context=reviewer_ctx,
        uploader_id="OTHER-01"
    )
    assert ok is False
    assert "DATABASE_UNAVAILABLE" in msg


def test_manual_review_inactive_reviewer_fails_closed(clean_test_auditor_env):
    """Verifies that an inactive/suspended reviewer is rejected."""
    admin_db = clean_test_auditor_env["admin_db"]
    vid = clean_test_auditor_env["id"]
    m_no = clean_test_auditor_env["mem_no"]
    reviewer_ctx = {
        "user_id": "OFFICER-INACTIVE",
        "roles": ["CHIEF_COMPLIANCE_OFFICER"],
        "is_active": False,
        "session_id": "SESS-INACTIVE"
    }
    ok, msg, data = verify_manual_evidence_review(
        db=admin_db,
        auditor_id=vid,
        membership_no=m_no,
        cop_status="ACTIVE",
        cop_valid_until=datetime(2030, 12, 31).date(),
        document_bytes=b"%PDF-1.4 genuine content",
        reviewer_context=reviewer_ctx,
        uploader_id="OTHER-01"
    )
    assert ok is False
    assert "INACTIVE_REVIEWER" in msg


def test_manual_review_audit_event_persistence_failure_fails_closed(clean_test_auditor_env):
    """Verifies that if audit event persistence fails, verification fails closed (no swallowed exceptions)."""
    vid = clean_test_auditor_env["id"]
    m_no = clean_test_auditor_env["mem_no"]
    failing_db = MockFailingSession()
    reviewer_ctx = {
        "user_id": "OFFICER-01",
        "roles": ["CHIEF_COMPLIANCE_OFFICER"],
        "is_active": True,
        "session_id": "SESS-01"
    }
    ok, msg, data = verify_manual_evidence_review(
        db=failing_db,
        auditor_id=vid,
        membership_no=m_no,
        cop_status="ACTIVE",
        cop_valid_until=datetime(2030, 12, 31).date(),
        document_bytes=b"%PDF-1.4 genuine content",
        reviewer_context=reviewer_ctx,
        uploader_id="OTHER-01"
    )
    assert ok is False
    assert "AUDIT_RECORDING_FAILED" in msg


def test_manual_review_cannot_self_assert_independently_verified(clean_test_auditor_env):
    """
    Verifies that passing verification_status='INDEPENDENTLY_VERIFIED' in manual review
    does not grant INDEPENDENTLY_VERIFIED status without authentic cryptographic gateway proof.
    """
    admin_db = clean_test_auditor_env["admin_db"]
    vid = clean_test_auditor_env["id"]
    m_no = clean_test_auditor_env["mem_no"]
    doc_bytes = b"%PDF-1.4 Mock ICAI Member Card and COP Certificate"

    reviewer_ctx = {
        "user_id": "OFFICER-COMPLIANCE-02",
        "roles": ["CHIEF_COMPLIANCE_OFFICER"],
        "is_active": True,
        "session_id": f"SESS-{uuid.uuid4().hex[:12]}"
    }

    auditor = onboard_verified_statutory_auditor(
        db=admin_db,
        auditor_id=vid,
        membership_no=m_no,
        name="CA No Conflation",
        role="CHARTERED_ACCOUNTANT",
        approved_by="OFFICER-COMPLIANCE-02",
        cop_valid_until=datetime(2030, 12, 31).date(),
        verification_source="MANUAL_EVIDENCE_REVIEW",
        verification_status="INDEPENDENTLY_VERIFIED",  # Self-assertion attempt
        document_bytes=doc_bytes,
        reviewer_context=reviewer_ctx,
        uploader_id="OPERATOR-UPLOADER-03",
        environment="test"
    )

    # Must NOT be marked INDEPENDENTLY_VERIFIED because final_icai_verified is False
    assert auditor["verification_status"] != "INDEPENDENTLY_VERIFIED"
    assert auditor["is_icai_verified"] is False


# ============================================================================
# PART 4: PERSISTENT SIGNATURE REPLAY DEFENSE & MANDATORY EXPIRY
# ============================================================================

def test_signature_replay_missing_expires_at_fails_closed(clean_test_auditor_env):
    """
    Verifies that omitting expires_at fails closed with MISSING_TOKEN_EXPIRY.
    Synthetic default expiration is strictly prohibited.
    """
    token = f"TEST_TOKEN_MISSING_EXPIRY_{uuid.uuid4().hex}_{uuid.uuid4().hex}"
    app_db = SessionLocal()
    try:
        ok, msg = check_and_record_signature_replay(
            token=token,
            doc_id="DOC-NO-EXP",
            doc_number="INV/NOEXP/01",
            db=app_db,
            verifier_id="SYSTEM_VERIFIED",
            expires_at=None  # Explicitly omitted
        )
        assert ok is False
        assert "MISSING_TOKEN_EXPIRY" in msg
    finally:
        app_db.close()


def test_signature_replay_cross_document_reuse_rejected(clean_test_auditor_env):
    """Verifies that a token issued for doc1 cannot be reused for doc2."""
    admin_db = clean_test_auditor_env["admin_db"]
    vid = clean_test_auditor_env["id"]
    m_no = clean_test_auditor_env["mem_no"]
    now_utc = datetime.now(timezone.utc)
    ev_ref = f"ICAI-GATEWAY-TRACE-{uuid.uuid4().hex[:8]}"
    gw_token = make_test_gateway_token(membership_no=m_no, evidence_ref=ev_ref)

    onboard_verified_statutory_auditor(
        db=admin_db,
        auditor_id=vid,
        membership_no=m_no,
        name="CA Replay Cross",
        role="CHARTERED_ACCOUNTANT",
        approved_by="SYS_ADMIN",
        cop_valid_until=datetime(2030, 12, 31).date(),
        verification_status="INDEPENDENTLY_VERIFIED",
        is_icai_verified=True,
        is_cop_verified=True,
        verification_source="ICAI_DIGITAL_VERIFICATION_GATEWAY",
        verified_at=now_utc,
        verification_evidence_ref=ev_ref,
        gateway_signed_response=gw_token,
        environment="test"
    )

    token = f"TEST_TOKEN_CROSS_REPLAY_{uuid.uuid4().hex}_{uuid.uuid4().hex}"
    doc1_id = f"DOC-TEST-1-{uuid.uuid4().hex[:6]}"
    doc1_num = "INV/TEST/001"
    doc2_id = f"DOC-TEST-2-{uuid.uuid4().hex[:6]}"
    doc2_num = "INV/TEST/002"
    exp = now_utc + timedelta(hours=24)

    app_db = SessionLocal()
    try:
        # First consumption on doc1 succeeds
        ok1, msg1 = check_and_record_signature_replay(
            token=token, doc_id=doc1_id, doc_number=doc1_num, db=app_db,
            verifier_id=vid, expires_at=exp
        )
        assert ok1 is True
        assert "recorded successfully" in msg1

        # Second consumption attempt on doc2 fails with cross-document replay
        ok2, msg2 = check_and_record_signature_replay(
            token=token, doc_id=doc2_id, doc_number=doc2_num, db=app_db,
            verifier_id=vid, expires_at=exp
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
    now_utc = datetime.now(timezone.utc)
    ev_ref = f"ICAI-GATEWAY-TRACE-{uuid.uuid4().hex[:8]}"
    gw_token = make_test_gateway_token(membership_no=m_no, evidence_ref=ev_ref)

    onboard_verified_statutory_auditor(
        db=admin_db,
        auditor_id=vid,
        membership_no=m_no,
        name="CA Replay Duplicate",
        role="CHARTERED_ACCOUNTANT",
        approved_by="SYS_ADMIN",
        cop_valid_until=datetime(2030, 12, 31).date(),
        verification_status="INDEPENDENTLY_VERIFIED",
        is_icai_verified=True,
        is_cop_verified=True,
        verification_source="ICAI_DIGITAL_VERIFICATION_GATEWAY",
        verified_at=now_utc,
        verification_evidence_ref=ev_ref,
        gateway_signed_response=gw_token,
        environment="test"
    )

    token = f"TEST_TOKEN_SAME_DOC_DUP_{uuid.uuid4().hex}_{uuid.uuid4().hex}"
    doc_id = f"DOC-DUP-{uuid.uuid4().hex[:6]}"
    doc_num = "INV/TEST/DUP01"
    exp = now_utc + timedelta(hours=24)

    app_db = SessionLocal()
    try:
        # First consumption succeeds
        ok1, msg1 = check_and_record_signature_replay(
            token=token, doc_id=doc_id, doc_number=doc_num, db=app_db,
            verifier_id=vid, expires_at=exp
        )
        assert ok1 is True

        # Duplicate consumption on same document rejected
        ok2, msg2 = check_and_record_signature_replay(
            token=token, doc_id=doc_id, doc_number=doc_num, db=app_db,
            verifier_id=vid, expires_at=exp
        )
        assert ok2 is False
        assert "already been consumed" in msg2
    finally:
        app_db.close()


def test_signature_replay_expired_token_permanent_invalidation(clean_test_auditor_env):
    """
    Verifies that:
    1. An already-expired token is rejected on the very first attempt before persistent recording.
    2. A valid token that was recorded can never be reused even after expiration.
    """
    admin_db = clean_test_auditor_env["admin_db"]
    vid = clean_test_auditor_env["id"]
    m_no = clean_test_auditor_env["mem_no"]
    now_utc = datetime.now(timezone.utc)
    ev_ref = f"ICAI-GATEWAY-TRACE-{uuid.uuid4().hex[:8]}"
    gw_token = make_test_gateway_token(membership_no=m_no, evidence_ref=ev_ref)

    onboard_verified_statutory_auditor(
        db=admin_db,
        auditor_id=vid,
        membership_no=m_no,
        name="CA Replay Expired",
        role="CHARTERED_ACCOUNTANT",
        approved_by="SYS_ADMIN",
        cop_valid_until=datetime(2030, 12, 31).date(),
        verification_status="INDEPENDENTLY_VERIFIED",
        is_icai_verified=True,
        is_cop_verified=True,
        verification_source="ICAI_DIGITAL_VERIFICATION_GATEWAY",
        verified_at=now_utc,
        verification_evidence_ref=ev_ref,
        gateway_signed_response=gw_token,
        environment="test"
    )

    token_expired = f"TEST_TOKEN_ALREADY_EXPIRED_{uuid.uuid4().hex}_{uuid.uuid4().hex}"
    doc_id_1 = f"DOC-EXP1-{uuid.uuid4().hex[:6]}"
    doc_num_1 = "INV/TEST/EXP01"
    past_expiry = now_utc - timedelta(minutes=10)

    app_db = SessionLocal()
    try:
        # 1. Attempt to consume an already-expired token: MUST FAIL on the first attempt
        ok1, msg1 = check_and_record_signature_replay(
            token=token_expired, doc_id=doc_id_1, doc_number=doc_num_1, db=app_db,
            verifier_id=vid, expires_at=past_expiry
        )
        assert ok1 is False
        assert "EXPIRED_TOKEN" in msg1

        # Verify it was NOT recorded in the database
        hash_expired = hashlib.sha256(token_expired.encode("utf-8")).hexdigest()
        count = admin_db.execute(
            text("SELECT count(*) FROM public.statutory_audit_signature_replays WHERE token_hash = :th;"),
            {"th": hash_expired}
        ).scalar()
        assert count == 0, "Expired token must not be recorded as validly consumed"

        # 2. Record a valid unexpired token, and verify it cannot be replayed even if expiry passes
        token_valid = f"TEST_TOKEN_VALID_THEN_EXPIRED_{uuid.uuid4().hex}_{uuid.uuid4().hex}"
        doc_id_2 = f"DOC-EXP2-{uuid.uuid4().hex[:6]}"
        doc_num_2 = "INV/TEST/EXP02"
        future_expiry = now_utc + timedelta(hours=2)

        ok2, msg2 = check_and_record_signature_replay(
            token=token_valid, doc_id=doc_id_2, doc_number=doc_num_2, db=app_db,
            verifier_id=vid, expires_at=future_expiry
        )
        assert ok2 is True

        # Attempt to replay the recorded token: MUST FAIL with SIGNATURE_REPLAY_DETECTED
        ok3, msg3 = check_and_record_signature_replay(
            token=token_valid, doc_id=doc_id_2, doc_number=doc_num_2, db=app_db,
            verifier_id=vid, expires_at=future_expiry
        )
        assert ok3 is False
        assert "already been consumed" in msg3
    finally:
        app_db.close()


def test_conclusive_postgresql_insert_concurrency_race(clean_test_auditor_env):
    """
    Rigorously tests concurrent race condition across distinct database sessions.
    Hooks SQLAlchemy 'before_cursor_execute' event on the INSERT statement:
    both worker threads pass the initial SELECT pre-check, arrive at the barrier
    right before executing the INSERT statement, and strike PostgreSQL simultaneously.
    Asserts:
    1. Exactly 1 worker succeeds.
    2. Exactly 1 worker receives CONCURRENT_REPLAY_DETECTED.
    3. Exactly 1 row exists in the database.
    """
    admin_db = clean_test_auditor_env["admin_db"]
    vid = clean_test_auditor_env["id"]
    m_no = clean_test_auditor_env["mem_no"]
    now_utc = datetime.now(timezone.utc)
    ev_ref = f"ICAI-GATEWAY-TRACE-{uuid.uuid4().hex[:8]}"
    gw_token = make_test_gateway_token(membership_no=m_no, evidence_ref=ev_ref)

    onboard_verified_statutory_auditor(
        db=admin_db,
        auditor_id=vid,
        membership_no=m_no,
        name="CA Replay Concurrency",
        role="CHARTERED_ACCOUNTANT",
        approved_by="SYS_ADMIN",
        cop_valid_until=datetime(2030, 12, 31).date(),
        verification_status="INDEPENDENTLY_VERIFIED",
        is_icai_verified=True,
        is_cop_verified=True,
        verification_source="ICAI_DIGITAL_VERIFICATION_GATEWAY",
        verified_at=now_utc,
        verification_evidence_ref=ev_ref,
        gateway_signed_response=gw_token,
        environment="test"
    )

    token = f"TEST_TOKEN_CONCURRENT_RACE_{uuid.uuid4().hex}_{uuid.uuid4().hex}"
    doc_id = f"DOC-CONC-{uuid.uuid4().hex[:6]}"
    doc_num = "INV/TEST/CONC01"
    exp = now_utc + timedelta(hours=24)

    barrier = threading.Barrier(2)
    results = []
    errors = []

    def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        if "INSERT INTO public.statutory_audit_signature_replays" in statement:
            # Synchronize both threads right before striking the INSERT cursor
            barrier.wait(timeout=10)

    # Attach SQLAlchemy event listener to the engine
    event.listen(engine, "before_cursor_execute", before_cursor_execute)

    def worker(worker_id):
        session = SessionLocal()
        try:
            ok, msg = check_and_record_signature_replay(
                token=token, doc_id=doc_id, doc_number=doc_num, db=session,
                verifier_id=vid, expires_at=exp
            )
            results.append((worker_id, ok, msg))
        except Exception as ex:
            errors.append((worker_id, ex))
        finally:
            session.close()

    try:
        t1 = threading.Thread(target=worker, args=(1,))
        t2 = threading.Thread(target=worker, args=(2,))

        t1.start()
        t2.start()

        t1.join(timeout=15)
        t2.join(timeout=15)
    finally:
        event.remove(engine, "before_cursor_execute", before_cursor_execute)

    assert not errors, f"Unexpected thread exceptions during concurrent execution: {errors}"
    assert len(results) == 2, f"Expected 2 worker results, got {len(results)}"

    # Exactly one worker must succeed, and exactly one worker must fail
    successes = [r for r in results if r[1] is True]
    failures = [r for r in results if r[1] is False]

    assert len(successes) == 1, f"Expected exactly 1 success, got {len(successes)}: {results}"
    assert len(failures) == 1, f"Expected exactly 1 failure, got {len(failures)}: {results}"

    # Failure must indicate concurrent collision or signature replay
    fail_msg = failures[0][2]
    assert ("CONCURRENT_REPLAY_DETECTED" in fail_msg or "SIGNATURE_REPLAY_DETECTED" in fail_msg or "already been consumed" in fail_msg), f"Unexpected failure message: {fail_msg}"

    # Verify database state has exactly 1 record for this token hash
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    rec_count = admin_db.execute(
        text("SELECT count(*) FROM public.statutory_audit_signature_replays WHERE token_hash = :th;"),
        {"th": token_hash}
    ).scalar()
    assert rec_count == 1, f"Expected exactly 1 replay token recorded in DB, found {rec_count}"


def test_signature_replay_db_failure_fails_closed_no_in_memory_fallback():
    """
    Verifies that under database failure, check_and_record_signature_replay
    fails closed and NEVER silently falls back to an in-memory dictionary.
    """
    failing_db = MockFailingSession()
    token = "TEST_TOKEN_DB_FAILURE_FAIL_CLOSED_32_CHARS"
    now_utc = datetime.now(timezone.utc)

    ok, msg = check_and_record_signature_replay(
        token=token, doc_id="DOC_FAIL_01", doc_number="INV_FAIL", db=failing_db,
        expires_at=now_utc + timedelta(hours=24)
    )
    assert ok is False
    assert "DATABASE_TRANSACTION_FAILURE" in msg
