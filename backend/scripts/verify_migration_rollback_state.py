import os
import json
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

def load_trusted_0019_reference():
    json_path = os.path.join(os.path.dirname(__file__), "baseline_0019_reference.json")
    if not os.path.exists(json_path):
        raise FileNotFoundError(f"Independent reference catalog file not found: {json_path}")
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
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
        "provenance": data.get("provenance", {})
    }

def main():
    conn = psycopg.connect(DB_ADMIN_URL)
    conn.autocommit = True

    trusted_ref = load_trusted_0019_reference()
    print("=== Loaded Independent Trusted Baseline Reference from baseline_0019_reference.json ===")
    print(f"  Provenance Origin: {trusted_ref['provenance'].get('generated_from')}")
    print(f"  Clean Reference DB: {trusted_ref['provenance'].get('isolated_database_name')}")
    print(f"  Schema SHA256: {trusted_ref['provenance'].get('schema_sha256')}")
    print(f"  Complete Definitions: {len(trusted_ref['grants'])} grants, {len(trusted_ref['policies'])} policies, {len(trusted_ref['rls_flags'])} tables.")

    print("\n=== Step 1: Establish Baseline at 0019 & Verify Against Independent Reference ===")
    run_cmd(["alembic", "downgrade", "0019_m3_gstr3b_filings"])
    baseline_0019 = capture_database_state(conn)
    print(f"  Captured 0019 Database State: {len(baseline_0019['grants'])} grants, {len(baseline_0019['policies'])} policies, {len(baseline_0019['rls_flags'])} tables.")
    assert baseline_0019["grants"] == trusted_ref["grants"], f"Grants mismatch with independent reference! Diff: {baseline_0019['grants'] ^ trusted_ref['grants']}"
    assert baseline_0019["policies"] == trusted_ref["policies"], f"Policies mismatch with independent reference! Diff: {baseline_0019['policies'] ^ trusted_ref['policies']}"
    assert baseline_0019["rls_flags"] == trusted_ref["rls_flags"], "RLS flags mismatch with independent reference!"
    print("  PASS: Initial downgrade to 0019 matches independent trusted reference snapshot 100% on full definitions (79 grants, 87 policies, 41 tables).")

    print("\n=== Step 2: Upgrade to Head (0021_enforce_tenant_force_rls) ===")
    run_cmd(["alembic", "upgrade", "head"])
    state_0021 = capture_database_state(conn)
    print(f"  Head State (0021): {len(state_0021['grants'])} grants, {len(state_0021['policies'])} policies.")
    assert len(state_0021["grants"]) == 105, f"Expected 105 grants at 0021, got {len(state_0021['grants'])}"
    assert len(state_0021["policies"]) == 95, f"Expected 95 policies at 0021, got {len(state_0021['policies'])}"

    # Verify all 21 tables have relforcerowsecurity=True
    for tbl in TENANT_TABLES_21:
        assert state_0021["rls_flags"][tbl][1] is True, f"{tbl} must have relforcerowsecurity=True at 0021"
    # Verify all 8 0020 policies present
    for p_name in POLICIES_0020_8:
        matching = [p for p in state_0021["policies"] if p[2] == p_name]
        assert len(matching) == 1, f"Policy {p_name} missing at 0021"
    print("PASS: Head state (0021) completely verified.")

    print("\n=== Step 3: Downgrade to 0020_m4_m5_grants_and_policies ===")
    run_cmd(["alembic", "downgrade", "0020_m4_m5_grants_and_policies"])
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

    print("\n=== Step 5: Re-Upgrade to Head (0021_enforce_tenant_force_rls) ===")
    run_cmd(["alembic", "upgrade", "head"])
    final_head_state = capture_database_state(conn)
    assert final_head_state["grants"] == state_0021["grants"]
    assert final_head_state["policies"] == state_0021["policies"]
    assert final_head_state["rls_flags"] == state_0021["rls_flags"]
    print("  PASS: Re-upgrade to head completely restored production security state.")

    conn.close()
    print("\nALL MIGRATION ROLLBACK & INDEPENDENT BASELINE REFERENCE INVARIANTS 100% VERIFIED!")

if __name__ == "__main__":
    main()
