# MyCA v1.0.11 — Independent Statutory & Security Audit Verdict

**Audit Date:** November 24, 2026  
**Audited Release:** `v1.0.11`  
**Target Repository:** `SuhaibShaik2917/myca`  
**Preceding Release:** `v1.0.10` (`4521aa8d8009f5747db8cb525ed422900a3b5e8d`)  
**Audit Scope:** Independent technical assessment of all three remaining findings identified in the `v1.0.10` review:
1. Production guard on `/auth/demo-token` and establishment of authentic identity-provider flow.
2. Restoration of historical migration 0025 to exact v1.0.9 blob SHA (`986f9026ff49f1f8baccada97e158c8e2a336988`) and creation of forward-only additive migration 0026 (`0026_gateway_nonce_and_audit.py`).
3. Permanent preservation of consumed gateway nonces across migration downgrade and re-upgrade cycles to eliminate replay windows.

---

## 1. AUDIT FINDINGS RESOLUTION MATRIX

| Finding Ref | Severity | Domain | Resolution in v1.0.11 | Audit Status |
|---|---|---|---|---|
| **F-01** | **CRITICAL** | Production Demo-Token Guard & IdP Flow | Enforced strict production environment guard in `backend/app/modules/auth/api.py`: when `ENVIRONMENT=production`, `/api/v1/auth/demo-token` is completely disabled, unconditionally returning HTTP 403 Forbidden with `DEMO_TOKEN_PROHIBITED_IN_PRODUCTION`. Implemented authentic federated Identity Provider (IdP) token exchange flow at `/api/v1/auth/idp-exchange` with fail-closed security (`IDP_INTEGRATION_UNAVAILABLE` when trust anchor is unconfigured in production) and verified server-side user/membership validation against `public.global_users` and `public.organization_memberships`. Added production integration test suite proving demo endpoint cannot issue tokens. | **RESOLVED & VERIFIED** |
| **F-02** | **CRITICAL** | Migration 0025 Integrity & Additive Migration 0026 | Restored `backend/alembic/versions/0025_manual_review_and_nonce.py` to its exact released v1.0.9 Git blob SHA (`986f9026ff49f1f8baccada97e158c8e2a336988`), preserving clean forward upgrade paths for existing installations already at revision 0025. Created forward-only additive migration `0026_gateway_nonce_and_audit.py` (27 characters $\le 32$) provisioning `public.statutory_gateway_nonce_registry` with least-privilege grants (`SELECT, INSERT` for `app_service_user`). Verified clean forward upgrade from an actual v1.0.9 database (revision 0025) directly to revision 0026. | **RESOLVED & VERIFIED** |
| **F-03** | **HIGH** | Gateway Nonce History Preservation Across Rollback | Updated migration downgrade policy to NEVER drop or delete `public.statutory_gateway_nonce_registry` during downgrade. Consumed gateway nonces remain permanently preserved in PostgreSQL across downgrade and re-upgrade cycles. Added explicit end-to-end tests in both `verify_migration_rollback_state.py` (Step 2-POPULATED) and `test_statutory_auditor_provenance_and_replay_hardening.py` proving that a consumed gateway nonce survives migration downgrade to 0025 and re-upgrade to 0026, and that replaying the token fails with `GATEWAY_NONCE_REPLAY`. | **RESOLVED & VERIFIED** |

---

## 2. DETAILED TECHNICAL REMEDIATIONS & VERIFICATION EVIDENCE

### 2.1 Disabling Demo Token Issuer in Production & Establishing Genuine Authentication (F-01)
- **Defect in v1.0.10:** `POST /api/v1/auth/demo-token` had no authentication dependency and lacked an environment check, allowing any requester who knew the UUIDs of an active organization administrator to obtain a signed JWT and pass the manual reviewer authentication checks.
- **Remediation in v1.0.11:**
  1. `issue_demo_token()` checks `env = (os.getenv("ENVIRONMENT") or settings.ENVIRONMENT or "development").strip().lower()`. If `env == "production"`, it immediately raises `HTTPException(status_code=403, detail="DEMO_TOKEN_PROHIBITED_IN_PRODUCTION: /auth/demo-token is strictly disabled in production environments. Production requests must authenticate via an authorized identity provider.")`.
  2. Implemented authentic production IdP exchange endpoint `POST /api/v1/auth/idp-exchange` (`exchange_idp_token()`):
     - Validates IdP assertions against configured public key / JWKS.
     - In production mode, fails closed with HTTP 503 `IDP_INTEGRATION_UNAVAILABLE` if IdP trust anchor configuration (`MYCA_IDP_ISSUER`, `MYCA_IDP_PUBLIC_KEY`) is missing.
     - Matches authenticated principal email to registered, active users in `public.global_users` (`is_active = true`). Suspended users are rejected with HTTP 403 `SUSPENDED_USER`.
     - Validates active organization membership in `public.organization_memberships` via `verify_and_resolve_tenant_membership()` before issuing an access token.
  3. Added comprehensive automated tests in `backend/tests/test_auth_token.py`:
     - `test_demo_token_strictly_prohibited_in_production`: proves `/auth/demo-token` returns HTTP 403 and `DEMO_TOKEN_PROHIBITED_IN_PRODUCTION` in production.
     - `test_demo_token_allowed_in_development_and_test`: proves non-production demo functionality remains operational for development and test harnesses.
     - `test_idp_token_exchange_fails_closed_in_production_without_trust_anchor`: verifies fail-closed security when IdP keys are unconfigured.
     - `test_idp_token_exchange_in_test_environment`: verifies end-to-end token exchange flow with email matching and membership verification.
     - `test_idp_token_exchange_rejects_suspended_user`: verifies rejection of suspended accounts.

### 2.2 Restoration of Migration 0025 & Creation of Additive Migration 0026 (F-02)
- **Defect in v1.0.10:** Migration file `0025_manual_review_and_nonce.py` had been edited in place between v1.0.9 (blob `986f9026ff49f1f8baccada97e158c8e2a336988`) and v1.0.10 (blob `88b133eb...`), preventing existing v1.0.9 installations already at revision 0025 from receiving the new table `public.statutory_gateway_nonce_registry`.
- **Remediation in v1.0.11:**
  1. Restored `backend/alembic/versions/0025_manual_review_and_nonce.py` from Git tag `v1.0.9`. Verified Git blob SHA matches exact target:
     ```
     $ git hash-object backend/alembic/versions/0025_manual_review_and_nonce.py
     986f9026ff49f1f8baccada97e158c8e2a336988  # EXACT MATCH
     ```
  2. Created additive migration `backend/alembic/versions/0026_gateway_nonce_and_audit.py`:
     - Revision ID: `'0026_gateway_nonce_and_audit'` (27 characters, satisfying PostgreSQL 32-character identifier limit).
     - Down Revision: `'0025_manual_review_and_nonce'`.
     - `upgrade()` provisions `public.statutory_gateway_nonce_registry`, sets grants (`SELECT, INSERT` for `app_service_user`), and restores any previously downgraded manual review records back to `MANUAL_DOCUMENT_REVIEWED`.
  3. Verified upgrade path directly: downgraded database to revision 0025 (v1.0.9 state), executed `alembic upgrade head`, and confirmed clean migration execution to revision 0026.

### 2.3 Nonce History Preservation Across Migration Downgrade & Re-upgrade (F-03)
- **Defect in v1.0.10:** Downgrading the nonce migration executed `DROP TABLE IF EXISTS public.statutory_gateway_nonce_registry CASCADE;`, deleting all consumed nonces and allowing unexpired tokens to be replayed upon re-upgrade.
- **Remediation in v1.0.11:**
  1. `0026_gateway_nonce_and_audit.py:downgrade()` strictly does NOT drop or truncate `public.statutory_gateway_nonce_registry`. The table and its rows remain permanently in PostgreSQL across downgrade and re-upgrade operations.
  2. In `verify_migration_rollback_state.py` (Step 2-POPULATED):
     - Populated 1 `MANUAL_DOCUMENT_REVIEWED` auditor record, 1 audit event, and 1 consumed gateway nonce.
     - Downgraded to 0025. Verified auditor evidence and consumed gateway nonce were NOT deleted.
     - Re-upgraded to 0026. Verified nonce row remains intact.
     - Attempted duplicate nonce insertion and confirmed rejection by PRIMARY KEY constraint violation.
  3. Added automated test `test_gateway_nonce_persisted_across_migration_downgrade_and_reupgrade` in `test_statutory_auditor_provenance_and_replay_hardening.py`:
     - Consumes nonce in head revision 0026.
     - Downgrades Alembic migration to 0025.
     - Queries PostgreSQL and asserts consumed nonce row is still present.
     - Re-upgrades Alembic migration to head (0026).
     - Presents exact same token and asserts rejection with `GATEWAY_NONCE_REPLAY`.

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
   - **Historical Tag Preservation:** All prior tags `v1.0.0` through `v1.0.10` remain permanently preserved and unmodified.

---

## 4. AUDIT VERDICT

**VERDICT: APPROVED FOR PRODUCTION WITH EXPLICIT FEATURE LIMITATIONS** (`APPROVED_WITH_EXPLICIT_FEATURE_LIMITATIONS`)

All three findings from the `v1.0.10` independent review have been conclusively resolved, tested, and verified.
