# MyCA v1.0.13 — Independent Audit & Remediation Verification

**Audited Release Target:** `v1.0.13`  
**Candidate Release Commit:** `a64eb6ba82b79c7b39de7eda7594bbbe25c62d2a` (v1.0.12 candidate)  
**Remediation Verdict:** **APPROVED FOR PRODUCTION WITH DISCLOSURES** (`APPROVED_WITH_DISCLOSURES`)  

---

## 1. Audit Finding Remediation Assessment

### Finding 1: Strict Boolean IdP Email Verification & Provider Binding
- **Previous Risk:** IdP verifier permitted potential type-coercion or unverified email spoofing; fallback to `sub` claim could allow unauthorized account association.
- **Remediation Implemented:**
  - `email_verified` claim must be strictly Python boolean `True`:
    `isinstance(v, bool) and v is True`. Non-boolean types (`"true"`, `1`, `0`, null) raise HTTP 401.
  - Requires non-empty string `email` claim containing `@`.
  - Fallback to `sub` is completely eliminated.
  - Email normalized via `.strip().lower()` prior to database query.
  - Provider `google` is strictly bound to issuer domain `accounts.google.com`.
- **Verdict:** **CLOSED & VERIFIED**.

### Finding 2: Single-Use IdP Authentication Nonces & Replay Defense
- **Previous Risk:** IdP identity assertions could be replayed if captured, lacking server-tracked single-use transaction validation.
- **Remediation Implemented:**
  - `POST /api/v1/auth/idp-nonce` generates unpredictable 256-bit nonces stored in `public.idp_authentication_nonces` with 5-minute TTL.
  - `POST /api/v1/auth/idp-exchange` requires a matching `nonce` claim in the verified token.
  - Atomically marks the nonce as `CONSUMED` within a database transaction.
  - Replayed nonces trigger HTTP 401 `IDP_NONCE_REPLAY`.
  - Additive migration `0027_idp_auth_nonces.py` provides persistent storage. Nonces survive migration downgrades and block replays upon re-upgrade.
- **Verdict:** **CLOSED & VERIFIED**.

### Finding 3: Rollback Script Destructive Safety & Database Isolation
- **Previous Risk:** Rollback script allowed running on development database `myca` if flags were set, and did not run in an isolated disposable database in CI.
- **Remediation Implemented:**
  - Hardened `assert_disposable_database_environment(db_url)` to require `ENVIRONMENT in ('test', 'ci')`, explicit `DB_ADMIN_URL`, and designated disposable naming markers.
  - Explicitly rejects database name `myca`.
  - Validates active database connection session name via `SELECT current_database();`.
  - Updated CI workflow to create disposable database `myca_test` via `CREATE DATABASE myca_test WITH TEMPLATE myca;` for rollback testing.
- **Verdict:** **CLOSED & VERIFIED**.

### Finding 4: Staging Configuration Hardening
- **Previous Risk:** Staging environment allowed insecure default keys and local database URLs.
- **Remediation Implemented:**
  - `SECRET_KEY`, `DATABASE_URL`, and `DATABASE_WORKER_URL` validators apply production-grade rules to `staging`.
  - Rejects default/insecure keys and localhost URLs.
  - Enforces `ALLOW_DEMO_TOKENS=False` in staging.
- **Verdict:** **CLOSED & VERIFIED**.

---

## 2. Invariant Verification Summary

| Audit Dimension | Test Execution / Method | Verdict |
|---|---|:---:|
| Full Backend Test Suite | `pytest tests/` (373 tests) | PASS (373/373) |
| Rollback Safety Suite | `pytest tests/test_rollback_script_safety.py` (25 tests) | PASS (25/25) |
| Auth & Nonce Suite | `pytest tests/test_auth_token.py` (55 tests) | PASS (55/55) |
| Migration Rollback Verification | `verify_migration_rollback_state.py` against `baseline_0019_reference.json` | PASS (100% Match) |
| Nonce Persistence on Rollback | Populated downgrade to 0025, re-upgrade, replay test | PASS |
| RLS & FORCE RLS Audit | 36 of 36 tenant tables FORCE RLS | PASS (36/36) |
| Frontend Build | `npm run build` | PASS |
