# MyCA — Production Operations Runbook

**Audience:** Site Reliability Engineers (SRE), DevOps, and On-Call Engineers  
**System:** MyCA Production Infrastructure  
**Severity Levels:** P1 (Outage / Cross-Tenant Risk), P2 (Degraded Statutory Calculation), P3 (Minor Glitch)  

---

## 1. Routine Operational Commands

### 1.1 Checking Container Health & Logs
```bash
# View running status
docker compose ps

# Tail backend API logs
docker compose logs -f backend --tail=100

# Tail PostgreSQL logs
docker compose logs -f db --tail=100
```

### 1.2 Database Backup & Recovery

#### Scheduled Automated Backup (Nightly)
```bash
# Export full transactional dump with schemas and data
docker compose exec db pg_dump -U postgres -d myca -F c -b -v -f /var/lib/postgresql/data/myca_backup_$(date +%Y%m%d).dump

# Encrypt and stream to offsite S3 bucket
aws s3 cp /var/lib/postgresql/data/myca_backup_*.dump s3://myca-backups-secure/db/ --sse aws:kms
```

#### Point-in-Time Restore
```bash
# Terminate existing connections
docker compose exec db psql -U postgres -d myca -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = 'myca' AND pid <> pg_backend_pid();"

# Restore from binary dump
docker compose exec -T db pg_restore -U postgres -d myca -v /var/lib/postgresql/data/myca_backup_20261122.dump
```

---

## 2. Database Migration Workflow (Zero-Downtime)

1. Review migration script locally:
   ```bash
   alembic check
   ```
2. Take a snapshot backup of the production database before applying DDL.
3. Apply migration:
   ```bash
   docker compose exec backend alembic upgrade head
   ```
4. Verify migration head matches Alembic version table:
   ```bash
   docker compose exec db psql -U postgres -d myca -c "SELECT version_num FROM alembic_version;"
   # Expected head: 0020_m4_m5_grants_and_policies
   ```

---

## 3. Incident Response Playbooks

### Incident 1: Cross-Tenant Data Access Attempt (P1)
- **Symptom:** Application logs show repeated `HTTP 403 Forbidden: User does not possess active membership`.
- **Diagnosis:** Possible malicious attempt to access resources across organizations or compromised user token.
- **Action:**
  1. Inspect the source IP and user ID from Uvicorn access logs.
  2. Invalidate the user session by updating `public.global_users.is_active = false`.
  3. Verify RLS held firm:
     ```sql
     SELECT * FROM audit.audit_logs WHERE action_status = 'DENIED' ORDER BY created_at DESC LIMIT 50;
     ```

### Incident 2: High Latency on ITC Ledger Aggregations (P2)
- **Symptom:** Endpoint `/api/v1/reconciliation/itc-ledger/summary` exceeds 500ms P95 latency.
- **Diagnosis:** Table bloat on `itc_ledger_entries` or missing index scan.
- **Action:**
  1. Run `EXPLAIN ANALYZE` on the summary query:
     ```sql
     EXPLAIN ANALYZE SELECT event_type, SUM(cgst) FROM public.itc_ledger_entries WHERE org_id = '...' GROUP BY event_type;
     ```
  2. If sequential scan is observed, reindex the table:
     ```sql
     REINDEX TABLE CONCURRENTLY public.itc_ledger_entries;
     VACUUM ANALYZE public.itc_ledger_entries;
     ```

### Incident 3: GSP Sandbox Filing Rejection (P3)
- **Symptom:** GSTR-3B filing fails with error `GSTN_GSP_VALIDATION_ERROR`.
- **Diagnosis:** Taxable + Tax does not match Table 3.1 totals or maker-checker approval was skipped.
- **Action:**
  1. Verify the filing status is `APPROVED_BY_CHECKER`.
  2. Check that `maker_id` and `checker_id` are distinct UUIDs (four-eyes principle).
  3. Inspect `public.gstr3b_filings.payload_hash` against the payload dictionary.
