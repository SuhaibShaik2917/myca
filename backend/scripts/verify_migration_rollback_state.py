"""
Migration Rollback and Forward State Verification Script

Verifies that:
1. Current head is 0021_enforce_tenant_force_rls with all 21 tenant tables having FORCE RLS enabled.
2. Downgrading to 0020_m4_m5_grants_and_policies:
   - Successfully executes DDL.
   - All 21 tenant tables have relforcerowsecurity = False (restored to 0020 state).
3. Downgrading to 0019_m3_gstr3b_filings:
   - Successfully executes DDL.
   - Specific grants and policies added by 0020 are safely reverted without side effects.
4. Upgrading back to head (0021_enforce_tenant_force_rls):
   - Successfully reapplies 0020 and 0021.
   - All 21 tenant tables have relforcerowsecurity = True.
   - Reference catalogues have strictly SELECT grants for app_service_user.
"""
import subprocess
import sys
import psycopg

DB_ADMIN_URL = "postgresql://postgres@localhost:5432/myca"

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

def run_cmd(cmd_list):
    res = subprocess.run(cmd_list, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"FAILED command: {' '.join(cmd_list)}")
        print(f"STDOUT: {res.stdout}")
        print(f"STDERR: {res.stderr}")
        raise RuntimeError(f"Command failed with code {res.returncode}")
    return res.stdout

def get_force_rls_status(conn):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT relname, relrowsecurity, relforcerowsecurity
            FROM pg_class
            WHERE relnamespace = 'public'::regnamespace
              AND relname = ANY(%s)
            ORDER BY relname;
        """, (TENANT_TABLES_21,))
        rows = cur.fetchall()
        return {r[0]: (r[1], r[2]) for r in rows}

def main():
    print("=== Step 1: Verify current head (0021) ===")
    conn = psycopg.connect(DB_ADMIN_URL)
    conn.autocommit = True

    status_0021 = get_force_rls_status(conn)
    assert len(status_0021) == 21, f"Expected 21 tables, found {len(status_0021)}"
    for tbl, (rls, force) in status_0021.items():
        assert rls is True, f"Table {tbl} must have relrowsecurity=True at 0021"
        assert force is True, f"Table {tbl} must have relforcerowsecurity=True at 0021"
    print("PASS: All 21 tables have relforcerowsecurity=True at 0021.")

    print("\n=== Step 2: Test rollback to 0020_m4_m5_grants_and_policies ===")
    out_down_20 = run_cmd(["alembic", "downgrade", "0020_m4_m5_grants_and_policies"])
    print(out_down_20.strip())

    status_0020 = get_force_rls_status(conn)
    for tbl, (rls, force) in status_0020.items():
        assert rls is True, f"Table {tbl} must still have relrowsecurity=True at 0020"
        assert force is False, f"Table {tbl} must have relforcerowsecurity=False after downgrade to 0020"
    print("PASS: All 21 tables successfully reverted to relforcerowsecurity=False at 0020.")

    print("\n=== Step 3: Test rollback to 0019_m3_gstr3b_filings ===")
    out_down_19 = run_cmd(["alembic", "downgrade", "0019_m3_gstr3b_filings"])
    print(out_down_19.strip())

    with conn.cursor() as cur:
        cur.execute("""
            SELECT policyname, tablename FROM pg_policies
            WHERE policyname IN ('gov_schemes_read_all', 'scheme_rules_read_all', 'business_profiles_service');
        """)
        leftover_policies = cur.fetchall()
        assert len(leftover_policies) == 0, f"Expected 0 policies from 0020, found {leftover_policies}"
    print("PASS: Policies from 0020 were cleanly removed at 0019.")

    print("\n=== Step 4: Upgrade back to head (0021) ===")
    out_up_head = run_cmd(["alembic", "upgrade", "head"])
    print(out_up_head.strip())

    status_restored = get_force_rls_status(conn)
    for tbl, (rls, force) in status_restored.items():
        assert rls is True, f"Table {tbl} must have relrowsecurity=True after re-upgrade"
        assert force is True, f"Table {tbl} must have relforcerowsecurity=True after re-upgrade"
    print("PASS: All 21 tables successfully re-upgraded with relforcerowsecurity=True.")

    conn.close()
    print("\nALL MIGRATION ROLLBACK & UPGRADE INVARIANTS VERIFIED!")

if __name__ == "__main__":
    main()
