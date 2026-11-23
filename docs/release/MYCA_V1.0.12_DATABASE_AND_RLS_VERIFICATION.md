# MyCA v1.0.12 — Database & RLS Security Verification

**Release Target:** `v1.0.12`  
**Database Engine:** PostgreSQL 16  
**Active Schema Head:** `0026_gateway_nonce_and_audit`  
**Total Tables in Public Schema:** 45 tables (36 tenant-scoped tables, 9 reference/metadata tables)  

---

## 1. PostgreSQL Table RLS & FORCE RLS Reconciliation

The independent audit of `v1.0.11` requested reconciliation of the 21-versus-36 FORCE RLS discrepancy.

### Audit Findings from PostgreSQL Catalog
- Query executed:
  ```sql
  SELECT relname, relrowsecurity, relforcerowsecurity
  FROM pg_class c
  JOIN pg_namespace n ON n.oid = c.relnamespace
  WHERE n.nspname = 'public' AND c.relkind = 'r'
  ORDER BY relname;
  ```
- **Total Tables in Public Schema:** 45
- **Tenant-Scoped Tables:** Exactly 36 tables
- **Tenant Tables with `relrowsecurity = True`:** 36 / 36 (100%)
- **Tenant Tables with `relforcerowsecurity = True`:** 36 / 36 (100%)

### Full 36 Tenant Tables Verification Matrix

| Table Name | `relrowsecurity` (RLS) | `relforcerowsecurity` (FORCE RLS) | Security Status |
|---|---|---|---|
| `business_profiles` | `True` | `True` | **ENFORCED** |
| `documents` | `True` | `True` | **ENFORCED** |
| `financial_summaries` | `True` | `True` | **ENFORCED** |
| `financial_transactions` | `True` | `True` | **ENFORCED** |
| `global_users` | `True` | `True` | **ENFORCED** |
| `goods_receipt_lot_ledger` | `True` | `True` | **ENFORCED** |
| `gst_records` | `True` | `True` | **ENFORCED** |
| `gst_registrations` | `True` | `True` | **ENFORCED** |
| `gstr2b_imports` | `True` | `True` | **ENFORCED** |
| `gstr2b_snapshots` | `True` | `True` | **ENFORCED** |
| `gstr2b_source_documents` | `True` | `True` | **ENFORCED** |
| `gstr2b_unbooked_candidates` | `True` | `True` | **ENFORCED** |
| `gstr3b_filings` | `True` | `True` | **ENFORCED** |
| `human_resolution_events` | `True` | `True` | **ENFORCED** |
| `ims_action_history` | `True` | `True` | **ENFORCED** |
| `ims_document_actions` | `True` | `True` | **ENFORCED** |
| `invoice_line_items` | `True` | `True` | **ENFORCED** |
| `invoices` | `True` | `True` | **ENFORCED** |
| `itc_assessments` | `True` | `True` | **ENFORCED** |
| `itc_ledger_entries` | `True` | `True` | **ENFORCED** |
| `itc_reavailment_events` | `True` | `True` | **ENFORCED** |
| `itc_reversal_events` | `True` | `True` | **ENFORCED** |
| `organization_memberships` | `True` | `True` | **ENFORCED** |
| `organizations` | `True` | `True` | **ENFORCED** |
| `purchase_documents` | `True` | `True` | **ENFORCED** |
| `purchase_line_items` | `True` | `True` | **ENFORCED** |
| `purchase_payment_allocations` | `True` | `True` | **ENFORCED** |
| `purchase_payments` | `True` | `True` | **ENFORCED** |
| `receipt_remediation_audit` | `True` | `True` | **ENFORCED** |
| `reconciliation_matches` | `True` | `True` | **ENFORCED** |
| `reconciliation_runs` | `True` | `True` | **ENFORCED** |
| `reconciliation_variances` | `True` | `True` | **ENFORCED** |
| `scheme_matches` | `True` | `True` | **ENFORCED** |
| `summary_transactions` | `True` | `True` | **ENFORCED** |
| `worker_service_authorizations` | `True` | `True` | **ENFORCED** |
| `worker_tasks` | `True` | `True` | **ENFORCED** |

### Non-Tenant Reference & System Tables (9 Tables)
The remaining 9 tables are global catalogs or metadata, which do not hold tenant data and are protected via role-based permissions:
1. `alembic_version`: Alembic migration tracker.
2. `government_schemes`: Public statutory catalog (`app_service_user` has `SELECT` only).
3. `invoice_transition_rules`: Public catalog (`app_service_user` has `SELECT` only).
4. `itc_rule_catalogue`: Public statutory catalog (`app_service_user` has `SELECT` only).
5. `scheme_eligibility_rules`: Public statutory catalog (`app_service_user` has `SELECT` only).
6. `statutory_audit_signature_replays`: Security replay tracker (immutable append-only).
7. `statutory_auditor_verification_events`: Security event log.
8. `statutory_auditors`: Global auditor catalog (`SELECT` only for `app_service_user`).
9. `statutory_gateway_nonce_registry`: Gateway nonce tracker (`SELECT, INSERT` for `app_service_user`).

---

## 2. Migration Rollback Safety & Invariant Verification

- Verification Script: `backend/scripts/verify_migration_rollback_state.py`
- Safety Guard: `assert_disposable_database_environment(DB_ADMIN_URL)`
  - Refuses execution if `ENVIRONMENT in ("production", "staging")`.
  - Refuses execution unless `ALLOW_DISPOSABLE_DATABASE_ROLLBACK=true`.
  - Refuses execution if database host is remote or database name contains `prod`, `production`, or `live`.
- Invariant Match: 100% exact match against independent reference `baseline_0019_reference.json` (SHA256: `bfe3e3a342b56732d1d0cf43f8edfc50391df1020a2c487f22fb5834e9503373`):
  - 79 grants, 87 policies, 41 tables match exactly at baseline 0019.
  - Head state (0026): 112 grants, 95 policies, 43 tables.
  - Consumed gateway nonces permanently preserved across downgrade and re-upgrade.
