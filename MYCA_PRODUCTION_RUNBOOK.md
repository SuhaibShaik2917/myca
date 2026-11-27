# MyCA Production Operations Runbook

## 1. Prerequisites & Security Controls

Before deploying MyCA into `staging` or `production`:

1. **PostgreSQL 16 Role Separation (`migrations` + `runtime`):**
   - Run `alembic upgrade head` using privileged `MIGRATION_DATABASE_URL` (`postgres` / DBA role).
   - Migration `0001` creates login roles without passwords; you **must** provision strong credentials on `app_service_user`, `worker_service_user`, `worker_service_user_1`, and `worker_service_user_2` before starting application containers.
   - Keep PostgreSQL unexposed outside `myca_net` (`5432` is intentionally not published on host interfaces in `docker-compose.yml`).
2. **Environment Fail-Closed Configuration (`app/config.py`):**
   - Set `ENVIRONMENT=production` (or `staging`).
   - Set `ALLOW_DEMO_TOKENS=false` and `ALLOW_DEV_HEADER_AUTH=false`.
   - Set distinct >=32-character secrets for `SECRET_KEY`, `GSTR3B_REVIEW_ATTESTATION_KEY`, and `STATUTORY_AUDIT_SIGNING_KEY`.
   - Terminate TLS at your upstream load balancer / reverse proxy (Nginx serves HTTP inside Docker on port `80`), set explicit `https://` `CORS_ORIGINS`, and include the proxy CIDR in `TRUSTED_PROXIES`.
3. **Federated Identity (OIDC Authorization Code + PKCE):**
   - Configure `MYCA_IDP_PROVIDER`, `MYCA_IDP_ISSUER`, `MYCA_IDP_AUDIENCE`, `MYCA_IDP_JWKS_URL` (`https://`), `MYCA_IDP_TOKEN_URL` (`https://`), `MYCA_IDP_CLIENT_SECRET`, and `MYCA_IDP_REDIRECT_URIS`.
   - Only asymmetric algorithms (`RS256`–`RS512`, `ES256`–`ES512`) are permitted.

## 2. Deployment Sequence

```bash
# 1. Populate secrets from your vault into .env.production (ignored by git)
cp .env.production.example .env.production

# 2. Bring up database and apply schema migrations
docker compose --env-file .env.production up -d db
docker compose --env-file .env.production run --rm backend alembic upgrade head

# 3. Provision least-privilege PostgreSQL runtime role passwords (matching .env.production)
docker compose --env-file .env.production exec db psql -U postgres -d myca -c "ALTER ROLE app_service_user WITH PASSWORD '<APP_PWD>'; ALTER ROLE worker_service_user WITH PASSWORD '<WORKER_PWD>'; ALTER ROLE worker_service_user_1 WITH PASSWORD '<WORKER_PWD>'; ALTER ROLE worker_service_user_2 WITH PASSWORD '<WORKER_PWD>';"

# 4. Start backend API, background worker, and Nginx frontend
docker compose --env-file .env.production up -d --build backend worker frontend

# 5. Verify health & readiness
curl -f http://localhost:8000/health
curl -f http://localhost:8000/ready
```

## 3. Operational Verification Checklist

- **RLS Enforcement (Isolated Copy Only):** Execute `python scripts/audit_rls_security_comprehensive.py` against an **isolated/ephemeral staging database clone** after migrations (`40/40` tenant tables must report `RLS + FORCE RLS` enabled). Do not run it directly against live production data as the harness creates synthetic test tenant rows.
- **Backup & Restore:** Take `pg_dump` before schema upgrades and test PITR recovery cleanly on an isolated staging DB (never execute destructive rollbacks on live primary databases).
- **Storage Hygiene:** For multi-replica deployments, set `STORAGE_BACKEND=s3`, configure KMS encryption (`AWS_S3_KMS_KEY_ID`), and run orphan reconciliation periodically.
- **External Integration Boundaries:** Keep `GSP_MODE=sandbox` unless licensed GSP transport credentials and network egress policies are provisioned.
