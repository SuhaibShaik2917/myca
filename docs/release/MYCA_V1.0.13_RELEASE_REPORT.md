# MyCA v1.0.13 Release Report

**Release Tag:** `v1.0.13`  
**Target Repository:** `SuhaibShaik2917/myca`  
**Parent Release:** `v1.0.12` candidate commit (`a64eb6ba82b79c7b39de7eda7594bbbe25c62d2a`)  
**Release Type:** Critical Authentication Security, IdP Single-Use Nonce Replay Defense, Migration Rollback Isolation, and Staging Lockdown  
**Release Recommendation:** **APPROVED FOR PRODUCTION WITH EXPLICIT STATUTORY & ARCHITECTURAL DISCLOSURES** (`APPROVED_WITH_STATUTORY_DISCLOSURES`)  

---

## 1. Executive Summary

MyCA `v1.0.13` provides a rigorous, evidence-backed remediation resolving all security, authentication, and release-governance findings identified in the independent review of `v1.0.12`:

1. **Strict Federated IdP Email Verification & Provider Binding:**
   - Enforced strict boolean validation in `backend/app/modules/auth/idp_verifier.py`: `email_verified` must strictly be Python boolean `True` (`isinstance(claims.get("email_verified"), bool) and claims.get("email_verified") is True`).
   - Tokens with missing, null, false, integer (`1`, `0`), or non-boolean string values (`"true"`, `"True"`, `"yes"`, `"1"`) are strictly rejected with HTTP 401 `UNVERIFIED_EMAIL` or `INVALID_EMAIL_VERIFIED_CLAIM`.
   - Rejects missing, empty, or whitespace-only `email` claims and non-email formats missing `@` with HTTP 401 `MISSING_EMAIL_CLAIM` or `INVALID_EMAIL_FORMAT`.
   - Never falls back to `sub` as an email identifier under any circumstances.
   - Normalizes verified emails (`.strip().lower()`) before user resolution.
   - Enforces provider-to-issuer binding: tokens presented under provider identifier `google` must originate from `accounts.google.com` or `https://accounts.google.com`.

2. **Single-Use IdP Authentication Transactions & Nonce Replay Protection:**
   - Implemented server-issued, cryptographically secure unpredictable nonces via `POST /api/v1/auth/idp-nonce` (`create_idp_login_nonce`).
   - Created PostgreSQL-backed tracking table `public.idp_authentication_nonces` (provisioned via additive migration `0027_idp_auth_nonces.py`) with 5-minute TTL and active state.
   - Enforced single-use nonce validation in `POST /api/v1/auth/idp-exchange`: requires a non-empty `nonce` claim in the IdP token matching an active issued nonce for the requested organization.
   - Consumes nonces atomically within a PostgreSQL transaction (`UPDATE ... SET status='CONSUMED', consumed_at=NOW() WHERE status='ACTIVE' AND expires_at > NOW()`).
   - Rejection of duplicate or replayed nonces with HTTP 401 `IDP_NONCE_REPLAY`, expired nonces with HTTP 401 `IDP_NONCE_EXPIRED`, and unknown nonces with HTTP 401 `IDP_NONCE_INVALID`.
   - Fails closed if the database is unreachable during nonce verification.
   - Nonce preservation policy: nonces survive migration downgrades and persist across service restarts, preventing historical token replay upon database re-upgrade.

3. **Hardened Migration Rollback Database Safety & Isolation:**
   - Fortified `assert_disposable_database_environment(db_url)` in `backend/scripts/verify_migration_rollback_state.py`.
   - Strictly requires `ENVIRONMENT` to be explicitly `test` or `ci` (rejects unset, empty, `development`, `staging`, and `production`).
   - Strictly requires an explicit `DB_ADMIN_URL` with no persistent default fallback.
   - Strictly requires a designated disposable database name bearing recognized markers (`myca_test`, `myca_test_*`, `*_test*`, `*_disposable*`).
   - Explicitly rejects ordinary development database names, specifically `myca`, even if `ALLOW_DISPOSABLE_DATABASE_ROLLBACK=true` is set.
   - Validates live database identity by querying `SELECT current_database();` to ensure runtime safety against connection-string spoofing or alias redirection.
   - Updated migration rollback pipeline: CI creates an isolated, disposable database `myca_test` via `CREATE DATABASE myca_test WITH TEMPLATE myca;` specifically for rollback testing.

4. **Staging Environment Security Hardening:**
   - Upgraded `backend/app/config.py` validators for `SECRET_KEY`, `DATABASE_URL`, and `DATABASE_WORKER_URL` to enforce production-grade security rules across both `production` and `staging`.
   - Staging strictly rejects default, short, or insecure `SECRET_KEY` values, specifically rejecting keys containing `"dev-insecure"`, `"test_secret"`, `"test-secret"`, `"myca_jwt_test"`, or `"default"`.
   - Staging strictly rejects default or local database credentials (`localhost`, `127.0.0.1`, `:secret@`).
   - Staging enforces `ALLOW_DEMO_TOKENS=False` with zero exceptions.

5. **Additive Migration 0027:**
   - Down revision: `0026_gateway_nonce_and_audit`.
   - Creates `public.idp_authentication_nonces` table with primary key `nonce`, foreign keys to `organizations`, check constraints on `status` (`'ACTIVE'`, `'CONSUMED'`), and compound indexing on `(status, expires_at)`.
   - Grants least-privilege `SELECT, INSERT, UPDATE` to runtime application role `app_service_user` with zero table drop or DDL privileges.
   - Downgrade policy safely preserves nonces to prevent token replay on rollback while cleanly revoking application grants.

---

## 2. Comprehensive Verification Matrix

| Verification Scope | Standard / Invariant | Result | Status |
|---|---|---|---|
| **Strict `email_verified` Boolean** | Rejects non-boolean, string `"true"`, ints (`1`, `0`), nulls, missing | Verified in `test_auth_token.py` (55 tests) | **PASS** |
| **Strict Email Format & Normalization** | Rejects missing `@`, empty string, whitespace; normalizes `.strip().lower()` | Verified; `sub` fallback eliminated | **PASS** |
| **Provider-to-Issuer Binding** | Provider `google` rejects tokens from non-Google issuers | HTTP 401 `ISSUER_PROVIDER_MISMATCH` | **PASS** |
| **IdP Nonce Issuance & Lifecycle** | Server-issued unpredictable nonce with 5-min TTL in PostgreSQL | `POST /api/v1/auth/idp-nonce` verified | **PASS** |
| **Atomic Nonce Consumption** | Single-use consumption in DB transaction; duplicate use rejected | HTTP 401 `IDP_NONCE_REPLAY` | **PASS** |
| **Nonce Survival on Downgrade** | Nonces survive migration downgrade to 0025; block replay on re-upgrade | Tested & verified in rollback suite | **PASS** |
| **Rollback Disposable Guard** | Rejects `ENVIRONMENT=development`, rejects database name `myca` | 25/25 safety unit tests passing | **PASS** |
| **Active DB Session Verification** | Validates session via `SELECT current_database();` against allowed list | Tested & verified in rollback suite | **PASS** |
| **CI Disposable DB Isolation** | Dedicated `myca_test` created from template; isolated from app DB | Configured & verified in `.github/workflows/ci.yml` | **PASS** |
| **Staging Config Lockdown** | Production-grade `SECRET_KEY` & DB URL validation applied to `staging` | Verified in config tests | **PASS** |
| **Full Backend Test Suite** | 100% passing across all modules | **373 / 373 tests passed** | **PASS** |
| **Migration Rollback Invariants** | 100% match against independent reference (`baseline_0019_reference.json`) | 79 grants, 87 policies, 41 tables | **PASS** |
| **Populated Rollback & Preservation** | Nonce tables & evidence preserved during downgrade | Populated downgrade test passed | **PASS** |
| **RLS Security Audit** | Comprehensive RLS, grant, and cross-tenant tests | 36 tables RLS=True, 36 FORCE_RLS=True | **PASS** |
| **Frontend Production Build** | TypeScript typecheck and Vite production bundling | Built cleanly in 49.79s | **PASS** |

---

## 3. Statutory & Architectural Disclosures

1. **IdP Authentication Flow Architecture:**
   - **Mechanism:** Single-use, server-issued transaction nonces tracked in PostgreSQL (`public.idp_authentication_nonces`).
   - **Disclosure:** MyCA implements server-tracked transaction nonces for replay prevention during federated IdP identity assertions. This provides defense-in-depth against replay attacks without requiring client-side PKCE state management in headless or token-exchange API scenarios.
2. **ICAI Digital Gateway Verification:**
   - **Production Status:** **FAIL-CLOSED.**
   - **Disclosure:** ICAI does not currently provide an open, standardized public API or published trust anchor for automated third-party cryptographic verification. In production mode (`ENVIRONMENT=production`), automated digital gateway verification fails closed with `TRUST_ANCHOR_UNAVAILABLE`.
3. **Statutory Auditor Onboarding:**
   - **Operational Route:** Authenticated manual documentary evidence review (`MANUAL_DOCUMENT_REVIEWED`).
   - **Disclosure:** Auditor verification records are strictly classified as human-reviewed documentary evidence (`is_icai_verified=False`, `is_human_reviewed=True`). The platform makes zero claims of automated cryptographic ICAI gateway validation.
4. **Historical Tag Preservation:**
   - All historical tags (`v1.0.0` through `v1.0.12`) are permanently preserved, immutable, and unmodified in repository history.
