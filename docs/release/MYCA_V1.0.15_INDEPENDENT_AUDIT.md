# MyCA v1.0.15 Independent Audit

**Release Target:** `v1.0.15`  
**Auditor Role:** Independent Statutory & Release Security Auditor  
**Audit Decision:** **APPROVED AS REVISED RELEASE CANDIDATE (PRODUCTION SIGN-OFF WITHHELD / NOT PRODUCTION APPROVED FOR UNRESTRICTED PUBLIC USE UNTIL EXTERNAL PRODUCTION JWKS & ICAI GATEWAY INTEGRATIONS ARE OPERATIONAL)**  

---

## 1. Audit Scope & Verification Objective

The objective of the v1.0.15 independent audit is to evaluate the remediation of findings identified during the review of v1.0.14:

1. **Mandatory PKCE & State:** Verify that `state` and `code_challenge` are strictly mandatory and rejected when omitted or malformed.
2. **HttpOnly Cookie-Only Binding:** Verify that `session_token` is never returned in JSON response bodies and that exchange strictly requires the `myca_auth_session` HttpOnly cookie, rejecting all header and body fallbacks.
3. **Constant-Time Verification:** Verify that `hmac.compare_digest()` is used for all PKCE, state, and session binding comparisons.
4. **Distributed Rate Limiting:** Verify that rate limiting is backed by shared PostgreSQL table `public.auth_rate_limits` with atomic upsert locking rather than process-local memory.
5. **Atomic Nonce Quotas:** Verify that active nonce quotas (max 5 per org) are enforced under PostgreSQL advisory transaction locks (`pg_advisory_xact_lock`), eliminating race conditions.
6. **Bounded Cleanup Strategy:** Verify that consumed nonces within 24 hours are preserved for replay defense, while older records are pruned to maintain table boundedness.
7. **Flow Honesty & Disclosures:** Accurately characterize the flow as **MyCA Federated IdP Token Verification with Browser PKCE & Session Binding** rather than standards-compliant OAuth authorization-code server.

---

## 2. Findings & Verification Analysis

### Finding 1: Mandatory PKCE and State Verification
- **Status in v1.0.14:** PKCE and state were optional or allowed fallbacks.
- **Remediation in v1.0.15:** `IdpNonceRequest` enforces mandatory `state` (16–128 chars) and `code_challenge` (43–128 chars base64url). Missing or invalid values return HTTP 400 or HTTP 422.
- **Audit Verdict:** **CLOSED (VERIFIED)**.

### Finding 2: Session Token Exposure & Cookie Enforcement
- **Status in v1.0.14:** `session_token` was returned in the JSON response and accepted via headers (`X-Session-Token`, `X-Auth-Session-Id`) or body.
- **Remediation in v1.0.15:** `session_token` removed from `IdpNonceResponse`. Exchange endpoint accepts the session token exclusively from `myca_auth_session` cookie; all header and body fallbacks were removed.
- **Audit Verdict:** **CLOSED (VERIFIED)**.

### Finding 3: Timing Side-Channel Vulnerabilities
- **Status in v1.0.14:** Standard Python string equality was used in some verification branches.
- **Remediation in v1.0.15:** Replaced with `hmac.compare_digest()` for PKCE verifier hashing, state matching, and session token binding hashes.
- **Audit Verdict:** **CLOSED (VERIFIED)**.

### Finding 4: In-Memory Rate Limiting in Multi-Worker Environments
- **Status in v1.0.14:** Rate limiting was held in memory (`NonceRateLimiter`), allowing bypasses across worker processes.
- **Remediation in v1.0.15:** Migration `0029_auth_rate_limits.py` adds `public.auth_rate_limits`. `check_distributed_rate_limit` enforces 20 req/min IP and 30 req/min Org atomically via PostgreSQL upsert.
- **Audit Verdict:** **CLOSED (VERIFIED)**.

### Finding 5: Active Nonce Quota Race Condition
- **Status in v1.0.14:** Concurrency check was subject to count-then-insert race under high load.
- **Remediation in v1.0.15:** Wrapped under `pg_advisory_xact_lock(hashtext(:org_id))`, ensuring atomic serialization per tenant. Tested under multithreaded concurrent requests.
- **Audit Verdict:** **CLOSED (VERIFIED)**.

### Finding 6: Long-Term Nonce Table Growth vs Replay Protection
- **Status in v1.0.14:** Consumed nonces were permanently retained without any retention window policy, risking unbounded growth.
- **Remediation in v1.0.15:** Prunes consumed nonces older than 24 hours while strictly preserving nonces within the 24-hour window to protect against replay attacks.
- **Audit Verdict:** **CLOSED (VERIFIED)**.

---

## 3. Disclosures & Release Recommendation

1. **Production Sign-Off Withheld:**
   - Automated ICAI credential verification and federated IdP verification currently operate in test/staging modes with mock anchors or manual document review paths.
   - Live external integrations with the statutory ICAI portal and external vendor JWKS endpoints must be completed and validated in a production network environment before full statutory production sign-off can be granted.
   - Flow is honestly documented as **MyCA Federated IdP Token Verification with Browser PKCE & Session Binding**.

2. **Commit Signing & Branch Protection:**
   - Commits and tags remain unsigned; branch protection rules are disabled on GitHub.
