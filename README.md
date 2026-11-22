# MyCA — Autonomous Accounting & GST Compliance Platform for Indian MSMEs

MyCA is a comprehensive, production-ready autonomous financial intelligence and GST compliance platform purpose-built for Indian Micro, Small, and Medium Enterprises (MSMEs). 

It combines deterministic statutory tax engines (CGST Act & Rules 2017), an automated 4-Pass reconciliation pipeline, an Electronic Credit Ledger offset engine (Rule 88A), an internal MSME Financial Readiness Index (300–900), a Government Scheme Navigator, and a multi-turn AI Financial Copilot grounded in tenant accounting data.

---

## 🚀 Key Modules & Product Capabilities

| Module | Core Functionality | Statutory Grounding | Status |
| :--- | :--- | :--- | :--- |
| **Outward Invoices (M1)** | Autonomous OCR ingestion, line-item arithmetic validation, and state machine lifecycle | Sections 31 & 37, GSTR-1 preparation | **Complete** |
| **Inward Statutory Engine (M2)** | GSTR-2B snapshot ingestion, purchase register, 4-Pass Reconciliation (Exact, Tolerance, Timing, Unmatched), and immutable event-based ITC ledger | Section 16(2)(b) receipt gate, Section 16(4) cut-off, Rule 37 vs 37A separation | **Complete** |
| **GSTR-3B & Offset Engine (M3)** | Table 3.1 & Table 4 auto-aggregation, Rule 88A electronic ledger offset (100% IGST exhaustion & zero CGST/SGST cross-offset), Maker-Checker workflow, and GSP Sandbox filing (ARN/CIN generation) | Sections 49/49A/49B, Rule 88A, Form GST PMT-06 | **Complete** |
| **Credit Builder (M4)** | Multi-bank/UPI statement ingestion, automated 8-head expense categorization, cash-flow margin trends, MSME Financial Readiness Index (300–900), and structured Loan Dossier generator | RBI Priority Sector Lending Guidelines | **Complete** |
| **Scheme Navigator (M5)** | Deterministic multi-operator matching engine for 6 central MSME schemes (PMMY MUDRA, CGTMSE, PMEGP, PM-SVANidhi, Stand-Up India, ZED) with documentation checklists and direct portal links | Ministry of MSME / DFS Schemes | **Complete** |
| **AI Financial Advisor** | Multi-turn Copilot grounded in real-time tenant GSTIN, tax liabilities, and credit readiness scores. Supports Gemini 2.5 Flash with automatic fallback to built-in deterministic GST domain rule engine | CGST Act 2017 Domain Rules | **Complete** |

---

## 🛡️ Security & Multi-Tenancy Architecture

- **PostgreSQL 16 Forced Row Level Security (RLS):** Every multi-tenant table enforces kernel-level RLS policies against unprivileged application user `app_service_user`.
- **JWT Cryptographic Authentication:** HS256 tokens encoding verified organization membership; decoded token claims strictly override user-supplied headers to prevent identity spoofing.
- **Audit Trails & Immutability:** SHA-256 cryptographic snapshot hashing on inward supply statements and tamper-evident payload hashing on GSTR-3B filings.

---

## 🧪 Quality Assurance & Test Verification

```text
======================= 253 passed in 36.85s =======================
Pass Rate: 100% (253 / 253 Backend Tests Passing)
Frontend Build: Zero TypeScript / Lint Errors (Vite production bundle: 420 kB)
```

- **M1 Tests:** Invoice OCR, line-item validation, multi-tenant security closure (42 tests).
- **M2 Tests:** GSTR-2B reconciliation, 10-queue exception management, Rule 37/37A separation, Section 16(2)(b) goods receipt gating (193 tests).
- **M3 Tests:** Table 3.1 & 4 aggregation, Rule 88A credit offset priority, Maker-Checker gate, GSP Sandbox filing (5 tests).
- **M4 Tests:** Statement CSV ingestion, auto-categorizer, DSCR proxy, Financial Readiness Index (5 tests).
- **M5 Tests:** Catalog seeding, multi-operator rule evaluation, scheme matching, checklist generation (5 tests).
- **AI Tests:** Tenant context grounding, proactive alerts, statutory distinction (3 tests).

---

## ⚡ Concurrency & Performance Benchmarks

Benchmark executed with `backend/tests/benchmark_load.py` under concurrent load:
- **Health Check (`GET /health`):** 100 reqs, 0 errors, Min: 7.1 ms, P50: 26.8 ms
- **Auth Token Issuance (`POST /auth/demo-token`):** 50 reqs, 0 errors, **168.7 RPS**, Avg: 57.1 ms
- **GST Registrations (`GET /reconciliation/gst-registrations`):** 50 reqs, 0 errors, **142.1 RPS**, Avg: 67.7 ms
- **ITC Ledger Summary (`GET /itc-ledger/summary`):** 50 reqs, 0 errors, **151.9 RPS**, Avg: 63.0 ms
- **Scheme Navigator Catalog (`GET /scheme-navigator/schemes`):** 50 reqs, 0 errors, **85.7 RPS**, Avg: 113.7 ms
- **Credit Builder Summary (`GET /credit-builder/summary`):** 50 reqs, 0 errors, **100.6 RPS**, Avg: 94.0 ms
- **AI Quick Insights (`GET /ai/quick-insights`):** 50 reqs, 0 errors, **82.3 RPS**, Avg: 117.9 ms

---

## 📦 Docker Deployment Quickstart

### Using Docker Compose
```bash
# 1. Clone repository
git clone https://github.com/SuhaibShaik2917/myca.git
cd myca

# 2. Configure production environment
cp .env.production.example .env.production
# (Edit .env.production with your secrets)

# 3. Build and launch all services (PostgreSQL 16, Backend API, Frontend Nginx)
docker compose --env-file .env.production up -d --build

# 4. Apply database migrations
docker compose exec backend alembic upgrade head

# 5. Verify service health
curl http://localhost:8000/health
```

---

## 📚 Complete Release Documentation

The full release documentation suite is available in `docs/release/`:
1. [Product Completion Report](file:///C:/Users/notyo/.gemini/antigravity/scratch/myca/docs/release/MYCA_FINAL_PRODUCT_COMPLETION_REPORT.md)
2. [PRD Requirement Matrix](file:///C:/Users/notyo/.gemini/antigravity/scratch/myca/docs/release/MYCA_FINAL_PRD_REQUIREMENT_MATRIX.md)
3. [Milestone Completion Report](file:///C:/Users/notyo/.gemini/antigravity/scratch/myca/docs/release/MYCA_MILESTONE_COMPLETION_REPORT.md)
4. [Final QA Report](file:///C:/Users/notyo/.gemini/antigravity/scratch/myca/docs/release/MYCA_FINAL_QA_REPORT.md)
5. [Security Audit Report](file:///C:/Users/notyo/.gemini/antigravity/scratch/myca/docs/release/MYCA_SECURITY_AUDIT.md)
6. [GST Statutory Verification Report](file:///C:/Users/notyo/.gemini/antigravity/scratch/myca/docs/release/MYCA_GST_STATUTORY_VERIFICATION.md)
7. [Performance & Benchmark Report](file:///C:/Users/notyo/.gemini/antigravity/scratch/myca/docs/release/MYCA_PERFORMANCE_REPORT.md)
8. [Production Deployment Guide](file:///C:/Users/notyo/.gemini/antigravity/scratch/myca/docs/release/MYCA_DEPLOYMENT_GUIDE.md)
9. [Production Runbook](file:///C:/Users/notyo/.gemini/antigravity/scratch/myca/docs/release/MYCA_PRODUCTION_RUNBOOK.md)
10. [Outstanding External Dependencies](file:///C:/Users/notyo/.gemini/antigravity/scratch/myca/docs/release/MYCA_OUTSTANDING_DEPENDENCIES.md)

---

## ⚖️ Statutory & Regulatory Notice

The MyCA MSME Financial Readiness Indicator is an internal, deterministic analytical metric calculated from cash-flow regularity, operating margins, and statutory GST compliance records. It is NOT an official credit bureau score (e.g., CIBIL, Equifax, Experian, CRIF High Mark) and does not constitute a loan guarantee. Final credit underwriting decisions rest exclusively with empaneled banks and financial institutions. Live GSTN filing operates through certified GST Suvidha Provider (GSP) sandbox channels.
