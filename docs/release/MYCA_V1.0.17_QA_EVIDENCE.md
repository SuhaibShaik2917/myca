# MyCA v1.0.17 QA Evidence

**Target Release:** `v1.0.17`  
**Execution Context:** Local Test PostgreSQL Instance & GitHub Actions CI Matrix  

---

## 1. Automated Test Execution Summary

### Backend Pytest Suite
- **Command:** `python -m pytest tests/ -v --tb=short`
- **Result:** **402 passed, 10 warnings in 51.59s**
- **Test Modules Executed:**
  - `tests/test_ai_assistant.py` (3 tests)
  - `tests/test_auth_token.py` (78 tests — including right-to-left proxy chain traversal, explicit boolean rate-limiting decision invariance, operational scheduled cleanup, and in-flow BackgroundTasks verification)
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

### Dedicated v1.0.17 Tests Verified
1. `test_trusted_proxy_client_ip_extraction`:
   - Validates that spoofed `X-Forwarded-For` from untrusted direct peers is ignored.
   - Validates that single trusted proxy extracts rightmost untrusted hop `198.51.100.42` ignoring attacker spoof `1.1.1.1` on left.
   - Validates multi-hop trusted proxy chain (CDN `10.0.0.1` -> Ingress `127.0.0.1`) walks past internal hops and selects `198.51.100.42`.
   - Validates CIDR network block in `TRUSTED_PROXIES` (e.g. `10.0.0.0/8`).
   - Validates malformed header values are skipped safely.
2. `test_rate_limiter_explicit_acceptance_boolean_identical_timestamps`:
   - Validates that multiple requests bearing the exact same microsecond timestamp strictly enforce quota boundaries via SQL boolean return without array membership conflation.
3. `test_rate_limit_scheduled_cleanup_operational`:
   - Validates standalone operational runner `run_rate_limit_cleanup_job()`.
   - Validates opportunistic dispatching with FastAPI `BackgroundTasks`.
   - Validates continuous async runner `run_periodic_rate_limit_cleanup()` with `stop_event` cancellation.
   - Proves stale rows are purged and active rows are preserved.
4. `test_idp_nonce_triggers_background_cleanup`:
   - Validates that calling `/api/v1/auth/idp-nonce` through `TestClient` executes cleanly and schedules background cleanup without latency penalty.

### Rollback Script Safety Unit Tests
- **Command:** `python -m pytest tests/test_rollback_script_safety.py -v`
- **Result:** **31 passed in 0.33s**
- **Safety Markers:** Tested with token `MYCA_VERIFIED_DISPOSABLE_TEST_DATABASE_TOKEN_V1_0_17`.

---

## 2. Dynamic RLS Security Audit

- **Command:** `python scripts/audit_rls_security_comprehensive.py`
- **Result:** **ALL COMPREHENSIVE RLS & SECURITY AUDITS PASSED FLAWLESSLY!**
  - Section 1: Exactly 37 of 37 tenant tables enforce `RLS=True` and `FORCE_RLS=True`.
  - Section 2: Reference catalogue write denials verified under `app_service_user`.
  - Section 3: Cross-tenant READ, UPDATE, DELETE, and INSERT isolation verified across all 12 core domains.
  - Section 4: API-layer bearer token gating and tamper protection verified.

---

## 3. Migration Rollback & Baseline Verification

- **Command:** `python scripts/verify_migration_rollback_state.py`
- **Result:** **ALL MIGRATION ROLLBACK & INDEPENDENT BASELINE REFERENCE INVARIANTS 100% VERIFIED!**
  - Established baseline at `0019`: 100% match with clean independent reference (79 grants, 87 policies, 41 tables).
  - Populated rollback: verified evidence preservation, gateway nonce persistence, and IdP nonce replay defense.
  - Downgrade 0030 -> 0029 verified.
  - Final re-upgrade to `0030`: restored production security state (120 grants, 96 policies).

---

## 4. Frontend Production Build

- **Command:** `npm run build` in `frontend/`
- **Result:** **1583 modules transformed, built in 32.96s** (Zero TypeScript or bundling errors).
