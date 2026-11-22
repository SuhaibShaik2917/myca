# MyCA — FINAL SECURITY & MULTI-TENANT ISOLATION AUDIT

**Audit Date:** November 23, 2026  
**Auditor:** Principal Application Security Engineer  
**Scope:** PostgreSQL 16 Row Level Security (RLS), Least-Privilege Role Grants, Token Spoofing Defense, Cross-Tenant Isolation.

---

## 1. POSTGRESQL ROLE & PRIVILEGE AUDIT

### 1.1 Database Role Privileges (`app_service_user`)
The runtime application connects via `app_service_user`. An independent query against `pg_roles` was executed:
```sql
SELECT rolname, rolsuper, rolbypassrls, rolcanlogin 
FROM pg_roles 
WHERE rolname = 'app_service_user';
```

**Results:**
- `rolname`: `app_service_user`
- `rolsuper`: **`False`** (Role is NOT a superuser)
- `rolbypassrls`: **`False`** (Role CANNOT bypass Row Level Security)
- `rolcanlogin`: **`True`** (Allowed to authenticate via database credentials)

**Explicit Session Assertion:**
Tests and audit scripts explicitly execute and assert:
```python
current_db_user = db.execute(text("SELECT current_user;")).scalar()
assert current_db_user == "app_service_user"
```

> **Security Finding:** `app_service_user` has zero administrative or RLS bypass privileges, ensuring that all queries executed by FastAPI are strictly constrained by PostgreSQL's kernel-level RLS policy engine.

### 1.2 Least-Privilege Grants on Reference Catalogues
Inspection of `information_schema.role_table_grants` for `app_service_user` on public reference catalogues:
```sql
SELECT table_name, privilege_type 
FROM information_schema.role_table_grants 
WHERE grantee = 'app_service_user' 
  AND table_name IN ('government_schemes', 'scheme_eligibility_rules');
```

**Results:**
- `government_schemes`: **`SELECT` ONLY** (0 `INSERT`, 0 `UPDATE`, 0 `DELETE`)
- `scheme_eligibility_rules`: **`SELECT` ONLY** (0 `INSERT`, 0 `UPDATE`, 0 `DELETE`)

**Regression Test Verification:**
Test `test_app_service_user_cannot_write_to_reference_catalogues` in `tests/test_statutory_section_17_5_boundaries_and_reference_grants.py` and comprehensive audit script `scripts/audit_rls_security_comprehensive.py` execute live `INSERT`, `UPDATE`, and `DELETE` attempts against **BOTH** tables under the application connection and confirm that PostgreSQL raises `InsufficientPrivilege` / `ProgrammingError` (`permission denied for table ...`), permanently blocking unauthorized writes across all three mutation verbs.

---

## 2. ROW LEVEL SECURITY (RLS) & FORCED RLS CONFIGURATION

### 2.1 Complete Table Enumeration (All 36 Tenant Tables)
Querying `pg_class` for all tenant-scoped tables confirms that **100% of the 36 tenant-scoped tables have both RLS and FORCE RLS enabled**:

| Table Name | Table Owner | `relrowsecurity` (RLS Enabled) | `relforcerowsecurity` (Forced RLS) | Security Status |
| :--- | :--- | :--- | :--- | :--- |
| `business_profiles` | `postgres` | **True** | **True** | **PASS** |
| `documents` | `postgres` | **True** | **True** | **PASS** |
| `financial_summaries` | `postgres` | **True** | **True** | **PASS** |
| `financial_transactions` | `postgres` | **True** | **True** | **PASS** |
| `global_users` | `postgres` | **True** | **True** | **PASS** |
| `goods_receipt_lot_ledger` | `postgres` | **True** | **True** | **PASS** |
| `gst_records` | `postgres` | **True** | **True** | **PASS** |
| `gst_registrations` | `postgres` | **True** | **True** | **PASS** |
| `gstr2b_imports` | `postgres` | **True** | **True** | **PASS** |
| `gstr2b_snapshots` | `postgres` | **True** | **True** | **PASS** |
| `gstr2b_source_documents` | `postgres` | **True** | **True** | **PASS** |
| `gstr2b_unbooked_candidates` | `postgres` | **True** | **True** | **PASS** |
| `gstr3b_filings` | `postgres` | **True** | **True** | **PASS** |
| `human_resolution_events` | `postgres` | **True** | **True** | **PASS** |
| `ims_action_history` | `postgres` | **True** | **True** | **PASS** |
| `ims_document_actions` | `postgres` | **True** | **True** | **PASS** |
| `invoice_line_items` | `postgres` | **True** | **True** | **PASS** |
| `invoices` | `postgres` | **True** | **True** | **PASS** |
| `itc_assessments` | `postgres` | **True** | **True** | **PASS** |
| `itc_ledger_entries` | `postgres` | **True** | **True** | **PASS** |
| `itc_reavailment_events` | `postgres` | **True** | **True** | **PASS** |
| `itc_reversal_events` | `postgres` | **True** | **True** | **PASS** |
| `organization_memberships` | `postgres` | **True** | **True** | **PASS** |
| `organizations` | `postgres` | **True** | **True** | **PASS** |
| `purchase_documents` | `postgres` | **True** | **True** | **PASS** |
| `purchase_line_items` | `postgres` | **True** | **True** | **PASS** |
| `purchase_payment_allocations` | `postgres` | **True** | **True** | **PASS** |
| `purchase_payments` | `postgres` | **True** | **True** | **PASS** |
| `receipt_remediation_audit` | `postgres` | **True** | **True** | **PASS** |
| `reconciliation_matches` | `postgres` | **True** | **True** | **PASS** |
| `reconciliation_runs` | `postgres` | **True** | **True** | **PASS** |
| `reconciliation_variances` | `postgres` | **True** | **True** | **PASS** |
| `scheme_matches` | `postgres` | **True** | **True** | **PASS** |
| `summary_transactions` | `postgres` | **True** | **True** | **PASS** |
| `worker_service_authorizations`| `postgres` | **True** | **True** | **PASS** |
| `worker_tasks` | `postgres` | **True** | **True** | **PASS** |

**Public Reference Catalogues (RLS Intentionally Disabled):**
`government_schemes`, `scheme_eligibility_rules`, `itc_rule_catalogue`, `invoice_transition_rules`, and `alembic_version` have RLS disabled intentionally as they contain shared statutory definitions. They are protected by least-privilege grants (`SELECT` only).

### 2.2 Table Ownership & Forced RLS Mechanics
- All database tables are owned by administrative role `postgres`.
- The application executes queries as `app_service_user` (a non-owner, non-superuser role).
- Applying `FORCE ROW LEVEL SECURITY` across all tenant tables ensures that even if table ownership is reassigned, or if maintenance procedures execute under different roles, RLS policies apply unconditionally.

---

## 3. CROSS-TENANT ISOLATION EVIDENCE

Executed via `backend/scripts/audit_rls_security_comprehensive.py`:

### 3.1 Read Isolation Test
- Two distinct organizations (`Tenant Alpha Corp` and `Tenant Beta Ltd`) were provisioned with respective users and GSTINs.
- Under `Tenant Alpha` context, records were inserted across 11 core tenant tables:
  - `invoices`
  - `documents`
  - `gst_registrations`
  - `purchase_documents`
  - `itc_ledger_entries`
  - `gst_records`
  - `financial_transactions`
  - `gstr3b_filings`
  - `financial_summaries`
  - `business_profiles`
  - `scheme_matches`
- Context was switched to `Tenant Beta`:
  - `SELECT id FROM public.<table_name> WHERE id = <Alpha ID>` &rarr; **0 rows returned across all 11 tables** [PASS]

### 3.2 Write / Tamper Protection Test
- While authenticated as `Tenant Beta`, attempts to update `Tenant Alpha`'s records in `invoices`, `gstr3b_filings`, and `financial_summaries`:
  - `UPDATE public.<table_name> ... WHERE id = <Alpha ID>` &rarr; **0 rows updated** [PASS]
- An attempt was made to insert a record into `financial_summaries` tagged with `Tenant Alpha`'s `org_id` while operating under `Tenant Beta`:
  - Transaction rejected by PostgreSQL RLS with `WITH CHECK` violation (`ProgrammingError`) [PASS]

### 3.3 Null Context Leakage Protection
- When tenant session variables (`app.current_org` and `app.current_user`) were cleared:
  - All tenant tables returned **0 rows**, preventing unauthenticated bulk exfiltration.

---

## 4. API LAYER SECURITY VERIFICATION

All protected API endpoints require a valid JWT Bearer token:
- `GET /api/v1/gstr3b/status` &rarr; **HTTP 401 Unauthorized** without token
- `GET /api/v1/credit-builder/summary` &rarr; **HTTP 401 Unauthorized** without token
- `GET /api/v1/credit-builder/readiness-score` &rarr; **HTTP 401 Unauthorized** without token
- `GET /api/v1/credit-builder/loan-dossier` &rarr; **HTTP 401 Unauthorized** without token
- `GET /api/v1/scheme-navigator/profile` &rarr; **HTTP 401 Unauthorized** without token
- `GET /api/v1/scheme-navigator/schemes` &rarr; **HTTP 401 Unauthorized** without token
- `GET /api/v1/ai/quick-insights` &rarr; **HTTP 401 Unauthorized** without token

**Header Tampering Defense:**
When a caller supplies a valid JWT token for Tenant A but attempts to spoof `X-Organization-Id: Tenant B`, the backend detects the identity mismatch and halts the request with **`HTTP 403 Forbidden`**.

---

## 5. MIGRATION ROLLBACK SAFETY VERIFICATION

Migration downgrade safety verified via `backend/scripts/verify_migration_rollback_state.py`:
- `0021` downgrade sets `NO FORCE ROW LEVEL SECURITY` on all 21 tables (restoring exact `0020` state).
- `0020` downgrade selectively revokes the 8 grants and drops the 8 policies added in `0020` (avoiding broad `REVOKE ALL`).
- Full round-trip downgrade to `0019` and re-upgrade to `0021` executes cleanly and restores all 36 tables to `FORCE RLS = True`.
