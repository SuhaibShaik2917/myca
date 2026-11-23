# MyCA — FINAL QA & TEST SUITE VERIFICATION EVIDENCE

**Audit Date:** November 23, 2026  
**Environment:** Linux (WSL Ubuntu 24.04 / Python 3.12.3 / PostgreSQL 16.11 / Node.js v24.14)  
**Branch:** `remediation/audit-findings-phase3` (Merging to `master`)  
**Git Checkpoints:** Baseline `9f97516`, Audit Phase 1 `d421f0e`, Phase 2 Baseline `9961014`, Release Checkpoint v1.0.1  

---

## 1. REPRODUCIBLE TEST RUN LOG

### Exact Command
```bash
cd backend
PYTHONPATH=. /opt/myca-venv/bin/pytest tests/ -v --tb=short
```

### Complete Test Run Summary
```text
============================= test session starts ==============================
platform linux -- Python 3.12.3, pytest-9.2.0, pluggy-1.7.0 -- /opt/myca-venv/bin/python3
cachedir: .pytest_cache
rootdir: /mnt/c/Users/notyo/.gemini/antigravity/scratch/myca/backend
plugins: anyio-4.16.0, asyncio-1.5.0
asyncio: mode=Mode.STRICT, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collected 259 items

tests/test_ai_assistant.py ...                                           [  1%]
tests/test_auth_token.py .......                                         [  3%]
tests/test_invoice_brain.py .......                                      [  6%]
tests/test_migration_security_baseline.py .....................          [ 14%]
tests/test_milestone1_security_closure.py .............................. [ 26%]
.........                                                                [ 29%]
tests/test_milestone2_1_statutory_corrections.py .......                 [ 32%]
tests/test_milestone2_2_itc_ledger.py .....                              [ 34%]
tests/test_milestone2_3_itc_reconciliation.py .....                      [ 36%]
tests/test_milestone2_4_source_reconciliation.py ....                    [ 37%]
tests/test_milestone2_5_tax_heads_and_receipt_gate.py ....               [ 39%]
tests/test_milestone2_6_receipt_gate_and_reversal.py .........           [ 42%]
tests/test_milestone2_7_1_statutory_integrity.py .........               [ 46%]
tests/test_milestone2_7_2_10_consolidated_defect_remediation.py ........ [ 49%]
.....                                                                    [ 51%]
tests/test_milestone2_7_2_11_audit_corrections_and_evidence_validation.py . [ 51%]
.......                                                                  [ 54%]
tests/test_milestone2_7_2_12_filing_eligibility_and_statutory_audit.py . [ 54%]
.......                                                                  [ 57%]
tests/test_milestone2_7_2_13_period_chronology_and_reversal_lineage.py . [ 57%]
.....                                                                    [ 59%]
tests/test_milestone2_7_2_14_rule37_37a_separation.py ...............    [ 65%]
tests/test_milestone2_7_2_financial_reconciliation_closure.py .......... [ 69%]
..........................................                               [ 85%]
tests/test_milestone2_7_inward_accounting_workflow.py ......             [ 88%]
tests/test_milestone2_reconciliation.py ..........                       [ 91%]
tests/test_milestone3_gstr3b_offset_and_filing.py .....                  [ 93%]
tests/test_milestone4_credit_builder.py .....                            [ 95%]
tests/test_milestone5_scheme_navigator.py .....                          [ 97%]
tests/test_statutory_section_17_5_boundaries_and_reference_grants.py ... [ 98%]
...                                                                      [100%]

============================ 259 passed in 18.62s =============================
```

- **Total Test Items:** 259
- **Passed:** 259
- **Failed:** 0
- **Errors:** 0
- **Duration:** 18.62 seconds
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
     - Vehicle nexus missing/omitted &rarr; `REVIEW_REQUIRED_FACTS_PENDING`.
   - **Proviso (ii)(II) Insurer Negative Tests:**
     - Verified IRDAI license + vehicle insured by taxpayer &rarr; `ELIGIBLE_STATUTORY_EXCEPTION`.
     - Vehicle NOT insured by taxpayer (missing policy evidence) &rarr; `REVIEW_REQUIRED_FACTS_PENDING`.
     - Context unverified &rarr; `REVIEW_REQUIRED_FACTS_PENDING`.
     - Commercial business having car repaired with policy &rarr; evaluated on seating capacity (not insurer).
     - Third-party policy issuer &rarr; `REVIEW_REQUIRED_FACTS_PENDING`.
     - Policy document verified alone without proof vehicle is insured by taxpayer &rarr; `REVIEW_REQUIRED_FACTS_PENDING`.
5. `test_review_required_preservation_in_itc_engine_and_gstr3b`:
   - Items pending factual review receive status `REVIEW_REQUIRED` (not `BLOCKED_CREDIT_INDICATOR`).
   - Risk reversal amount is strictly ₹0.00 (not confirmed reversal).
   - In GSTR-3B Table 4(B)(1), permanent reversals are ₹0.00; Net ITC in 4(C) retains the full amount until CA confirms block.
6. `test_multi_line_invoice_exception_and_blocked_catering`:
   - Evaluates multi-line invoices across 14 comprehensive statutory scenarios:
     - Scenario A (Explicit Line Tax): ₹2,000 catering reversed out of ₹5,000 total invoice tax, preserving ₹3,000 eligible repair credit.
     - Scenario B1 (Rate-Aware Proportional Allocation): Vehicle repair at 18% (₹20,000 -> ₹3,600 tax) and catering at 5% (₹10,000 -> ₹500 tax), total tax ₹4,100; catering reversed at exactly ₹500.00, NOT ₹1,366.67 from an unweighted ratio.
     - Scenario B2 (Explicit Line Tax Discrepancy Gate): Line taxes (₹3,000 + ₹1,500 = ₹4,500) fail to reconcile to invoice tax (₹5,000); routes to `REVIEW_REQUIRED` with `risk_reversal_amount = Decimal("0.00")`.
     - Scenario C1 (Missing Rates on Mixed Lines): Line taxable values provided without GST rates; routes to `REVIEW_REQUIRED` with `0.00` reversal.
     - Scenario C2 (Disallowed GST-Inclusive Total Amount Basis): Only `total_amount`/`line_total` provided without pure `taxable_value`; routes to `REVIEW_REQUIRED` with `0.00` reversal.
     - Scenario C3 (Completely Missing Line Data): Invoices without line-level itemization route to `REVIEW_REQUIRED` with `0.00` reversal.
     - Scenario D1 (Document-Level Rate without Uniformity Gate): Multi-line invoice cannot inherit document rate without `is_uniform_rate_verified`; routes to `REVIEW_REQUIRED` with `0.00` reversal.
     - Scenario D2 (Non-Statutory Rate on Line Items): Blended/composite rate (13.67%) rejected under statutory rate whitelist; routes to `REVIEW_REQUIRED` with `0.00` reversal.
     - Scenario D3 (Document-Level Rate WITH Uniformity Gate): `is_uniform_rate_verified: True` allows valid statutory rate (18%) across lines, correctly reversing ₹1,800 catering and granting ₹3,600 repair.
     - Scenario D4 (Dynamic Rounding Tolerance Exceeded): Derived tolerance $N \times 0.01$ (₹0.02 for 2 lines) exceeded by ₹0.05 discrepancy routes to `REVIEW_REQUIRED` with `0.00` reversal.
     - Scenario D5 (Legitimate 40.00% Statutory Rate): Notification No. 9/2025-Integrated Tax (Rate) 40% rate on aerated waters / pan masala correctly validated and allocated (₹4,000 catering/luxury reversed).
     - Scenario D6 (Legitimate 0.25% Statutory Rate): Schedule V rate (0.25%) parsed directly as percentage points without erroneous 100x inflation to 25.00% (reverses ₹25.00).
     - Scenario D7 (Strict Boolean Uniform-Rate Verification): String values ("false", "False", "0", "true", "yes") strictly rejected; prevents truthiness bypass.
     - Scenario D8 (Structured Verification Record Acceptance): Verification metadata object with verifier identity accepted and authorized.

---

## 3. DATABASE MIGRATION ROLLBACK & BASELINE VERIFICATION EVIDENCE

### Verification Script: `backend/scripts/verify_migration_rollback_state.py`
Executed:
```bash
cd backend
PYTHONPATH=. /opt/myca-venv/bin/python3 scripts/verify_migration_rollback_state.py
```

**Log Output:**
```text
=== Loaded Independent Trusted Baseline Reference from baseline_0019_reference.json ===
  Provenance Origin: Independently provisioned clean PostgreSQL 16 database migrated strictly forward from base to 0019_m3_gstr3b_filings
  Clean Reference DB: myca_independent_reference
  Cryptographic Authentication: SHA256 verified (bfe3e3a342b56732d1d0cf43f8edfc50391df1020a2c487f22fb5834e9503373) [PASS]
  Complete Definitions: 79 grants, 87 policies, 41 tables.

=== Step 1: Establish Baseline at 0019 & Verify Against Independent Reference ===
  Captured 0019 Database State: 79 grants, 87 policies, 41 tables.
  PASS: Initial downgrade to 0019 matches independent trusted reference snapshot 100% on full definitions (79 grants, 87 policies, 41 tables).

=== Step 2: Upgrade to Head (0021_enforce_tenant_force_rls) ===
  Head State (0021): 105 grants, 95 policies.
PASS: Head state (0021) completely verified.

=== Step 3: Downgrade to 0020_m4_m5_grants_and_policies ===
  State at 0020: 105 grants, 95 policies.
PASS: Downgrade to 0020 completely verified.

=== Step 4: Downgrade to 0019_m3_gstr3b_filings & Assert Exact Baseline Match ===
  PASS: All 8 policies from 0020 were cleanly dropped.
  PASS: Exact match on all 79 full grant definitions against independent reference.
  PASS: Exact match on all 87 complete policy definitions (including cmd, roles, qual, with_check) against independent reference.
  PASS: Exact match on all table RLS and FORCE RLS flags against independent reference.

=== Step 5: Re-Upgrade to Head (0021_enforce_tenant_force_rls) ===
  PASS: Re-upgrade to head completely restored production security state.

ALL MIGRATION ROLLBACK & INDEPENDENT BASELINE REFERENCE INVARIANTS 100% VERIFIED!
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
✓ built in 2.41s
```
- TypeScript: 0 errors
- Vite: 1,583 modules transformed in 2.41s
