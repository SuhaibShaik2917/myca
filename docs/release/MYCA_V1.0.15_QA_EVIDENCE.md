# MyCA v1.0.15 QA Evidence

**Target Release:** `v1.0.15`  
**Execution Context:** Local Test PostgreSQL Instance & GitHub Actions CI Matrix  

---

## 1. Automated Test Execution Summary

### Backend Pytest Suite
- **Command:** `python -m pytest tests/ -v --tb=short`
- **Result:** **393 passed, 1 warning in 24.80s**
- **Test Modules Executed:**
  - `tests/test_ai_assistant.py` (3 tests)
  - `tests/test_auth_token.py` (69 tests — including 7 new dedicated security and race tests)
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

### Dedicated v1.0.15 Security Tests Verified
1. `test_mandatory_state_and_challenge_rejected_when_missing_or_malformed`:
   - Validates that requests omitting `state` or `code_challenge`, or providing values outside length/character constraints, are rejected with HTTP 400 or HTTP 422.
2. `test_httponly_cookie_only_session_binding_and_rejection_of_fallbacks`:
   - Validates that session token is absent from JSON response body.
   - Validates that token exchange succeeds with `myca_auth_session` cookie.
   - Validates that exchange fails with HTTP 401 `MISSING_SESSION_COOKIE` when the cookie is absent, even if `X-Session-Token` or payload `session_token` are provided.
3. `test_constant_time_pkce_and_state_mismatch_rejection`:
   - Validates rejection when `code_verifier` does not match `code_challenge` (HTTP 401 `PKCE_VERIFICATION_FAILED`).
   - Validates rejection when `state` does not match issued state (HTTP 401 `STATE_MISMATCH`).
4. `test_concurrent_nonce_issuance_atomically_capped`:
   - Uses multithreading (8 concurrent threads) to request nonces for the same organization simultaneously.
   - Validates that exactly 5 active nonces succeed, and subsequent requests fail with HTTP 429 `ACTIVE_NONCE_LIMIT_EXCEEDED` under advisory locking.
5. `test_bounded_retention_preserves_consumed_nonces_within_24h_and_purges_older`:
   - Validates that consumed nonces within 24 hours are retained.
   - Validates that consumed nonces older than 24 hours and expired active nonces are purged.
6. `test_cross_session_attack_prevention`:
   - Validates that exchanging an authorization nonce with a session cookie from a different browser session fails with HTTP 401 `SESSION_BINDING_MISMATCH`.
7. `test_distributed_rate_limiting_with_postgres_backend`:
   - Validates atomic rate limits (20/min IP, 30/min Org) via `public.auth_rate_limits`.

### Rollback Script Safety Unit Tests
- **Command:** `python -m pytest tests/test_rollback_script_safety.py -v`
- **Result:** **31 passed in 0.11s**
- **Safety Markers:** Tested with token `MYCA_VERIFIED_DISPOSABLE_TEST_DATABASE_TOKEN_V1_0_15`.

---

## 2. Frontend Production Build

- **Command:** `npm run build`
- **Result:** **Built in 2.13s** (0 errors, 0 warnings)
- **Artifacts:**
  - `dist/index.html` (0.48 kB)
  - `dist/assets/index-CXHqnTlf.css` (29.39 kB)
  - `dist/assets/index-DBkuh5Fw.js` (420.23 kB)
