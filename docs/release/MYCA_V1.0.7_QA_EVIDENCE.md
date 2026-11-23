# MyCA v1.0.7 — QA Test Execution & Verification Evidence

**Date:** November 23, 2026  
**Target Release:** `v1.0.7`  
**Test Suite:** Pytest 9.2.0 (Python 3.12.3)  
**Database:** PostgreSQL 16 on Ubuntu WSL / Linux  
**Total Tests:** **275 passed out of 275 tests (100% PASS)**

---

## 1. BACKEND TEST SUITE SUMMARY

| Test File | Total Tests | Passed | Failed | Status |
|---|---|---|---|---|
| `tests/test_ai_assistant.py` | 3 | 3 | 0 | PASS |
| `tests/test_auth_token.py` | 7 | 7 | 0 | PASS |
| `tests/test_invoice_brain.py` | 7 | 7 | 0 | PASS |
| `tests/test_migration_security_baseline.py` | 17 | 17 | 0 | PASS |
| `tests/test_milestone1_security_closure.py` | 31 | 31 | 0 | PASS |
| `tests/test_milestone2_reconciliation.py` | 5 | 5 | 0 | PASS |
| `tests/test_milestone3_gstr3b_offset_and_filing.py` | 5 | 5 | 0 | PASS |
| `tests/test_milestone4_credit_builder.py` | 5 | 5 | 0 | PASS |
| `tests/test_milestone5_scheme_navigator.py` | 5 | 5 | 0 | PASS |
| `tests/test_statutory_auditor_provenance_and_replay_hardening.py` | 12 | 12 | 0 | PASS |
| `tests/test_statutory_section_17_5_boundaries_and_reference_grants.py` | 10 | 10 | 0 | PASS |
| Other test modules (reconciliation, invoices, auth, etc.) | 168 | 168 | 0 | PASS |
| **TOTAL** | **275** | **275** | **0** | **100% PASS** |

---

## 2. KEY NEW & HARDENED TEST CASES IN v1.0.7

1. `test_statutory_auditor_self_assertion_precluded`:
   - Verifies arbitrary caller claiming `is_icai_verified=True` and `verification_status="INDEPENDENTLY_VERIFIED"` without authentic evidence is denied and saved as unverified.
2. `test_signature_replay_expired_token_permanent_invalidation`:
   - Verifies an expired token is rejected immediately on first presentation with `EXPIRED_TOKEN` and is not stored in the replay log.
   - Verifies a recorded valid token cannot be replayed even after it passes expiration.
3. `test_signature_replay_concurrent_collision_rejected`:
   - Uses `threading.Barrier(2)` across two distinct OS threads and independent PostgreSQL sessions to race simultaneous token consumption.
   - Asserts exactly one consumption succeeds and the other fails with collision detection (`CONCURRENT_REPLAY_DETECTED` or `SIGNATURE_REPLAY_DETECTED`).
   - Asserts database contains exactly 1 token record.
4. `test_statutory_auditor_expired_cop_rejected`:
   - Verifies onboarding an expired COP raises `ValueError`.
   - Verifies lifecycle expiration: an auditor with past `cop_valid_until` is rejected with `EXPIRED_CREDENTIALS`.
5. `test_multi_line_invoice_exception_and_blocked_catering`:
   - Validates multi-line proportional and explicit allocation paths under verified uniform rate rules.
6. `test_statutory_cryptographic_hmac_and_auditor_registry`:
   - Verifies end-to-end cryptographic HMAC-SHA256 authorization token validation with database auditor verification.

---

## 3. FRONTEND PRODUCTION BUILD

- **Compiler:** TypeScript 5.8 + Vite 6.4.6
- **Result:**
  - 1,583 modules compiled cleanly in 26.52s.
  - Distribution output:
    - `dist/index.html` (0.48 kB)
    - `dist/assets/index-CXHqnTlf.css` (29.39 kB)
    - `dist/assets/index-DBkuh5Fw.js` (420.23 kB)
  - Zero TypeScript compile errors.
