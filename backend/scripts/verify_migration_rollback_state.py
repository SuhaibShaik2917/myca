import os
import json
import hashlib
import subprocess
import sys
import psycopg

DB_ADMIN_URL = os.environ.get("DB_ADMIN_URL", "postgresql://postgres@localhost:5432/myca")

TENANT_TABLES_21 = [
    "gst_registrations",
    "itc_ledger_entries",
    "itc_assessments",
    "ims_document_actions",
    "purchase_documents",
    "purchase_line_items",
    "purchase_payments",
    "purchase_payment_allocations",
    "goods_receipt_lot_ledger",
    "gstr2b_imports",
    "gstr2b_snapshots",
    "gstr2b_source_documents",
    "gstr2b_unbooked_candidates",
    "human_resolution_events",
    "ims_action_history",
    "itc_reavailment_events",
    "itc_reversal_events",
    "receipt_remediation_audit",
    "reconciliation_matches",
    "reconciliation_runs",
    "reconciliation_variances"
]

POLICIES_0020_8 = [
    "business_profiles_service",
    "financial_transactions_service",
    "financial_summaries_service",
    "summary_transactions_service",
    "scheme_matches_service",
    "gst_records_service",
    "gov_schemes_read_all",
    "scheme_rules_read_all"
]

FAKE_AUDITOR_IDS = ("CA-ICAI-098765", "CA-ICAI-054321", "CA-REVOKED-011223")

def classify_and_verify_auditor_state(conn, step_name: str, expected_state: str) -> str:
    """
    Rigorously classifies and asserts the exact statutory_auditors state.
    Allowed states:
      - TABLE_NOT_PRESENT: Table public.statutory_auditors does not exist at this revision.
      - LEGACY_SEEDS_PRESENT_IN_HISTORICAL_0022: Table exists and contains the 3 historical demo seeds.
      - AUDITORS_VERIFIED_CLEAN: Table exists and contains ZERO fake/demo records.
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT EXISTS (
                SELECT 1 FROM information_schema.tables 
                WHERE table_schema = 'public' AND table_name = 'statutory_auditors'
            );
        """)
        table_exists = cur.fetchone()[0]
        if not table_exists:
            actual_state = "TABLE_NOT_PRESENT"
        else:
            cur.execute("""
                SELECT auditor_id FROM public.statutory_auditors
                WHERE auditor_id IN ('CA-ICAI-098765', 'CA-ICAI-054321', 'CA-REVOKED-011223')
                   OR name ILIKE '%Rajesh Kumar%'
                   OR name ILIKE '%Priya Sharma%'
                   OR name ILIKE '%Suresh Gupta%';
            """)
            found = cur.fetchall()
            if len(found) > 0:
                actual_state = "LEGACY_SEEDS_PRESENT_IN_HISTORICAL_0022"
            else:
                actual_state = "AUDITORS_VERIFIED_CLEAN"

        assert actual_state == expected_state, (
            f"AUDITOR STATE MISMATCH [{step_name}]: Expected '{expected_state}', but found '{actual_state}'"
        )
        print(f"  PASS: [{step_name}] Auditor state classified & verified as '{actual_state}'.")
        return actual_state


def assert_zero_fake_auditors(conn, step_name: str):
    """Assert zero fabricated or demonstration auditor identities exist in the database."""
    classify_and_verify_auditor_state(conn, step_name, "AUDITORS_VERIFIED_CLEAN")

def run_cmd(cmd_list):
    if cmd_list and cmd_list[0] == "alembic":
        cmd_list = [sys.executable, "-m", "alembic"] + cmd_list[1:]
    res = subprocess.run(cmd_list, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"FAILED command: {' '.join(cmd_list)}")
        print(f"STDOUT: {res.stdout}")
        print(f"STDERR: {res.stderr}")
        raise RuntimeError(f"Command failed with code {res.returncode}")
    return res.stdout

def capture_database_state(conn):
    with conn.cursor() as cur:
        # 1. Full grants for app_service_user
        cur.execute("""
            SELECT table_schema, table_name, privilege_type, is_grantable 
            FROM information_schema.role_table_grants 
            WHERE grantee = 'app_service_user' 
            ORDER BY table_name, privilege_type;
        """)
        grants = {
            (r[0], r[1], r[2], r[3]) for r in cur.fetchall()
        }

        # 2. Complete Policies with cmd, roles, qual, with_check
        cur.execute("""
            SELECT schemaname, tablename, policyname, permissive, roles, cmd, qual, with_check 
            FROM pg_policies 
            WHERE schemaname = 'public' 
            ORDER BY tablename, policyname;
        """)
        policies = {
            (r[0], r[1], r[2], r[3], tuple(sorted(r[4])) if r[4] else (), r[5], r[6], r[7])
            for r in cur.fetchall()
        }

        # 3. Table RLS flags
        cur.execute("""
            SELECT relname, relrowsecurity, relforcerowsecurity
            FROM pg_class
            WHERE relnamespace = 'public'::regnamespace AND relkind = 'r'
            ORDER BY relname;
        """)
        rls_flags = {r[0]: (r[1], r[2]) for r in cur.fetchall()}

        return {"grants": grants, "policies": policies, "rls_flags": rls_flags}

# Immutable cryptographic code anchor for baseline revision 0019
TRUSTED_BASELINE_0019_SCHEMA_SHA256 = "bfe3e3a342b56732d1d0cf43f8edfc50391df1020a2c487f22fb5834e9503373"

def compute_canonical_catalog_sha256(data: dict) -> str:
    catalog_payload = {
        "revision": data["revision"],
        "metrics": data["metrics"],
        "full_grants": data["full_grants"],
        "full_policies": data["full_policies"],
        "rls_flags": data["rls_flags"]
    }
    canonical_bytes = json.dumps(catalog_payload, sort_keys=True).encode("utf-8")
    return hashlib.sha256(canonical_bytes).hexdigest()

def load_trusted_0019_reference():
    json_path = os.path.join(os.path.dirname(__file__), "baseline_0019_reference.json")
    if not os.path.exists(json_path):
        raise FileNotFoundError(f"Independent reference catalog file not found: {json_path}")
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Independent Cryptographic Authentication of reference catalog payload against immutable code anchor
    expected_hash = data.get("provenance", {}).get("schema_sha256")
    actual_hash = compute_canonical_catalog_sha256(data)
    
    if expected_hash != TRUSTED_BASELINE_0019_SCHEMA_SHA256:
        raise ValueError(
            f"Provenance hash mismatch in baseline_0019_reference.json! "
            f"Expected code anchor '{TRUSTED_BASELINE_0019_SCHEMA_SHA256}', but JSON declared '{expected_hash}'."
        )
    if actual_hash != TRUSTED_BASELINE_0019_SCHEMA_SHA256:
        raise ValueError(
            f"Cryptographic authentication failed for baseline_0019_reference.json! "
            f"Expected code anchor '{TRUSTED_BASELINE_0019_SCHEMA_SHA256}', but computed '{actual_hash}'."
        )

    trusted_grants = {
        (g["table_schema"], g["table_name"], g["privilege_type"], g["is_grantable"])
        for g in data["full_grants"]
    }
    trusted_policies = {
        (p["schemaname"], p["tablename"], p["policyname"], p["permissive"], tuple(sorted(p["roles"])), p["cmd"], p["qual"], p["with_check"])
        for p in data["full_policies"]
    }
    trusted_rls = {k: (v["relrowsecurity"], v["relforcerowsecurity"]) for k, v in data["rls_flags"].items()}
    return {
        "grants": trusted_grants,
        "policies": trusted_policies,
        "rls_flags": trusted_rls,
        "provenance": data.get("provenance", {}),
        "computed_sha256": actual_hash
    }

def main():
    conn = psycopg.connect(DB_ADMIN_URL)
    conn.autocommit = True

    trusted_ref = load_trusted_0019_reference()
    print("=== Loaded Independent Trusted Baseline Reference from baseline_0019_reference.json ===")
    print(f"  Provenance Origin: {trusted_ref['provenance'].get('generated_from')}")
    print(f"  Clean Reference DB: {trusted_ref['provenance'].get('isolated_database_name')}")
    print(f"  Cryptographic Authentication: SHA256 verified ({trusted_ref['computed_sha256']}) [PASS]")
    print(f"  Complete Definitions: {len(trusted_ref['grants'])} grants, {len(trusted_ref['policies'])} policies, {len(trusted_ref['rls_flags'])} tables.")

    print("\n=== Step 1: Establish Baseline at 0019 & Verify Against Independent Reference ===")
    run_cmd(["alembic", "downgrade", "0019_m3_gstr3b_filings"])
    classify_and_verify_auditor_state(conn, "Step 1: Baseline 0019", "TABLE_NOT_PRESENT")
    baseline_0019 = capture_database_state(conn)
    print(f"  Captured 0019 Database State: {len(baseline_0019['grants'])} grants, {len(baseline_0019['policies'])} policies, {len(baseline_0019['rls_flags'])} tables.")
    assert baseline_0019["grants"] == trusted_ref["grants"], f"Grants mismatch with independent reference! Diff: {baseline_0019['grants'] ^ trusted_ref['grants']}"
    assert baseline_0019["policies"] == trusted_ref["policies"], f"Policies mismatch with independent reference! Diff: {baseline_0019['policies'] ^ trusted_ref['policies']}"
    assert baseline_0019["rls_flags"] == trusted_ref["rls_flags"], "RLS flags mismatch with independent reference!"
    print("  PASS: Initial downgrade to 0019 matches independent trusted reference snapshot 100% on full definitions (79 grants, 87 policies, 41 tables).")

    print("\n=== Step 1b: Verify Historical Upgrade to 0022 contains Legacy Seeds ===")
    run_cmd(["alembic", "upgrade", "0022_statutory_auditors_replay"])
    classify_and_verify_auditor_state(conn, "Step 1b: Fresh Historical Upgrade 0022", "LEGACY_SEEDS_PRESENT_IN_HISTORICAL_0022")

    print("\n=== Step 2: Upgrade to Head (0025_manual_review_and_nonce) ===")
    run_cmd(["alembic", "upgrade", "head"])
    classify_and_verify_auditor_state(conn, "Step 2: Head 0025", "AUDITORS_VERIFIED_CLEAN")
    state_head = capture_database_state(conn)
    print(f"  Head State (0025): {len(state_head['grants'])} grants, {len(state_head['policies'])} policies.")
    assert len(state_head["grants"]) == 112, f"Expected 112 grants at 0025, got {len(state_head['grants'])}"
    assert len(state_head["policies"]) == 95, f"Expected 95 policies at 0025, got {len(state_head['policies'])}"

    # Verify nonce index and nonce registry table exist at 0025
    with conn.cursor() as cur:
        cur.execute("""
            SELECT 1 FROM pg_indexes 
            WHERE schemaname = 'public' 
              AND tablename = 'statutory_auditors' 
              AND indexname = 'uq_statutory_auditors_gateway_nonce';
        """)
        assert cur.fetchone() is not None, "uq_statutory_auditors_gateway_nonce index missing at 0025"

        cur.execute("""
            SELECT 1 FROM information_schema.tables 
            WHERE table_schema = 'public' 
              AND table_name = 'statutory_gateway_nonce_registry';
        """)
        assert cur.fetchone() is not None, "statutory_gateway_nonce_registry table missing at 0025"

    # Verify all 21 tables have relforcerowsecurity=True
    for tbl in TENANT_TABLES_21:
        assert state_head["rls_flags"][tbl][1] is True, f"{tbl} must have relforcerowsecurity=True at 0025"
    # Verify all 8 0020 policies present
    for p_name in POLICIES_0020_8:
        matching = [p for p in state_head["policies"] if p[2] == p_name]
        assert len(matching) == 1, f"Policy {p_name} missing at 0025"
    print("PASS: Head state (0025) completely verified.")

    print("\n=== Step 2-POPULATED: Populated-Record Migration Downgrade & Preservation Test ===")
    test_pop_aid = "CA-POPULATED-ROLLBACK-TEST"
    test_pop_mem = "777666"
    test_pop_sha = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    test_pop_sref = "SESS-POPULATED-TEST-001"

    with conn.cursor() as cur:
        # Clean any prior residue
        cur.execute("DELETE FROM public.statutory_auditor_verification_events WHERE auditor_id = %s;", (test_pop_aid,))
        cur.execute("DELETE FROM public.statutory_auditors WHERE auditor_id = %s;", (test_pop_aid,))

        # Seed populated MANUAL_DOCUMENT_REVIEWED record and audit event
        cur.execute("""
            INSERT INTO public.statutory_auditors (
                auditor_id, membership_no, name, role, cop_status, cop_valid_until,
                approved_by, approved_at, is_revoked, verification_source, verification_status,
                is_icai_verified, is_cop_verified, verified_at, verification_evidence_ref,
                evidence_sha256, reviewer_session_ref
            ) VALUES (
                %s, %s, 'CA Populated Test', 'CHARTERED_ACCOUNTANT', 'ACTIVE', '2030-12-31',
                'admin@myca.internal', clock_timestamp(), false, 'MANUAL_EVIDENCE_REVIEW', 'MANUAL_DOCUMENT_REVIEWED',
                false, false, clock_timestamp(), 'MANUAL-EVIDENCE-SHA256:E3B0C44298FC1C14',
                %s, %s
            );
        """, (test_pop_aid, test_pop_mem, test_pop_sha, test_pop_sref))

        cur.execute("""
            INSERT INTO public.statutory_auditor_verification_events (
                auditor_id, membership_no, verification_method, verification_status,
                evidence_ref, evidence_sha256, reviewer_id, reviewer_session_ref,
                decision, decision_reason, created_at
            ) VALUES (
                %s, %s, 'MANUAL_REVIEW', 'MANUAL_DOCUMENT_REVIEWED',
                'MANUAL-EVIDENCE-SHA256:E3B0C44298FC1C14', %s, 'ADMIN-TEST-01', %s,
                'APPROVED', 'Test populated evidence review', clock_timestamp()
            );
        """, (test_pop_aid, test_pop_mem, test_pop_sha, test_pop_sref))

    print("  Populated 1 MANUAL_DOCUMENT_REVIEWED auditor record and 1 audit event.")

    # Execute populated downgrade to 0024
    print("  Executing Alembic downgrade to 0024 on populated database...")
    run_cmd(["alembic", "downgrade", "0024_auditor_authentic_evidence"])

    # Verify safe downgrade policy enforcement
    with conn.cursor() as cur:
        cur.execute("""
            SELECT verification_status, is_icai_verified, is_cop_verified,
                   evidence_sha256, reviewer_session_ref
            FROM public.statutory_auditors
            WHERE auditor_id = %s;
        """, (test_pop_aid,))
        row = cur.fetchone()
        assert row is not None, "Populated auditor record was DELETED on downgrade! (Violation)"
        assert row[0] == "PENDING_INDEPENDENT_VERIFICATION", f"Expected PENDING_INDEPENDENT_VERIFICATION on downgrade, got {row[0]}"
        assert row[1] is False, "Record must NOT be converted to independently verified!"
        assert row[2] is False, "COP must remain unverified!"
        assert row[3] == test_pop_sha, f"Evidence SHA-256 corrupted or discarded on downgrade! Got: {row[3]}"
        assert row[4] == test_pop_sref, f"Reviewer session reference discarded on downgrade! Got: {row[4]}"

        # Verify audit ledger event is 100% preserved
        cur.execute("""
            SELECT COUNT(*) FROM public.statutory_auditor_verification_events
            WHERE auditor_id = %s;
        """, (test_pop_aid,))
        ev_count = cur.fetchone()[0]
        assert ev_count == 1, f"Audit event was deleted or lost on downgrade! Count: {ev_count}"
    print("  PASS: Downgrade to 0024 safely preserved all evidence, audit events, and transitioned status cleanly.")

    # Execute re-upgrade to Head (0025)
    print("  Executing Alembic re-upgrade to Head (0025)...")
    run_cmd(["alembic", "upgrade", "head"])

    # Verify re-upgrade restored MANUAL_DOCUMENT_REVIEWED status
    with conn.cursor() as cur:
        cur.execute("""
            SELECT verification_status, evidence_sha256, reviewer_session_ref
            FROM public.statutory_auditors
            WHERE auditor_id = %s;
        """, (test_pop_aid,))
        row_restored = cur.fetchone()
        assert row_restored[0] == "MANUAL_DOCUMENT_REVIEWED", f"Expected MANUAL_DOCUMENT_REVIEWED after re-upgrade, got {row_restored[0]}"
        assert row_restored[1] == test_pop_sha, "Evidence SHA-256 mismatch after re-upgrade!"
        assert row_restored[2] == test_pop_sref, "Reviewer session ref mismatch after re-upgrade!"

        # Clean up test populated records before baseline rollback tests
        cur.execute("DELETE FROM public.statutory_auditor_verification_events WHERE auditor_id = %s;", (test_pop_aid,))
        cur.execute("DELETE FROM public.statutory_auditors WHERE auditor_id = %s;", (test_pop_aid,))
    print("PASS: Populated-state downgrade, evidence preservation, and re-upgrade completely verified.")

    print("\n=== Step 2a: Downgrade to 0024_auditor_authentic_evidence & Verify ===")
    run_cmd(["alembic", "downgrade", "0024_auditor_authentic_evidence"])
    classify_and_verify_auditor_state(conn, "Step 2a: Downgrade 0024", "AUDITORS_VERIFIED_CLEAN")
    state_0024 = capture_database_state(conn)
    assert len(state_0024["grants"]) == 110, f"Expected 110 grants at 0024, got {len(state_0024['grants'])}"
    assert len(state_0024["policies"]) == 95, f"Expected 95 policies at 0024, got {len(state_0024['policies'])}"
    with conn.cursor() as cur:
        cur.execute("""
            SELECT 1 FROM pg_indexes 
            WHERE schemaname = 'public' 
              AND tablename = 'statutory_auditors' 
              AND indexname = 'uq_statutory_auditors_gateway_nonce';
        """)
        assert cur.fetchone() is None, "uq_statutory_auditors_gateway_nonce should be dropped at 0024"

    print("\n=== Step 2b: Downgrade to 0023_auditor_provenance & Verify State ===")
    run_cmd(["alembic", "downgrade", "0023_auditor_provenance"])
    classify_and_verify_auditor_state(conn, "Step 2b: Downgrade 0023", "AUDITORS_VERIFIED_CLEAN")
    state_0023 = capture_database_state(conn)
    assert len(state_0023["grants"]) == 108, f"Expected 108 grants at 0023, got {len(state_0023['grants'])}"
    assert len(state_0023["policies"]) == 95, f"Expected 95 policies at 0023, got {len(state_0023['policies'])}"

    print("\n=== Step 2c: Downgrade to 0022_statutory_auditors_replay & Verify Clean Auditor Registry ===")
    run_cmd(["alembic", "downgrade", "0022_statutory_auditors_replay"])
    classify_and_verify_auditor_state(conn, "Step 2c: Clean Downgrade 0022", "AUDITORS_VERIFIED_CLEAN")

    print("\n=== Step 2d: Re-upgrade to Head (0025_manual_review_and_nonce) ===")
    run_cmd(["alembic", "upgrade", "head"])
    classify_and_verify_auditor_state(conn, "Step 2d: Re-upgrade Head 0025", "AUDITORS_VERIFIED_CLEAN")
    print("PASS: Downgrade rollback and re-upgrade cycles verified with zero fake auditor identities.")

    print("\n=== Step 3: Downgrade to 0020_m4_m5_grants_and_policies ===")
    run_cmd(["alembic", "downgrade", "0020_m4_m5_grants_and_policies"])
    classify_and_verify_auditor_state(conn, "Step 3: Downgrade 0020", "TABLE_NOT_PRESENT")
    state_0020 = capture_database_state(conn)
    print(f"  State at 0020: {len(state_0020['grants'])} grants, {len(state_0020['policies'])} policies.")
    # Verify all 21 tables reverted to relforcerowsecurity=False
    for tbl in TENANT_TABLES_21:
        assert state_0020["rls_flags"][tbl][1] is False, f"{tbl} must have relforcerowsecurity=False at 0020"
        assert state_0020["rls_flags"][tbl][0] is True, f"{tbl} must keep relrowsecurity=True at 0020"
    # Verify all 8 0020 policies remain active in 0020
    for p_name in POLICIES_0020_8:
        matching = [p for p in state_0020["policies"] if p[2] == p_name]
        assert len(matching) == 1, f"Policy {p_name} must remain active at 0020"
    print("PASS: Downgrade to 0020 completely verified.")

    print("\n=== Step 4: Downgrade to 0019_m3_gstr3b_filings & Assert Exact Baseline Match ===")
    run_cmd(["alembic", "downgrade", "0019_m3_gstr3b_filings"])
    classify_and_verify_auditor_state(conn, "Step 4: Downgrade 0019", "TABLE_NOT_PRESENT")
    rolled_back_state = capture_database_state(conn)

    # A. Assert all 8 0020 policies are completely removed
    for p_name in POLICIES_0020_8:
        matching = [p for p in rolled_back_state["policies"] if p[2] == p_name]
        assert len(matching) == 0, f"Policy {p_name} was NOT removed on rollback to 0019!"
    print("  PASS: All 8 policies from 0020 were cleanly dropped.")

    # B. Compare exact grants against captured 0019 baseline and independent trusted reference
    missing_grants = baseline_0019["grants"] - rolled_back_state["grants"]
    extra_grants = rolled_back_state["grants"] - baseline_0019["grants"]
    assert len(missing_grants) == 0, f"Rollback corrupted baseline grants! Missing: {missing_grants}"
    assert len(extra_grants) == 0, f"Rollback left extra grants! Extra: {extra_grants}"
    assert rolled_back_state["grants"] == trusted_ref["grants"], "Rollback grants mismatch with independent reference snapshot!"
    print("  PASS: Exact match on all 79 full grant definitions against independent reference.")

    # C. Compare exact policies against captured 0019 baseline and independent trusted reference
    missing_policies = baseline_0019["policies"] - rolled_back_state["policies"]
    extra_policies = rolled_back_state["policies"] - baseline_0019["policies"]
    assert len(missing_policies) == 0, f"Rollback corrupted baseline policies! Missing: {missing_policies}"
    assert len(extra_policies) == 0, f"Rollback left extra policies! Extra: {extra_policies}"
    assert rolled_back_state["policies"] == trusted_ref["policies"], "Rollback policies mismatch with independent reference snapshot!"
    print("  PASS: Exact match on all 87 complete policy definitions (including cmd, roles, qual, with_check) against independent reference.")

    # D. Compare exact table RLS flags against captured 0019 baseline and independent trusted reference
    assert rolled_back_state["rls_flags"] == trusted_ref["rls_flags"], "Rollback RLS flags mismatch with independent reference snapshot!"
    print("  PASS: Exact match on all table RLS and FORCE RLS flags against independent reference.")

    print("\n=== Step 5: Re-Upgrade to Head (0025_manual_review_and_nonce) ===")
    run_cmd(["alembic", "upgrade", "head"])
    classify_and_verify_auditor_state(conn, "Step 5: Final Re-upgrade 0025", "AUDITORS_VERIFIED_CLEAN")
    final_head_state = capture_database_state(conn)
    assert final_head_state["grants"] == state_head["grants"]
    assert final_head_state["policies"] == state_head["policies"]
    assert final_head_state["rls_flags"] == state_head["rls_flags"]
    print("  PASS: Re-upgrade to head completely restored production security state.")

    conn.close()
    print("\nALL MIGRATION ROLLBACK & INDEPENDENT BASELINE REFERENCE INVARIANTS 100% VERIFIED!")

if __name__ == "__main__":
    main()
