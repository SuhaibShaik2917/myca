# MyCA — FINAL DEPLOYMENT & RUNTIME INFRASTRUCTURE VERIFICATION

**Audit Date:** November 23, 2026  
**Auditor:** Principal DevOps & Infrastructure Verification Engineer  
**Audit Branch:** `remediation/audit-findings-phase3` (Merging to `master`)  
**Scope:** Container manifests, local runtime processes, database migrations, health endpoints, and external integration boundaries.

---

## 1. RUNTIME SERVICES STATUS

Both backend and frontend services were verified in live running state:

| Service | Target Port | Host Binding | Process / Command | Live Health / Status |
| :--- | :--- | :--- | :--- | :--- |
| **Backend API** | `8000` | `0.0.0.0` | `uvicorn app.main:app --port 8000` (Windows / Python 3.14) | **200 OK** (`/health`: `{"status":"ok"}`) |
| **Frontend SPA** | `5173` | `0.0.0.0` | `vite --port 5173` (Node.js v20) | **200 OK** (HTML SPA served) |
| **PostgreSQL** | `5432` | `localhost` | PostgreSQL 16.4 Engine | **Connected** (DB: `myca`, Role: `app_service_user`) |

### Verification Record Summary
- **Backend Test Suite:** 259 / 259 tests passing (100% pass rate in 31.66s).
- **Frontend Production Build:** `npm run build` compiles 1,583 modules in 3.48s with 0 errors.
- **Cross-Tenant Security:** 100% isolation verified across all 11 core tenant tables and 36 forced RLS tables.

---

## 2. DATABASE MIGRATION INTEGRITY & ROLLBACK PROOF

Alembic migration history was verified against PostgreSQL 16 `alembic_version`:
```sql
SELECT version_num FROM alembic_version;
```
- **Active Current Revision:** `0021_enforce_tenant_force_rls`
- **Total Applied Migrations:** 21 migrations
  - `0001` to `0005`: Core architecture, tenant schema, business financial tables
  - `0006` to `0018`: Statutory ledger, ITC reconciliation, Rule 37/37A tables, IMS actions
  - `0019_m3_gstr3b_filings`: Milestone 3 GSTR-3B filings table, unique constraints, and maker-checker schema
  - `0020_m4_m5_grants_and_policies`: Milestone 4 & 5 table grants and tenant RLS policies for `app_service_user` with granular, non-destructive downgrade
  - `0021_enforce_tenant_force_rls`: Least privilege write revocation on reference catalogues and `FORCE ROW LEVEL SECURITY` across 100% of tenant-scoped tables

### Migration Baseline & Rollback Cycle Tested
Script `backend/scripts/verify_migration_rollback_state.py` verified the complete baseline rollback cycle:
1. Captured exact database baseline at `0019_m3_gstr3b_filings` (79 grants for `app_service_user`, 88 policies in `pg_policies`, 41 tables).
2. Upgraded to `0021_enforce_tenant_force_rls` head (105 grants, 96 policies, all 36 tenant tables having FORCE RLS = True).
3. Downgraded to `0020_m4_m5_grants_and_policies` (safely reverting 21 tables to `FORCE RLS = False`).
4. Downgraded to `0019_m3_gstr3b_filings` and compared current database state against captured baseline:
   - Grants: **100% exact match** (0 missing, 0 extra).
   - Policies: **100% exact match** (0 missing, 0 extra).
   - Table RLS & FORCE RLS flags: **100% exact match**.
5. Re-upgraded to `0021_enforce_tenant_force_rls` head, fully restoring the production security state.

---

## 3. EXTERNAL PRODUCTION INTEGRATION DISCLOSURE

> [!IMPORTANT]
> **Statutory & Environmental Boundaries:**  
> Live production deployment to external government servers and banking networks is **explicitly NOT claimed as completed**:
> - **Live GSTN Production Direct Filing:** Requires formal GSP agreement and taxpayer DSC credentials. Software is verified against GSP Sandbox simulation (`simulate_gsp_gstn_filing`).
> - **Live RBI Account Aggregator:** Requires live FIU certification with Sahamati/RBI. Software is verified with high-fidelity bank statement CSV parsing.
> - **Live Gemini Production Enterprise Quota:** In local and test runs, deterministic statutory fallback operates. Production deployment requires binding enterprise Cloud project keys.

---

## 4. DOCKER CONTAINER ASSETS & CONFIGURATION

The repository provides production container manifests:
- `backend/Dockerfile`: Multi-stage Python 3.12-slim build with non-root user (`USER appuser`)
- `frontend/Dockerfile`: Multi-stage Node.js build with Nginx alpine runtime
- `docker-compose.yml`: Coordinates PostgreSQL 16, Backend API, Frontend SPA, and Redis worker queue
