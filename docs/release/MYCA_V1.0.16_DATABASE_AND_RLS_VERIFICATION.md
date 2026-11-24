# MyCA v1.0.16 Database and RLS Verification

**Target Release:** `v1.0.16`  
**Evaluation Standard:** 100% Dynamic RLS Coverage, Rolling Rate Limiter Schema Architecture, and Reversible Migration Invariants  

---

## 1. Schema Changes & Migration 0030

### Additive Migration: `0030_auth_rate_limits_rolling.py`
Migration `0030_auth_rate_limits_rolling.py` upgrades the backend rate-limiting infrastructure from calendar-minute fixed windows to rolling 60-second sliding windows:

1. **Table Schema Evolution: `public.auth_rate_limits`:**
   ```sql
   ALTER TABLE public.auth_rate_limits
       DROP COLUMN IF EXISTS window_start,
       DROP COLUMN IF EXISTS request_count,
       ADD COLUMN timestamps TIMESTAMPTZ[] NOT NULL DEFAULT '{}'::timestamptz[],
       ADD COLUMN updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp();

   CREATE INDEX IF NOT EXISTS ix_auth_rate_limits_updated_at ON public.auth_rate_limits (updated_at);
   ```
2. **Reversible Downgrade:**
   Cleanly reverts back to `0029_auth_rate_limits` schema:
   ```sql
   DROP INDEX IF EXISTS public.ix_auth_rate_limits_updated_at;

   ALTER TABLE public.auth_rate_limits
       DROP COLUMN IF EXISTS timestamps,
       DROP COLUMN IF EXISTS updated_at,
       ADD COLUMN window_start TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
       ADD COLUMN request_count INTEGER NOT NULL DEFAULT 1;
   ```

---

## 2. Dynamic Tenant Table RLS Discovery & Verification

The dynamic RLS audit script (`backend/scripts/audit_rls_security_comprehensive.py`) scans PostgreSQL catalog metadata for all tenant tables in schema `public`:

### Discovery Results
Total Tenant Tables Discovered: **37 tables** (35 `org_id` scoped tables + 2 root identity tables).

| # | Table Name | RLS Enabled | FORCE RLS Enabled | Audit Verdict |
|---|---|---|---|---|
| 1 | `business_profiles` | TRUE | TRUE | **PASS** |
| 2 | `documents` | TRUE | TRUE | **PASS** |
| 3 | `financial_summaries` | TRUE | TRUE | **PASS** |
| 4 | `financial_transactions` | TRUE | TRUE | **PASS** |
| 5 | `global_users` | TRUE | TRUE | **PASS** |
| 6 | `goods_receipt_lot_ledger` | TRUE | TRUE | **PASS** |
| 7 | `gst_records` | TRUE | TRUE | **PASS** |
| 8 | `gst_registrations` | TRUE | TRUE | **PASS** |
| 9 | `gstr2b_imports` | TRUE | TRUE | **PASS** |
| 10 | `gstr2b_snapshots` | TRUE | TRUE | **PASS** |
| 11 | `gstr2b_source_documents` | TRUE | TRUE | **PASS** |
| 12 | `gstr2b_unbooked_candidates` | TRUE | TRUE | **PASS** |
| 13 | `gstr3b_filings` | TRUE | TRUE | **PASS** |
| 14 | `human_resolution_events` | TRUE | TRUE | **PASS** |
| 15 | `idp_authentication_nonces` | TRUE | TRUE | **PASS** |
| 16 | `ims_action_history` | TRUE | TRUE | **PASS** |
| 17 | `ims_document_actions` | TRUE | TRUE | **PASS** |
| 18 | `invoice_line_items` | TRUE | TRUE | **PASS** |
| 19 | `invoices` | TRUE | TRUE | **PASS** |
| 20 | `itc_assessments` | TRUE | TRUE | **PASS** |
| 21 | `itc_ledger_entries` | TRUE | TRUE | **PASS** |
| 22 | `itc_reavailment_events` | TRUE | TRUE | **PASS** |
| 23 | `itc_reversal_events` | TRUE | TRUE | **PASS** |
| 24 | `organization_memberships` | TRUE | TRUE | **PASS** |
| 25 | `organizations` | TRUE | TRUE | **PASS** |
| 26 | `purchase_documents` | TRUE | TRUE | **PASS** |
| 27 | `purchase_line_items` | TRUE | TRUE | **PASS** |
| 28 | `purchase_payment_allocations` | TRUE | TRUE | **PASS** |
| 29 | `purchase_payments` | TRUE | TRUE | **PASS** |
| 30 | `receipt_remediation_audit` | TRUE | TRUE | **PASS** |
| 31 | `reconciliation_matches` | TRUE | TRUE | **PASS** |
| 32 | `reconciliation_runs` | TRUE | TRUE | **PASS** |
| 33 | `reconciliation_variances` | TRUE | TRUE | **PASS** |
| 34 | `scheme_matches` | TRUE | TRUE | **PASS** |
| 35 | `summary_transactions` | TRUE | TRUE | **PASS** |
| 36 | `worker_service_authorizations` | TRUE | TRUE | **PASS** |
| 37 | `worker_tasks` | TRUE | TRUE | **PASS** |

**Summary: 37/37 tenant tables enforce RLS and FORCE RLS (100% coverage).**

---

## 3. Migration Rollback Invariant Verification

- **Verification Script:** `backend/scripts/verify_migration_rollback_state.py`
- **Disposable Marker Token:** `MYCA_VERIFIED_DISPOSABLE_TEST_DATABASE_TOKEN_V1_0_16`
- **Head Target:** `0030_auth_rate_limits_rolling` (120 table grants, 96 policies)
- **Downgrade Cycles Tested:**
  - Step 2a-PRE-30: Downgrade to `0029_auth_rate_limits`, verifying `timestamps` and `updated_at` drop and `window_start`/`request_count` restore.
  - Step 2a-PRE-29: Downgrade to `0028_idp_nonces_rls`, verifying `auth_rate_limits` table dropped and grants return to 116.
  - Step 2a-PRE: Downgrade to `0027_idp_auth_nonces`, verifying RLS removed on nonces.
  - Step 2-POPULATED: Populated record preservation across downgrade to `0025` and re-upgrade to Head.
  - Step 4: Full downgrade to `0019_m3_gstr3b_filings` matching independent reference baseline `baseline_0019_reference.json` (79 grants, 87 policies, 41 tables).
  - Step 5: Final re-upgrade to Head (`0030_auth_rate_limits_rolling`) restoring 120 grants and 96 policies.
