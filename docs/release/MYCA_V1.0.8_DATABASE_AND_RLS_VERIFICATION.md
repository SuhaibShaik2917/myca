# MyCA v1.0.8 — Database Schema, Migration & RLS Verification Report

**Date:** November 24, 2026  
**Target Release:** `v1.0.8`  
**Repository:** `SuhaibShaik2917/myca`  
**Database:** PostgreSQL 16 (Port 5432)  
**Database Security State:** **100% VERIFIED SECURE & AUDITED**

---

## 1. MIGRATION REVISION CHAIN & EXACT BASELINE RESTORATION

### 1.1 Revision Lineage

```
0019_m3_gstr3b_filings
       │
       ▼
0020_m4_m5_grants_and_policies
       │
       ▼
0021_fix_tenant_isolation_m1_m5
       │
       ▼
0022_statutory_auditors_replay (Restored to exact v1.0.5 blob: b97ca120d0ccdf3ec2197cab8441dd3741286ceb)
       │
       ▼
0023_statutory_auditor_provenance (Cleanses historical demo seeds; no downgrade re-insertion)
       │
       ▼
0024_auditor_authentic_evidence (Additive v1.0.8 schema: evidence_sha256, verification events ledger) [HEAD]
```

### 1.2 Migration Verification Evidence

- **0022 Exact Blob Hash:**
  ```bash
  $ git hash-object backend/alembic/versions/0022_statutory_auditors_replay.py
  b97ca120d0ccdf3ec2197cab8441dd3741286ceb  # EXACT MATCH
  ```
- **Additive Migration 0024:**
  - Upgrades check constraint `chk_statutory_auditors_verification_source` to include `'MANUAL_EVIDENCE_REVIEW'`.
  - Adds columns `evidence_sha256`, `reviewer_session_ref`, `gateway_kid`, `gateway_response_id` to `public.statutory_auditors`.
  - Creates table `public.statutory_auditor_verification_events` with least-privilege role grants (`SELECT, INSERT` for `app_service_user`).
  - Downgrade safely drops table `public.statutory_auditor_verification_events`, added columns, and restores constraint without data loss.

---

## 2. COMPREHENSIVE MIGRATION ROLLBACK AUDIT

Executed via `backend/scripts/verify_migration_rollback_state.py`:

```
=== Step 1: Establish Baseline at 0019 & Verify Against Independent Reference ===
  PASS: [Step 1: Baseline 0019] Auditor state classified & verified as 'TABLE_NOT_PRESENT'.
  Captured 0019 Database State: 79 grants, 87 policies, 41 tables.
  PASS: Initial downgrade to 0019 matches independent trusted reference snapshot 100% (79 grants, 87 policies, 41 tables).

=== Step 1b: Verify Historical Upgrade to 0022 contains Legacy Seeds ===
  PASS: [Step 1b: Fresh Historical Upgrade 0022] Auditor state classified & verified as 'LEGACY_SEEDS_PRESENT_IN_HISTORICAL_0022'.

=== Step 2: Upgrade to Head (0024_auditor_authentic_evidence) ===
  PASS: [Step 2: Head 0024] Auditor state classified & verified as 'AUDITORS_VERIFIED_CLEAN'.
  Head State (0024): 110 grants, 95 policies.
PASS: Head state (0024) completely verified.

=== Step 2b: Downgrade to 0023_auditor_provenance & Verify State ===
  PASS: [Step 2b: Downgrade 0023] Auditor state classified & verified as 'AUDITORS_VERIFIED_CLEAN'.

=== Step 2c: Downgrade to 0022_statutory_auditors_replay & Verify Clean Auditor Registry ===
  PASS: [Step 2c: Clean Downgrade 0022] Auditor state classified & verified as 'AUDITORS_VERIFIED_CLEAN'.

=== Step 2d: Re-upgrade to Head (0024_auditor_authentic_evidence) ===
  PASS: [Step 2d: Re-upgrade Head 0024] Auditor state classified & verified as 'AUDITORS_VERIFIED_CLEAN'.
PASS: Downgrade rollback and re-upgrade cycles verified with zero fake auditor identities.

=== Step 3: Downgrade to 0020_m4_m5_grants_and_policies ===
  PASS: [Step 3: Downgrade 0020] Auditor state classified & verified as 'TABLE_NOT_PRESENT'.
  State at 0020: 105 grants, 95 policies.
PASS: Downgrade to 0020 completely verified.

=== Step 4: Downgrade to 0019_m3_gstr3b_filings & Assert Exact Baseline Match ===
  PASS: [Step 4: Downgrade 0019] Auditor state classified & verified as 'TABLE_NOT_PRESENT'.
  PASS: All 8 policies from 0020 were cleanly dropped.
  PASS: Exact match on all 79 full grant definitions against independent reference.
  PASS: Exact match on all 87 complete policy definitions against independent reference.
  PASS: Exact match on all table RLS and FORCE RLS flags against independent reference.

=== Step 5: Re-Upgrade to Head (0024_auditor_authentic_evidence) ===
  PASS: [Step 5: Final Re-upgrade 0024] Auditor state classified & verified as 'AUDITORS_VERIFIED_CLEAN'.
  PASS: Re-upgrade to head completely restored production security state.

ALL MIGRATION ROLLBACK & INDEPENDENT BASELINE REFERENCE INVARIANTS 100% VERIFIED!
```

---

## 3. ROW LEVEL SECURITY (RLS) & ACCESS CONTROL AUDIT

Executed via `backend/scripts/audit_rls_security_comprehensive.py`:

1. **Role Security:**
   - `app_service_user`: Superuser=False, BypassRLS=False, CanLogin=True.
2. **Table RLS Configuration:**
   - All 36 application tables have `relrowsecurity = True` and `relforcerowsecurity = True`.
3. **Reference Catalogue Protection:**
   - `statutory_auditors`, `statutory_audit_signature_replays`, `government_schemes`, and `scheme_eligibility_rules` strictly reject INSERT, UPDATE, DELETE under `app_service_user`.
4. **Cross-Tenant Isolation:**
   - 11/11 tenant domains verify zero cross-tenant read leakage, zero cross-tenant update leakage, and immediate rejection of cross-tenant spoofed inserts (`SQLSTATE 42501: RLS_WITH_CHECK_VIOLATION`).
   - Null-tenant context queries return 0 rows across all tenant domains.
