# MyCA v1.0.10 — Security Verification Report

**Verification Date:** November 24, 2026  
**Release:** `v1.0.10`  
**Security Status:** **VERIFIED SECURE**  

---

## 1. AUTHENTICATED MANUAL REVIEWER SECURITY ARCHITECTURE

### 1.1 Principal Derivation & Cryptographic Validation
- Reviewer authentication requires a signed JWT Bearer access token issued by MyCA's authentication authority.
- The token is cryptographically decoded using `decode_access_token()`, validating `SECRET_KEY`, algorithm (`HS256`), and mandatory claims (`sub`, `org_id`, `exp`).
- Expired tokens, malformed tokens, or missing claims fail closed immediately (`INVALID_REVIEWER_SESSION`).

### 1.2 User Identity Verification in `public.global_users`
- The token subject (`claims["sub"]`) must parse as a valid UUID.
- Query: `SELECT id, email, full_name, is_active FROM public.global_users WHERE id = :uid;`
- Rejection triggers:
  * Non-existent user UUID -> `UNREGISTERED_REVIEWER`
  * Account with `is_active = false` -> `INACTIVE_REVIEWER`

### 1.3 Authoritative Role Authorization in `public.organization_memberships`
- The user's role is queried strictly from the persistent database:
  `SELECT role, status FROM public.organization_memberships WHERE user_id = :uid AND org_id = :oid;`
- Authorized roles: `['admin', 'owner']` (defined in `AUTHORIZED_ORGANIZATION_ROLES`).
- Rejection triggers:
  * No membership record for `(user_id, org_id)` -> `INSUFFICIENT_PERMISSIONS`
  * Inactive membership status -> `INACTIVE_REVIEWER_MEMBERSHIP`
  * Unauthorized role (e.g. `'viewer'`, `'accountant'`) -> `UNAUTHORIZED_REVIEWER_ROLE`
- **Zero-Trust Rule:** Any caller-supplied `roles`, `is_active`, `can_verify_statutory_auditors`, or `approved_by` values in the request payload are completely ignored and discarded.

### 1.4 Maker-Checker Segregation
- The authenticated reviewer's identity is compared against the submission uploader:
  `clean_uploader in (reviewer_user_id.lower(), reviewer_email.lower())`
- Self-approval attempts fail closed with `MAKER_CHECKER_VIOLATION`.

### 1.5 Durable Audit Event Ledger
- Review decisions are committed to `public.statutory_auditor_verification_events`:
  * `evidence_sha256` computed server-side from raw document bytes (client-supplied hash is not trusted).
  * `reviewer_id` set to authenticated user UUID.
  * `reviewer_session_ref` set to token JTI / session hash.
  * `decision = 'APPROVED'`, `verification_status = 'MANUAL_DOCUMENT_REVIEWED'`.
- Any database transaction failure during audit logging triggers `db.rollback()` and aborts with `AUDIT_RECORDING_FAILED`.

---

## 2. ATOMIC GATEWAY NONCE REGISTRY & REPLAY HARDENING

### 2.1 Dedicated Registry Table
- Table: `public.statutory_gateway_nonce_registry`
- Primary Key: `nonce VARCHAR(128)`
- Implements atomic insertion:
  ```sql
  INSERT INTO public.statutory_gateway_nonce_registry (
      nonce, issuer, kid, membership_no, evidence_ref,
      issued_at, expires_at, consumed_at, consumed_by
  ) VALUES (
      :nonce, :issuer, :kid, :mem, :ev_ref,
      :iat, :exp, clock_timestamp(), :auditor_id
  ) ON CONFLICT (nonce) DO NOTHING;
  ```
- Evaluates `nonce_res.rowcount`:
  * If `rowcount == 0`: immediately aborts with `GATEWAY_NONCE_REPLAY: Gateway response nonce has already been consumed.`
  * If `rowcount == 1`: commits transaction to PostgreSQL.

### 2.2 In-Memory Fallback Elimination
- Production and test environments require an active PostgreSQL session.
- If `db is None`: fails closed with `DATABASE_UNAVAILABLE`.
- No in-memory set fallbacks exist.

### 2.3 Concurrency Race Serialization
- Multi-threaded testing using `threading.Barrier(2)` verified that concurrent execution across separate database sessions serializes cleanly:
  * Thread 1: `GATEWAY_VERIFIED` (HTTP 200 / True)
  * Thread 2: `GATEWAY_NONCE_REPLAY` (False)
  * PostgreSQL state: Exactly 1 record inserted.

---

## 3. NEGATIVE SECURITY TEST RESULTS

| Test Name | Threat Vector | Target Defense | Result |
|---|---|---|---|
| `test_manual_evidence_review_unauthenticated_rejected` | Missing Bearer token | `UNAUTHENTICATED_REVIEWER` | **PASS** |
| `test_manual_evidence_review_missing_user_in_global_users_rejected` | Forged user UUID | `UNREGISTERED_REVIEWER` | **PASS** |
| `test_manual_evidence_review_inactive_user_rejected` | Inactive / suspended account | `INACTIVE_REVIEWER` | **PASS** |
| `test_manual_evidence_review_unauthorized_role_rejected` | Viewer / non-admin role | `UNAUTHORIZED_REVIEWER_ROLE` | **PASS** |
| `test_manual_evidence_review_caller_supplied_roles_ignored_and_rejected` | Spoofed caller role dict | DB lookup override -> Reject | **PASS** |
| `test_manual_evidence_review_expired_token_rejected` | Expired Bearer access token | `INVALID_REVIEWER_SESSION` | **PASS** |
| `test_manual_evidence_review_maker_checker_rejected` | Self-approval by uploader | `MAKER_CHECKER_VIOLATION` | **PASS** |
| `test_manual_evidence_review_tampered_document_bytes_rejected` | Corrupted document bytes | `EVIDENCE_INTEGRITY_MISMATCH` | **PASS** |
| `test_manual_review_missing_db_fails_closed` | Missing DB session | `DATABASE_UNAVAILABLE` | **PASS** |
| `test_manual_review_audit_event_persistence_failure_fails_closed` | Audit ledger failure | `AUDIT_RECORDING_FAILED` | **PASS** |
| `test_conclusive_gateway_nonce_atomic_concurrency_race` | Concurrent nonce collision | `GATEWAY_NONCE_REPLAY` | **PASS** |
| `test_gateway_nonce_cross_auditor_reuse_rejected` | Cross-auditor nonce reuse | `GATEWAY_NONCE_REPLAY` | **PASS** |
| `test_gateway_nonce_restart_replay_defense` | Memory restart replay | PostgreSQL persistent check | **PASS** |
| `test_gateway_expired_token_rejected_before_nonce_consumption` | Expired gateway token | Pre-consumption expiry check | **PASS** |
| `test_gateway_missing_db_fails_closed` | Gateway check without DB | `DATABASE_UNAVAILABLE` | **PASS** |
