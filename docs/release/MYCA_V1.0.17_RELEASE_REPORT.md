# MyCA v1.0.17 Release Report

**Release Tag:** `v1.0.17`  
**Target Repository:** `SuhaibShaik2917/myca`  
**Parent Release:** `v1.0.16` candidate commit (`a6f05a75448aac8ab1effc7fc532668ccf095ba5`)  
**Release Type:** Post-v1.0.16 Security Remediation: Proxy Trust Right-to-Left Traversal & Client IP Spoofing Elimination, Explicit Boolean SQL Rate-Limit Decision Invariance, Operational Scheduled Rate-Limit Cleanup Architecture (FastAPI Lifespan & In-Flow Scheduling)  
**Release Verdict:** **APPROVED AS REVISED RELEASE CANDIDATE (PRODUCTION SIGN-OFF WITHHELD / NOT PRODUCTION APPROVED FOR UNRESTRICTED PUBLIC USE UNTIL EXTERNAL PRODUCTION JWKS & ICAI GATEWAY INTEGRATIONS ARE OPERATIONAL)**  

---

## 1. Executive Summary

MyCA `v1.0.17` implements an exacting remediation resolving the three findings raised during the independent audit of `v1.0.16`. This release eliminates reverse proxy header spoofing, ensures mathematical rate-limiting decision invariance even under microsecond clock collisions, establishes automated background and in-flow storage cleanup, and lays the groundwork for the broader post-authentication production readiness audit:

1. **Proxy Trust Right-to-Left Traversal & Spoofing Defense (Finding 1):**
   - Redesigned `extract_client_ip()` in `app/modules/auth/api.py` according to RFC 7239 / proxy chain trust standards.
   - Connections from untrusted direct peers strictly ignore `X-Forwarded-For` and use the direct connection IP.
   - For connections from trusted proxies, the proxy chain in `X-Forwarded-For` is traversed from **right to left**. Each trusted internal proxy hop is traversed, and the **first untrusted address** is selected as the authentic client IP.
   - Attacker-injected values positioned to the left of the true client IP (e.g. `X-Forwarded-For: 1.1.1.1, 198.51.100.42`) are strictly ignored.
   - Extended proxy trust validation with `is_trusted_proxy()` supporting both individual IPv4/IPv6 addresses and CIDR network blocks (e.g. `10.0.0.0/8`).
   - All extracted IPs are validated against standard RFC syntax using Python's `ipaddress` library, safely discarding malformed or SQL-injected header fragments.

2. **Explicit Boolean SQL Rate-Limit Decision Invariance (Finding 3):**
   - Refactored `check_distributed_rate_limit()` in `app/modules/auth/nonce_service.py` to utilize an atomic PostgreSQL Common Table Expression (CTE) with row-level locking (`locked_row FOR UPDATE`).
   - The query now returns an **explicit boolean acceptance value** `(current_state.current_count < :max_limit) AS is_accepted` directly from the atomic SQL statement.
   - Completely eliminated the vulnerability where acceptance was inferred from array membership (`timestamps @> ARRAY[:now]`), which could cause duplicate microsecond timestamps or concurrent identical timestamps to misclassify a rejected request as accepted.
   - Proved through automated tests that multiple requests bearing the exact same microsecond timestamp strictly enforce quota boundaries without conflation.

3. **Operational Scheduled Rate-Limit Cleanup Architecture (Finding 2):**
   - Operationalized rate-limit cleanup across three robust execution surfaces:
     - **FastAPI Lifespan Background Task:** `run_periodic_rate_limit_cleanup()` runs as an asynchronous worker during application lifespan, sleeping for configurable `RATE_LIMIT_CLEANUP_INTERVAL_SECONDS` (default: 3600s) and stopping gracefully on server shutdown.
     - **In-Flow Opportunistic Cleanup Hook:** `schedule_rate_limit_cleanup_if_due()` dispatches background cleanup via FastAPI `BackgroundTasks` during authentication requests (`/idp-nonce`, `/idp-exchange`) guarded by a thread-safe monotonic clock throttle.
     - **Standalone Operational Runner:** `run_rate_limit_cleanup_job()` manages an independent session with error isolation, suitable for cron jobs, container orchestrators, or CLI invocation.
   - Added configuration parameters `RATE_LIMIT_CLEANUP_INTERVAL_SECONDS` and `RATE_LIMIT_RETENTION_HOURS` in `app/config.py`.

4. **Preservation of Previous Hardening:**
   - Retained all RFC 7636 PKCE length/charset constraints (43–128 unreserved chars).
   - Retained 100% RLS and FORCE RLS enforcement across all 37 tenant tables.
   - Retained full migration rollback safety with updated disposable marker token `MYCA_VERIFIED_DISPOSABLE_TEST_DATABASE_TOKEN_V1_0_17`.
   - Historical tags `v1.0.0` through `v1.0.16` remain completely preserved and immutable.

---

## 2. Test & Verification Summary

| Verification Gate | Requirement | Actual Result | Status |
|---|---|---|---|
| **Backend Pytest Suite** | All unit, integration, and security tests pass | **402 / 402 passed** (0 failures, 10 deprecation warnings) | **PASS** |
| **Rollback Safety Suite** | Disposable marker & connection verification | **31 / 31 passed** | **PASS** |
| **RLS Security Audit** | Dynamic discovery of all tenant tables; RLS & FORCE RLS | **37 / 37 tables** (100% RLS=True, 100% FORCE_RLS=True) | **PASS** |
| **Cross-Tenant Isolation** | Read, UPDATE, DELETE, INSERT spoofing across 12 domains | 100% blocked under `app_service_user` | **PASS** |
| **Catalogue Immutability** | Reference & system tables write revocation | INSERT/UPDATE/DELETE denied | **PASS** |
| **Migration Rollback State** | Downgrade to 0019 against independent baseline | 79 grants, 87 policies, 41 tables match 100% | **PASS** |
| **Populated Rollback** | Preserves evidence, audit events, gateway nonces & IdP nonces | Verified across downgrade and re-upgrade cycles | **PASS** |
| **Right-to-Left Proxy Defense** | Traversal selects first untrusted hop; ignores spoofed prefixes | Verified across single-proxy, multi-hop, and CIDR chains | **PASS** |
| **Explicit Boolean Rate Limit** | Quota enforced under identical microsecond timestamps | Microsecond collision test: exact quota enforced | **PASS** |
| **Operational Cleanup** | Standalone runner, lifespan worker, and in-flow background tasks | All 3 operational surfaces verified | **PASS** |
| **Frontend Production Build** | TypeScript typecheck and Vite bundle | 1583 modules compiled cleanly in 32.96s | **PASS** |

---

## 3. Disclosures & Release Governance

1. **Production Sign-Off Withholding:**
   - In accordance with independent audit guidelines, this release is classified as **APPROVED AS REVISED RELEASE CANDIDATE**. Production sign-off remains withheld pending live external JWKS and ICAI gateway endpoints in production.
   - **Real customer GSTIN or financial data must not be onboarded** until external IdP endpoints and gateway integrations are certified.

2. **Git Commit & Tag Signing:**
   - Commits and tags in this repository are currently unsigned. Repository branch protection rules are not enabled on the GitHub repository.

3. **Release Governance:**
   - Historical tags `v1.0.0` through `v1.0.16` remain permanently untouched.
   - Annotated tag `v1.0.17` is created and pushed strictly after branch CI succeeds on both `master` and `remediation/audit-findings-phase3`.

4. **Strategic Roadmap (Post-v1.0.17):**
   - With authentication, session binding, PKCE, sliding-window rate limiting, and proxy security fully hardened, engineering focus shifts toward a comprehensive production-readiness audit across the entire MyCA platform (Database isolation, GST/ITC calculations, Audit trails, Financial integrity, Backup/observability, and CA review).
