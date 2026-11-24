# MyCA v1.0.17 Database and RLS Verification

**Target Release:** `v1.0.17`  
**Evaluation Standard:** 100% Dynamic RLS Coverage, Atomic CTE Rate Limiting with Row Locking, Operational Cleanup, and Reversible Migration Invariants  

---

## 1. Migration Head & Rate Limiter Schema

### Active Head: `0030_auth_rate_limits_rolling.py`
The rate-limiting database infrastructure on `public.auth_rate_limits` supports atomic rolling 60-second sliding windows:

1. **Table Schema: `public.auth_rate_limits`:**
   - `rate_key` (VARCHAR(255), PRIMARY KEY): Keyed by `ip:<ip>` or `org:<org_id>`.
   - `timestamps` (TIMESTAMPTZ[], NOT NULL, DEFAULT '{}'): Dynamic rolling array of request timestamps.
   - `updated_at` (TIMESTAMPTZ, NOT NULL, DEFAULT clock_timestamp()): Indexed for non-blocking retention pruning.
   - Index: `ix_auth_rate_limits_updated_at` on `(updated_at)`.

2. **Atomic CTE Query with Row Lock (`locked_row FOR UPDATE`):**
   ```sql
   WITH locked_row AS (
       SELECT rate_key, timestamps
       FROM public.auth_rate_limits
       WHERE rate_key = :key
       FOR UPDATE
   ),
   current_state AS (
       SELECT 
           locked_row.rate_key,
           coalesce(array_agg(t_item ORDER BY t_item) FILTER (WHERE t_item IS NOT NULL), '{}'::timestamptz[]) AS filtered_ts,
           count(t_item) AS current_count
       FROM locked_row
       LEFT JOIN LATERAL unnest(locked_row.timestamps) AS t_item ON t_item > :now - INTERVAL '60 seconds'
       GROUP BY locked_row.rate_key
   )
   UPDATE public.auth_rate_limits
   SET 
       timestamps = CASE 
           WHEN current_state.current_count < :max_limit 
           THEN current_state.filtered_ts || :now 
           ELSE current_state.filtered_ts 
       END,
       updated_at = :now
   FROM current_state
   WHERE public.auth_rate_limits.rate_key = current_state.rate_key
   RETURNING 
       (current_state.current_count < :max_limit) AS is_accepted,
       CASE 
           WHEN current_state.current_count < :max_limit 
           THEN current_state.current_count + 1 
           ELSE current_state.current_count 
       END AS active_count;
   ```
   - **Row-Level Lock:** Ensures atomic execution across concurrent worker processes and threads.
   - **Explicit Boolean Acceptance:** `(current_state.current_count < :max_limit)` returned directly from SQL.

3. **High-Performance Non-Blocking Storage Maintenance:**
   ```sql
   DELETE FROM public.auth_rate_limits
   WHERE updated_at < NOW() - make_interval(hours => :hours);
   ```
   - Uses `ix_auth_rate_limits_updated_at` b-tree index.
   - Deletion of records older than 1 hour has zero impact on active 60-second sliding windows.

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

**Coverage:** Exactly 37 / 37 tenant tables enforce `rowsecurity = true` AND `forcerowsecurity = true` (100% compliance).

---

## 3. Migration Rollback & Baseline Verification

- **Script:** `backend/scripts/verify_migration_rollback_state.py`
- **Disposable Marker Token:** `MYCA_VERIFIED_DISPOSABLE_TEST_DATABASE_TOKEN_V1_0_17`
- **Baseline Snapshot:** `baseline_0019_reference.json` (SHA256: `bfe3e3a342b56732d1d0cf43f8edfc50391df1020a2c487f22fb5834e9503373`)

### Rollback Cycle Invariants
1. Downgrade from `0030` to `0019` matches the clean reference database:
   - 79 Grants (100% exact match).
   - 87 Security Policies (100% exact match).
   - 41 Tables and RLS / FORCE RLS flags (100% exact match).
2. Populated Downgrade to `0025` preserves authentic review evidence, consumed gateway nonces, and IdP nonces.
3. Re-upgrade to `0030` fully restores 120 grants and 96 security policies with zero fake auditor records.
