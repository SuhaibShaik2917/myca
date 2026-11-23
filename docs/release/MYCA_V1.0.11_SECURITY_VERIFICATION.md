# MyCA v1.0.11 — Security Verification Report

**Verification Date:** November 24, 2026  
**Target Release:** `v1.0.11`  
**Repository:** `SuhaibShaik2917/myca`  
**Parent Commit:** `4521aa8d8009f5747db8cb525ed422900a3b5e8d` (v1.0.10)  

---

## 1. Executive Summary

This report documents the security verification of MyCA v1.0.11, focusing on the resolution of the three security and migration findings identified in the v1.0.10 audit:
1. **Unauthenticated Token Issuer Hardening:** Complete production lockout of `/auth/demo-token` and establishment of authentic identity provider (IdP) federated authentication.
2. **Migration Baseline Integrity:** Restoration of historical migration 0025 to its original v1.0.9 blob hash and addition of forward-only additive migration 0026.
3. **Nonce Replay Immunity Across Rollback:** Guarantee that consumed gateway nonces survive migration downgrade and re-upgrade cycles without deletion or truncation.

---

## 2. Authentication & Authorization Controls

### 2.1 Production Lockout of `/auth/demo-token`
- **Control Implementation:** `backend/app/modules/auth/api.py:issue_demo_token()` checks both `os.getenv("ENVIRONMENT")` and `settings.ENVIRONMENT`.
- **Enforcement:** If `env == "production"`, the endpoint immediately aborts with HTTP 403 Forbidden:
  ```json
  {
    "detail": "DEMO_TOKEN_PROHIBITED_IN_PRODUCTION: /auth/demo-token is strictly disabled in production environments. Production requests must authenticate via an authorized identity provider."
  }
  ```
- **Security Impact:** Eliminates the vulnerability where an adversary with knowledge of user and organization UUIDs could acquire an administrative bearer token without presenting valid credentials.

### 2.2 Authentic Federated IdP Token Exchange
- **Endpoint:** `POST /api/v1/auth/idp-exchange` (`exchange_idp_token()`)
- **Control Implementation:**
  1. Validates IdP cryptographic assertion against configured IdP trust anchor.
  2. In production, fails closed with HTTP 503 `IDP_INTEGRATION_UNAVAILABLE` when `MYCA_IDP_ISSUER` or `MYCA_IDP_PUBLIC_KEY` is not configured.
  3. Authoritatively queries `public.global_users` for the user corresponding to the verified email address.
  4. Enforces account status checks: inactive or suspended accounts are rejected with HTTP 403 `SUSPENDED_USER`.
  5. Enforces organization tenant membership via `verify_and_resolve_tenant_membership(db, user_id, org_id)`.
  6. Generates a signed MyCA JWT containing the authenticated `sub` and `org_id`.

---

## 3. Database Security & Migration Integrity

### 3.1 Historical Migration Integrity
- **Restoration of Migration 0025:** Restored `0025_manual_review_and_nonce.py` to its exact released v1.0.9 Git blob SHA `986f9026ff49f1f8baccada97e158c8e2a336988`.
- **Additive Migration 0026:** Created `0026_gateway_nonce_and_audit.py` with revision ID length of 27 characters ($\le 32$ chars).
- **Least-Privilege Grants:** Runtime user `app_service_user` is granted strictly `SELECT, INSERT` on `public.statutory_gateway_nonce_registry`. `UPDATE` and `DELETE` privileges are strictly revoked.

### 3.2 Nonce Replay Defense Across Downgrade & Re-upgrade
- **Downgrade Invariant:** `0026:downgrade()` does not drop `public.statutory_gateway_nonce_registry`.
- **Replay Protection:** Once a nonce is recorded upon token consumption, it remains permanently stored in PostgreSQL. Re-applying migrations or rolling back to 0025 does not clear the registry.
- **Atomic Insertion:** `INSERT INTO public.statutory_gateway_nonce_registry (...) VALUES (...) ON CONFLICT (nonce) DO NOTHING;` with `db.commit()` guarantees serialization across concurrent worker threads.

---

## 4. Automated Security Test Results

| Security Test Case | Target Control | Result |
|---|---|---|
| `test_demo_token_strictly_prohibited_in_production` | `/auth/demo-token` 403 in production | **PASS** |
| `test_demo_token_allowed_in_development_and_test` | Demo token allowed in dev/test | **PASS** |
| `test_idp_token_exchange_fails_closed_in_production_without_trust_anchor` | Production IdP fails closed (503) | **PASS** |
| `test_idp_token_exchange_in_test_environment` | IdP exchange positive flow | **PASS** |
| `test_idp_token_exchange_rejects_suspended_user` | Suspended account rejection (403) | **PASS** |
| `test_gateway_nonce_persisted_across_migration_downgrade_and_reupgrade` | Nonce survival across rollback/re-upgrade | **PASS** |
| `test_conclusive_gateway_nonce_atomic_concurrency_race` | PostgreSQL atomic ON CONFLICT race | **PASS** |
| `test_manual_evidence_review_unauthenticated_rejected` | Manual review requires Bearer token | **PASS** |
| `test_manual_evidence_review_caller_supplied_roles_ignored_and_rejected` | Caller role spoofing ignored | **PASS** |
| `test_manual_evidence_review_maker_checker_rejected` | Segregation of duties enforced | **PASS** |
| `test_cross_tenant_receipt_isolation` | PostgreSQL Row-Level Security isolation | **PASS** |
