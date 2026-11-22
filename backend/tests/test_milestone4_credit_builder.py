import os
import uuid
import random
from decimal import Decimal
import pytest
import psycopg
from fastapi.testclient import TestClient

from app.main import app
from app.database import SessionLocal, set_tenant_context
from app.modules.credit_builder.categorizer import categorize_transaction
from app.modules.credit_builder.service import (
    ingest_statement_csv, get_financial_summary,
    compute_financial_readiness_score, generate_loan_dossier
)

DB_ADMIN = os.environ.get("TEST_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/myca")

@pytest.fixture
def m4_tenant():
    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            org_id = str(uuid.uuid4())
            user_id = str(uuid.uuid4())
            rand_digits = f"{random.randint(1000, 9999)}"
            pan = f"MBILD{rand_digits}K"
            gstin = f"27{pan}1ZT"
            reg_id = str(uuid.uuid4())
            profile_id = str(uuid.uuid4())

            cur.execute("INSERT INTO public.organizations (id, legal_name, pan) VALUES (%s, 'M4 Test Enterprise', %s);", (org_id, pan))
            cur.execute("INSERT INTO public.global_users (id, email, full_name) VALUES (%s, %s, 'Credit Tester');", (user_id, f"m4_{uuid.uuid4().hex[:6]}@example.com"))
            cur.execute("INSERT INTO public.organization_memberships (org_id, user_id, role, status) VALUES (%s, %s, 'owner', 'active');", (org_id, user_id))
            cur.execute("""
                INSERT INTO public.gst_registrations (id, org_id, gstin, state_code, legal_name, is_primary)
                VALUES (%s, %s, %s, '27', 'M4 Legal Entity', true);
            """, (reg_id, org_id, gstin))
            cur.execute("""
                INSERT INTO public.business_profiles (id, org_id, gstin, business_type, industry_sector, created_at, updated_at)
                VALUES (%s, %s, %s, 'Proprietorship', 'Manufacturing', clock_timestamp(), clock_timestamp());
            """, (profile_id, org_id, gstin))

    return {"org_id": org_id, "user_id": user_id, "reg_id": reg_id, "gstin": gstin, "pan": pan}

def test_categorizer_rules():
    assert categorize_transaction("GST PMT-06 CHALLAN PAYMENT", "debit") == "Taxes & Statutory"
    assert categorize_transaction("HDFC BANK BUSINESS LOAN EMI AUTO-DEBIT", "debit") == "Debt Service / EMI"
    assert categorize_transaction("MONTHLY STAFF SALARY NEFT PAYOUT", "debit") == "Salary & Wages"
    assert categorize_transaction("OFFICE DEPOT PREMISES RENT", "debit") == "Rent & Facility"
    assert categorize_transaction("BESCOM ELECTRICITY BILL ONLINE", "debit") == "Utilities & Telecom"
    assert categorize_transaction("PURCHASE OF RAW STEEL MATERIAL SUPPLIER PVT LTD", "debit") == "Vendor / Procurement"
    assert categorize_transaction("CUSTOMER INVOICE SETTLEMENT VIA RTGS", "credit") == "Customer Revenue"
    assert categorize_transaction("BLUE DART COURIER FREIGHT CHARGES", "debit") == "Logistics & Freight"
    assert categorize_transaction("UNKNOWN MISCELLANEOUS CASH EXPENSE", "debit") == "General Operations"

def test_statement_csv_ingestion_and_summary(m4_tenant):
    org_id = m4_tenant["org_id"]
    user_id = m4_tenant["user_id"]

    sample_csv = """Date,Description,Type,Amount,Reference
2025-08-01,Customer RTGS Collection Invoice 101,credit,500000,REF101
2025-08-05,Steel Raw Material Vendor Purchase,debit,250000,REF102
2025-08-10,Staff Salary Payroll,debit,80000,REF103
2025-08-15,Office Premises Rent Depot,debit,40000,REF104
2025-08-20,GST PMT-06 Challan Tax Payment,debit,35000,REF105
2025-08-25,Business Loan EMI HDFC,debit,25000,REF106
2025-09-02,Customer UPI Inflow Invoice 102,credit,450000,REF107
2025-09-08,Raw Materials Supplier Corp,debit,220000,REF108
2025-09-10,Staff Remuneration,debit,80000,REF109
2025-09-20,GST Challan Payment,debit,30000,REF110
2025-10-05,Customer NEFT Inflow,credit,600000,REF111
2025-10-12,Vendor Procurement Ltd,debit,280000,REF112
2025-10-25,Business Loan EMI HDFC,debit,25000,REF113
"""

    with SessionLocal() as session:
        upload_res = ingest_statement_csv(session, org_id, user_id, sample_csv)
        assert upload_res.transactions_imported == 13
        assert upload_res.total_credits == Decimal("1550000.00")
        assert upload_res.total_debits == Decimal("1065000.00")
        assert upload_res.net_flow == Decimal("485000.00")

        # Test financial summary
        summary = get_financial_summary(session, org_id, user_id)
        assert summary.total_revenue == Decimal("1550000.00")
        assert summary.total_expenses == Decimal("1065000.00")
        assert summary.net_profit_proxy == Decimal("485000.00")
        assert len(summary.monthly_trends) == 3
        # Check that expense categories have Vendor, Salary, Rent, Taxes, Debt Service
        cat_names = [c.category for c in summary.expense_categories]
        assert "Vendor / Procurement" in cat_names
        assert "Salary & Wages" in cat_names
        assert "Taxes & Statutory" in cat_names
        assert "Debt Service / EMI" in cat_names

def test_msme_financial_readiness_score_and_dossier(m4_tenant):
    org_id = m4_tenant["org_id"]
    user_id = m4_tenant["user_id"]

    sample_csv = """Date,Description,Type,Amount,Reference
2025-08-01,Customer Collection Invoice 101,credit,600000,REF101
2025-08-05,Raw Material Purchase,debit,300000,REF102
2025-08-10,Staff Salary,debit,80000,REF103
2025-08-25,Business Loan EMI,debit,20000,REF104
2025-09-01,Customer RTGS Payment,credit,700000,REF105
2025-09-05,Vendor Supplier Payment,debit,350000,REF106
2025-09-25,Business Loan EMI,debit,20000,REF107
2025-10-01,Customer NEFT Inflow,credit,800000,REF108
2025-10-05,Raw Materials Enterprise,debit,400000,REF109
2025-10-25,Business Loan EMI,debit,20000,REF110
"""
    with SessionLocal() as session:
        ingest_statement_csv(session, org_id, user_id, sample_csv)

        # Compute Score
        scorecard = compute_financial_readiness_score(session, org_id, user_id)
        assert 300 <= scorecard.score <= 900
        assert scorecard.dscr_proxy > Decimal("1.5")
        assert len(scorecard.factors) == 4
        assert "NOT an official credit bureau score" in scorecard.disclaimer

        # Generate Dossier
        dossier = generate_loan_dossier(session, org_id, user_id)
        assert dossier.legal_name == "M4 Test Enterprise"
        assert dossier.gstin == m4_tenant["gstin"]
        assert dossier.scorecard.score == scorecard.score
        assert len(dossier.recommended_loan_products) > 0
        assert "CGTMSE" in dossier.recommended_loan_products[0] or "MUDRA" in dossier.recommended_loan_products[0]

def test_multi_tenant_financial_isolation(m4_tenant):
    org_a = m4_tenant["org_id"]
    user_a = m4_tenant["user_id"]

    # Create Org B
    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            org_b = str(uuid.uuid4())
            user_b = str(uuid.uuid4())
            rand_digits = f"{random.randint(1000, 9999)}"
            pan_b = f"ZZZZZ{rand_digits}A"
            cur.execute("INSERT INTO public.organizations (id, legal_name, pan) VALUES (%s, 'Org B Isolated', %s);", (org_b, pan_b))
            cur.execute("INSERT INTO public.global_users (id, email, full_name) VALUES (%s, %s, 'Tester B');", (user_b, f"m4_b_{uuid.uuid4().hex[:6]}@example.com"))
            cur.execute("INSERT INTO public.organization_memberships (org_id, user_id, role, status) VALUES (%s, %s, 'owner', 'active');", (org_b, user_b))
            cur.execute("""
                INSERT INTO public.business_profiles (id, org_id, business_type, industry_sector, created_at, updated_at)
                VALUES (%s, %s, 'Partnership', 'Trading', clock_timestamp(), clock_timestamp());
            """, (str(uuid.uuid4()), org_b))

    csv_a = "Date,Description,Type,Amount\n2025-08-01,Huge Org A Revenue,credit,10000000\n"
    csv_b = "Date,Description,Type,Amount\n2025-08-01,Small Org B Revenue,credit,50000\n"

    with SessionLocal() as session:
        ingest_statement_csv(session, org_a, user_a, csv_a)
        ingest_statement_csv(session, org_b, user_b, csv_b)

        summary_a = get_financial_summary(session, org_a, user_a)
        summary_b = get_financial_summary(session, org_b, user_b)

        # Assert Org B never sees Org A's 10,000,000 revenue
        assert summary_a.total_revenue == Decimal("10000000.00")
        assert summary_b.total_revenue == Decimal("50000.00")

def test_credit_builder_api_endpoints(m4_tenant):
    org_id = m4_tenant["org_id"]
    user_id = m4_tenant["user_id"]
    client = TestClient(app)

    csv_data = "Date,Description,Type,Amount\n2025-09-01,Customer Payment,credit,200000\n2025-09-05,Office Rent,debit,30000\n"

    # Upload via Form / csv_text
    res_upload = client.post(
        "/api/v1/credit-builder/statements/upload",
        data={"csv_text": csv_data},
        headers={"X-Org-Id": org_id, "X-User-Id": user_id}
    )
    assert res_upload.status_code == 200
    data = res_upload.json()
    assert data["transactions_imported"] == 2
    assert float(data["total_credits"]) == 200000.0

    # GET /summary
    res_summary = client.get(
        "/api/v1/credit-builder/summary",
        headers={"X-Org-Id": org_id, "X-User-Id": user_id}
    )
    assert res_summary.status_code == 200
    assert float(res_summary.json()["total_revenue"]) == 200000.0

    # GET /readiness-score
    res_score = client.get(
        "/api/v1/credit-builder/readiness-score",
        headers={"X-Org-Id": org_id, "X-User-Id": user_id}
    )
    assert res_score.status_code == 200
    assert 300 <= res_score.json()["score"] <= 900

    # GET /loan-dossier
    res_dossier = client.get(
        "/api/v1/credit-builder/loan-dossier",
        headers={"X-Org-Id": org_id, "X-User-Id": user_id}
    )
    assert res_dossier.status_code == 200
    assert res_dossier.json()["legal_name"] == "M4 Test Enterprise"
    assert len(res_dossier.json()["recommended_loan_products"]) > 0
