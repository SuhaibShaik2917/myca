"""
MyCA Statutory Auditor Verification — Authenticated Manual Evidence Reviewer.

Implements controlled manual evidence verification with durable persistence and honest status:
1. Server-side Reviewer Authentication:
   - Derives reviewer identity exclusively from the validated authenticated principal token.
   - Validates that the reviewer user exists in public.global_users and is_active is True.
   - Rejects unauthenticated callers, forged identities, missing users, or inactive/suspended accounts.
2. Server-side Authorization & Role Lookup:
   - Queries authoritative role from public.organization_memberships in the database.
   - Requires active membership and administrative role ('admin' or 'owner').
   - NEVER trusts caller-supplied roles, is_active, can_verify_statutory_auditors, or approved_by.
3. Separation of Duties (Maker-Checker):
   - Reviewer cannot approve their own submitted evidence (uploader_id != reviewer_id / reviewer_email).
4. Documentary Evidence Verification:
   - Server computes SHA-256 directly from raw uploaded bytes.
   - Validates file size (16B - 10MB) and allowed MIME types (PDF, PNG, JPEG).
   - Client-supplied hash alone is never trusted.
5. Durable Evidence Recording:
   - Persists immutable event into public.statutory_auditor_verification_events.
   - FAILS CLOSED: Any database failure immediately rolls back and aborts verification (no swallowed exceptions).
6. Honest Status Nomenclature & Evidence Assurance:
   - Explicitly records outcome as 'MANUAL_DOCUMENT_REVIEWED'.
   - NEVER conflated with 'INDEPENDENTLY_VERIFIED' (which is reserved exclusively for cryptographic issuer verification).
   - Clear disclosure that uploaded evidence is human-reviewed and not cryptographically certified by ICAI gateway.
"""

import os
import uuid as _py_uuid
import hashlib
from datetime import datetime, timezone, timedelta, date
from typing import Dict, Any, Optional, Tuple
from sqlalchemy import text

from app.core.security import decode_access_token

AUTHORIZED_ORGANIZATION_ROLES = ["admin", "owner"]
AUTHORIZED_REVIEWER_ROLES = AUTHORIZED_ORGANIZATION_ROLES
ALLOWED_DOCUMENT_TYPES = ["application/pdf", "image/png", "image/jpeg"]
MIN_DOCUMENT_SIZE_BYTES = 16
MAX_DOCUMENT_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB


def _is_valid_uuid(val: str) -> bool:
    try:
        _py_uuid.UUID(str(val).strip())
        return True
    except (ValueError, AttributeError, TypeError):
        return False


def verify_manual_evidence_review(
    db: Any,
    auditor_id: str,
    membership_no: str,
    cop_status: str,
    cop_valid_until: date,
    document_bytes: Optional[bytes],
    reviewer_context: Optional[Dict[str, Any]] = None,
    uploader_id: Optional[str] = None,
    claimed_evidence_sha256: Optional[str] = None,
    document_content_type: str = "application/pdf",
    verification_notes: Optional[str] = None,
    auth_token: Optional[str] = None
) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Rigorously validates a manual statutory auditor verification review with durable audit recording.
    Requires server-side authenticated reviewer identity and active organization role.
    """
    if not auditor_id or not membership_no:
        return False, "INVALID_INPUT: auditor_id and membership_no are mandatory.", {}

    # Mandatory database session requirement
    if db is None:
        return False, "DATABASE_UNAVAILABLE: Active database session required for reviewer authentication and verification.", {}

    # 1. Extract Bearer access token from auth_token or reviewer_context
    raw_token = auth_token
    if not raw_token and isinstance(reviewer_context, dict):
        raw_token = (
            reviewer_context.get("access_token")
            or reviewer_context.get("token")
            or reviewer_context.get("bearer_token")
            or reviewer_context.get("auth_token")
        )

    if not raw_token or not isinstance(raw_token, str) or not raw_token.strip():
        return False, "UNAUTHENTICATED_REVIEWER: Valid bearer access token is required for reviewer authentication.", {}

    clean_token = raw_token.strip()

    # 2. Cryptographically validate access token against server SECRET_KEY
    try:
        claims = decode_access_token(clean_token)
    except ValueError as ex:
        return False, f"INVALID_REVIEWER_SESSION: Access token validation failed: {ex}", {}
    except Exception as ex:
        return False, f"INVALID_REVIEWER_SESSION: Token decode error: {ex}", {}

    reviewer_user_id = str(claims.get("sub") or "").strip()
    reviewer_org_id = str(claims.get("org_id") or "").strip()

    if not reviewer_user_id or not _is_valid_uuid(reviewer_user_id):
        return False, "INVALID_REVIEWER_IDENTITY: Reviewer token subject is not a valid UUID.", {}

    if not reviewer_org_id or not _is_valid_uuid(reviewer_org_id):
        return False, "INVALID_REVIEWER_IDENTITY: Reviewer token org_id is not a valid UUID.", {}

    u_uuid = _py_uuid.UUID(reviewer_user_id)
    o_uuid = _py_uuid.UUID(reviewer_org_id)

    # 3. Authoritative database user verification (global_users)
    try:
        user_stmt = text("SELECT id, email, full_name, is_active FROM public.global_users WHERE id = :uid;")
        user_row = db.execute(user_stmt, {"uid": u_uuid}).fetchone()
    except Exception as ex:
        if hasattr(db, "rollback"):
            db.rollback()
        return False, f"DATABASE_ERROR: Failed to query global_users: {ex}", {}

    if not user_row:
        return False, f"UNREGISTERED_REVIEWER: Reviewer user '{reviewer_user_id}' does not exist in global_users.", {}

    u_map = user_row._mapping
    if u_map.get("is_active") is not True:
        return False, f"INACTIVE_REVIEWER: Reviewer account '{reviewer_user_id}' is inactive or suspended in global_users.", {}

    reviewer_email = str(u_map.get("email") or "").strip()
    reviewer_name = str(u_map.get("full_name") or "").strip()

    # 4. Authoritative server-side role lookup in organization_memberships
    # NEVER trust caller-supplied roles, is_active, can_verify_statutory_auditors, or approved_by!
    try:
        mem_stmt = text("""
            SELECT role, status FROM public.organization_memberships 
            WHERE user_id = :uid AND org_id = :oid;
        """)
        mem_row = db.execute(mem_stmt, {"uid": u_uuid, "oid": o_uuid}).fetchone()
    except Exception as ex:
        if hasattr(db, "rollback"):
            db.rollback()
        return False, f"DATABASE_ERROR: Failed to query organization_memberships: {ex}", {}

    if not mem_row:
        return False, f"INSUFFICIENT_PERMISSIONS: Reviewer '{reviewer_user_id}' has no membership in organization '{reviewer_org_id}'.", {}

    m_map = mem_row._mapping
    if str(m_map.get("status") or "").strip().lower() != "active":
        return False, f"INACTIVE_REVIEWER_MEMBERSHIP: Reviewer membership status is '{m_map.get('status')}'; must be active.", {}

    auth_role = str(m_map.get("role") or "").strip().lower()
    if auth_role not in AUTHORIZED_ORGANIZATION_ROLES:
        return False, f"UNAUTHORIZED_REVIEWER_ROLE: Role '{auth_role}' is not authorized to verify statutory auditors (requires admin or owner).", {}

    # 5. Session reference derived directly from validated token
    session_ref = str(claims.get("jti") or f"TOKEN-SESS:{hashlib.sha256(clean_token.encode()).hexdigest()[:16]}").strip()

    # 6. Separation of Duties (Maker-Checker): Reviewer cannot approve their own submitted evidence
    if not uploader_id or not str(uploader_id).strip():
        return False, "MISSING_UPLOADER_ID: uploader_id is mandatory to verify maker-checker separation.", {}
    clean_uploader = str(uploader_id).strip().lower()
    if clean_uploader in (reviewer_user_id.lower(), reviewer_email.lower()):
        return False, "MAKER_CHECKER_VIOLATION: Reviewer cannot approve their own submitted auditor evidence (self-approval prohibited).", {}

    # 7. Documentary Evidence Validation
    if not document_bytes or not isinstance(document_bytes, (bytes, bytearray)):
        return False, "MISSING_DOCUMENT_BYTES: Actual uploaded documentary evidence bytes are required for manual verification.", {}

    doc_len = len(document_bytes)
    if doc_len < MIN_DOCUMENT_SIZE_BYTES:
        return False, f"DOCUMENT_TOO_SMALL: Uploaded document size ({doc_len} bytes) is below minimum allowed ({MIN_DOCUMENT_SIZE_BYTES} bytes).", {}
    if doc_len > MAX_DOCUMENT_SIZE_BYTES:
        return False, f"DOCUMENT_TOO_LARGE: Uploaded document size ({doc_len} bytes) exceeds maximum allowed ({MAX_DOCUMENT_SIZE_BYTES} bytes).", {}

    if document_content_type not in ALLOWED_DOCUMENT_TYPES:
        return False, f"INVALID_CONTENT_TYPE: Document type '{document_content_type}' is not an authorized statutory evidence type.", {}
    if document_content_type == "application/pdf" and not bytes(document_bytes).startswith(b"%PDF-"):
        return False, "INVALID_DOCUMENT_MAGIC_BYTES: Uploaded PDF evidence failed %PDF- signature validation.", {}
    if document_content_type == "image/png" and not bytes(document_bytes).startswith(b"\x89PNG\r\n\x1a\n"):
        return False, "INVALID_DOCUMENT_MAGIC_BYTES: Uploaded PNG evidence failed PNG signature validation.", {}
    if document_content_type in ("image/jpeg", "image/jpg") and not bytes(document_bytes).startswith(b"\xff\xd8\xff"):
        return False, "INVALID_DOCUMENT_MAGIC_BYTES: Uploaded JPEG evidence failed JPEG signature validation.", {}

    # Compute SHA-256 strictly server-side from raw bytes
    server_sha256 = hashlib.sha256(document_bytes).hexdigest()

    if claimed_evidence_sha256:
        cleaned_claimed = claimed_evidence_sha256.strip().lower()
        if cleaned_claimed != server_sha256:
            return False, f"EVIDENCE_INTEGRITY_MISMATCH: Server-computed hash '{server_sha256}' does not match claimed hash '{cleaned_claimed}'.", {}

    # 8. COP Credential Validation
    clean_cop_status = str(cop_status or "").strip().upper()
    if clean_cop_status != "ACTIVE":
        return False, f"INACTIVE_COP: Certificate of Practice status is '{clean_cop_status}'; must be ACTIVE.", {}

    if not cop_valid_until:
        return False, "MISSING_COP_VALIDITY: cop_valid_until date is mandatory.", {}

    c_until = cop_valid_until if isinstance(cop_valid_until, date) else date.fromisoformat(str(cop_valid_until))
    if c_until <= date.today():
        return False, f"EXPIRED_COP: Certificate of Practice expired on {c_until}.", {}

    now_utc = datetime.now(timezone.utc)
    evidence_ref = f"MANUAL-EVIDENCE-SHA256:{server_sha256[:16].upper()}"
    authoritative_approved_by = reviewer_email or reviewer_user_id

    # 9. Durable Evidence Recording into statutory_auditor_verification_events (Fail Closed on DB error)
    try:
        event_stmt = text("""
            INSERT INTO public.statutory_auditor_verification_events (
                auditor_id, membership_no, verification_method, verification_status,
                evidence_ref, evidence_sha256, reviewer_id, reviewer_session_ref,
                decision, decision_reason, created_at
            ) VALUES (
                :aid, :mem, 'MANUAL_REVIEW', 'MANUAL_DOCUMENT_REVIEWED',
                :eref, :esha, :rid, :sref,
                'APPROVED', :reason, :now
            );
        """)
        db.execute(event_stmt, {
            "aid": auditor_id.strip(),
            "mem": membership_no.strip(),
            "eref": evidence_ref,
            "esha": server_sha256,
            "rid": reviewer_user_id,
            "sref": session_ref,
            "reason": verification_notes or f"Authenticated manual documentary verification approved by {authoritative_approved_by}",
            "now": now_utc
        })
    except Exception as ex:
        if hasattr(db, "rollback"):
            db.rollback()
        return False, f"AUDIT_RECORDING_FAILED: Failed to durably record verification event in database: {ex}", {}

    verified_data = {
        "membership_no": membership_no.strip(),
        "cop_status": clean_cop_status,
        "cop_valid_until": c_until,
        "evidence_ref": evidence_ref,
        "evidence_sha256": server_sha256,
        "reviewer_id": reviewer_user_id,
        "reviewer_session_ref": session_ref,
        "approved_by": authoritative_approved_by,
        "verified_at": now_utc,
        "verification_source": "MANUAL_EVIDENCE_REVIEW",
        "verification_status": "MANUAL_DOCUMENT_REVIEWED",
        "is_icai_verified": False,
        "is_cop_verified": False,
        "is_human_reviewed": True
    }

    return True, f"Auditor documentary evidence (SHA-256: {server_sha256[:16]}) human-reviewed by {authoritative_approved_by}; not cryptographically verified by ICAI gateway.", verified_data
