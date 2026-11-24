# MyCA v1.0.14 Independent Audit

**Release Target:** `v1.0.14`  
**Auditor Role:** Independent Statutory & Release Security Auditor  
**Audit Decision:** **APPROVED AS REVISED RELEASE CANDIDATE (PRODUCTION SIGN-OFF WITHHELD / NOT PRODUCTION APPROVED FOR UNRESTRICTED PUBLIC USE UNTIL EXTERNAL PRODUCTION JWKS & ICAI GATEWAY INTEGRATIONS ARE OPERATIONAL)**  

---

## 1. Audit Scope & Verification Objective

The objective of the v1.0.14 independent audit is to evaluate the remediation of findings identified during the review of v1.0.13:

1. **IdP Nonce Multi-Tenancy:** Verify that `public.idp_authentication_nonces` is strictly governed by PostgreSQL Row Level Security (`RLS`) and `FORCE ROW LEVEL SECURITY`, preventing cross-tenant leakage.
2. **Browser Session & PKCE Binding:** Verify that authentication transactions are cryptographically bound to the initiating browser client, preventing cross-browser authorization code reuse, CSRF, and session fixation.
3. **Abuse Protection:** Verify rate limiting and active nonce limits on `POST /api/v1/auth/idp-nonce`, as well as automatic safe cleanup of expired active nonces.
4. **Canonical Identity Provider Binding:** Verify that provider trust matches exact canonical issuer sets rather than permissive substring matching.
5. **Rollback Safety & Database Disposability:** Verify that migration rollback execution requires explicit database-level disposable markers and that Alembic and psycopg target the exact same database.
6. **Test Harness & Concurrency Races:** Verify that test fixtures cleanly detect errors and that concurrent requests against the same nonce produce exactly 1 success and 4 rejections.

---

## 2. Findings & Verification Analysis

### Finding 1: Database-Enforced Tenant Isolation for Nonces
- **Analysis:** In `v1.0.13`, `idp_authentication_nonces` lacked RLS. In `v1.0.14`, additive migration `0028_idp_nonces_rls.py` enabled RLS and `FORCE ROW LEVEL SECURITY`, establishing policy `idp_auth_nonces_service_policy`.
- **Evidence:** Comprehensive RLS audit dynamically discovered 37 tenant tables, confirming `RLS=True` and `FORCE_RLS=True` on `idp_authentication_nonces`. Cross-tenant SELECT, UPDATE, DELETE, and INSERT attempts under `app_service_user` were 100% blocked.
- **Verdict:** **CLOSED (VERIFIED)**.

### Finding 2: Browser Session Binding & Anti-CSRF
- **Analysis:** Authentication nonces could previously be consumed from any browser or client that intercepted the authorization code. In `v1.0.14`, nonces are tied to a cryptographic session token stored as a SHA-256 digest in `session_binding_hash`, transmitted via `myca_auth_session` HttpOnly cookie. Optional PKCE S256 verification was implemented and tested.
- **Evidence:** Tested in `test_browser_session_binding_and_anti_csrf` and `test_pkce_s256_verification`. Requests with tampered session tokens, mismatched verifiers, or missing cookies are rejected with HTTP 401.
- **Verdict:** **CLOSED (VERIFIED)**.

### Finding 3: Nonce Endpoint Abuse Controls & Safe Pruning
- **Analysis:** Nonce issuance was previously unthrottled and table rows could grow unbounded. In `v1.0.14`, `NonceRateLimiter` enforces 20 req/min per IP and 30 req/min per org. An active nonce cap of 5 per org is enforced. Safe pruning removes expired unconsumed nonces while strictly preserving consumed nonces.
- **Evidence:** Tested in `test_nonce_issuance_rate_limiting_abuse_controls`, `test_active_nonce_cap_per_org`, and `test_expired_nonce_safe_cleanup`.
- **Verdict:** **CLOSED (VERIFIED)**.

### Finding 4: Exact Canonical Provider Trust Configuration
- **Analysis:** Substring matching on provider issuers created spoofing risks. In `v1.0.14`, `PROVIDER_CANONICAL_ISSUERS` enforces exact canonical sets (`https://accounts.google.com`, `accounts.google.com`, etc.).
- **Evidence:** Tested in `test_exact_canonical_provider_issuer_matching`. Substring lookalikes (`https://accounts.google.com.attacker.com`) are rejected with HTTP 401 `ISSUER_PROVIDER_MISMATCH`.
- **Verdict:** **CLOSED (VERIFIED)**.

### Finding 5: Rollback Script Database Disposability Marker
- **Analysis:** In previous releases, rollback scripts checked only connection strings, which could be misdirected or spoofed. In `v1.0.14`, `public.disposable_database_marker` table with token `MYCA_VERIFIED_DISPOSABLE_TEST_DATABASE_TOKEN_V1_0_14` must exist inside the target database.
- **Evidence:** Tested across 31 unit tests in `test_rollback_script_safety.py` and demonstrated on disposable database `myca_test`.
- **Verdict:** **CLOSED (VERIFIED)**.

### Finding 6: Concurrency Race Condition Verification
- **Analysis:** Concurrency safety on nonce consumption required multithreaded proof. In `v1.0.14`, 5 threads simultaneously consumed the same nonce.
- **Evidence:** Tested in `test_multithreaded_concurrent_race_against_same_nonce`. Exactly 1 thread succeeded; 4 threads failed with HTTP 401 `IDP_NONCE_REPLAY`.
- **Verdict:** **CLOSED (VERIFIED)**.

---

## 3. Disclosures & Release Recommendation

1. **Production Sign-Off Withheld:**
   - Automated ICAI credential verification and federated IdP verification currently operate in test/staging modes with mock anchors or manual document review paths.
   - Live external integrations with the statutory ICAI portal and external vendor JWKS endpoints must be completed and validated in a production network environment before full statutory production sign-off can be granted.
   - Consequently, the release status is formally designated:
     **APPROVED AS REVISED RELEASE CANDIDATE (PRODUCTION SIGN-OFF WITHHELD)**.

2. **Governance Transparency:**
   - Commits and tags are unsigned.
   - Branch protection is disabled on the repository.
   - Historical tags `v1.0.0` through `v1.0.13` remain unmodified.
