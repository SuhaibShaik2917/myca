# MyCA v1.0.8 — Security & Cryptographic Verification Report

**Date:** November 24, 2026  
**Target Release:** `v1.0.8`  
**Repository:** `SuhaibShaik2917/myca`  
**Security Posture:** **PRODUCTION HARDENED (FAIL-CLOSED PROTOCOLS ENFORCED)**

---

## 1. CRYPTOGRAPHIC PROVENANCE & PROTOCOL VERIFICATION

### 1.1 Digital Gateway Protocol (`icai_gateway_verifier.py`)

| Control | Mechanism | Verification Result | Status |
|---|---|---|---|
| **Trust Anchor Availability** | `is_official_icai_gateway_configured()` | Fails closed with `TRUST_ANCHOR_UNAVAILABLE` when no official public key or JWKS is configured in production. | **PASS** |
| **Test-Key Isolation** | `is_test_key` flag enforcement | Rejects test keys with `TEST_KEY_REJECTED` in production environment. | **PASS** |
| **Digital Signature Verification** | RS256/ES256/HS256 signature check via `python-jose` | Rejects tampered tokens with `CRYPTOGRAPHIC_VERIFICATION_FAILED`. | **PASS** |
| **Algorithm Confusion Defense** | Algorithm whitelist (`SUPPORTED_GATEWAY_ALGORITHMS`) | Rejects `alg=none` and unrecognized algorithms. | **PASS** |
| **Key Lifecycle Defense** | Revocation & validity window checks | Rejects revoked keys (`REVOKED_SIGNING_KEY`) and expired keys (`EXPIRED_SIGNING_KEY`). | **PASS** |
| **Statutory Claim Binding** | Strict equality on `membership_no`, `firm_registration_no`, `cop_status`, `cop_valid_until`, and `evidence_ref` | Mismatched claims fail closed (`MEMBERSHIP_MISMATCH`, `COP_DATE_MISMATCH`). | **PASS** |
| **Anti-Replay Nonce (`jti`)** | Tracking in `_RECORDED_GATEWAY_NONCES` & DB | Replayed token nonces fail closed (`GATEWAY_NONCE_REPLAY`). | **PASS** |
| **Timestamp Freshness** | 24-hour freshness & 5-minute skew bound | Rejects tokens older than 24 hours (`STALE_VERIFICATION`) or future-dated beyond clock skew. | **PASS** |

### 1.2 Authenticated Manual Evidence Review (`manual_review_verifier.py`)

| Control | Mechanism | Verification Result | Status |
|---|---|---|---|
| **Server-Side Hash Computation** | `hashlib.sha256(document_bytes).hexdigest()` | Computed directly from raw uploaded bytes; client-supplied hash mismatch fails closed (`EVIDENCE_INTEGRITY_MISMATCH`). | **PASS** |
| **Session-Bound Identity** | Session context extraction | Reviewer identity taken strictly from server session; caller request-body overrides ignored. | **PASS** |
| **Permission Check** | Role whitelist (`AUTHORIZED_REVIEWER_ROLES`) | Unauthorized roles fail closed (`INSUFFICIENT_PERMISSIONS`). Inactive accounts fail (`INACTIVE_REVIEWER`). | **PASS** |
| **Maker-Checker Segregation** | `uploader_id != reviewer_id` | Self-approval fails closed (`MAKER_CHECKER_VIOLATION`). | **PASS** |
| **Document Constraints** | Content-type & size bounds (16B - 10MB) | Disallowed MIME types or out-of-bound sizes fail closed. | **PASS** |
| **Immutable Audit Logging** | `statutory_auditor_verification_events` insert | Every approval and rejection logged with SHA-256, reviewer ID, session ref, and decision reason. | **PASS** |

---

## 2. REPLAY PROTECTION & TOKEN EXPIRATION HARDENING

### 2.1 Engine-Level Replay Wiring

- In `_is_strictly_verified_uniform_rate()`:
  - Extracted and validated `verification_timestamp`.
  - Computed non-synthesized expiration: `token_expiry = ts_dt + timedelta(hours=24)`.
  - Passed `expires_at=token_expiry` to `check_and_record_signature_replay()`.

### 2.2 Replay Protection Invariants

- **Mandatory Expiration:** `expires_at` is strictly required. Omission returns `(False, "MISSING_TOKEN_EXPIRY: Explicit expires_at timestamp is mandatory; synthetic defaults are prohibited.")`.
- **Pre-Insert Expiration Rejection:** Tokens with `expires_at <= now_utc` fail immediately with `EXPIRED_TOKEN` before database insertion.
- **Cross-Document Replay Rejection:** Token certified for `doc1` cannot be reused for `doc2` (`SIGNATURE_REPLAY_DETECTED: cross-document replay rejected`).
- **Duplicate Consumption Rejection:** Token already consumed for `doc1` cannot be re-applied to `doc1` (`SIGNATURE_REPLAY_DETECTED: already been consumed`).
- **Permanent Invalidation:** Valid tokens recorded in the database can never be re-used, even after token expiration has passed.
- **Conclusive Concurrency Race Test:** Validated via SQLAlchemy `before_cursor_execute` hook and `threading.Barrier(2)` striking PostgreSQL's `INSERT ... ON CONFLICT (token_hash) DO NOTHING` simultaneously. Exactly 1 worker succeeds; 1 worker receives `CONCURRENT_REPLAY_DETECTED`.

---

## 3. TEST SUITE RESULTS

```
tests/test_statutory_auditor_provenance_and_replay_hardening.py:
  test_statutory_auditor_unregistered_fails_closed                       PASSED
  test_statutory_auditor_unverified_provenance_rejected                 PASSED
  test_statutory_auditor_self_assertion_precluded                       PASSED
  test_statutory_auditor_independently_verified_provenance_accepted     PASSED
  test_statutory_auditor_revoked_status_rejected                         PASSED
  test_statutory_auditor_expired_cop_rejected                           PASSED
  test_statutory_auditor_db_failure_fails_closed                         PASSED
  test_icai_gateway_fabricated_reference_rejected                       PASSED
  test_icai_gateway_cryptographic_verification_claims_and_replay        PASSED
  test_icai_gateway_unconfigured_production_fails_closed                 PASSED
  test_manual_evidence_review_provenance_success                        PASSED
  test_manual_evidence_review_unauthorized_role_rejected                 PASSED
  test_manual_evidence_review_maker_checker_rejected                    PASSED
  test_manual_evidence_review_tampered_document_bytes_rejected          PASSED
  test_signature_replay_missing_expires_at_fails_closed                 PASSED
  test_signature_replay_cross_document_reuse_rejected                   PASSED
  test_signature_replay_duplicate_consumption_rejected                 PASSED
  test_signature_replay_expired_token_permanent_invalidation           PASSED
  test_conclusive_postgresql_insert_concurrency_race                   PASSED
  test_signature_replay_db_failure_fails_closed_no_in_memory_fallback   PASSED

Result: 20 passed in 2.03s (100% Pass)
```
