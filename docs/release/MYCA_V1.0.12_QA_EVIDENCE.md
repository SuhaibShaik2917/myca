# MyCA v1.0.12 — Quality Assurance & Verification Evidence

**Release Target:** `v1.0.12`  
**Test Execution Environment:** PostgreSQL 16 (WSL2), Python 3.12 (venv), Node.js 20 / Vite  
**Test Suite Pass Rate:** **334 / 334 PASSED (100%)**  

---

## 1. Full Backend Pytest Suite Output

```
============================= test session starts ==============================
platform linux -- Python 3.12.3, pytest-9.2.0, pluggy-1.7.0
rootdir: /mnt/c/Users/notyo/.gemini/antigravity/scratch/myca/backend
plugins: anyio-4.16.0, asyncio-1.5.0
asyncio: mode=Mode.STRICT, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collected 334 items

tests/test_ai_assistant.py ...                                           [  0%]
tests/test_auth_token.py .............................                   [  9%]
tests/test_invoice_brain.py .......                                      [ 11%]
tests/test_migration_security_baseline.py .....................          [ 17%]
tests/test_milestone1_security_closure.py .............................. [ 26%]
.........                                                                [ 29%]
tests/test_milestone2_1_statutory_corrections.py .......                 [ 31%]
tests/test_milestone2_2_itc_ledger.py .....                              [ 33%]
tests/test_milestone2_3_itc_reconciliation.py .....                      [ 34%]
tests/test_milestone2_4_source_reconciliation.py ....                    [ 35%]
tests/test_milestone2_5_tax_heads_and_receipt_gate.py ....               [ 37%]
tests/test_milestone2_6_receipt_gate_and_reversal.py .........           [ 39%]
tests/test_milestone2_7_1_statutory_integrity.py .........               [ 42%]
tests/test_milestone2_7_2_10_consolidated_defect_remediation.py ........ [ 44%]
.....                                                                    [ 46%]
tests/test_milestone2_7_2_11_audit_corrections_and_evidence_validation.py . [ 46%]
.......                                                                  [ 48%]
tests/test_milestone2_7_2_12_filing_eligibility_and_statutory_audit.py . [ 49%]
.......                                                                  [ 51%]
tests/test_milestone2_7_2_13_period_chronology_and_reversal_lineage.py . [ 51%]
.....                                                                    [ 52%]
tests/test_milestone2_7_2_14_rule37_37a_separation.py ...............    [ 57%]
tests/test_milestone2_7_2_financial_reconciliation_closure.py .......... [ 60%]
..........................................                               [ 73%]
tests/test_milestone2_7_inward_accounting_workflow.py ......             [ 74%]
tests/test_milestone2_reconciliation.py ..........                       [ 77%]
tests/test_milestone3_gstr3b_offset_and_filing.py .....                  [ 79%]
tests/test_milestone4_credit_builder.py .....                            [ 80%]
tests/test_milestone5_scheme_navigator.py .....                          [ 82%]
tests/test_rollback_script_safety.py ............                        [ 85%]
tests/test_statutory_auditor_provenance_and_replay_hardening.py ........ [ 88%]
.............................                                            [ 97%]
tests/test_statutory_section_17_5_boundaries_and_reference_grants.py ... [ 97%]
.......                                                                  [100%]

============================= 334 passed in 26.17s =============================
```

---

## 2. Migration Rollback Safety & Execution Logs

```
=== Loaded Independent Trusted Baseline Reference from baseline_0019_reference.json ===
  Provenance Origin: Independently provisioned clean PostgreSQL 16 database migrated strictly forward from base to 0019_m3_gstr3b_filings
  Clean Reference DB: myca_independent_reference
  Cryptographic Authentication: SHA256 verified (bfe3e3a342b56732d1d0cf43f8edfc50391df1020a2c487f22fb5834e9503373) [PASS]
  Complete Definitions: 79 grants, 87 policies, 41 tables.

=== Step 1: Establish Baseline at 0019 & Verify Against Independent Reference ===
  PASS: [Step 1: Baseline 0019] Auditor state classified & verified as 'TABLE_NOT_PRESENT'.
  Captured 0019 Database State: 79 grants, 87 policies, 41 tables.
  PASS: Initial downgrade to 0019 matches independent trusted reference snapshot 100% on full definitions (79 grants, 87 policies, 41 tables).

=== Step 1b: Verify Historical Upgrade to 0022 contains Legacy Seeds ===
  PASS: [Step 1b: Fresh Historical Upgrade 0022] Auditor state classified & verified as 'LEGACY_SEEDS_PRESENT_IN_HISTORICAL_0022'.

=== Step 2: Upgrade to Head (0026_gateway_nonce_and_audit) ===
  PASS: [Step 2: Head 0026] Auditor state classified & verified as 'AUDITORS_VERIFIED_CLEAN'.
  Head State (0026): 112 grants, 95 policies.
PASS: Head state (0026) completely verified.

=== Step 2-POPULATED: Populated-Record Migration Downgrade & Nonce Preservation Test ===
  Populated 1 MANUAL_DOCUMENT_REVIEWED auditor record, 1 audit event, and 1 consumed gateway nonce.
  Executing Alembic downgrade to 0025 on populated database...
  PASS: Downgrade to 0025 safely preserved all evidence, audit events, AND consumed gateway nonce history.
  Executing Alembic re-upgrade to Head (0026)...
  PASS: Replay attack presenting same gateway nonce after re-upgrade rejected by persistent PostgreSQL nonce registry constraint.
PASS: Populated-state downgrade, evidence preservation, nonce replay defense, and re-upgrade completely verified.

=== Step 2a: Downgrade to 0025_manual_review_and_nonce & Verify ===
  PASS: [Step 2a: Downgrade 0025] Auditor state classified & verified as 'AUDITORS_VERIFIED_CLEAN'.

=== Step 2a-2: Downgrade to 0024_auditor_authentic_evidence & Verify ===
  PASS: [Step 2a-2: Downgrade 0024] Auditor state classified & verified as 'AUDITORS_VERIFIED_CLEAN'.

=== Step 2b: Downgrade to 0023_auditor_provenance & Verify State ===
  PASS: [Step 2b: Downgrade 0023] Auditor state classified & verified as 'AUDITORS_VERIFIED_CLEAN'.

=== Step 2c: Downgrade to 0022_statutory_auditors_replay & Verify Clean Auditor Registry ===
  PASS: [Step 2c: Clean Downgrade 0022] Auditor state classified & verified as 'AUDITORS_VERIFIED_CLEAN'.

=== Step 2d: Re-upgrade to Head (0026_gateway_nonce_and_audit) ===
  PASS: [Step 2d: Re-upgrade Head 0026] Auditor state classified & verified as 'AUDITORS_VERIFIED_CLEAN'.
PASS: Downgrade rollback and re-upgrade cycles verified with zero fake auditor identities.

=== Step 3: Downgrade to 0020_m4_m5_grants_and_policies ===
  PASS: [Step 3: Downgrade 0020] Auditor state classified & verified as 'TABLE_NOT_PRESENT'.
  State at 0020: 105 grants, 95 policies.
PASS: Downgrade to 0020 completely verified.

=== Step 4: Downgrade to 0019_m3_gstr3b_filings & Assert Exact Baseline Match ===
  PASS: [Step 4: Downgrade 0019] Auditor state classified & verified as 'TABLE_NOT_PRESENT'.
  PASS: All 8 policies from 0020 were cleanly dropped.
  PASS: Exact match on all 79 full grant definitions against independent reference.
  PASS: Exact match on all 87 complete policy definitions (including cmd, roles, qual, with_check) against independent reference.
  PASS: Exact match on all table RLS and FORCE RLS flags against independent reference.

=== Step 5: Re-Upgrade to Head (0026_gateway_nonce_and_audit) ===
  PASS: [Step 5: Final Re-upgrade 0026] Auditor state classified & verified as 'AUDITORS_VERIFIED_CLEAN'.
  PASS: Re-upgrade to head completely restored production security state.

ALL MIGRATION ROLLBACK & INDEPENDENT BASELINE REFERENCE INVARIANTS 100% VERIFIED!
```

---

## 3. Comprehensive RLS Audit Output

```
=== [1] Auditing PostgreSQL Roles & Table RLS Configuration (All 36 Tables) ===
Role Check: app_service_user -> Superuser=False, BypassRLS=False, CanLogin=True
  --> Role Security PASS: app_service_user has NO superuser and NO bypassrls privileges.
  Table 'business_profiles               ': RLS=True, FORCE_RLS=True [PASS]
  Table 'documents                       ': RLS=True, FORCE_RLS=True [PASS]
  Table 'financial_summaries             ': RLS=True, FORCE_RLS=True [PASS]
  Table 'financial_transactions          ': RLS=True, FORCE_RLS=True [PASS]
  Table 'global_users                    ': RLS=True, FORCE_RLS=True [PASS]
  Table 'goods_receipt_lot_ledger        ': RLS=True, FORCE_RLS=True [PASS]
  Table 'gst_records                     ': RLS=True, FORCE_RLS=True [PASS]
  Table 'gst_registrations               ': RLS=True, FORCE_RLS=True [PASS]
  Table 'gstr2b_imports                  ': RLS=True, FORCE_RLS=True [PASS]
  Table 'gstr2b_snapshots                ': RLS=True, FORCE_RLS=True [PASS]
  Table 'gstr2b_source_documents         ': RLS=True, FORCE_RLS=True [PASS]
  Table 'gstr2b_unbooked_candidates      ': RLS=True, FORCE_RLS=True [PASS]
  Table 'gstr3b_filings                  ': RLS=True, FORCE_RLS=True [PASS]
  Table 'human_resolution_events         ': RLS=True, FORCE_RLS=True [PASS]
  Table 'ims_action_history              ': RLS=True, FORCE_RLS=True [PASS]
  Table 'ims_document_actions            ': RLS=True, FORCE_RLS=True [PASS]
  Table 'invoice_line_items              ': RLS=True, FORCE_RLS=True [PASS]
  Table 'invoices                        ': RLS=True, FORCE_RLS=True [PASS]
  Table 'itc_assessments                 ': RLS=True, FORCE_RLS=True [PASS]
  Table 'itc_ledger_entries              ': RLS=True, FORCE_RLS=True [PASS]
  Table 'itc_reavailment_events          ': RLS=True, FORCE_RLS=True [PASS]
  Table 'itc_reversal_events             ': RLS=True, FORCE_RLS=True [PASS]
  Table 'organization_memberships        ': RLS=True, FORCE_RLS=True [PASS]
  Table 'organizations                   ': RLS=True, FORCE_RLS=True [PASS]
  Table 'purchase_documents              ': RLS=True, FORCE_RLS=True [PASS]
  Table 'purchase_line_items             ': RLS=True, FORCE_RLS=True [PASS]
  Table 'purchase_payment_allocations    ': RLS=True, FORCE_RLS=True [PASS]
  Table 'purchase_payments               ': RLS=True, FORCE_RLS=True [PASS]
  Table 'receipt_remediation_audit       ': RLS=True, FORCE_RLS=True [PASS]
  Table 'reconciliation_matches          ': RLS=True, FORCE_RLS=True [PASS]
  Table 'reconciliation_runs             ': RLS=True, FORCE_RLS=True [PASS]
  Table 'reconciliation_variances        ': RLS=True, FORCE_RLS=True [PASS]
  Table 'scheme_matches                  ': RLS=True, FORCE_RLS=True [PASS]
  Table 'summary_transactions            ': RLS=True, FORCE_RLS=True [PASS]
  Table 'worker_service_authorizations   ': RLS=True, FORCE_RLS=True [PASS]
  Table 'worker_tasks                    ': RLS=True, FORCE_RLS=True [PASS]
  --> RLS Reconciliation: Exactly 36 of 36 tenant tables enforce RLS=True AND FORCE_RLS=True (100%).
  --> Reference & system catalogues (9 tables) enforce table-level least-privilege role permissions.
  Reference Catalogues: app_service_user has strictly SELECT privilege [PASS]
=== [1] Role and Table RLS Configuration: 100% VERIFIED SECURE ===
```

---

## 4. Frontend Production Build Output

```
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
✓ built in 2.31s
```
