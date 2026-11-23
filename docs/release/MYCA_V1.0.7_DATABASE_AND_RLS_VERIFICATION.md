# MyCA v1.0.7 — Database & RLS Verification Report

**Date:** November 23, 2026  
**Target Release:** `v1.0.7`  
**Database Version:** PostgreSQL 16  
**Alembic Head Revision:** `0023_auditor_provenance`  
**Status:** **100% VERIFIED — ALL INVARIANTS SATISFIED**

---

## 1. MIGRATION ROLLBACK & STATE INVARIANT AUDIT

Automated verification script `scripts/verify_migration_rollback_state.py` verified the following state transitions against cryptographic SHA256 baseline reference (`bfe3e3a342b56732d1d0cf43f8edfc50391df1020a2c487f22fb5834e9503373`):

1. **Step 1 (Downgrade to 0019):**
   - Matched baseline snapshot: 79 grants, 87 policies, 41 tables.
   - Asserted `statutory_auditors` table does not exist.
2. **Step 2 (Upgrade to Head 0023):**
   - Verified 108 grants and 95 policies.
   - Verified all 21 tenant tables enforce `relforcerowsecurity=True`.
   - Verified 0 fabricated demo auditors in `statutory_auditors`.
3. **Step 2b (Intermediate Downgrade to 0022 and Re-upgrade to 0023):**
   - Verified 0 demo auditors at revision 0022.
   - Verified 0 demo auditors upon return to 0023.
4. **Step 3 (Downgrade to 0020):**
   - Verified all 21 tenant tables revert to `relforcerowsecurity=False`.
   - Verified all 8 0020 policies active.
   - Verified `statutory_auditors` does not exist.
5. **Step 4 (Downgrade to 0019):**
   - Verified all 8 0020 policies dropped.
   - Exact match on all 79 grants, 87 policies, and table RLS flags against independent reference snapshot.
6. **Step 5 (Final Re-upgrade to Head 0023):**
   - Restored 108 grants, 95 policies, and FORCE RLS on all 21 tenant tables.
   - Verified 0 fabricated demo auditors in `statutory_auditors`.

---

## 2. ROW-LEVEL SECURITY & CROSS-TENANT ISOLATION

Comprehensive security audit `scripts/audit_rls_security_comprehensive.py` confirmed:

- **36 / 36 Tables with FORCE RLS:** All multi-tenant tables enforce forced row-level security.
- **Catalogue Immutability:** `app_service_user` has strictly `SELECT` permissions on reference catalogues (`government_schemes`, `scheme_eligibility_rules`, `statutory_auditors`). All `INSERT`, `UPDATE`, and `DELETE` attempts by `app_service_user` fail with permission denied (SQLSTATE 42501).
- **Persistent Replay Table Immutability:** `app_service_user` has `SELECT` and `INSERT` permissions on `statutory_audit_signature_replays`. All `UPDATE` and `DELETE` operations fail with permission denied.
- **Cross-Tenant Isolation:**
  - 11 core tenant tables tested across Org Alpha and Org Beta.
  - Read isolation: Org Beta cannot see Org Alpha data.
  - Update isolation: Org Beta UPDATE affects 0 rows.
  - Delete isolation: Org Beta DELETE affects 0 rows.
  - Insert spoofing: Org Beta attempting to insert with Org Alpha's `tenant_id` is rejected by PostgreSQL `WITH CHECK` constraint (SQLSTATE 42501).
  - Null context isolation: Queries under null session context return 0 rows.
