# MyCA — FINAL SECURITY & MULTI-TENANT ISOLATION AUDIT

**Audit Date:** November 23, 2026  
**Auditor:** Principal Application Security Engineer  
**Audit Branch:** `remediation/audit-findings-phase3` (Merging to `master`)  
**Scope:** PostgreSQL 16 Row Level Security (RLS), Least-Privilege Role Grants, Token Spoofing Defense, 11-Domain Cross-Tenant Write Isolation, Migration Rollback Baseline Exact Match.

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
- Under `Tenant Alpha` context, records were inserted across all 11 core tenant domain tables:
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
- While authenticated as `Tenant Beta`, UPDATE attempts were executed on `Tenant Alpha`'s records across all 11 core tables:
  - `invoices`, `documents`, `gst_registrations`, `purchase_documents`, `gst_records`, `financial_transactions`, `gstr3b_filings`, `financial_summaries`, `business_profiles`, `scheme_matches`: **0 rows updated** (RLS isolation enforced) [PASS]
  - `itc_ledger_entries`: blocked by **least-privilege grant denial** (immutable statutory ledger) [PASS]
- While authenticated as `Tenant Beta`, DELETE attempts were executed on `Tenant Alpha`'s records across all tables:
  - `invoices`, `documents`, `gst_registrations`, `purchase_documents`, `gst_records`, `financial_transactions`, `gstr3b_filings`, `financial_summaries`, `business_profiles`, `scheme_matches`: **0 rows deleted** (RLS isolation enforced) [PASS]
  - `itc_ledger_entries`: blocked by **least-privilege grant denial** (immutable statutory ledger) [PASS]
- Cross-tenant INSERT verification executed via two-phase testing across all 11 core tenant domain tables:
  - **Phase 1 (Positive Alpha Baseline):** Proved that valid INSERT statements for all 11 domain tables succeed under valid Tenant Alpha context (`app.current_org = org_a`, `app.current_user = user_a`) and verified that the row exists in `public.<table_name>` [PASS].
  - **Phase 2 (Spoofed Beta Rejection):** Re-executed the identical valid statement structure under Tenant Beta (`app.current_org = org_b`, `app.current_user = user_b`) with spoofed `org_id = org_a`.
  - **Specific SQLSTATE & Policy Assertion:** All 11 domain tables (`business_profiles`, `invoices`, `documents`, `gst_registrations`, `purchase_documents`, `itc_ledger_entries`, `gst_records`, `financial_transactions`, `gstr3b_filings`, `financial_summaries`, `scheme_matches`) specifically raised SQLSTATE `42501` (`insufficient_privilege`) with message `new row violates row-level security policy for table ...` and were classified as `RLS_WITH_CHECK_VIOLATION` [PASS].

### 3.3 Null Context Leakage Protection
- When tenant session variables (`app.current_org` and `app.current_user`) were cleared:
  - All 11 tenant tables returned **0 rows**, preventing unauthenticated bulk exfiltration.

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

## 5. MIGRATION ROLLBACK SAFETY & BASELINE VERIFICATION

Migration downgrade safety and baseline restoration verified via `backend/scripts/verify_migration_rollback_state.py` against independent reference catalog snapshot (`baseline_0019_reference.json`):
- **Step 1 (Establish Baseline at 0019 & Verify Against Independent Reference):** Downgraded to `0019_m3_gstr3b_filings`: compared live database catalog state against independent reference snapshot `baseline_0019_reference.json`: **79 role_table_grants** for `app_service_user`, **88 policies** in `pg_policies`, and **41 tables** (100% exact match).
- **Step 2 (Upgrade to Head 0021):** Upgraded to `0021_enforce_tenant_force_rls`: **105 grants**, **96 policies**, and confirmed all 36 tenant tables have both `relrowsecurity = True` and `relforcerowsecurity = True`.
- **Step 3 (Downgrade to 0020):** Downgraded to `0020_m4_m5_grants_and_policies`: confirmed 21 tenant tables successfully reverted to `relforcerowsecurity = False` while retaining all 8 new policies.
- **Step 4 (Downgrade to 0019 & Baseline Comparison):** Downgraded to `0019_m3_gstr3b_filings`:
  - Confirmed all 8 policies from 0020 were cleanly dropped (0 leftover).
  - Compared exact role table grants against baseline: **0 missing grants, 0 extra grants (100% exact match)**.
  - Compared exact policies against baseline: **0 missing policies, 0 extra policies (100% exact match)**.
  - Compared table RLS and FORCE RLS flags: **100% exact match**.
- **Step 5 (Re-Upgrade to Head 0021):** Re-upgraded to `0021_enforce_tenant_force_rls`, fully restoring head state with 105 grants and 96 policies.
