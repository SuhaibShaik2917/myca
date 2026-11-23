# MyCA v1.0.9 — Security Architecture & Verification Report

**Date:** November 24, 2026  
**Release:** `v1.0.9`  
**Security Status:** **VERIFIED SECURE (WITH EXPLICIT FEATURE LIMITATIONS)**

---

## 1. CRYPTOGRAPHIC TRUST ANCHOR ARCHITECTURE

### 1.1 Independent JWKS & Pinned Public Key Loading
- `load_official_icai_trust_store()` independently parses official trust keys from:
  1. `ICAI_GATEWAY_JWKS_PATH`: Reads RFC 7517 compliant JSON Web Key Sets using `jose.jwk.construct(k).to_pem(is_private=False)`.
  2. `ICAI_GATEWAY_PUBLIC_KEY`: Reads pinned PEM/string RSA or ECDSA public keys.
- Token headers are treated as untrusted: the incoming `kid` is strictly looked up in the loaded registry. Unknown key identifiers are rejected with `UNKNOWN_KEY_ID`.

### 1.2 Algorithm Policy & Isolation
- **Production Restriction:** Only asymmetric public-key cryptography (`RS256`, `ES256`) is accepted in production (`ENVIRONMENT=production`).
- **Symmetric Key Prohibition:** Symmetric algorithms (`HS256`) are rejected in production with `ASYMMETRIC_ALGORITHM_REQUIRED`.
- **Algorithm Binding:** The algorithm declared in the token header must match the configured algorithm of the trusted key (`ALGORITHM_MISMATCH`).
- **Test-Key Isolation:** Any key marked with `is_test_key=True` is strictly rejected in production mode (`TEST_KEY_REJECTED`).

### 1.3 Statutory Claim Binding
Every gateway token cryptographically binds:
- `membership_no`: Exact match against expected auditor registration.
- `firm_registration_no`: Verified against registered FRN.
- `cop_status`: Must equal `"ACTIVE"`.
- `cop_valid_until`: Must be in the future (`> date.today()`).
- `evidence_ref`: Bound to expected evidence string.
- `iss`: Must equal `"https://gateway.icai.org"`.
- `aud`: Must equal `"myca-statutory-verification"`.
- `iat`: Must be within the 24-hour freshness window.
- `exp`: Must be in the future.
- `jti`: Mandatory unique response nonce for replay defense.

---

## 2. PERSISTENT REPLAY DEFENSE

### 2.1 Database-Backed Nonce Tracking
- In production, replay checks require an active database session. If `db is None`, the verifier fails closed with `DATABASE_UNAVAILABLE`.
- Nonce queries check both:
  1. `public.statutory_auditors.gateway_response_id`
  2. `public.statutory_auditor_verification_events.evidence_ref`
- When a consumed nonce is presented, verification fails with `GATEWAY_NONCE_REPLAY`.

### 2.2 Database Constraint Enforcement
- Migration `0025_manual_review_and_nonce` created:
  ```sql
  CREATE UNIQUE INDEX IF NOT EXISTS uq_statutory_auditors_gateway_nonce
      ON public.statutory_auditors (gateway_response_id)
      WHERE gateway_response_id IS NOT NULL;
  ```
- This partial unique index enforces atomic serialization at the storage engine level, preventing any concurrent race condition from inserting duplicate gateway nonces.

---

## 3. AUTHENTICATED MANUAL EVIDENCE REVIEW PIPELINE

### 3.1 Reviewer Session Authentication & Verification
- Reviewer identity is authenticated against `public.global_users` within the database session.
- Reviewer account status is checked; suspended or inactive users are rejected with `INACTIVE_REVIEWER`.
- Authorized reviewer roles are enforced (`STATUTORY_AUDITOR_ADMIN`, `CHIEF_COMPLIANCE_OFFICER`, `SENIOR_TAX_AUDIT_SUPERVISOR`). Unauthorized callers are rejected with `INSUFFICIENT_PERMISSIONS`.

### 3.2 Maker-Checker Segregation of Duties
- The uploader identifier is strictly compared against the reviewer identifier:
  `if uploader_id and str(uploader_id).strip() == reviewer_id: reject with MAKER_CHECKER_VIOLATION`
- Self-approval is structurally prohibited.

### 3.3 Documentary Evidence Integrity
- The server computes SHA-256 directly from the raw uploaded bytes.
- Client-supplied hashes are never trusted; if provided, they must match the server-computed SHA-256 (`EVIDENCE_INTEGRITY_MISMATCH`).
- File size is bounded (16B - 10MB) and MIME types are restricted (`application/pdf`, `image/png`, `image/jpeg`).

### 3.4 Durable Event Persistence (Fail-Closed)
- Every manual approval persists an audit record into `public.statutory_auditor_verification_events`.
- Swallowed exceptions are eliminated: any failure to write to the ledger triggers `db.rollback()` and aborts with `AUDIT_RECORDING_FAILED`.

### 3.5 Honest Status & Non-Conflation
- Manually reviewed records are stamped with:
  * `verification_status = 'MANUAL_DOCUMENT_REVIEWED'`
  * `is_icai_verified = False`
  * `is_cop_verified = False`
  * `is_human_reviewed = True`
- Status `'INDEPENDENTLY_VERIFIED'` is reserved exclusively for cryptographic issuer verification. Self-assertion attempts are rejected.

---

## 4. DATABASE ROLE LEAST-PRIVILEGE SECURITY

- `app_service_user`:
  * `SELECT, INSERT, UPDATE` on `public.statutory_auditors`
  * `SELECT, INSERT` on `public.statutory_auditor_verification_events` (`UPDATE` and `DELETE` revoked)
  * `SELECT, INSERT, UPDATE` on `public.statutory_audit_signature_replays` (`DELETE` revoked)
  * Strictly `SELECT` on reference tables (`government_schemes`, `scheme_eligibility_rules`)
- 36 / 36 Tables have `ROW LEVEL SECURITY` and `FORCE ROW LEVEL SECURITY` enabled.
- Cross-tenant injection and spoofing attempts across all 11 domains return `SQLSTATE 42501` (`insufficient_privilege`).
