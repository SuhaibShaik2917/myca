import os
import sys
import io
import json
import uuid
import random
from pathlib import Path

# Ensure backend root and vendor are on sys.path
backend_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(backend_root))
vendor_dir = backend_root / "vendor"
if vendor_dir.exists():
    sys.path.insert(0, str(vendor_dir))
sys.path.insert(0, "/home/shaby/vendor")

import openpyxl
from datetime import date, datetime, timedelta
from decimal import Decimal
import pytest
from fastapi.testclient import TestClient

import psycopg
from psycopg.rows import dict_row

from app.main import app
from app.config import settings
from app.modules.invoice_brain.validator import calculate_gstin_checksum
from app.modules.reconciliation.schemas import (
    IMSActionEnum, MatchCategoryEnum, ITCEligibilityEnum,
    GSTR2BParsedStatement, PurchaseDocumentCreate, PurchaseLineItemIn
)
from app.modules.reconciliation.gstr2b_parser import (
    parse_gstr2b_json, parse_gstr2b_excel, normalize_doc_number
)
from app.modules.reconciliation.purchase_service import (
    create_purchase_document, approve_purchase_document, record_purchase_payment,
    DuplicatePurchaseDocumentError, PurchaseServiceError
)
from app.modules.reconciliation.matching_engine import DeterministicReconciliationEngine
from app.modules.reconciliation.itc_engine import StatutoryITCRiskEngine
from app.modules.reconciliation.gstr3b_itc import generate_gstr3b_table4_summary

from tests.test_db_config import DB_ADMIN, DB_APP

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
    """Seeds two distinct organizations Org A and Org B with GST registrations and users."""
    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            # Tenant A
            org_a = str(uuid.uuid4())
            user_a = str(uuid.uuid4())
            gstin_a = random_gstin("27")
            pan_a = gstin_a[2:12]
            reg_a = str(uuid.uuid4())

            cur.execute("INSERT INTO public.organizations (id, legal_name, pan) VALUES (%s, 'M2 Tenant A Org', %s);", (org_a, pan_a))
            cur.execute("INSERT INTO public.global_users (id, email, full_name) VALUES (%s, %s, 'Owner M2 A');", (user_a, f"user_m2a_{uuid.uuid4().hex}@example.com"))
            cur.execute("INSERT INTO public.organization_memberships (org_id, user_id, role, status) VALUES (%s, %s, 'owner', 'active');", (org_a, user_a))
            cur.execute("""
                INSERT INTO public.gst_registrations (id, org_id, gstin, state_code, legal_name, is_primary)
                VALUES (%s, %s, %s, '27', 'M2 Tenant A Legal', true);
            """, (reg_a, org_a, gstin_a))

            # Tenant B
            org_b = str(uuid.uuid4())
            user_b = str(uuid.uuid4())
            gstin_b = random_gstin("29")
            pan_b = gstin_b[2:12]
            reg_b = str(uuid.uuid4())

            cur.execute("INSERT INTO public.organizations (id, legal_name, pan) VALUES (%s, 'M2 Tenant B Org', %s);", (org_b, pan_b))
            cur.execute("INSERT INTO public.global_users (id, email, full_name) VALUES (%s, %s, 'Owner M2 B');", (user_b, f"user_m2b_{uuid.uuid4().hex}@example.com"))
            cur.execute("INSERT INTO public.organization_memberships (org_id, user_id, role, status) VALUES (%s, %s, 'owner', 'active');", (org_b, user_b))
            cur.execute("""
                INSERT INTO public.gst_registrations (id, org_id, gstin, state_code, legal_name, is_primary)
                VALUES (%s, %s, %s, '29', 'M2 Tenant B Legal', true);
            """, (reg_b, org_b, gstin_b))

    return {
        "org_a": org_a, "user_a": user_a, "gstin_a": gstin_a, "reg_a": reg_a,
        "org_b": org_b, "user_b": user_b, "gstin_b": gstin_b, "reg_b": reg_b
    }


# =============================================================================
# 1. Multi-GST Registration Scoping under PAN
# =============================================================================

def test_multi_gst_registration_scoping(test_tenants):
    t = test_tenants
    # 1. Add secondary registration under same PAN in Org A
    second_gstin_a = f"24{t['gstin_a'][2:12]}1Z{calculate_gstin_checksum('24' + t['gstin_a'][2:12] + '1Z')}"
    second_reg_id = str(uuid.uuid4())

    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO public.gst_registrations (id, org_id, gstin, state_code, legal_name, is_primary)
                VALUES (%s, %s, %s, '24', 'M2 Tenant A Gujarat Branch', false);
            """, (second_reg_id, t["org_a"], second_gstin_a))

            # Verify unique constraint on (org_id, gstin)
            with pytest.raises(psycopg.errors.UniqueViolation):
                cur.execute("""
                    INSERT INTO public.gst_registrations (id, org_id, gstin, state_code, legal_name, is_primary)
                    VALUES (%s, %s, %s, '24', 'Duplicate Reg', false);
                """, (str(uuid.uuid4()), t["org_a"], second_gstin_a))

    # 2. Verify RLS with app_service_user
    with psycopg.connect(DB_APP, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('app.current_org', %s, false);", (t["org_a"],))
            cur.execute("SELECT gstin FROM public.gst_registrations;")
            rows_a = [r[0] for r in cur.fetchall()]
            assert t["gstin_a"] in rows_a
            assert second_gstin_a in rows_a
            assert t["gstin_b"] not in rows_a

            cur.execute("SELECT set_config('app.current_org', %s, false);", (t["org_b"],))
            cur.execute("SELECT gstin FROM public.gst_registrations;")
            rows_b = [r[0] for r in cur.fetchall()]
            assert t["gstin_b"] in rows_b
            assert t["gstin_a"] not in rows_b


# =============================================================================
# 2. GSTR-2B Statement Parser (JSON & Excel)
# =============================================================================

def test_gstr2b_json_parser_all_sections():
    supp_gstin_1 = random_gstin("27")
    supp_gstin_2 = random_gstin("27")
    recipient = random_gstin("27")

    sample_json = {
        "gstin": recipient,
        "fp": "112026",
        "fy": "2026-2027",
        "gendt": "2026-12-14",
        "data": {
            "b2b": [
                {
                    "ctin": supp_gstin_1,
                    "trdnm": "Supplier One Pvt Ltd",
                    "inv": [
                        {
                            "inum": "INV/2026/001",
                            "idt": "2026-11-04",
                            "val": 118000.00,
                            "pos": "27",
                            "rchrg": "N",
                            "itcavl": "Y",
                            "items": [
                                {
                                    "num": 1,
                                    "txval": 100000.00,
                                    "rt": 18.0,
                                    "camt": 9000.00,
                                    "samt": 9000.00,
                                    "iamt": 0.00,
                                    "csamt": 0.00
                                }
                            ]
                        }
                    ]
                }
            ],
            "b2ba": [
                {
                    "ctin": supp_gstin_2,
                    "trdnm": "Supplier Two Ltd",
                    "inv": [
                        {
                            "inum": "AMD-99",
                            "idt": "2026-11-06",
                            "val": 59000.00,
                            "pos": "27",
                            "rchrg": "N",
                            "itcavl": "Y",
                            "oinum": "ORIG-99",
                            "oidt": "2026-10-15",
                            "items": [
                                {"num": 1, "txval": 50000.00, "rt": 18.0, "camt": 4500.00, "samt": 4500.00, "iamt": 0.00, "csamt": 0.00}
                            ]
                        }
                    ]
                }
            ],
            "cdnr": [
                {
                    "ctin": supp_gstin_1,
                    "trdnm": "Supplier One Pvt Ltd",
                    "nt": [
                        {
                            "ntnum": "CRN-10",
                            "ntdt": "2026-11-10",
                            "val": 11800.00,
                            "ntty": "C",
                            "pos": "27",
                            "rchrg": "N",
                            "itcavl": "Y",
                            "items": [
                                {"num": 1, "txval": 10000.00, "rt": 18.0, "camt": 900.00, "samt": 900.00, "iamt": 0.00, "csamt": 0.00}
                            ]
                        }
                    ]
                }
            ],
            "isd": [
                {
                    "ctin": random_gstin("27"),
                    "trdnm": "Corporate ISD HO",
                    "doclist": [
                        {
                            "docnum": "ISD-DOC-01",
                            "docdt": "2026-11-15",
                            "doctyp": "ISD_INV",
                            "iamt": 12000.00,
                            "camt": 3000.00,
                            "samt": 3000.00,
                            "csamt": 0.00,
                            "itcavl": "Y"
                        }
                    ]
                }
            ],
            "impg": [
                {
                    "boenum": "BOE-CUSTOMS-440",
                    "boedt": "2026-11-20",
                    "portcode": "INBOM4",
                    "txval": 200000.00,
                    "iamt": 36000.00,
                    "csamt": 0.00,
                    "itcavl": "Y"
                }
            ]
        }
    }

    raw_bytes = json.dumps(sample_json).encode("utf-8")
    parsed: GSTR2BParsedStatement = parse_gstr2b_json(raw_bytes)

    assert parsed.recipient_gstin == recipient
    assert parsed.return_period == "2026-11"
    assert len(parsed.documents) == 5

    # Check Document types and normalization
    doc_numbers = [d.document_number for d in parsed.documents]
    assert "INV/2026/001" in doc_numbers
    assert "AMD-99" in doc_numbers
    assert "CRN-10" in doc_numbers
    assert "ISD-DOC-01" in doc_numbers
    assert "BOE-CUSTOMS-440" in doc_numbers

    # Verify document number normalizer helper
    assert normalize_doc_number("INV/2026/001") == "INV2026001"
    assert normalize_doc_number("000456") == "456"
    assert normalize_doc_number("AMD-99") == "AMD99"


def test_gstr2b_excel_parser():
    wb = openpyxl.Workbook()
    # Sheet 1: ITC Available
    ws_sum = wb.active
    ws_sum.title = "ITC Available"
    ws_sum.cell(row=2, column=1, value="GSTIN : 27AABCB1234M1ZT")
    ws_sum.cell(row=3, column=1, value="Return Period : 2026-11")

    # Sheet 2: B2B
    ws_b2b = wb.create_sheet(title="B2B")
    headers = [
        "GSTIN of Supplier", "Trade Name", "Invoice number", "Invoice date",
        "Invoice Value", "Place of supply", "Reverse Charge", "Invoice Type",
        "Taxable Value", "Integrated Tax", "Central Tax", "State/UT Tax", "Cess"
    ]
    ws_b2b.append(headers)
    ws_b2b.append([
        "27AAGCB1234F1ZQ", "Apex Industrial Ltd", "EXCEL-INV-001", "2026-11-05",
        59000, "27", "N", "Regular", 50000, 0, 4500, 4500, 0
    ])

    buf = io.BytesIO()
    wb.save(buf)
    excel_bytes = buf.getvalue()

    parsed = parse_gstr2b_excel(excel_bytes)
    assert parsed.recipient_gstin == "27AABCB1234M1ZT"
    assert parsed.return_period == "2026-11"
    assert len(parsed.documents) == 1
    assert parsed.documents[0].document_number == "EXCEL-INV-001"
    assert parsed.documents[0].taxable_value == Decimal("50000")
    assert parsed.documents[0].central_tax == Decimal("4500")


# =============================================================================
# 3. Snapshotting, Versioning, Deduplication & IMS Ingestion API
# =============================================================================

def test_gstr2b_upload_dedup_and_versioning(test_tenants):
    t = test_tenants

    # Generate synthetic statement payload
    sample_json = {
        "gstin": t["gstin_a"],
        "fp": "112026",
        "fy": "2026-2027",
        "gendt": "2026-12-14",
        "data": {
            "b2b": [
                {
                    "ctin": random_gstin("27"),
                    "trdnm": "Supplier A1",
                    "inv": [
                        {
                            "inum": "API-INV-001",
                            "idt": "2026-11-02",
                            "val": 11800.00,
                            "pos": "27",
                            "rchrg": "N",
                            "itcavl": "Y",
                            "items": [{"txval": 10000.00, "camt": 900.00, "samt": 900.00, "iamt": 0.00, "csamt": 0.00}]
                        }
                    ]
                }
            ]
        }
    }
    file_bytes = json.dumps(sample_json).encode("utf-8")

    headers = {
        "X-User-Id": t["user_a"],
        "X-Org-Id": t["org_a"]
    }

    # 1. First upload
    res1 = client.post(
        "/api/v1/reconciliation/gstr2b/upload",
        headers=headers,
        data={"registration_id": t["reg_a"], "return_period": "2026-11"},
        files={"file": ("gstr2b_nov_v1.json", file_bytes, "application/json")}
    )
    assert res1.status_code == 200, res1.text
    d1 = res1.json()
    assert d1["version_number"] == 1
    snapshot_1_id = d1["snapshot_id"]

    # 2. Duplicate upload of identical file (same hash) -> 409 Conflict
    res2 = client.post(
        "/api/v1/reconciliation/gstr2b/upload",
        headers=headers,
        data={"registration_id": t["reg_a"], "return_period": "2026-11"},
        files={"file": ("gstr2b_nov_v1_dup.json", file_bytes, "application/json")}
    )
    assert res2.status_code == 409

    # 3. Revised statement for same return period (Version 2)
    sample_json["data"]["b2b"][0]["inv"].append({
        "inum": "API-INV-002",
        "idt": "2026-11-08",
        "val": 23600.00,
        "pos": "27",
        "rchrg": "N",
        "itcavl": "Y",
        "items": [{"txval": 20000.00, "camt": 1800.00, "samt": 1800.00, "iamt": 0.00, "csamt": 0.00}]
    })
    file_bytes_v2 = json.dumps(sample_json).encode("utf-8")

    res3 = client.post(
        "/api/v1/reconciliation/gstr2b/upload",
        headers=headers,
        data={"registration_id": t["reg_a"], "return_period": "2026-11"},
        files={"file": ("gstr2b_nov_v2.json", file_bytes_v2, "application/json")}
    )
    assert res3.status_code == 200
    d3 = res3.json()
    assert d3["version_number"] == 2
    snapshot_2_id = d3["snapshot_id"]

    # Verify DB: Version 1 is marked inactive, Version 2 is active
    with psycopg.connect(DB_ADMIN) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, version_number, is_active FROM public.gstr2b_snapshots WHERE id = %s;", (snapshot_1_id,))
            s1 = cur.fetchone()
            assert s1[2] is False

            cur.execute("SELECT id, version_number, is_active, previous_snapshot_id FROM public.gstr2b_snapshots WHERE id = %s;", (snapshot_2_id,))
            s2 = cur.fetchone()
            assert s2[2] is True
            assert str(s2[3]) == snapshot_1_id


def test_ims_document_action_tracking(test_tenants):
    t = test_tenants
    headers = {"X-User-Id": t["user_a"], "X-Org-Id": t["org_a"]}

    # Upload statement with regular B2B and RCM
    supp_gstin = random_gstin("27")
    statement = {
        "gstin": t["gstin_a"],
        "fp": "112026",
        "data": {
            "b2b": [
                {
                    "ctin": supp_gstin,
                    "trdnm": "Supplier IMS Test",
                    "inv": [
                        {
                            "inum": "IMS-INV-REG",
                            "idt": "2026-11-04",
                            "val": 11800.00,
                            "pos": "27",
                            "rchrg": "N",
                            "itcavl": "Y",
                            "items": [{"txval": 10000.00, "camt": 900.00, "samt": 900.00, "iamt": 0.00, "csamt": 0.00}]
                        },
                        {
                            "inum": "IMS-INV-RCM",
                            "idt": "2026-11-05",
                            "val": 10500.00,
                            "pos": "27",
                            "rchrg": "Y",
                            "itcavl": "Y",
                            "items": [{"txval": 10000.00, "camt": 250.00, "samt": 250.00, "iamt": 0.00, "csamt": 0.00}]
                        }
                    ]
                }
            ]
        }
    }

    up_res = client.post(
        "/api/v1/reconciliation/gstr2b/upload",
        headers=headers,
        data={"registration_id": t["reg_a"], "return_period": "2026-11"},
        files={"file": (f"ims_test_{uuid.uuid4().hex[:6]}.json", json.dumps(statement).encode("utf-8"), "application/json")}
    )
    assert up_res.status_code == 200
    snap_id = up_res.json()["snapshot_id"]

    # List documents
    docs_res = client.get(f"/api/v1/reconciliation/gstr2b/documents?snapshot_id={snap_id}", headers=headers)
    assert docs_res.status_code == 200
    docs = docs_res.json()
    assert len(docs) == 2

    reg_doc = next(d for d in docs if d["document_number"] == "IMS-INV-REG")
    rcm_doc = next(d for d in docs if d["document_number"] == "IMS-INV-RCM")

    assert reg_doc["ims_action"] == "NO_ACTION"
    assert rcm_doc["ims_action"] == "NOT_AVAILABLE_IN_IMS"

    # Action regular document: ACCEPTED
    act_res = client.post(
        "/api/v1/reconciliation/ims/action",
        headers=headers,
        json={"gstr2b_doc_id": reg_doc["id"], "action": "ACCEPTED"}
    )
    assert act_res.status_code == 200
    assert act_res.json()["current_action"] == "ACCEPTED"
    assert act_res.json()["previous_action"] == "NO_ACTION"

    # Freeze period with simulated GSTR-3B filing
    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE public.ims_document_actions
                SET is_frozen_by_gstr3b = true, gstr3b_filing_date = clock_timestamp()
                WHERE gstr2b_doc_id = %s;
            """, (reg_doc["id"],))

    # Attempting to change frozen IMS action must be rejected (403)
    act_res2 = client.post(
        "/api/v1/reconciliation/ims/action",
        headers=headers,
        json={"gstr2b_doc_id": reg_doc["id"], "action": "REJECTED"}
    )
    assert act_res2.status_code == 403


# =============================================================================
# 4. Independent Purchase Register & Payment Allocation
# =============================================================================

def test_purchase_register_crud_and_approval(test_tenants):
    t = test_tenants
    supp_gstin = random_gstin("27")

    payload = PurchaseDocumentCreate(
        registration_id=t["reg_a"],
        document_type="tax_invoice",
        document_number="PO-2026-9001",
        document_date="2026-11-04",
        financial_period="2026-11",
        supplier_gstin=supp_gstin,
        supplier_legal_name="Apex Precision Tools",
        recipient_gstin=t["gstin_a"],
        place_of_supply="27",
        line_items=[
            PurchaseLineItemIn(
                item_description="Industrial CNC drill bits",
                hsn_sac_code="8207",
                quantity=Decimal("10"),
                unit_price=Decimal("10000.00"),
                taxable_amount=Decimal("100000.00"),
                gst_rate=Decimal("18.00"),
                cgst_amount=Decimal("9000.00"),
                sgst_amount=Decimal("9000.00"),
                igst_amount=Decimal("0.00"),
                total_amount=Decimal("118000.00")
            )
        ]
    )

    headers = {"X-User-Id": t["user_a"], "X-Org-Id": t["org_a"]}

    # 1. Create Purchase document via API
    res = client.post("/api/v1/reconciliation/purchase/create", headers=headers, json=json.loads(payload.model_dump_json()))
    assert res.status_code == 200, res.text
    p_data = res.json()
    assert p_data["status"] == "draft"
    p_id = p_data["id"]

    # 2. Duplicate document constraint verification
    res_dup = client.post("/api/v1/reconciliation/purchase/create", headers=headers, json=json.loads(payload.model_dump_json()))
    assert res_dup.status_code == 409

    # 3. Human Approval Workflow
    app_res = client.post(f"/api/v1/reconciliation/purchase/{p_id}/approve", headers=headers)
    assert app_res.status_code == 200
    assert app_res.json()["status"] == "approved"

    # 4. Record Vendor Payment (Rule 37 180-Day Safe Harbour)
    pay_payload = {
        "payment_reference": "NEFT-UTR-88219",
        "payment_date": "2026-11-12",
        "payment_mode": "NEFT",
        "bank_reference": "HDFC-BANK-0012",
        "total_amount_paid": 118000.00,
        "allocations": [
            {
                "purchase_doc_id": p_id,
                "allocated_taxable_amount": 100000.00,
                "allocated_tax_amount": 18000.00,
                "allocated_total_amount": 118000.00
            }
        ]
    }
    pay_res = client.post("/api/v1/reconciliation/purchase/payment", headers=headers, json=pay_payload)
    assert pay_res.status_code == 200
    assert pay_res.json()["allocation_count"] == 1


# =============================================================================
# 5. Deterministic 4-Pass Matching Engine
# =============================================================================

def test_deterministic_reconciliation_4_passes():
    engine = DeterministicReconciliationEngine(
        taxable_tolerance=Decimal("1.00"),
        tax_tolerance=Decimal("1.00"),
        total_tolerance=Decimal("2.00")
    )

    gstin_1 = "27AAGCB1234F1ZQ"
    gstin_2 = "27BBBBB2222B1Z8"
    gstin_3 = "27CCCCC3333C1Z7"

    purchase_docs = [
        # 1. Exact match candidate
        {
            "id": "p1",
            "document_number": "INV-101",
            "document_date": "2026-11-04",
            "supplier_gstin": gstin_1,
            "total_taxable_amount": Decimal("100000.00"),
            "cgst_amount": Decimal("9000.00"),
            "sgst_amount": Decimal("9000.00"),
            "igst_amount": Decimal("0.00"),
            "cess_amount": Decimal("0.00"),
            "total_invoice_value": Decimal("118000.00"),
            "is_reverse_charge": False,
            "status": "approved"
        },
        # 2. Amendment match candidate (Books has original number, Portal has amended)
        {
            "id": "p2",
            "document_number": "ORIG-202",
            "document_date": "2026-10-15",
            "supplier_gstin": gstin_2,
            "total_taxable_amount": Decimal("50000.00"),
            "cgst_amount": Decimal("4500.00"),
            "sgst_amount": Decimal("4500.00"),
            "igst_amount": Decimal("0.00"),
            "cess_amount": Decimal("0.00"),
            "total_invoice_value": Decimal("59000.00"),
            "is_reverse_charge": False,
            "status": "approved"
        },
        # 3. Rounding tolerance match candidate (differs by ₹0.40)
        {
            "id": "p3",
            "document_number": "ROUND-303",
            "document_date": "2026-11-10",
            "supplier_gstin": gstin_3,
            "total_taxable_amount": Decimal("10000.50"),
            "cgst_amount": Decimal("900.20"),
            "sgst_amount": Decimal("900.20"),
            "igst_amount": Decimal("0.00"),
            "cess_amount": Decimal("0.00"),
            "total_invoice_value": Decimal("11800.90"),
            "is_reverse_charge": False,
            "status": "approved"
        },
        # 4. Missing in Portal candidate
        {
            "id": "p4",
            "document_number": "MISSING-404",
            "document_date": "2026-11-12",
            "supplier_gstin": gstin_1,
            "total_taxable_amount": Decimal("20000.00"),
            "cgst_amount": Decimal("1800.00"),
            "sgst_amount": Decimal("1800.00"),
            "igst_amount": Decimal("0.00"),
            "cess_amount": Decimal("0.00"),
            "total_invoice_value": Decimal("23600.00"),
            "is_reverse_charge": False,
            "status": "approved"
        }
    ]

    gstr2b_docs = [
        # Matches p1 in Pass 1
        {
            "id": "g1",
            "document_number": "INV/101",
            "document_date": "2026-11-04",
            "supplier_gstin": gstin_1,
            "taxable_value": Decimal("100000.00"),
            "central_tax": Decimal("9000.00"),
            "state_ut_tax": Decimal("9000.00"),
            "integrated_tax": Decimal("0.00"),
            "cess_amount": Decimal("0.00"),
            "invoice_value": Decimal("118000.00"),
            "supply_attract_reverse_charge": False,
            "section_name": "B2B",
            "itc_availability": "available",
            "is_amended": False
        },
        # Matches p2 in Pass 2 via amendment link
        {
            "id": "g2",
            "document_number": "AMD-202",
            "document_date": "2026-11-06",
            "original_document_number": "ORIG-202",
            "original_document_date": "2026-10-15",
            "supplier_gstin": gstin_2,
            "taxable_value": Decimal("50000.00"),
            "central_tax": Decimal("4500.00"),
            "state_ut_tax": Decimal("4500.00"),
            "integrated_tax": Decimal("0.00"),
            "cess_amount": Decimal("0.00"),
            "invoice_value": Decimal("59000.00"),
            "supply_attract_reverse_charge": False,
            "section_name": "B2BA",
            "itc_availability": "available",
            "is_amended": True
        },
        # Matches p3 in Pass 3 with tolerance
        {
            "id": "g3",
            "document_number": "ROUND-303",
            "document_date": "2026-11-10",
            "supplier_gstin": gstin_3,
            "taxable_value": Decimal("10000.00"),
            "central_tax": Decimal("900.00"),
            "state_ut_tax": Decimal("900.00"),
            "integrated_tax": Decimal("0.00"),
            "cess_amount": Decimal("0.00"),
            "invoice_value": Decimal("11800.00"),
            "supply_attract_reverse_charge": False,
            "section_name": "B2B",
            "itc_availability": "available",
            "is_amended": False
        },
        # Extra in Portal (Missing in books candidate)
        {
            "id": "g5",
            "document_number": "PORTAL-EXTRA-99",
            "document_date": "2026-11-20",
            "supplier_gstin": gstin_2,
            "taxable_value": Decimal("30000.00"),
            "central_tax": Decimal("2700.00"),
            "state_ut_tax": Decimal("2700.00"),
            "integrated_tax": Decimal("0.00"),
            "cess_amount": Decimal("0.00"),
            "invoice_value": Decimal("35400.00"),
            "supply_attract_reverse_charge": False,
            "section_name": "B2B",
            "itc_availability": "available",
            "is_amended": False
        }
    ]

    matches, variances = engine.run_reconciliation(purchase_docs, gstr2b_docs)

    # Assert matches across passes
    assert len(matches) == 3

    m1 = next(m for m in matches if m.purchase_doc_id == "p1")
    assert m1.match_pass == 1
    assert m1.is_exact is True

    m2 = next(m for m in matches if m.purchase_doc_id == "p2")
    assert m2.match_pass == 2

    m3 = next(m for m in matches if m.purchase_doc_id == "p3")
    assert m3.match_pass == 3

    # Assert 11 variance categories detected
    var_categories = [v.variance_category for v in variances]
    assert MatchCategoryEnum.MISSING_IN_GSTR2B in var_categories
    assert MatchCategoryEnum.MISSING_IN_BOOKS in var_categories

    missing_p4 = next(v for v in variances if v.purchase_doc_id == "p4")
    assert missing_p4.variance_category == MatchCategoryEnum.MISSING_IN_GSTR2B

    missing_g5 = next(v for v in variances if v.gstr2b_doc_id == "g5")
    assert missing_g5.variance_category == MatchCategoryEnum.MISSING_IN_BOOKS


# =============================================================================
# 6. Versioned Statutory ITC Risk & Eligibility Engine
# =============================================================================

def test_itc_risk_engine_statutory_rules():
    engine = StatutoryITCRiskEngine()
    today = date(2026, 11, 18)

    # 1. Section 16(2)(aa) check: missing in GSTR-2B
    p_doc_unreported = {
        "id": "p_unrep",
        "document_number": "INV-UNREP",
        "document_date": "2026-11-01",
        "cgst_amount": Decimal("9000.00"),
        "sgst_amount": Decimal("9000.00"),
        "igst_amount": Decimal("0.00"),
        "cess_amount": Decimal("0.00"),
        "total_invoice_value": Decimal("118000.00"),
        "goods_receipt_date": "2026-11-03",
        "goods_receipt_note_ref": "GRN-01"
    }
    evals_unrep = engine.evaluate_itc_eligibility(purchase_doc=p_doc_unreported, gstr2b_doc=None, evaluation_date=today)
    sec_16_aa = next(e for e in evals_unrep if e.rule_code == "SEC_16_2_AA")
    assert sec_16_aa.eligibility_status == ITCEligibilityEnum.SUPPLIER_STATUS_UNKNOWN

    # 2. Section 16(2)(b) check: goods receipt missing
    p_doc_no_grn = {
        "id": "p_no_grn",
        "document_number": "INV-NOGRN",
        "document_date": "2026-11-01",
        "cgst_amount": Decimal("9000.00"),
        "sgst_amount": Decimal("9000.00"),
        "igst_amount": Decimal("0.00"),
        "cess_amount": Decimal("0.00"),
        "total_invoice_value": Decimal("118000.00"),
        "goods_receipt_date": None,
        "goods_receipt_note_ref": None
    }
    g_doc = {"id": "g_valid", "itc_availability": "available", "section_name": "B2B"}
    evals_no_grn = engine.evaluate_itc_eligibility(purchase_doc=p_doc_no_grn, gstr2b_doc=g_doc, evaluation_date=today)
    sec_16_b = next(e for e in evals_no_grn if e.rule_code == "SEC_16_2_B")
    assert sec_16_b.eligibility_status == ITCEligibilityEnum.PENDING_GOODS_OR_SERVICE_RECEIPT

    # 3. Rule 37: 180-day vendor payment non-compliance
    # Invoice dated 200 days ago, zero payments made
    old_date = (today - timedelta(days=200)).strftime("%Y-%m-%d")
    p_doc_rule37 = {
        "id": "p_rule37",
        "document_number": "INV-OLD-UNPAID",
        "document_date": old_date,
        "cgst_amount": Decimal("9000.00"),
        "sgst_amount": Decimal("9000.00"),
        "igst_amount": Decimal("0.00"),
        "cess_amount": Decimal("0.00"),
        "total_invoice_value": Decimal("118000.00"),
        "goods_receipt_date": old_date,
        "goods_receipt_note_ref": "GRN-OLD",
        "is_reverse_charge": False
    }
    evals_r37 = engine.evaluate_itc_eligibility(purchase_doc=p_doc_rule37, gstr2b_doc=g_doc, payments=[], evaluation_date=today)
    r37_eval = next(e for e in evals_r37 if e.rule_code == "RULE_37_180_DAYS")
    assert r37_eval.eligibility_status == ITCEligibilityEnum.PAYMENT_180_DAY_RISK
    assert r37_eval.risk_reversal_amount == Decimal("18000.00")
    assert "18% p.a." in r37_eval.findings_explanation

    # Partial payment allocated (50% paid): reversal proportionate to unpaid balance
    partial_payments = [
        {"allocated_total_amount": Decimal("59000.00"), "allocated_tax_amount": Decimal("9000.00")}
    ]
    evals_r37_partial = engine.evaluate_itc_eligibility(
        purchase_doc=p_doc_rule37, gstr2b_doc=g_doc, payments=partial_payments, evaluation_date=today
    )
    r37_partial_eval = next(e for e in evals_r37_partial if e.rule_code == "RULE_37_180_DAYS")
    assert r37_partial_eval.risk_reversal_amount == Decimal("9000.00")

    # 4. Section 17(5) Blocked Credit Catalogue Checks
    blocked_cases = [
        ("SEC_17_5_MOTOR", "Purchase of 5-seater passenger car for director"),
        ("SEC_17_5_FOOD", "Outdoor catering services for Diwali celebration"),
        ("SEC_17_5_PERSONAL", "Personal club membership fees"),
        ("SEC_17_5_WORKS_CONTR", "Works contract for construction of office building")
    ]
    for rule_code, desc in blocked_cases:
        p_blocked = {
            "id": f"p_{rule_code}",
            "document_number": "INV-BLOCKED",
            "document_date": "2026-11-01",
            "cgst_amount": Decimal("9000.00"),
            "sgst_amount": Decimal("9000.00"),
            "igst_amount": Decimal("0.00"),
            "cess_amount": Decimal("0.00"),
            "total_invoice_value": Decimal("118000.00"),
            "goods_receipt_date": "2026-11-02",
            "goods_receipt_note_ref": "GRN-01",
            "item_description": desc,
            "facts": {"is_factual_context_verified": True, "seating_capacity": 5} if rule_code == "SEC_17_5_MOTOR" else {}
        }
        evals_blocked = engine.evaluate_itc_eligibility(purchase_doc=p_blocked, gstr2b_doc=g_doc, evaluation_date=today)
        target_eval = next(e for e in evals_blocked if e.rule_code == rule_code)
        assert target_eval.eligibility_status == ITCEligibilityEnum.BLOCKED_CREDIT_INDICATOR


# =============================================================================
# 7. GSTR-3B Table 4 Working Output (4 Non-Confused Subtotals)
# =============================================================================

def test_gstr3b_table4_working_output_subtotals():
    gstr2b_docs = [
        # Regular B2B: 18,000
        {
            "id": "g1",
            "section_name": "B2B",
            "itc_availability": "available",
            "integrated_tax": Decimal("0.00"),
            "central_tax": Decimal("9000.00"),
            "state_ut_tax": Decimal("9000.00"),
            "cess_amount": Decimal("0.00")
        },
        # Overseas IMPG: 36,000
        {
            "id": "g2",
            "section_name": "IMPG",
            "itc_availability": "available",
            "integrated_tax": Decimal("36000.00"),
            "central_tax": Decimal("0.00"),
            "state_ut_tax": Decimal("0.00"),
            "cess_amount": Decimal("0.00")
        },
        # Inward RCM: 5,000
        {
            "id": "g3",
            "section_name": "RCM",
            "supply_attract_reverse_charge": True,
            "itc_availability": "available",
            "integrated_tax": Decimal("0.00"),
            "central_tax": Decimal("2500.00"),
            "state_ut_tax": Decimal("2500.00"),
            "cess_amount": Decimal("0.00")
        }
    ]

    purchase_docs = [
        # Matched and eligible: 18,000
        {
            "id": "p1",
            "cgst_amount": Decimal("9000.00"),
            "sgst_amount": Decimal("9000.00"),
            "igst_amount": Decimal("0.00"),
            "cess_amount": Decimal("0.00")
        },
        # Matched but Rule 37 reversed: 10,000
        {
            "id": "p2",
            "cgst_amount": Decimal("5000.00"),
            "sgst_amount": Decimal("5000.00"),
            "igst_amount": Decimal("0.00"),
            "cess_amount": Decimal("0.00")
        }
    ]

    matches = [
        {"purchase_doc_id": "p1", "gstr2b_doc_id": "g1", "match_pass": 1},
        {"purchase_doc_id": "p2", "gstr2b_doc_id": "g1", "match_pass": 1}
    ]

    assessments = [
        {
            "purchase_doc_id": "p1",
            "eligibility_status": ITCEligibilityEnum.ELIGIBLE_EVIDENCE_COMPLETE.value,
            "rule_code": "SEC_16_2_A",
            "risk_reversal_amount": Decimal("0.00")
        },
        {
            "purchase_doc_id": "p2",
            "eligibility_status": ITCEligibilityEnum.PAYMENT_180_DAY_RISK.value,
            "rule_code": "RULE_37_180_DAYS",
            "risk_reversal_amount": Decimal("10000.00")
        }
    ]

    human_approved = {"p1"}

    summary = generate_gstr3b_table4_summary(
        return_period="2026-11",
        recipient_gstin="27AABCB1234M1ZT",
        gstr2b_docs=gstr2b_docs,
        purchase_docs=purchase_docs,
        matches=matches,
        assessments=assessments,
        human_approved_ids=human_approved
    )

    # Verify 4 distinct statutory non-confused subtotals
    assert summary.subtotal_gstr2b_portal_available == Decimal("59000.00")  # 18k + 36k + 5k
    assert summary.subtotal_books_matched == Decimal("28000.00")           # 18k + 10k
    assert summary.subtotal_legally_eligible == Decimal("18000.00")        # 28k - 10k Rule 37
    assert summary.subtotal_human_approved == Decimal("18000.00")          # only p1

    # Verify Table 4 sections
    assert summary.itc_available_import_goods == Decimal("36000.00")
    assert summary.itc_available_inward_reverse_charge == Decimal("5000.00")
    assert summary.itc_reversed_rule_37_180_days == Decimal("10000.00")
    assert summary.total_itc_reversed == Decimal("10000.00")


# =============================================================================
# 8. Multitenant RLS Security Isolation across All Milestone 2 Tables
# =============================================================================

def test_multitenant_rls_isolation_milestone2(test_tenants):
    t = test_tenants

    # Seed Milestone 2 records in Tenant A and Tenant B
    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            # Import & snapshot A
            imp_a = str(uuid.uuid4())
            snap_a = str(uuid.uuid4())
            doc_a = str(uuid.uuid4())
            pur_a = str(uuid.uuid4())

            cur.execute("""
                INSERT INTO public.gstr2b_imports (id, org_id, registration_id, recipient_gstin, return_period, financial_year, file_name, file_type, file_hash, file_size_bytes)
                VALUES (%s, %s, %s, %s, '2026-11', '2026-2027', '2b_a.json', 'json', %s, 1024);
            """, (imp_a, t["org_a"], t["reg_a"], t["gstin_a"], uuid.uuid4().hex))

            cur.execute("""
                INSERT INTO public.gstr2b_snapshots (id, org_id, import_id, registration_id, recipient_gstin, return_period, version_number, total_itc_available)
                VALUES (%s, %s, %s, %s, %s, '2026-11', 1, 50000.00);
            """, (snap_a, t["org_a"], imp_a, t["reg_a"], t["gstin_a"]))

            cur.execute("""
                INSERT INTO public.gstr2b_source_documents (id, org_id, snapshot_id, section_name, itc_availability, source_form, document_type, document_number, document_date, place_of_supply)
                VALUES (%s, %s, %s, 'B2B', 'available', 'GSTR-1', 'INV', 'DOC-A-01', '2026-11-04', '27');
            """, (doc_a, t["org_a"], snap_a))

            cur.execute("""
                INSERT INTO public.purchase_documents (id, org_id, registration_id, document_type, document_number, document_date, financial_period, supplier_legal_name, recipient_gstin, place_of_supply)
                VALUES (%s, %s, %s, 'tax_invoice', 'PUR-A-01', '2026-11-04', '2026-11', 'Vendor A', %s, '27');
            """, (pur_a, t["org_a"], t["reg_a"], t["gstin_a"]))

            # Import & snapshot B
            imp_b = str(uuid.uuid4())
            snap_b = str(uuid.uuid4())
            doc_b = str(uuid.uuid4())
            pur_b = str(uuid.uuid4())

            cur.execute("""
                INSERT INTO public.gstr2b_imports (id, org_id, registration_id, recipient_gstin, return_period, financial_year, file_name, file_type, file_hash, file_size_bytes)
                VALUES (%s, %s, %s, %s, '2026-11', '2026-2027', '2b_b.json', 'json', %s, 1024);
            """, (imp_b, t["org_b"], t["reg_b"], t["gstin_b"], uuid.uuid4().hex))

            cur.execute("""
                INSERT INTO public.gstr2b_snapshots (id, org_id, import_id, registration_id, recipient_gstin, return_period, version_number, total_itc_available)
                VALUES (%s, %s, %s, %s, %s, '2026-11', 1, 75000.00);
            """, (snap_b, t["org_b"], imp_b, t["reg_b"], t["gstin_b"]))

            cur.execute("""
                INSERT INTO public.gstr2b_source_documents (id, org_id, snapshot_id, section_name, itc_availability, source_form, document_type, document_number, document_date, place_of_supply)
                VALUES (%s, %s, %s, 'B2B', 'available', 'GSTR-1', 'INV', 'DOC-B-01', '2026-11-04', '29');
            """, (doc_b, t["org_b"], snap_b))

            cur.execute("""
                INSERT INTO public.purchase_documents (id, org_id, registration_id, document_type, document_number, document_date, financial_period, supplier_legal_name, recipient_gstin, place_of_supply)
                VALUES (%s, %s, %s, 'tax_invoice', 'PUR-B-01', '2026-11-04', '2026-11', 'Vendor B', %s, '29');
            """, (pur_b, t["org_b"], t["reg_b"], t["gstin_b"]))

    # Test RLS under app_service_user
    with psycopg.connect(DB_APP, autocommit=True) as conn:
        with conn.cursor() as cur:
            # Set Tenant A context
            cur.execute("SELECT set_config('app.current_org', %s, false);", (t["org_a"],))

            cur.execute("SELECT id FROM public.gstr2b_imports;")
            imp_rows = [str(r[0]) for r in cur.fetchall()]
            assert imp_a in imp_rows
            assert imp_b not in imp_rows

            cur.execute("SELECT id FROM public.gstr2b_snapshots;")
            snap_rows = [str(r[0]) for r in cur.fetchall()]
            assert snap_a in snap_rows
            assert snap_b not in snap_rows

            cur.execute("SELECT id FROM public.gstr2b_source_documents;")
            doc_rows = [str(r[0]) for r in cur.fetchall()]
            assert doc_a in doc_rows
            assert doc_b not in doc_rows

            cur.execute("SELECT id FROM public.purchase_documents;")
            pur_rows = [str(r[0]) for r in cur.fetchall()]
            assert pur_a in pur_rows
            assert pur_b not in pur_rows

            # Attempt cross-tenant insert into Tenant B's snapshot as Tenant A -> RLS check violation
            with pytest.raises((psycopg.errors.InsufficientPrivilege, psycopg.errors.CheckViolation, psycopg.Error)):
                cur.execute("""
                    INSERT INTO public.gstr2b_source_documents (id, org_id, snapshot_id, section_name, itc_availability, source_form, document_type, document_number, document_date, place_of_supply)
                    VALUES (%s, %s, %s, 'B2B', 'available', 'GSTR-1', 'INV', 'HACK-01', '2026-11-04', '29');
                """, (str(uuid.uuid4()), t["org_b"], snap_b))
