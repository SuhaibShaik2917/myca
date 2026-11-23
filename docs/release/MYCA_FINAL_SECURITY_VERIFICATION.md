# MyCA — FINAL SECURITY & MULTI-TENANT ISOLATION AUDIT

**Audit Date:** November 23, 2026  
**Auditor:** Principal Application Security Engineer  
**Audit Branch:** `remediation/audit-findings-phase3` (Merging to `master`)  
**Release Checkpoint:** `v1.0.5`  
**Scope:** PostgreSQL 16 Row Level Security (RLS), Least-Privilege Role Grants, Token Spoofing Defense, 11-Domain Cross-Tenant Write Isolation, Migration Rollback Baseline Exact Match with Cryptographic Authentication, Fail-Closed Server-Side HMAC Auditor Authentication, Controlled Auditor Database, and Persistent Database Replay Protection.

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

### 1.2 Least-Privilege Grants on Reference Catalogues & Controlled Tables
Inspection of `information_schema.role_table_grants` for `app_service_user`:
```sql
SELECT table_name, privilege_type 
FROM information_schema.role_table_grants 
WHERE grantee = 'app_service_user' 
  AND table_name IN ('government_schemes', 'scheme_eligibility_rules', 'statutory_auditors', 'statutory_audit_signature_replays');
```

**Results:**
- `government_schemes`: **`SELECT` ONLY** (0 `INSERT`, 0 `UPDATE`, 0 `DELETE`)
- `scheme_eligibility_rules`: **`SELECT` ONLY** (0 `INSERT`, 0 `UPDATE`, 0 `DELETE`)
- `statutory_auditors`: **`SELECT` ONLY** (0 `INSERT`, 0 `UPDATE`, 0 `DELETE`)
- `statutory_audit_signature_replays`: **`SELECT, INSERT` ONLY** (0 `UPDATE`, 0 `DELETE` — immutable audit trail)

**Regression Test Verification:**
Tests in `tests/test_statutory_section_17_5_boundaries_and_reference_grants.py` and `scripts/audit_rls_security_comprehensive.py` execute live write attempts under `app_service_user`:
- `INSERT`, `UPDATE`, `DELETE` on `government_schemes`, `scheme_eligibility_rules`, and `statutory_auditors` raise `InsufficientPrivilege` / `ProgrammingError` (`permission denied for table ...`) [PASS].
- `UPDATE` and `DELETE` on `statutory_audit_signature_replays` raise `InsufficientPrivilege` / `ProgrammingError` (`permission denied for table statutory_audit_signature_replays`) [PASS]. Administrative onboarding & revocation of auditors require administrative connection credentials (`DB_ADMIN_URL`).

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
  - `invoices`, `documents`, `gst_registrations`, `purchase_documents`, `itc_ledger_entries`, `gst_records`, `financial_transactions`, `gstr3b_filings`, `financial_summaries`, `business_profiles`, `scheme_matches`.
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
- **Independent Provenance & Cryptographic Code Anchor Authentication:** Generated from an isolated, virgin PostgreSQL 16 database (`myca_independent_reference`) migrated strictly forward from base to `0019_m3_gstr3b_filings`. Script anchors the trusted expected hash as an immutable hardcoded constant (`TRUSTED_BASELINE_0019_SCHEMA_SHA256 = "bfe3e3a342b56732d1d0cf43f8edfc50391df1020a2c487f22fb5834e9503373"`). Script independently computes canonical SHA256 checksum across `full_grants`, `full_policies`, and `rls_flags` and asserts that BOTH the declared metadata hash and the dynamically computed payload hash match the immutable code anchor before asserting rollback invariants [PASS].
- **Step 1 (Establish Baseline at 0019 & Verify Against Independent Reference):** Downgraded to `0019_m3_gstr3b_filings`: compared live database catalog state against independent reference snapshot `baseline_0019_reference.json`: **79 full role_table_grants** for `app_service_user` (`table_schema`, `table_name`, `privilege_type`, `is_grantable`), **87 complete policies** in `pg_policies` (`schemaname`, `tablename`, `policyname`, `permissive`, `roles`, `cmd`, `qual`, `with_check`), and **41 tables** (100% exact match).
- **Step 2 (Upgrade to Head 0022):** Upgraded to `0022_statutory_auditors_replay`: **108 grants**, **95 policies**, and confirmed all 36 tenant tables have both `relrowsecurity = True` and `relforcerowsecurity = True`.
- **Step 3 (Downgrade to 0020):** Downgraded to `0020_m4_m5_grants_and_policies`: confirmed 21 tenant tables successfully reverted to `relforcerowsecurity = False` while retaining all 8 new policies.
- **Step 4 (Downgrade to 0019 & Baseline Comparison):** Downgraded to `0019_m3_gstr3b_filings`:
  - Confirmed all 8 policies from 0020 were cleanly dropped (0 leftover).
  - Compared exact role table grant definitions against baseline: **0 missing grants, 0 extra grants (100% exact match)**.
  - Compared exact complete policy definitions against baseline: **0 missing policies, 0 extra policies (100% exact match)**.
  - Compared table RLS and FORCE RLS flags: **100% exact match**.
- **Step 5 (Re-Upgrade to Head 0022):** Re-upgraded to `0022_statutory_auditors_replay`, fully restoring head state with 108 grants, 95 policies, and 36 forced RLS tenant tables.

---

## 6. CONTROLLED STATUTORY AUDITOR DATABASE & PERSISTENT REPLAY PROTECTION (v1.0.5)

Implemented in `backend/app/modules/reconciliation/itc_engine.py` and migration `0022_statutory_auditors_replay.py`:
- **Fail-Closed Key Resolver:**
  - Removed all hardcoded fallback secrets.
  - `get_statutory_audit_signing_key()` strictly inspects `os.environ.get("STATUTORY_AUDIT_SIGNING_KEY")`.
  - Rejects insecure/test secret defaults when running in production (`ENVIRONMENT=production`).
  - If the environment variable is missing, empty, or strictly shorter than 32 characters, it raises `KeyResolverError` immediately, failing closed and routing to `REVIEW_REQUIRED` (₹0.00 reversal).
- **Controlled Database-Backed Auditor Registry (`public.statutory_auditors`):**
  - Stored in persistent PostgreSQL table with strict schema constraints:
    - ICAI membership format: strictly 6 digits (`^\d{6}$`)
    - Firm registration format: 6 digits + 1 letter (`^\d{6}[A-Z]$`)
    - Certificate of Practice: `cop_status == "ACTIVE"`
    - Active validity window: `cop_valid_until >= date.today()`
    - Administrative approval: `approved_by` must be non-empty
    - Revocation status: `is_revoked == False`
  - Validated via `validate_auditor_credentials()` querying the database.
  - Administrative onboarding (`onboard_verified_statutory_auditor`) and revocation (`revoke_statutory_auditor`) require administrative connection (`DB_ADMIN_URL`).
- **Timestamp Freshness & Clock Skew Defense:**
  - Validated via `validate_certification_timestamp()`:
    - Strict ISO 8601 UTC format validation (`YYYY-MM-DDTHH:MM:SSZ` or with offset).
    - Maximum certification age: 24 hours (86,400 seconds).
    - Maximum clock skew into the future: 5 minutes (300 seconds).
- **Persistent Database-Backed Replay Defense (`public.statutory_audit_signature_replays`):**
  - Implemented via `check_and_record_signature_replay()`:
    - Unique constraint on `token_hash = sha256(token)`.
    - Atomic insert with `ON CONFLICT DO NOTHING`.
    - Replay attempt across documents (`doc_id` or `doc_number` mismatch) or duplicate consumption fails closed immediately.
    - Evaluated at invoice document level, ensuring all lines on a multi-line invoice receive consistent uniform-rate gating.
- **Cryptographic Signature Engine & Constant-Time Verification:**
  - Server-side HMAC-SHA256 signature generation and verification over canonical invoice and certification state:
    ```text
    canonical_msg = f"{doc_id}:{doc_number}:{total_invoice_value}:{total_tax}:{header_rate}:{verifier_id}:{verifier_role}:{evidence_ref}:{timestamp}"
    ```
  - Employs `hmac.compare_digest` to prevent side-channel timing attacks.
  - Arbitrary client strings, unregistered IDs, tampered totals, revoked auditors, or expired timestamps fail safe to CA review (`REVIEW_REQUIRED`, ₹0.00 reversal).

