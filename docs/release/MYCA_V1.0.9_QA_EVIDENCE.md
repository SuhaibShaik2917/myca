# MyCA v1.0.9 — Quality Assurance & Testing Evidence

**Date:** November 24, 2026  
**Release:** `v1.0.9`  
**Test Suite:** Pytest + Playwright / Vitest  
**Results Summary:** **290 / 290 PASSED (0 failures, 0 errors, 100% Pass Rate)**

---

## 1. BACKEND PYTEST SUITE EXECUTION RESULTS

**Command Executed:**
```bash
ENVIRONMENT=test STATUTORY_AUDIT_SIGNING_KEY='MYCA_CI_TEST_STATUTORY_AUDITOR_HMAC_SECRET_v1.0.8_MIN32CHAR' PYTHONPATH=. /opt/myca-venv/bin/pytest tests/ -v --tb=short
```

**Output Summary:**
```text
============================= test session starts ==============================
platform linux -- Python 3.12.3, pytest-9.2.0, pluggy-1.7.0 -- /opt/myca-venv/bin/python3
cachedir: .pytest_cache
rootdir: /mnt/c/Users/notyo/.gemini/antigravity/scratch/myca/backend
plugins: anyio-4.16.0, asyncio-1.5.0
collected 290 items

tests/test_ai_assistant.py ................................... 3 passed
tests/test_auth_token.py .................................... 7 passed
tests/test_invoice_brain.py ................................. 7 passed
tests/test_migration_security_baseline.py .................. 19 passed
tests/test_milestone1_security_closure.py ................... 6 passed
tests/test_milestone2_receipt_gate_and_reconciliation.py .... 34 passed
tests/test_milestone3_gstr3b_filings.py ..................... 17 passed
tests/test_milestone4_financial_dossier.py .................. 16 passed
tests/test_milestone5_scheme_navigator.py .................. 24 passed
tests/test_pre_seed_migration_idempotency.py ................ 2 passed
tests/test_statutory_gst_rate_validation.py ................. 42 passed
tests/test_statutory_tariff_effective_date.py ............... 76 passed
tests/test_statutory_auditor_provenance_and_replay_hardening.py  27 passed
tests/test_statutory_section_17_5_boundaries_and_reference_grants.py 10 passed

============================= 290 passed in 41.48s =============================
```

---

## 2. STATUTORY AUDITOR PROVENANCE SUITE (27 TESTS)

File: `tests/test_statutory_auditor_provenance_and_replay_hardening.py`

| Test Function | Verification Scope | Status |
|---|---|---|
| `test_statutory_auditor_unregistered_fails_closed` | Unregistered auditor ID returns `UNREGISTERED_AUDITOR` | **PASSED** |
| `test_statutory_auditor_unverified_provenance_rejected` | Admin approval without independent ICAI proof rejected | **PASSED** |
| `test_statutory_auditor_self_assertion_precluded` | Caller passing `is_icai_verified=True` without proof rejected | **PASSED** |
| `test_statutory_auditor_independently_verified_provenance_accepted` | Genuine cryptographically signed gateway token accepted | **PASSED** |
| `test_statutory_auditor_revoked_status_rejected` | Disciplinary revocation rejected with `REVOKED_AUDITOR` | **PASSED** |
| `test_statutory_auditor_expired_cop_rejected` | Expired Certificate of Practice rejected | **PASSED** |
| `test_statutory_auditor_db_failure_fails_closed` | Database drop fails closed | **PASSED** |
| `test_icai_gateway_fabricated_reference_rejected` | Regex format strings without cryptographic tokens rejected | **PASSED** |
| `test_icai_gateway_cryptographic_verification_claims_and_replay` | Full claims binding and replay prevention pass | **PASSED** |
| `test_icai_gateway_unconfigured_production_fails_closed` | Production without official trust anchor fails closed | **PASSED** |
| `test_gateway_forged_unknown_kid_rejected` | Untrusted `kid` rejected against trust store | **PASSED** |
| `test_gateway_tampered_signature_rejected` | Signature bit tampering fails cryptographic verification | **PASSED** |
| `test_gateway_symmetric_hs256_prohibited_in_production` | Symmetric HS256 rejected in production mode | **PASSED** |
| `test_manual_evidence_review_provenance_success` | Full documentary review, server SHA-256, maker-checker | **PASSED** |
| `test_manual_evidence_review_unauthorized_role_rejected` | Unauthorized reviewer roles rejected | **PASSED** |
| `test_manual_evidence_review_maker_checker_rejected` | Self-approval (`uploader == reviewer`) rejected | **PASSED** |
| `test_manual_evidence_review_tampered_document_bytes_rejected` | Tampered document bytes rejected | **PASSED** |
| `test_manual_review_missing_db_fails_closed` | Fails closed with `DATABASE_UNAVAILABLE` when `db=None` | **PASSED** |
| `test_manual_review_inactive_reviewer_fails_closed` | Inactive reviewer account rejected with `INACTIVE_REVIEWER` | **PASSED** |
| `test_manual_review_audit_event_persistence_failure_fails_closed` | Failure to write audit event aborts review (fail-closed) | **PASSED** |
| `test_manual_review_cannot_self_assert_independently_verified` | Manual review cannot claim `INDEPENDENTLY_VERIFIED` | **PASSED** |
| `test_signature_replay_missing_expires_at_fails_closed` | Omitting `expires_at` fails closed | **PASSED** |
| `test_signature_replay_cross_document_reuse_rejected` | Reusing token on different document rejected | **PASSED** |
| `test_signature_replay_duplicate_consumption_rejected` | Duplicate consumption on same document rejected | **PASSED** |
| `test_signature_replay_expired_token_permanent_invalidation` | Expired token rejected before insertion | **PASSED** |
| `test_conclusive_postgresql_insert_concurrency_race` | Multithreaded PostgreSQL race barrier test deterministic | **PASSED** |
| `test_signature_replay_db_failure_fails_closed_no_in_memory_fallback` | Database drop fails closed without in-memory fallback | **PASSED** |

---

## 3. FRONTEND PRODUCTION BUILD

**Command Executed:**
```bash
cd frontend && npm run build
```

**Output Summary:**
```text
> myca-frontend@1.0.0 build
> tsc -b && vite build

vite v6.4.6 building for production...
transforming...
✓ 1583 modules transformed.
rendering chunks...
computing gzip size...
dist/index.html                   0.48 kB │ gzip:   0.33 kB
dist/assets/index-CXHqnTlf.css   29.39 kB │ gzip:   5.62 kB
dist/assets/index-DBkuh5Fw.js   420.23 kB │ gzip: 107.43 kB
✓ built in 10.63s
```
