# MyCA v1.0.16 Release Report

**Release Tag:** `v1.0.16`  
**Target Repository:** `SuhaibShaik2917/myca`  
**Parent Release:** `v1.0.15` candidate commit (`32fd86881737811b2537dfde0a0f49d7048dfed2`)  
**Release Type:** Post-v1.0.15 Security Audit Remediation: PKCE code_verifier Length & Character Set Validation, True Rolling 60-Second Distributed Sliding Window Rate Limiting, Bounded Retention & Non-Blocking Cleanup for Rate Limit Storage, Multi-Session & Concurrent Thread Invariance, Trusted Proxy Client IP Extraction, Sanitized Exception Handling  
**Release Verdict:** **APPROVED AS REVISED RELEASE CANDIDATE (PRODUCTION SIGN-OFF WITHHELD / NOT PRODUCTION APPROVED FOR UNRESTRICTED PUBLIC USE UNTIL EXTERNAL PRODUCTION JWKS & ICAI GATEWAY INTEGRATIONS ARE OPERATIONAL)**  

---

## 1. Executive Summary

MyCA `v1.0.16` delivers a thorough, targeted remediation addressing all findings raised during the independent audit of `v1.0.15`. This release closes boundary burst vulnerabilities in rate limiting, adds rigorous RFC 7636 PKCE verifier validation, guarantees non-blocking bounded storage retention, and hardens client IP identity extraction against spoofing:

1. **RFC 7636 PKCE `code_verifier` Validation (Finding 1):**
   - Enforced strict length constraints (43 to 128 characters) and character set restrictions (`^[A-Za-z0-9_.~-]{43,128}$`) on `code_verifier`.
   - Validated both at the Pydantic schema layer (`IdpTokenExchangeRequest`) and in the core service layer (`consume_idp_login_nonce`).
   - Short (<43 chars), long (>128 chars), and invalid character verifiers are rejected with HTTP 400 or HTTP 422.

2. **Genuine Rolling 60-Second Sliding Window Distributed Rate Limiter (Finding 2):**
   - Replaced the calendar-minute fixed window with an atomic PostgreSQL sliding window tracking exact request timestamps.
   - Tested immediately before and after minute boundaries (e.g., 20 requests at 10:00:59 followed by a 21st request at 10:01:00.500); boundary burst bypasses are mathematically impossible and rejected with HTTP 429.
   - Operates across multi-worker deployments, independent database sessions, and concurrent application processes.

3. **Bounded Retention & Non-Blocking Cleanup for `auth_rate_limits` (Finding 3):**
   - Additive migration `0030_auth_rate_limits_rolling.py` provisioned timestamp array tracking and indexed `updated_at`.
   - Implemented `cleanup_expired_auth_rate_limits(db, retention_hours=1)`, safely deleting inactive records older than 1 hour.
   - Because active rate limiting operates strictly on a rolling 60-second window, pruning records older than 1 hour can never interfere with active rate limit enforcement.

4. **Multi-Session and Concurrent Thread Verification:**
   - Proved rate limiting is shared across independent database sessions (alternating requests between separate `SessionLocal` instances).
   - Proved concurrency safety under multithreaded load (25 concurrent worker threads hit the rate limiter simultaneously; exactly 20 succeed and 5 are throttled).

5. **Hardened Client IP Extraction & Reverse Proxy Defense:**
   - Implemented `extract_client_ip(request)` utilizing `settings.TRUSTED_PROXIES`.
   - Direct connections from untrusted peers ignore spoofed `X-Forwarded-For` headers.
   - Forwarded IPs from authorized reverse proxies are validated against RFC IPv4/IPv6 address syntax, neutralizing header injection attacks.

6. **Sanitized Exception Handling & Transaction Boundaries:**
   - Replaced raw exception exposures with sanitized HTTP 503 / HTTP 500 error messages.
   - Eliminated broad exception swallowing in user identity resolution.

---

## 2. Test & Verification Summary

| Verification Gate | Requirement | Actual Result | Status |
|---|---|---|---|
| **Backend Test Suite** | All unit, integration, and security tests pass | **399 / 399 passed** (0 failures, 1 warning) | **PASS** |
| **Rollback Safety Suite** | Disposable marker & connection verification | **31 / 31 passed** | **PASS** |
| **RLS Security Audit** | Dynamic discovery of all tenant tables; RLS & FORCE RLS | **37 / 37 tables** (100% RLS=True, 100% FORCE_RLS=True) | **PASS** |
| **Cross-Tenant Isolation** | Read, UPDATE, DELETE, INSERT spoofing across 12 domains | 100% blocked under `app_service_user` | **PASS** |
| **Catalogue Immutability** | Reference & system tables write revocation | INSERT/UPDATE/DELETE denied | **PASS** |
| **Migration Rollback State** | Downgrade to 0019 against independent baseline | 79 grants, 87 policies, 41 tables match 100% | **PASS** |
| **Populated Rollback** | Preserves evidence, audit events, gateway nonces & IdP nonces | Verified across downgrade and re-upgrade cycles | **PASS** |
| **Rolling 60s Rate Limiter** | Boundary burst defense across minute boundaries | Boundary burst blocked; aged requests allowed | **PASS** |
| **Cross-Session Rate Limiting** | Shared enforcement across separate DB sessions | Verified with 2 distinct sessions | **PASS** |
| **Thread Concurrency** | Atomic rate limit under 25 concurrent threads | Exactly 20 allowed, 5 throttled | **PASS** |
| **Bounded Rate Limit Cleanup** | Pruning inactive records older than 1 hour | Stale rows purged; active rows preserved | **PASS** |
| **Frontend Production Build** | TypeScript typecheck and Vite bundle | 1583 modules compiled cleanly in 46.09s | **PASS** |

---

## 3. Disclosures & Release Governance

1. **Production Sign-Off Withholding:**
   - Automated ICAI verification and live external IdP token verification require live operational integration with statutory portals and external vendor JWKS endpoints.
   - In accordance with independent audit guidelines, this release is classified as **APPROVED AS REVISED RELEASE CANDIDATE**. Production sign-off is withheld until live external JWKS and ICAI gateway endpoints are operational in production. Real customer GST data must not be onboarded yet.

2. **Git Commit & Tag Signing:**
   - Commits and tags in this repository are currently unsigned. Repository branch protection rules are not enabled on the GitHub repository.

3. **Release Governance:**
   - Historical tags `v1.0.0` through `v1.0.15` remain permanently untouched.
   - The annotated tag `v1.0.16` is created and pushed strictly after branch CI succeeds on both `master` and `remediation/audit-findings-phase3`.
