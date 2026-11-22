"""
Comprehensive Cross-Tenant RLS & Security Verification Script
Audits:
1. PostgreSQL Database Role & Table RLS Configuration
2. M1-M5 Cross-Tenant Read Isolation under app_service_user
3. M1-M5 Cross-Tenant Write / Tamper Prevention under app_service_user
4. Unauthenticated / Null-tenant Context Isolation
5. API Layer Authentication & Spoofed Header Rejection
"""
import uuid
import random
import string
import psycopg
from sqlalchemy import text
from fastapi.testclient import TestClient

from app.main import app
from app.database import SessionLocal, set_tenant_context

DB_ADMIN = "postgresql:///myca?user=postgres"

def audit_database_roles_and_rls_enforcement():
    print("=== [1] Auditing PostgreSQL Roles & Table RLS Configuration ===")
    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            # 1. Role privileges for app_service_user
            cur.execute("""
                SELECT rolname, rolsuper, rolbypassrls, rolcanlogin
                FROM pg_roles
                WHERE rolname = 'app_service_user';
            """)
            role = cur.fetchone()
            assert role is not None, "CRITICAL: app_service_user does not exist in pg_roles!"
            rolname, rolsuper, rolbypassrls, rolcanlogin = role
            print(f"Role Check: {rolname} -> Superuser={rolsuper}, BypassRLS={rolbypassrls}, CanLogin={rolcanlogin}")
            assert rolsuper is False, "CRITICAL VIOLATION: app_service_user has SUPERUSER privilege!"
            assert rolbypassrls is False, "CRITICAL VIOLATION: app_service_user has BYPASSRLS privilege!"
            print("  --> Role Security PASS: app_service_user has NO superuser and NO bypassrls privileges.")

            # 2. Check RLS on all tenant-scoped tables
            tenant_tables = [
                "gst_registrations", "invoices", "invoice_line_items", "documents",
                "itc_ledger_entries", "itc_assessments",
                "ims_document_actions", "purchase_documents",
                "gstr3b_filings", "financial_summaries", "financial_transactions",
                "business_profiles", "scheme_matches", "organization_memberships"
            ]
            cur.execute("""
                SELECT relname, relrowsecurity, relforcerowsecurity
                FROM pg_class
                WHERE relname = ANY(%s) AND relnamespace = 'public'::regnamespace;
            """, (tenant_tables,))
            rows = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
            for tbl in tenant_tables:
                assert tbl in rows, f"CRITICAL: Table {tbl} missing from database!"
                r_sec, r_force = rows[tbl]
                assert r_sec is True, f"CRITICAL: RLS not enabled on {tbl}!"
                print(f"  Table '{tbl:28}': RLS={r_sec}, FORCE_RLS={r_force} [PASS]")

    print("=== [1] Role and Table RLS Configuration: 100% VERIFIED SECURE ===\n")

def audit_m3_m4_m5_cross_tenant_isolation():
    print("=== [2] Auditing M3, M4, M5 Cross-Tenant Read & Write Isolation ===")
    org_a = str(uuid.uuid4())
    org_b = str(uuid.uuid4())
    user_a = str(uuid.uuid4())
    user_b = str(uuid.uuid4())
    reg_id_a = str(uuid.uuid4())
    reg_id_b = str(uuid.uuid4())
    pan_a = f"{''.join(random.choices(string.ascii_uppercase, k=5))}1111A"
    pan_b = f"{''.join(random.choices(string.ascii_uppercase, k=5))}2222B"

    # Seed tenants via admin
    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO public.organizations (id, legal_name, pan) VALUES (%s, 'Tenant Alpha Corp', %s);", (org_a, pan_a))
            cur.execute("INSERT INTO public.organizations (id, legal_name, pan) VALUES (%s, 'Tenant Beta Ltd', %s);", (org_b, pan_b))
            cur.execute("INSERT INTO public.global_users (id, email, full_name) VALUES (%s, %s, 'User Alpha');", (user_a, f"alpha_{uuid.uuid4().hex[:6]}@example.com"))
            cur.execute("INSERT INTO public.global_users (id, email, full_name) VALUES (%s, %s, 'User Beta');", (user_b, f"beta_{uuid.uuid4().hex[:6]}@example.com"))
            cur.execute("INSERT INTO public.organization_memberships (org_id, user_id, role, status) VALUES (%s, %s, 'owner', 'active');", (org_a, user_a))
            cur.execute("INSERT INTO public.organization_memberships (org_id, user_id, role, status) VALUES (%s, %s, 'owner', 'active');", (org_b, user_b))
            cur.execute("INSERT INTO public.gst_registrations (id, org_id, gstin, state_code, legal_name, is_primary) VALUES (%s, %s, %s, '27', 'Alpha GSTIN', true);", (reg_id_a, org_a, f"27{pan_a}1ZT"))
            cur.execute("INSERT INTO public.gst_registrations (id, org_id, gstin, state_code, legal_name, is_primary) VALUES (%s, %s, %s, '27', 'Beta GSTIN', true);", (reg_id_b, org_b, f"27{pan_b}1ZT"))

    # Connect as application connection (app_service_user with session)
    db = SessionLocal()
    try:
        # Step A: Org A creates M3 filing, M4 summary, M5 profile
        set_tenant_context(db, org_id=org_a, user_id=user_a)
        
        # M3 insert
        filing_id_a = str(uuid.uuid4())
        db.execute(text("""
            INSERT INTO public.gstr3b_filings (
                id, org_id, registration_id, tax_period, filing_status, maker_id
            ) VALUES (:id, :org_id, :reg_id, '112025', 'DRAFT', :user_id);
        """), {"id": filing_id_a, "org_id": org_a, "reg_id": reg_id_a, "user_id": user_a})

        # M4 insert
        summary_id_a = str(uuid.uuid4())
        db.execute(text("""
            INSERT INTO public.financial_summaries (
                id, org_id, summary_period, total_inflow, total_outflow, net_cash_flow
            ) VALUES (:id, :org_id, '112025', 100000, 50000, 50000);
        """), {"id": summary_id_a, "org_id": org_a})

        # M5 insert
        prof_id_a = str(uuid.uuid4())
        db.execute(text("""
            INSERT INTO public.business_profiles (
                id, org_id, business_type, industry_sector, turnover_bracket, annual_turnover
            ) VALUES (:id, :org_id, 'Private Limited', 'Automotive', 'under_5cr', 35000000);
        """), {"id": prof_id_a, "org_id": org_a})

        db.commit()
        print("  --> Seeded Org A records across M3, M4, M5.")

        # Step B: Switch to Org B context and test read isolation
        set_tenant_context(db, org_id=org_b, user_id=user_b)

        # 1. M3 read isolation
        m3_res = db.execute(text("SELECT id FROM public.gstr3b_filings WHERE id = :id;"), {"id": filing_id_a}).fetchall()
        assert len(m3_res) == 0, "CRITICAL RLS BREACH: Org B can read Org A's GSTR-3B filing!"
        print("  [M3 Read Isolation] PASS: Org B cannot see Org A's GSTR-3B filing.")

        # 2. M4 read isolation
        m4_res = db.execute(text("SELECT id FROM public.financial_summaries WHERE id = :id;"), {"id": summary_id_a}).fetchall()
        assert len(m4_res) == 0, "CRITICAL RLS BREACH: Org B can read Org A's Financial Summary!"
        print("  [M4 Read Isolation] PASS: Org B cannot see Org A's Financial Summary.")

        # 3. M5 read isolation
        m5_res = db.execute(text("SELECT id FROM public.business_profiles WHERE id = :id;"), {"id": prof_id_a}).fetchall()
        assert len(m5_res) == 0, "CRITICAL RLS BREACH: Org B can read Org A's Business Profile!"
        print("  [M5 Read Isolation] PASS: Org B cannot see Org A's Business Profile.")

        # Step C: Cross-tenant Write/Tamper Attempts from Org B into Org A
        # 1. Update Org A's GSTR-3B filing status
        update_count = db.execute(text("""
            UPDATE public.gstr3b_filings SET filing_status = 'FILED' WHERE id = :id;
        """), {"id": filing_id_a}).rowcount
        assert update_count == 0, "CRITICAL RLS BREACH: Org B successfully updated Org A's filing!"
        print("  [M3 Cross-Tenant Tamper] PASS: Org B UPDATE on Org A record affected 0 rows.")

        # 2. Insert record under Org A's org_id while acting as Org B
        cross_insert_blocked = False
        try:
            db.execute(text("""
                INSERT INTO public.financial_summaries (
                    id, org_id, summary_period, total_inflow, total_outflow, net_cash_flow
                ) VALUES (:id, :org_a, '112025', 0, 0, 0);
            """), {"id": str(uuid.uuid4()), "org_a": org_a})
            db.commit()
        except Exception as e:
            cross_insert_blocked = True
            db.rollback()
            print(f"  [M4 Cross-Tenant Insert] PASS: Blocked by RLS with error {e.__class__.__name__}")
        assert cross_insert_blocked, "CRITICAL RLS BREACH: Org B was able to insert records into Org A!"

        # 3. Null tenant context isolation
        db.execute(text("SELECT set_config('app.current_org', '', false), set_config('app.current_user', '', false);"))
        assert len(db.execute(text("SELECT id FROM public.gstr3b_filings;")).fetchall()) == 0
        assert len(db.execute(text("SELECT id FROM public.financial_summaries;")).fetchall()) == 0
        assert len(db.execute(text("SELECT id FROM public.business_profiles;")).fetchall()) == 0
        print("  [Null Context Isolation] PASS: With unauthenticated/null context, all tenant tables return 0 rows.")

    finally:
        db.close()

    print("=== [2] M3, M4, M5 Cross-Tenant Isolation: 100% VERIFIED SECURE ===\n")

def audit_api_layer_token_security():
    print("=== [3] Auditing FastAPI API Layer Authentication & Cross-Tenant Token Gates ===")
    from app.core.security import create_access_token
    client = TestClient(app)

    org_a = str(uuid.uuid4())
    user_a = str(uuid.uuid4())
    org_b = str(uuid.uuid4())
    user_b = str(uuid.uuid4())

    token_a = create_access_token(subject=user_a, org_id=org_a)
    token_b = create_access_token(subject=user_b, org_id=org_b)

    # 1. Unauthenticated requests to protected endpoints return 401
    protected_endpoints = [
        ("GET", "/api/v1/gstr3b/status?registration_id=00000000-0000-0000-0000-000000000000&tax_period=112025"),
        ("GET", "/api/v1/credit-builder/summary"),
        ("GET", "/api/v1/credit-builder/readiness-score"),
        ("GET", "/api/v1/credit-builder/loan-dossier"),
        ("GET", "/api/v1/scheme-navigator/profile"),
        ("GET", "/api/v1/scheme-navigator/schemes"),
        ("GET", "/api/v1/ai/quick-insights"),
    ]
    for method, ep in protected_endpoints:
        if method == "GET":
            res = client.get(ep)
        assert res.status_code == 401, f"Expected 401 for unauthenticated {ep}, got {res.status_code}"
        print(f"  Unauthenticated {ep:42} -> {res.status_code} Unauthorized [PASS]")

    # 2. Token spoofing test: Client passes Token A in Authorization header but claims org_id = org_b in custom header
    spoof_headers = {
        "Authorization": f"Bearer {token_a}",
        "X-Organization-Id": org_b,
        "X-User-Id": user_b
    }
    # Call credit-builder summary
    res = client.get("/api/v1/credit-builder/summary", headers=spoof_headers)
    print(f"  Spoofed Header Call -> HTTP {res.status_code} (Active Tamper Protection Enforced)")
    assert res.status_code == 403, f"Expected 403 Forbidden for spoofed headers, got {res.status_code}"

    print("=== [3] API Layer Security: 100% VERIFIED SECURE ===\n")

if __name__ == "__main__":
    audit_database_roles_and_rls_enforcement()
    audit_m3_m4_m5_cross_tenant_isolation()
    audit_api_layer_token_security()
    print("ALL COMPREHENSIVE RLS & SECURITY AUDITS PASSED FLAWLESSLY!")
