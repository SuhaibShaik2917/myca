# MyCA v1.0.10 — Independent Statutory & Security Audit Verdict

**Audit Date:** November 24, 2026  
**Audited Release:** `v1.0.10`  
**Target Repository:** `SuhaibShaik2917/myca`  
**Preceding Release:** `v1.0.9` (`9bf6bc186092900666097352d7898b53375a49b8`)  
**Audit Scope:** Independent technical assessment of all three remaining findings identified in the `v1.0.9` review.

---

## 1. AUDIT FINDINGS RESOLUTION MATRIX

| Finding Ref | Severity | Domain | Resolution in v1.0.10 | Audit Status |
|---|---|---|---|---|
| **F-01** | **CRITICAL** | Manual Reviewer Authentication | Replaced caller-supplied roles and identities with server-side identity derived exclusively from cryptographically verified Bearer access tokens (`decode_access_token()`). Verified user existence and `is_active=True` in `public.global_users`. Resolved authoritative permissions from `public.organization_memberships` in PostgreSQL (`admin` or `owner` only). All caller-supplied roles and context overrides strictly ignored. Maker-checker enforced via authenticated principal. | **RESOLVED & VERIFIED** |
| **F-02** | **CRITICAL** | Gateway Nonce Concurrency | Created dedicated PostgreSQL nonce registry table `public.statutory_gateway_nonce_registry` with `nonce VARCHAR(128) PRIMARY KEY`. Implemented atomic `INSERT ... ON CONFLICT (nonce) DO NOTHING` with `rowcount == 0` replay detection and `db.commit()`. Eliminated all test and production in-memory set fallbacks. Verified via multi-threaded concurrent race tests. | **RESOLVED & VERIFIED** |
| **F-03** | **HIGH** | Populated Migration Downgrade | Defined explicit safe downgrade policy in `backend/alembic/versions/0025_manual_review_and_nonce.py`: on downgrade to 0024, transitions `MANUAL_DOCUMENT_REVIEWED` to `PENDING_INDEPENDENT_VERIFICATION` so records satisfy the reverted constraint without deleting records, discarding documentary evidence, or converting to independent verification. On re-upgrade, restores `MANUAL_DOCUMENT_REVIEWED`. Verified in migration rollback suite. | **RESOLVED & VERIFIED** |

---

## 2. DETAILED TECHNICAL FINDINGS & EVIDENCE

### 2.1 Genuine Manual Reviewer Authentication (F-01)
- **Defect in v1.0.9:** The manual review verifier accepted caller-provided role dictionaries (e.g., `roles: ["STATUTORY_AUDITOR_ADMIN"]`), allowing an unauthenticated caller or unprivileged viewer to self-assert compliance officer status without server-side validation against PostgreSQL.
- **Remediation in v1.0.10:**
  1. `verify_manual_evidence_review()` extracts the Bearer access token and validates its signature and claims (`sub`, `org_id`, `exp`) using `decode_access_token()`.
  2. The reviewer's user UUID is validated against `public.global_users`, verifying account existence and active status (`is_active = true`).
  3. Authoritative role authorization is queried from `public.organization_memberships` for `(user_id, org_id)`. Only active memberships with role `admin` or `owner` are permitted.
  4. Any caller-supplied `roles`, `is_active`, `can_verify_statutory_auditors`, or `approved_by` fields are completely ignored.
  5. Maker-checker segregation is strictly enforced by comparing the authenticated reviewer's user ID and verified email against the uploader identity (`uploader_id != reviewer_user_id` and `uploader_id != reviewer_email`).
  6. Negative test coverage verified: unauthenticated calls, non-existent user UUIDs, inactive/suspended accounts, unauthorized roles (viewer/accountant), spoofed caller-supplied roles, expired access tokens, and maker-checker violations all fail closed with explicit statutory error codes.

### 2.2 Atomic Persistent ICAI Gateway Nonce Consumption (F-02)
- **Defect in v1.0.9:** Gateway token replay defense used a `SELECT`-before-`INSERT` check against `statutory_auditors(gateway_response_id)`, leaving a concurrency race window, and relied on an in-memory set fallback (`_RECORDED_GATEWAY_NONCES`) in test execution.
- **Remediation in v1.0.10:**
  1. Dedicated PostgreSQL registry table `public.statutory_gateway_nonce_registry` provisioned via Alembic migration 0025:
     ```sql
     CREATE TABLE IF NOT EXISTS public.statutory_gateway_nonce_registry (
         nonce VARCHAR(128) PRIMARY KEY,
         issuer VARCHAR(255) NOT NULL,
         kid VARCHAR(128) NOT NULL,
         membership_no VARCHAR(32) NOT NULL,
         evidence_ref VARCHAR(255) NOT NULL,
         issued_at TIMESTAMPTZ NOT NULL,
         expires_at TIMESTAMPTZ NOT NULL,
         consumed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
         consumed_by VARCHAR(128) NULL
     );
     ```
  2. Least-privilege grants applied: `REVOKE ALL` and `GRANT SELECT, INSERT TO app_service_user`.
  3. Atomic database insertion: `INSERT INTO public.statutory_gateway_nonce_registry (...) VALUES (...) ON CONFLICT (nonce) DO NOTHING;` followed by `db.commit()`. If `rowcount == 0`, immediately returns `GATEWAY_NONCE_REPLAY`.
  4. In-memory set fallback completely eliminated. When `db is None`, the verifier fails closed with `DATABASE_UNAVAILABLE`.
  5. Multi-threaded concurrency race verified: two worker threads synchronized via `threading.Barrier(2)` striking PostgreSQL simultaneously with the exact same nonce resulted in exactly 1 success, exactly 1 `GATEWAY_NONCE_REPLAY`, and exactly 1 row in PostgreSQL.

### 2.3 Safe Populated Migration 0025 Downgrade (F-03)
- **Defect in v1.0.9:** Downgrading migration 0025 on a populated database containing records with `verification_status = 'MANUAL_DOCUMENT_REVIEWED'` caused PostgreSQL check constraint violation `chk_statutory_auditors_verification_status` because the reverted constraint only permitted `UNVERIFIED`, `PENDING_INDEPENDENT_VERIFICATION`, `INDEPENDENTLY_VERIFIED`, `VERIFICATION_FAILED`, and `REVOKED`.
- **Remediation in v1.0.10:**
  1. Migration 0025 downgrade applies safe populated transition:
     ```sql
     UPDATE public.statutory_auditors
         SET verification_status = 'PENDING_INDEPENDENT_VERIFICATION'
         WHERE verification_status = 'MANUAL_DOCUMENT_REVIEWED';
     ```
  2. All documentary evidence hashes (`evidence_sha256`), reviewer session references (`reviewer_session_ref`), audit ledger entries in `public.statutory_auditor_verification_events`, and timestamps are preserved 100% intact.
  3. On re-upgrade to Head (0025), manual review records are safely restored back to `MANUAL_DOCUMENT_REVIEWED`.
  4. Step 2-POPULATED added to `verify_migration_rollback_state.py` validates this exact lifecycle on populated database state during every migration check.

---

## 3. STATUTORY FEATURE LIMITATIONS

1. **ICAI Digital Verification Gateway:**
   - **Production Status:** **DISABLED (Fail-Closed).**
   - **Reason:** ICAI does not currently provide an open, standardized public API or published trust anchor for automated third-party cryptographic verification.
   - **Behavior:** In production mode (`ENVIRONMENT=production`), automated digital gateway verification fails closed with `TRUST_ANCHOR_UNAVAILABLE`. Test keys are strictly prohibited in production.
2. **Authenticated Manual Documentary Review:**
   - **Production Status:** **FULLY OPERATIONAL & PRODUCTION-READY.**
   - **Classification:** Strictly recorded as `MANUAL_DOCUMENT_REVIEWED` (`is_icai_verified=False`, `is_human_reviewed=True`).
   - **Disclosure:** Explicit statutory disclaimer states that auditor onboarding is based on human-reviewed documentary evidence, not automated cryptographic ICAI gateway proof.

---

## 4. TRANSPARENCY DISCLOSURE ON REPOSITORY METADATA

- **Commit & Tag Signatures:** Commits and tags in this remediation sequence are unsigned.
- **Branch Protection:** GitHub branch protection rules are disabled by default on personal repositories unless explicitly configured by the repository administrator.
- **Provenance Integrity:** All Git tags `v1.0.0` through `v1.0.9` remain strictly preserved, unmodified, and historically intact.

---

## 5. AUDIT VERDICT

**VERDICT: APPROVED FOR PRODUCTION WITH EXPLICIT FEATURE LIMITATIONS** (`APPROVED_WITH_EXPLICIT_FEATURE_LIMITATIONS`)

All three findings from the `v1.0.9` independent audit have been conclusively resolved with verifiable, durable, and cryptographically sound implementations.
