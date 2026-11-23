# MyCA v1.0.12 Release Report

**Release Tag:** `v1.0.12`  
**Target Repository:** `SuhaibShaik2917/myca`  
**Parent Release:** `v1.0.11` (`8b93e60e03bfa9e28c4c56fe6b83b96d78759613`)  
**Release Type:** Critical Security & Authentication Hardening, RLS Reconciliation & Rollback Safety  
**Release Recommendation:** **APPROVED FOR PRODUCTION WITH EXPLICIT FEATURE LIMITATIONS** (`APPROVED_WITH_EXPLICIT_FEATURE_LIMITATIONS`)  

---

## 1. Executive Summary

MyCA `v1.0.12` resolves all findings identified in the independent review of `v1.0.11`:

1. **Environment Configuration & Startup Enforcement:**
   - Enforced strict startup validation in `backend/app/config.py`: `ENVIRONMENT` must be explicitly configured from the valid set `{'production', 'staging', 'development', 'test'}`.
   - An unset, empty, or invalid environment immediately halts application startup with a `ValueError`. Fallback to development is strictly prohibited.
   - Demo-token authentication (`/auth/demo-token`) is **disabled by default** across all environments (`ALLOW_DEMO_TOKENS=False`). It is permitted exclusively in designated local/test environments (`development`, `test`) when `ALLOW_DEMO_TOKENS=True` is explicitly configured. In `production`, `staging`, or any non-local environment, it unconditionally returns HTTP 403 Forbidden.

2. **Authentic Federated IdP Cryptographic & Claims Validation:**
   - Replaced self-asserted token parsing with strict cryptographic verification in `backend/app/modules/auth/idp_verifier.py`.
   - **Algorithm Confusion Defense:** Prohibits symmetric algorithms (`HS256`, `HS384`, `HS512`) in production/staging and whenever an asymmetric public key trust anchor is configured.
   - **Header & Token Inspection:** Strictly rejects tokens with `none` algorithm, missing algorithms, or mismatched key IDs (`kid`).
   - **Claims Enforcement:** Verifies trusted issuer (`iss`), audience (`aud`), expiration (`exp`), not-before freshness (`nbf`), issue time (`iat`), and `email_verified` claim.
   - **Production Fail-Closed:** If IdP trust anchors are unconfigured or incomplete in production or staging, token exchange fails closed with HTTP 503 `IDP_INTEGRATION_UNAVAILABLE`.
   - Identity resolution is extracted strictly from cryptographically verified claims, matching active users in `public.global_users` and verified tenant memberships in `public.organization_memberships`.

3. **PostgreSQL RLS & FORCE RLS Reconciliation:**
   - Audited all 45 tables in the public schema on live PostgreSQL.
   - Resolved the narrative discrepancy from earlier releases: exactly **36 of 36 tenant tables (100%)** enforce both `relrowsecurity=True` (RLS) AND `relforcerowsecurity=True` (FORCE RLS).
   - The remaining 9 tables are non-tenant reference catalogs and security metadata tables, protected by strict table-level least-privilege role permissions (application runtime role `app_service_user` has strictly `SELECT` grants, with zero DDL or `BYPASSRLS` privileges).

4. **Rollback Script Destructive Operation Safety Guard:**
   - Implemented `assert_disposable_database_environment(DB_ADMIN_URL)` in `backend/scripts/verify_migration_rollback_state.py`.
   - Refuses execution if `ENVIRONMENT` is `production` or `staging`.
   - Refuses execution unless `ALLOW_DISPOSABLE_DATABASE_ROLLBACK=true` is explicitly set.
   - Refuses execution if the database connection URL targets a remote host or contains production identifiers (`prod`, `production`, `live`).

---

## 2. Verification Summary

| Verification Category | Target Standard | Result | Status |
|---|---|---|---|
| **Explicit Environment Startup Guard** | Startup halts with `ValueError` if `ENVIRONMENT` is unset or invalid | Refuses unset/invalid env; no dev fallback | **PASS** |
| **Demo Token Default Lockdown** | Disabled by default; HTTP 403 in production, staging, and unconfigured dev | Verified across all environments | **PASS** |
| **IdP Algorithm Confusion Defense** | Symmetric algorithms (`HS256`) rejected when public key configured or in prod | HTTP 401 `ALGORITHM_CONFUSION_REJECTED` | **PASS** |
| **IdP Cryptographic Verification** | Validates signature, issuer, audience, exp, nbf, and verified email | Tested with authentic RS256 RSA keypair | **PASS** |
| **IdP Production Fail-Closed** | HTTP 503 `IDP_INTEGRATION_UNAVAILABLE` when trust anchors unconfigured | Verified in unit and integration tests | **PASS** |
| **RLS Policy Reconciliation** | Reconciled 21 vs 36 FORCE RLS discrepancy against PostgreSQL catalog | Exactly 36 of 36 tenant tables FORCE RLS | **PASS** |
| **Rollback Script Safety Guard** | Aborts destructive migrations on production or non-disposable DBs | Aborts without explicit disposable marker | **PASS** |
| **Full Backend Test Suite** | 100% passing across all modules | **334 / 334 tests passed** | **PASS** |
| **Migration Rollback Invariants** | 100% exact match against trusted reference (`baseline_0019_reference.json`) | 79 grants, 87 policies, 41 tables | **PASS** |
| **RLS Security Audit** | Comprehensive RLS, grant, and cross-tenant tests | All 36 tables RLS, 36 FORCE RLS | **PASS** |
| **CI Diagnostics** | Database connectivity & clean statutory tables | All 5 service roles connected | **PASS** |
| **Frontend Production Build** | TypeScript typecheck and Vite bundling | Built cleanly in 2.31s | **PASS** |

---

## 3. Production Deployment Notes & Feature Limitations

1. **ICAI Digital Gateway Verification:**
   - **Production Status:** **DISABLED (Fail-Closed).**
   - **Reason:** ICAI does not currently provide an open, standardized public API or published trust anchor for automated third-party cryptographic verification.
   - **Production Behavior:** In production mode (`ENVIRONMENT=production`), automated digital gateway verification fails closed with `TRUST_ANCHOR_UNAVAILABLE`. Test keys are strictly prohibited in production.
2. **Authenticated Manual Documentary Review:**
   - **Production Status:** **FULLY OPERATIONAL & SECURED.**
   - **Classification:** Strictly recorded as `MANUAL_DOCUMENT_REVIEWED` (`is_icai_verified=False`, `is_human_reviewed=True`).
   - **Disclosure:** Explicit statutory disclaimer states that auditor onboarding is based on human-reviewed documentary evidence, not automated cryptographic ICAI gateway proof.
3. **Repository Metadata & Branch Governance:**
   - **Signatures:** Commits and tags in this remediation sequence are unsigned.
   - **Branch Protection:** GitHub branch protection rules are disabled by default on personal repositories unless explicitly configured by the repository administrator.
   - **Historical Tag Preservation:** All prior tags `v1.0.0` through `v1.0.11` remain permanently preserved and unmodified.
