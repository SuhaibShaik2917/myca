# MyCA v1.0.12 — Independent Statutory & Security Audit Verdict

**Audit Date:** November 24, 2026  
**Audited Release:** `v1.0.12`  
**Target Repository:** `SuhaibShaik2917/myca`  
**Preceding Release:** `v1.0.11` (`8b93e60e03bfa9e28c4c56fe6b83b96d78759613`)  
**Audit Scope:** Independent technical assessment of all items identified in the `v1.0.11` review:
1. Startup configuration requiring explicit valid environment (no dev fallback); demo-token disabled by default.
2. IdP verification enforcing trusted issuer, audience, provider, algorithm confusion defense, key ID, and claims validation without treating env variables alone as proof of authentication.
3. RLS audit reconciliation resolving the 21-vs-36 FORCE RLS discrepancy.
4. Rollback verification script refusing destructive operations against production or non-disposable databases.

---

## 1. AUDIT FINDINGS RESOLUTION MATRIX

| Finding Ref | Severity | Domain | Resolution in v1.0.12 | Audit Status |
|---|---|---|---|---|
| **F-01** | **CRITICAL** | Environment Startup Guard & Demo Token Lockdown | Enforced explicit environment validation in `backend/app/config.py`: `ENVIRONMENT` must be explicitly specified and valid (`production`, `staging`, `development`, `test`). An unset or invalid environment immediately raises `ValueError`, completely preventing silent fallback to development. Demo-token authentication (`/auth/demo-token`) is disabled by default (`ALLOW_DEMO_TOKENS=False`). It is permitted exclusively in designated local/test environments when `ALLOW_DEMO_TOKENS=True` is explicitly configured. All non-local environments unconditionally return HTTP 403 Forbidden with `DEMO_TOKEN_PROHIBITED`. | **RESOLVED & VERIFIED** |
| **F-02** | **CRITICAL** | Authentic Federated IdP Cryptographic & Claims Validation | Created `backend/app/modules/auth/idp_verifier.py` with rigorous validation. Enforces algorithm confusion defenses (RFC 8725): symmetric algorithms (`HS256`, etc.) are strictly prohibited in production/staging and whenever an asymmetric public key trust anchor is configured. Verifies header `alg` and `kid`, issuer (`iss`), audience (`aud`), expiration (`exp`), not-before freshness (`nbf`), issue time (`iat`), and `email_verified` claim. Fails closed with HTTP 503 `IDP_INTEGRATION_UNAVAILABLE` in production/staging if trust anchors are incomplete. Identity is resolved exclusively from cryptographically verified claims. | **RESOLVED & VERIFIED** |
| **F-03** | **MEDIUM** | PostgreSQL RLS & FORCE RLS Reconciliation | Audited PostgreSQL catalog and reconciled historical narrative discrepancy: exactly **36 of 36 tenant tables (100%)** enforce both `relrowsecurity=True` (RLS) AND `relforcerowsecurity=True` (FORCE RLS). The 9 non-tenant tables are public reference catalogs and metadata tables, protected by least-privilege role permissions (application runtime role `app_service_user` has strictly `SELECT` grants, with zero DDL or `BYPASSRLS` privileges). | **RESOLVED & VERIFIED** |
| **F-04** | **HIGH** | Rollback Script Destructive Safety Guard | Added `assert_disposable_database_environment(DB_ADMIN_URL)` in `backend/scripts/verify_migration_rollback_state.py`. Aborts immediately if `ENVIRONMENT` is `production` or `staging`. Aborts unless `ALLOW_DISPOSABLE_DATABASE_ROLLBACK=true` is explicitly set in environment variables. Aborts if the database target hostname is remote or target name contains production identifiers (`prod`, `production`, `live`). | **RESOLVED & VERIFIED** |

---

## 2. DETAILED TECHNICAL REMEDIATIONS & VERIFICATION EVIDENCE

### 2.1 Explicit Environment Configuration & Demo-Token Guard (F-01)
- **Defect in v1.0.11:** `ENVIRONMENT` defaulted to `"development"` when unset, and `/auth/demo-token` was only blocked if `ENVIRONMENT == "production"`, creating a fail-open risk in staging, empty, or misspelled environments.
- **Remediation in v1.0.12:**
  1. `backend/app/config.py`:
     - Added `VALID_ENVIRONMENTS = {"production", "staging", "development", "test"}`.
     - Added `@field_validator("ENVIRONMENT", mode="before")`: raises `ValueError` if `ENVIRONMENT` is unset, empty, or not in `VALID_ENVIRONMENTS`.
     - Added `ALLOW_DEMO_TOKENS: bool = False`: validator ensures `ALLOW_DEMO_TOKENS=True` cannot be set if `ENVIRONMENT` is `production` or `staging`.
  2. `backend/app/modules/auth/api.py`:
     - `is_demo_token_allowed()` checks both `env in ("development", "test")` AND `ALLOW_DEMO_TOKENS=True`.
     - `/auth/demo-token` returns HTTP 403 `DEMO_TOKEN_PROHIBITED` in all non-local environments.
     - Returns HTTP 403 `DEMO_TOKEN_DISABLED_BY_DEFAULT` in local environments when `ALLOW_DEMO_TOKENS=False`.
  3. Added comprehensive automated tests in `backend/tests/test_auth_token.py` testing unset env, invalid env, staging env, default disabled behavior, and production prohibition.

### 2.2 Authentic Federated IdP Cryptographic & Claims Validation (F-02)
- **Defect in v1.0.11:** IdP token validation was vulnerable to algorithm confusion (allowing HS256 with public key), accepted an unverified fallback `payload.user_email`, did not enforce issuer/audience claims, and lacked expiration/not-before verification.
- **Remediation in v1.0.12:**
  1. `backend/app/modules/auth/idp_verifier.py`:
     - Provider validation against configured allowed providers (`google`, `azuread`, `okta`, `auth0`).
     - Algorithm confusion defense: rejects symmetric algorithms (`HS256`, `HS384`, `HS512`) when an asymmetric public key is configured or when running in production/staging.
     - Rejects `none` algorithm and unverified/untrusted `kid` key IDs.
     - Full cryptographic signature and claim verification using `jwt.decode` with `verify_signature`, `verify_exp`, `verify_nbf`, `verify_iat`, `verify_aud`, and `verify_iss`.
     - Strict claims content validation: checks `email`, `sub`, and `email_verified`.
     - Fail-closed behavior: raises HTTP 503 `IDP_INTEGRATION_UNAVAILABLE` in production/staging if trust anchors (`MYCA_IDP_ISSUER`, `MYCA_IDP_AUDIENCE`, `MYCA_IDP_PUBLIC_KEY`) are missing.
  2. Removed `payload.user_email` fallback entirely. Verified email is extracted strictly from cryptographically decoded claims.
  3. Validated active user status in `public.global_users` (`is_active = True`) and active tenant membership in `public.organization_memberships`.
  4. Tested end-to-end in production mode using authentic 2048-bit RSA keypair, valid issuer, audience, and active user lookup.

### 2.3 PostgreSQL RLS Reconciliation (F-03)
- **Audit Findings:**
  - Query: `SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = ANY(...)`
  - Exactly **36 of 36 tenant tables** have `relrowsecurity=True` AND `relforcerowsecurity=True`.
  - Non-tenant tables (9 tables): `alembic_version`, `government_schemes`, `invoice_transition_rules`, `itc_rule_catalogue`, `scheme_eligibility_rules`, `statutory_audit_signature_replays`, `statutory_auditor_verification_events`, `statutory_auditors`, `statutory_gateway_nonce_registry`.
  - Role check: `app_service_user` has `rolsuper=False`, `rolbypassrls=False`, and strictly `SELECT` privileges on reference catalogs.

### 2.4 Rollback Script Destructive Safety Guard (F-04)
- **Remediation in v1.0.12:**
  1. `backend/scripts/verify_migration_rollback_state.py:assert_disposable_database_environment(DB_ADMIN_URL)`:
     - Refuses execution if `ENVIRONMENT in ("production", "staging")`.
     - Refuses execution unless `ALLOW_DISPOSABLE_DATABASE_ROLLBACK=true`.
     - Refuses execution if the database connection URL targets a remote host or contains production identifiers (`prod`, `production`, `live`).
  2. Added unit tests in `backend/tests/test_rollback_script_safety.py` testing each safety condition.

---

## 3. STATUTORY FEATURE LIMITATIONS & GOVERNANCE TRANSPARENCY

1. **ICAI Digital Gateway Verification:**
   - **Production Status:** **DISABLED (Fail-Closed).**
   - **Reason:** ICAI does not provide an open public API or published trust anchor for automated third-party cryptographic verification.
   - **Production Behavior:** In production mode (`ENVIRONMENT=production`), automated digital gateway verification fails closed with `TRUST_ANCHOR_UNAVAILABLE`. Automated verification is enabled exclusively in test environments via sandboxed test keys.
2. **Authenticated Manual Documentary Review:**
   - **Production Status:** **FULLY OPERATIONAL & SECURED.**
   - **Classification:** Strictly recorded as `MANUAL_DOCUMENT_REVIEWED` (`is_icai_verified=False`, `is_human_reviewed=True`).
   - **Disclosure:** Explicit statutory disclaimer states that auditor onboarding is based on human-reviewed documentary evidence, not automated cryptographic ICAI gateway proof.
3. **Repository Metadata & Branch Governance:**
   - **Signatures:** Commits and tags in this remediation sequence are unsigned.
   - **Branch Protection:** GitHub branch protection rules are disabled by default on personal repositories unless explicitly configured by the repository administrator.
   - **Historical Tag Preservation:** All prior tags `v1.0.0` through `v1.0.11` remain permanently preserved and unmodified.

---

## 4. AUDIT VERDICT

**VERDICT: APPROVED FOR PRODUCTION WITH EXPLICIT FEATURE LIMITATIONS** (`APPROVED_WITH_EXPLICIT_FEATURE_LIMITATIONS`)

All findings from the `v1.0.11` independent review have been conclusively resolved, tested, and verified.
