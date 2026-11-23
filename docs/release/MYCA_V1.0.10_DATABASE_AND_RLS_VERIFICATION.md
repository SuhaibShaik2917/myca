# MyCA v1.0.10 — Database, RLS & Migration Verification Report

**Verification Date:** November 24, 2026  
**Release:** `v1.0.10`  
**Database Engine:** PostgreSQL 16  
**Head Revision:** `0025_manual_review_and_nonce`  

---

## 1. MIGRATION 0025 ENHANCEMENT SUMMARY

`backend/alembic/versions/0025_manual_review_and_nonce.py`:
1. **Dedicated Nonce Registry Table:**
   - Table `public.statutory_gateway_nonce_registry` with `nonce VARCHAR(128) PRIMARY KEY`.
   - Least privilege grants: `REVOKE ALL ON public.statutory_gateway_nonce_registry FROM app_service_user; GRANT SELECT, INSERT ON public.statutory_gateway_nonce_registry TO app_service_user;`
2. **Safe Populated Downgrade Policy:**
   - Transition rule:
     ```sql
     UPDATE public.statutory_auditors
         SET verification_status = 'PENDING_INDEPENDENT_VERIFICATION'
         WHERE verification_status = 'MANUAL_DOCUMENT_REVIEWED';
     ```
   - Drops `uq_statutory_auditors_gateway_nonce` and `statutory_gateway_nonce_registry`.
   - Drops and resets `chk_statutory_auditors_verification_status` to 0024 baseline.
   - Preserves 100% of documentary evidence (`evidence_sha256`), reviewer session references (`reviewer_session_ref`), audit logs, and timestamps.
3. **Safe Re-Upgrade Restoration:**
   - On re-upgrade to Head (0025), records with `verification_source = 'MANUAL_EVIDENCE_REVIEW'` and valid evidence references are restored back to `verification_status = 'MANUAL_DOCUMENT_REVIEWED'`.

---

## 2. MIGRATION ROLLBACK & POPULATED RECORD VERIFICATION

Script: `backend/scripts/verify_migration_rollback_state.py`  
Output: **ALL INVARIANTS 100% VERIFIED**

| Verification Step | Target Baseline | Result | Status |
|---|---|---|---|
| **Step 1: Baseline 0019** | Exact Match with `baseline_0019_reference.json` | 79 grants, 87 policies, 41 tables matched 100% | **PASS** |
| **0019 Reference SHA256** | `bfe3e3a342b56732d1d0cf43f8edfc50391df1020a2c487f22fb5834e9503373` | Exact SHA256 Match | **PASS** |
| **Step 1b: Fresh 0022** | Contains Historical Seeds | Classified as `LEGACY_SEEDS_PRESENT_IN_HISTORICAL_0022` | **PASS** |
| **Step 2: Head 0025** | Clean Production State | 112 grants, 95 policies, 0 fake auditors | **PASS** |
| **Step 2-POPULATED: Downgrade** | Populated `MANUAL_DOCUMENT_REVIEWED` records | Transitioned to `PENDING_INDEPENDENT_VERIFICATION`; evidence SHA-256 and session ref 100% preserved; audit event 100% preserved | **PASS** |
| **Step 2-POPULATED: Re-Upgrade** | Re-upgrade to Head | Restored to `MANUAL_DOCUMENT_REVIEWED` with intact evidence | **PASS** |
| **Step 2a: Downgrade 0024** | Clean Downgrade | 0 fake auditors, `AUDITORS_VERIFIED_CLEAN` | **PASS** |
| **Step 2b: Downgrade 0023** | Clean Downgrade | 0 fake auditors, `AUDITORS_VERIFIED_CLEAN` | **PASS** |
| **Step 2c: Downgrade 0022** | Clean Downgrade | 0 fake auditors, `AUDITORS_VERIFIED_CLEAN` | **PASS** |
| **Step 2d: Re-upgrade Head** | Re-upgrade to 0025 | 112 grants, 95 policies, `AUDITORS_VERIFIED_CLEAN` | **PASS** |
| **Step 3: Downgrade 0020** | Clean Downgrade | 105 grants, 95 policies, table not present | **PASS** |
| **Step 4: Downgrade 0019** | Exact Anchor Match | All 79 grants, 87 policies, table RLS match independent reference | **PASS** |
| **Step 5: Final Head 0025** | Final Re-upgrade | 112 grants, 95 policies, production state fully restored | **PASS** |

---

## 3. ROW-LEVEL SECURITY (RLS) COMPREHENSIVE AUDIT

Script: `backend/scripts/audit_rls_security_comprehensive.py`  
Output: **ALL 36 TABLES FORCED RLS, 100% SECURE**

1. **Role Security:**
   - Role `app_service_user`: `Superuser=False`, `BypassRLS=False`, `CanLogin=True`.
2. **Forced Table RLS:**
   - All 36 application tables have `ROW LEVEL SECURITY = True` and `FORCE ROW LEVEL SECURITY = True`.
3. **Catalogue Write Denial Audit:**
   - Write attempts under `app_service_user` on `government_schemes`, `scheme_eligibility_rules`, `statutory_auditors`, and `statutory_audit_signature_replays` all denied (`PERMISSION_DENIED`).
4. **Cross-Tenant Isolation (11 Core Domains):**
   - Read isolation: Tenant B cannot read Tenant A records across all 11 domains.
   - Update isolation: Tenant B update affects 0 rows.
   - Delete isolation: Tenant B delete affects 0 rows.
   - Insert isolation: Tenant B spoofing Tenant A context rejected with `SQLSTATE 42501 (RLS_WITH_CHECK_VIOLATION)`.
   - Null context isolation: Under null context, all 11 tables return 0 rows.
