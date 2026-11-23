import os
import sys
import io
import uuid
import random
import pytest
from pathlib import Path

# Ensure backend root is on sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

import psycopg
from psycopg.rows import dict_row
from decimal import Decimal
from fastapi.testclient import TestClient

from app.main import app
from app.config import settings
from app.modules.invoice_brain.validator import (
    validate_gstin_format_and_checksum,
    calculate_gstin_checksum,
    validate_hsn_sac,
    validate_invoice_financials
)
from app.schemas.invoice_brain import GSTR_WORKING_DRAFT_DISCLAIMER, GSTR3B_WORKING_SUMMARY_DISCLAIMER
from app.modules.invoice_brain.service import process_invoice_ocr_task

# Database connection strings for test assertions
from tests.test_db_config import DB_ADMIN, DB_APP, DB_WORKER

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

def random_email():
    return f"user_{uuid.uuid4().hex[:8]}@test.com"

# Sample minimal valid PDF (with %PDF- header)
MINIMAL_VALID_PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Count 1/Kids[3 0 R]>>endobj\n"
    b"3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R>>endobj\n"
    b"xref\n0 4\n0000000000 65535 f\n0000000010 00000 n\n0000000053 00000 n\n0000000102 00000 n\n"
    b"trailer<</Size 4/Root 1 0 R>>\nstartxref\n178\n%%EOF\n"
)

@pytest.fixture
def seeded_org():
    """
    Sets up an isolated tenant organization, business profile, and active human owner.
    """
    org_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    profile_id = str(uuid.uuid4())
    supplier_gstin = random_gstin()

    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO public.organizations (id, legal_name, pan) VALUES (%s, 'Invoice Brain Test Org', %s);", (org_id, random_pan()))
            cur.execute("INSERT INTO public.global_users (id, email, full_name) VALUES (%s, %s, 'Invoice Approver');", (user_id, random_email()))
            cur.execute("INSERT INTO public.organization_memberships (org_id, user_id, role, status) VALUES (%s, %s, 'owner', 'active');", (org_id, user_id))
            cur.execute("INSERT INTO public.business_profiles (id, org_id, gstin, business_type, industry_sector) VALUES (%s, %s, %s, 'Services', 'IT Consulting');", (profile_id, org_id, supplier_gstin))
            cur.execute("INSERT INTO public.worker_service_authorizations (worker_role_name, org_id, is_active) VALUES ('worker_service_user', %s, true);", (org_id,))

    return {
        "org_id": org_id,
        "user_id": user_id,
        "profile_id": profile_id,
        "supplier_gstin": supplier_gstin
    }

# ==============================================================================
# STAGE 1 TESTS: Document Upload & Storage Isolation
# ==============================================================================

def test_stage1_document_upload_and_deduplication(seeded_org):
    """
    Stage 1: Verify document upload creates storage file, documents entry,
    initial invoice in uploaded state, and worker_tasks entry in queued state.
    Second upload of identical file must return 409 Conflict.
    """
    headers = {
        "X-User-Id": seeded_org["user_id"],
        "X-Org-Id": seeded_org["org_id"]
    }

    # Upload valid PDF
    files = {"file": ("test_invoice.pdf", io.BytesIO(MINIMAL_VALID_PDF), "application/pdf")}
    data = {"profile_id": seeded_org["profile_id"]}

    response = client.post("/api/v1/invoices/upload", headers=headers, files=files, data=data)
    assert response.status_code == 200, response.text
    res_data = response.json()

    assert res_data["status"] == "uploaded"
    assert res_data["invoice_id"] is not None
    assert res_data["document_id"] is not None
    assert res_data["task_id"] is not None

    inv_id = res_data["invoice_id"]
    task_id = res_data["task_id"]

    # Verify database state directly
    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            # Check document record
            cur.execute("SELECT * FROM public.documents WHERE id = %s;", (res_data["document_id"],))
            doc = cur.fetchone()
            assert doc is not None
            assert doc["mime_type"] == "application/pdf"
            assert doc["sha256_checksum"] == res_data["sha256_checksum"]
            assert os.path.exists(doc["file_path"])

            # Check invoice record starts in uploaded
            cur.execute("SELECT * FROM public.invoices WHERE id = %s;", (inv_id,))
            inv = cur.fetchone()
            assert inv is not None
            assert inv["status"] == "uploaded"

            # Check worker task record starts in queued
            cur.execute("SELECT * FROM public.worker_tasks WHERE id = %s;", (task_id,))
            task = cur.fetchone()
            assert task is not None
            assert task["status"] == "queued"
            assert task["task_type"] == "invoice_ocr"

    # Deduplication test: upload identical file again to the same org
    files_dup = {"file": ("test_invoice_copy.pdf", io.BytesIO(MINIMAL_VALID_PDF), "application/pdf")}
    dup_res = client.post("/api/v1/invoices/upload", headers=headers, files=files_dup, data=data)
    assert dup_res.status_code == 409
    assert "Duplicate document detected" in dup_res.json()["detail"]


def test_stage1_unauthorized_tenant_upload_rejection(seeded_org):
    """
    Stage 1: Attempting to upload to a foreign organization where the user
    does not have active membership must be rejected with 403 Forbidden.
    """
    foreign_org_id = str(uuid.uuid4())
    headers = {
        "X-User-Id": seeded_org["user_id"],
        "X-Org-Id": foreign_org_id
    }
    files = {"file": ("test.pdf", io.BytesIO(MINIMAL_VALID_PDF), "application/pdf")}
    data = {"profile_id": seeded_org["profile_id"]}

    response = client.post("/api/v1/invoices/upload", headers=headers, files=files, data=data)
    assert response.status_code == 403
    assert "does not possess active membership" in response.json()["detail"]


# ==============================================================================
# STAGE 2 & 3 TESTS: OCR Extraction, Structured State & GST Validation
# ==============================================================================

def test_stage2_and_3_ocr_extraction_and_validation(seeded_org):
    """
    Stage 2 & 3: Worker claims task, runs OCR extraction, updates invoice through
    uploaded -> queued -> processing -> extracted -> pending_review.
    Crucial verification: OCR output NEVER silently becomes approved!
    """
    headers = {
        "X-User-Id": seeded_org["user_id"],
        "X-Org-Id": seeded_org["org_id"]
    }

    # Upload document
    files = {"file": ("valid_invoice.pdf", io.BytesIO(MINIMAL_VALID_PDF), "application/pdf")}
    data = {"profile_id": seeded_org["profile_id"]}
    res = client.post("/api/v1/invoices/upload", headers=headers, files=files, data=data)
    inv_id = res.json()["invoice_id"]
    task_id = res.json()["task_id"]

    # Worker claims task using hardened claim_task() function
    with psycopg.connect(DB_WORKER, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT o_task_id, o_lease_token FROM public.claim_task(%s, 'worker-test-instance');", (seeded_org["org_id"],))
            claimed_id, lease_token = cur.fetchone()
            assert str(claimed_id) == task_id
            assert lease_token is not None

        # Process the claimed task
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT set_config('app.current_org', %s, false);", (seeded_org["org_id"],))
            cur.execute("SELECT payload FROM public.worker_tasks WHERE id = %s;", (task_id,))
            payload = cur.fetchone()["payload"]

        # Run OCR processing
        ocr_result = process_invoice_ocr_task(
            db_conn=conn,
            task_id=task_id,
            invoice_id=inv_id,
            document_id=payload["document_id"],
            org_id=seeded_org["org_id"],
            file_path=payload["file_path"],
            mime_type=payload["mime_type"],
            lease_token=lease_token
        )

        # Worker completes task
        with conn.cursor() as cur:
            cur.execute(
                "SELECT public.complete_task(%s, %s, %s::jsonb);",
                (task_id, lease_token, psycopg.types.json.Jsonb(ocr_result))
            )

    # Verify invoice status in database
    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT * FROM public.invoices WHERE id = %s;", (inv_id,))
            inv = cur.fetchone()

            # CRITICAL CHECK: OCR must advance to pending_review, NEVER approved!
            assert inv["status"] == "pending_review", f"OCR illegally marked invoice as {inv['status']} without human approval!"
            assert inv["approved_by"] is None
            assert inv["approved_at"] is None
            assert inv["total_amount"] > Decimal("0.00")

            # Verify line items extracted
            cur.execute("SELECT * FROM public.invoice_line_items WHERE invoice_id = %s;", (inv_id,))
            lines = cur.fetchall()
            assert len(lines) >= 1
            for l in lines:
                assert l["taxable_amount"] > Decimal("0.00")
                assert l["total_amount"] > Decimal("0.00")


# ==============================================================================
# STAGE 3 UNIT TESTS: GSTIN & Deterministic Math Validation Rules
# ==============================================================================

def test_stage3_gstin_checksum_validation():
    """
    Test GSTIN format and Modulo-36 checksum algorithm on real valid and corrupt GSTINs.
    """
    # Valid Indian GSTINs
    gstin_1 = "27AABCB1234M1ZT"
    assert validate_gstin_format_and_checksum(gstin_1)[0] is True

    base_2 = "29ABCDE1234F1Z"
    gstin_2 = f"{base_2}{calculate_gstin_checksum(base_2)}"
    assert validate_gstin_format_and_checksum(gstin_2)[0] is True

    # Invalid length
    assert validate_gstin_format_and_checksum("27AABCB1234M1Z")[0] is False
    # Invalid state code (e.g. 00)
    assert validate_gstin_format_and_checksum("00AABCB1234M1ZT")[0] is False
    # Corrupt checksum character (changed 'T' to '9')
    assert validate_gstin_format_and_checksum("27AABCB1234M1Z9")[0] is False


def test_stage3_hsn_sac_validation():
    """
    Test 4, 6, 8 digit HSN goods codes and 6-digit SAC service codes.
    """
    assert validate_hsn_sac("998311")[0] is True  # 6-digit SAC
    assert validate_hsn_sac("8471")[0] is True    # 4-digit HSN
    assert validate_hsn_sac("84713010")[0] is True # 8-digit HSN
    assert validate_hsn_sac("9983")[0] is False   # SAC must be 6 digits
    assert validate_hsn_sac("123")[0] is False    # Invalid length
    assert validate_hsn_sac("ABCD")[0] is False   # Non-numeric


# ==============================================================================
# STAGE 4 TESTS: Human Review, Field Correction, and Immutability Enforcement
# ==============================================================================

def test_stage4_human_review_and_field_correction(seeded_org):
    """
    Stage 4: Review extracted fields, correct a field before approval,
    then execute authenticated approval. Verify approved invoice is immutable.
    """
    headers = {
        "X-User-Id": seeded_org["user_id"],
        "X-Org-Id": seeded_org["org_id"]
    }

    # Upload and run OCR
    files = {"file": ("review_test.pdf", io.BytesIO(MINIMAL_VALID_PDF), "application/pdf")}
    data = {"profile_id": seeded_org["profile_id"]}
    res = client.post("/api/v1/invoices/upload", headers=headers, files=files, data=data)
    inv_id = res.json()["invoice_id"]
    task_id = res.json()["task_id"]

    # Claim and process OCR to get into pending_review
    with psycopg.connect(DB_WORKER, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT o_task_id, o_lease_token FROM public.claim_task(%s, 'worker-review');", (seeded_org["org_id"],))
            c_id, token = cur.fetchone()
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT set_config('app.current_org', %s, false);", (seeded_org["org_id"],))
            cur.execute("SELECT payload FROM public.worker_tasks WHERE id = %s;", (task_id,))
            payload = cur.fetchone()["payload"]
        ocr_result = process_invoice_ocr_task(
            db_conn=conn, task_id=task_id, invoice_id=inv_id,
            document_id=payload["document_id"], org_id=seeded_org["org_id"],
            file_path=payload["file_path"], mime_type=payload["mime_type"],
            lease_token=token
        )
        with conn.cursor() as cur:
            cur.execute("SELECT public.complete_task(%s, %s, %s::jsonb);", (task_id, token, psycopg.types.json.Jsonb(ocr_result)))

    # 1. Fetch side-by-side review details
    review_res = client.get(f"/api/v1/invoices/{inv_id}", headers=headers)
    assert review_res.status_code == 200
    inv_detail = review_res.json()
    assert inv_detail["status"] == "pending_review"
    assert inv_detail["validation_report"]["is_valid"] is True
    assert len(inv_detail["line_items"]) > 0

    # 2. Human reviewer field correction (Allowed while in pending_review)
    patch_data = {"counterparty_name": "Corrected Vendor Enterprises"}
    patch_res = client.patch(f"/api/v1/invoices/{inv_id}", headers=headers, json=patch_data)
    assert patch_res.status_code == 200
    assert "counterparty_name" in patch_res.json()["modified_fields"]

    # 3. Authenticated Human Approval
    app_res = client.post(f"/api/v1/invoices/{inv_id}/approve", headers=headers, json={"notes": "Verified against PO"})
    assert app_res.status_code == 200
    assert app_res.json()["status"] == "approved"

    # 4. IMMUTABILITY ENFORCEMENT: Attempting to edit invoice after approval MUST FAIL
    post_app_patch = client.patch(f"/api/v1/invoices/{inv_id}", headers=headers, json={"total_amount": 99999.00})
    assert post_app_patch.status_code == 403
    assert "Approved invoices are immutable" in post_app_patch.json()["detail"]

    # Database-level trigger test: Direct SQL attempt to modify financial figures on approved invoice
    with psycopg.connect(DB_APP, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('app.current_org', %s, false);", (seeded_org["org_id"],))
            cur.execute("SELECT set_config('app.current_user', %s, false);", (seeded_org["user_id"],))
            with pytest.raises(psycopg.Error) as exc_info:
                cur.execute("UPDATE public.invoices SET total_amount = 50000.00 WHERE id = %s;", (inv_id,))
            assert "Cannot modify financial figures" in str(exc_info.value) or "42501" in str(exc_info.value)


# ==============================================================================
# STAGE 5 TESTS: GSTR-1 and GSTR-3B Working Outputs
# ==============================================================================

def test_stage5_gstr_working_outputs_generation(seeded_org):
    """
    Stage 5: Generate GSTR-1 and GSTR-3B working drafts from approved invoices.
    Verify Tables 4, 12, 13 and prominent legal working draft disclaimer.
    """
    headers = {
        "X-User-Id": seeded_org["user_id"],
        "X-Org-Id": seeded_org["org_id"]
    }

    # Upload, process OCR, and approve an invoice for November 2026
    files = {"file": ("gst_invoice.pdf", io.BytesIO(MINIMAL_VALID_PDF), "application/pdf")}
    data = {"profile_id": seeded_org["profile_id"]}
    res = client.post("/api/v1/invoices/upload", headers=headers, files=files, data=data)
    inv_id = res.json()["invoice_id"]
    task_id = res.json()["task_id"]

    # Worker claims and processes
    with psycopg.connect(DB_WORKER, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT o_task_id, o_lease_token FROM public.claim_task(%s, 'worker-gstr');", (seeded_org["org_id"],))
            c_id, token = cur.fetchone()
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT set_config('app.current_org', %s, false);", (seeded_org["org_id"],))
            cur.execute("SELECT payload FROM public.worker_tasks WHERE id = %s;", (task_id,))
            payload = cur.fetchone()["payload"]
        ocr_result = process_invoice_ocr_task(
            db_conn=conn, task_id=task_id, invoice_id=inv_id,
            document_id=payload["document_id"], org_id=seeded_org["org_id"],
            file_path=payload["file_path"], mime_type=payload["mime_type"],
            lease_token=token
        )
        with conn.cursor() as cur:
            cur.execute("SELECT public.complete_task(%s, %s, %s::jsonb);", (task_id, token, psycopg.types.json.Jsonb(ocr_result)))

    # Approve invoice
    client.post(f"/api/v1/invoices/{inv_id}/approve", headers=headers)

    # 1. Fetch GSTR-1 Working Draft
    gstr1_res = client.get("/api/v1/gst/gstr1-draft?period=2026-11", headers=headers)
    assert gstr1_res.status_code == 200
    gstr1 = gstr1_res.json()

    # Verify Prominent Working Draft Legal Disclaimer
    assert gstr1["disclaimer"] == GSTR_WORKING_DRAFT_DISCLAIMER
    assert "NOT AN OFFICIAL FILING" in gstr1["disclaimer"]

    # Verify Table 4 B2B supplies contains our approved invoice
    assert len(gstr1["table_4_b2b"]) >= 1
    assert float(gstr1["total_taxable_value"]) > 0
    assert float(gstr1["total_tax_liability"]) > 0
    assert len(gstr1["table_12_hsn_summary"]) >= 1
    assert gstr1["table_13_doc_summary"]["net_issued"] >= 1

    # 2. Fetch GSTR-3B Working Draft
    gstr3b_res = client.get("/api/v1/gst/gstr3b-draft?period=2026-11", headers=headers)
    assert gstr3b_res.status_code == 200
    gstr3b = gstr3b_res.json()

    # Verify Outward-Supply-Derived Disclaimer and Field Provenance
    assert gstr3b["disclaimer"] == GSTR3B_WORKING_SUMMARY_DISCLAIMER
    assert "OUTWARD-SUPPLY-DERIVED WORKING SUMMARY" in gstr3b["disclaimer"]
    assert gstr3b["authoritative_itc_calculated"] is False
    assert gstr3b["authoritative_net_cash_calculated"] is False
    assert "table_3_1_a_outward_taxable" in gstr3b["field_provenance"]
    assert "table_4_a_itc_available" in gstr3b["field_provenance"]

    # Verify Table 3.1 Outward supplies aggregated
    t31 = gstr3b["table_3_1_outward_supplies"]["3_1_a_outward_taxable_supplies"]
    assert float(t31["total_taxable_value"]) > 0
    assert (float(t31["central_tax"]) + float(t31["integrated_tax"])) > 0
