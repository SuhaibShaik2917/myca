# MyCA v1.0.6 — Database & RLS Verification Report

**Audit Date:** November 23, 2026  
**Auditor:** Principal Database Architect & Security Auditor  
**Release Version:** `v1.0.6`  
**Head Revision:** `0023_auditor_provenance`  
**Target Database:** PostgreSQL 16  

---

## 1. MIGRATION CHAIN & ROLLBACK VERIFICATION

### 1.1 Revision Lineage
The Alembic migration lineage is strictly forward, deterministic, and reversible:
- `0019_m3_gstr3b_filings` (Baseline: 79 grants, 87 policies, 41 tables)
- `0020_m4_m5_grants_and_policies` (Adds 8 policies for M4 Credit Builder & M5 Scheme Navigator)
- `0021_hsn_sac_lookup_and_rcm_permissions` (Grants on lookup catalogues)
- `0022_statutory_auditors_replay` (Tables `statutory_auditors` and `statutory_audit_signature_replays`)
- `0023_auditor_provenance` (Revision ID `0023_auditor_provenance`):
  - Cleanses demo identities from `statutory_auditors`.
  - Adds provenance tracking columns and check constraints.

### 1.2 Automated Migration Rollback Test Execution
`scripts/verify_migration_rollback_state.py` was executed against an independent clean baseline:
1. **Cryptographic Baseline Provenance:** SHA256 of `baseline_0019_reference.json` matches `bfe3e3a342b56732d1d0cf43f8edfc50391df1020a2c487f22fb5834e9503373` [PASS].
2. **Initial Baseline at 0019:** Matches independent reference 100% on all 79 grants, 87 policies, and 41 tables [PASS].
3. **Upgrade to Head (0023):** 108 grants, 95 policies [PASS].
4. **Downgrade to 0020:** 105 grants, 95 policies [PASS].
5. **Downgrade to 0019:** Exactly matches baseline reference (all 8 policies from 0020 dropped cleanly, exact match on all 79 grants, 87 policies, and forced RLS flags) [PASS].
6. **Re-Upgrade to Head (0023):** Completely restores production security state (108 grants, 95 policies) [PASS].

---

## 2. TABLE AND POLICY AUDIT

### 2.1 Table Categorization (43 Total PostgreSQL Tables)
- **36 Tenant Tables (Forced RLS):**
  Enforce multi-tenant isolation via `tenant_isolation_policy` and `FORCE ROW LEVEL SECURITY`.
- **4 Controlled / Reference Tables:**
  - `government_schemes`: Reference catalog (`SELECT` to `app_service_user`).
  - `scheme_eligibility_rules`: Reference catalog (`SELECT` to `app_service_user`).
  - `statutory_auditors`: Controlled auditor registry (`SELECT` to `app_service_user`, DDL/DML to `postgres`).
  - `statutory_audit_signature_replays`: Immutable audit trail (`SELECT, INSERT` to `app_service_user`).
- **3 Infrastructure Tables:**
  - `alembic_version`: Migration state tracker.
  - `storage_blobs`: Internal object storage reference.
  - `app_settings`: Global system settings.

### 2.2 RLS Policy Coverage
- Total active security policies at Head (0023): **95**.
- Total least-privilege role grants at Head (0023): **108**.

---

## 3. UNIFIED CI DATABASE CONTRACT & PRE-FLIGHT DIAGNOSTICS

### 3.1 Role Connection Matrix
Pre-flight diagnostics (`backend/scripts/diagnose_ci_database.py`) verified connectivity and role mappings across all execution contexts:

| Role Name | Environment Variable | Usage Scope | Superuser | BypassRLS | Status |
|---|---|---|---|---|---|
| `postgres` | `DB_ADMIN_URL` / `DATABASE_ADMIN_URL` | Migrations, DDL, Test Setup | Yes | Yes | **PASS** |
| `app_service_user` | `DATABASE_URL` / `DB_APP_URL` | FastAPI Runtime, API Requests | No | No | **PASS** |
| `worker_service_user` | `DATABASE_WORKER_URL` | Background Tasks, Queue Workers | No | No | **PASS** |
| `worker_service_user_1` | `DATABASE_WORKER_1_URL` | Worker Authorization Tests (Worker 1) | No | No | **PASS** |
| `worker_service_user_2` | `DATABASE_WORKER_2_URL` | Worker Authorization Tests (Worker 2) | No | No | **PASS** |

### 3.2 Reference Catalogue Population Guarantee
- `conftest.py` contains a session-scoped autouse fixture `ensure_reference_catalogs_seeded`:
  Automatically seeds `public.government_schemes` (6 schemes) and `public.scheme_eligibility_rules` (16 rules) before any tests run.
  Eliminates zero-row catalogue errors across all test runner permutations.
