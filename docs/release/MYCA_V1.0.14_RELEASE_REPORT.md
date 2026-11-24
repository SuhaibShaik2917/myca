# MyCA v1.0.14 Release Report

**Release Tag:** `v1.0.14`  
**Target Repository:** `SuhaibShaik2917/myca`  
**Parent Release:** `v1.0.13` candidate commit (`f0dd7627c3165a9cf39598f123462131ccf08eba`)  
**Release Type:** Critical Authentication Hardening, IdP Nonce PostgreSQL RLS Tenant Isolation, Browser PKCE Session Binding, Nonce Rate Limiting & Abuse Defense, Exact Canonical Issuer Matching, Disposable Database Marker Rollback Safety  
**Release Verdict:** **APPROVED AS REVISED RELEASE CANDIDATE (PRODUCTION SIGN-OFF WITHHELD / NOT PRODUCTION APPROVED FOR UNRESTRICTED PUBLIC USE UNTIL EXTERNAL PRODUCTION JWKS & ICAI GATEWAY INTEGRATIONS ARE OPERATIONAL)**  

---

## 1. Executive Summary

MyCA `v1.0.14` delivers a comprehensive security and architectural remediation directly resolving all findings raised in the independent review of `v1.0.13`. This release eliminates trust boundary gaps across federated identity authentication, database multi-tenancy, and release operations:

1. **Database-Enforced Tenant Isolation for Federated IdP Nonces:**
   - Provisioned via additive migration `0028_idp_nonces_rls.py`.
   - Enabled `ROW LEVEL SECURITY` and `FORCE ROW LEVEL SECURITY` on `public.idp_authentication_nonces`.
   - Configured policy `idp_auth_nonces_service_policy` for least-privilege application role `app_service_user` enforcing `org_id = (NULLIF(current_setting('app.current_org', true), ''))::uuid`.
   - Dynamic RLS audit (`backend/scripts/audit_rls_security_comprehensive.py`) discovered all 37 tenant tables (35 `org_id` scoped tables + `organizations` and `global_users`), asserting 100% RLS and FORCE RLS coverage.

2. **Browser Session Binding & PKCE S256 Cryptographic Verification:**
   - Defeats cross-client authorization code injection, CSRF, and session fixation attacks.
   - `POST /api/v1/auth/idp-nonce` generates a cryptographically secure random session token, stores its SHA-256 digest in `public.idp_authentication_nonces.session_binding_hash`, and returns it via `myca_auth_session` HttpOnly cookie and JSON response body.
   - Nonce issuance accepts optional PKCE `code_challenge` (S256).
   - `POST /api/v1/auth/idp-exchange` requires the matching browser session token (via cookie, header, or payload) and PKCE `code_verifier`, verifying SHA-256 digests in constant time before token consumption.

3. **Abuse Protection, Rate Limiting & Safe Nonce Cleanup:**
   - Implemented in-memory sliding-window `NonceRateLimiter` protecting `POST /api/v1/auth/idp-nonce`:
     - Client IP rate limit: maximum 20 nonce issuance requests per minute per IP.
     - Organization rate limit: maximum 30 nonce issuance requests per minute per organization.
   - Implemented active nonce concurrency cap: maximum 5 active unconsumed nonces per organization.
   - Automatic safe cleanup: during nonce issuance, expired unconsumed (`ACTIVE`) nonces are automatically pruned, while consumed (`CONSUMED`) nonces are strictly preserved for permanent replay protection.

4. **Exact Canonical Provider Trust Configuration:**
   - Replaced substring matching in `backend/app/modules/auth/idp_verifier.py` with exact canonical issuer sets (`PROVIDER_CANONICAL_ISSUERS`):
     - `google`: `{"https://accounts.google.com", "accounts.google.com"}`
     - `azuread`: `{"https://login.microsoftonline.com/common/v2.0", "https://sts.windows.net/common/"}`
     - `okta`: verified exact canonical domain bindings.
     - `auth0`: verified exact canonical domain bindings.
   - Prohibited loose domain substring matches (e.g. `malicious-google.com` or attacker-controlled subdomains).
   - Enforced strict algorithm restrictions via `PROVIDER_ALLOWED_ALGORITHMS` (`RS256`, `RS384`, `RS512`, `ES256`, `ES384`, `ES512`).

5. **Disposability Proof & Rollback Safety Invariants:**
   - Implemented database-level marker table `public.disposable_database_marker` with mandatory verification token `MYCA_VERIFIED_DISPOSABLE_TEST_DATABASE_TOKEN_V1_0_14`.
   - `backend/scripts/verify_migration_rollback_state.py` validates marker existence and token before executing any destructive operations.
   - Asserts that Alembic and psycopg target the exact same database (`current_database()`).
   - Verified clean bidirectional rollback through all historical revisions back to `0019_m3_gstr3b_filings` matching the independent baseline reference (`baseline_0019_reference.json`) with zero deviation across 79 grants, 87 policies, and 41 tables.

6. **Fixture Reliability & Multithreaded Concurrency Race Tests:**
   - Eliminated exception swallowing in test setup helpers.
   - Added multithreaded concurrent race tests (5 threads simultaneously attempting to exchange the exact same nonce claim), proving exactly 1 thread succeeds while 4 threads are rejected with HTTP 401 `IDP_NONCE_REPLAY`.

---

## 2. Test & Verification Summary

| Verification Gate | Requirement | Actual Result | Status |
|---|---|---|---|
| **Backend Test Suite** | All unit, integration, and security tests pass | **387 / 387 passed** (0 failures, 1 warning) | **PASS** |
| **Rollback Safety Suite** | Disposable marker & connection verification | **31 / 31 passed** | **PASS** |
| **RLS Security Audit** | Dynamic discovery of all tenant tables; RLS & FORCE RLS | **37 / 37 tables** (100% RLS=True, 100% FORCE_RLS=True) | **PASS** |
| **Cross-Tenant Isolation** | Read, UPDATE, DELETE, INSERT spoofing across 12 domains | 100% blocked under `app_service_user` | **PASS** |
| **Catalogue Immutability** | Reference & system tables write revocation | INSERT/UPDATE/DELETE denied | **PASS** |
| **Migration Rollback State** | Downgrade to 0019 against independent baseline | 79 grants, 87 policies, 41 tables match 100% | **PASS** |
| **Populated Rollback** | Preserves evidence, audit events, gateway nonces & IdP nonces | Verified across downgrade and re-upgrade cycles | **PASS** |
| **Frontend Production Build** | TypeScript typecheck and Vite bundle | 1583 modules compiled cleanly in 24.73s | **PASS** |

---

## 3. Disclosures & Release Governance

1. **Production Sign-Off Withholding:**
   - Automated ICAI verification and live external IdP token verification require live operational integration with statutory portals and external vendor JWKS endpoints.
   - In accordance with independent audit guidelines, this release is classified as **APPROVED AS REVISED RELEASE CANDIDATE**. Production sign-off is withheld until live external JWKS and ICAI gateway endpoints are operational in production.

2. **Git Commit & Tag Signing:**
   - Commits and tags in this repository are currently unsigned. Repository branch protection rules are not enabled on the GitHub repository.

3. **Release Governance:**
   - Historical tags `v1.0.0` through `v1.0.13` remain permanently untouched.
   - The annotated tag `v1.0.14` is created and pushed strictly after branch CI succeeds on both `master` and `remediation/audit-findings-phase3`.
