# MyCA v1.0.15 Database and RLS Verification

**Target Release:** `v1.0.15`  
**Evaluation Standard:** 100% Dynamic RLS Coverage, Distributed Rate Limiting Table Architecture, and Reversible Migration Invariants  

---

## 1. Schema Changes & Migration 0029

### Additive Migration: `0029_auth_rate_limits.py`
Migration `0029_auth_rate_limits.py` provisions the backend infrastructure for distributed rate limiting and index optimization on nonce tracking:

1. **Table Creation: `public.auth_rate_limits`:**
   ```sql
   CREATE TABLE public.auth_rate_limits (
       rate_key VARCHAR(128) PRIMARY KEY,
       window_start TIMESTAMPTZ NOT NULL,
       request_count INTEGER NOT NULL DEFAULT 1
   );
   ```
2. **Compound Indexes on `public.idp_authentication_nonces`:**
   - `ix_idp_nonces_ip_created`: Index on `(client_ip, created_at)` for rapid IP rate and anomaly auditing.
   - `ix_idp_nonces_org_created`: Index on `(org_id, created_at)` for org-scoped nonce retrieval and lifecycle queries.
3. **Least-Privilege Grants:**
   - `app_service_user`: Granted `SELECT, INSERT, UPDATE, DELETE` on `public.auth_rate_limits`.
   - `worker_service_user`: Read-only `SELECT` grants where applicable.
4. **Reversible Downgrade:**
   - Cleanly drops `public.auth_rate_limits` and the compound indexes on downgrade to `0028_idp_nonces_rls`.

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
| 27 | `reconciliation_batches` | TRUE | TRUE | **PASS** |
| 28 | `reconciliation_discrepancies` | TRUE | TRUE | **PASS** |
| 29 | `reconciliation_matches` | TRUE | TRUE | **PASS** |
| 30 | `statutory_audit_events` | TRUE | TRUE | **PASS** |
| 31 | `tax_periods` | TRUE | TRUE | **PASS** |
| 32 | `vendor_compliance_cache` | TRUE | TRUE | **PASS** |
| 33 | `vendor_credit_profiles` | TRUE | TRUE | **PASS** |
| 34 | `vendor_gstins` | TRUE | TRUE | **PASS** |
| 35 | `vendor_hsn_profiles` | TRUE | TRUE | **PASS** |
| 36 | `vendor_scores` | TRUE | TRUE | **PASS** |
| 37 | `vendors` | TRUE | TRUE | **PASS** |

**Summary: 37/37 tenant tables enforce RLS and FORCE RLS (100% coverage).**

---

## 3. Migration Rollback Invariant Verification

- **Verification Script:** `backend/scripts/verify_migration_rollback_state.py`
- **Disposable Marker Token:** `MYCA_VERIFIED_DISPOSABLE_TEST_DATABASE_TOKEN_V1_0_15`
- **Head Target:** `0029_auth_rate_limits` (120 table grants, 96 policies)
- **Downgrade Steps:**
  - Step 2a-PRE-29: Downgrade to `0028_idp_nonces_rls`, verifying `auth_rate_limits` dropped and grants return to 116.
  - Step 2a-PRE: Downgrade to `0027_idp_auth_nonces`, verifying RLS removed on nonces.
  - Step 2-POPULATED: Populated record preservation across downgrade to `0025` and re-upgrade to Head.
  - Step 4: Full downgrade to `0019_m3_gstr3b_filings` matching independent reference baseline `baseline_0019_reference.json` (79 grants, 87 policies, 41 tables).
