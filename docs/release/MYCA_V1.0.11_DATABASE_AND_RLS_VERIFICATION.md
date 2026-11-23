# MyCA v1.0.11 — Database & RLS Security Verification Report

**Verification Date:** November 24, 2026  
**Audited Target:** `v1.0.11`  
**Repository:** `SuhaibShaik2917/myca`  
**Database Engine:** PostgreSQL 16  

---

## 1. Migration History & Blob Hash Anchoring

All Alembic migrations have been validated for exact blob integrity against released references:

| Migration Revision | Revision File | Released In | Target Git Blob SHA | Verified Git Blob SHA | Match Status |
|---|---|---|---|---|---|
| `0001` | `0001_extensions_roles_enums_audit.py` | v1.0.0 | `cde85dbfd19db5c2363d6f3804c3f42e622d3ea8` | `cde85dbfd19db5c2363d6f3804c3f42e622d3ea8` | **EXACT MATCH** |
| `0002` | `0002_core_multitenant_tables.py` | v1.0.0 | `6d33ace93aaa137688c04d4297741de03eaebd0c` | `6d33ace93aaa137688c04d4297741de03eaebd0c` | **EXACT MATCH** |
| `0019` | `0019_m3_gstr3b_filings.py` | v1.0.0 | `2b5048cfcec58f7ca5239fec17618e260645bec9` | `2b5048cfcec58f7ca5239fec17618e260645bec9` | **EXACT MATCH** |
| `0020` | `0020_m4_m5_grants_and_policies.py` | v1.0.0 | `ffed1662be8dcb583659b0800fc1142798b317b5` | `ffed1662be8dcb583659b0800fc1142798b317b5` | **EXACT MATCH** |
| `0021` | `0021_enforce_tenant_force_rls.py` | v1.0.0 | `41a3822f27e82fef0aa6f3b250b9f8f4a08c58e1` | `41a3822f27e82fef0aa6f3b250b9f8f4a08c58e1` | **EXACT MATCH** |
| `0022` | `0022_statutory_auditors_replay.py` | v1.0.5 | `b97ca120d0ccdf3ec2197cab8441dd3741286ceb` | `b97ca120d0ccdf3ec2197cab8441dd3741286ceb` | **EXACT MATCH** |
| `0023` | `0023_statutory_auditor_provenance.py` | v1.0.6 | `04648826c281348fcc0688832f3a0559721ac9ee` | `04648826c281348fcc0688832f3a0559721ac9ee` | **EXACT MATCH** |
| `0024` | `0024_auditor_authentic_evidence.py` | v1.0.8 | `864d283d7c6fa976184df3c35e5c85864ee73b51` | `864d283d7c6fa976184df3c35e5c85864ee73b51` | **EXACT MATCH** |
| `0025` | `0025_manual_review_and_nonce.py` | v1.0.9 | `986f9026ff49f1f8baccada97e158c8e2a336988` | `986f9026ff49f1f8baccada97e158c8e2a336988` | **EXACT MATCH** |
| `0026` | `0026_gateway_nonce_and_audit.py` | v1.0.11 | Forward-only additive | Forward-only additive | **NEW REVISION** |

---

## 2. Rollback Verification & Baseline Invariants

`backend/scripts/verify_migration_rollback_state.py` verified the full migration rollback lifecycle against the independent cryptographically authenticated reference snapshot (`baseline_0019_reference.json`, SHA-256 `bfe3e3a342b56732d1d0cf43f8edfc50391df1020a2c487f22fb5834e9503373`):

1. **Step 1 (Establish Baseline 0019):** Matches independent reference snapshot 100% on full definitions (79 grants, 87 policies, 41 tables).
2. **Step 1b (Historical 0022 Upgrade):** Contains historical demonstration seed records as originally published in v1.0.5.
3. **Step 2 (Head 0026 Upgrade):** Cleanses demonstration seeds; provisions `statutory_gateway_nonce_registry` (112 grants, 95 policies, 42 tables).
4. **Step 2-POPULATED (Populated Downgrade & Nonce Replay Defense):**
   - Populated 1 `MANUAL_DOCUMENT_REVIEWED` auditor record, 1 audit event, and 1 consumed gateway nonce.
   - Downgraded to 0025: preserved auditor record, audit event, and consumed gateway nonce.
   - Re-upgraded to 0026: restored `MANUAL_DOCUMENT_REVIEWED` status; replaying identical gateway nonce rejected by PRIMARY KEY violation.
5. **Step 2a (Downgrade to 0025):** 110 grants, 95 policies.
6. **Step 2a-2 (Downgrade to 0024):** 110 grants, 95 policies; partial unique index dropped.
7. **Step 2b (Downgrade to 0023):** 108 grants, 95 policies.
8. **Step 2c (Downgrade to 0022):** Clean auditor registry (zero fake records).
9. **Step 2d (Re-upgrade to 0026):** Restores head security state (112 grants, 95 policies).
10. **Step 3 (Downgrade to 0020):** 105 grants, 95 policies; FORCE RLS reverted to False on tenant tables while keeping RLS active.
11. **Step 4 (Downgrade to 0019 Baseline):** All 8 0020 policies cleanly dropped; exact match on all 79 grants, 87 policies, and RLS flags against independent reference.
12. **Step 5 (Final Re-upgrade to 0026):** Restores head state completely (112 grants, 95 policies).

---

## 3. Comprehensive RLS & Access Control Audit

Executed `backend/scripts/audit_rls_security_comprehensive.py`:
- **Role Privileges:** `app_service_user` has `Superuser=False`, `BypassRLS=False`, `CanLogin=True`.
- **Table RLS Enforcement:** All 36 application tables enforce `RLS=True`, and all 21 tenant tables enforce `FORCE_RLS=True`.
- **Catalogue Immutability:** `INSERT`, `UPDATE`, and `DELETE` on reference tables (`government_schemes`, `scheme_eligibility_rules`, `statutory_auditors`, `statutory_audit_signature_replays`) are strictly denied to `app_service_user`.
- **Cross-Tenant Isolation:** Verified across all 11 tenant domains for Read, Update, Delete, and Insert operations. Spoofed context inserts rejected with SQLSTATE 42501 (`RLS_WITH_CHECK_VIOLATION`). Under NULL tenant context, all tenant tables return 0 rows.
- **FastAPI Token Enforcement:** Unauthenticated endpoints return 401 Unauthorized. Spoofed identity headers without valid tokens return 403 Forbidden.
