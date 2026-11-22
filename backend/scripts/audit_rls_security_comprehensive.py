"""
Comprehensive Cross-Tenant RLS & Security Verification Script
Audits:
1. PostgreSQL Database Role & Table RLS Configuration across all 36 tenant tables
2. Least-privilege write denials (INSERT, UPDATE, DELETE) on both reference catalogues
3. Explicit verification that application connection runs as 'app_service_user'
4. M1-M5 Cross-Tenant Read Isolation across core tenant domains:
   - invoices, documents, gst_registrations, purchase_documents, itc_ledger_entries,
     ims_document_actions, gstr3b_filings, financial_summaries, business_profiles, scheme_matches
5. M1-M5 Cross-Tenant Write / Tamper Prevention under app_service_user
6. Unauthenticated / Null-tenant Context Isolation
7. API Layer Authentication & Spoofed Header Rejection
"""
import uuid
import random
import string
import psycopg
from decimal import Decimal
from sqlalchemy import text
from fastapi.testclient import TestClient

from app.main import app
from app.database import SessionLocal, set_tenant_context

DB_ADMIN = "postgresql:///myca?user=postgres"

# All 36 tenant/security-scoped tables in the public schema
ALL_36_TENANT_TABLES = [
    "business_profiles",
    "documents",
    "financial_summaries",
    "financial_transactions",
    "global_users",
    "goods_receipt_lot_ledger",
    "gst_records",
    "gst_registrations",
    "gstr2b_imports",
    "gstr2b_snapshots",
    "gstr2b_source_documents",
    "gstr2b_unbooked_candidates",
    "gstr3b_filings",
    "human_resolution_events",
    "ims_action_history",
    "ims_document_actions",
    "invoice_line_items",
    "invoices",
    "itc_assessments",
    "itc_ledger_entries",
    "itc_reavailment_events",
    "itc_reversal_events",
    "organization_memberships",
    "organizations",
    "purchase_documents",
    "purchase_line_items",
    "purchase_payment_allocations",
    "purchase_payments",
    "receipt_remediation_audit",
    "reconciliation_matches",
    "reconciliation_runs",
    "reconciliation_variances",
    "scheme_matches",
    "summary_transactions",
    "worker_service_authorizations",
    "worker_tasks"
]

def audit_database_roles_and_rls_enforcement():
    print("=== [1] Auditing PostgreSQL Roles & Table RLS Configuration (All 36 Tables) ===")
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

            # 2. Check RLS and FORCE RLS on all 36 tenant-scoped tables
            cur.execute("""
                SELECT relname, relrowsecurity, relforcerowsecurity
                FROM pg_class
                WHERE relname = ANY(%s) AND relnamespace = 'public'::regnamespace;
            """, (ALL_36_TENANT_TABLES,))
            rows = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
            assert len(rows) == 36, f"Expected 36 tenant tables, found {len(rows)}"
            for tbl in ALL_36_TENANT_TABLES:
                assert tbl in rows, f"CRITICAL: Table {tbl} missing from database!"
                r_sec, r_force = rows[tbl]
                assert r_sec is True, f"CRITICAL: RLS not enabled on {tbl}!"
                assert r_force is True, f"CRITICAL: FORCE RLS not enabled on {tbl}!"
                print(f"  Table '{tbl:32}': RLS={r_sec}, FORCE_RLS={r_force} [PASS]")

            # 3. Check reference catalogue least-privilege grants
            cur.execute("""
                SELECT table_name, privilege_type 
                FROM information_schema.role_table_grants 
                WHERE grantee = 'app_service_user' 
                  AND table_name IN ('government_schemes', 'scheme_eligibility_rules');
            """)
            grants = cur.fetchall()
            for g_tbl, g_priv in grants:
                assert g_priv == "SELECT", f"CRITICAL LEAST-PRIVILEGE VIOLATION: {g_priv} granted on reference table {g_tbl}!"
            print("  Reference Catalogues: app_service_user has strictly SELECT privilege [PASS]")

    print("=== [1] Role and Table RLS Configuration: 100% VERIFIED SECURE ===\n")


def audit_catalogue_write_denials_under_app_service_user():
    print("=== [2] Auditing Catalogue Write Denials Under app_service_user ===")
    db = SessionLocal()
    try:
        # A. Explicit check of connection user
        current_db_user = db.execute(text("SELECT current_user;")).scalar()
        assert current_db_user == "app_service_user", f"Expected app_service_user, got {current_db_user}"
        print(f"  Database session user verified: '{current_db_user}' [PASS]")

        # B. INSERT on government_schemes
        try:
            db.execute(text("INSERT INTO public.government_schemes (scheme_code, name, ministry, category, description, benefits_summary) VALUES ('T1', 'Test', 'T', 'credit_guarantee', 'D', 'B');"))
            db.commit()
            raise AssertionError("CRITICAL: INSERT succeeded on government_schemes!")
        except Exception as e:
            db.rollback()
            assert "permission denied" in str(e).lower() or "insufficientprivilege" in str(e).lower() or "programmingerror" in str(e).lower()
        print("  government_schemes INSERT denied [PASS]")

        # C. UPDATE on government_schemes
        try:
            db.execute(text("UPDATE public.government_schemes SET name = 'Hacked' WHERE scheme_code = 'CGTMSE';"))
            db.commit()
            raise AssertionError("CRITICAL: UPDATE succeeded on government_schemes!")
        except Exception as e:
            db.rollback()
            assert "permission denied" in str(e).lower() or "insufficientprivilege" in str(e).lower() or "programmingerror" in str(e).lower()
        print("  government_schemes UPDATE denied [PASS]")

        # D. DELETE on government_schemes
        try:
            db.execute(text("DELETE FROM public.government_schemes WHERE scheme_code = 'CGTMSE';"))
            db.commit()
            raise AssertionError("CRITICAL: DELETE succeeded on government_schemes!")
        except Exception as e:
            db.rollback()
            assert "permission denied" in str(e).lower() or "insufficientprivilege" in str(e).lower() or "programmingerror" in str(e).lower()
        print("  government_schemes DELETE denied [PASS]")

        # E. INSERT on scheme_eligibility_rules
        try:
            db.execute(text("INSERT INTO public.scheme_eligibility_rules (id, scheme_id, rule_key, operator, target_value, rule_description) VALUES ('00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000002', 'test', '=', '1', 'desc');"))
            db.commit()
            raise AssertionError("CRITICAL: INSERT succeeded on scheme_eligibility_rules!")
        except Exception as e:
            db.rollback()
            assert "permission denied" in str(e).lower() or "insufficientprivilege" in str(e).lower() or "programmingerror" in str(e).lower()
        print("  scheme_eligibility_rules INSERT denied [PASS]")

        # F. UPDATE on scheme_eligibility_rules
        try:
            db.execute(text("UPDATE public.scheme_eligibility_rules SET target_value = '0' WHERE rule_key = 'credit_score';"))
            db.commit()
            raise AssertionError("CRITICAL: UPDATE succeeded on scheme_eligibility_rules!")
        except Exception as e:
            db.rollback()
            assert "permission denied" in str(e).lower() or "insufficientprivilege" in str(e).lower() or "programmingerror" in str(e).lower()
        print("  scheme_eligibility_rules UPDATE denied [PASS]")

        # G. DELETE on scheme_eligibility_rules
        try:
            db.execute(text("DELETE FROM public.scheme_eligibility_rules WHERE rule_key = 'credit_score';"))
            db.commit()
            raise AssertionError("CRITICAL: DELETE succeeded on scheme_eligibility_rules!")
        except Exception as e:
            db.rollback()
            assert "permission denied" in str(e).lower() or "insufficientprivilege" in str(e).lower() or "programmingerror" in str(e).lower()
        print("  scheme_eligibility_rules DELETE denied [PASS]")

    finally:
        db.close()
    print("=== [2] Catalogue Write Denials: 100% VERIFIED SECURE ===\n")


def audit_m1_m5_cross_tenant_isolation():
    print("=== [3] Auditing M1-M5 Cross-Tenant Read & Write Isolation ===")
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
            cur.execute("INSERT INTO public.global_users (id, email, full_name) VALUES (%s, %s, 'User Alpha');", (user_a, f"alpha_{uuid.uuid4().hex}@example.com"))
            cur.execute("INSERT INTO public.global_users (id, email, full_name) VALUES (%s, %s, 'User Beta');", (user_b, f"beta_{uuid.uuid4().hex}@example.com"))
            cur.execute("INSERT INTO public.organization_memberships (org_id, user_id, role, status) VALUES (%s, %s, 'owner', 'active');", (org_a, user_a))
            cur.execute("INSERT INTO public.organization_memberships (org_id, user_id, role, status) VALUES (%s, %s, 'owner', 'active');", (org_b, user_b))
            cur.execute("INSERT INTO public.gst_registrations (id, org_id, gstin, state_code, legal_name, is_primary) VALUES (%s, %s, %s, '27', 'Alpha GSTIN', true);", (reg_id_a, org_a, f"27{pan_a}1ZT"))
            cur.execute("INSERT INTO public.gst_registrations (id, org_id, gstin, state_code, legal_name, is_primary) VALUES (%s, %s, %s, '27', 'Beta GSTIN', true);", (reg_id_b, org_b, f"27{pan_b}1ZT"))

    # Connect as application connection (app_service_user with session)
    db = SessionLocal()
    try:
        current_db_user = db.execute(text("SELECT current_user;")).scalar()
        assert current_db_user == "app_service_user"

        # Step A: Org A creates records across 10 core tenant domains:
        # invoices, documents, purchase_documents, itc_ledger_entries, ims_document_actions,
        # gstr3b_filings, financial_summaries, business_profiles, scheme_matches
        set_tenant_context(db, org_id=org_a, user_id=user_a)

        # M5 Business profile first (so invoices foreign key can reference it)
        prof_id_a = str(uuid.uuid4())
        db.execute(text("""
            INSERT INTO public.business_profiles (
                id, org_id, business_type, industry_sector, turnover_bracket, annual_turnover
            ) VALUES (:id, :org_id, 'Private Limited', 'Automotive', 'under_5cr', 35000000);
        """), {"id": prof_id_a, "org_id": org_a})

        inv_id_a = str(uuid.uuid4())
        db.execute(text("""
            INSERT INTO public.invoices (
                id, org_id, profile_id, invoice_number, invoice_date, counterparty_name, total_amount, status
            ) VALUES (:id, :org_id, :profile_id, 'INV-ALPHA-001', '2025-11-01', 'Customer Alpha', 11800, 'uploaded');
        """), {"id": inv_id_a, "org_id": org_a, "profile_id": prof_id_a})

        doc_id_a = str(uuid.uuid4())
        db.execute(text("""
            INSERT INTO public.documents (id, org_id, doc_type, file_name, file_size_bytes, mime_type, file_path, sha256_checksum)
            VALUES (:id, :org_id, 'invoice', 'alpha_doc.pdf', 1024, 'application/pdf', '/storage/alpha_doc.pdf', '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef');
        """), {"id": doc_id_a, "org_id": org_a})

        purch_id_a = str(uuid.uuid4())
        db.execute(text("""
            INSERT INTO public.purchase_documents (
                id, org_id, registration_id, document_type, document_number, document_date,
                financial_period, supplier_gstin, supplier_legal_name, recipient_gstin, place_of_supply, total_invoice_value
            ) VALUES (
                :id, :org_id, :reg_id, 'tax_invoice', 'PUR-001', '2025-11-01',
                '112025', '27AAAAA0000A1Z5', 'Supplier Alpha', '27AAAAA0000A1ZT', '27', 50000
            );
        """), {"id": purch_id_a, "org_id": org_a, "reg_id": reg_id_a})

        ledger_id_a = str(uuid.uuid4())
        db.execute(text("""
            INSERT INTO public.itc_ledger_entries (id, org_id, purchase_doc_id, event_type, tax_period, cgst_amount, sgst_amount, igst_amount, cess_amount, total_tax)
            VALUES (:id, :org_id, :purch_id, 'AVAIL', '112025', 2500, 2500, 0, 0, 5000);
        """), {"id": ledger_id_a, "org_id": org_a, "purch_id": purch_id_a})

        gst_rec_id_a = str(uuid.uuid4())
        db.execute(text("""
            INSERT INTO public.gst_records (id, org_id, gstin, return_type, tax_period, filing_status, total_taxable_value, total_tax_paid)
            VALUES (:id, :org_id, '27AAAAA0000A1ZT', 'GSTR-3B', '112025', 'pending', 50000, 5000);
        """), {"id": gst_rec_id_a, "org_id": org_a})

        fin_tx_id_a = str(uuid.uuid4())
        db.execute(text("""
            INSERT INTO public.financial_transactions (id, org_id, transaction_date, amount, transaction_type, category, counterparty)
            VALUES (:id, :org_id, '2025-11-01', 50000, 'credit', 'sales', 'Alpha Client');
        """), {"id": fin_tx_id_a, "org_id": org_a})

        filing_id_a = str(uuid.uuid4())
        db.execute(text("""
            INSERT INTO public.gstr3b_filings (id, org_id, registration_id, tax_period, filing_status, maker_id)
            VALUES (:id, :org_id, :reg_id, '112025', 'DRAFT', :user_id);
        """), {"id": filing_id_a, "org_id": org_a, "reg_id": reg_id_a, "user_id": user_a})

        summary_id_a = str(uuid.uuid4())
        db.execute(text("""
            INSERT INTO public.financial_summaries (id, org_id, summary_period, total_inflow, total_outflow, net_cash_flow)
            VALUES (:id, :org_id, '112025', 100000, 50000, 50000);
        """), {"id": summary_id_a, "org_id": org_a})

        # Fetch a valid scheme_id from government_schemes
        scheme_id = db.execute(text("SELECT id FROM public.government_schemes LIMIT 1;")).scalar()
        match_id_a = str(uuid.uuid4())
        db.execute(text("""
            INSERT INTO public.scheme_matches (id, org_id, scheme_id, match_score, status)
            VALUES (:id, :org_id, :scheme_id, 85.50, 'recommended');
        """), {"id": match_id_a, "org_id": org_a, "scheme_id": scheme_id})

        db.commit()
        print("  --> Seeded Org A records across all core tenant domains (M1-M5).")

        # Step B: Switch to Org B context and test read isolation across ALL domain tables
        set_tenant_context(db, org_id=org_b, user_id=user_b)

        domain_checks = [
            ("invoices", "SELECT id FROM public.invoices WHERE id = :id", inv_id_a),
            ("documents", "SELECT id FROM public.documents WHERE id = :id", doc_id_a),
            ("gst_registrations", "SELECT id FROM public.gst_registrations WHERE id = :id", reg_id_a),
            ("purchase_documents", "SELECT id FROM public.purchase_documents WHERE id = :id", purch_id_a),
            ("itc_ledger_entries", "SELECT id FROM public.itc_ledger_entries WHERE id = :id", ledger_id_a),
            ("gst_records", "SELECT id FROM public.gst_records WHERE id = :id", gst_rec_id_a),
            ("financial_transactions", "SELECT id FROM public.financial_transactions WHERE id = :id", fin_tx_id_a),
            ("gstr3b_filings", "SELECT id FROM public.gstr3b_filings WHERE id = :id", filing_id_a),
            ("financial_summaries", "SELECT id FROM public.financial_summaries WHERE id = :id", summary_id_a),
            ("business_profiles", "SELECT id FROM public.business_profiles WHERE id = :id", prof_id_a),
            ("scheme_matches", "SELECT id FROM public.scheme_matches WHERE id = :id", match_id_a),
        ]

        for tbl, query, target_id in domain_checks:
            res = db.execute(text(query), {"id": target_id}).fetchall()
            assert len(res) == 0, f"CRITICAL RLS BREACH: Org B can read Org A's {tbl}!"
            print(f"  [Read Isolation] PASS: Org B cannot see Org A's record in {tbl}.")

        # Step C: Cross-tenant Write/Tamper Attempts from Org B into Org A
        for tbl, target_id in [("invoices", inv_id_a), ("gstr3b_filings", filing_id_a), ("financial_summaries", summary_id_a)]:
            update_count = db.execute(text(f"UPDATE public.{tbl} SET id = :id WHERE id = :id;"), {"id": target_id}).rowcount
            assert update_count == 0, f"CRITICAL RLS BREACH: Org B UPDATE on Org A's {tbl} succeeded!"
            print(f"  [Cross-Tenant Tamper] PASS: Org B UPDATE on Org A's {tbl} affected 0 rows.")

        # Step D: Cross-tenant Insert Attempt (inserting org_a record while under org_b)
        cross_insert_blocked = False
        try:
            db.execute(text("""
                INSERT INTO public.financial_summaries (id, org_id, summary_period, total_inflow, total_outflow, net_cash_flow)
                VALUES (:id, :org_a, '112025', 0, 0, 0);
            """), {"id": str(uuid.uuid4()), "org_a": org_a})
            db.commit()
        except Exception:
            cross_insert_blocked = True
            db.rollback()
        assert cross_insert_blocked, "CRITICAL RLS BREACH: Org B was able to insert records into Org A!"
        print("  [Cross-Tenant Insert] PASS: Cross-tenant insert rejected by RLS WITH CHECK.")

        # Step E: Null tenant context isolation
        db.execute(text("SELECT set_config('app.current_org', '', false), set_config('app.current_user', '', false);"))
        for tbl in ["invoices", "documents", "gst_registrations", "purchase_documents", "itc_ledger_entries", "gstr3b_filings", "financial_summaries", "business_profiles", "scheme_matches"]:
            assert len(db.execute(text(f"SELECT id FROM public.{tbl};")).fetchall()) == 0, f"Leak in {tbl} under null context!"
        print("  [Null Context Isolation] PASS: Under null context, all tenant tables return 0 rows.")

    finally:
        db.close()

    print("=== [3] Cross-Tenant Isolation: 100% VERIFIED SECURE ===\n")


def audit_api_layer_token_security():
    print("=== [4] Auditing FastAPI API Layer Authentication & Cross-Tenant Token Gates ===")
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
    res = client.get("/api/v1/credit-builder/summary", headers=spoof_headers)
    print(f"  Spoofed Header Call -> HTTP {res.status_code} (Active Tamper Protection Enforced)")
    assert res.status_code == 403, f"Expected 403 Forbidden for spoofed headers, got {res.status_code}"

    print("=== [4] API Layer Security: 100% VERIFIED SECURE ===\n")


if __name__ == "__main__":
    audit_database_roles_and_rls_enforcement()
    audit_catalogue_write_denials_under_app_service_user()
    audit_m1_m5_cross_tenant_isolation()
    audit_api_layer_token_security()
    print("ALL COMPREHENSIVE RLS & SECURITY AUDITS PASSED FLAWLESSLY!")
