# MyCA v1.0.12 — Security Verification Report

**Release Target:** `v1.0.12`  
**Parent Commit:** `8b93e60e03bfa9e28c4c56fe6b83b96d78759613` (v1.0.11)  
**Security Verification Scope:** Environment configuration guards, demo-token lockdown, authentic IdP verification, algorithm confusion defense, RLS policy audit, and rollback safety.

---

## 1. Environment Configuration & Startup Guards

- **Strict Startup Validation:** `backend/app/config.py:validate_environment()` validates that `ENVIRONMENT` is set and matches `{'production', 'staging', 'development', 'test'}`.
  - If unset: raises `ValueError("CRITICAL CONFIGURATION ERROR: ENVIRONMENT is not configured... Automatic fallback to development is strictly prohibited.")`.
  - If invalid: raises `ValueError("CRITICAL CONFIGURATION ERROR: Invalid ENVIRONMENT...")`.
- **Demo-Token Configuration Lockdown:**
  - `ALLOW_DEMO_TOKENS: bool = False` (disabled by default).
  - Pydantic validator `validate_allow_demo_tokens` rejects `ALLOW_DEMO_TOKENS=True` in production or staging with a `ValueError`.
- **Endpoint Protection:**
  - `backend/app/modules/auth/api.py:issue_demo_token()` checks:
    1. `env not in ("development", "test")`: raises HTTP 403 `DEMO_TOKEN_PROHIBITED`.
    2. `not is_demo_token_allowed()`: raises HTTP 403 `DEMO_TOKEN_DISABLED_BY_DEFAULT`.

---

## 2. Federated Identity Provider (IdP) Cryptographic Validation

- **Module:** `backend/app/modules/auth/idp_verifier.py`
- **Supported Providers:** Google, AzureAD, Okta, Auth0 (and test-idp in test mode only).
- **Algorithm Confusion Mitigations (RFC 8725):**
  - Symmetric algorithms (`HS256`, `HS384`, `HS512`) are strictly prohibited in production and staging environments (`ALGORITHM_CONFUSION_REJECTED`).
  - Symmetric algorithms are strictly prohibited whenever an asymmetric public key trust anchor is configured, even in non-production environments.
  - Rejects tokens with `none` algorithm (`ALGORITHM_NOT_PERMITTED`).
  - Rejects tokens with unapproved algorithms in production/staging (`ASYMMETRIC_ALGORITHM_REQUIRED`).
- **Cryptographic Signature Verification:**
  - Decoded strictly with configured trusted public key (`MYCA_IDP_PUBLIC_KEY`).
  - Rejects untrusted key IDs (`kid`) with `UNTRUSTED_KEY_ID`.
- **Claims Enforcement:**
  - Issuer verification: `iss` must match `MYCA_IDP_ISSUER`.
  - Audience verification: `aud` must match `MYCA_IDP_AUDIENCE`.
  - Expiration verification: `exp` must be in the future; expired tokens raise HTTP 401 `EXPIRED_IDP_TOKEN`.
  - Not-before verification: `nbf` must not be in the future.
  - Verified email verification: `email_verified` must be `True`; unverified emails raise HTTP 401 `UNVERIFIED_EMAIL`.
- **Database Identity & Tenant Validation:**
  - Verified email matches active account in `public.global_users` (`is_active = True`).
  - Verified active tenant membership in `public.organization_memberships`.
- **Production Fail-Closed:**
  - If `MYCA_IDP_ISSUER`, `MYCA_IDP_AUDIENCE`, or `MYCA_IDP_PUBLIC_KEY` is missing in production/staging, fails closed with HTTP 503 `IDP_INTEGRATION_UNAVAILABLE`.

---

## 3. Database Role Security & RLS Enforcement

- **Database Role Privileges:**
  - `app_service_user`: `rolsuper=False`, `rolbypassrls=False`, `rolcanlogin=True`.
- **Table RLS Status (All 36 Tenant Tables):**
  - Exactly 36 of 36 tenant tables enforce both `relrowsecurity=True` AND `relforcerowsecurity=True`.
- **Reference Catalog Least Privilege:**
  - `government_schemes`, `scheme_eligibility_rules`, `statutory_auditors`: application runtime role `app_service_user` has strictly `SELECT` grants.
  - `INSERT`, `UPDATE`, `DELETE` operations are denied by PostgreSQL permission checks.

---

## 4. Migration Rollback Destructive Safety Guard

- **Guard:** `assert_disposable_database_environment(DB_ADMIN_URL)` in `backend/scripts/verify_migration_rollback_state.py`.
- **Safety Rules:**
  1. Refuses execution if `ENVIRONMENT in ("production", "staging")`.
  2. Refuses execution unless `ALLOW_DISPOSABLE_DATABASE_ROLLBACK=true` is explicitly set.
  3. Refuses execution if database target host is remote or target name contains production identifiers (`prod`, `production`, `live`).
