"""Cross-tenant S3, local storage, GSTR-2B CRN, matching, read-only GET, and AI grounding security tests."""

import io
import json
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import openpyxl
import pytest
from fastapi.testclient import TestClient

import app.modules.invoice_brain.storage as storage_mod
from app.config import settings
from app.database import SessionLocal
from app.main import app
from app.modules.ai_assistant.gemini_client import answer_ai_query
from app.modules.ai_assistant.schemas import ChatMessageIn, GroundingContextSummary
from app.modules.invoice_brain.ocr import extract_invoice_mock, extract_invoice_with_gemini
from app.modules.invoice_brain.storage import (
    StorageError,
    compute_stream_sha256_and_save,
    delete_stored_document,
    read_document_bytes,
    reconcile_storage_orphans,
    stat_document_size,
)
from app.modules.reconciliation.gstr2b_parser import parse_gstr2b_excel, parse_gstr2b_json
from app.modules.reconciliation.matching_engine import DeterministicReconciliationEngine


class _FakeS3Client:
    """In-memory fake S3 client mimicking get_object, head_object, put_object, delete_object, list_objects_v2."""

    def __init__(self):
        self.objects = {}

    def put_object(self, Bucket, Key, Body, **kwargs):
        data = Body.read() if hasattr(Body, "read") else bytes(Body)
        self.objects[(Bucket, Key)] = {
            "Body": data,
            "LastModified": datetime.now(timezone.utc) - timedelta(hours=2),
        }

    def get_object(self, Bucket, Key):
        item = self.objects.get((Bucket, Key))
        if item is None:
            raise KeyError(Key)
        return {"Body": io.BytesIO(item["Body"])}

    def head_object(self, Bucket, Key):
        item = self.objects.get((Bucket, Key))
        if item is None:
            raise KeyError(Key)
        return {"ContentLength": len(item["Body"])}

    def delete_object(self, Bucket, Key):
        self.objects.pop((Bucket, Key), None)

    def get_paginator(self, op_name):
        client = self

        class _Paginator:
            def paginate(self, Bucket, Prefix=""):
                contents = []
                for (b, k), meta in sorted(client.objects.items()):
                    if b == Bucket and k.startswith(Prefix):
                        contents.append({
                            "Key": k,
                            "Size": len(meta["Body"]),
                            "LastModified": meta["LastModified"],
                        })
                return [{"Contents": contents}]

        return _Paginator()


class _FakeSession:
    def __init__(self, current_org: str, active_paths=None):
        self.info = {"current_org": current_org}
        self._active_paths = list(active_paths or [])

    def execute(self, stmt, params=None):
        sql = str(stmt)

        class _Result:
            def __init__(self, scalar_val=None, rows=None):
                self._scalar = scalar_val
                self._rows = rows or []

            def scalar(self):
                return self._scalar

            def fetchall(self):
                return self._rows

        if "SELECT current_user" in sql:
            return _Result(scalar_val="app_service_user")
        if "current_setting('app.current_org'" in sql:
            return _Result(scalar_val=self.info["current_org"])
        if "FROM public.documents" in sql:
            return _Result(rows=[(p,) for p in self._active_paths])
        return _Result()


def test_storage_rejects_traversal_and_missing_or_cross_tenant_org_s3_and_local(monkeypatch, tmp_path):
    org_a = str(uuid.uuid4())
    org_b = str(uuid.uuid4())
    doc_a = str(uuid.uuid4())
    doc_b = str(uuid.uuid4())
    payload = b"%PDF-1.4\n%Tenant A Invoice\n%%EOF\n"

    # 1. Path/key traversal rejected on upload for both org_id and doc_id
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "local")
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setattr(settings, "STORAGE_LOCAL_ROOT", str(tmp_path))
    monkeypatch.setenv("STORAGE_LOCAL_ROOT", str(tmp_path))

    for bad_org in ("", "   ", "..", "../escape", "org/sub", "org\\sub"):
        with pytest.raises(StorageError, match="INVALID_STORAGE_SEGMENT"):
            compute_stream_sha256_and_save(io.BytesIO(payload), bad_org, doc_a, base_dir=str(tmp_path))

    for bad_doc in ("", "   ", "..", "../bad", "doc/nested"):
        with pytest.raises(StorageError, match="INVALID_STORAGE_SEGMENT"):
            compute_stream_sha256_and_save(io.BytesIO(payload), org_a, bad_doc, base_dir=str(tmp_path))

    # 2. Local storage cross-tenant and missing expected_org_id fail closed across all accessors
    local_path, _, _, mime = compute_stream_sha256_and_save(io.BytesIO(payload), org_a, doc_a, base_dir=str(tmp_path))
    for missing_org in (None, "", "   "):
        with pytest.raises(StorageError, match="TENANT_STORAGE_ISOLATION_VIOLATION"):
            read_document_bytes(local_path, expected_org_id=missing_org)
        with pytest.raises(StorageError, match="TENANT_STORAGE_ISOLATION_VIOLATION"):
            stat_document_size(local_path, expected_org_id=missing_org)
        with pytest.raises(StorageError, match="TENANT_STORAGE_ISOLATION_VIOLATION"):
            delete_stored_document(local_path, expected_org_id=missing_org)
        with pytest.raises(StorageError, match="TENANT_STORAGE_ISOLATION_VIOLATION"):
            extract_invoice_mock(local_path, mime, expected_org_id=missing_org)
        with pytest.raises(StorageError, match="TENANT_STORAGE_ISOLATION_VIOLATION"):
            extract_invoice_with_gemini(local_path, mime, expected_org_id=missing_org)

    for fn in (read_document_bytes, stat_document_size, delete_stored_document):
        with pytest.raises(StorageError, match="TENANT_STORAGE_ISOLATION_VIOLATION"):
            fn(local_path, expected_org_id=org_b)
    with pytest.raises(StorageError, match="TENANT_STORAGE_ISOLATION_VIOLATION"):
        extract_invoice_mock(local_path, mime, expected_org_id=org_b)
    with pytest.raises(StorageError, match="TENANT_STORAGE_ISOLATION_VIOLATION"):
        extract_invoice_with_gemini(local_path, mime, expected_org_id=org_b)
    assert os.path.exists(local_path), "Cross-tenant delete failure must preserve tenant A file"

    # 3. S3 backend cross-tenant isolation & orphan cleanup boundary
    fake_s3 = _FakeS3Client()
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "s3")
    monkeypatch.setenv("STORAGE_BACKEND", "s3")
    monkeypatch.setattr(settings, "AWS_S3_BUCKET_NAME", "myca-prod-vault")
    monkeypatch.setenv("AWS_S3_BUCKET_NAME", "myca-prod-vault")
    monkeypatch.setattr(storage_mod, "_get_s3_client", lambda: fake_s3)

    s3_uri_a, _, _, _ = compute_stream_sha256_and_save(io.BytesIO(payload), org_a, doc_a)
    s3_uri_b, _, _, _ = compute_stream_sha256_and_save(io.BytesIO(payload), org_b, doc_b)
    assert read_document_bytes(s3_uri_a, expected_org_id=org_a) == payload

    for fn in (read_document_bytes, stat_document_size, delete_stored_document):
        with pytest.raises(StorageError, match="TENANT_STORAGE_ISOLATION_VIOLATION"):
            fn(s3_uri_a, expected_org_id=org_b)
    with pytest.raises(StorageError, match="TENANT_STORAGE_ISOLATION_VIOLATION"):
        extract_invoice_mock(s3_uri_a, mime, expected_org_id=org_b)
    with pytest.raises(StorageError, match="TENANT_STORAGE_ISOLATION_VIOLATION"):
        extract_invoice_with_gemini(s3_uri_a, mime, expected_org_id=org_b)

    # Conflicting explicit org_id vs bound session tenant context must fail closed
    sess_a = _FakeSession(current_org=org_a, active_paths=[s3_uri_a])
    with pytest.raises(StorageError, match="TENANT_STORAGE_ISOLATION_VIOLATION"):
        reconcile_storage_orphans(sess_a, org_id=org_b, grace_period_seconds=1800)

    # Org A orphan cleanup must NEVER touch Org B objects
    _, key_b = storage_mod.parse_s3_uri(s3_uri_b)
    assert ("myca-prod-vault", key_b) in fake_s3.objects
    reconcile_storage_orphans(sess_a, org_id=org_a, grace_period_seconds=1800)
    assert ("myca-prod-vault", key_b) in fake_s3.objects


def test_gstr2b_credit_note_subtraction_and_matching_doc_type_guard():
    # JSON parser subtracts CRN from net available ITC
    payload = {
        "gstin": "27AABCB1234M1ZT",
        "fp": "112026",
        "fy": "2026-2027",
        "data": {
            "b2b": [
                {
                    "ctin": "27BBBBB2222B1Z8",
                    "trdnm": "Supp B",
                    "inv": [
                        {
                            "inum": "INV-1",
                            "idt": "2026-11-05",
                            "val": 11800.0,
                            "pos": "27",
                            "itcavl": "Y",
                            "items": [{"txval": 10000.0, "camt": 900.0, "samt": 900.0, "iamt": 0.0, "csamt": 0.0}],
                        }
                    ],
                }
            ],
            "cdnr": [
                {
                    "ctin": "27BBBBB2222B1Z8",
                    "trdnm": "Supp B",
                    "nt": [
                        {
                            "ntnum": "CRN-1",
                            "ntdt": "2026-11-10",
                            "val": 1180.0,
                            "ntty": "C",
                            "pos": "27",
                            "itcavl": "Y",
                            "items": [{"txval": 1000.0, "camt": 90.0, "samt": 90.0, "iamt": 0.0, "csamt": 0.0}],
                        }
                    ],
                }
            ],
        },
    }
    parsed_json = parse_gstr2b_json(json.dumps(payload).encode("utf-8"))
    assert parsed_json.total_itc_available == Decimal("1620.00")

    # Excel read_only parser parses both B2B and CDNR and subtracts CRN
    wb = openpyxl.Workbook()
    ws_b2b = wb.active
    ws_b2b.title = "B2B"
    ws_b2b.append(["GSTIN of Supplier", "Trade Name", "Invoice number", "Invoice date", "Invoice Value", "Place of supply", "Reverse Charge", "Invoice Type", "Taxable Value", "Integrated Tax", "Central Tax", "State/UT Tax", "Cess"])
    ws_b2b.append(["27BBBBB2222B1Z8", "Supp B", "INV-1", "2026-11-05", 11800, "27", "N", "Regular", 10000, 0, 900, 900, 0])
    ws_cdnr = wb.create_sheet("CDNR")
    ws_cdnr.append(["GSTIN of Supplier", "Trade Name", "Note number", "Note date", "Note Value", "Place of supply", "Reverse Charge", "Note Type", "Taxable Value", "Integrated Tax", "Central Tax", "State/UT Tax", "Cess"])
    ws_cdnr.append(["27BBBBB2222B1Z8", "Supp B", "CRN-1", "2026-11-10", 1180, "27", "N", "Credit", 1000, 0, 90, 90, 0])
    buf = io.BytesIO()
    wb.save(buf)
    parsed_xlsx = parse_gstr2b_excel(buf.getvalue())
    assert len(parsed_xlsx.documents) == 2
    assert parsed_xlsx.total_itc_available == Decimal("1620.00")

    # Matching engine must NOT match a credit_note to an invoice across Passes 1-4
    engine = DeterministicReconciliationEngine()
    p_docs = [{
        "id": "p-inv",
        "document_type": "tax_invoice",
        "document_number": "DOC-77",
        "document_date": "2026-11-05",
        "supplier_gstin": "27BBBBB2222B1Z8",
        "total_taxable_amount": Decimal("1000.00"),
        "cgst_amount": Decimal("90.00"),
        "sgst_amount": Decimal("90.00"),
        "igst_amount": Decimal("0.00"),
        "total_invoice_value": Decimal("1180.00"),
    }]
    g_docs = [{
        "id": "g-crn",
        "document_type": "CRN",
        "section_name": "CDNRA",
        "is_amended": True,
        "document_number": "DOC-77",
        "document_date": "2026-11-06",
        "supplier_gstin": "27BBBBB2222B1Z8",
        "taxable_value": Decimal("1000.00"),
        "central_tax": Decimal("90.00"),
        "state_ut_tax": Decimal("90.00"),
        "integrated_tax": Decimal("0.00"),
        "invoice_value": Decimal("1180.00"),
    }]
    matches, _ = engine.run_reconciliation(p_docs, g_docs)
    assert len(matches) == 0


def test_ai_ungrounded_flag_and_scheme_prompt(monkeypatch):
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    res_ungrounded = answer_ai_query([ChatMessageIn(role="user", content="Explain Rule 37")], None)
    assert res_ungrounded.grounding_applied is False

    ctx = GroundingContextSummary(org_legal_name="Demo Org", top_recommended_scheme=None)
    from app.modules.ai_assistant.gemini_client import build_system_prompt
    prompt = build_system_prompt(ctx)
    assert "Top Matched Scheme: None matched yet" in prompt
    assert "Top Matched Scheme: CGTMSE" not in prompt


def test_unbooked_candidates_get_is_purely_read_only():
    import psycopg
    from sqlalchemy import text as sa_text
    from tests.test_db_config import DB_ADMIN

    org_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    reg_id = str(uuid.uuid4())
    imp_id = str(uuid.uuid4())
    snap_id = str(uuid.uuid4())
    g_doc_id = str(uuid.uuid4())
    pan = f"AAARO{uuid.uuid4().int % 9000 + 1000}Z"
    gstin = f"27{pan}1ZT"
    email = f"viewer_ro_{uuid.uuid4().hex[:8]}@example.com"

    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO public.organizations (id, legal_name, pan) VALUES (%s, 'RO Get Org', %s);", (org_id, pan))
            cur.execute("INSERT INTO public.global_users (id, email, full_name, is_active) VALUES (%s, %s, 'RO Viewer', true);", (user_id, email))
            cur.execute("INSERT INTO public.organization_memberships (id, org_id, user_id, role, status) VALUES (%s, %s, %s, 'viewer', 'active');", (str(uuid.uuid4()), org_id, user_id))
            cur.execute("INSERT INTO public.gst_registrations (id, org_id, gstin, state_code, legal_name, is_primary) VALUES (%s, %s, %s, '27', 'RO Entity', true);", (reg_id, org_id, gstin))
            cur.execute(
                "INSERT INTO public.gstr2b_imports (id, org_id, registration_id, recipient_gstin, return_period, financial_year, file_name, file_type, file_size_bytes, file_hash) VALUES (%s, %s, %s, %s, '2026-11', '2026-27', 'g.json', 'json', 100, %s);",
                (imp_id, org_id, reg_id, gstin, uuid.uuid4().hex),
            )
            cur.execute(
                "INSERT INTO public.gstr2b_snapshots (id, org_id, import_id, registration_id, recipient_gstin, return_period, version_number, total_itc_available) VALUES (%s, %s, %s, %s, %s, '2026-11', 1, 1800.00);",
                (snap_id, org_id, imp_id, reg_id, gstin),
            )
            cur.execute(
                "INSERT INTO public.gstr2b_source_documents (id, org_id, snapshot_id, section_name, itc_availability, source_form, supplier_gstin, supplier_name, document_type, document_number, document_date, taxable_value, central_tax, state_ut_tax, place_of_supply) VALUES (%s, %s, %s, 'B2B', 'available', 'GSTR-1', '27BBBBB2222B1Z8', 'Vendor RO', 'INV', 'RO-INV-1', '2026-11-10', 10000.00, 900.00, 900.00, '27');",
                (g_doc_id, org_id, snap_id),
            )

    client = TestClient(app)
    headers = {"X-Org-Id": org_id, "X-User-Id": user_id}
    res = client.get(f"/api/v1/reconciliation/unbooked-candidates?snapshot_id={snap_id}", headers=headers)
    assert res.status_code == 200
    rows = res.json()
    assert len(rows) == 1
    assert rows[0]["gstr2b_doc_id"] == g_doc_id

    # GET must NOT have mutated gstr2b_unbooked_candidates
    with psycopg.connect(DB_ADMIN) as conn:
        cnt = conn.execute("SELECT count(*) FROM public.gstr2b_unbooked_candidates WHERE org_id = %s;", (org_id,)).fetchone()[0]
        assert cnt == 0

    # Cleanup
    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM public.gstr2b_source_documents WHERE org_id = %s;", (org_id,))
            cur.execute("DELETE FROM public.gstr2b_snapshots WHERE org_id = %s;", (org_id,))
            cur.execute("DELETE FROM public.gstr2b_imports WHERE org_id = %s;", (org_id,))
            cur.execute("DELETE FROM public.gst_registrations WHERE org_id = %s;", (org_id,))
            cur.execute("DELETE FROM public.organization_memberships WHERE org_id = %s;", (org_id,))
            cur.execute("DELETE FROM public.global_users WHERE id = %s;", (user_id,))
            cur.execute("DELETE FROM public.organizations WHERE id = %s;", (org_id,))


def test_audit2_storage_parser_table4_bucket_and_worker_orphan_guards(tmp_path):
    """Regression coverage for Audit 2 P2/P3 fixes: non-UUID path, worker orphan guard, parser period, table4_bucket."""
    import openpyxl
    from unittest.mock import MagicMock
    from app.modules.reconciliation.gstr3b_itc import generate_gstr3b_table4_summary

    # 1. Outside-root non-UUID path must fail unless org_id segment is in path
    outside_file = tmp_path / "flat_invoice.pdf"
    outside_file.write_bytes(b"%PDF-1.4\n%Flat")
    with pytest.raises(StorageError, match="TENANT_STORAGE_ISOLATION_VIOLATION"):
        read_document_bytes(str(outside_file), expected_org_id="org-alpha")

    # 2. worker_service_user orphan reconciliation without org_id scope must fail closed
    mock_sess = MagicMock()
    mock_sess.info = {}
    mock_sess.execute.return_value.scalar.side_effect = ["", "worker_service_user"]
    with pytest.raises(StorageError, match="TENANT_SCOPE_REQUIRED"):
        reconcile_storage_orphans(mock_sess, base_dir=str(tmp_path))

    # 3. GSTR-2B parser must not fabricate 2026-11 when fp/Period is omitted
    parsed_json = parse_gstr2b_json(json.dumps({"gstin": "27AABCB1234M1ZT", "b2b": []}).encode("utf-8"))
    assert parsed_json.return_period == ""
    assert parsed_json.financial_year == ""

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "B2B"
    ws.append(["GSTIN of supplier", "Name", "Invoice number", "Date", "Value", "PoS", "RCM", "Type", "Taxable", "IGST", "CGST", "SGST", "Cess"])
    buf = io.BytesIO()
    wb.save(buf)
    parsed_xlsx = parse_gstr2b_excel(buf.getvalue())
    assert parsed_xlsx.return_period == ""
    assert parsed_xlsx.financial_year == ""

    # 4. Table 4 reversals_lineage must include table4_bucket 4B1 / 4B2 for frontend drill-down
    p_175 = str(uuid.uuid4())
    p_r37 = str(uuid.uuid4())
    t4 = generate_gstr3b_table4_summary(
        return_period="2026-11",
        recipient_gstin="27AABCB1234M1ZT",
        gstr2b_docs=[],
        purchase_docs=[
            {"id": p_175, "document_number": "INV-175", "document_date": "2026-11-01", "supplier_gstin": "27AAACV1234M1ZV", "cgst_amount": 0, "sgst_amount": 0, "igst_amount": 900, "cess_amount": 0},
            {"id": p_r37, "document_number": "INV-R37", "document_date": "2026-11-02", "supplier_gstin": "27AAACV1234M1ZV", "cgst_amount": 0, "sgst_amount": 0, "igst_amount": 500, "cess_amount": 0},
        ],
        matches=[],
        assessments=[
            {"purchase_doc_id": p_175, "rule_code": "SEC_17_5_MOTOR_VEHICLE", "eligibility_status": "BLOCKED_CREDIT_INDICATOR", "risk_reversal_amount": Decimal("900.00")},
            {"purchase_doc_id": p_r37, "rule_code": "RULE_37_180_DAYS", "eligibility_status": "PAYMENT_180_DAY_RISK", "risk_reversal_amount": Decimal("500.00")},
        ],
    )
    buckets = {r["document_number"]: r.get("table4_bucket") for r in t4.reversals_lineage}
    assert buckets["INV-175"] == "4B1"
    assert buckets["INV-R37"] == "4B2"


def test_gstr3b_table31_credit_notes_categories_and_null_profile_isolation():
    """Verify GSTR-3B Table 3.1 subtracts credit notes, splits 3.1(b)/(c)/RCM, isolates multi-GSTIN NULL profiles, and records total_tax_paid."""
    import random
    import psycopg
    from fastapi.testclient import TestClient
    from app.core.security import create_access_token
    from app.main import app
    from tests.test_db_config import DB_ADMIN
    from tests.test_milestone1_security_closure import seed_invoice

    org_id = str(uuid.uuid4())
    maker_id = str(uuid.uuid4())
    checker_id = str(uuid.uuid4())
    reg_mh = str(uuid.uuid4())
    reg_ka = str(uuid.uuid4())
    null_prof_id = str(uuid.uuid4())
    pan = f"GSTRB{random.randint(1000, 9999)}K"
    gstin_mh = f"27{pan}1ZT"
    gstin_ka = f"29{pan}1ZT"

    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO public.organizations (id, legal_name, pan) VALUES (%s, 'GSTR3B Sign Co', %s);", (org_id, pan))
            for uid, role in ((maker_id, "owner"), (checker_id, "admin")):
                cur.execute("INSERT INTO public.global_users (id, email, full_name, is_active) VALUES (%s, %s, %s, true);", (uid, f"{role}_{uuid.uuid4().hex}@example.com", role.title()))
                cur.execute("INSERT INTO public.organization_memberships (org_id, user_id, role, status) VALUES (%s, %s, %s, 'active');", (org_id, uid, role))
            cur.execute(
                """
                INSERT INTO public.gst_registrations (id, org_id, gstin, state_code, legal_name, is_primary)
                VALUES (%s, %s, %s, '27', 'MH Unit', true), (%s, %s, %s, '29', 'KA Unit', false);
                """,
                (reg_mh, org_id, gstin_mh, reg_ka, org_id, gstin_ka),
            )
            cur.execute(
                """
                INSERT INTO public.business_profiles (id, org_id, gstin, business_type, industry_sector)
                VALUES (%s, %s, %s, 'Private Limited', 'Manufacturing'),
                       (%s, %s, %s, 'Private Limited', 'Manufacturing'),
                       (%s, %s, NULL, 'Private Limited', 'Services');
                """,
                (reg_mh, org_id, gstin_mh, reg_ka, org_id, gstin_ka, null_prof_id, org_id),
            )
            # MH Taxable B2B: +10,000 (IGST 1,800)
            seed_invoice(cur, org_id, reg_mh, maker_id, "INV-T31-1", "2026-11-05", "Buyer 1", None, Decimal("10000.00"), Decimal("0.00"), Decimal("0.00"), Decimal("1800.00"), Decimal("11800.00"), status="approved", supply_category="B2C_SMALL")
            # MH Credit Note: -2,000 (IGST -360)
            seed_invoice(cur, org_id, reg_mh, maker_id, "CN-T31-1", "2026-11-06", "Buyer 1", None, Decimal("2000.00"), Decimal("0.00"), Decimal("0.00"), Decimal("360.00"), Decimal("2360.00"), status="approved", supply_category="B2C_SMALL", document_type="credit_note")
            # MH SEZ with payment -> Table 3.1(b)
            seed_invoice(cur, org_id, reg_mh, maker_id, "SEZ-T31-1", "2026-11-07", "SEZ Buyer", None, Decimal("5000.00"), Decimal("0.00"), Decimal("0.00"), Decimal("900.00"), Decimal("5900.00"), status="approved", supply_category="SEZ_WITH_PAYMENT", is_sez_supply=True)
            # MH NIL/Exempt -> Table 3.1(c)
            seed_invoice(cur, org_id, reg_mh, maker_id, "NIL-T31-1", "2026-11-08", "Exempt Buyer", None, Decimal("3000.00"), Decimal("0.00"), Decimal("0.00"), Decimal("0.00"), Decimal("3000.00"), status="approved", supply_category="NIL_EXEMPT")
            # MH Outward RCM -> excluded from 3.1(a)
            seed_invoice(cur, org_id, reg_mh, maker_id, "RCM-OUT-1", "2026-11-09", "RCM Corp", gstin_ka, Decimal("4000.00"), Decimal("0.00"), Decimal("0.00"), Decimal("720.00"), Decimal("4720.00"), status="approved", supply_category="B2B_REVERSE_CHARGE", is_reverse_charge=True)
            # Unlinked NULL-GSTIN profile invoice: must NOT leak into KA (or MH) in multi-GSTIN org
            seed_invoice(cur, org_id, null_prof_id, maker_id, "NULL-PROF-1", "2026-11-10", "Amb Customer", None, Decimal("9000.00"), Decimal("0.00"), Decimal("0.00"), Decimal("1620.00"), Decimal("10620.00"), status="approved", supply_category="B2C_SMALL")

    client = TestClient(app)
    maker_hdr = {"Authorization": f"Bearer {create_access_token(subject=maker_id, org_id=org_id)}"}
    checker_hdr = {"Authorization": f"Bearer {create_access_token(subject=checker_id, org_id=org_id)}"}

    ka_res = client.post("/api/v1/gstr3b/prepare", json={"registration_id": reg_ka, "tax_period": "2026-11"}, headers=maker_hdr)
    assert ka_res.status_code == 200, ka_res.text
    assert Decimal(str(ka_res.json()["table_3_1"]["a_taxable_supplies"]["taxable_value"])) == Decimal("0.00")

    mh_res = client.post("/api/v1/gstr3b/prepare", json={"registration_id": reg_mh, "tax_period": "2026-11"}, headers=maker_hdr)
    assert mh_res.status_code == 200, mh_res.text
    body = mh_res.json()
    t31 = body["table_3_1"]
    assert Decimal(str(t31["a_taxable_supplies"]["taxable_value"])) == Decimal("8000.00")
    assert Decimal(str(t31["a_taxable_supplies"]["igst"])) == Decimal("1440.00")
    assert Decimal(str(t31["b_zero_rated"]["taxable_value"])) == Decimal("5000.00")
    assert Decimal(str(t31["b_zero_rated"]["igst"])) == Decimal("900.00")
    assert Decimal(str(t31["c_other_nil_exempt"])) == Decimal("3000.00")

    filing_id = body["id"]
    assert client.post(f"/api/v1/gstr3b/{filing_id}/submit-review", json={"remarks": "Ready"}, headers=maker_hdr).status_code == 200
    assert client.post(f"/api/v1/gstr3b/{filing_id}/review-decision", json={"decision": "APPROVED", "remarks": "Approved"}, headers=checker_hdr).status_code == 200
    assert client.post(f"/api/v1/gstr3b/{filing_id}/file", headers=checker_hdr).status_code == 200

    with psycopg.connect(DB_ADMIN) as conn:
        paid = conn.execute(
            "SELECT total_tax_paid FROM public.gst_records WHERE gstr3b_filing_id = %s;",
            (filing_id,),
        ).fetchone()[0]
    assert Decimal(str(paid)) == Decimal("2340.00")

