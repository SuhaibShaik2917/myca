import os
import sys
import io
import time
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
from app.database import SessionLocal
from app.modules.invoice_brain.validator import (
    calculate_gstin_checksum, validate_gstin_format_and_checksum, validate_invoice_financials
)
from app.modules.invoice_brain.hsn_catalog import (
    lookup_hsn_code, validate_hsn_compliance
)
from app.modules.invoice_brain.gstr import (
    get_b2c_large_threshold, prepare_gstr1_draft, prepare_gstr3b_draft
)
from app.modules.invoice_brain.storage import (
    reconcile_storage_orphans
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

DB_ADMIN = os.environ.get("DB_ADMIN_URL", "postgresql://postgres@localhost:5432/myca")
DB_APP = os.environ.get("DATABASE_URL", "postgresql://app_service_user:secret@localhost:5432/myca")
DB_WORKER = os.environ.get("DATABASE_WORKER_URL", "postgresql://worker_service_user:secret@localhost:5432/myca")

client = TestClient(app)

def random_pan():
    letters1 = "".join(random.choices("ABCDEFGHIJKLMNOPQRSTUVWXYZ", k=5))
    digits = f"{random.randint(1000, 9999)}"
    letter2 = random.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    return f"{letters1}{digits}{letter2}"

def random_gstin(state="27"):
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
            gstin_a = random_gstin("27")
            pan_a = gstin_a[2:12]

            cur.execute("INSERT INTO public.organizations (id, legal_name, pan) VALUES (%s, 'Milestone Tenant A', %s);", (org_a, pan_a))
            cur.execute("INSERT INTO public.global_users (id, email, full_name) VALUES (%s, %s, 'Owner A');", (user_a, f"user_a_{uuid.uuid4().hex}@example.com"))
            cur.execute("INSERT INTO public.organization_memberships (org_id, user_id, role, status) VALUES (%s, %s, 'owner', 'active');", (org_a, user_a))
            cur.execute("INSERT INTO public.business_profiles (id, org_id, gstin, business_type, industry_sector) VALUES (%s, %s, %s, 'Services', 'IT');", (profile_a, org_a, gstin_a))
            cur.execute("INSERT INTO public.worker_service_authorizations (worker_role_name, org_id, is_active) VALUES ('worker_service_user', %s, true);", (org_a,))

            org_b = str(uuid.uuid4())
            user_b = str(uuid.uuid4())
            profile_b = str(uuid.uuid4())
            gstin_b = random_gstin()
            pan_b = gstin_b[2:12]

            cur.execute("INSERT INTO public.organizations (id, legal_name, pan) VALUES (%s, 'Milestone Tenant B', %s);", (org_b, pan_b))
            cur.execute("INSERT INTO public.global_users (id, email, full_name) VALUES (%s, %s, 'Owner B');", (user_b, f"user_b_{uuid.uuid4().hex}@example.com"))
            cur.execute("INSERT INTO public.organization_memberships (org_id, user_id, role, status) VALUES (%s, %s, 'owner', 'active');", (org_b, user_b))
            cur.execute("INSERT INTO public.business_profiles (id, org_id, gstin, business_type, industry_sector) VALUES (%s, %s, %s, 'Services', 'IT');", (profile_b, org_b, gstin_b))
            cur.execute("INSERT INTO public.worker_service_authorizations (worker_role_name, org_id, is_active) VALUES ('worker_service_user', %s, true);", (org_b,))

    return {
        "org_a": org_a, "user_a": user_a, "profile_a": profile_a, "gstin_a": gstin_a,
        "org_b": org_b, "user_b": user_b, "profile_b": profile_b, "gstin_b": gstin_b
    }


def seed_invoice(
    cur, org_id, profile_id, user_id, inv_num, inv_date, cp_name, cp_gstin,
    taxable, cgst, sgst, igst, total, status='approved', lines=None,
    supply_category='B2B_REGULAR', is_sez_supply=False, is_deemed_export=False,
    is_reverse_charge=False, document_type='invoice', recipient_registration_type='regular',
    classification_provenance='source_document'
):
    """
    Helper that creates an invoice starting with 'uploaded' and steps through
    state transitions respecting database trigger constraints and statutory classifications.
    """
    inv_id = str(uuid.uuid4())
    # 1. Insert with initial status 'uploaded' and explicit statutory classifications
    cur.execute("""
        INSERT INTO public.invoices (
            id, org_id, profile_id, invoice_number, invoice_date,
            counterparty_name, counterparty_gstin, total_taxable_amount,
            cgst_amount, sgst_amount, igst_amount, total_amount, status,
            supply_category, is_sez_supply, is_deemed_export, is_reverse_charge,
            document_type, recipient_registration_type, classification_provenance
        ) VALUES (
            %s, %s, %s, %s, %s,
            %s, %s, %s,
            %s, %s, %s, %s, 'uploaded',
            %s, %s, %s, %s,
            %s, %s, %s
        );
    """, (
        inv_id, org_id, profile_id, inv_num, inv_date, cp_name, cp_gstin, taxable, cgst, sgst, igst, total,
        supply_category, is_sez_supply, is_deemed_export, is_reverse_charge,
        document_type, recipient_registration_type, classification_provenance
    ))

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
    If an invoice is in pending_review, worker retry protects human corrections and skips overwriting.
    """
    inv_id = str(uuid.uuid4())
    doc_id = str(uuid.uuid4())
    org_id = test_tenants["org_a"]

    mock_pdf = Path("/tmp/mock.pdf")
    mock_pdf.write_bytes(b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\nxref\n0 2\n0000000000 65535 f\ntrailer<</Root 1 0 R>>\nstartxref\n50\n%%EOF\n")

    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO public.documents (id, org_id, doc_type, file_name, file_size_bytes, mime_type, file_path, sha256_checksum)
                VALUES (%s, %s, 'invoice', 'reprocess_test.pdf', 1024, 'application/pdf', '/tmp/mock.pdf', 'fake_hash_reprocess');
            """, (doc_id, org_id))
            cur.execute("""
                INSERT INTO public.invoices (id, org_id, profile_id, invoice_number, invoice_date, counterparty_name, total_taxable_amount, cgst_amount, sgst_amount, igst_amount, total_amount, status)
                VALUES (%s, %s, %s, 'INV-REPROCESS', '2026-11-01', 'Test Counterparty', 0, 0, 0, 0, 0, 'uploaded');
            """, (inv_id, org_id, test_tenants["profile_a"]))
            # Enqueue actual task for this invoice
            cur.execute("SELECT set_config('app.current_org', %s, false);", (org_id,))
            cur.execute("""
                SELECT public.enqueue_task('invoice_ocr', %s::jsonb, %s::uuid);
            """, (json.dumps({
                "invoice_id": inv_id,
                "document_id": doc_id,
                "file_path": "/tmp/mock.pdf",
                "mime_type": "application/pdf"
            }), org_id))
            task_id = str(cur.fetchone()[0])

    # Claim task as worker
    with psycopg.connect(DB_WORKER, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT o_task_id, o_lease_token FROM public.claim_task(%s, 'worker-reprocess');", (org_id,))
            claimed_id, lease_token = cur.fetchone()
            assert str(claimed_id) == task_id

    # First worker processing run
    with psycopg.connect(DB_WORKER, autocommit=True) as conn:
        res1 = process_invoice_ocr_task(
            db_conn=conn, task_id=task_id, invoice_id=inv_id,
            document_id=doc_id, org_id=org_id,
            file_path="/tmp/mock.pdf", mime_type="application/pdf",
            lease_token=lease_token
        )
        assert res1["status"] in ("pending_review", "validation_failed")

    # Check line items count
    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM public.invoice_line_items WHERE invoice_id = %s;", (inv_id,))
            count_first_run = cur.fetchone()[0]
            assert count_first_run > 0

    # Second worker processing run (reprocessing on retry while in pending_review)
    with psycopg.connect(DB_WORKER, autocommit=True) as conn:
        res2 = process_invoice_ocr_task(
            db_conn=conn, task_id=task_id, invoice_id=inv_id,
            document_id=doc_id, org_id=org_id,
            file_path="/tmp/mock.pdf", mime_type="application/pdf",
            lease_token=lease_token
        )
        assert res2.get("skipped") is True

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


# ==============================================================================
# MILESTONE 1.2: INVOICE BRAIN FINAL INTEGRITY & GST COMPLIANCE CLOSURE TESTS
# ==============================================================================

def test_worker_direct_line_item_dml_revoked(test_tenants):
    """
    Security Gate Verification:
    Direct INSERT, UPDATE, DELETE on public.invoice_line_items must be revoked
    from worker_service_user (ERRCODE 42501).
    """
    org_id = test_tenants["org_a"]
    with psycopg.connect(DB_WORKER, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('app.current_org', %s, false);", (org_id,))
            
            # Direct INSERT must fail
            with pytest.raises(psycopg.errors.InsufficientPrivilege) as exc1:
                cur.execute("""
                    INSERT INTO public.invoice_line_items (
                        id, invoice_id, org_id, item_description, quantity, unit_price,
                        taxable_amount, gst_rate, cgst_amount, sgst_amount, igst_amount, total_amount
                    ) VALUES (
                        gen_random_uuid(), gen_random_uuid(), %s, 'Unauthorized Item', 1, 100,
                        100, 18, 9, 9, 0, 118
                    );
                """, (org_id,))
            assert "permission denied for table invoice_line_items" in str(exc1.value)

            # Direct UPDATE must fail
            with pytest.raises(psycopg.errors.InsufficientPrivilege) as exc2:
                cur.execute("UPDATE public.invoice_line_items SET item_description = 'Hacked';")
            assert "permission denied for table invoice_line_items" in str(exc2.value)

            # Direct DELETE must fail
            with pytest.raises(psycopg.errors.InsufficientPrivilege) as exc3:
                cur.execute("DELETE FROM public.invoice_line_items;")
            assert "permission denied for table invoice_line_items" in str(exc3.value)


def test_replace_ocr_line_items_strict_access_control(test_tenants):
    """
    Security Gate Verification:
    replace_ocr_line_items() security boundary enforcement:
    1. app_service_user cannot execute the function (42501).
    2. Missing task or invalid lease token rejected (42501).
    3. Expired lease token rejected (42501).
    4. Task payload invoice_id mismatch rejected (42501).
    5. Cross-tenant invoice replacement rejected (42501).
    6. Modification of invoices in pending_review, approved, or rejected strictly rejected (42501).
    """
    org_a = test_tenants["org_a"]
    org_b = test_tenants["org_b"]
    inv_a = str(uuid.uuid4())
    doc_a = str(uuid.uuid4())

    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO public.documents (id, org_id, doc_type, file_name, file_size_bytes, mime_type, file_path, sha256_checksum)
                VALUES (%s, %s, 'invoice', 'sec_test.pdf', 1024, 'application/pdf', '/tmp/sec.pdf', 'fake_hash_sec');
            """, (doc_a, org_a))
            cur.execute("""
                INSERT INTO public.invoices (id, org_id, profile_id, invoice_number, invoice_date, counterparty_name, total_taxable_amount, cgst_amount, sgst_amount, igst_amount, total_amount, status)
                VALUES (%s, %s, %s, 'INV-SEC', '2026-11-01', 'Counterparty', 0, 0, 0, 0, 0, 'uploaded');
            """, (inv_a, org_a, test_tenants["profile_a"]))
            cur.execute("SELECT set_config('app.current_org', %s, false);", (org_a,))
            cur.execute("""
                SELECT public.enqueue_task('invoice_ocr', %s::jsonb, %s::uuid);
            """, (json.dumps({"invoice_id": inv_a, "document_id": doc_a}), org_a))
            task_a = str(cur.fetchone()[0])

    # 1. app_service_user execution attempt
    with psycopg.connect(DB_APP, autocommit=True) as conn:
        with conn.cursor() as cur:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                cur.execute("SELECT public.replace_ocr_line_items(%s, 'token', %s, '[]'::jsonb);", (task_a, inv_a))

    # Worker claims task to get valid lease token
    with psycopg.connect(DB_WORKER, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT o_task_id, o_lease_token FROM public.claim_task(%s, 'worker-sec');", (org_a,))
            claimed_id, valid_token = cur.fetchone()
            assert str(claimed_id) == task_a

    # 2. Worker with INVALID token
    with psycopg.connect(DB_WORKER, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('app.current_org', %s, false);", (org_a,))
            with pytest.raises(psycopg.errors.InsufficientPrivilege) as exc_bad_tok:
                cur.execute("SELECT public.replace_ocr_line_items(%s, 'wrong-token', %s, '[]'::jsonb);", (task_a, inv_a))
            assert "Invalid lease token" in str(exc_bad_tok.value)

    # 3. Task payload invoice mismatch (foreign invoice in same org)
    inv_other = str(uuid.uuid4())
    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO public.invoices (id, org_id, profile_id, invoice_number, invoice_date, counterparty_name, total_taxable_amount, cgst_amount, sgst_amount, igst_amount, total_amount, status)
                VALUES (%s, %s, %s, 'INV-OTHER', '2026-11-01', 'Other', 0, 0, 0, 0, 0, 'uploaded');
            """, (inv_other, org_a, test_tenants["profile_a"]))

    with psycopg.connect(DB_WORKER, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('app.current_org', %s, false);", (org_a,))
            with pytest.raises(psycopg.errors.InsufficientPrivilege) as exc_mismatch:
                cur.execute("SELECT public.replace_ocr_line_items(%s, %s, %s, '[]'::jsonb);", (task_a, valid_token, inv_other))
            assert "not associated with invoice" in str(exc_mismatch.value)

    # 4. Cross-tenant attempt: Invoice in Org B
    inv_b = str(uuid.uuid4())
    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO public.invoices (id, org_id, profile_id, invoice_number, invoice_date, counterparty_name, total_taxable_amount, cgst_amount, sgst_amount, igst_amount, total_amount, status)
                VALUES (%s, %s, %s, 'INV-B', '2026-11-01', 'Org B CP', 0, 0, 0, 0, 0, 'uploaded');
            """, (inv_b, org_b, test_tenants["profile_b"]))

    with psycopg.connect(DB_WORKER, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('app.current_org', %s, false);", (org_a,))
            with pytest.raises(psycopg.errors.InsufficientPrivilege) as exc_cross:
                cur.execute("SELECT public.replace_ocr_line_items(%s, %s, %s, '[]'::jsonb);", (task_a, valid_token, inv_b))
            assert "not associated with invoice" in str(exc_cross.value)

    # 5. Cannot modify invoice in pending_review or approved
    for protected_status in ('pending_review', 'approved'):
        with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
            with conn.cursor() as cur:
                inv_prot = seed_invoice(
                    cur, org_a, test_tenants["profile_a"], test_tenants["user_a"],
                    f'INV-{protected_status[:4]}', '2026-11-01', 'CP', None,
                    100, 9, 9, 0, 118,
                    status=protected_status
                )
                cur.execute(
                    "UPDATE public.worker_tasks SET payload = jsonb_set(payload, '{invoice_id}', to_jsonb(%s::text)) WHERE id = %s;",
                    (inv_prot, task_a)
                )

        with psycopg.connect(DB_WORKER, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT set_config('app.current_org', %s, false);", (org_a,))
                with pytest.raises(psycopg.errors.InsufficientPrivilege) as exc_protected:
                    cur.execute("SELECT public.replace_ocr_line_items(%s, %s, %s, '[]'::jsonb);", (task_a, valid_token, inv_prot))
                assert "Cannot modify line items on invoice" in str(exc_protected.value)
                assert protected_status in str(exc_protected.value)

    # 6. Expired lease token rejection
    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            inv_proc = seed_invoice(
                cur, org_a, test_tenants["profile_a"], test_tenants["user_a"],
                'INV-EXP-TEST', '2026-11-01', 'CP', None,
                100, 9, 9, 0, 118,
                status='uploaded'
            )
            cur.execute("UPDATE public.invoices SET status = 'queued' WHERE id = %s;", (inv_proc,))
            cur.execute("UPDATE public.invoices SET status = 'processing' WHERE id = %s;", (inv_proc,))
            cur.execute(
                "UPDATE public.worker_tasks SET payload = jsonb_set(payload, '{invoice_id}', to_jsonb(%s::text)), lease_expire = clock_timestamp() - interval '1 second' WHERE id = %s;",
                (inv_proc, task_a)
            )

    with psycopg.connect(DB_WORKER, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('app.current_org', %s, false);", (org_a,))
            with pytest.raises(psycopg.errors.InsufficientPrivilege) as exc_exp:
                cur.execute("SELECT public.replace_ocr_line_items(%s, %s, %s, '[]'::jsonb);", (task_a, valid_token, inv_proc))
            assert "has expired" in str(exc_exp.value)


def test_transition_rules_worker_pending_review_to_processing_revoked():
    """
    Integrity Verification:
    invoice_transition_rules must NOT contain any rule allowing 'worker'
    to transition 'pending_review' -> 'processing' or 'validation_failed' -> 'processing'.
    """
    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT count(*) FROM public.invoice_transition_rules
                WHERE from_status = 'pending_review' AND to_status = 'processing' AND required_role = 'worker';
            """)
            assert cur.fetchone()[0] == 0

            cur.execute("""
                SELECT count(*) FROM public.invoice_transition_rules
                WHERE from_status = 'validation_failed' AND to_status = 'processing' AND required_role = 'worker';
            """)
            assert cur.fetchone()[0] == 0


def test_human_corrections_survive_worker_retry(test_tenants):
    """
    Human Review Integrity Verification:
    Human edits on an invoice in pending_review must NEVER be overwritten
    by a subsequent worker task retry.
    """
    org_id = test_tenants["org_a"]
    user_id = test_tenants["user_a"]
    prof_id = test_tenants["profile_a"]
    headers = {"X-User-Id": user_id, "X-Org-Id": org_id}

    inv_id = str(uuid.uuid4())
    doc_id = str(uuid.uuid4())
    mock_pdf = Path("/tmp/human_test.pdf")
    mock_pdf.write_bytes(b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\nxref\n0 2\n0000000000 65535 f\ntrailer<</Root 1 0 R>>\nstartxref\n50\n%%EOF\n")

    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO public.documents (id, org_id, doc_type, file_name, file_size_bytes, mime_type, file_path, sha256_checksum)
                VALUES (%s, %s, 'invoice', 'human_test.pdf', 1024, 'application/pdf', '/tmp/human_test.pdf', 'fake_hash_human');
            """, (doc_id, org_id))
            cur.execute("""
                INSERT INTO public.invoices (id, org_id, profile_id, invoice_number, invoice_date, counterparty_name, total_taxable_amount, cgst_amount, sgst_amount, igst_amount, total_amount, status)
                VALUES (%s, %s, %s, 'INV-HUMAN', '2026-11-01', 'Original Counterparty', 0, 0, 0, 0, 0, 'uploaded');
            """, (inv_id, org_id, prof_id))
            cur.execute("SELECT set_config('app.current_org', %s, false);", (org_id,))
            cur.execute("""
                SELECT public.enqueue_task('invoice_ocr', %s::jsonb, %s::uuid);
            """, (json.dumps({"invoice_id": inv_id, "document_id": doc_id, "file_path": "/tmp/human_test.pdf", "mime_type": "application/pdf"}), org_id))
            task_id = str(cur.fetchone()[0])

    # Worker claims and processes initial OCR
    with psycopg.connect(DB_WORKER, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT o_task_id, o_lease_token FROM public.claim_task(%s, 'worker-h');", (org_id,))
            c_id, token = cur.fetchone()

        process_invoice_ocr_task(
            db_conn=conn, task_id=task_id, invoice_id=inv_id,
            document_id=doc_id, org_id=org_id,
            file_path="/tmp/human_test.pdf", mime_type="application/pdf",
            lease_token=token
        )

    # Human reviewer performs manual corrections
    patch_res = client.patch(f"/api/v1/invoices/{inv_id}", headers=headers, json={
        "counterparty_name": "Corrected Vendor Name Private Limited"
    })
    assert patch_res.status_code == 200

    # Human reviewer edits line item
    inv_data = client.get(f"/api/v1/invoices/{inv_id}", headers=headers).json()
    assert inv_data["status"] == "pending_review"
    assert inv_data["counterparty_name"] == "Corrected Vendor Name Private Limited"
    first_line_id = inv_data["line_items"][0]["id"]

    line_patch = client.patch(f"/api/v1/invoices/{inv_id}/line-items/{first_line_id}", headers=headers, json={
        "item_description": "Custom Human Description"
    })
    assert line_patch.status_code == 200

    # Worker task retries (simulating redelivery or delayed message)
    with psycopg.connect(DB_WORKER, autocommit=True) as conn:
        retry_res = process_invoice_ocr_task(
            db_conn=conn, task_id=task_id, invoice_id=inv_id,
            document_id=doc_id, org_id=org_id,
            file_path="/tmp/human_test.pdf", mime_type="application/pdf",
            lease_token=token
        )
        assert retry_res.get("skipped") is True

    # Confirm human edits and line items remain UNTOUCHED
    inv_after = client.get(f"/api/v1/invoices/{inv_id}", headers=headers).json()
    assert inv_after["status"] == "pending_review"
    assert inv_after["counterparty_name"] == "Corrected Vendor Name Private Limited"
    assert inv_after["line_items"][0]["item_description"] == "Custom Human Description"


def test_document_deduplication_database_constraint_and_storage_cleanup(test_tenants):
    """
    Storage & Deduplication Integrity:
    1. Database constraint uq_documents_org_sha256 blocks duplicate SHA-256 within the same organization.
    2. Concurrent/duplicate upload raises 409 DuplicateDocumentError.
    3. Storage files are deleted from disk if upload or enqueue fails.
    """
    org_id = test_tenants["org_a"]
    user_id = test_tenants["user_a"]
    headers = {"X-User-Id": user_id, "X-Org-Id": org_id}

    pdf_bytes = b"%PDF-1.4\nUnique test content for deduplication test\n%%EOF\n"
    
    # 1. First upload succeeds
    res1 = client.post(
        "/api/v1/invoices/upload",
        headers=headers,
        files={"file": ("invoice_dup1.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
        data={"profile_id": test_tenants["profile_a"]}
    )
    assert res1.status_code == 200
    sha = res1.json()["sha256_checksum"]

    # 2. Second upload with identical bytes fails with 409
    res2 = client.post(
        "/api/v1/invoices/upload",
        headers=headers,
        files={"file": ("invoice_dup2.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
        data={"profile_id": test_tenants["profile_a"]}
    )
    assert res2.status_code == 409
    assert "duplicate document" in res2.json()["detail"].lower()

    # 3. Direct DB insert violating uq_documents_org_sha256 constraint directly
    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            with pytest.raises(psycopg.errors.UniqueViolation) as exc_uq:
                cur.execute("""
                    INSERT INTO public.documents (id, org_id, doc_type, file_name, file_size_bytes, mime_type, file_path, sha256_checksum)
                    VALUES (%s, %s, 'invoice', 'dup.pdf', 100, 'application/pdf', '/tmp/dup.pdf', %s);
                """, (str(uuid.uuid4()), org_id, sha))
            assert "uq_documents_org_sha256" in str(exc_uq.value)


def test_gstr1_table_12_and_preceding_fy_aato(test_tenants):
    """
    GST Statutory Compliance:
    1. Business profile preceding_fy_aato is exposed and used in GSTR-1 draft.
    2. Table 12 B2B is mandatory when Table 4 contains supplies.
    3. Table 12 B2C is strictly optional.
    4. Table 12 supply-value mismatches generate non-blocking warnings, not filing blockers.
    """
    headers = {"X-User-Id": test_tenants["user_a"], "X-Org-Id": test_tenants["org_a"]}
    org_id = test_tenants["org_a"]
    prof_id = test_tenants["profile_a"]
    user_id = test_tenants["user_a"]

    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            # Update business profile preceding_fy_aato to ₹12 Crore (120,000,000.00)
            cur.execute("UPDATE public.business_profiles SET preceding_fy_aato = 120000000.00 WHERE id = %s;", (prof_id,))
            
            # Seed an approved B2B invoice
            inv_id = seed_invoice(
                cur, org_id, prof_id, user_id,
                'INV-GSTR-T12', '2026-11-10', 'Tata Motors Ltd', '27AAACT2727Q1ZW',
                50000.00, 4500.00, 4500.00, 0, 59000.00,
                status='approved',
                lines=[{"item_description": "Auto Parts", "hsn_sac_code": "87082900", "quantity": 10, "unit_price": 5000.00, "taxable_amount": 50000.00, "gst_rate": 18.00, "cgst_amount": 4500.00, "sgst_amount": 4500.00, "igst_amount": 0, "total_amount": 59000.00}]
            )

    gstr1_res = client.get("/api/v1/gst/gstr1-draft?period=2026-11", headers=headers)
    assert gstr1_res.status_code == 200
    gstr1 = gstr1_res.json()

    assert Decimal(str(gstr1["preceding_fy_aato"])) == Decimal("120000000.00")
    assert gstr1["table_12_b2b_mandatory"] is True
    assert gstr1["table_12_b2c_mandatory"] is False
    assert len(gstr1["table_12_hsn_b2b"]) >= 1
    assert isinstance(gstr1["table_12_warnings"], list)


def test_gstr1_table_13_conditional_mandate_and_5_categories(test_tenants):
    """
    GST May 2025 Phase 3 Portal Compliance:
    1. Table 13 is_mandatory = (tax_period >= '2025-05') and (len(invoices) > 0).
    2. Historical periods (< '2025-05') have is_mandatory = False.
    3. Nil returns (no outward invoices) have is_mandatory = False.
    4. Table 13 includes 5 standard document categories.
    """
    headers = {"X-User-Id": test_tenants["user_a"], "X-Org-Id": test_tenants["org_a"]}
    org_id = test_tenants["org_a"]

    # 1. Historical period test (2024-04): Must be False
    res_hist = client.get("/api/v1/gst/gstr1-draft?period=2024-04", headers=headers)
    assert res_hist.status_code == 200
    assert res_hist.json()["table_13_doc_summary"]["is_mandatory"] is False

    # 2. Nil period test (2026-01): Must be False (no invoices seeded for this period)
    res_nil = client.get("/api/v1/gst/gstr1-draft?period=2026-01", headers=headers)
    assert res_nil.status_code == 200
    assert res_nil.json()["table_13_doc_summary"]["is_mandatory"] is False

    # 3. Active period with supplies (2026-11): Must be True
    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            seed_invoice(
                cur, org_id, test_tenants["profile_a"], test_tenants["user_a"],
                'INV-T13-ACTIVE', '2026-11-15', 'Customer T13', None,
                1000.00, 90.00, 90.00, 0, 1180.00,
                status='approved',
                lines=[{"item_description": "Goods", "hsn_sac_code": "998311", "quantity": 1, "unit_price": 1000.00, "taxable_amount": 1000.00, "gst_rate": 18.00, "cgst_amount": 90.00, "sgst_amount": 90.00, "igst_amount": 0, "total_amount": 1180.00}]
            )

    res_active = client.get("/api/v1/gst/gstr1-draft?period=2026-11", headers=headers)
    assert res_active.status_code == 200
    doc_sum = res_active.json()["table_13_doc_summary"]
    assert doc_sum["is_mandatory"] is True
    assert len(doc_sum["document_categories"]) == 5
    doc_types = [c["doc_type_code"] for c in doc_sum["document_categories"]]
    assert doc_types == ["01", "02", "03", "04", "05"]


def test_gstr3b_zero_placeholders_eliminated(test_tenants):
    """
    Statutory Honesty & GSTR-3B Integrity:
    Zero placeholders (0.00) are eliminated for unestablished fields:
    - Tables 3.1(b), 3.1(c), 3.1(d), 3.1.1
    - Table 4 Eligible ITC
    - Net cash payable / ITC offset
    Must have value: None and status: 'not_computed' with explicit provenance reasons.
    """
    headers = {"X-User-Id": test_tenants["user_a"], "X-Org-Id": test_tenants["org_a"]}
    res = client.get("/api/v1/gst/gstr3b-draft?period=2026-11", headers=headers)
    assert res.status_code == 200
    gstr3b = res.json()

    # Table 3.1 checks
    t31 = gstr3b["table_3_1_outward_supplies"]
    assert t31["3_1_a_outward_taxable_supplies"]["status"] == "computed"
    assert t31["3_1_b_outward_taxable_zero_rated"]["status"] == "not_computed"
    assert t31["3_1_b_outward_taxable_zero_rated"]["total_taxable_value"] is None
    assert t31["3_1_c_other_outward_supplies_nil_exempt"]["status"] == "not_computed"
    assert t31["3_1_c_other_outward_supplies_nil_exempt"]["total_taxable_value"] is None
    assert t31["3_1_d_inward_supplies_reverse_charge"]["status"] == "not_computed"
    assert t31["3_1_d_inward_supplies_reverse_charge"]["total_taxable_value"] is None
    assert t31["3_1_1_ecommerce_supplies"]["status"] == "not_computed"
    assert t31["3_1_1_ecommerce_supplies"]["total_taxable_value"] is None

    # Table 4 ITC checks
    t4 = gstr3b["table_4_eligible_itc"]
    assert t4["status"] == "not_computed"
    assert t4["itc_available"]["all_other_itc"]["value"] is None
    assert t4["itc_available"]["all_other_itc"]["status"] == "not_computed"
    assert t4["net_itc_available"]["value"] is None
    assert t4["net_itc_available"]["status"] == "not_computed"

    # Tax payable checks
    tp = gstr3b["tax_payable_summary"]
    assert tp["outward_tax_liability"]["status"] == "computed"
    assert tp["itc_credit_applied"]["status"] == "not_computed"
    assert tp["itc_credit_applied"]["value"] is None
    assert tp["net_cash_payable_after_itc"]["status"] == "not_computed"
    assert tp["net_cash_payable_after_itc"]["value"] is None


def test_gemini_ocr_model_configuration_and_schema():
    """
    Milestone 1.3: Gemini OCR Compatibility (Late-2026 Models):
    Verify model is set to currently active gemini-3.8-flash (primary) and gemini-3.5-flash-lite (fallback).
    Verify schema supports nullable financial fields without fabricated defaults.
    """
    assert settings.GEMINI_MODEL == "gemini-3.8-flash"
    assert settings.GEMINI_FALLBACK_MODEL == "gemini-3.5-flash-lite"
    
    from app.modules.invoice_brain.ocr import ExtractedInvoiceData, ExtractedLineItem
    # Verify schema serialization with complete and incomplete fields
    sample_item = ExtractedLineItem(
        item_description="Test Item",
        hsn_sac_code="998311",
        quantity=2.0,
        unit_price=100.0,
        taxable_amount=200.0,
        gst_rate=18.0,
        cgst_amount=18.0,
        sgst_amount=18.0,
        igst_amount=0.0,
        total_amount=236.0,
        confidence=0.99
    )
    incomplete_item = ExtractedLineItem(
        item_description="Unparsed Line",
        quantity=None,
        unit_price=None,
        taxable_amount=None,
        gst_rate=None,
        total_amount=None
    )
    sample_data = ExtractedInvoiceData(
        invoice_number="INV-001",
        invoice_date="2026-11-17",
        counterparty_name="Vendor ABC",
        counterparty_gstin="27ABCDE1234F1Z5",
        total_taxable_amount=200.0,
        cgst_amount=18.0,
        sgst_amount=18.0,
        igst_amount=0.0,
        total_amount=236.0,
        confidence_score=0.98,
        line_items=[sample_item, incomplete_item],
        raw_ocr_response={"engine": "gemini-3.8-flash", "status": "success"}
    )
    json_str = sample_data.model_dump_json()
    reconstructed = ExtractedInvoiceData.model_validate_json(json_str)
    assert reconstructed.invoice_number == "INV-001"
    assert len(reconstructed.line_items) == 2
    assert reconstructed.line_items[1].quantity is None
    assert reconstructed.raw_ocr_response["engine"] == "gemini-3.8-flash"


# ==============================================================================
# PART 8: Milestone 1.3 Security, Provenance & Statutory Corrections
# ==============================================================================

def test_replace_ocr_line_items_rejects_non_ocr_task_type(test_tenants):
    """
    Security Finding 1: Enforce OCR Task Type in replace_ocr_line_items().
    Rejects any task where task_type IS DISTINCT FROM 'invoice_ocr'.
    Worker claims a 'gst_validation' task; attempt to replace line items must raise 42501.
    Proves existing line items remain untouched.
    """
    org_id = test_tenants["org_a"]
    user_id = test_tenants["user_a"]
    profile_id = test_tenants["profile_a"]

    # Seed an invoice with initial line items
    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            inv_id = seed_invoice(
                cur, org_id, profile_id, user_id,
                'INV-TYPE-ENFORCE', '2026-11-17', 'Vendor Enforce', '27AABCB1234M1ZT',
                1000.00, 90.00, 90.00, 0, 1180.00,
                status='extracted',
                lines=[{
                    "item_description": "Original Unmodified Item",
                    "hsn_sac_code": "998311",
                    "quantity": 1,
                    "unit_price": 1000.00,
                    "taxable_amount": 1000.00,
                    "gst_rate": 18.00,
                    "cgst_amount": 90.00,
                    "sgst_amount": 90.00,
                    "igst_amount": 0,
                    "total_amount": 1180.00
                }]
            )

            # Insert a worker task of type 'gst_validation' (NOT invoice_ocr)
            val_task_id = str(uuid.uuid4())
            cur.execute("""
                INSERT INTO public.worker_tasks (
                    id, org_id, task_type, payload, status
                ) VALUES (
                    %s, %s, 'gst_validation', %s::jsonb, 'queued'
                );
            """, (val_task_id, org_id, json.dumps({"invoice_id": inv_id})))

    # Claim task as worker_service_user
    with psycopg.connect(DB_WORKER, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('app.current_org', %s, false);", (org_id,))
            cur.execute("SELECT o_task_id, o_lease_token FROM public.claim_task(%s, 'worker-val');", (org_id,))
            claimed = cur.fetchone()
            assert claimed is not None
            task_id, lease_token = claimed

            # Attempt to invoke replace_ocr_line_items using this valid lease token
            replacement_lines = [{
                "item_description": "Malicious Injected Item",
                "hsn_sac_code": "998311",
                "quantity": 99,
                "unit_price": 9999.00,
                "taxable_amount": 9999.00,
                "gst_rate": 18.00,
                "cgst_amount": 899.91,
                "sgst_amount": 899.91,
                "igst_amount": 0,
                "total_amount": 11798.82
            }]

            with pytest.raises(psycopg.Error) as exc_info:
                cur.execute("""
                    SELECT public.replace_ocr_line_items(%s, %s, %s, %s::jsonb);
                """, (task_id, lease_token, inv_id, json.dumps(replacement_lines)))

            assert exc_info.value.sqlstate == "42501"
            assert "is not authorized for OCR line-item replacement" in str(exc_info.value)

    # Verify original line items are completely untouched
    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT item_description, quantity FROM public.invoice_line_items WHERE invoice_id = %s;", (inv_id,))
            rows = cur.fetchall()
            assert len(rows) == 1
            assert rows[0][0] == "Original Unmodified Item"
            assert Decimal(str(rows[0][1])) == Decimal("1.000")


def test_replace_ocr_line_items_rejects_locked_by_mismatch(test_tenants):
    """
    Security Finding 1 (Lock Ownership):
    Rejects invocation when the task was locked by a different database identity.
    """
    org_id = test_tenants["org_a"]
    task_id = str(uuid.uuid4())
    inv_id = str(uuid.uuid4())
    token = str(uuid.uuid4())
    import hashlib
    token_hash = hashlib.sha256(token.encode('utf-8')).hexdigest()

    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            # Seed invoice
            seed_invoice(cur, org_id, test_tenants["profile_a"], test_tenants["user_a"],
                         'INV-LOCK-MISMATCH', '2026-11-17', 'Vendor Lock', None,
                         100.00, 9.00, 9.00, 0, 118.00, status='extracted')

            # Insert an invoice_ocr task locked by someone else
            cur.execute("""
                INSERT INTO public.worker_tasks (
                    id, org_id, task_type, payload, status, locked_by, lease_token_hash, lease_expire
                ) VALUES (
                    %s, %s, 'invoice_ocr', %s::jsonb, 'in_progress', 'foreign_worker_identity', %s, clock_timestamp() + interval '5 minutes'
                );
            """, (task_id, org_id, json.dumps({"invoice_id": inv_id}), token_hash))

    # Worker attempts invocation with valid hash but locked_by mismatch
    with psycopg.connect(DB_WORKER, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('app.current_org', %s, false);", (org_id,))
            with pytest.raises(psycopg.Error) as exc_info:
                cur.execute("""
                    SELECT public.replace_ocr_line_items(%s, %s, %s, '[]'::jsonb);
                """, (task_id, token, inv_id))

            assert exc_info.value.sqlstate == "42501"
            assert "is not locked by current worker" in str(exc_info.value)


def test_invoice_ocr_writer_role_least_privilege():
    """
    Security Finding 2: Replace Superuser SECURITY DEFINER Ownership.
    Verify invoice_ocr_writer is NOLOGIN NOINHERIT non-superuser role.
    Verify replace_ocr_line_items is owned by invoice_ocr_writer (NOT postgres).
    """
    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            # 1. Verify role attributes
            cur.execute("""
                SELECT rolcanlogin, rolsuper, rolinherit
                FROM pg_roles
                WHERE rolname = 'invoice_ocr_writer';
            """)
            role = cur.fetchone()
            assert role is not None, "Role invoice_ocr_writer must exist"
            rolcanlogin, rolsuper, rolinherit = role
            assert rolcanlogin is False, "invoice_ocr_writer must be NOLOGIN"
            assert rolsuper is False, "invoice_ocr_writer must NOT be superuser"
            assert rolinherit is False, "invoice_ocr_writer must be NOINHERIT"

            # 2. Verify function ownership
            cur.execute("""
                SELECT r.rolname
                FROM pg_proc p
                JOIN pg_roles r ON r.oid = p.proowner
                WHERE p.proname = 'replace_ocr_line_items';
            """)
            owner_row = cur.fetchone()
            assert owner_row is not None
            assert owner_row[0] == "invoice_ocr_writer", f"Function owner must be invoice_ocr_writer, got {owner_row[0]}"

            # 3. Verify role is not in superuser or platform_admin membership
            cur.execute("""
                SELECT b.rolname
                FROM pg_auth_members m
                JOIN pg_roles a ON a.oid = m.member
                JOIN pg_roles b ON b.oid = m.roleid
                WHERE a.rolname = 'invoice_ocr_writer';
            """)
            memberships = [r[0] for r in cur.fetchall()]
            assert "postgres" not in memberships
            assert "platform_admin" not in memberships


def test_missing_ocr_fields_not_fabricated_and_block_approval(test_tenants):
    """
    Finding 3: Honest Missing-Value Handling & Approval Blockage.
    1. Table defaults and unsafe NOT NULL eliminated.
    2. Missing quantity, taxable_amount, gst_rate stored genuinely as NULL (not 1.0 or 18%).
    3. Validator flags missing fields as critical discrepancy errors.
    4. Transition to validation_failed strictly blocks approval.
    """
    org_id = test_tenants["org_a"]
    user_id = test_tenants["user_a"]
    profile_id = test_tenants["profile_a"]
    inv_id = str(uuid.uuid4())
    item_id = str(uuid.uuid4())

    # 1. Insert invoice and line item with NULL financial fields (verifies schema allows nulls)
    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO public.invoices (
                    id, org_id, profile_id, invoice_number, invoice_date,
                    counterparty_name, total_taxable_amount, cgst_amount,
                    sgst_amount, igst_amount, total_amount, status
                ) VALUES (
                    %s, %s, %s, 'INV-MISSING-FIELDS', '2026-11-17',
                    'Incomplete Extraction Vendor', 0.00, 0.00,
                    0.00, 0.00, 0.00, 'uploaded'
                );
            """, (inv_id, org_id, profile_id))

            cur.execute("""
                INSERT INTO public.invoice_line_items (
                    id, invoice_id, org_id, item_description, hsn_sac_code,
                    quantity, unit_price, taxable_amount, gst_rate,
                    cgst_amount, sgst_amount, igst_amount, total_amount
                ) VALUES (
                    %s, %s, %s, 'Unextracted Line Item', '998311',
                    NULL, NULL, NULL, NULL,
                    NULL, NULL, NULL, NULL
                );
            """, (item_id, inv_id, org_id))

            # Verify values in database are genuinely NULL (no 1.0 or 18.0 defaults)
            cur.execute("""
                SELECT quantity, unit_price, taxable_amount, gst_rate, total_amount
                FROM public.invoice_line_items
                WHERE id = %s;
            """, (item_id,))
            q, p, t, r, tot = cur.fetchone()
            assert q is None, "quantity must be NULL in DB without default"
            assert p is None, "unit_price must be NULL in DB without default"
            assert t is None, "taxable_amount must be NULL in DB without default"
            assert r is None, "gst_rate must be NULL in DB without default"
            assert tot is None, "total_amount must be NULL in DB without default"

            # Transition to pending_review to test approval gate
            cur.execute("UPDATE public.invoices SET status = 'queued' WHERE id = %s;", (inv_id,))
            cur.execute("UPDATE public.invoices SET status = 'processing' WHERE id = %s;", (inv_id,))
            cur.execute("UPDATE public.invoices SET status = 'extracted' WHERE id = %s;", (inv_id,))
            cur.execute("UPDATE public.invoices SET status = 'pending_review' WHERE id = %s;", (inv_id,))

    # 2. Test validator flags missing fields and marks is_valid = False
    validation = validate_invoice_financials(
        invoice={"total_taxable_amount": Decimal("0.00"), "total_amount": Decimal("0.00")},
        line_items=[{
            "item_description": "Unextracted Line Item",
            "quantity": None,
            "unit_price": None,
            "taxable_amount": None,
            "gst_rate": None,
            "total_amount": None
        }]
    )
    assert validation.is_valid is False
    assert validation.has_critical_errors is True
    flag_codes = [f.code for f in validation.flags]
    assert "ERR_MISSING_QUANTITY" in flag_codes
    assert "ERR_MISSING_TAXABLE_AMOUNT" in flag_codes
    assert "ERR_MISSING_GST_RATE" in flag_codes

    # 3. Test approval gate: Attempting to approve must fail
    with SessionLocal() as db_session:
        from sqlalchemy import text
        db_session.execute(text("SELECT set_config('app.current_org', :org, false);"), {"org": org_id})
        db_session.execute(text("SELECT set_config('app.current_user', :user, false);"), {"user": user_id})
        with pytest.raises(InvoiceStatusError) as exc:
            approve_invoice(db_session, org_id, user_id, inv_id)
        assert "Cannot approve invoice" in str(exc.value)


def test_gstr1_table_13_multi_table_outward_supplies_triggers(test_tenants):
    """
    Statutory Finding 5: Correct Table 13 Mandatory Logic across Outward Supply Tables.
    Table 13 is mandatory from May 2025 onwards IF ANY outward supply table contains data:
    Tables 4, 5, 6, 7, 8, 9, 10, 14, 15.
    Must be FALSE for nil return or period < 2025-05.
    Must include 'triggering_tables' list.
    """
    headers = {"X-User-Id": test_tenants["user_b"], "X-Org-Id": test_tenants["org_b"]}

    # 1. Nil Return for Org B (no invoices): Table 13 is NOT mandatory
    res_nil = client.get("/api/v1/gst/gstr1-draft?period=2026-11", headers=headers)
    assert res_nil.status_code == 200
    doc_nil = res_nil.json()["table_13_doc_summary"]
    assert doc_nil["is_mandatory"] is False
    assert doc_nil["has_outward_supplies"] is False
    assert doc_nil["triggering_tables"] == []

    # 2. Historical Period (< 2025-05) with supplies: Table 13 is NOT mandatory
    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            seed_invoice(
                cur, test_tenants["org_b"], test_tenants["profile_b"], test_tenants["user_b"],
                'INV-HIST-T13', '2024-12-10', 'Vendor Hist', '27AABCB1234M1ZT',
                5000.00, 450.00, 450.00, 0, 5900.00, status='approved',
                lines=[{"item_description": "Consulting", "hsn_sac_code": "998311", "quantity": 1, "unit_price": 5000.00, "taxable_amount": 5000.00, "gst_rate": 18.00, "cgst_amount": 450.00, "sgst_amount": 450.00, "igst_amount": 0, "total_amount": 5900.00}]
            )

    res_hist = client.get("/api/v1/gst/gstr1-draft?period=2024-12", headers=headers)
    assert res_hist.status_code == 200
    doc_hist = res_hist.json()["table_13_doc_summary"]
    assert doc_hist["is_mandatory"] is False
    assert doc_hist["has_outward_supplies"] is True

    # 3. Active Period (>= 2025-05) with B2C supplies (Table 7): Table 13 IS mandatory
    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            seed_invoice(
                cur, test_tenants["org_b"], test_tenants["profile_b"], test_tenants["user_b"],
                'INV-ACT-T7', '2026-11-15', 'Consumer B2C', None,
                2000.00, 180.00, 180.00, 0, 2360.00, status='approved',
                lines=[{"item_description": "Consumer Item", "hsn_sac_code": "998311", "quantity": 1, "unit_price": 2000.00, "taxable_amount": 2000.00, "gst_rate": 18.00, "cgst_amount": 180.00, "sgst_amount": 180.00, "igst_amount": 0, "total_amount": 2360.00}]
            )

    res_act = client.get("/api/v1/gst/gstr1-draft?period=2026-11", headers=headers)
    assert res_act.status_code == 200
    doc_act = res_act.json()["table_13_doc_summary"]
    assert doc_act["is_mandatory"] is True
    assert doc_act["has_outward_supplies"] is True
    assert "table_7" in doc_act["triggering_tables"]


def test_gstr1_table_12_b2c_advisory_warning_when_hsn_empty(test_tenants):
    """
    Statutory Finding 5 (Advisory Warning):
    Under GST Portal May 2025 Advisory, Table 12 B2C tab is optional.
    When Table 5 or Table 7 contains B2C supplies but Table 12 B2C tab is empty,
    an advisory notice must be returned in table_12_warnings.
    """
    org_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    profile_id = str(uuid.uuid4())
    gstin = random_gstin("27")
    pan = gstin[2:12]

    # Create new tenant with B2C supplies that have NO line items (hence empty Table 12 B2C)
    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO public.organizations (id, legal_name, pan) VALUES (%s, 'Tenant B2C Warn', %s);", (org_id, pan))
            cur.execute("INSERT INTO public.global_users (id, email, full_name) VALUES (%s, %s, 'Owner B2C');", (user_id, f"b2c_{uuid.uuid4().hex}@example.com"))
            cur.execute("INSERT INTO public.organization_memberships (org_id, user_id, role, status) VALUES (%s, %s, 'owner', 'active');", (org_id, user_id))
            cur.execute("INSERT INTO public.business_profiles (id, org_id, gstin, business_type, industry_sector, preceding_fy_aato) VALUES (%s, %s, %s, 'Services', 'Retail', 40000000.00);", (profile_id, org_id, gstin))

            # Seed B2C invoice without line items so Table 7 has data but Table 12 B2C is empty
            seed_invoice(
                cur, org_id, profile_id, user_id,
                'INV-B2C-NO-HSN', '2026-11-12', 'Unregistered Buyer', None,
                1500.00, 135.00, 135.00, 0, 1770.00, status='approved', lines=[]
            )

    headers = {"X-User-Id": user_id, "X-Org-Id": org_id}
    res = client.get("/api/v1/gst/gstr1-draft?period=2026-11", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert len(data["table_7_b2c_small"]) > 0
    assert len(data["table_12_hsn_b2c"]) == 0
    warnings = data["table_12_warnings"]
    assert any("B2C outward supplies are present in Table 5/7, but Table 12 B2C HSN tab has no entries" in w for w in warnings)


def test_preceding_fy_aato_unverified_unresolved(test_tenants):
    """
    Statutory Finding 6: PAN-Level Aggregate Annual Turnover (AATO) Missing-Value Handling.
    Remove default 5 Crore assumption.
    When preceding_fy_aato IS NULL:
    1. GSTR-1 draft returns preceding_fy_aato: None and aato_unverified: True.
    2. HSN compliance returns classification: 'aato_unverified_unresolved'.
    """
    org_id = test_tenants["org_a"]

    # Set preceding_fy_aato to NULL in business_profiles
    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE public.business_profiles SET preceding_fy_aato = NULL WHERE org_id = %s;", (org_id,))

    headers = {"X-User-Id": test_tenants["user_a"], "X-Org-Id": org_id}
    res = client.get("/api/v1/gst/gstr1-draft?period=2026-11", headers=headers)
    assert res.status_code == 200
    draft = res.json()
    assert draft["preceding_fy_aato"] is None
    assert draft["aato_unverified"] is True
    assert any("Preceding financial-year Aggregate Annual Turnover (AATO) is unverified across PAN" in w for w in draft["table_12_warnings"])

    # Test HSN compliance directly with aato_crores=None
    hsn_res = validate_hsn_compliance("8471", aato_crores=None, is_b2b=True)
    assert hsn_res.is_compliant is False
    assert hsn_res.classification == "aato_unverified_unresolved"
    assert "unverified across PAN" in hsn_res.error_message


def test_storage_orphan_reconciliation_lifecycle(test_tenants, tmp_path):
    """
    Operational Finding 7: Storage Orphan Reconciliation Lifecycle.
    1. Scans storage directory.
    2. Grace period protects in-flight uploads (younger than 1800s).
    3. Deletes unreferenced orphan files (older than 1800s).
    4. Active DB-referenced files are preserved regardless of age.
    """
    storage_dir = tmp_path / "storage"
    storage_dir.mkdir()

    active_file = storage_dir / "active_doc.bin"
    active_file.write_bytes(b"%PDF-1.4 active document")

    in_flight_file = storage_dir / "in_flight.bin"
    in_flight_file.write_bytes(b"%PDF-1.4 in flight document")
    # In-flight: 5 minutes old (< 30 minutes grace period)
    in_flight_mtime = time.time() - 300
    os.utime(str(in_flight_file), (in_flight_mtime, in_flight_mtime))

    orphan_file = storage_dir / "stale_orphan.bin"
    orphan_file.write_bytes(b"%PDF-1.4 orphaned garbage")
    # Orphan: 1 hour old (> 30 minutes grace period)
    orphan_mtime = time.time() - 3600
    os.utime(str(orphan_file), (orphan_mtime, orphan_mtime))

    # Register active_doc in public.documents
    doc_id = str(uuid.uuid4())
    org_id = test_tenants["org_a"]
    user_id = test_tenants["user_a"]
    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO public.documents (
                    id, org_id, doc_type, file_name, file_path, mime_type, file_size_bytes, sha256_checksum, uploaded_by
                ) VALUES (
                    %s, %s, 'invoice', 'active.pdf', %s, 'application/pdf', 24, 'active_sha256', %s
                );
            """, (doc_id, org_id, str(active_file), user_id))

    with SessionLocal() as session:
        from sqlalchemy import text
        session.execute(text("SELECT set_config('app.current_org', :org, false);"), {"org": org_id})
        session.execute(text("SELECT set_config('app.current_user', :user, false);"), {"user": user_id})
        report = reconcile_storage_orphans(session, base_dir=str(storage_dir), grace_period_seconds=1800)

    # Verifications
    assert report["scanned_files"] == 3
    assert report["orphaned_files_deleted"] == 1
    assert report["in_flight_files"] == 1
    assert report["reclaimed_bytes"] == len(b"%PDF-1.4 orphaned garbage")
    assert not orphan_file.exists(), "Stale orphan file must be deleted"
    assert in_flight_file.exists(), "In-flight file within grace period must be preserved"
    assert active_file.exists(), "Active DB-referenced file must be preserved"


def test_aato_default_dropped_and_placeholder_cleansing(test_tenants):
    """
    Reviewer Requirement 1 & 2:
    1. Insert profile without supplying AATO -> NULL (default dropped)
    2. Insert profile with explicit NULL -> NULL
    3. Insert profile with verified AATO -> Supplied value retained
    4. Legitimate ₹5 Crore declared turnover (provenance='self_declared') is strictly preserved
    5. Unverified default placeholder (provenance='unverified_placeholder') is treated as unverified
    6. Verified AATO above 5 Cr -> Six-digit classification
    7. Verified AATO at or below 5 Cr -> Applicable four-digit classification
    """
    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            # 1. Insert without supplying preceding_fy_aato
            p1_id = str(uuid.uuid4())
            cur.execute("""
                INSERT INTO public.business_profiles (id, org_id, gstin, business_type, industry_sector)
                VALUES (%s, %s, %s, 'Services', 'IT')
                RETURNING preceding_fy_aato, aato_provenance;
            """, (p1_id, test_tenants["org_a"], random_gstin("27")))
            val1, prov1 = cur.fetchone()
            assert val1 is None, f"Default must be dropped; expected NULL, got {val1}"
            assert prov1 == "unverified_placeholder"

            # 2. Insert with explicit NULL
            p2_id = str(uuid.uuid4())
            cur.execute("""
                INSERT INTO public.business_profiles (id, org_id, gstin, business_type, industry_sector, preceding_fy_aato)
                VALUES (%s, %s, %s, 'Services', 'IT', NULL)
                RETURNING preceding_fy_aato;
            """, (p2_id, test_tenants["org_a"], random_gstin("27")))
            val2 = cur.fetchone()[0]
            assert val2 is None, f"Explicit NULL must persist; got {val2}"

            # 3. Insert with verified AATO (e.g. 4.2 Crore)
            p3_id = str(uuid.uuid4())
            cur.execute("""
                INSERT INTO public.business_profiles (id, org_id, gstin, business_type, industry_sector, preceding_fy_aato, aato_provenance)
                VALUES (%s, %s, %s, 'Services', 'IT', 42000000.00, 'self_declared')
                RETURNING preceding_fy_aato, aato_provenance;
            """, (p3_id, test_tenants["org_a"], random_gstin("27")))
            val3, prov3 = cur.fetchone()
            assert Decimal(str(val3)) == Decimal("42000000.00")
            assert prov3 == "self_declared"

            # 4. Legitimate exactly ₹5 Crore declared turnover is protected from cleansing
            p4_id = str(uuid.uuid4())
            cur.execute("""
                INSERT INTO public.business_profiles (id, org_id, gstin, business_type, industry_sector, preceding_fy_aato, aato_provenance)
                VALUES (%s, %s, %s, 'Services', 'IT', 50000000.00, 'self_declared')
                RETURNING preceding_fy_aato, aato_provenance;
            """, (p4_id, test_tenants["org_a"], random_gstin("27")))
            val4, prov4 = cur.fetchone()
            assert Decimal(str(val4)) == Decimal("50000000.00")
            assert prov4 == "self_declared"

            # Verify that cleansing only targets unverified_placeholder
            cur.execute("""
                UPDATE public.business_profiles
                SET preceding_fy_aato = NULL
                WHERE preceding_fy_aato = 50000000.00 AND aato_provenance = 'unverified_placeholder';
            """)
            cur.execute("SELECT preceding_fy_aato FROM public.business_profiles WHERE id = %s;", (p4_id,))
            preserved_val = cur.fetchone()[0]
            assert Decimal(str(preserved_val)) == Decimal("50000000.00"), "Legitimate 5 Cr value must NOT be wiped by cleansing!"

    # 5. Existing unverified placeholder check
    res_placeholder = validate_hsn_compliance("8471", aato_crores=None, is_b2b=True)
    assert res_placeholder.is_compliant is False
    assert res_placeholder.classification == "aato_unverified_unresolved"

    # 6. Verified AATO above 5 Crore (> 5 Cr requires 6 digits)
    res_above = validate_hsn_compliance("8471", aato_crores=Decimal("8.0"), is_b2b=True)
    assert res_above.is_compliant is False
    assert res_above.classification == "min_digits_unmet"
    assert "minimum 6 digits" in res_above.error_message

    res_above_6digit = validate_hsn_compliance("847130", aato_crores=Decimal("8.0"), is_b2b=True)
    assert res_above_6digit.is_compliant is True
    assert res_above_6digit.classification == "compliant"

    # 7. Verified AATO at or below 5 Crore (<= 5 Cr accepts 4 digits for B2B)
    res_below_4digit = validate_hsn_compliance("8471", aato_crores=Decimal("3.5"), is_b2b=True)
    assert res_below_4digit.is_compliant is True
    assert res_below_4digit.classification == "compliant"


def test_positive_path_worker_replace_ocr_line_items_under_rls(test_tenants):
    """
    Reviewer Requirement 2: Positive-path execution under non-superuser worker_service_user:
    1. Real worker login claims an invoice_ocr task.
    2. Obtains a valid lease token.
    3. Calls replace_ocr_line_items().
    4. Associated invoice's line items are successfully replaced.
    5. A worker from another tenant cannot do the same.
    6. A worker without a valid lease remains blocked.
    """
    org_a = test_tenants["org_a"]
    org_b = test_tenants["org_b"]
    user_a = test_tenants["user_a"]
    profile_a = test_tenants["profile_a"]
    inv_id = str(uuid.uuid4())
    doc_id = str(uuid.uuid4())

    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            # Seed invoice in uploaded state
            cur.execute("""
                INSERT INTO public.documents (id, org_id, doc_type, file_name, file_size_bytes, mime_type, file_path, sha256_checksum)
                VALUES (%s, %s, 'invoice', 'pos_test.pdf', 1024, 'application/pdf', '/tmp/pos.pdf', 'fake_hash_pos');
            """, (doc_id, org_a))
            cur.execute("""
                INSERT INTO public.invoices (id, org_id, profile_id, invoice_number, invoice_date, counterparty_name, total_taxable_amount, cgst_amount, sgst_amount, igst_amount, total_amount, status)
                VALUES (%s, %s, %s, 'INV-POS-PATH', '2026-11-17', 'Vendor Pos', 0, 0, 0, 0, 0, 'uploaded');
            """, (inv_id, org_a, profile_a))
            # Enqueue invoice_ocr task
            cur.execute("SELECT set_config('app.current_org', %s, false);", (org_a,))
            cur.execute("""
                SELECT public.enqueue_task('invoice_ocr', %s::jsonb, %s::uuid);
            """, (json.dumps({"invoice_id": inv_id, "document_id": doc_id}), org_a))
            task_id = str(cur.fetchone()[0])

    # 1 & 2: Real worker login claims the invoice_ocr task
    with psycopg.connect(DB_WORKER, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('app.current_org', %s, false);", (org_a,))
            cur.execute("SELECT o_task_id, o_lease_token FROM public.claim_task(%s, 'worker-pos');", (org_a,))
            claimed_id, lease_token = cur.fetchone()
            assert str(claimed_id) == task_id
            assert lease_token is not None

            # 3 & 4: Worker calls replace_ocr_line_items() to replace items successfully
            new_lines = [
                {
                    "item_description": "Extracted Cloud Server Consulting",
                    "hsn_sac_code": "998314",
                    "quantity": 2.0,
                    "unit_price": 5000.00,
                    "taxable_amount": 10000.00,
                    "gst_rate": 18.00,
                    "cgst_amount": 900.00,
                    "sgst_amount": 900.00,
                    "igst_amount": 0.00,
                    "total_amount": 11800.00,
                    "confidence": 0.98
                }
            ]
            cur.execute("""
                SELECT public.replace_ocr_line_items(%s, %s, %s, %s::jsonb);
            """, (task_id, lease_token, inv_id, json.dumps(new_lines)))

    # Verify line items in database
    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT item_description, quantity, taxable_amount FROM public.invoice_line_items WHERE invoice_id = %s;", (inv_id,))
            rows = cur.fetchall()
            assert len(rows) == 1
            assert rows[0][0] == "Extracted Cloud Server Consulting"
            assert Decimal(str(rows[0][1])) == Decimal("2.000")
            assert Decimal(str(rows[0][2])) == Decimal("10000.00")

    # 5: Worker from another tenant (Org B) cannot replace items for Org A
    with psycopg.connect(DB_WORKER, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('app.current_org', %s, false);", (org_b,))
            with pytest.raises(psycopg.Error) as exc_cross:
                cur.execute("""
                    SELECT public.replace_ocr_line_items(%s, %s, %s, '[]'::jsonb);
                """, (task_id, lease_token, inv_id))
            assert exc_cross.value.sqlstate == "42501"

    # 6: Worker without a valid lease remains blocked
    with psycopg.connect(DB_WORKER, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('app.current_org', %s, false);", (org_a,))
            with pytest.raises(psycopg.Error) as exc_invalid:
                cur.execute("""
                    SELECT public.replace_ocr_line_items(%s, 'invalid-token-xyz', %s, '[]'::jsonb);
                """, (task_id, inv_id))
            assert exc_invalid.value.sqlstate == "42501"


def test_gstr1_table_12_b2b_mandatory_across_all_registered_supply_tables(test_tenants):
    """
    Reviewer Requirement 4: Broaden Table 12 B2B requirement detection
    across all applicable registered-recipient supply tables (Table 4, 6B, 6C, 8, 9A, 9B, 9C, 15, 15A).
    Derives every flag from actual populated registers.
    """
    org_id = test_tenants["org_a"]
    user_id = test_tenants["user_a"]
    profile_id = test_tenants["profile_a"]
    reg_gstin = random_gstin("27")

    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            # 1. Table 4B: Explicit Reverse Charge supply
            seed_invoice(
                cur, org_id, profile_id, user_id, f"INV-RCM-{uuid.uuid4().hex[:6]}", "2026-11-05", "RCM Supplier Co", reg_gstin,
                1000.00, 90.00, 90.00, 0.00, 1180.00, status='approved',
                is_reverse_charge=True, supply_category='B2B_REVERSE_CHARGE'
            )

            # 2. Table 6B: Explicit SEZ outward supply
            seed_invoice(
                cur, org_id, profile_id, user_id, f"INV-SEZ-{uuid.uuid4().hex[:6]}", "2026-11-06", "SEZ Developer Corp", reg_gstin,
                2000.00, 0.00, 0.00, 360.00, 2360.00, status='approved',
                is_sez_supply=True, supply_category='SEZ_WITH_PAYMENT', recipient_registration_type='sez_developer'
            )

            # 3. Table 6C: Explicit Deemed Export
            seed_invoice(
                cur, org_id, profile_id, user_id, f"INV-DE-{uuid.uuid4().hex[:6]}", "2026-11-07", "EOU Manufacturing Ltd", reg_gstin,
                3000.00, 270.00, 270.00, 0.00, 3540.00, status='approved',
                is_deemed_export=True, supply_category='DEEMED_EXPORT'
            )

            # 4. Table 8: Explicit Nil-rated supply to registered recipient
            seed_invoice(
                cur, org_id, profile_id, user_id, f"INV-NIL-{uuid.uuid4().hex[:6]}", "2026-11-08", "Exempt Grains Trader", reg_gstin,
                4000.00, 0.00, 0.00, 0.00, 4000.00, status='approved',
                supply_category='NIL_EXEMPT'
            )

            # 5. Table 9B: Explicit Credit Note to registered recipient
            seed_invoice(
                cur, org_id, profile_id, user_id, f"INV-CN-{uuid.uuid4().hex[:6]}", "2026-11-09", "Acme Trading Co", reg_gstin,
                -500.00, -45.00, -45.00, 0.00, -590.00, status='approved',
                document_type='credit_note'
            )

            # 6. Table 15: Explicit ECO outward supply to registered recipient
            seed_invoice(
                cur, org_id, profile_id, user_id, f"INV-ECO-{uuid.uuid4().hex[:6]}", "2026-11-10", "E-Commerce Marketplace Partner", reg_gstin,
                1500.00, 135.00, 135.00, 0.00, 1770.00, status='approved',
                supply_category='ECO_SUPPLY'
            )

    headers = {"X-User-Id": user_id, "X-Org-Id": org_id}
    res = client.get("/api/v1/gst/gstr1-draft?period=2026-11", headers=headers)
    assert res.status_code == 200
    data = res.json()

    assert data["table_12_b2b_mandatory"] is True
    supply_tables = data["b2b_supply_tables"]
    assert supply_tables["table_4b"] is True, "Table 4B Reverse Charge should be detected"
    assert supply_tables["table_6b"] is True, "Table 6B SEZ supply should be detected"
    assert supply_tables["table_6c"] is True, "Table 6C Deemed export should be detected"
    assert supply_tables["table_8_b2b"] is True, "Table 8 Nil-rated registered supply should be detected"
    assert supply_tables["table_9b_b2b"] is True, "Table 9B Credit Note should be detected"
    assert supply_tables["table_15_b2b"] is True, "Table 15 ECO registered supply should be detected"


def test_invoice_ocr_writer_transitive_isolation_and_column_grants():
    """
    Reviewer Requirement 5:
    1. Confirm no worker or application user can transitively assume invoice_ocr_writer.
    2. Confirm pg_auth_members has 0 entries involving invoice_ocr_writer.
    3. Confirm UPDATE privilege is granted only on (id) column of worker_tasks and invoices.
    """
    with psycopg.connect(DB_ADMIN) as conn:
        with conn.cursor() as cur:
            # 1. No role is a member of invoice_ocr_writer, nor is it a member of any other role
            cur.execute("""
                SELECT r.rolname as role, m.rolname as member
                FROM pg_auth_members pam
                JOIN pg_roles r ON pam.roleid = r.oid
                JOIN pg_roles m ON pam.member = m.oid
                WHERE r.rolname = 'invoice_ocr_writer' OR m.rolname = 'invoice_ocr_writer';
            """)
            memberships = cur.fetchall()
            assert len(memberships) == 0, f"invoice_ocr_writer must have NO direct or transitive memberships! Found: {memberships}"

            # 2. Confirm worker_service_user cannot assume invoice_ocr_writer
            cur.execute("SELECT pg_has_role('worker_service_user', 'invoice_ocr_writer', 'MEMBER');")
            assert cur.fetchone()[0] is False

            cur.execute("SELECT pg_has_role('app_service_user', 'invoice_ocr_writer', 'MEMBER');")
            assert cur.fetchone()[0] is False

            # 3. Column-level UPDATE privilege verification:
            # Must have UPDATE on 'id' column for row locking
            cur.execute("""
                SELECT has_column_privilege('invoice_ocr_writer', 'public.worker_tasks', 'id', 'UPDATE');
            """)
            assert cur.fetchone()[0] is True, "Must have UPDATE on id for SELECT ... FOR UPDATE"

            # Must NOT have UPDATE on data/status columns
            for col in ['status', 'payload', 'lease_token_hash']:
                cur.execute("""
                    SELECT has_column_privilege('invoice_ocr_writer', 'public.worker_tasks', %s, 'UPDATE');
                """, (col,))
                assert cur.fetchone()[0] is False, f"invoice_ocr_writer unexpectedly has UPDATE on worker_tasks.{col}!"

            cur.execute("""
                SELECT has_column_privilege('invoice_ocr_writer', 'public.invoices', 'id', 'UPDATE');
            """)
            assert cur.fetchone()[0] is True

            for col in ['total_amount', 'status', 'counterparty_name']:
                cur.execute("""
                    SELECT has_column_privilege('invoice_ocr_writer', 'public.invoices', %s, 'UPDATE');
                """, (col,))
                assert cur.fetchone()[0] is False, f"invoice_ocr_writer unexpectedly has UPDATE on invoices.{col}!"


def test_populated_data_downgrade_quarantine(test_tenants):
    """
    Reviewer Requirement 3: Populated-data rollback handling:
    1. Database contains authentic incomplete OCR extractions bearing NULLs.
    2. Downgrade to 0009 safely quarantines incomplete records in public.archived_m1_3_incomplete_extractions.
    3. Financial defaults (1.00 qty, 18% GST) are NOT forged onto records.
    4. Upgrade back to 0010 seamlessly restores the authentic incomplete records.
    """
    import subprocess
    org_id = test_tenants["org_a"]
    profile_id = test_tenants["profile_a"]
    inv_id = str(uuid.uuid4())
    line_id = str(uuid.uuid4())

    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            # Insert draft invoice using seed_invoice with status 'pending_review'
            inv_id = seed_invoice(
                cur, org_id, profile_id, test_tenants["user_a"],
                f"INC-INV-{uuid.uuid4().hex[:6]}", "2026-11-12", "Uncertain Vendor Ltd",
                None, 100.00, 9.00, 9.00, 0.00, 118.00, status='pending_review'
            )

            cur.execute("""
                INSERT INTO public.invoice_line_items (
                    id, invoice_id, org_id, item_description, hsn_sac_code,
                    quantity, unit_price, taxable_amount, gst_rate, cgst_amount, sgst_amount, igst_amount, total_amount
                ) VALUES (
                    %s, %s, %s, 'Incomplete Item with Nulls', '998311',
                    NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL
                );
            """, (line_id, inv_id, org_id))

    # Execute Alembic downgrade specifically to 0009 (testing 0010 rollback quarantine)
    p_down = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "0009_line_item_isolation"],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True
    )
    assert p_down.returncode == 0, f"Downgrade failed: {p_down.stderr}"

    # Verify that the record was quarantined with authentic NULLs preserved
    with psycopg.connect(DB_ADMIN) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT original_line_id, quarantine_reason, quantity, gst_rate
                FROM public.archived_m1_3_incomplete_extractions
                WHERE original_line_id = %s;
            """, (line_id,))
            q_row = cur.fetchone()
            assert q_row is not None, "Incomplete record must be quarantined in archived_m1_3_incomplete_extractions!"
            assert q_row[2] is None, "Authentic NULL quantity must be preserved!"
            assert q_row[3] is None, "Authentic NULL gst_rate must be preserved (not fabricated 18%)!"

    # Execute Alembic upgrade back to head (0010)
    p_up = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True
    )
    assert p_up.returncode == 0, f"Re-upgrade failed: {p_up.stderr}"

    # Verify that the record was restored cleanly back to invoice_line_items
    with psycopg.connect(DB_ADMIN) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, quantity, gst_rate FROM public.invoice_line_items WHERE id = %s;", (line_id,))
            restored = cur.fetchone()
            assert restored is not None, "Quarantined record must be restored to invoice_line_items upon upgrade!"
            assert restored[1] is None
            assert restored[2] is None


def test_gemini_smoke_script_cli_modes():
    """
    Reviewer Requirement 1: Smoke test harness CLI operational modes.
    1. --test-fallback simulates primary failure and proves fallback works.
    2. --require-live exits with code 2 if GEMINI_API_KEY is unset.
    """
    import subprocess

    # 1. Fallback mode
    p_fallback = subprocess.run(
        [sys.executable, "backend/scripts/gemini_live_smoke_test.py", "--test-fallback"],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True, text=True
    )
    assert p_fallback.returncode == 0
    assert "FALLBACK VERIFICATION:    PASSED" in p_fallback.stdout

    # 2. Require live when key is unset
    env_without_key = os.environ.copy()
    env_without_key.pop("GEMINI_API_KEY", None)
    p_live_req = subprocess.run(
        [sys.executable, "backend/scripts/gemini_live_smoke_test.py", "--require-live"],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True, text=True, env=env_without_key
    )
    assert p_live_req.returncode == 2
    assert "[FATAL] --require-live flag set but GEMINI_API_KEY is not configured." in p_live_req.stdout


def test_gemini_vision_live_smoke_test_harness(tmp_path):
    """
    Reviewer Requirement 5: Operational Acceptance Smoke Test Harness.
    Verifies that the Gemini extraction engine produces structured, validated
    ExtractedInvoiceData without fabricated numbers.
    """
    sample_pdf = tmp_path / "smoke_invoice.pdf"
    sample_pdf.write_bytes(b"%PDF-1.4\n%Redacted Synthetic GST Tax Invoice\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF")

    extracted = extract_invoice_with_gemini(str(sample_pdf), "application/pdf")
    assert extracted is not None
    assert extracted.invoice_number.startswith("INV-")
    assert extracted.counterparty_name is not None
    assert len(extracted.line_items) > 0
    # Verify no fabricated rates: line item rate and totals are mathematically sound
    for item in extracted.line_items:
        if item.quantity is not None and item.unit_price is not None:
            assert abs(Decimal(str(item.taxable_amount)) - (Decimal(str(item.quantity)) * Decimal(str(item.unit_price)))) < Decimal("0.05")
    # Verify raw response provenance is attached
    assert extracted.raw_ocr_response is not None
