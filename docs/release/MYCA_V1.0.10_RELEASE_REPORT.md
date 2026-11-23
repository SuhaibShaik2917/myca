# MyCA v1.0.10 — Production Release Report

**Release Date:** November 24, 2026  
**Target Release Tag:** `v1.0.10`  
**Repository:** `SuhaibShaik2917/myca`  
**Target Branches:** `master` and `remediation/audit-findings-phase3`  
**Preceding Release Tag:** `v1.0.9` (`9bf6bc186092900666097352d7898b53375a49b8`)  
**Release Recommendation:** **APPROVED WITH EXPLICIT FEATURE LIMITATIONS** (`APPROVED_WITH_EXPLICIT_FEATURE_LIMITATIONS`)

---

## 1. RELEASE OBJECTIVES & REMEDIATION SUMMARY

MyCA `v1.0.10` is a targeted, forward-only security remediation release that resolves all three findings identified during the independent review of `v1.0.9`:

1. **Genuine Manual Reviewer Authentication (F-01 - CRITICAL):**
   - Eliminated reliance on caller-supplied role dicts or unauthenticated context overrides.
   - Requires cryptographically valid Bearer access token (`decode_access_token()`).
   - Validates user existence and active account status in `public.global_users`.
   - Resolves authoritative role directly from `public.organization_memberships` in PostgreSQL (requires `role IN ('admin', 'owner')` and `status = 'active'`).
   - Enforces maker-checker segregation using verified user IDs and emails (`uploader_id != reviewer_user_id` and `uploader_id != reviewer_email`).
   - Records decisions strictly as `MANUAL_DOCUMENT_REVIEWED` with explicit disclosure that evidence is human-reviewed and not certified by automated ICAI digital gateway.

2. **Atomic Persistent ICAI Gateway Nonce Consumption (F-02 - CRITICAL):**
   - Created dedicated PostgreSQL table `public.statutory_gateway_nonce_registry` with `nonce VARCHAR(128) PRIMARY KEY`.
   - Enforced atomic insertion (`INSERT ... ON CONFLICT (nonce) DO NOTHING`) and committed via `db.commit()`.
   - Replay detected atomically when `rowcount == 0`, closing the previous `SELECT`-before-`INSERT` race window.
   - Eliminated all in-memory set fallbacks; fails closed with `DATABASE_UNAVAILABLE` when database session is missing.
   - Validated via multi-threaded concurrent barrier tests.

3. **Safe Populated Migration 0025 Downgrade (F-03 - HIGH):**
   - Implemented safe populated downgrade policy in `backend/alembic/versions/0025_manual_review_and_nonce.py`:
     Transitions `MANUAL_DOCUMENT_REVIEWED` to `PENDING_INDEPENDENT_VERIFICATION` before dropping the constraint, preserving 100% of documentary evidence hashes, reviewer session references, and audit events.
   - Automatically restores `MANUAL_DOCUMENT_REVIEWED` on re-upgrade to Head.
   - Added Step 2-POPULATED to `backend/scripts/verify_migration_rollback_state.py` validating downgrade, preservation, and re-upgrade on populated database states.

---

## 2. VERIFIED QUALITY & SECURITY METRICS

| Verification Domain | Target Standard | Result in v1.0.10 | Audit Status |
|---|---|---|---|
| **Backend Test Suite** | 100% Pass | **299 / 299 passed (0 failures, 0 errors)** | **PASS** |
| **Auditor Hardening Tests** | 100% Pass | **36 / 36 passed** | **PASS** |
| **Concurrent Race Serialization** | 1 Success, 1 Replay | **1 Verified, 1 GATEWAY_NONCE_REPLAY, 1 DB Row** | **PASS** |
| **Migration Rollback Cycles** | Exact Anchor Match | **112 grants, 95 policies at Head / Exact match at 0019** | **PASS** |
| **Populated Downgrade Preservation**| 100% Preserved | **Zero records deleted, 100% evidence hashes preserved** | **PASS** |
| **0019 Reference Hash** | Exact SHA256 Match | **`bfe3e3a342b56732d1d0cf43f8edfc50391df1020a2c487f22fb5834e9503373`** | **PASS** |
| **Forced Table RLS Audit** | 36 / 36 Tables | **36 / 36 Tables Forced RLS** | **PASS** |
| **Cross-Tenant Injection Gate** | 11 / 11 Domains | **11 / 11 Spoofed Inserts Rejected (SQLSTATE 42501)** | **PASS** |
| **Catalogue Write Denials** | 100% Denied | **100% Write Attempts Denied under app_service_user** | **PASS** |
| **CI Database Role Diagnostics** | 5 / 5 Roles | **5 / 5 Roles Authenticated & Verified** | **PASS** |
| **Zero Demo Fake Auditors** | 0 records | **0 fake records across all clean states** | **PASS** |
| **Frontend Production Build** | Clean Build | **1,583 modules compiled cleanly in 2.04s** | **PASS** |

---

## 3. STATUTORY FEATURE LIMITATIONS & DEPENDENCIES

| Verification Mode | Status | Operational Rules |
|---|---|---|
| **ICAI Gateway Digital Verification** | **DISABLED IN PRODUCTION (Fail-Closed)** | ICAI does not currently provide an open, standardized public API or published trust anchor for automated third-party key verification. In production mode (`ENVIRONMENT=production`), gateway verification fails closed with `TRUST_ANCHOR_UNAVAILABLE`. Automated verification is enabled exclusively in test environments via sandboxed test keys. |
| **Authenticated Manual Evidence Review** | **FULLY OPERATIONAL (Production-Ready)** | Fully implemented with server-side document SHA-256 calculation, authorized reviewer role enforcement, maker-checker separation, and immutable event logging. Classified honestly as `MANUAL_DOCUMENT_REVIEWED` (`is_icai_verified=False`, `is_human_reviewed=True`). |

---

## 4. METADATA TRANSPARENCY DISCLOSURE

- **Commit Signatures:** Commits and tags in this remediation sequence are unsigned.
- **Branch Protection:** GitHub branch protection is disabled by default on personal repositories unless configured by repository owner.
- **Tag Preservation:** All existing tags `v1.0.0` through `v1.0.9` remain strictly preserved, permanent, and untouched.

---

## 5. RELEASE VERDICT

**VERDICT: APPROVED FOR PRODUCTION WITH EXPLICIT FEATURE LIMITATIONS** (`APPROVED_WITH_EXPLICIT_FEATURE_LIMITATIONS`)

The release is technically complete, fully verified across 299 tests, and ready for publication.
