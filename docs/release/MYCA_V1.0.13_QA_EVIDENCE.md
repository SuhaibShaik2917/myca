# MyCA v1.0.13 — QA Evidence & Test Execution Report

**Release Target:** `v1.0.13`  
**Parent Commit:** `a64eb6ba82b79c7b39de7eda7594bbbe25c62d2a`  
**Test Suite Summary:** 373 passing backend tests, 25 rollback safety tests, 55 auth token tests, complete RLS audit, and clean frontend build.

---

## 1. Backend Pytest Suite Execution (373 / 373 Passing)

```
============================= test session starts =============================
platform win32 -- Python 3.14.4, pytest-8.4.2, pluggy-1.6.0 -- C:\Python314\python.exe
cachedir: .pytest_cache
rootdir: C:\Users\notyo\.gemini\antigravity\scratch\myca\backend
plugins: anyio-4.13.0, asyncio-0.26.0
asyncio: mode=Mode.STRICT
collecting ... collected 373 items

tests/test_ai_assistant.py ............................................ [  1%]
tests/test_auth_token.py .............................................. [ 15%]
tests/test_business_profiles.py ....................................... [ 18%]
tests/test_credit_builder.py .......................................... [ 25%]
tests/test_documents.py ............................................... [ 28%]
tests/test_financial_summaries.py ..................................... [ 32%]
tests/test_gemini_ocr.py .............................................. [ 35%]
tests/test_gst_records.py ............................................. [ 38%]
tests/test_gstr3b_filings.py .......................................... [ 45%]
tests/test_invoices.py ................................................ [ 52%]
tests/test_itc_assessments.py ......................................... [ 58%]
tests/test_itc_ledger.py .............................................. [ 65%]
tests/test_migration_security_baseline.py ............................. [ 72%]
tests/test_milestone1_security_closure.py ............................. [ 82%]
tests/test_purchase_register.py ....................................... [ 87%]
tests/test_reconciliation.py .......................................... [ 90%]
tests/test_rollback_script_safety.py .................................. [ 93%]
tests/test_scheme_navigator.py ........................................ [ 96%]
tests/test_statutory_auditor_provenance_and_replay_hardening.py ........ [ 98%]
tests/test_statutory_section_17_5_boundaries_and_reference_grants.py ... [100%]

======================= 373 passed, 1 warning in 49.18s =======================
```

---

## 2. Authentication & IdP Nonce Verification (`test_auth_token.py` — 55 Tests)

Key test results for IdP authentication hardening and replay prevention:

- `test_access_token_round_trip_preserves_identity_claims`: PASSED
- `test_invalid_tokens_are_rejected`: PASSED
- `test_bearer_token_identity_overrides_spoofed_headers`: PASSED
- `test_production_requires_bearer_token`: PASSED
- `test_settings_requires_explicit_valid_environment`: PASSED
- `test_settings_prohibits_allow_demo_tokens_in_production`: PASSED
- `test_settings_prohibits_symmetric_algorithms_in_production`: PASSED
- `test_demo_token_prohibited_in_non_local_environments[production]`: PASSED
- `test_demo_token_prohibited_in_non_local_environments[staging]`: PASSED
- `test_demo_token_disabled_by_default_in_local_environments`: PASSED
- `test_idp_token_exchange_fails_closed_in_production_without_trust_anchors`: PASSED
- `test_idp_rejects_unauthorized_provider`: PASSED
- `test_idp_rejects_none_algorithm`: PASSED
- `test_idp_rejects_algorithm_confusion_in_production`: PASSED
- `test_idp_rejects_algorithm_confusion_with_public_key_anchor`: PASSED
- `test_idp_rejects_wrong_issuer`: PASSED
- `test_idp_rejects_wrong_audience`: PASSED
- `test_idp_rejects_expired_token`: PASSED
- `test_idp_rejects_unverified_email`: PASSED
- `test_idp_rejects_untrusted_key_id`: PASSED
- `test_idp_token_exchange_with_authentic_asymmetric_rsa_in_production`: PASSED
- `test_idp_token_exchange_rejects_suspended_user`: PASSED
- `test_idp_token_exchange_rejects_non_member_user`: PASSED
- `test_idp_nonce_endpoint_issues_tracked_nonce`: PASSED
- `test_idp_token_exchange_requires_nonce_claim`: PASSED
- `test_idp_token_exchange_rejects_missing_email_claim`: PASSED
- `test_idp_token_exchange_rejects_invalid_email_format`: PASSED
- `test_idp_token_exchange_rejects_missing_email_verified_claim`: PASSED
- `test_idp_token_exchange_rejects_non_boolean_email_verified`: PASSED (tested strings `"true"`, `"True"`, ints `1`, `0`, non-boolean values)
- `test_idp_token_exchange_rejects_false_email_verified`: PASSED
- `test_idp_token_exchange_rejects_unknown_nonce`: PASSED
- `test_idp_token_exchange_rejects_expired_nonce`: PASSED
- `test_idp_token_exchange_rejects_org_mismatch_nonce`: PASSED
- `test_idp_token_exchange_single_use_prevents_replay`: PASSED
- `test_idp_token_exchange_normalizes_email`: PASSED
- `test_idp_token_exchange_rejects_sub_fallback`: PASSED
- `test_idp_token_exchange_enforces_provider_issuer_binding`: PASSED
- `test_idp_token_exchange_db_failure_fails_closed`: PASSED
- `test_idp_token_exchange_atomic_concurrency_race`: PASSED

---

## 3. Rollback Safety Verification (`test_rollback_script_safety.py` — 25 Tests)

- Safety assertion unit tests: **25 / 25 PASSED**.
- Validated:
  - Rejection of `ENVIRONMENT=production`, `staging`, `development`, unset, empty.
  - Mandatory requirement of `ALLOW_DISPOSABLE_DATABASE_ROLLBACK=true`.
  - Rejection of database name `myca`.
  - Acceptance of designated disposable databases (`myca_test`, `myca_disposable`).
  - Runtime validation of active database connection session name (`SELECT current_database();`).

---

## 4. Frontend Production Build

```
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
✓ built in 49.79s
```
