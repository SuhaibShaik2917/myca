# MyCA v1.0.14 Database and RLS Verification

**Target Release:** `v1.0.14`  
**Evaluation Standard:** 100% Dynamic RLS Coverage, Least-Privilege Role Grants, and Reversible Migration Invariants  

---

## 1. Dynamic Tenant Table RLS Discovery

In `v1.0.14`, `backend/scripts/audit_rls_security_comprehensive.py` was enhanced to dynamically discover all tables requiring multi-tenant row-level security:

```sql
SELECT c.relname
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public'
  AND c.relkind = 'r'
  AND (
      EXISTS (
          SELECT 1 FROM information_schema.columns
          WHERE table_schema = 'public'
            AND table_name = c.relname
            AND column_name = 'org_id'
      )
      OR c.relname IN ('organizations', 'global_users')
  )
ORDER BY c.relname;
```

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

**Summary:** Exactly 37 of 37 tables enforce both `rowsecurity = TRUE` and `relforcerowsecurity = TRUE` (100% compliance).

---

## 2. Cross-Tenant Isolation Across All 12 Functional Tenant Domains

Cross-tenant isolation was validated by creating Org A and Org B records across 12 distinct functional domains and verifying that `app_service_user` bound to Org B cannot read, modify, delete, or insert records with Org A's tenant ID:

1. `business_profiles`
2. `invoices`
3. `documents`
4. `gst_registrations`
5. `purchase_documents`
6. `itc_ledger_entries`
7. `gst_records`
8. `financial_transactions`
9. `gstr3b_filings`
10. `financial_summaries`
11. `scheme_matches`
12. `idp_authentication_nonces`

**Result:**
- Read Isolation: 12 / 12 domains return 0 rows for foreign tenant data.
- Update Isolation: 12 / 12 domains affect 0 rows on foreign tenant updates.
- Delete Isolation: 12 / 12 domains affect 0 rows or trigger permission denial on foreign tenant deletes.
- Insert Spoofing: 12 / 12 domains raise SQLSTATE `42501` (`RLS_WITH_CHECK_VIOLATION`) when Org B attempts to insert with `org_id = Org A`.
- Null Context Isolation: 12 / 12 domains return 0 rows when `app.current_org` is null or empty.

---

## 3. Migration Rollback & Database Disposability Verification

`backend/scripts/verify_migration_rollback_state.py` verified the following invariants:

1. **Database Disposability Marker Enforcement:**
   - Requires marker table `public.disposable_database_marker` containing token `MYCA_VERIFIED_DISPOSABLE_TEST_DATABASE_TOKEN_V1_0_14`.
   - Confirms Alembic and psycopg target the exact same database (`SELECT current_database();`).
2. **Reversible Migration Chain:**
   - Head revision: `0028_idp_nonces_rls` (116 grants, 96 policies).
   - Downgrade to `0027_idp_auth_nonces` cleanly drops RLS policy and restores RLS disabled state.
   - Populated downgrade to `0025_manual_review_and_nonce` safely preserves all auditor evidence, audit events, gateway nonces, and IdP nonces.
   - Re-upgrade to Head restores replay protection; repeated nonces are rejected by PostgreSQL unique/primary constraints.
   - Downgrade to `0019_m3_gstr3b_filings` matches independent reference snapshot (`baseline_0019_reference.json`) with 100% precision:
     - Exactly 79 grants matching reference.
     - Exactly 87 policies matching reference (including command, roles, qual, with_check).
     - Exactly 41 tables matching reference.
   - Final re-upgrade to `0028_idp_nonces_rls` restores full production schema (116 grants, 96 policies).
