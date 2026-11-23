# MyCA — Final PRD Requirement Traceability Matrix

This document maps every requirement defined in the canonical MyCA Product Requirements Document (PRD) to its exact code implementation file, API route, database schema, and test suite verification.

---

## 1. Milestone 1: Core Outward Invoice Brain & Multi-Tenant Foundations

| PRD Req ID | Requirement Description | Implementation Location | Database / Schema | Automated Test Verification | Status |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **M1-F01** | Multi-tenant organization and user account provisioning | `backend/app/modules/auth/` | `public.organizations`, `public.global_users`, `public.organization_memberships` | `test_auth_token.py` | Verified |
| **M1-F02** | JWT Bearer authentication with tenant claims | `backend/app/core/security.py`, `backend/app/core/dependencies.py` | Session context GUC `app.current_org`, `app.current_user` | `test_auth_token.py` | Verified |
| **M1-F03** | Outward invoice upload and OCR ingestion pipeline | `backend/app/modules/invoice_brain/` | `public.invoices`, `public.documents` | `test_invoice_brain.py` | Verified |
| **M1-F04** | Invoice line item mathematical reconciliation | `backend/app/modules/invoice_brain/service.py` | `public.invoice_line_items` | `test_invoice_brain.py` | Verified |
| **M1-F05** | Strict state machine lifecycle (`draft` &rarr; `approved`) | `backend/app/modules/invoice_brain/service.py` | `chk_invoice_status` | `test_invoice_brain.py` | Verified |
| **M1-S01** | Multi-tenant Row Level Security (RLS) enforcement | `alembic/versions/0007_rls_policies_and_grants.py` | `FORCE ROW LEVEL SECURITY` | `test_milestone1_security_closure.py` | Verified |

---

## 2. Milestone 2: Inward Statutory Engine & 4-Pass Reconciliation

| PRD Req ID | Requirement Description | Implementation Location | Database / Schema | Automated Test Verification | Status |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **M2-F01** | GSTR-2B JSON statement ingestion & cryptographic snapshot | `backend/app/modules/reconciliation/service.py` | `public.gstr2b_snapshots`, `public.gstr2b_documents` | `test_milestone2_reconciliation.py` | Verified |
| **M2-F02** | Independent Purchase Register with payment allocations | `backend/app/modules/reconciliation/api.py` | `public.purchase_documents`, `public.purchase_payments` | `test_milestone2_reconciliation.py` | Verified |
| **M2-F03** | 4-Pass Reconciliation Engine (Exact, Tolerance, Timing, Unmatched) | `backend/app/modules/reconciliation/engine.py` | `public.reconciliation_runs`, `public.reconciliation_matches` | `test_milestone2_reconciliation.py` | Verified |
| **M2-S01** | Strict statutory distinction between Rule 37 and Rule 37A | `backend/app/modules/reconciliation/exception_service.py` | `public.itc_ledger_entries`, `public.reversal_records` | `test_milestone2_7_2_14_rule37_37a_separation.py` | Verified |
| **M2-S02** | Immutable Event-Based ITC Ledger | `backend/app/modules/reconciliation/lot_service.py` | `public.itc_ledger_entries`, `public.itc_ledger_reversals` | `test_milestone2_2_itc_ledger.py` | Verified |
| **M2-S03** | Section 16(2)(b) Goods Receipt Gating before ITC Availment | `backend/app/modules/reconciliation/api.py` | `purchase_documents.goods_receipt_date` | `test_milestone2_5_tax_heads_and_receipt_gate.py` | Verified |
| **M2-S04** | Section 16(4) Annual Cut-Off (30th Nov) permanent extinction | `backend/app/modules/reconciliation/api.py` | `itc_ledger_entries.is_sec_16_4_ineligible` | `test_milestone2_7_1_statutory_integrity.py` | Verified |
| **M2-S05** | Table 4 GSTR-3B Working Summary Generation | `backend/app/modules/reconciliation/api.py` | `getTable4Summary` endpoint | `test_milestone2_7_inward_accounting_workflow.py` | Verified |

---

## 3. Milestone 3: GSTR-3B Preparation, Electronic Ledger Offset Engine & Maker-Checker

| PRD Req ID | Requirement Description | Implementation Location | Database / Schema | Automated Test Verification | Status |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **M3-F01** | Table 3.1 Outward Liability auto-aggregation | `backend/app/modules/gstr3b/service.py` | `public.invoices` &rarr; `public.gstr3b_filings.table_3_1_data` | `test_milestone3_gstr3b_offset_and_filing.py` | Verified |
| **M3-F02** | Table 4 Eligible ITC auto-aggregation from reconciled 2B | `backend/app/modules/gstr3b/service.py` | `public.purchase_documents` &rarr; `public.gstr3b_filings.table_4_data` | `test_milestone3_gstr3b_offset_and_filing.py` | Verified |
| **M3-F03** | Electronic Ledger Offset Engine under Sec 49 & Rule 88A | `backend/app/modules/gstr3b/offset_engine.py` | `public.gstr3b_filings.offset_data` | `test_milestone3_gstr3b_offset_and_filing.py` | Verified |
| **M3-S01** | Mandatory 100% IGST credit exhaustion priority | `backend/app/modules/gstr3b/offset_engine.py` | Rule 88A Step 1 | `test_milestone3_gstr3b_offset_and_filing.py` | Verified |
| **M3-S02** | Strict cross-head offset prohibition (CGST &harr; SGST barred) | `backend/app/modules/gstr3b/offset_engine.py` | Sections 49(5)(e) and 49(5)(f) | `test_milestone3_gstr3b_offset_and_filing.py` | Verified |
| **M3-S03** | Automated PMT-06 cash challan calculation | `backend/app/modules/gstr3b/offset_engine.py` | `pmt06_cash_required` | `test_milestone3_gstr3b_offset_and_filing.py` | Verified |
| **M3-F04** | Maker-Checker review workflow state machine | `backend/app/modules/gstr3b/service.py` | `chk_gstr3b_filing_status` (`DRAFT`, `REVIEW`, `APPROVED`, `FILED`) | `test_milestone3_gstr3b_offset_and_filing.py` | Verified |
| **M3-F05** | GSP Sandbox Filing Adapter with ARN and CIN generation | `backend/app/modules/gstr3b/gsp_sandbox.py` | 15-char ARN, 17-char CIN, SHA-256 payload hash | `test_milestone3_gstr3b_offset_and_filing.py` | Verified |

---

## 4. Milestone 4: Credit Builder & Cash Flow Analytics

| PRD Req ID | Requirement Description | Implementation Location | Database / Schema | Automated Test Verification | Status |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **M4-F01** | Multi-bank & UPI CSV statement ingestion | `backend/app/modules/credit_builder/service.py` | `public.financial_transactions` | `test_milestone4_credit_builder.py` | Verified |
| **M4-F02** | Automated transaction categorization (8 business heads) | `backend/app/modules/credit_builder/categorizer.py` | `financial_transactions.category` | `test_milestone4_credit_builder.py` | Verified |
| **M4-F03** | Monthly cash flow trend analysis and margins | `backend/app/modules/credit_builder/service.py` | `get_financial_summary()` | `test_milestone4_credit_builder.py` | Verified |
| **M4-F04** | MSME Financial Readiness Index (scale 300 to 900) | `backend/app/modules/credit_builder/service.py` | `public.business_profiles.credit_score` | `test_milestone4_credit_builder.py` | Verified |
| **M4-F05** | Structured Loan Dossier generation | `backend/app/modules/credit_builder/service.py` | `LoanDossierOut` schema | `test_milestone4_credit_builder.py` | Verified |
| **M4-S01** | Statutory regulatory disclaimer on credit score | `backend/app/modules/credit_builder/service.py` | `CREDIT_DISCLAIMER` | `test_milestone4_credit_builder.py` | Verified |

---

## 5. Milestone 5: Government Scheme Navigator

| PRD Req ID | Requirement Description | Implementation Location | Database / Schema | Automated Test Verification | Status |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **M5-F01** | Central MSME Scheme Catalog (6 Indian Schemes) | `backend/app/modules/scheme_navigator/schemes_catalog.py` | `public.government_schemes` | `test_milestone5_scheme_navigator.py` | Verified |
| **M5-F02** | Deterministic multi-operator rule evaluation engine | `backend/app/modules/scheme_navigator/eligibility_engine.py` | `public.scheme_eligibility_rules` | `test_milestone5_scheme_navigator.py` | Verified |
| **M5-F03** | Tenant eligibility matching and scoring (0-100%) | `backend/app/modules/scheme_navigator/service.py` | `public.scheme_matches` | `test_milestone5_scheme_navigator.py` | Verified |
| **M5-F04** | Official application documentation checklist | `backend/app/modules/scheme_navigator/service.py` | `get_checklist_for_scheme()` | `test_milestone5_scheme_navigator.py` | Verified |
| **M5-F05** | MSME Profile attributes editor (Turnover, Sector, Udyam) | `backend/app/modules/scheme_navigator/api.py` | `public.business_profiles` | `test_milestone5_scheme_navigator.py` | Verified |

---

## 6. AI Financial Copilot & Intelligence Layer

| PRD Req ID | Requirement Description | Implementation Location | Database / Schema | Automated Test Verification | Status |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **AI-F01** | Tenant ledger grounding context synthesis | `backend/app/modules/ai_assistant/grounding_service.py` | `public.gstr3b_filings`, `public.purchase_documents`, `public.business_profiles` | `test_ai_assistant.py` | Verified |
| **AI-F02** | Proactive statutory alerts (Rule 37, 88A, Sec 16(4)) | `backend/app/modules/ai_assistant/grounding_service.py` | `generate_quick_insights()` | `test_ai_assistant.py` | Verified |
| **AI-F03** | Multi-turn conversational copilot with Gemini API integration | `backend/app/modules/ai_assistant/gemini_client.py` | Gemini 2.5 Flash / Fallback | `test_ai_assistant.py` | Verified |
| **AI-F04** | Clean deterministic GST knowledge fallback on API absence | `backend/app/modules/ai_assistant/gemini_client.py` | Deterministic GST Domain Engine | `test_ai_assistant.py` | Verified |

---

## 7. Frontend Integration & UI Completeness

| PRD Req ID | Screen Name | Component Location | Connected API Route | Status |
| :--- | :--- | :--- | :--- | :--- |
| **UI-01** | Outward Invoice Ingestion (M1) | `frontend/src/App.tsx` | `/api/v1/invoices` | Live |
| **UI-02** | GSTR-2B JSON Ingestion (M2) | `frontend/src/pages/Gstr2bUpload.tsx` | `/api/v1/reconciliation/gstr2b/snapshots/upload` | Live |
| **UI-03** | Purchase Register (M2) | `frontend/src/pages/PurchaseRegister.tsx` | `/api/v1/reconciliation/purchase/documents` | Live |
| **UI-04** | 4-Pass Reconciliation Workbench (M2) | `frontend/src/pages/ReconciliationWorkbench.tsx` | `/api/v1/reconciliation/run` | Live |
| **UI-05** | ITC Risk Analysis & Audit Drilldown (M2) | `frontend/src/pages/ItcRiskDashboard.tsx` | `/api/v1/reconciliation/gstr3b-table4-working-summary` | Live |
| **UI-06** | GSTR-3B & Rule 88A Offset Engine (M3) | `frontend/src/pages/Gstr3bFilingView.tsx` | `/api/v1/gstr3b/prepare`, `/review`, `/file-sandbox` | Live |
| **UI-07** | Credit Builder & Cash Flow Analytics (M4) | `frontend/src/pages/CreditBuilder.tsx` | `/api/v1/credit-builder/statements/upload`, `/summary`, `/readiness-score`, `/loan-dossier` | Live |
| **UI-08** | Government Scheme Navigator (M5) | `frontend/src/pages/SchemeNavigator.tsx` | `/api/v1/scheme-navigator/schemes`, `/evaluate`, `/profile`, `/checklist/{code}` | Live |
| **UI-09** | AI Financial Copilot Chat & Alerts (AI) | `frontend/src/pages/AiAdvisor.tsx` | `/api/v1/ai/chat`, `/api/v1/ai/quick-insights` | Live |
