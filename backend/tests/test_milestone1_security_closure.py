import os
import sys
import io
import json
import uuid
import random
import pytest
from pathlib import Path
from decimal import Decimal
from fastapi.testclient import TestClient

# Ensure backend root is on sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

import psycopg
from psycopg.rows import dict_row

from app.main import app
from app.config import settings
from app.modules.invoice_brain.validator import (
    calculate_gstin_checksum, validate_gstin_format_and_checksum
)
from app.modules.invoice_brain.hsn_catalog import (
    lookup_hsn_code, validate_hsn_compliance
)
from app.modules.invoice_brain.gstr import (
    get_b2c_large_threshold, prepare_gstr1_draft, prepare_gstr3b_draft
)
from app.modules.invoice_brain.service import (
    process_invoice_ocr_task, update_invoice_fields, update_line_item,
    approve_invoice, InvoiceStatusError
)
from app.modules.invoice_brain.ocr import extract_invoice_with_gemini
from app.schemas.invoice_brain import (
    InvoicePatch, LineItemUpdate,
    GSTR_WORKING_DRAFT_DISCLAIMER, GSTR3B_WORKING_SUMMARY_DISCLAIMER
)

DB_ADMIN = "postgresql:///myca?user=postgres"
DB_APP = "postgresql:///myca?user=app_service_user"
DB_WORKER = "postgresql:///myca?user=worker_service_user"

client = TestClient(app)

def random_pan():
    letters1 = "".join(random.choices("ABCDEFGHIJKLMNOPQRSTUVWXYZ", k=5))
    digits = f"{random.randint(1000, 9999)}"
    letter2 = random.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    return f"{letters1}{digits}{letter2}"

def random_gstin():
    state = random.choice(["27", "29", "07", "33", "24", "19"])
    pan = random_pan()
    base = f"{state}{pan}1Z"
    return f"{base}{calculate_gstin_checksum(base)}"

@pytest.fixture
def test_tenants():
    """Seeds two distinct organizations Org A and Org B with users and business profiles."""
    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            org_a = str(uuid.uuid4())
            user_a = str(uuid.uuid4())
            profile_a = str(uuid.uuid4())
            gstin_a = random_gstin()
            pan_a = gstin_a[2:12]

            cur.execute("INSERT INTO public.organizations (id, legal_name, pan) VALUES (%s, 'Milestone Tenant A', %s);", (org_a, pan_a))
            cur.execute("INSERT INTO public.global_users (id, email, full_name) VALUES (%s, %s, 'Owner A');", (user_a, f"user_a_{uuid.uuid4().hex[:6]}@example.com"))
            cur.execute("INSERT INTO public.organization_memberships (org_id, user_id, role, status) VALUES (%s, %s, 'owner', 'active');", (org_a, user_a))
            cur.execute("INSERT INTO public.business_profiles (id, org_id, gstin, business_type, industry_sector) VALUES (%s, %s, %s, 'Services', 'IT');", (profile_a, org_a, gstin_a))
            cur.execute("INSERT INTO public.worker_service_authorizations (worker_role_name, org_id, is_active) VALUES ('worker_service_user', %s, true);", (org_a,))

            org_b = str(uuid.uuid4())
            user_b = str(uuid.uuid4())
            profile_b = str(uuid.uuid4())
            gstin_b = random_gstin()
            pan_b = gstin_b[2:12]

            cur.execute("INSERT INTO public.organizations (id, legal_name, pan) VALUES (%s, 'Milestone Tenant B', %s);", (org_b, pan_b))
            cur.execute("INSERT INTO public.global_users (id, email, full_name) VALUES (%s, %s, 'Owner B');", (user_b, f"user_b_{uuid.uuid4().hex[:6]}@example.com"))
            cur.execute("INSERT INTO public.organization_memberships (org_id, user_id, role, status) VALUES (%s, %s, 'owner', 'active');", (org_b, user_b))
            cur.execute("INSERT INTO public.business_profiles (id, org_id, gstin, business_type, industry_sector) VALUES (%s, %s, %s, 'Services', 'IT');", (profile_b, org_b, gstin_b))
            cur.execute("INSERT INTO public.worker_service_authorizations (worker_role_name, org_id, is_active) VALUES ('worker_service_user', %s, true);", (org_b,))

    return {
        "org_a": org_a, "user_a": user_a, "profile_a": profile_a, "gstin_a": gstin_a,
        "org_b": org_b, "user_b": user_b, "profile_b": profile_b, "gstin_b": gstin_b
    }


def seed_invoice(cur, org_id, profile_id, user_id, inv_num, inv_date, cp_name, cp_gstin, taxable, cgst, sgst, igst, total, status='approved', lines=None):
    """
    Helper that creates an invoice starting with 'uploaded' and steps through
    state transitions respecting database trigger constraints.
    """
    inv_id = str(uuid.uuid4())
    # 1. Insert with initial status 'uploaded'
    cur.execute("""
        INSERT INTO public.invoices (
            id, org_id, profile_id, invoice_number, invoice_date,
            counterparty_name, counterparty_gstin, total_taxable_amount,
            cgst_amount, sgst_amount, igst_amount, total_amount, status
        ) VALUES (
            %s, %s, %s, %s, %s,
            %s, %s, %s,
            %s, %s, %s, %s, 'uploaded'
        );
    """, (inv_id, org_id, profile_id, inv_num, inv_date, cp_name, cp_gstin, taxable, cgst, sgst, igst, total))

    if lines:
        for line in lines:
            cur.execute("""
                INSERT INTO public.invoice_line_items (
                    id, invoice_id, org_id, item_description, hsn_sac_code,
                    quantity, unit_price, taxable_amount, gst_rate,
                    cgst_amount, sgst_amount, igst_amount, total_amount
                ) VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s, %s
                );
            """, (
                str(uuid.uuid4()), inv_id, org_id,
                line.get("item_description", "Item Description"), line.get("hsn_sac_code", "998311"),
                line.get("quantity", 1), line.get("unit_price", taxable), line.get("taxable_amount", taxable),
                line.get("gst_rate", 18.00), line.get("cgst_amount", cgst), line.get("sgst_amount", sgst),
                line.get("igst_amount", igst), line.get("total_amount", total)
            ))

    if status != 'uploaded':
        cur.execute("UPDATE public.invoices SET status = 'queued' WHERE id = %s;", (inv_id,))
        cur.execute("UPDATE public.invoices SET status = 'processing' WHERE id = %s;", (inv_id,))
        cur.execute("UPDATE public.invoices SET status = 'extracted' WHERE id = %s;", (inv_id,))
        cur.execute("UPDATE public.invoices SET status = 'pending_review' WHERE id = %s;", (inv_id,))
        if status == 'approved':
            cur.execute("SELECT set_config('app.current_user', %s, false);", (user_id,))
            cur.execute("UPDATE public.invoices SET status = 'approved', approved_by = %s, approved_at = clock_timestamp() WHERE id = %s;", (user_id, inv_id))
        elif status == 'validation_failed':
            cur.execute("UPDATE public.invoices SET status = 'validation_failed', rejection_reason = 'Discrepancies found' WHERE id = %s;", (inv_id,))

    return inv_id


# ==============================================================================
# PART 1: Enqueue Authorization & Queue Security Tests
# ==============================================================================

def test_enqueue_task_null_tenant_context_rejected(test_tenants):
    """
    Reviewer Blocker Verification 1:
    Calling enqueue_task() without setting app.current_org must fail with 42501.
    PostgreSQL NULL comparison bypass must not occur.
    """
    with psycopg.connect(DB_APP, autocommit=True) as conn:
        with conn.cursor() as cur:
            # Explicitly reset tenant context to empty/null
            cur.execute("SELECT set_config('app.current_org', '', false);")
            with pytest.raises(psycopg.errors.InsufficientPrivilege) as exc:
                cur.execute("""
                    SELECT public.enqueue_task(
                        'invoice_ocr',
                        '{"document_id": "test"}'::jsonb,
                        %s::uuid
                    );
                """, (test_tenants["org_a"],))
            assert exc.value.sqlstate == "42501" or "42501" in str(exc.value)


def test_enqueue_task_cross_tenant_impersonation_rejected(test_tenants):
    """
    Reviewer Blocker Verification 2:
    Caller in Org A tenant context attempting to enqueue for Org B must fail with 42501.
    """
    with psycopg.connect(DB_APP, autocommit=True) as conn:
        with conn.cursor() as cur:
            # Set tenant context to Org A
            cur.execute("SELECT set_config('app.current_org', %s, false);", (test_tenants["org_a"],))
            with pytest.raises(psycopg.errors.InsufficientPrivilege) as exc:
                cur.execute("""
                    SELECT public.enqueue_task(
                        'invoice_ocr',
                        '{"document_id": "test"}'::jsonb,
                        %s::uuid
                    );
                """, (test_tenants["org_b"],))
            assert exc.value.sqlstate == "42501" or "42501" in str(exc.value)


def test_enqueue_task_matching_and_default_tenant_context_accepted(test_tenants):
    """
    Reviewer Blocker Verification 3:
    1. Caller in Org A tenant context enqueuing for Org A explicitly succeeds.
    2. Caller in Org A tenant context omitting p_org defaults safely to Org A.
    """
    with psycopg.connect(DB_APP, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('app.current_org', %s, false);", (test_tenants["org_a"],))
            
            # Case 1: Explicit matching Org A
            cur.execute("""
                SELECT public.enqueue_task(
                    'invoice_ocr',
                    '{"file_name": "test1.pdf"}'::jsonb,
                    %s::uuid
                );
            """, (test_tenants["org_a"],))
            task_id_1 = cur.fetchone()[0]
            assert task_id_1 is not None

            # Case 2: Omitted p_org (defaults to context Org A)
            cur.execute("""
                SELECT public.enqueue_task(
                    'invoice_ocr',
                    '{"file_name": "test2.pdf"}'::jsonb
                );
            """)
            task_id_2 = cur.fetchone()[0]
            assert task_id_2 is not None


def test_enqueue_task_platform_admin_override(test_tenants):
    """
    Reviewer Blocker Verification 4:
    Platform admin can enqueue for any organization without requiring app.current_org.
    """
    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            # Ensure no tenant context is set
            cur.execute("SELECT set_config('app.current_org', '', false);")
            cur.execute("""
                SELECT public.enqueue_task(
                    'invoice_ocr',
                    '{"admin_enqueued": true}'::jsonb,
                    %s::uuid
                );
            """, (test_tenants["org_b"],))
            task_id = cur.fetchone()[0]
            assert task_id is not None


def test_worker_tasks_direct_select_revoked_from_application_user():
    """
    Security Barrier Verification:
    Direct table SELECT on public.worker_tasks must be REVOKED from app_user (42501).
    Prevents leaking lease_token_hash, payload, result, locked_by.
    """
    with psycopg.connect(DB_APP, autocommit=True) as conn:
        with conn.cursor() as cur:
            with pytest.raises(psycopg.errors.InsufficientPrivilege) as exc:
                cur.execute("SELECT * FROM public.worker_tasks LIMIT 1;")
            assert exc.value.sqlstate == "42501" or "permission denied" in str(exc.value).lower()


def test_v_tenant_task_status_view_accessibility_and_column_isolation(test_tenants):
    """
    Security Barrier Verification:
    app_service_user can query public.v_tenant_task_status, which exposes only:
    id, org_id, task_type, status, created_at, updated_at.
    Lease tokens, hashes, and worker assignment metadata are strictly hidden.
    """
    with psycopg.connect(DB_APP, autocommit=True) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT set_config('app.current_org', %s, false);", (test_tenants["org_a"],))
            cur.execute("SELECT * FROM public.v_tenant_task_status LIMIT 5;")
            rows = cur.fetchall()
            if rows:
                row = rows[0]
                allowed_cols = {"id", "org_id", "task_type", "status", "created_at", "updated_at"}
                assert set(row.keys()) == allowed_cols
                assert "lease_token_hash" not in row
                assert "payload" not in row
                assert "result" not in row
                assert "locked_by" not in row


def test_enqueue_task_parameter_and_payload_constraints(test_tenants):
    """
    Constraint Verification:
    1. Null task_type rejected.
    2. Non-JSON-object payload rejected.
    3. Payload > 64KB rejected.
    """
    with psycopg.connect(DB_APP, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('app.current_org', %s, false);", (test_tenants["org_a"],))

            # 1. NULL task type
            with pytest.raises(psycopg.errors.DatabaseError):
                cur.execute("SELECT public.enqueue_task(NULL, '{}'::jsonb, %s::uuid);", (test_tenants["org_a"],))

            # 2. JSON array payload
            with pytest.raises(psycopg.errors.DatabaseError) as exc_arr:
                cur.execute("SELECT public.enqueue_task('invoice_ocr', '[]'::jsonb, %s::uuid);", (test_tenants["org_a"],))
            assert "JSON object" in str(exc_arr.value)

            # 3. Payload > 64KB
            large_str = "x" * 70000
            large_payload = json.dumps({"data": large_str})
            with pytest.raises(psycopg.errors.DatabaseError) as exc_size:
                cur.execute("SELECT public.enqueue_task('invoice_ocr', %s::jsonb, %s::uuid);", (large_payload, test_tenants["org_a"]))
            assert "64KB" in str(exc_size.value)


# ==============================================================================
# PART 2: Statutory GST Rules & Phase 3 Portal Requirements
# ==============================================================================

def test_b2c_large_threshold_statutory_cutoffs():
    """
    Statutory Cutoff Verification:
    - Notification No. 12/2024 - Central Tax (Rule 59(4) amendment):
      Period >= '2024-08': Threshold is ₹1,00,000.
      Period < '2024-08': Threshold is ₹2,50,000.
    """
    assert get_b2c_large_threshold("2026-11") == Decimal("100000.00")
    assert get_b2c_large_threshold("2024-08") == Decimal("100000.00")
    assert get_b2c_large_threshold("2024-07") == Decimal("250000.00")
    assert get_b2c_large_threshold("2023-12") == Decimal("250000.00")


def test_b2c_large_boundary_classification(test_tenants):
    """
    Boundary Verification for Return Periods:
    1. Period '2026-11':
       - ₹99,999.00 unregistered interstate -> B2C Small
       - ₹1,00,000.00 unregistered interstate -> B2C Small (strictly > 1L)
       - ₹1,00,000.01 unregistered interstate -> B2C Large
    2. Period '2024-05':
       - ₹1,50,000.00 unregistered interstate -> B2C Small
       - ₹2,50,000.00 unregistered interstate -> B2C Small (strictly > 2.5L)
       - ₹2,50,001.00 unregistered interstate -> B2C Large
    """
    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            org_id = test_tenants["org_a"]
            prof_id = test_tenants["profile_a"]
            user_id = test_tenants["user_a"]

            # Seed 3 invoices for period 2026-11 (Interstate POS '29' Karnataka vs Supplier '27' MH)
            seed_invoice(cur, org_id, prof_id, user_id, 'INV-2026-SMALL-1', '2026-11-05', 'Consumer 1', None, 84744.92, 0, 0, 15254.08, 99999.00, 'approved')
            seed_invoice(cur, org_id, prof_id, user_id, 'INV-2026-SMALL-2', '2026-11-10', 'Consumer 2', None, 84745.76, 0, 0, 15254.24, 100000.00, 'approved')
            seed_invoice(cur, org_id, prof_id, user_id, 'INV-2026-LARGE-1', '2026-11-15', 'Consumer 3', None, 84745.77, 0, 0, 15254.24, 100000.01, 'approved')

            # Seed 2 invoices for historical period 2024-05
            seed_invoice(cur, org_id, prof_id, user_id, 'INV-2024-SMALL-1', '2024-05-10', 'Old Consumer 1', None, 127118.64, 0, 0, 22881.36, 150000.00, 'approved')
            seed_invoice(cur, org_id, prof_id, user_id, 'INV-2024-LARGE-1', '2024-05-20', 'Old Consumer 2', None, 211865.25, 0, 0, 38135.75, 250001.00, 'approved')

    # Evaluate 2026-11 return
    headers = {"X-User-Id": test_tenants["user_a"], "X-Org-Id": test_tenants["org_a"]}
    res_2026 = client.get("/api/v1/gst/gstr1-draft?period=2026-11", headers=headers)
    assert res_2026.status_code == 200
    d_2026 = res_2026.json()
    assert float(d_2026["applicable_b2c_large_threshold"]) == 100000.00
    large_inv_nums_2026 = [inv["invoice_number"] for inv in d_2026["table_5_b2c_large"]]
    assert "INV-2026-LARGE-1" in large_inv_nums_2026
    assert "INV-2026-SMALL-1" not in large_inv_nums_2026
    assert "INV-2026-SMALL-2" not in large_inv_nums_2026

    # Evaluate 2024-05 return
    res_2024 = client.get("/api/v1/gst/gstr1-draft?period=2024-05", headers=headers)
    assert res_2024.status_code == 200
    d_2024 = res_2024.json()
    assert float(d_2024["applicable_b2c_large_threshold"]) == 250000.00
    large_inv_nums_2024 = [inv["invoice_number"] for inv in d_2024["table_5_b2c_large"]]
    assert "INV-2024-LARGE-1" in large_inv_nums_2024
    assert "INV-2024-SMALL-1" not in large_inv_nums_2024


def test_table_12_phase3_split_and_catalog_validation(test_tenants):
    """
    Phase 3 Advisory (May 2025) Verification:
    Table 12 must be segregated into separate B2B and B2C tabs.
    HSN descriptions must be resolved from official statutory catalogue.
    """
    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            org_id = test_tenants["org_a"]
            prof_id = test_tenants["profile_a"]
            user_id = test_tenants["user_a"]
            period = "2026-10"

            # Invoice 1: B2B with HSN 847130 (Personal laptops)
            seed_invoice(
                cur, org_id, prof_id, user_id,
                'INV-B2B-HSN', '2026-10-12', 'Registered Corp', test_tenants["gstin_b"],
                50000.00, 4500.00, 4500.00, 0, 59000.00,
                status='approved',
                lines=[{"item_description": "Laptops", "hsn_sac_code": "847130", "quantity": 1, "unit_price": 50000.00, "taxable_amount": 50000.00, "gst_rate": 18.00, "cgst_amount": 4500.00, "sgst_amount": 4500.00, "igst_amount": 0, "total_amount": 59000.00}]
            )

            # Invoice 2: B2C with SAC 998311 (Consulting)
            seed_invoice(
                cur, org_id, prof_id, user_id,
                'INV-B2C-SAC', '2026-10-14', 'Direct Customer', None,
                20000.00, 1800.00, 1800.00, 0, 23600.00,
                status='approved',
                lines=[{"item_description": "Consulting", "hsn_sac_code": "998311", "quantity": 1, "unit_price": 20000.00, "taxable_amount": 20000.00, "gst_rate": 18.00, "cgst_amount": 1800.00, "sgst_amount": 1800.00, "igst_amount": 0, "total_amount": 23600.00}]
            )

    headers = {"X-User-Id": test_tenants["user_a"], "X-Org-Id": test_tenants["org_a"]}
    res = client.get(f"/api/v1/gst/gstr1-draft?period={period}", headers=headers)
    assert res.status_code == 200
    d = res.json()

    # B2B Tab
    b2b_tab = d["table_12_hsn_b2b"]
    assert len(b2b_tab) >= 1
    assert any(h["hsn_sac_code"] == "847130" and "data processing" in h["description"].lower() for h in b2b_tab)

    # B2C Tab
    b2c_tab = d["table_12_hsn_b2c"]
    assert len(b2c_tab) >= 1
    assert any(h["hsn_sac_code"] == "998311" and "management consulting" in h["description"].lower() for h in b2c_tab)

    # Table 13 Document Details mandatory check
    assert d["table_13_doc_summary"]["is_mandatory"] is True
    assert d["table_13_doc_summary"]["net_issued"] >= 2


def test_hsn_statutory_turnover_digit_compliance():
    """
    Statutory HSN rules under Notification No. 78/2020:
    - Preceding year turnover <= 5 Cr: Minimum 4 digits for B2B.
    - Preceding year turnover > 5 Cr: Minimum 6 digits for all supplies.
    """
    # Small turnover (<= 5 Cr)
    ok, err, entry = validate_hsn_compliance("8471", aato_crores=Decimal("3.5"), is_b2b=True)
    assert ok is True
    assert entry.code == "8471"

    ok, err, _ = validate_hsn_compliance("84", aato_crores=Decimal("3.5"), is_b2b=True)
    assert ok is False
    assert "minimum 4 digits" in err

    # Large turnover (> 5 Cr)
    ok, err, _ = validate_hsn_compliance("8471", aato_crores=Decimal("10.0"), is_b2b=True)
    assert ok is False
    assert "minimum 6 digits" in err

    ok, err, entry = validate_hsn_compliance("847130", aato_crores=Decimal("10.0"), is_b2b=True)
    assert ok is True
    assert entry.code == "847130"

    # Non-catalogue code
    ok, err, _ = validate_hsn_compliance("123456", aato_crores=Decimal("2.0"), is_b2b=True)
    assert ok is False
    assert "not recognized" in err


# ==============================================================================
# PART 3: Operational Integrity & Review Lifecycles
# ==============================================================================

def test_ocr_mock_fallback_prohibited_in_production(monkeypatch):
    """
    Operational Integrity Verification:
    If ENVIRONMENT == 'production', mock OCR fallback must be strictly prohibited.
    """
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "")

    with pytest.raises(RuntimeError) as exc:
        extract_invoice_with_gemini("/tmp/nonexistent.pdf", "application/pdf")
    assert "strictly prohibited in production" in str(exc.value)


def test_worker_task_reprocessing_idempotency(test_tenants):
    """
    Operational Integrity Verification:
    Reprocessing an OCR task multiple times must NOT duplicate line items.
    """
    inv_id = str(uuid.uuid4())
    doc_id = str(uuid.uuid4())
    task_id = str(uuid.uuid4())
    org_id = test_tenants["org_a"]

    mock_pdf = Path("/tmp/mock.pdf")
    mock_pdf.write_bytes(b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\nxref\n0 2\n0000000000 65535 f\ntrailer<</Root 1 0 R>>\nstartxref\n50\n%%EOF\n")

    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO public.documents (id, org_id, doc_type, file_name, file_size_bytes, mime_type, file_path, sha256_checksum)
                VALUES (%s, %s, 'invoice', 'reprocess_test.pdf', 1024, 'application/pdf', '/tmp/mock.pdf', 'fake_hash');
            """, (doc_id, org_id))
            cur.execute("""
                INSERT INTO public.invoices (id, org_id, profile_id, invoice_number, invoice_date, counterparty_name, total_taxable_amount, cgst_amount, sgst_amount, igst_amount, total_amount, status)
                VALUES (%s, %s, %s, 'INV-REPROCESS', '2026-11-01', 'Test Counterparty', 0, 0, 0, 0, 0, 'uploaded');
            """, (inv_id, org_id, test_tenants["profile_a"]))

    # First worker processing run
    with psycopg.connect(DB_WORKER, autocommit=True) as conn:
        process_invoice_ocr_task(
            db_conn=conn, task_id=task_id, invoice_id=inv_id,
            document_id=doc_id, org_id=org_id,
            file_path="/tmp/mock.pdf", mime_type="application/pdf"
        )

    # Check line items count
    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM public.invoice_line_items WHERE invoice_id = %s;", (inv_id,))
            count_first_run = cur.fetchone()[0]
            assert count_first_run > 0

    # Second worker processing run (reprocessing on retry)
    with psycopg.connect(DB_WORKER, autocommit=True) as conn:
        process_invoice_ocr_task(
            db_conn=conn, task_id=task_id, invoice_id=inv_id,
            document_id=doc_id, org_id=org_id,
            file_path="/tmp/mock.pdf", mime_type="application/pdf"
        )

    # Verify count remains identical (no duplicated rows)
    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM public.invoice_line_items WHERE invoice_id = %s;", (inv_id,))
            count_second_run = cur.fetchone()[0]
            assert count_second_run == count_first_run


def test_auto_revalidation_on_field_and_line_item_patch(test_tenants):
    """
    Review Integrity Verification:
    Modifying an invoice or its line items automatically re-evaluates validation
    and transitions status between 'pending_review' and 'validation_failed'.
    """
    headers = {"X-User-Id": test_tenants["user_a"], "X-Org-Id": test_tenants["org_a"]}
    org_id = test_tenants["org_a"]
    prof_id = test_tenants["profile_a"]
    user_id = test_tenants["user_a"]

    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            inv_id = seed_invoice(
                cur, org_id, prof_id, user_id,
                'INV-REVAL-1', '2026-11-01', 'Retail Customer', None,
                1000.00, 90.00, 90.00, 0, 1180.00,
                status='pending_review',
                lines=[{"item_description": "Test Service", "hsn_sac_code": "998311", "quantity": 1, "unit_price": 1000.00, "taxable_amount": 1000.00, "gst_rate": 18.00, "cgst_amount": 90.00, "sgst_amount": 90.00, "igst_amount": 0, "total_amount": 1180.00}]
            )
            cur.execute("SELECT id FROM public.invoice_line_items WHERE invoice_id = %s LIMIT 1;", (inv_id,))
            item_id = str(cur.fetchone()[0])

    # 1. Tamper with invoice total_amount (creating grand total discrepancy)
    patch_res = client.patch(f"/api/v1/invoices/{inv_id}", headers=headers, json={"total_amount": 5000.00})
    assert patch_res.status_code == 200

    # Verify status transitioned to validation_failed
    inv_res = client.get(f"/api/v1/invoices/{inv_id}", headers=headers).json()
    assert inv_res["status"] == "validation_failed"
    assert inv_res["rejection_reason"] is not None

    # 2. Correct invoice total_amount back to 1180.00
    patch_res_2 = client.patch(f"/api/v1/invoices/{inv_id}", headers=headers, json={"total_amount": 1180.00})
    assert patch_res_2.status_code == 200

    # Verify status transitioned back to pending_review
    inv_res_2 = client.get(f"/api/v1/invoices/{inv_id}", headers=headers).json()
    assert inv_res_2["status"] == "pending_review"
    assert inv_res_2["rejection_reason"] is None

    # 3. Modify line item to introduce arithmetic error (quantity 5, but taxable 1000)
    line_patch = client.patch(f"/api/v1/invoices/{inv_id}/line-items/{item_id}", headers=headers, json={"quantity": 5.0})
    assert line_patch.status_code == 200

    inv_res_3 = client.get(f"/api/v1/invoices/{inv_id}", headers=headers).json()
    assert inv_res_3["status"] == "validation_failed"


def test_approval_strictly_prohibited_for_invalid_invoice(test_tenants):
    """
    Review Integrity Verification:
    Human approval of an invoice with critical validation discrepancies is strictly rejected.
    """
    headers = {"X-User-Id": test_tenants["user_a"], "X-Org-Id": test_tenants["org_a"]}
    org_id = test_tenants["org_a"]
    prof_id = test_tenants["profile_a"]
    user_id = test_tenants["user_a"]

    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            # Create invoice in pending_review but with deliberate math error (taxable 1000 + tax 180 != total 2000)
            inv_id = seed_invoice(
                cur, org_id, prof_id, user_id,
                'INV-FAIL-APPROVE', '2026-11-01', 'Retail Customer', None,
                1000.00, 90.00, 90.00, 0, 2000.00,
                status='pending_review',
                lines=[{"item_description": "Service", "hsn_sac_code": "998311", "quantity": 1, "unit_price": 1000.00, "taxable_amount": 1000.00, "gst_rate": 18.00, "cgst_amount": 90.00, "sgst_amount": 90.00, "igst_amount": 0, "total_amount": 1180.00}]
            )

    # Attempt to approve
    approve_res = client.post(f"/api/v1/invoices/{inv_id}/approve", headers=headers)
    assert approve_res.status_code == 400
    assert "invoice failed validation" in approve_res.json()["detail"].lower()

    # Verify database status is not approved
    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT status FROM public.invoices WHERE id = %s;", (inv_id,))
            assert cur.fetchone()[0] == "validation_failed"
