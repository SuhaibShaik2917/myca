# MyCA v1.0.13 — Security Verification Report

**Release Target:** `v1.0.13`  
**Parent Commit:** `a64eb6ba82b79c7b39de7eda7594bbbe25c62d2a` (v1.0.12 candidate)  
**Security Verification Scope:** Strict IdP verified email enforcement, single-use authentication nonces, PostgreSQL replay protection, disposable database rollback boundaries, staging environment configuration lockdown, and RLS tenant isolation.

---

## 1. Strict Federated IdP Email Verification & Identity Binding

- **Module:** `backend/app/modules/auth/idp_verifier.py`
- **Boolean Type Enforcement:**
  - `email_verified` claim is strictly validated using:
    ```python
    v = claims.get("email_verified")
    if not isinstance(v, bool) or v is not True:
        raise HTTPException(status_code=401, detail="UNVERIFIED_EMAIL")
    ```
  - Rejection includes: missing claim, `None`, Python `False`, integers `0` and `1`, and strings `"true"`, `"True"`, `"false"`, `"yes"`, `"1"`.
- **Email Format & Normalization:**
  - `email` claim must be a non-empty string containing `@`.
  - Missing, empty, or whitespace-only emails raise HTTP 401 `MISSING_EMAIL_CLAIM` or `INVALID_EMAIL_FORMAT`.
  - Elimination of `sub` fallback: Under no circumstances does the system fall back to using `sub` as an email identifier.
  - Normalization: Verified email is normalized via `.strip().lower()` prior to user lookup in `v_org_users`.
- **Provider-to-Issuer Binding:**
  - Provider identifier `google` enforces issuer domain `accounts.google.com` or `https://accounts.google.com`.
  - Mismatches raise HTTP 401 `ISSUER_PROVIDER_MISMATCH`.

---

## 2. Server-Tracked Single-Use IdP Authentication Nonces

- **Modules:**
  - `backend/app/modules/auth/nonce_service.py`
  - `backend/app/modules/auth/api.py`
  - `backend/alembic/versions/0027_idp_auth_nonces.py`
- **Nonce Lifecycle:**
  1. **Issuance (`POST /api/v1/auth/idp-nonce`):**
     - Generates 256-bit cryptographically secure URL-safe token: `secrets.token_urlsafe(32)`.
     - Inserts record into `public.idp_authentication_nonces`:
       - `nonce`: text PRIMARY KEY
       - `org_id`: uuid REFERENCES public.organizations(id) ON DELETE CASCADE
       - `issued_at`: timestamptz DEFAULT clock_timestamp()
       - `expires_at`: timestamptz (5-minute TTL)
       - `status`: text DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'CONSUMED'))
  2. **Exchange & Atomic Consumption (`POST /api/v1/auth/idp-exchange`):**
     - IdP token must contain a non-empty `nonce` claim matching the issued nonce.
     - Atomically marks nonce as `CONSUMED` within an active database transaction:
       ```sql
       UPDATE public.idp_authentication_nonces
       SET status = 'CONSUMED', consumed_at = clock_timestamp()
       WHERE nonce = %s AND org_id = %s AND status = 'ACTIVE' AND expires_at > clock_timestamp()
       RETURNING nonce;
       ```
  3. **Replay & Error Handling:**
     - Duplicate consumption attempt: Raises HTTP 401 `IDP_NONCE_REPLAY`.
     - Expired nonce: Raises HTTP 401 `IDP_NONCE_EXPIRED`.
     - Unrecognized nonce: Raises HTTP 401 `IDP_NONCE_INVALID`.
     - Missing or empty nonce claim: Raises HTTP 401 `MISSING_NONCE_CLAIM`.
     - Unreachable database: Fails closed with HTTP 503 `DATABASE_UNAVAILABLE`.
  4. **Persistence Across Migration Cycles:**
     - Nonces are stored in persistent PostgreSQL storage and survive migration downgrades to prevent replay attacks during system maintenance.

---

## 3. Hardened Migration Rollback Database Boundaries

- **Module:** `backend/scripts/verify_migration_rollback_state.py`
- **Function:** `assert_disposable_database_environment(db_url: str)`
- **Safety Enforcement Rules:**
  1. `ENVIRONMENT` must be explicitly configured as `"test"` or `"ci"`. Environments `"production"`, `"staging"`, `"development"`, unset, or empty immediately abort execution.
  2. `ALLOW_DISPOSABLE_DATABASE_ROLLBACK="true"` is mandatory.
  3. `DB_ADMIN_URL` must be explicitly provided with no persistent fallback.
  4. Database name parsed from URL must match disposable naming conventions:
     - Recognized disposable patterns: `myca_test`, `myca_test_*`, `*_test*`, `*_disposable*`.
     - Ordinary development database names, specifically `"myca"`, are strictly rejected even if `ALLOW_DISPOSABLE_DATABASE_ROLLBACK=true` is set.
  5. Active session verification: Executes `SELECT current_database();` against the connected PostgreSQL instance to verify that the active database matches the authorized disposable target.
- **Verification Suite:** 25 dedicated unit tests in `backend/tests/test_rollback_script_safety.py` validate every boundary condition.

---

## 4. Staging Configuration Lockdown

- **Module:** `backend/app/config.py`
- **Staging Equivalence with Production:**
  - `SECRET_KEY`:
    - Rejects default, short (<32 chars), or known insecure marker strings (`"dev-insecure"`, `"test_secret"`, `"test-secret"`, `"myca_jwt_test"`, `"default"`).
  - `DATABASE_URL` and `DATABASE_WORKER_URL`:
    - Rejects default password markers (`:secret@`).
    - Rejects localhost/loopback addresses (`localhost`, `127.0.0.1`).
  - `ALLOW_DEMO_TOKENS`:
    - Strictly prohibited in staging (`ALLOW_DEMO_TOKENS=False`).

---

## 5. PostgreSQL RLS & Tenant Isolation Audit

- **Script:** `backend/scripts/audit_rls_security_comprehensive.py`
- **Tenant Tables (All 36 Tables):**
  - Exactly 36 of 36 tenant tables enforce both `relrowsecurity=True` AND `relforcerowsecurity=True` (100%).
- **Reference Catalogues (9 Tables):**
  - Application runtime role `app_service_user` has strictly `SELECT` permissions.
  - `INSERT`, `UPDATE`, and `DELETE` queries are denied by PostgreSQL access controls.
- **Cross-Tenant Isolation:**
  - Read, Update, Delete, and Insert isolation verified across all 11 core tenant functional domains.
  - Cross-tenant INSERT attempts trigger PostgreSQL `42501: RLS_WITH_CHECK_VIOLATION`.
  - Null tenant context queries unconditionally return 0 rows.
