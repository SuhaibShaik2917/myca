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
| **AI Financial Advisor** | Multi-turn Copilot grounded in real-time tenant GSTIN, tax liabilities, and credit readiness scores. Uses Gemini (`gemini-3.8-flash` / fallback `gemini-3.5-flash-lite`) with automatic fallback to built-in deterministic GST domain rule engine | CGST Act 2017 Domain Rules | **Complete** |

---

## 🛡️ Security & Multi-Tenancy Architecture

- **PostgreSQL 16 Forced Row Level Security (RLS):** Every multi-tenant table (`40/40`) enforces kernel-level RLS policies against unprivileged application user `app_service_user`.
- **JWT Cryptographic Authentication & OIDC PKCE:** RS256/ES256 OIDC Authorization Code + PKCE (`auth/idp-code-exchange`), server-side `jti` token revocation (`auth/logout`), and verified membership context (`auth/me`).
- **Audit Trails & Immutability:** SHA-256 cryptographic snapshot hashing on inward supply statements and HMAC-attested payload hashing on GSTR-3B filings.

---

## 🧪 Verification Commands

```bash
# Backend (clean PostgreSQL 16 database)
cd backend
alembic upgrade head
python scripts/diagnose_ci_database.py
python -m pytest tests/ -v --tb=short
python scripts/audit_rls_security_comprehensive.py

# Frontend
cd frontend
npx tsc --noEmit && npm test && npm run build
```

---

## 📦 Docker Deployment Quickstart

See [MYCA_PRODUCTION_RUNBOOK.md](file:///C:/Users/notyo/.gemini/antigravity/scratch/myca/MYCA_PRODUCTION_RUNBOOK.md) and `docs/release/` for complete operational procedures.

```bash
# 1. Configure production environment
cp .env.production.example .env.production
# (Fill POSTGRES_*, DATABASE_*, SECRET_KEY, GSTR3B_REVIEW_ATTESTATION_KEY, OIDC & GEMINI keys)

# 2. Initialize PostgreSQL & apply schema migrations
docker compose --env-file .env.production up -d db
docker compose --env-file .env.production run --rm backend alembic upgrade head

# 3. Launch all services (PostgreSQL 16, Backend API, Background Worker, Frontend Nginx)
docker compose --env-file .env.production up -d --build backend worker frontend

# 4. Verify service health
curl -f http://localhost:8000/health
curl -f http://localhost:8000/ready
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

The MyCA MSME Financial Readiness Indicator is an internal, deterministic analytical metric calculated from cash-flow regularity, operating margins, and statutory GST compliance records. It is NOT an official credit bureau score (e.g., CIBIL, Equifax, Experian, CRIF High Mark) and does not constitute a loan guarantee. Final credit underwriting decisions rest exclusively with empaneled banks and financial institutions. GSTN filing in this repository operates in GSP sandbox mode (`GSP_MODE=sandbox`); `GSP_MODE=live` fails closed unless licensed GST Suvidha Provider (GSP) credentials and live transport integration are provisioned.
