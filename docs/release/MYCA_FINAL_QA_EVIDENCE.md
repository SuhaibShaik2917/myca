# MyCA — FINAL QA & TEST SUITE VERIFICATION EVIDENCE

**Audit Date:** November 23, 2026  
**Environment:** Linux (WSL Ubuntu 24.04 / Python 3.12.3 / PostgreSQL 16.11 / Node.js v24.14)  
**Branch:** `remediation/audit-findings-phase3` (Merging to `master`)  
**Git Checkpoints:** Baseline `9f97516`, Audit Phase 1 `d421f0e`, Phase 2 Baseline `9961014`, v1.0.0 `56dc543`, v1.0.1 `eaa451a`, v1.0.2 `abbc575`, v1.0.3 `49b33ae`, Release Checkpoint `v1.0.4`  

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
collected 263 items

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
tests/test_milestone4_credit_builder.py .....                            [ 94%]
tests/test_milestone5_scheme_navigator.py .....                          [ 96%]
tests/test_statutory_section_17_5_boundaries_and_reference_grants.py .......... [100%]

============================ 263 passed in 20.76s =============================
```

- **Total Test Items:** 263
- **Passed:** 263
- **Failed:** 0
- **Errors:** 0
- **Duration:** 20.76 seconds
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
   - Evaluates multi-line invoices across 15 comprehensive statutory scenarios including explicit tax allocations, proportional calculations, dynamic tolerance gating, and rate representations.
7. `test_statutory_tariff_effective_date_and_subheadings` (v1.0.4):
   - Notification No. 19/2025-IT (Rate):
     - Pan Masala `2106 90 20`: 28% pre-Feb 2026 / 40% post-Feb 2026.
     - Heading 2404: Strictly subheadings `2404 11 00` and `2404 19 00` move to 40% post-01.02.2026 (28% pre-Feb 2026); `2404 12 00` (e-liquids) and `2404 91 00`, `2404 92 00`, `2404 99 00` (oral/transdermal) are strictly 18% (40% rejected); broad `2404` fails safe to `AMBIGUOUS_TARIFF`.
     - Heading 2401: Unmanufactured tobacco leaves `2401 10` and `2401 20` strictly 5% (Schedule I); tobacco refuse `2401 30 00` 40% post-01.02.2026 (28% pre-Feb 2026); incomplete `2401` fails safe to `AMBIGUOUS_TARIFF`.
     - Heading 2403: Biris `2403 19 21` / `2403 19 29` moved to 18% schedule from 1 Feb 2026 (rejecting 28% and 40%); exact 8-digit manufactured tobacco (`2403 11 10`, `2403 19 10`, `2403 19 90`, `2403 91 00`, `2403 99 10`, `2403 99 20`, `2403 99 30`, `2403 99 90`) moves to 40% post-01.02.2026; broad `2403` or 6-digit `240319`/`240399` fails safe to `AMBIGUOUS_TARIFF`.
     - Heading 2402: Cigarettes and cigars 28% pre-Feb 2026 / 40% post-Feb 2026.
   - Notification No. 01/2026-IT (Rate) & Corrigendum G.S.R. 339(E):
     - Non-alcoholic beer `2202 91 00` (Corrigendum G.S.R. 339(E) dated 6 May 2026) and caffeinated drinks `2202 99 91` / `2202 99 99` move to 40% post-01.05.2026 (28% pre-May 2026).
     - Aerated waters containing added sugar (`2202 10 10`, `2202 10 20`, `2202 10 90`, `2202 10`) statutorily capped at 28% (40% prohibited).
     - Fruit juice and milk drinks `2202 90` / `2202 99 10` / `2202 99 21` strictly 5% or 12%.
     - Generic heading `2202` without subheading fails safe to CA review (`AMBIGUOUS_TARIFF`).
8. `test_statutory_cryptographic_hmac_and_auditor_registry` (v1.0.4):
   - Fail-closed secret key resolution: raises `KeyResolverError` immediately when `STATUTORY_AUDIT_SIGNING_KEY` is missing, empty, or shorter than 32 characters.
   - Auditor credentials validation: validates ICAI membership (`^ICAI-[MF]-\d{6}$`), firm registration (`^\d{6}[A-Z]$`), Certificate of Practice (`cop_status == "ACTIVE"`), validity period, and blacklists revoked auditors (`REVOKED_STATUTORY_AUDITOR_IDS`).
   - Timestamp freshness: strictly validates ISO 8601 UTC timestamp within 24-hour freshness window and 5-minute future clock skew.
   - Replay attack defense: in-memory replay cache tracking `(doc_id, doc_number)` prevents cross-document token reuse.
   - Server-side HMAC-SHA256 verification over canonical document state with constant-time `hmac.compare_digest`.
9. `test_statutory_decimal_precision_and_component_rates` (v1.0.4):
   - Rejection of excess decimal precision beyond statutory 2 decimal places (`18.004`, `0.254`, `5.001`, `0.18004`), prohibiting silent rounding.
   - Component rate aggregation: `cgst_rate: 9% + sgst_rate: 9% = 18%`; `igst_rate: 5%` direct; standalone ambiguous component rates (14% or 20% in `gst_rate`) rejected; `1.50%` prohibited as total statutory rate.
10. `test_statutory_zero_trust_missing_or_unknown_hsn` (v1.0.4):
    - Missing HSN (`""` or `None`) and unknown HSN (`"999999"`) fail safe to CA review (`REVIEW_REQUIRED`) with ₹0.00 reversal across single-line, explicit-line, and multi-line proportional invoices.
    - End-to-end multi-line invoice evaluation with missing HSN line routes to `REVIEW_REQUIRED` with ₹0.00 reversal.

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

---

## 5. CONTINUOUS INTEGRATION (CI) AUTOMATION (v1.0.4)

File: `.github/workflows/ci.yml`
- **PostgreSQL 16 Service Container:** Provisions official `postgres:16` container with health checks (`pg_isready`), dedicated application database `myca`, and administrative password credentials.
- **Environment & Key Configuration:**
  - `STATUTORY_AUDIT_SIGNING_KEY` injected via environment with 64-character entropy, satisfying fail-closed key resolution requirements.
  - Runtime and administrative database URLs configured for non-superuser `app_service_user`.
- **Database Provisioning & Alembic Migrations:**
  - Executes `alembic upgrade head` to apply all migrations up to `0021_enforce_tenant_force_rls`.
  - Configures dedicated `app_service_user` with restricted grants and verified passwords.
- **Full Test Suite & Audit Script Execution:**
  - Replaced legacy `--collect-only` with live test execution:
    ```bash
    python -m pytest tests/ -v --tb=short
    ```
  - Executes `verify_migration_rollback_state.py` to cryptographically verify schema rollback invariants.
  - Executes `audit_rls_security_comprehensive.py` to audit table-level RLS, forced RLS, and 11-domain cross-tenant mutation blocks.
