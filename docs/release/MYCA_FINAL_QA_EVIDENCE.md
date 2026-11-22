# MyCA — FINAL QA & TEST SUITE VERIFICATION EVIDENCE

**Audit Date:** November 23, 2026  
**Environment:** Windows 11 / Python 3.14.4 / PostgreSQL 16.4 / Node.js v20.x  
**Branch:** `remediation/audit-findings-phase3` (Merging to `master`)  
**Git Checkpoints:** Baseline `9f97516`, Audit Phase 1 `d421f0e`, Phase 2 Baseline `9961014`, Remediation Head (Phase 3)  

---

## 1. REPRODUCIBLE TEST RUN LOG

### Exact Command
```powershell
cd C:\Users\notyo\.gemini\antigravity\scratch\myca\backend
python -m pytest tests/ -v --tb=short
```

### Complete Test Run Summary
```text
============================= test session starts ==============================
platform win32 -- Python 3.14.4, pytest-9.1.1, pluggy-1.6.0
rootdir: C:\Users\notyo\.gemini\antigravity\scratch\myca\backend
plugins: anyio-4.13.0, asyncio-1.4.0
asyncio: mode=Mode.STRICT, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collected 258 items

tests\test_ai_assistant.py ...                                           [  1%]
tests\test_auth_token.py .......                                         [  3%]
tests\test_invoice_brain.py .......                                      [  6%]
tests\test_migration_security_baseline.py .....................          [ 14%]
tests\test_milestone1_security_closure.py .............................. [ 26%]
.........                                                                [ 29%]
tests\test_milestone2_1_statutory_corrections.py .......                 [ 32%]
tests\test_milestone2_2_itc_ledger.py .....                              [ 34%]
tests\test_milestone2_3_itc_reconciliation.py .....                      [ 36%]
tests\test_milestone2_4_source_reconciliation.py ....                    [ 37%]
tests\test_milestone2_5_tax_heads_and_receipt_gate.py ....               [ 39%]
tests\test_milestone2_6_receipt_gate_and_reversal.py .........           [ 43%]
tests\test_milestone2_7_1_statutory_integrity.py .........               [ 46%]
tests\test_milestone2_7_2_10_consolidated_defect_remediation.py ........ [ 49%]
.....                                                                    [ 51%]
tests\test_milestone2_7_2_11_audit_corrections_and_evidence_validation.py . [ 51%]
.......                                                                  [ 54%]
tests\test_milestone2_7_2_12_filing_eligibility_and_statutory_audit.py . [ 55%]
.......                                                                  [ 57%]
tests\test_milestone2_7_2_13_period_chronology_and_reversal_lineage.py . [ 58%]
.....                                                                    [ 60%]
tests\test_milestone2_7_2_14_rule37_37a_separation.py ...............    [ 65%]
tests\test_milestone2_7_2_financial_reconciliation_closure.py .......... [ 69%]
..........................................                               [ 86%]
tests\test_milestone2_7_inward_accounting_workflow.py ......             [ 88%]
tests\test_milestone2_reconciliation.py ..........                       [ 92%]
tests\test_milestone3_gstr3b_offset_and_filing.py .....                  [ 94%]
tests\test_milestone4_credit_builder.py .....                            [ 96%]
tests\test_milestone5_scheme_navigator.py .....                          [ 98%]
tests\test_statutory_section_17_5_boundaries_and_reference_grants.py ... [ 99%]
..                                                                       [100%]

============================ 258 passed in 33.09s =============================
```

- **Total Test Items:** 258
- **Passed:** 258
- **Failed:** 0
- **Errors:** 0
- **Duration:** 33.09 seconds
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
   - **Proviso (ii)(I) Manufacturer Negative Tests:**
     - Verified manufacturing license + confirmed vehicle nexus &rarr; `ELIGIBLE_STATUTORY_EXCEPTION`.
     - Manufacturer claimed but license unverified &rarr; `REVIEW_REQUIRED_FACTS_PENDING`.
     - Factual context unverified &rarr; `REVIEW_REQUIRED_FACTS_PENDING`.
   - **Proviso (ii)(II) Insurer Negative Tests:**
     - Verified IRDAI license + vehicle insured by taxpayer &rarr; `ELIGIBLE_STATUTORY_EXCEPTION`.
     - Vehicle NOT insured by taxpayer (missing policy evidence) &rarr; `REVIEW_REQUIRED_FACTS_PENDING`.
     - Insurer license unverified &rarr; `REVIEW_REQUIRED_FACTS_PENDING`.
     - Factual context unverified &rarr; `REVIEW_REQUIRED_FACTS_PENDING`.
5. `test_review_required_preservation_in_itc_engine_and_gstr3b`:
   - Evaluates vehicle invoice with unverified facts (`is_factual_context_verified: False`).
   - Asserts engine produces `eligibility_status = ITCEligibilityEnum.REVIEW_REQUIRED`.
   - Asserts `risk_reversal_amount = Decimal("0.00")` (not confirmed statutory reversal).
   - Asserts `potential_itc = Decimal("28000.00")`.
   - Feeds assessment into downstream `generate_gstr3b_table4_summary`:
     - Asserts `ineligible_sec_17_5_blocked = Decimal("0.00")`.
     - Asserts `total_itc_reversed = Decimal("0.00")`.
     - Asserts `net_itc_available = Decimal("28000.00")` (preserved for CA adjudication).

---

## 3. DATABASE MIGRATION ROLLBACK & BASELINE VERIFICATION EVIDENCE

### Verification Script: `backend/scripts/verify_migration_rollback_state.py`
Executed:
```powershell
python scripts/verify_migration_rollback_state.py
```

**Log Output:**
```text
=== Step 1: Establish & Capture Baseline at 0019_m3_gstr3b_filings ===
  Captured 0019 Baseline: 79 grants, 88 policies, 41 tables.

=== Step 2: Upgrade to Head (0021_enforce_tenant_force_rls) ===
  Head State (0021): 105 grants, 96 policies.
PASS: Head state (0021) completely verified.

=== Step 3: Downgrade to 0020_m4_m5_grants_and_policies ===
  State at 0020: 105 grants, 96 policies.
PASS: Downgrade to 0020 completely verified.

=== Step 4: Downgrade to 0019_m3_gstr3b_filings & Assert Exact Baseline Match ===
  PASS: All 8 policies from 0020 were cleanly dropped.
  PASS: Exact match on all 79 role_table_grants for app_service_user.
  PASS: Exact match on all 88 policies in pg_policies.
  PASS: Exact match on all table RLS and FORCE RLS flags.

=== Step 5: Re-Upgrade to Head (0021_enforce_tenant_force_rls) ===
  PASS: Re-upgrade to head completely restored production security state.

ALL MIGRATION ROLLBACK & BASELINE RESTORATION INVARIANTS 100% VERIFIED!
```

---

## 4. FRONTEND PRODUCTION BUILD

### Exact Command
```powershell
cd frontend
npm run build
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
✓ built in 3.48s
```
- TypeScript: 0 errors
- Vite: 1,583 modules transformed in 3.48s
