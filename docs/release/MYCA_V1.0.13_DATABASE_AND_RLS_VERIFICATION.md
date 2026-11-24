# MyCA v1.0.13 — Database & RLS Verification Report

**Release Target:** `v1.0.13`  
**Parent Commit:** `a64eb6ba82b79c7b39de7eda7594bbbe25c62d2a`  
**Verification Scope:** Migration 0027 schema and grants, RLS policy audit, populated rollback invariants, and nonce replay defense.

---

## 1. Migration 0027: IdP Authentication Nonces

- **Migration File:** `backend/alembic/versions/0027_idp_auth_nonces.py`
- **Revision ID:** `0027_idp_auth_nonces`
- **Down Revision:** `0026_gateway_nonce_and_audit`
- **Table Definition:** `public.idp_authentication_nonces`
  ```sql
  CREATE TABLE IF NOT EXISTS public.idp_authentication_nonces (
      nonce text PRIMARY KEY,
      org_id uuid NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
      issued_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      expires_at timestamptz NOT NULL,
      consumed_at timestamptz,
      status text NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'CONSUMED'))
  );
  CREATE INDEX IF NOT EXISTS idx_idp_auth_nonces_status_expiry
      ON public.idp_authentication_nonces(status, expires_at);
  ```
- **Role Permissions (Least Privilege):**
  - `GRANT SELECT, INSERT, UPDATE ON public.idp_authentication_nonces TO app_service_user;`
  - Zero `DELETE` or `TRUNCATE` grants.
  - Zero schema or DDL modifications permitted for `app_service_user`.
- **Downgrade Policy:**
  - Nonces are intentionally preserved across downgrades:
    ```sql
    REVOKE ALL ON public.idp_authentication_nonces FROM app_service_user;
    -- Nonces preserved to prevent replay attacks upon rollback
    ```

---

## 2. PostgreSQL RLS & FORCE RLS Catalogue Audit

Live database audit executed via `backend/scripts/audit_rls_security_comprehensive.py`:

- **Active Session User:** `app_service_user`
  - `Superuser: False`
  - `BypassRLS: False`
  - `CanLogin: True`

### Tenant Tables (Exactly 36 of 36 Enforce RLS + FORCE RLS):

| Table Name | RLS Enabled (`relrowsecurity`) | FORCE RLS (`relforcerowsecurity`) |
|---|:---:|:---:|
| `business_profiles` | True | True |
| `documents` | True | True |
| `financial_summaries` | True | True |
| `financial_transactions` | True | True |
| `global_users` | True | True |
| `goods_receipt_lot_ledger` | True | True |
| `gst_records` | True | True |
| `gst_registrations` | True | True |
| `gstr2b_imports` | True | True |
| `gstr2b_snapshots` | True | True |
| `gstr2b_source_documents` | True | True |
| `gstr2b_unbooked_candidates` | True | True |
| `gstr3b_filings` | True | True |
| `human_resolution_events` | True | True |
| `ims_action_history` | True | True |
| `ims_document_actions` | True | True |
| `invoice_line_items` | True | True |
| `invoices` | True | True |
| `itc_assessments` | True | True |
| `itc_ledger_entries` | True | True |
| `itc_reavailment_events` | True | True |
| `itc_reversal_events` | True | True |
| `organization_memberships` | True | True |
| `organizations` | True | True |
| `purchase_documents` | True | True |
| `purchase_line_items` | True | True |
| `purchase_payment_allocations` | True | True |
| `purchase_payments` | True | True |
| `receipt_remediation_audit` | True | True |
| `reconciliation_matches` | True | True |
| `reconciliation_runs` | True | True |
| `reconciliation_variances` | True | True |
| `scheme_matches` | True | True |
| `summary_transactions` | True | True |
| `worker_service_authorizations` | True | True |
| `worker_tasks` | True | True |

**Result:** Exactly 36 of 36 tenant tables (100%) enforce `RLS=True` and `FORCE_RLS=True`.

---

## 3. Migration Rollback Invariants & Populated Nonce Survival

Executed via `backend/scripts/verify_migration_rollback_state.py` against disposable database `myca_test`:

1. **Baseline 0019 Exact Snapshot Verification:**
   - Evaluated against independent reference snapshot `baseline_0019_reference.json` (SHA256: `bfe3e3a342b56732d1d0cf43f8edfc50391df1020a2c487f22fb5834e9503373`).
   - Downgrade to `0019` resulted in:
     - Exactly **79 grants** (100% match on role, table, and privilege type).
     - Exactly **87 policies** (100% match on command, roles, qual, with_check).
     - Exactly **41 tables** (100% match).
2. **Historical Upgrade to 0022:**
   - Clean verification that historical migration 0022 restores original demonstration seeds before 0023 removes them.
3. **Populated Record Downgrade & Nonce Replay Defense:**
   - Seeded active auditor record, audit event, gateway nonce, and IdP authentication nonce.
   - Executed downgrade to `0025`.
   - Verified evidence, audit records, gateway nonces, and IdP nonces survived downgrade intact.
   - Executed re-upgrade to Head (`0027_idp_auth_nonces`).
   - Replay attack presenting consumed gateway nonce rejected by unique constraint.
   - Replay attack presenting consumed IdP nonce rejected by primary key constraint.
4. **Final Head State (0027):**
   - Head State: **115 grants**, **95 policies**, **46 tables**.
   - Production security state 100% verified.
