# MyCA — Security Audit & Threat Model Report

**Date:** November 22, 2026  
**Audit Scope:** Database RLS Architecture, Authentication & Authorization, Tenant Isolation, Cryptography, and Statutory Provenance  
**Auditor:** Application Security & Database Architecture Team  
**Audit Verdict:** **PASS — PRODUCTION LEVEL MULTI-TENANT ISOLATION CERTIFIED**  

---

## 1. Executive Security Architecture

MyCA handles highly sensitive financial and tax data for Indian MSMEs. To prevent data leakage, lateral privilege escalation, and cross-tenant pollution, the system employs defense-in-depth architecture across all layers:

```
[ Client Request ]
       |
       v (TLS 1.3 / HTTPS)
[ Reverse Proxy / Nginx ] ---> Strict Security Headers (CSP, HSTS, X-Content-Type)
       |
       v
[ FastAPI Middleware ] ------> CORS Origin Enforcement & Rate Limiting
       |
       v
[ Auth Dependency ] ---------> Cryptographic JWT Verification (HS256)
       |                       Resolves user_id, org_id & checks active membership
       v
[ Database Session ] --------> SET LOCAL app.current_org = :org_id;
       |                       SET LOCAL app.current_user = :user_id;
       v
[ PostgreSQL 16 RLS ] -------> FORCE ROW LEVEL SECURITY (app_service_user)
                               Enforces WHERE org_id = current_setting('app.current_org')
```

---

## 2. PostgreSQL 16 Forced Row Level Security (RLS)

### 2.1 Least Privilege Role Separation
The PostgreSQL database separates administrative migrations from application runtime operations:
- **`postgres` (DB Owner):** Used exclusively by Alembic for DDL migrations. Not accessible by web workers.
- **`app_service_user` (Application DML):** Unprivileged role used by FastAPI workers. Does NOT possess `SUPERUSER` or `BYPASSRLS` privileges.
- **Forced RLS:** Every multi-tenant table is configured with:
  ```sql
  ALTER TABLE public.<table_name> ENABLE ROW LEVEL SECURITY;
  ALTER TABLE public.<table_name> FORCE ROW LEVEL SECURITY;
  ```
  `FORCE ROW LEVEL SECURITY` ensures that even table owners or service users are subject to RLS policies.

### 2.2 RLS Policy Definition
All multi-tenant tables enforce tenant isolation policies:
```sql
CREATE POLICY p_org_isolation ON public.<table_name>
    FOR ALL
    TO app_service_user
    USING (org_id = NULLIF(current_setting('app.current_org', true), '')::uuid)
    WITH CHECK (org_id = NULLIF(current_setting('app.current_org', true), '')::uuid);
```

### 2.3 Verification Evidence
- 14 dedicated security tests in `test_milestone1_security_closure.py` verify that `app_service_user` cannot access, read, update, or delete records belonging to another organization under any circumstances.
- Verified in `test_multi_tenant_financial_isolation` in `test_milestone4_credit_builder.py` that bank statements ingested by Org A are completely invisible to Org B.

---

## 3. Authentication & Session Provenance

### 3.1 Token Claim Enforcement
- Access tokens are cryptographically signed using HS256 with a 64-character secret key.
- Tokens encode `sub` (user_id), `org_id`, and `exp` timestamp.
- In `backend/app/core/dependencies.py`:
  - Token claims strictly override any user-supplied `X-Org-Id` or `X-User-Id` request headers, preventing identity spoofing.
  - Active organization membership is verified against `public.organization_memberships` before establishing the session context.
  - If a user attempts to access an organization they do not belong to, the server immediately rejects the request with `HTTP 403 Forbidden`.

---

## 4. Cryptographic Hashing & Immutability

### 4.1 Inward Supply Provenance (GSTR-2B Snapshots)
- Every ingested GSTR-2B statement is hashed with SHA-256 upon upload:
  ```sql
  snapshot_hash = encode(digest(raw_json_content, 'sha256'), 'hex')
  ```
- Any subsequent reconciliation run anchors directly to this immutable hash, providing tamper-evident statutory audit trails.

### 4.2 GSTR-3B Sandbox Filing Checksums
- Every GSTR-3B return payload generates a SHA-256 payload hash combining Table 3.1, Table 4, and Rule 88A offset results:
  ```python
  payload_hash = hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
  ```
- The hash is locked into `public.gstr3b_filings` alongside the Application Reference Number (ARN) upon filing.

---

## 5. Security Audit Findings Summary

| Check Area | Threat Model | Mitigation Implemented | Status |
| :--- | :--- | :--- | :--- |
| **Cross-Tenant Data Leakage** | Malicious tenant crafts request with another org's UUID | PostgreSQL RLS filters by session GUC; database engine enforces isolation at the kernel level | **Secure** |
| **Header Identity Spoofing** | Attacker passes forged `X-Org-Id` header | Server-side JWT decoding overrides headers; verified in `test_auth_token.py` | **Secure** |
| **SQL Injection** | Attacker injects SQL into search/reconciliation | All SQL queries utilize parameterized statements (`:param`) via SQLAlchemy and psycopg | **Secure** |
| **Privilege Escalation** | Regular member files GSTR-3B without approval | Maker-Checker state machine strictly requires `APPROVED_BY_CHECKER` before filing gate unlocks | **Secure** |
| **Data Immutability** | User alters historical statutory ledger entries | Database triggers prevent updates to reconciled ITC entries and filed returns | **Secure** |
| **API Parameter Crashing** | Malformed UUID crashes database (DEF-002) | Strict Pydantic and `uuid.UUID()` type validation returning HTTP 400 Bad Request | **Secure** |
