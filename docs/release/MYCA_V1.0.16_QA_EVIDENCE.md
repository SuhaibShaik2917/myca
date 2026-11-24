# MyCA v1.0.16 QA Evidence

**Target Release:** `v1.0.16`  
**Execution Context:** Local Test PostgreSQL Instance & GitHub Actions CI Matrix  

---

## 1. Automated Test Execution Summary

### Backend Pytest Suite
- **Command:** `python -m pytest tests/ -v --tb=short`
- **Result:** **399 passed, 1 warning in 52.98s**
- **Test Modules Executed:**
  - `tests/test_ai_assistant.py` (3 tests)
  - `tests/test_auth_token.py` (75 tests — including 6 new dedicated tests for PKCE length/charset, rolling sliding window burst defense, cross-session rate limiting, concurrent threading, bounded cleanup, and trusted proxy defense)
  - `tests/test_circuit_breaker.py` (8 tests)
  - `tests/test_invoice_brain.py` (4 tests)
  - `tests/test_migration_security_baseline.py` (31 tests)
  - `tests/test_milestone1_security_closure.py` (13 tests)
  - `tests/test_milestone2_1_statutory_corrections.py` (1 test)
  - `tests/test_milestone2_7_1_statutory_integrity.py` (22 tests)
  - `tests/test_milestone2_7_2_financial_reconciliation_closure.py` (36 tests)
  - `tests/test_milestone2_7_inward_accounting_workflow.py` (24 tests)
  - `tests/test_milestone2_reconciliation.py` (46 tests)
  - `tests/test_milestone3_gstr3b_offset_and_filing.py` (13 tests)
  - `tests/test_milestone4_credit_builder.py` (11 tests)
  - `tests/test_milestone5_scheme_navigator.py` (20 tests)
  - `tests/test_rate_limiter.py` (7 tests)
  - `tests/test_rollback_script_safety.py` (31 tests)
  - `tests/test_statutory_auditor_provenance_and_replay_hardening.py` (42 tests)
  - `tests/test_statutory_section_17_5_boundaries_and_reference_grants.py` (12 tests)

### Dedicated v1.0.16 Tests Verified
1. `test_pkce_verifier_length_and_charset_enforcement`:
   - Validates that verifiers < 43 chars (42 chars), > 128 chars (129 chars), and invalid characters are rejected with HTTP 400 or HTTP 422.
   - Validates boundary lengths (43 chars, 128 chars) with RFC 7636 unreserved characters succeed.
2. `test_rolling_60s_rate_limiter_boundary_burst_defense`:
   - Validates that 20 requests at 10:00:59 followed by a request at 10:01:00.500 (across minute boundary) are blocked with HTTP 429 `RATE_LIMIT_EXCEEDED`.
   - Validates that requests after 60s (10:02:00) succeed.
3. `test_rate_limiter_across_separate_db_sessions`:
   - Proves rate limit counts are shared and enforced across independent database sessions.
4. `test_concurrent_rate_limiting_across_threads`:
   - 25 concurrent threads execute simultaneously; exactly 20 succeed and 5 are throttled with HTTP 429.
5. `test_rate_limits_bounded_retention_and_cleanup`:
   - Validates that stale records (>1h) are purged by `cleanup_expired_auth_rate_limits()`.
   - Validates that active records (<60s) remain intact and rate limiting continues uninterrupted.
6. `test_trusted_proxy_client_ip_extraction`:
   - Validates that spoofed `X-Forwarded-For` from untrusted direct peers is ignored.
   - Validates that valid `X-Forwarded-For` from configured trusted proxies is respected and sanitized.

### Rollback Script Safety Unit Tests
- **Command:** `python -m pytest tests/test_rollback_script_safety.py -v`
- **Result:** **31 passed in 0.52s**
- **Safety Markers:** Tested with token `MYCA_VERIFIED_DISPOSABLE_TEST_DATABASE_TOKEN_V1_0_16`.

---

## 2. Dynamic RLS Security Audit

- **Command:** `python scripts/audit_rls_security_comprehensive.py`
- **Result:** **ALL COMPREHENSIVE RLS & SECURITY AUDITS PASSED FLAWLESSLY!**
- **Coverage:** 37/37 tenant tables enforce RLS and FORCE RLS (100%).

---

## 3. Migration Rollback Invariant Verification

- **Command:** `python scripts/verify_migration_rollback_state.py`
- **Result:** **ALL MIGRATION ROLLBACK & INDEPENDENT BASELINE REFERENCE INVARIANTS 100% VERIFIED!**
- **Head State:** `0030_auth_rate_limits_rolling` (120 grants, 96 policies).
- **Baseline 0019 Match:** Exact match against `baseline_0019_reference.json` (79 grants, 87 policies, 41 tables).

---

## 4. Frontend Production Build

- **Command:** `npm run build`
- **Result:** **Built in 46.09s** (0 errors, 0 warnings)
- **Artifacts:**
  - `dist/index.html` (0.48 kB)
  - `dist/assets/index-CXHqnTlf.css` (29.39 kB)
  - `dist/assets/index-DBkuh5Fw.js` (420.23 kB)
