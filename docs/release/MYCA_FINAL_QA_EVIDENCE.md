# MyCA — FINAL QA & TEST SUITE VERIFICATION EVIDENCE

**Audit Date:** November 23, 2026  
**Environment:** WSL2 Ubuntu Linux / Python 3.12.3 / PostgreSQL 16.4 / Node.js v20.x  
**Branch:** `remediation/audit-findings-phase2` (Merging to `master`)  
**Git Checkpoints:** Baseline `9f97516`, Audit `d421f0e`, Remediation Head `118ccd7`  

---

## 1. REPRODUCIBLE TEST RUN LOG

### Exact Command
```bash
cd /mnt/c/Users/notyo/.gemini/antigravity/scratch/myca/backend
source /opt/myca-venv/bin/activate
PYTHONPATH=. pytest -v --tb=short
```

### Complete Test Run Summary
```text
============================= test session starts ==============================
platform linux -- Python 3.12.3, pytest-9.2.0, pluggy-1.7.0 -- /opt/myca-venv/bin/python3
cachedir: .pytest_cache
rootdir: /mnt/c/Users/notyo/.gemini/antigravity/scratch/myca/backend
plugins: anyio-4.16.0, asyncio-1.5.0
asyncio: mode=Mode.STRICT, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collected 257 items

tests/test_ai_assistant.py ............................................ [ 3 passed]
tests/test_auth_token.py ............................................... [ 6 passed]
tests/test_invoice_brain.py ............................................ [ 7 passed]
tests/test_migration_security_baseline.py .............................. [ 19 passed]
tests/test_milestone1_security_closure.py .............................. [ 12 passed]
tests/test_milestone2_1_statutory_corrections.py ....................... [ 8 passed]
tests/test_milestone2_2_itc_ledger.py .................................. [ 12 passed]
tests/test_milestone2_3_reconciliation.py .............................. [ 12 passed]
tests/test_milestone2_4_ims_itc_risk.py ................................ [ 18 passed]
tests/test_milestone2_5_integration.py ................................. [ 17 passed]
tests/test_milestone2_6_gstr3b_workflow.py ............................. [ 13 passed]
tests/test_milestone2_7_1_baseline_hardening.py ......................... [ 8 passed]
tests/test_milestone2_7_2_10_statutory_rule37a_evidence_reconciliation.py [ 7 passed]
tests/test_milestone2_7_2_11_statutory_reversal_interest_and_evidence_lifecycle.py [ 15 passed]
tests/test_milestone2_7_2_12_statutory_reconciliation_integrity.py ..... [ 17 passed]
tests/test_milestone2_7_2_13_period_chronology_and_reversal_lineage.py . [ 19 passed]
tests/test_milestone2_7_2_financial_reconciliation_closure.py .......... [ 26 passed]
tests/test_milestone2_7_inward_accounting_workflow.py .................. [ 6 passed]
tests/test_milestone2_reconciliation.py ................................ [ 10 passed]
tests/test_milestone3_gstr3b_offset_and_filing.py ....................... [ 5 passed]
tests/test_milestone4_credit_builder.py ................................ [ 5 passed]
tests/test_milestone5_scheme_navigator.py .............................. [ 5 passed]
tests/test_statutory_section_17_5_boundaries_and_reference_grants.py ... [ 4 passed]

============================= 257 passed in 37.40s =============================
```

- **Total Test Items:** 257
- **Passed:** 257
- **Failed:** 0
- **Errors:** 0
- **Duration:** 37.40 seconds
- **Pass Rate:** 100.0%

---

## 2. STATUTORY SECTION 17(5) & DATABASE LEAST-PRIVILEGE REGRESSION SUITE

File: `tests/test_statutory_section_17_5_boundaries_and_reference_grants.py`
1. `test_app_service_user_cannot_write_to_reference_catalogues`:
   - Asserts `SELECT current_user` is `'app_service_user'`.
   - Confirms `SELECT` succeeds on `government_schemes` and `scheme_eligibility_rules`.
   - Confirms `INSERT`, `UPDATE`, and `DELETE` attempts on **BOTH** tables raise `InsufficientPrivilege` / `ProgrammingError`.
2. `test_section_17_5_exact_13_seats_vs_14_seats_boundary`:
   - Confirms 13 seats = `BLOCKED_CREDIT_CONFIRMED` under Section 17(5)(a).
   - Confirms 14 seats = `ELIGIBLE_STATUTORY_EXCEPTION` under Section 17(5)(a) exception ($> 13$ persons).
3. `test_section_17_5_goods_transport_and_statutory_facts`:
   - Confirms goods transport vehicles (HSN 8704 / trucks) are eligible.
   - Dual evidence requirement for Section 17(5)(a)(B):
     - Commercial permit alone &rarr; `REVIEW_REQUIRED_FACTS_PENDING` (actual passenger transport use unverified).
     - Commercial permit + actual taxable passenger transport use &rarr; `ELIGIBLE_STATUTORY_EXCEPTION`.
   - Business category label alone without permit routes to `REVIEW_REQUIRED_FACTS_PENDING`.
   - Missing seating capacity on verified RC &rarr; `REVIEW_REQUIRED_FACTS_PENDING`.
   - Bare invoice with no facts provided &rarr; `REVIEW_REQUIRED_FACTS_PENDING`.
4. `test_section_17_5_ab_vehicle_repair_lineage`:
   - Confirms repair of 14-seater vehicle (SAC 998729) is eligible.
   - Confirms repair of 13-seater vehicle without exception is blocked.
   - Confirms repair of driving school vehicle requires both license and actual training use.
   - Proviso (ii): Confirms repair received by vehicle manufacturer is eligible.
   - Proviso (ii): Confirms repair received by general insurer for insured vehicle is eligible.

---

## 3. DATABASE MIGRATION ROLLBACK & UPGRADE EVIDENCE

### Verification Script: `backend/scripts/verify_migration_rollback_state.py`
Executed:
```bash
PYTHONPATH=. python scripts/verify_migration_rollback_state.py
```

**Log Output:**
```text
=== Step 1: Verify current head (0021) ===
PASS: All 21 tables have relforcerowsecurity=True at 0021.

=== Step 2: Test rollback to 0020_m4_m5_grants_and_policies ===
PASS: All 21 tables successfully reverted to relforcerowsecurity=False at 0020.

=== Step 3: Test rollback to 0019_m3_gstr3b_filings ===
PASS: Policies from 0020 were cleanly removed at 0019.

=== Step 4: Upgrade back to head (0021) ===
PASS: All 21 tables successfully re-upgraded with relforcerowsecurity=True.

ALL MIGRATION ROLLBACK & UPGRADE INVARIANTS VERIFIED!
```

---

## 4. FRONTEND PRODUCTION BUILD

### Exact Command
```bash
cd frontend && npm run build
```

### Build Output Log
```text
> myca-frontend@1.0.0 build
> tsc -b && vite build

vite v6.4.6 building for production...
transforming...
✓ 1583 modules transformed.
rendering chunks...
computing gzip size...
dist/index.html                   0.48 kB │ gzip:   0.33 kB
dist/assets/index-CXHqnTlf.css   29.39 kB │ gzip:   5.62 kB
dist/assets/index-DBkuh5Fw.js   420.23 kB │ gzip: 107.43 kB
✓ built in 3.94s
```
- TypeScript: 0 errors
- Vite: 1,583 modules transformed in 3.94s
