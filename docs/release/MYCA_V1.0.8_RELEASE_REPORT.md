# MyCA v1.0.8 — Production Release Report

**Release Date:** November 24, 2026  
**Target Release Tag:** `v1.0.8`  
**Repository:** `SuhaibShaik2917/myca`  
**Target Branches:** `master` and `remediation/audit-findings-phase3`  
**Preceding Release Tag:** `v1.0.7` (`228fa1f63e6a875fb3384d7c0c5696db94d3a7d6`)  
**Release Recommendation:** **APPROVED WITH EXPLICIT FEATURE LIMITATIONS**

---

## 1. RELEASE HIGHLIGHTS & REMEDIATIONS

MyCA `v1.0.8` delivers focused, evidence-driven remediation of all six findings raised during the independent audit review of `v1.0.7`:

1. **Authentic ICAI Verification vs. Format Validation:**
   - Eliminated format-only regex pattern validation (`ICAI-GATEWAY-...`) that allowed self-asserted or fabricated evidence strings (such as `ICAI-GATEWAY-FAKE123`).
   - Implemented authentic cryptographic verification for the ICAI digital gateway protocol (`icai_gateway_verifier.py`) supporting RS256, ES256, and HS256 tokens.
   - Enforced cryptographic claim binding on `membership_no`, `firm_registration_no`, `cop_status`, `cop_valid_until`, `evidence_ref`, `kid`, `iat`, `exp`, and anti-replay nonce (`jti`).
   - In production environments, when official ICAI gateway digital public keys are unconfigured, the gateway path **fails closed** with `TRUST_ANCHOR_UNAVAILABLE` / `PENDING_INDEPENDENT_VERIFICATION`. Test keys are isolated and rejected in production.

2. **Authenticated Manual Evidence Review:**
   - Established a distinct, non-conflated `MANUAL_EVIDENCE_REVIEW` verification source with documentary provenance.
   - Requires actual uploaded evidence bytes (PDF, PNG, JPEG), with SHA-256 computed strictly server-side (client hashes are never trusted).
   - Validates reviewer identity and permissions exclusively from server-validated session context (`STATUTORY_AUDITOR_ADMIN`, `CHIEF_COMPLIANCE_OFFICER`), rejecting client request-body overrides.
   - Enforces strict maker-checker segregation: self-approval (`uploader_id == reviewer_id`) is strictly prohibited.
   - Records an immutable verification event in `public.statutory_auditor_verification_events`.

3. **Restoration of Migration 0022 and Additive Migration 0024:**
   - Restored historical migration `backend/alembic/versions/0022_statutory_auditors_replay.py` to its exact `v1.0.5` Git blob SHA (`b97ca120d0ccdf3ec2197cab8441dd3741286ceb`).
   - Created additive migration `backend/alembic/versions/0024_auditor_authentic_evidence.py` to add authentic evidence tracking columns (`evidence_sha256`, `reviewer_session_ref`, `gateway_kid`, `gateway_response_id`) to `statutory_auditors`, update source check constraints, and create `public.statutory_auditor_verification_events` with least-privilege role grants (`SELECT, INSERT` for `app_service_user`).
   - Cleansing of historical demonstration seed records remains strictly in `0023_statutory_auditor_provenance.py:upgrade()`, while `0023:downgrade()` never re-inserts them.

4. **Conclusive PostgreSQL Concurrency Race Verification:**
   - Implemented a conclusive multi-threaded race test using SQLAlchemy's `before_cursor_execute` event hook and `threading.Barrier(2)`.
   - Both concurrent worker threads execute the initial `SELECT` pre-check, arrive at the barrier immediately before cursor execution, and strike PostgreSQL's `INSERT ... ON CONFLICT (token_hash) DO NOTHING` statement simultaneously.
   - Confirmed deterministic serialization: exactly 1 thread succeeds, 1 thread receives `CONCURRENT_REPLAY_DETECTED` (rowcount == 0), and exactly 1 row exists in the database.

5. **Mandatory Token Expiry Policy & Engine Wiring:**
   - In `_is_strictly_verified_uniform_rate()`, derived token expiration directly from the certified verification timestamp (`expires_at = verification_ts + timedelta(hours=24)`), passing it explicitly to `check_and_record_signature_replay()`.
   - Eliminated synthetic default expiration in `check_and_record_signature_replay()`; missing `expires_at` fails closed immediately with `MISSING_TOKEN_EXPIRY`.
   - Pre-insert check rejects already-expired tokens (`expires_at <= now_utc`) with `EXPIRED_TOKEN`.

6. **Accurate Migration Rollback Status Reporting:**
   - Updated `scripts/verify_migration_rollback_state.py` with `classify_and_verify_auditor_state()` to classify exact states across all migration transitions:
     - `TABLE_NOT_PRESENT` at baseline 0019 and 0020.
     - `LEGACY_SEEDS_PRESENT_IN_HISTORICAL_0022` at intermediate fresh upgrade to historical 0022.
     - `AUDITORS_VERIFIED_CLEAN` at 0023, 0024, and following downgrade rollback to 0022.

---

## 2. VERIFIED RELEASE METRICS

| Metric | Target | Result | Status |
|---|---|---|---|
| Total Backend Pytest Suite | 100% Pass | **283 / 283 passed (0 failures, 0 errors)** | **PASS** |
| Migration Rollback & Baseline Verification | Exact Match (0019 <-> 0024) | **110 grants, 95 policies at Head / Exact match at 0019** | **PASS** |
| Zero Fake Demo Auditors In DB | 0 across clean states | **0 records across revisions 0019, 0020, clean 0022, 0023, 0024** | **PASS** |
| Historical 0022 Blob Hash | Exact SHA Match | **`b97ca120d0ccdf3ec2197cab8441dd3741286ceb`** | **PASS** |
| Forced RLS Table Audit | 36 / 36 Tables | **36 / 36 Tables Forced RLS** | **PASS** |
| Cross-Tenant Spoofed Insert Rejection | 11 / 11 Domains | **11 / 11 Rejected (SQLSTATE 42501)** | **PASS** |
| Reference Catalogue Write Denials | 100% Denied | **100% Write Attempts Denied under app_service_user** | **PASS** |
| CI Database Role Diagnostics | 5 / 5 Roles | **5 / 5 Roles Authenticated & Verified** | **PASS** |
| Frontend Production Build | Clean Build | **1,583 modules compiled cleanly in 13.85s** | **PASS** |

---

## 3. EXTERNAL TRUST DEPENDENCY & FEATURE STATUS

| Feature Path | Status | Operational Details |
|---|---|---|
| **ICAI Gateway Digital Verification** | **DISABLED IN PRODUCTION (Fail-Closed)** | ICAI does not provide an open public API or published trust anchor for automated third-party key verification. In production mode (`ENVIRONMENT=production`), gateway verification fails closed with `TRUST_ANCHOR_UNAVAILABLE`. Automated verification is enabled exclusively in test environments via sandboxed test keys. |
| **Authenticated Manual Evidence Review** | **FULLY OPERATIONAL (Production-Ready)** | Fully implemented with server-side document SHA-256 calculation, authorized reviewer role enforcement, maker-checker separation, and immutable event logging. This is the primary sanctioned path for auditor onboarding in v1.0.8. |

---

## 4. RELEASE VERDICT & IMMUTABLE TAG PRESERVATION

- Historical release tags `v1.0.0` through `v1.0.7` remain strictly preserved, intact, and untouched.
- Release tag `v1.0.8` is established on the synchronized fast-forward commit on both `master` and `remediation/audit-findings-phase3` following successful verification of all GitHub Actions CI checks on the commit SHA.
