# MyCA v1.0.9 — Database Migrations, RLS & Role Security Verification

**Date:** November 24, 2026  
**Release:** `v1.0.9`  
**Database Engine:** PostgreSQL 16  
**Head Migration:** `0025_manual_review_and_nonce`  
**Verification Status:** **100% VERIFIED**

---

## 1. ADDITIVE MIGRATION 0025 SPECIFICATION

**File:** `backend/alembic/versions/0025_manual_review_and_nonce.py`  
**Revision:** `0025_manual_review_and_nonce` (length 28 <= 32 chars)  
**Revises:** `0024_auditor_authentic_evidence`

### DDL Operations:
1. **Extend Verification Status Check Constraint:**
   ```sql
   ALTER TABLE public.statutory_auditors
       DROP CONSTRAINT IF EXISTS chk_statutory_auditors_verification_status;

   ALTER TABLE public.statutory_auditors
       ADD CONSTRAINT chk_statutory_auditors_verification_status
       CHECK (verification_status IN (
           'MANUAL_DOCUMENT_REVIEWED',
           'PENDING_INDEPENDENT_VERIFICATION',
           'INDEPENDENTLY_VERIFIED',
           'VERIFICATION_FAILED',
           'SUSPENDED',
           'REVOKED'
       ));
   ```
2. **Partial Unique Index on Gateway Nonce:**
   ```sql
   CREATE UNIQUE INDEX IF NOT EXISTS uq_statutory_auditors_gateway_nonce
       ON public.statutory_auditors (gateway_response_id)
       WHERE gateway_response_id IS NOT NULL;
   ```
3. **Audit Ledger Least-Privilege Role Grants:**
   ```sql
   REVOKE ALL ON public.statutory_auditor_verification_events FROM app_service_user;
   GRANT SELECT, INSERT ON public.statutory_auditor_verification_events TO app_service_user;
   ```

---

## 2. MULTI-CYCLE MIGRATION ROLLBACK VERIFICATION RESULTS

Executed via `scripts/verify_migration_rollback_state.py` against running PostgreSQL 16:

| Migration Step | Action | Expected State | Verified Actual State | Status |
|---|---|---|---|---|
| **Step 1** | Downgrade to `0019_m3_gstr3b_filings` | `TABLE_NOT_PRESENT`, exact match against independent reference | 79 grants, 87 policies, 41 tables (SHA256 verified) | **PASS** |
| **Step 1b** | Historical upgrade to `0022_statutory_auditors_replay` | `LEGACY_SEEDS_PRESENT_IN_HISTORICAL_0022` | Table exists with 3 legacy demo records | **PASS** |
| **Step 2** | Upgrade to Head (`0025_manual_review_and_nonce`) | `AUDITORS_VERIFIED_CLEAN`, 110 grants, 95 policies, index present | Clean (0 fake records), 110 grants, 95 policies, `uq_statutory_auditors_gateway_nonce` present | **PASS** |
| **Step 2a** | Downgrade to `0024_auditor_authentic_evidence` | `AUDITORS_VERIFIED_CLEAN`, 110 grants, 95 policies, index dropped | Clean (0 fake records), 110 grants, 95 policies, nonce index dropped | **PASS** |
| **Step 2b** | Downgrade to `0023_auditor_provenance` | `AUDITORS_VERIFIED_CLEAN`, 108 grants, 95 policies | Clean (0 fake records), 108 grants, 95 policies | **PASS** |
| **Step 2c** | Downgrade to `0022_statutory_auditors_replay` | `AUDITORS_VERIFIED_CLEAN` | Clean (0 fake records) | **PASS** |
| **Step 2d** | Re-upgrade to Head (`0025_manual_review_and_nonce`) | `AUDITORS_VERIFIED_CLEAN` | Clean (0 fake records) | **PASS** |
| **Step 3** | Downgrade to `0020_m4_m5_grants_and_policies` | `TABLE_NOT_PRESENT`, 105 grants, 95 policies | Table absent, 105 grants, 95 policies | **PASS** |
| **Step 4** | Downgrade to `0019_m3_gstr3b_filings` | `TABLE_NOT_PRESENT`, exact match to reference | Exact match on 79 grants, 87 policies, 41 tables | **PASS** |
| **Step 5** | Re-upgrade to Head (`0025_manual_review_and_nonce`) | `AUDITORS_VERIFIED_CLEAN`, 110 grants, 95 policies | Restored completely to production state | **PASS** |

**Baseline 0019 Cryptographic Anchor:**  
- SHA256: `bfe3e3a342b56732d1d0cf43f8edfc50391df1020a2c487f22fb5834e9503373` [PASS]

---

## 3. ROW LEVEL SECURITY (RLS) AUDIT SUMMARY

Executed via `scripts/audit_rls_security_comprehensive.py`:

- **Role Security:** `app_service_user` has `Superuser=False`, `BypassRLS=False`, `CanLogin=True`.
- **Forced Table RLS:** All 36 tables verified with `RLS=True` and `FORCE_RLS=True`.
- **Catalogue Write Denials:** Under `app_service_user`, `INSERT`, `UPDATE`, and `DELETE` attempts are denied on:
  * `government_schemes` (Write Denied)
  * `scheme_eligibility_rules` (Write Denied)
  * `statutory_auditors` (UPDATE/DELETE restricted)
  * `statutory_audit_signature_replays` (UPDATE/DELETE Denied)
- **Cross-Tenant Isolation:**
  * 11 / 11 core tenant tables verified for cross-tenant SELECT, UPDATE, and DELETE isolation.
  * Cross-tenant spoofed INSERTs rejected across all 11 domains with `SQLSTATE 42501` (`RLS_WITH_CHECK_VIOLATION`).
  * Under null tenant context (`current_setting('app.current_organization_id', true)` = NULL), all tenant tables return 0 rows.
