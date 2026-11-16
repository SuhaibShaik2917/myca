# MyCA — Autonomous Financial Companion for Indian MSMEs

MyCA is an enterprise-grade financial intelligence platform tailored for Indian Micro, Small, and Medium Enterprises (MSMEs). It automates invoice processing, monitors financial health and creditworthiness, matches businesses to government subsidy schemes, and provides an AI-powered conversational financial advisor.

## Architecture & Security Boundary
- **Multi-Tenancy:** Strict Row-Level Security (RLS) enforced at the PostgreSQL kernel level using `app.current_org` and `app.current_user`.
- **Modular Monolith:** Clean boundary separation across domain modules:
  - **Invoice Brain:** Ingestion, validation state-machine, OCR extraction, GST cross-verification, and export readiness.
  - **Credit Builder:** Financial health scoring, GST cash flow provenance, and credit readiness.
  - **Scheme Navigator:** Government scheme catalog, eligibility scoring, and application tracking.
  - **AI Assistant:** Google Gemini 2.5 integration with deterministic read-only tools and human approval gates for financial actions.
- **Worker Subsystem:** Distributed background task queue with 256-bit cryptographically secure database lease tokens, `clock_timestamp()` timing, and bounded auto-reaping of dead tasks.
- **Audit Logging:** Immutable append-only logging in dedicated `audit.audit_logs`.

## Repository Structure
```
myca/
├── backend/                  # FastAPI + SQLAlchemy + Alembic
│   ├── alembic/              # Ordered version-controlled migrations (0001 - 0007)
│   ├── app/                  # Application core, models, schemas, and API modules
│   ├── worker/               # Background task consumer (claim/complete/reap)
│   └── tests/                # Automated security, RLS, and tenant isolation tests
├── frontend/                 # React 19 + TypeScript + Vite + Tailwind CSS
└── README.md
```

## Security Invariants
1. Database migrations require verified superuser credentials; application connects as `app_service_user`; worker connects as `worker_service_user`.
2. Direct mutations to `worker_tasks` are strictly revoked from application and worker logins.
3. User directory (`global_users`, `v_org_users`) is completely inaccessible to background workers (`42501`).
4. Human approval (`owner` or `admin`) is required for invoice approval; amount modifications are blocked once an invoice reaches `export_ready`.
