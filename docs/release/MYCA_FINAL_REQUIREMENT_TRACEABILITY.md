# MyCA — FINAL REQUIREMENT TRACEABILITY MATRIX

**Document Version:** 1.0.0-final  
**Audit Date:** November 22, 2026  
**Canonical Baseline:** MyCA Master PRD & System Architecture  

This traceability matrix maps every product requirement across all 5 milestones and core capabilities to its concrete codebase implementation, database artifacts, and automated verification tests.

---

## 1. MILESTONE 1 — INVOICE BRAIN & MULTI-TENANT BACKBONE

| Req ID | PRD Requirement Description | Codebase Implementation | Database / Migration | Verification Test |
| :--- | :--- | :--- | :--- | :--- |
| **M1.1** | Multi-tenant organization isolation & user roles | `app/core/dependencies.py`<br>`app/database.py` | `0001_initial_schema.py`<br>`0002_rls_policies.py` | `tests/test_migration_security_baseline.py`<br>`tests/test_milestone1_security_closure.py` |
| **M1.2** | Invoice OCR ingestion & deduplication | `app/modules/invoicing/router.py`<br>`app/modules/invoicing/service.py` | `invoices`, `documents` | `tests/test_invoice_brain.py::test_stage1_document_upload_and_deduplication` |
| **M1.3** | GSTIN checksum & HSN/SAC validation | `app/modules/invoicing/validator.py` | `gst_records` | `tests/test_invoice_brain.py::test_stage3_gstin_checksum_validation`<br>`tests/test_invoice_brain.py::test_stage3_hsn_sac_validation` |
| **M1.4** | Human review & field correction workflow | `app/modules/invoicing/review.py` | `human_resolution_events` | `tests/test_invoice_brain.py::test_stage4_human_review_and_field_correction` |
| **M1.5** | Financial ledger immutability & tamper protection | Database triggers & row immutability | `prevent_invoice_mutation()` | `tests/test_migration_security_baseline.py::test_c1_invoice_financial_immutability` |

---

## 2. MILESTONE 2 — STATUTORY INWARD ENGINE & 4-PASS RECONCILIATION

| Req ID | PRD Requirement Description | Codebase Implementation | Database / Migration | Verification Test |
| :--- | :--- | :--- | :--- | :--- |
| **M2.1** | GSTR-2B JSON & Excel multi-section parser | `app/modules/reconciliation/gstr2b_parser.py` | `gstr2b_imports`, `gstr2b_snapshots` | `tests/test_milestone2_reconciliation.py::test_gstr2b_json_parser_all_sections` |
| **M2.2** | Purchase register CRUD & maker-checker approval | `app/modules/reconciliation/purchase_service.py` | `purchase_documents`, `purchase_line_items` | `tests/test_milestone2_reconciliation.py::test_purchase_register_crud_and_approval` |
| **M2.3** | Deterministic 4-pass reconciliation engine | `app/modules/reconciliation/matching_engine.py` | `reconciliation_runs`, `reconciliation_matches` | `tests/test_milestone2_reconciliation.py::test_deterministic_reconciliation_4_passes` |
| **M2.4** | Statutory Rule 37 & 37A ITC risk classification | `app/modules/reconciliation/itc_engine.py` | `itc_assessments`, `itc_rule_catalogue` | `tests/test_milestone2_7_2_13_period_chronology_and_reversal_lineage.py` |
| **M2.5** | Immutable double-entry ITC ledger & reavailment | `app/modules/reconciliation/ledger_service.py` | `itc_ledger_entries`, `itc_reversal_events` | `tests/test_milestone2_2_itc_ledger.py::test_itc_ledger_lifecycle_and_accounting_integrity` |
| **M2.6** | IMS action tracking & deemed acceptance | `app/modules/reconciliation/ims_service.py` | `ims_document_actions`, `ims_action_history` | `tests/test_milestone2_1_statutory_corrections.py::test_ims_lifecycle_deemed_acceptance_and_recomputation` |
| **M2.7** | GSTR-3B Table 4 working outputs (Model A & B) | `app/modules/reconciliation/unified_report_service.py` | `summary_transactions` | `tests/test_milestone2_7_inward_accounting_workflow.py::test_unified_financial_report_reconciliation` |

---

## 3. MILESTONE 3 — GSTR-3B & RULE 88A TAX OFFSET ENGINE

| Req ID | PRD Requirement Description | Codebase Implementation | Database / Migration | Verification Test |
| :--- | :--- | :--- | :--- | :--- |
| **M3.1** | Section 49 / Rule 88A IGST priority offset | `app/modules/gstr3b/offset_engine.py` | Algorithm in Python / Decimal arithmetic | `tests/test_milestone3_gstr3b_offset_and_filing.py::test_rule_88a_igst_credit_priority_and_exhaustion` |
| **M3.2** | CGST & SGST cross-utilization strict prohibition | `app/modules/gstr3b/offset_engine.py` | Section 49(5)(e)/(f) guard | `tests/test_milestone3_gstr3b_offset_and_filing.py::test_cgst_sgst_cross_offset_strictly_prohibited` |
| **M3.3** | PMT-06 cash challan calculation | `app/modules/gstr3b/offset_engine.py` | Cash shortfall breakdown | `tests/test_milestone3_gstr3b_offset_and_filing.py::test_cash_challan_pmt06_exact_calculation` |
| **M3.4** | Maker-checker review before statutory filing | `app/modules/gstr3b/service.py` | `0019_m3_gstr3b_filings.py` (`gstr3b_filings`) | `tests/test_milestone3_gstr3b_offset_and_filing.py::test_maker_checker_workflow_and_filing_gate` |
| **M3.5** | GSP Sandbox integration & ARN generation | `app/modules/gstr3b/gsp_sandbox.py` | Idempotent hash & regex `AA\d{13}[A-Z0-9]` | `tests/test_milestone3_gstr3b_offset_and_filing.py::test_gsp_sandbox_arn_syntax_and_idempotency` |

---

## 4. MILESTONE 4 — CREDIT BUILDER & CASH FLOW INTELLIGENCE

| Req ID | PRD Requirement Description | Codebase Implementation | Database / Migration | Verification Test |
| :--- | :--- | :--- | :--- | :--- |
| **M4.1** | Bank statement CSV ingestion & transaction parser | `app/modules/credit_builder/service.py` | `financial_transactions`, `financial_summaries` | `tests/test_milestone4_credit_builder.py::test_statement_csv_ingestion_and_summary` |
| **M4.2** | MSME transaction auto-categorization | `app/modules/credit_builder/categorizer.py` | 12 rules (GST, Salary, Rent, Vendor, etc.) | `tests/test_milestone4_credit_builder.py::test_categorizer_rules` |
| **M4.3** | Financial Readiness Score (0–100 weighted index) | `app/modules/credit_builder/service.py` | Weighted DSCR, GST compliance, Operating Margin | `tests/test_milestone4_credit_builder.py::test_msme_financial_readiness_score_and_dossier` |
| **M4.4** | Banker-grade loan dossier generation | `app/modules/credit_builder/service.py` | Summary metrics, DSCR, cash runway | `tests/test_milestone4_credit_builder.py::test_msme_financial_readiness_score_and_dossier` |
| **M4.5** | Multi-tenant financial data isolation | RLS on financial tables | `0020_m4_m5_grants_and_policies.py` | `tests/test_milestone4_credit_builder.py::test_multi_tenant_financial_isolation` |

---

## 5. MILESTONE 5 — GOVERNMENT SCHEME NAVIGATOR

| Req ID | PRD Requirement Description | Codebase Implementation | Database / Migration | Verification Test |
| :--- | :--- | :--- | :--- | :--- |
| **M5.1** | Central MSME schemes seed catalog | `app/modules/scheme_navigator/service.py` | `government_schemes`, `scheme_eligibility_rules` | `tests/test_milestone5_scheme_navigator.py::test_schemes_catalog_seeding_and_retrieval` |
| **M5.2** | Dynamic eligibility rules evaluator | `app/modules/scheme_navigator/evaluator.py` | Operators: `lte`, `gte`, `in`, `contains`, `eq` | `tests/test_milestone5_scheme_navigator.py::test_rule_evaluator_operators` |
| **M5.3** | Profile matching & match score calculation | `app/modules/scheme_navigator/service.py` | `scheme_matches`, `business_profiles` | `tests/test_milestone5_scheme_navigator.py::test_msme_scheme_evaluation_and_matching` |
| **M5.4** | Scheme application document checklist generator | `app/modules/scheme_navigator/service.py` | Scheme criteria, portal URL, documents | `tests/test_milestone5_scheme_navigator.py::test_scheme_checklist` |
| **M5.5** | Tenant profile management API | `app/modules/scheme_navigator/api.py` | GET/POST `/api/v1/scheme-navigator/profile` | `tests/test_milestone5_scheme_navigator.py::test_scheme_navigator_api_endpoints` |

---

## 6. AI FINANCIAL COPILOT & FRONTEND INTERFACE

| Req ID | PRD Requirement Description | Codebase Implementation | Verification Test |
| :--- | :--- | :--- | :--- |
| **AI.1** | Real-time multi-turn advisory grounded in tenant GST | `app/modules/ai_assistant/gemini_client.py`<br>`app/modules/ai_assistant/grounding_service.py` | `tests/test_ai_assistant.py::test_grounding_context_and_quick_insights` |
| **AI.2** | CGST Act & Rules statutory distinction guardrails | `app/modules/ai_assistant/gemini_client.py` | `tests/test_ai_assistant.py::test_ai_statutory_rule_distinction` |
| **UI.1** | 8 Dedicated Navigation Tabs & Responsive Layout | `frontend/src/App.tsx`<br>`frontend/src/pages/*` | `npm run build` (1,583 modules compiled, 0 errors) |
| **UI.2** | JWT Token & Session Bar Management | `frontend/src/api.ts`<br>`frontend/src/App.tsx` | Browser verified end-to-end |
