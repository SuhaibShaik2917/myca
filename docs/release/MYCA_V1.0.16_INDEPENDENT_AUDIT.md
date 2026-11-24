# MyCA v1.0.16 Independent Audit

**Release Target:** `v1.0.16`  
**Auditor Role:** Independent Statutory & Release Security Auditor  
**Audit Decision:** **APPROVED AS REVISED RELEASE CANDIDATE (PRODUCTION SIGN-OFF WITHHELD / NOT PRODUCTION APPROVED FOR UNRESTRICTED PUBLIC USE UNTIL EXTERNAL PRODUCTION JWKS & ICAI GATEWAY INTEGRATIONS ARE OPERATIONAL)**  

---

## 1. Audit Scope & Verification Objective

The objective of the v1.0.16 independent audit is to evaluate the remediation of findings identified during the review of v1.0.15:

1. **PKCE Verifier Length & Character Set Enforcement:** Verify that `code_verifier` enforces RFC 7636 length bounds (43–128 characters) and permitted unreserved character regex `^[A-Za-z0-9_.~-]{43,128}$`.
2. **Rolling 60-Second Sliding Window Rate Limiting:** Verify that the rate limiter enforces a genuine rolling 60-second window that cannot be bypassed via minute boundary bursts.
3. **Bounded Retention & Cleanup for Rate-Limit Storage:** Verify that `public.auth_rate_limits` has an explicit non-blocking cleanup strategy that purges inactive rows without interfering with active rate-limit enforcement.
4. **Multi-Session and Concurrent Thread Invariance:** Verify that rate limits are enforced across independent database sessions and under concurrent thread execution.
5. **Trusted Proxy Client IP Extraction:** Verify that client IP resolution resists header spoofing from untrusted peers while correctly parsing forwarded IPs from configured trusted proxies.
6. **Exception Sanitization & Error Handling:** Verify that database errors and unexpected exceptions do not leak internal connection strings or stack traces.

---

## 2. Findings & Verification Analysis

### Finding 1: PKCE Verifier Length & Character Set Validation
- **Status in v1.0.15:** `code_verifier` checked only for non-empty and ASCII encoding, omitting 43-128 length checks and character restrictions.
- **Remediation in v1.0.16:** Enforced length bounds (43–128) and regex `^[A-Za-z0-9_.~-]{43,128}$` in both `IdpTokenExchangeRequest` schema and `consume_idp_login_nonce` service. Shorter, longer, or invalid characters are rejected with HTTP 400 or HTTP 422.
- **Audit Verdict:** **CLOSED (VERIFIED)**.

### Finding 2: Genuine Rolling 60-Second Sliding Window
- **Status in v1.0.15:** Rate limits were rounded to calendar-minute boundaries, permitting up to 40 requests across a minute transition.
- **Remediation in v1.0.16:** Additive migration `0030_auth_rate_limits_rolling.py` provisions timestamp arrays. `check_distributed_rate_limit()` dynamically filters requests to `t > NOW() - INTERVAL '60 seconds'`. Boundary burst test proved request 21 across a minute boundary is blocked with HTTP 429.
- **Audit Verdict:** **CLOSED (VERIFIED)**.

### Finding 3: Rate-Limit Storage Bounded Retention
- **Status in v1.0.15:** `public.auth_rate_limits` had no retention policy or cleanup mechanism.
- **Remediation in v1.0.16:** Added `updated_at` column and index `ix_auth_rate_limits_updated_at`. Implemented `cleanup_expired_auth_rate_limits()` to safely purge inactive keys older than 1 hour. Active keys (< 60s) remain untouched.
- **Audit Verdict:** **CLOSED (VERIFIED)**.

### Finding 4: Multi-Session and Multi-Worker Concurrency
- **Status in v1.0.15:** Concurrency was tested only within single-session contexts.
- **Remediation in v1.0.16:** Tested across 2 separate `SessionLocal()` sessions and 25 concurrent threads; verified shared atomic enforcement.
- **Audit Verdict:** **CLOSED (VERIFIED)**.

### Finding 5: Client IP Extraction & Reverse Proxy Defense
- **Status in v1.0.15:** `request.client.host` was used without handling reverse proxies or header spoofing defenses.
- **Remediation in v1.0.16:** Implemented `extract_client_ip(request)` utilizing `settings.TRUSTED_PROXIES`. Untrusted direct peers cannot spoof `X-Forwarded-For`; trusted proxies forward validated RFC IPv4/IPv6 client addresses.
- **Audit Verdict:** **CLOSED (VERIFIED)**.

### Finding 6: Exception Sanitization & Error Handling
- **Status in v1.0.15:** Raw exception strings were embedded in HTTP 503 error details, and user lookups used bare `except Exception: pass`.
- **Remediation in v1.0.16:** Sanitized all error messages to return generic failure codes while logging structured details internally.
- **Audit Verdict:** **CLOSED (VERIFIED)**.

---

## 3. Disclosures & Release Recommendation

1. **Production Sign-Off Withheld:**
   - Automated ICAI credential verification and federated IdP verification currently operate in test/staging modes with mock anchors or manual document review paths.
   - Live external integrations with the statutory ICAI portal and external vendor JWKS endpoints must be completed and validated in a production network environment before full statutory production sign-off can be granted.
   - Real customer GST data must not be onboarded yet.

2. **Commit Signing & Branch Protection:**
   - Commits and tags remain unsigned; branch protection rules are disabled on GitHub.
