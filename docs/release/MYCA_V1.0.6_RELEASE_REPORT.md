# MyCA v1.0.6 — Production Release Report

**Release Date:** November 23, 2026  
**Target Release Tag:** `v1.0.6`  
**Repository:** `SuhaibShaik2917/myca`  
**Target Branches:** `master` and `remediation/audit-findings-phase3`  
**Preceding Release Tag:** `v1.0.5` (`482650b38a7ed05822c420032ee94d8e5414715c`)  
**Release Sign-off:** **APPROVED — ALL FOUR CRITICAL FINDINGS SATISFIED**

---

## 1. RELEASE HIGHLIGHTS & REMEDIATIONS

MyCA `v1.0.6` addresses all four critical security, database, and CI defects identified during the independent review of `v1.0.5`:

1. **Removal of Fake Auditor Seed Identities & In-Memory Registry:**
   - Purged all demonstration identities (`CA-ICAI-098765`, `CA-ICAI-054321`, `CA-REVOKED-011223`, `CA Rajesh Kumar`, `CA Priya Sharma`, `CA Suresh Gupta`) via forward migration `0023_statutory_auditor_provenance.py` (Revision ID `0023_auditor_provenance`).
   - Production registry begins with zero rows.
   - Removed `_TEST_STATUTORY_AUDITOR_REGISTRY` entirely from source code.
   - Test identities exist exclusively in ephemeral test fixtures.
   - Separated administrative onboarding from independent ICAI verification via provenance columns (`verification_source`, `verification_status`, `is_icai_verified`, `is_cop_verified`, `verified_at`, `verification_evidence_ref`).

2. **Persistent Database-Backed Replay Defense:**
   - Removed `_OFFLINE_RECORDED_AUDIT_SIGNATURES` in-memory fallback dictionary and reset functions.
   - `check_and_record_signature_replay()` strictly queries and inserts into `public.statutory_audit_signature_replays` with PostgreSQL kernel uniqueness on `token_hash = sha256(token)`.
   - Atomic collision detection via `ON CONFLICT (token_hash) DO NOTHING`.
   - Permanent invalidation lifecycle: expired tokens cannot be reused.
   - Strict fail-closed error handling: returns `DATABASE_UNAVAILABLE` or `DATABASE_TRANSACTION_FAILURE` on database disruption.

3. **Unified Test Database Configuration Contract:**
   - Created `backend/tests/test_db_config.py` standardizing environment URL lookups (`DB_ADMIN`, `DB_APP`, `DB_WORKER`, `DB_WORKER_1`, `DB_WORKER_2`).
   - Replaced hardcoded strings across 10+ test files, resolving CI `password authentication failed for user "postgres"` failures.
   - Added pre-test session autouse hook `ensure_reference_catalogs_seeded` in `backend/tests/conftest.py` ensuring reference tables (`government_schemes` and `scheme_eligibility_rules`) are populated prior to any test.
   - Added early CI diagnostic script `backend/scripts/diagnose_ci_database.py` verifying all 5 database roles and catalogue health.

4. **Automated CI Workflow Hardening:**
   - Updated `.github/workflows/ci.yml` with all required database URLs, worker role password provisioning, diagnostic stage, and end-to-end execution of full pytest suite, rollback verification, and RLS security audit.

---

## 2. VERIFIED RELEASE METRICS

| Metric | Target | Result | Status |
|---|---|---|---|
| Total Backend Pytest Suite | 100% Pass | **274 / 274 passed (0 failures, 0 errors)** | **PASS** |
| Migration Rollback & Baseline Verification | Exact Match (0019 <-> 0023) | **108 grants, 95 policies at Head / Exact match at 0019** | **PASS** |
| Forced RLS Table Audit | 36 / 36 Tables | **36 / 36 Tables Forced RLS** | **PASS** |
| Cross-Tenant Spoofed Insert Rejection | 11 / 11 Domains | **11 / 11 Rejected (SQLSTATE 42501)** | **PASS** |
| Reference Catalogue Write Denials | 100% Denied | **100% Write Attempts Denied under app_service_user** | **PASS** |
| CI Database Role Diagnostics | 5 / 5 Roles | **5 / 5 Roles Authenticated & Verified** | **PASS** |
| Frontend Production Build | Clean Build | **1,583 modules compiled cleanly in 9.64s** | **PASS** |

---

## 3. RELEASE VERDICT & IMMUTABLE TAG PRESERVATION

- Historical release tags `v1.0.0`, `v1.0.1`, `v1.0.2`, `v1.0.3`, `v1.0.4`, `v1.0.5` remain intact and untouched.
- Release tag `v1.0.6` is established on the synchronized fast-forward commit on both `master` and `remediation/audit-findings-phase3`.
