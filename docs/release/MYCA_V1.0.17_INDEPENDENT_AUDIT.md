# MyCA v1.0.17 Independent Audit

**Release Target:** `v1.0.17`  
**Auditor Role:** Independent Statutory & Release Security Auditor  
**Audit Decision:** **APPROVED AS REVISED RELEASE CANDIDATE (PRODUCTION SIGN-OFF WITHHELD / NOT PRODUCTION APPROVED FOR UNRESTRICTED PUBLIC USE UNTIL EXTERNAL PRODUCTION JWKS & ICAI GATEWAY INTEGRATIONS ARE OPERATIONAL)**  

---

## 1. Audit Scope & Verification Objective

The objective of the v1.0.17 independent audit is to evaluate the remediation of the three findings raised during the review of v1.0.16:

1. **Trusted Proxy IP Extraction Vulnerability to Spoofing (Finding 1):** Verify that `extract_client_ip()` traverses `X-Forwarded-For` from right to left, validating each trusted hop, selecting the first untrusted IP, and ignoring attacker-supplied header prefixes.
2. **Operational Scheduling of Rate-Limit Cleanup (Finding 2):** Verify that `cleanup_expired_auth_rate_limits()` is operationally scheduled and executed via server lifecycle tasks and in-flow background tasks, with proven execution tests.
3. **Explicit Boolean Return in Rate-Limit SQL Decision (Finding 3):** Verify that the atomic rate-limiting SQL operation returns an explicit boolean acceptance result rather than inferring acceptance from timestamp array membership, eliminating conflation under identical microsecond timestamps.

---

## 2. Findings & Verification Analysis

### Finding 1: Reverse Proxy Spoofing via Left-to-Right Header Parsing
- **Status in v1.0.16:** `extract_client_ip()` iterated left-to-right over `X-Forwarded-For`, selecting the first valid IP. This allowed attackers to prepend a spoofed IP ahead of the true IP appended by a trusted proxy.
- **Remediation in v1.0.17:** Rewrote `extract_client_ip()` with RFC 7239 compliant right-to-left traversal:
  - Untrusted direct peer: `X-Forwarded-For` is completely ignored; direct peer IP is returned.
  - Trusted proxy peer: walks `X-Forwarded-For` from right to left; internal hops in `TRUSTED_PROXIES` are skipped; the **first untrusted hop** is selected as the genuine client IP.
  - Added CIDR network matching (e.g. `10.0.0.0/8`) via `is_trusted_proxy()`.
  - Added RFC syntax validation via `ipaddress.ip_address`.
- **Evidence:** `test_trusted_proxy_client_ip_extraction` proved that spoofed IP prefixes are ignored and authentic client IPs are extracted across single-proxy, multi-hop, and CIDR proxy topologies.
- **Audit Verdict:** **CLOSED (VERIFIED)**.

### Finding 2: Rate-Limit Cleanup Operational Scheduling
- **Status in v1.0.16:** `cleanup_expired_auth_rate_limits()` was defined as a standalone function but was not scheduled or invoked in application runtime flows.
- **Remediation in v1.0.17:** Established a multi-layer operational scheduling architecture:
  - **FastAPI Lifespan Background Task:** Added async `lifespan` in `app/main.py` executing `run_periodic_rate_limit_cleanup()` periodically (default: 3600s), shutting down gracefully on server stop.
  - **In-Flow Opportunistic Background Dispatch:** Added `schedule_rate_limit_cleanup_if_due()` guarded by a thread-safe monotonic clock lock (300s throttle) dispatched via FastAPI `BackgroundTasks` during `/idp-nonce` and `/idp-exchange`.
  - **Standalone Runner:** Added `run_rate_limit_cleanup_job()` for standalone worker/cron invocation.
- **Evidence:** `test_rate_limit_scheduled_cleanup_operational` and `test_idp_nonce_triggers_background_cleanup` proved operational execution across all 3 surfaces with zero endpoint latency impact.
- **Audit Verdict:** **CLOSED (VERIFIED)**.

### Finding 3: Rate-Limit Decision Inferred from Array Membership
- **Status in v1.0.16:** Rate limit decision was evaluated by checking `timestamps @> ARRAY[:now]`. If multiple requests arrived with identical microsecond timestamps, a rejected request could be falsely accepted because `:now` was already present.
- **Remediation in v1.0.17:** Rewrote `check_distributed_rate_limit()` to use an atomic PostgreSQL CTE with `locked_row FOR UPDATE`. The query explicitly returns `(current_state.current_count < :max_limit) AS is_accepted` directly from SQL.
- **Evidence:** `test_rate_limiter_explicit_acceptance_boolean_identical_timestamps` proved that 4 requests sharing the exact same `fixed_now` timestamp enforce quota boundaries (`max_ip = 3`) with mathematical precision: requests 1–3 accepted, request 4 rejected with HTTP 429.
- **Audit Verdict:** **CLOSED (VERIFIED)**.

---

## 3. Disclosures & Release Recommendation

1. **Production Sign-Off Withheld:**
   - In accordance with independent release governance, this release is classified as **APPROVED AS REVISED RELEASE CANDIDATE (PRODUCTION SIGN-OFF WITHHELD)**.
   - **Real customer GST data must not be onboarded** until external vendor JWKS endpoints and statutory ICAI gateway integration are operational in production.

2. **Transition to Broader Product Validation:**
   - With the completion of v1.0.17, the recurring authentication remediation cycle is formally concluded.
   - Engineering focus now transitions toward the comprehensive production-readiness audit across all MyCA core functional pillars:
     1. Database isolation and migration resilience.
     2. GST calculations, HSN/SAC turnover classification, and Section 16(4) / 17(5) statutory ITC eligibility.
     3. Financial data immutability and audit trails.
     4. GSTR-3B Rule 88A offset calculations and Cash Challan PMT-06 reconciliation.
     5. Disaster recovery, backup validation, and production observability.
