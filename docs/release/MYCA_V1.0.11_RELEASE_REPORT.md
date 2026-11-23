# MyCA v1.0.11 Release Report

**Release Tag:** `v1.0.11`  
**Target Repository:** `SuhaibShaik2917/myca`  
**Parent Release:** `v1.0.10` (`4521aa8d8009f5747db8cb525ed422900a3b5e8d`)  
**Release Type:** Critical Security & Database Migration Integrity Hardening  
**Release Recommendation:** **APPROVED FOR PRODUCTION WITH EXPLICIT FEATURE LIMITATIONS** (`APPROVED_WITH_EXPLICIT_FEATURE_LIMITATIONS`)  

---

## 1. Executive Summary

MyCA `v1.0.11` resolves the three remaining defects identified in the independent review of `v1.0.10`:
1. **Critical 1 — Unauthenticated Demo-Token Lockout in Production:** Completely disabled `/api/v1/auth/demo-token` when running in production (`ENVIRONMENT=production`), returning HTTP 403 Forbidden with `DEMO_TOKEN_PROHIBITED_IN_PRODUCTION`. Implemented authentic federated Identity Provider (IdP) token exchange endpoint `POST /api/v1/auth/idp-exchange` with fail-closed security and validated account status.
2. **Critical 2 — Migration 0025 Restoration & Forward-Only Migration 0026:** Restored `0025_manual_review_and_nonce.py` to its exact released v1.0.9 Git blob SHA (`986f9026ff49f1f8baccada97e158c8e2a336988`), preserving clean forward upgrades for existing v1.0.9 installations. Created forward-only additive migration `0026_gateway_nonce_and_audit.py` (27 characters $\le 32$) provisioning `public.statutory_gateway_nonce_registry`.
3. **High 3 — Gateway Nonce History Preservation Across Rollback:** Guaranteed that `0026:downgrade()` does not drop or truncate `public.statutory_gateway_nonce_registry`. Consumed nonces remain permanently stored in PostgreSQL across downgrade and re-upgrade cycles, eliminating replay vulnerabilities.

---

## 2. Verification Summary

| Verification Category | Target Standard | Result | Status |
|---|---|---|---|
| **Demo Token Production Lockout** | HTTP 403 `DEMO_TOKEN_PROHIBITED_IN_PRODUCTION` when `ENVIRONMENT=production` | Verified in unit & integration tests | **PASS** |
| **Authentic IdP Token Exchange** | Authenticated principal email mapped to active `global_users` and `organization_memberships` | Verified fail-closed (503) and test flow | **PASS** |
| **Historical 0025 Blob Hash** | `986f9026ff49f1f8baccada97e158c8e2a336988` | Exact Git blob SHA match | **PASS** |
| **Additive Migration 0026** | Forward-only upgrade from v1.0.9 database | Applied cleanly to 0025 database | **PASS** |
| **Nonce Rollback Preservation** | Nonce history persists across downgrade/re-upgrade; replay rejected | Verified in rollback script and pytest | **PASS** |
| **Full Backend Test Suite** | 100% passing across all modules | **305 / 305 tests passed** | **PASS** |
| **Migration Rollback Invariants** | 100% exact match against trusted reference (`baseline_0019_reference.json`) | 79 grants, 87 policies, 41 tables | **PASS** |
| **RLS Security Audit** | Comprehensive RLS, grant, and cross-tenant tests | All 36 tables RLS, 21 FORCE RLS | **PASS** |
| **CI Diagnostics** | Database connectivity & clean statutory tables | All 5 service roles connected | **PASS** |
| **Frontend Production Build** | TypeScript typecheck and Vite bundling | Built cleanly in 29.92s | **PASS** |

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
   - **Historical Tag Preservation:** All prior tags `v1.0.0` through `v1.0.10` remain permanently preserved and unmodified.
