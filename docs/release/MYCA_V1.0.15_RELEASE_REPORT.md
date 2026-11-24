# MyCA v1.0.15 Release Report

**Release Tag:** `v1.0.15`  
**Target Repository:** `SuhaibShaik2917/myca`  
**Parent Release:** `v1.0.14` candidate commit (`eacdc2bdc10813bf2046bba8f5f1b8817be2afb8`)  
**Release Type:** Focused Authentication & Nonce-Security Hardening: Mandatory PKCE & State Verification, HttpOnly-Cookie-Only Session Binding, Constant-Time Comparison, Distributed PostgreSQL Rate Limiting, Advisory-Locked Active Nonce Quotas, Bounded Replay Retention  
**Release Verdict:** **APPROVED AS REVISED RELEASE CANDIDATE (PRODUCTION SIGN-OFF WITHHELD / NOT PRODUCTION APPROVED FOR UNRESTRICTED PUBLIC USE UNTIL EXTERNAL PRODUCTION JWKS & ICAI GATEWAY INTEGRATIONS ARE OPERATIONAL)**  

---

## 1. Executive Summary

MyCA `v1.0.15` delivers an exhaustive, targeted security remediation addressing the authentication and nonce-security findings identified during the independent review of `v1.0.14`. This release hardens the federated identity authentication boundary, eliminates race conditions in quota management, establishes shared-database rate limiting across distributed workers, and bounds long-term database storage while maintaining strict 24-hour cryptographic replay protection:

1. **Mandatory PKCE & State Verification:**
   - `state` (16–128 characters) and PKCE S256 `code_challenge` (43–128 characters, base64url-encoded) are now mandatory on `POST /api/v1/auth/idp-nonce`. Requests omitting either or submitting malformed inputs are rejected with HTTP 400 or HTTP 422.
   - `POST /api/v1/auth/idp-exchange` requires the matching `state` and `code_verifier`, verifying SHA-256 digests in constant time before token consumption.

2. **HttpOnly-Cookie-Only Session Binding for Browser Authentication:**
   - Stripped `session_token` exposure entirely from `IdpNonceResponse` JSON body. JavaScript in browser clients cannot access the raw session token.
   - `POST /api/v1/auth/idp-exchange` resolves the session token strictly from the `myca_auth_session` HttpOnly cookie (`HttpOnly; SameSite=Lax; Secure`).
   - Removed header (`X-Session-Token`, `X-Auth-Session-Id`) and JSON body fallbacks. Missing cookie requests are rejected with HTTP 401 `MISSING_SESSION_COOKIE`.

3. **Constant-Time Comparison Throughout Authentication:**
   - All cryptographic comparisons use Python standard library `hmac.compare_digest()`:
     - PKCE S256 code challenge verification
     - State parameter equality check
     - Browser session token hash verification
   - Eliminates timing side-channel attacks across token exchange flows.

4. **Distributed Rate Limiting via PostgreSQL (`public.auth_rate_limits`):**
   - Migration `0029_auth_rate_limits.py` creates table `public.auth_rate_limits` with compound indexes on `idp_authentication_nonces`.
   - Enforces sliding-window rate limits (20 req/min per IP, 30 req/min per Org) via atomic PostgreSQL upsert (`INSERT ... ON CONFLICT (rate_key) DO UPDATE ... RETURNING request_count`).
   - Eliminates per-process in-memory limits; rate limits are enforced consistently across multi-worker backend deployments.

5. **Atomic Active Nonce Quota under PostgreSQL Advisory Locks:**
   - Active nonce quota check (max 5 active nonces per organization) and nonce insertion are wrapped in a single database transaction guarded by `SELECT pg_advisory_xact_lock(hashtext(:org_id))`.
   - Eliminates count-then-insert race conditions between concurrent worker threads or processes.

6. **Bounded Cleanup & 24-Hour Replay Retention:**
   - Consumed nonces within 24 hours (`consumed_at >= NOW() - INTERVAL '24 hours'`) are strictly preserved to maintain cryptographic replay defense during any identity token validity window.
   - Older consumed nonces (`consumed_at < NOW() - INTERVAL '24 hours'`) and expired active nonces are pruned automatically, preventing unbounded table growth.

7. **Accurate Flow Architecture Documentation:**
   - Accurately characterized as **MyCA Federated IdP Token Verification with Browser PKCE & Session Binding**, rather than a full OAuth authorization-code server.
   - Automated ICAI verification remains disabled and fail-closed.

---

## 2. Test & Verification Summary

| Verification Gate | Requirement | Actual Result | Status |
|---|---|---|---|
| **Backend Test Suite** | All unit, integration, and security tests pass | **393 / 393 passed** (0 failures, 1 warning) | **PASS** |
| **Rollback Safety Suite** | Disposable marker & connection verification | **31 / 31 passed** | **PASS** |
| **RLS Security Audit** | Dynamic discovery of all tenant tables; RLS & FORCE RLS | **37 / 37 tables** (100% RLS=True, 100% FORCE_RLS=True) | **PASS** |
| **Cross-Tenant Isolation** | Read, UPDATE, DELETE, INSERT spoofing across 12 domains | 100% blocked under `app_service_user` | **PASS** |
| **Catalogue Immutability** | Reference & system tables write revocation | INSERT/UPDATE/DELETE denied | **PASS** |
| **Migration Rollback State** | Downgrade to 0019 against independent baseline | 79 grants, 87 policies, 41 tables match 100% | **PASS** |
| **Populated Rollback** | Preserves evidence, audit events, gateway nonces & IdP nonces | Verified across downgrade and re-upgrade cycles | **PASS** |
| **Distributed Rate Limiter** | Shared PostgreSQL rate limit enforcement | 20/min IP, 30/min Org atomic enforcement | **PASS** |
| **Atomic Nonce Quota** | Advisory-locked max 5 active nonces per org | Serialized across concurrent workers | **PASS** |
| **Frontend Production Build** | TypeScript typecheck and Vite bundle | 1583 modules compiled cleanly in 2.13s | **PASS** |

---

## 3. Disclosures & Release Governance

1. **Production Sign-Off Withholding:**
   - Automated ICAI verification and live external IdP token verification require live operational integration with statutory portals and external vendor JWKS endpoints.
   - In accordance with independent audit guidelines, this release is classified as **APPROVED AS REVISED RELEASE CANDIDATE**. Production sign-off is withheld until live external JWKS and ICAI gateway endpoints are operational in production.

2. **Git Commit & Tag Signing:**
   - Commits and tags in this repository are currently unsigned. Repository branch protection rules are not enabled on the GitHub repository.

3. **Release Governance:**
   - Historical tags `v1.0.0` through `v1.0.14` remain permanently untouched.
   - The annotated tag `v1.0.15` is created and pushed strictly after branch CI succeeds on both `master` and `remediation/audit-findings-phase3`.
