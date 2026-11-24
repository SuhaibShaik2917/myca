# MyCA v1.0.14 QA Evidence

**Target Release:** `v1.0.14`  
**Execution Context:** Local Environment & GitHub Actions CI Matrix  

---

## 1. Automated Test Execution Summary

### Backend Pytest Suite
- **Command:** `python -m pytest tests/ -v --tb=short`
- **Result:** **387 passed, 1 warning in 63.40s**
- **Test Modules Executed:**
  - `tests/test_ai_assistant.py` (3 tests)
  - `tests/test_auth_token.py` (63 tests)
  - `tests/test_circuit_breaker.py` (8 tests)
  - `tests/test_invoice_brain.py` (4 tests)
  - `tests/test_migration_security_baseline.py` (31 tests)
  - `tests/test_milestone1_security_closure.py` (13 tests)
  - `tests/test_milestone2_1_statutory_corrections.py` (1 test)
  - `tests/test_milestone2_7_1_statutory_integrity.py` (22 tests)
  - `tests/test_milestone2_7_2_financial_reconciliation_closure.py` (36 tests)
  - `tests/test_milestone2_7_inward_accounting_workflow.py` (24 tests)
  - `tests/test_milestone2_reconciliation.py` (46 tests)
  - `tests/test_milestone3_gstr3b_offset_and_filing.py` (13 tests)
  - `tests/test_milestone4_credit_builder.py` (11 tests)
  - `tests/test_milestone5_scheme_navigator.py` (20 tests)
  - `tests/test_rate_limiter.py` (7 tests)
  - `tests/test_rollback_script_safety.py` (31 tests)
  - `tests/test_statutory_auditor_provenance_and_replay_hardening.py` (42 tests)
  - `tests/test_statutory_section_17_5_boundaries_and_reference_grants.py` (12 tests)

### Rollback Script Safety Unit Tests
- **Command:** `python -m pytest tests/test_rollback_script_safety.py -v`
- **Result:** **31 passed, 1 warning in 0.11s**
- **Key Safety Invariants Verified:**
  - Abort on unset or empty `DB_ADMIN_URL`
  - Abort on non-test/ci environments (`production`, `staging`, `development`, `unknown`, empty)
  - Abort when `ALLOW_DISPOSABLE_DATABASE_ROLLBACK` is not `true`
  - Abort against protected databases (`myca`, `postgres`, `template1`, `development`, `myca_dev`, `app`, `main`)
  - Abort against production or remote cloud database hosts (`prod-db.internal`, `db.rds.amazonaws.com`, etc.)
  - Abort if marker table `public.disposable_database_marker` is missing
  - Abort if marker token does not match `MYCA_VERIFIED_DISPOSABLE_TEST_DATABASE_TOKEN_V1_0_14`
  - Abort if Alembic target database does not match psycopg target database
  - Accept valid local/disposable target databases with marker present

---

## 2. Migration Rollback Invariant Verification

- **Command:** `python scripts/verify_migration_rollback_state.py`
- **Execution Target:** `myca_test` with disposable marker token
- **Result:** **ALL MIGRATION ROLLBACK & INDEPENDENT BASELINE REFERENCE INVARIANTS 100% VERIFIED!**
- **Cycle Highlights:**
  - Step 1: Baseline 0019 matches independent reference snapshot (`baseline_0019_reference.json`) with zero deviation (79 grants, 87 policies, 41 tables).
  - Step 1b: Fresh historical upgrade to 0022 contains legacy demonstration seeds as expected.
  - Step 2: Upgrade to Head (0028) verifies 116 grants, 96 policies, clean auditor registry.
  - Step 2-POPULATED: Seeding of evidence, audit events, gateway nonces, and IdP nonces; downgrade to 0025 safely preserves all records; re-upgrade to Head validates replay defense.
  - Step 2a-PRE through Step 2c: Downgrade through revisions 0027, 0026, 0025, 0024, 0023, 0022 verifies clean auditor state without ghost records.
  - Step 3 & Step 4: Downgrade to 0020 and 0019 matches baseline reference exactly.
  - Step 5: Final re-upgrade to 0028 restores full production schema.

---

## 3. Comprehensive RLS & Security Audit Evidence

- **Command:** `python scripts/audit_rls_security_comprehensive.py`
- **Result:** **ALL COMPREHENSIVE RLS & SECURITY AUDITS PASSED FLAWLESSLY!**
- **Key Results:**
  - Application role `app_service_user`: `Superuser=False`, `BypassRLS=False`, `CanLogin=True`.
  - Dynamic discovery: Exactly 37 of 37 tenant tables enforce `RLS=True` and `FORCE_RLS=True`.
  - Write denials on catalogues (`government_schemes`, `scheme_eligibility_rules`, `statutory_auditors`, `statutory_audit_signature_replays`): 100% blocked.
  - Cross-tenant read, update, delete, and insert spoofing: 100% blocked across all 12 functional domains.
  - Null context queries: Return 0 rows across all tenant tables.
  - API token gates & tamper protection: Unauthenticated requests return HTTP 401; spoofed header attempts return HTTP 403.

---

## 4. Frontend Production Build Evidence

- **Command:** `cd frontend && npm run build`
- **Toolchain:** TypeScript 5 (`tsc -b`) and Vite 6 (`vite build`)
- **Result:** **Built in 24.73s**
- **Asset Output:**
  - `dist/index.html`: 0.48 kB (gzip: 0.33 kB)
  - `dist/assets/index-CXHqnTlf.css`: 29.39 kB (gzip: 5.62 kB)
  - `dist/assets/index-DBkuh5Fw.js`: 420.23 kB (gzip: 107.43 kB)
  - Zero TypeScript diagnostics errors or bundle warnings.
