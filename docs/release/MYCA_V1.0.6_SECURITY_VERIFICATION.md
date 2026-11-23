# MyCA v1.0.6 — Security & Multi-Tenant Isolation Verification

**Audit Date:** November 23, 2026  
**Auditor:** Principal Application Security Engineer  
**Release Target:** `v1.0.6`  
**Base Commit (v1.0.5):** `482650b38a7ed05822c420032ee94d8e5414715c`  
**Scope:** PostgreSQL 16 Kernel Row Level Security (RLS), Least-Privilege Role Privileges, Verification Provenance Validation, Fail-Closed HMAC Token Verification, Atomic Replay Defense, and 11-Domain Cross-Tenant Write Isolation.

---

## 1. POSTGRESQL ROLE & PRIVILEGE AUDIT

### 1.1 Application Role Security (`app_service_user`)
- The runtime application connects via `app_service_user`. Verified using PostgreSQL system catalogs:
  ```sql
  SELECT rolname, rolsuper, rolbypassrls, rolcanlogin 
  FROM pg_roles 
  WHERE rolname = 'app_service_user';
  ```
- **Results:**
  - `rolsuper`: **`False`** (Role is NOT a superuser)
  - `rolbypassrls`: **`False`** (Role CANNOT bypass Row Level Security)
  - `rolcanlogin`: **`True`** (Valid login role)

### 1.2 Reference Catalogues & Controlled Tables Least-Privilege Grants
- Table permissions verified via `information_schema.role_table_grants` for `app_service_user`:
  - `public.government_schemes`: `SELECT` ONLY.
  - `public.scheme_eligibility_rules`: `SELECT` ONLY.
  - `public.statutory_auditors`: `SELECT` ONLY. Write operations (`INSERT`, `UPDATE`, `DELETE`) are strictly forbidden for runtime users.
  - `public.statutory_audit_signature_replays`: `SELECT, INSERT` ONLY. Updates and deletes are revoked, guaranteeing an immutable audit trail.
- **Empirical Assertion:**
  `scripts/audit_rls_security_comprehensive.py` confirms that direct write attempts by `app_service_user` against reference tables raise:
  `SQLSTATE 42501: permission denied for table government_schemes / statutory_auditors` [PASS].

---

## 2. STATUTORY AUDITOR VERIFICATION PROVENANCE & FAIL-CLOSED ENFORCEMENT

### 2.1 Absence of Seeded Demonstration Identities
- Forward migration `0023_auditor_provenance` purged demonstration identities (`CA-ICAI-098765`, `CA-ICAI-054321`, `CA-REVOKED-011223`) from `public.statutory_auditors`.
- Production registry begins with **0** rows.
- In-memory registry `_TEST_STATUTORY_AUDITOR_REGISTRY` was deleted from application source code.
- Test identities exist exclusively in ephemeral test fixtures (`isolated_verified_auditor` in `conftest.py`) that create and teardown test auditors per execution.

### 2.2 Independent Verification Provenance Contract
- Added columns to `public.statutory_auditors`:
  - `verification_source`: Validated against check constraint `chk_statutory_auditors_verification_source` (`MANUAL_REVIEW`, `ICAI_PORTAL_MANUAL_SEARCH`, `ICAI_DIGITAL_VERIFICATION_GATEWAY`, `MOCK_TEST_FIXTURE`).
  - `verification_status`: Validated against check constraint `chk_statutory_auditors_verification_status` (`PENDING_INDEPENDENT_VERIFICATION`, `INDEPENDENTLY_VERIFIED`, `VERIFICATION_FAILED`, `SUSPENDED`, `REVOKED`).
  - `is_icai_verified`: Must be `True` for valid certification.
  - `is_cop_verified`: Must be `True` for valid certification.
- In `validate_auditor_credentials()`:
  - If `is_icai_verified` is False or `verification_status != 'INDEPENDENTLY_VERIFIED'`, validation immediately fails closed with:
    `UNVERIFIED_AUDITOR_PROVENANCE: Administrator approval alone cannot be misrepresented as independent ICAI verification; statutory certification requires independent ICAI membership & COP verification.`
  - If auditor is missing: fails closed with `UNREGISTERED_AUDITOR`.
  - If auditor is revoked: fails closed with `REVOKED_AUDITOR`.
  - If COP is expired: fails closed with `EXPIRED_CREDENTIALS`.
  - If database is unavailable: fails closed with `UNREGISTERED_AUDITOR` / `DATABASE_ERROR`.

---

## 3. PERSISTENT DATABASE-BACKED REPLAY PROTECTION

### 3.1 Elimination of In-Memory Replay Cache
- `_OFFLINE_RECORDED_AUDIT_SIGNATURES` in-memory dictionary was removed completely.
- `check_and_record_signature_replay()` requires an active PostgreSQL connection. If connection is unavailable, it fails closed immediately (`DATABASE_UNAVAILABLE`).
- Database exceptions or constraint aborts trigger rollback and return `DATABASE_TRANSACTION_FAILURE`.

### 3.2 Atomic Token Replay Prevention
- SHA-256 token hashing: `token_hash = sha256(token)`.
- Database constraint: `token_hash text NOT NULL UNIQUE`.
- Insertion uses atomic PostgreSQL `ON CONFLICT (token_hash) DO NOTHING`.
- Concurrency test `test_signature_replay_concurrent_collision_rejected` executes simultaneous inserts from distinct sessions:
  - Session 1 succeeds.
  - Session 2 detects `rowcount == 0` and returns `CONCURRENT_REPLAY_DETECTED`.

### 3.3 Replay Lifecycle & Permanent Invalidation
- Cross-document reuse attempt: Returns `False` with `cross-document replay rejected`.
- Duplicate document reuse attempt: Returns `False` with `already been consumed`.
- Expired tokens: Once inserted into `statutory_audit_signature_replays`, tokens are permanently recorded. Token expiration does not allow reuse.

---

## 4. ROW LEVEL SECURITY (RLS) & FORCED RLS ON 36 TENANT TABLES

### 4.1 Complete Coverage
All 36 tenant tables maintain `RLS = True` and `FORCE_RLS = True`:
1. `business_profiles`
2. `documents`
3. `financial_summaries`
4. `financial_transactions`
5. `global_users`
6. `goods_receipt_lot_ledger`
7. `gst_records`
8. `gst_registrations`
9. `gstr2b_imports`
10. `gstr2b_snapshots`
11. `gstr2b_source_documents`
12. `gstr2b_unbooked_candidates`
13. `gstr3b_filings`
14. `human_resolution_events`
15. `ims_action_history`
16. `ims_document_actions`
17. `invoice_line_items`
18. `invoices`
19. `itc_assessments`
20. `itc_ledger_entries`
21. `itc_reavailment_events`
22. `itc_reversal_events`
23. `organization_memberships`
24. `organizations`
25. `purchase_documents`
26. `purchase_line_items`
27. `purchase_payment_allocations`
28. `purchase_payments`
29. `receipt_remediation_audit`
30. `reconciliation_matches`
31. `reconciliation_runs`
32. `reconciliation_variances`
33. `scheme_matches`
34. `summary_transactions`
35. `worker_service_authorizations`
36. `worker_tasks`

### 4.2 Multi-Tenant Write Isolation (11 Domains)
- Cross-tenant spoofed inserts were tested against all 11 tenant domains (`business_profiles`, `invoices`, `documents`, `gst_registrations`, `purchase_documents`, `itc_ledger_entries`, `gst_records`, `financial_transactions`, `gstr3b_filings`, `financial_summaries`, `scheme_matches`).
- In all 11 cases, spoofed inserts under a foreign tenant context were aborted by PostgreSQL kernel with:
  `SQLSTATE 42501: new row violates row-level security policy for table ...` [PASS].
- Under null tenant context (`current_setting('app.current_org_id', true) IS NULL`), 0 rows are returned across all tables.

---

## 5. API LAYER TAMPER GATES

- All FastAPI endpoints enforce mandatory bearer token authentication.
- Unauthenticated requests return `401 Unauthorized`.
- Header tampering / org spoofing attempts return `403 Forbidden` (`Active Tamper Protection Enforced`).
