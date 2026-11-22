# MyCA — Financial Companion for Indian MSMEs

MyCA is a GST-focused reconciliation prototype for Indian MSMEs. The backend provides invoice ingestion/validation and GSTR-2B/purchase reconciliation APIs backed by PostgreSQL row-level security. The frontend is a React + Vite demo/preview: the GSTR-2B upload screen calls the backend, while several other screens still use sample data.

## Current status

Implemented:

- FastAPI backend with Invoice Brain and Reconciliation routers.
- PostgreSQL + Alembic migrations (`0001`–`0018`).
- Tenant membership checks and PostgreSQL row-level security context.
- GSTR-2B upload/recompute API used by the frontend upload screen.
- Frontend production build with TypeScript, Tailwind CSS, and PostCSS.

Not yet production-ready:

- Authentication is still development identity headers (`X-User-Id`, `X-Org-Id`). Add verified login/session/token auth before exposing the API.
- Purchase Register, Reconciliation Workbench, ITC Risk dashboard, Credit Builder, Scheme Navigator, and AI Advisor screens still use sample/demo data.
- Full backend tests require a disposable PostgreSQL database with the expected local users/roles.

## Repository structure

```text
myca/
├── backend/
│   ├── alembic/              # 18 ordered migrations (0001-0018)
│   ├── app/                  # FastAPI app, config, database, modules
│   ├── worker/               # Background task consumer
│   ├── scripts/              # Operational smoke-test scripts
│   └── tests/                # Backend test suite (228 tests collected)
├── frontend/
│   ├── src/                  # React + TypeScript app
│   ├── package-lock.json     # Locked frontend dependencies
│   └── vite.config.ts        # Dev server with /api proxy to localhost:8000
└── README.md
```

## Local setup

### Backend

Requirements: Python 3.11+, PostgreSQL 16/17.

```bash
cd backend
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # then edit database URLs and secrets
alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

Health check:

```bash
curl http://localhost:8000/health
```

### Frontend

Requirements: Node.js 20+ and npm.

```bash
cd frontend
npm ci
cp .env.example .env.local       # optional; defaults use the Vite /api proxy
npm run dev
npm run build
```

The dev server runs on `http://localhost:5173` and proxies `/api` to `http://localhost:8000`.

## Environment variables

Backend (see `backend/.env.example`):

- `DATABASE_URL`
- `DATABASE_WORKER_URL`
- `SECRET_KEY`
- `ENVIRONMENT`
- `CORS_ORIGINS`
- `GEMINI_API_KEY`

Frontend (see `frontend/.env.example`):

- `VITE_API_BASE_URL` — optional. Leave empty to use same-origin `/api` (Vite proxy in dev). Set to `http://localhost:8000` for a separately hosted backend.
- `VITE_REGISTRATION_ID`, `VITE_USER_ID`, `VITE_ORG_ID` — development identity values for the GSTR-2B upload screen until real auth is added.

## Tests

Collect tests without a database:

```bash
cd backend
python -m pytest --collect-only -q
```

Run the full suite against a disposable PostgreSQL database configured for the test users/roles:

```bash
cd backend
alembic upgrade head
python -m pytest -q
```

CI runs the frontend build, backend compile/import checks, and test collection. The full database-backed suite is not run in CI yet.

## Security notes

- Do not deploy with development secrets or default database credentials.
- Set `ENVIRONMENT=production` with a strong `SECRET_KEY` and non-default database URLs.
- Replace development identity headers with verified authentication before production use.
- Keep application and worker database logins least-privilege; migrations should use an admin/superuser connection.
