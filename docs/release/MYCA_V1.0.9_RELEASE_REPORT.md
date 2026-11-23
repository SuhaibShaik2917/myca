# MyCA v1.0.9 — Production Release Report

**Release Date:** November 24, 2026  
**Target Release Tag:** `v1.0.9`  
**Repository:** `SuhaibShaik2917/myca`  
**Target Branches:** `master` and `remediation/audit-findings-phase3`  
**Preceding Release Tag:** `v1.0.8` (`d27381e073a93b9ae43a1e3fabc529ac7aed152e`)  
**Release Recommendation:** **APPROVED WITH EXPLICIT FEATURE LIMITATIONS** (`APPROVED_WITH_EXPLICIT_FEATURE_LIMITATIONS`)

---

## 1. RELEASE SUMMARY

MyCA `v1.0.9` is a forward-only security and statutory compliance release addressing all five findings from the independent review of `v1.0.8`:

1. **Gateway Trust & Asymmetric Algorithm Enforcement:**
   - Independent JWKS JSON Web Key Set loading and parsing via `jose.jwk.construct()` from `ICAI_GATEWAY_JWKS_PATH`, alongside pinned public keys from `ICAI_GATEWAY_PUBLIC_KEY`.
   - Token header `kid` resolved strictly against the independently loaded trust store; unknown `kid` rejected immediately (`UNKNOWN_KEY_ID`).
   - Asymmetric algorithm requirement in production: only `RS256` and `ES256` permitted. Symmetric `HS256` rejected in production (`ASYMMETRIC_ALGORITHM_REQUIRED`).
   - Active database session mandated in production for nonce replay tracking; in-memory fallback eliminated in production mode (`DATABASE_UNAVAILABLE`).

2. **Authenticated Manual Review & Durable Ledger Persistence:**
   - Reviewer identity authenticated against database session (`public.global_users`). Suspended or inactive accounts rejected (`INACTIVE_REVIEWER`).
   - Mandatory active database session for manual review. Swallowed exceptions eliminated: any failure to write to `statutory_auditor_verification_events` executes `db.rollback()` and aborts with `AUDIT_RECORDING_FAILED`.
   - Maker-checker segregation enforced: self-approval (`uploader_id == reviewer_id`) rejected (`MAKER_CHECKER_VIOLATION`).
   - Honest statutory status: outcomes recorded strictly as `MANUAL_DOCUMENT_REVIEWED` with `is_icai_verified=False` and `is_human_reviewed=True`.
   - Clear disclosure that evidence is human-reviewed documentary evidence, not cryptographically certified by ICAI gateway.

3. **Additive Migration 0025:**
   - Applied `backend/alembic/versions/0025_manual_review_and_nonce.py`.
   - Updated check constraint `chk_statutory_auditors_verification_status` to include `'MANUAL_DOCUMENT_REVIEWED'`.
   - Added partial unique index `uq_statutory_auditors_gateway_nonce` on `public.statutory_auditors(gateway_response_id) WHERE gateway_response_id IS NOT NULL`.
   - Enforced least-privilege role grants on `public.statutory_auditor_verification_events` (`SELECT, INSERT` for `app_service_user`).

4. **Negative Test Suite & Complete Verification:**
   - 290/290 backend tests passing (100%).
   - All 27 tests in `test_statutory_auditor_provenance_and_replay_hardening.py` passing, including negative tests for unknown `kid`, tampered signature bits, symmetric `HS256` in production, missing database session, inactive reviewer, audit event persistence failure, and self-assertion preclusion.
   - Frontend production build passing (1,583 modules compiled in 10.63s).

---

## 2. VERIFIED RELEASE METRICS

| Verification Domain | Target Standard | Result | Audit Status |
|---|---|---|---|
| **Backend Test Suite** | 100% Pass | **290 / 290 passed (0 failures, 0 errors)** | **PASS** |
| **Auditor Provenance Tests** | 100% Pass | **27 / 27 passed** | **PASS** |
| **Migration Rollback Cycles** | Exact Anchor Match | **110 grants, 95 policies at Head / Exact match at 0019** | **PASS** |
| **0019 Reference Hash** | Exact SHA256 Match | **`bfe3e3a342b56732d1d0cf43f8edfc50391df1020a2c487f22fb5834e9503373`** | **PASS** |
| **Forced Table RLS Audit** | 36 / 36 Tables | **36 / 36 Tables Forced RLS** | **PASS** |
| **Cross-Tenant Injection Gate** | 11 / 11 Domains | **11 / 11 Spoofed Inserts Rejected (SQLSTATE 42501)** | **PASS** |
| **Catalogue Write Denials** | 100% Denied | **100% Write Attempts Denied under app_service_user** | **PASS** |
| **CI Database Role Diagnostics** | 5 / 5 Roles | **5 / 5 Roles Authenticated & Verified** | **PASS** |
| **Zero Demo Fake Auditors** | 0 records | **0 fake records across all clean states** | **PASS** |
| **Frontend Production Build** | Clean Build | **1,583 modules compiled cleanly in 10.63s** | **PASS** |

---

## 3. STATUTORY FEATURE LIMITATIONS & DEPENDENCIES

| Verification Mode | Status | Operational Rules |
|---|---|---|
| **ICAI Gateway Digital Verification** | **DISABLED IN PRODUCTION (Fail-Closed)** | ICAI does not currently provide an open, standardized public API or published trust anchor for automated third-party key verification. In production mode (`ENVIRONMENT=production`), gateway verification fails closed with `TRUST_ANCHOR_UNAVAILABLE`. Automated verification is enabled exclusively in test environments via sandboxed test keys. |
| **Authenticated Manual Evidence Review** | **FULLY OPERATIONAL (Production-Ready)** | Fully implemented with server-side document SHA-256 calculation, authorized reviewer role enforcement, maker-checker separation, and immutable event logging. Classified honestly as `MANUAL_DOCUMENT_REVIEWED` (`is_icai_verified=False`, `is_human_reviewed=True`). |

---

## 4. RELEASE VERDICT & TAG PRESERVATION

- Historical release tags `v1.0.0` through `v1.0.8` remain strictly preserved, intact, and untouched.
- Release tag `v1.0.9` will be established on the synchronized fast-forward commit on both `master` and `remediation/audit-findings-phase3` following successful verification of all GitHub Actions CI checks on the commit SHA.
