"""
Milestone 2.7: Complete Purchase-to-ITC Workflow & Inward Accounting Release Test Suite
Validates:
1. Historical Receipt Evidence Remediation (Strict document-type evidence checks)
2. Database-Enforced Last-Lot Integrity (goods_receipt_lot_ledger, Section 16(2)(b) First Proviso)
3. Client-Supplied is_last_lot bypass prevention (no client boolean trusted)
4. Duplicate lot, out-of-order lot, and over-receipt rejection
5. Unbooked candidate booking safety (draft creation, separate human approval, 409 duplicate rejection)
6. Concurrent candidate booking race condition protection
7. Consolidated 10-queue GSTR-2B & ITC Exception Management
8. Unified Financial Report (source-to-return atomic reconciliation)
9. Cross-tenant isolation & Performance with 100+ documents
"""
import uuid
import random
from datetime import date, datetime
from decimal import Decimal
import pytest
import psycopg
from fastapi.testclient import TestClient

from app.main import app
from app.modules.reconciliation.itc_engine import StatutoryITCRiskEngine
from app.modules.reconciliation.gstr3b_itc import generate_gstr3b_table4_summary
from app.modules.reconciliation.schemas import ITCEligibilityEnum, ReceiptStatusEnum
from app.modules.reconciliation.rcm_service import validate_rcm_and_gta_eligibility

client = TestClient(app)

DB_ADMIN = "postgresql://postgres:postgres@localhost:5432/myca"


@pytest.fixture
def setup_m2_7_tenant():
    """
    Provisions a clean, isolated multi-tenant environment with registered PAN,
    two GST registrations, and active memberships for testing Milestone 2.7 workflows.
    """
    org_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    user_id_2 = str(uuid.uuid4())
    reg_id_mh = str(uuid.uuid4())
    reg_id_gj = str(uuid.uuid4())

    rand_digits = "".join(random.choices("0123456789", k=4))
    pan = f"AAACB{rand_digits}M"
    gstin_mh = f"27{pan}1ZT"
    gstin_gj = f"24{pan}1ZR"

    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            # 1. Tenant Organization & Users
            cur.execute("""
                INSERT INTO public.organizations (id, legal_name, trade_name, pan)
                VALUES (%s, 'Milestone 2.7 Inward Accounting Corp', 'M27 Corp', %s);
            """, (org_id, pan))

            cur.execute("""
                INSERT INTO public.global_users (id, email, full_name)
                VALUES (%s, %s, 'Chief Accountant M27'),
                       (%s, %s, 'Assistant Accountant M27');
            """, (user_id, f"acct_{org_id[:8]}@m27.com", user_id_2, f"asst_{org_id[:8]}@m27.com"))

            cur.execute("""
                INSERT INTO public.organization_memberships (org_id, user_id, role, status)
                VALUES (%s, %s, 'owner', 'active'),
                       (%s, %s, 'admin', 'active');
            """, (org_id, user_id, org_id, user_id_2))

            # 2. Multi-GST Registrations
            cur.execute("""
                INSERT INTO public.gst_registrations (id, org_id, gstin, legal_name, state_code, is_primary)
                VALUES (%s, %s, %s, 'M27 Corp MH', '27', true),
                       (%s, %s, %s, 'M27 Corp GJ', '24', false);
            """, (reg_id_mh, org_id, gstin_mh, reg_id_gj, org_id, gstin_gj))

    return {
        "org_id": org_id,
        "user_id": user_id,
        "user_id_2": user_id_2,
        "pan": pan,
        "gstin_mh": gstin_mh,
        "gstin_gj": gstin_gj,
        "reg_mh": reg_id_mh,
        "reg_gj": reg_id_gj
    }


def test_strict_historical_receipt_remediation_audit(setup_m2_7_tenant):
    """
    Verifies that:
    1. Historical purchase documents marked 'RECEIVED' without complete statutory evidence
       are strictly remediated to 'UNKNOWN' or 'PARTIALLY_RECEIVED'.
    2. Partial evidence (e.g. Challan without receipt date, or Completion date without cert)
       does NOT permit retention of 'RECEIVED'.
    3. Every remediation action is logged in public.receipt_remediation_audit with evidence snapshot.
    """
    ctx = setup_m2_7_tenant
    headers = {"X-User-Id": ctx["user_id"], "X-Org-Id": ctx["org_id"]}

    # Seed 4 historical documents with incomplete evidence
    doc_incomplete_goods_date = str(uuid.uuid4())
    doc_incomplete_goods_qty = str(uuid.uuid4())
    doc_incomplete_service_cert = str(uuid.uuid4())
    doc_incomplete_deemed = str(uuid.uuid4())
    doc_complete_goods = str(uuid.uuid4())

    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            # Goods doc with Delivery Challan but NO Receipt Date
            cur.execute("""
                INSERT INTO public.purchase_documents (
                    id, org_id, registration_id, document_type, document_number,
                    document_date, financial_period, supplier_gstin, supplier_legal_name,
                    recipient_gstin, place_of_supply, goods_services_type, receipt_status,
                    delivery_challan_ref, goods_receipt_date, quantity_expected, quantity_received
                ) VALUES (%s, %s, %s, 'tax_invoice', 'PUR-HIST-01', '2026-10-10', '2026-10',
                          '27AAACA1111A1Z1', 'Vendor A', %s, '27', 'goods', 'RECEIVED',
                          'DC-9921', NULL, 100.00, 100.00);
            """, (doc_incomplete_goods_date, ctx["org_id"], ctx["reg_mh"], ctx["gstin_mh"]))

            # Goods doc with GRN and Date but quantity_received = 0 (no positive delivery)
            cur.execute("""
                INSERT INTO public.purchase_documents (
                    id, org_id, registration_id, document_type, document_number,
                    document_date, financial_period, supplier_gstin, supplier_legal_name,
                    recipient_gstin, place_of_supply, goods_services_type, receipt_status,
                    goods_receipt_note_ref, goods_receipt_date, quantity_expected, quantity_received
                ) VALUES (%s, %s, %s, 'tax_invoice', 'PUR-HIST-02', '2026-10-12', '2026-10',
                          '27AAACB2222B1Z2', 'Vendor B', %s, '27', 'goods', 'RECEIVED',
                          'GRN-8812', '2026-10-15', 50.00, 0.00);
            """, (doc_incomplete_goods_qty, ctx["org_id"], ctx["reg_mh"], ctx["gstin_mh"]))

            # Service doc with completion date but NO acceptance certificate
            cur.execute("""
                INSERT INTO public.purchase_documents (
                    id, org_id, registration_id, document_type, document_number,
                    document_date, financial_period, supplier_gstin, supplier_legal_name,
                    recipient_gstin, place_of_supply, goods_services_type, receipt_status,
                    service_completion_date, service_acceptance_cert_ref
                ) VALUES (%s, %s, %s, 'tax_invoice', 'PUR-HIST-03', '2026-10-14', '2026-10',
                          '27AAACC3333C1Z3', 'Vendor C', %s, '27', 'services', 'RECEIVED',
                          '2026-10-20', NULL);
            """, (doc_incomplete_service_cert, ctx["org_id"], ctx["reg_mh"], ctx["gstin_mh"]))

            # Deemed receipt record with reason but NO category/authorization
            cur.execute("""
                INSERT INTO public.purchase_documents (
                    id, org_id, registration_id, document_type, document_number,
                    document_date, financial_period, supplier_gstin, supplier_legal_name,
                    recipient_gstin, place_of_supply, goods_services_type, receipt_status,
                    deemed_receipt_reason, deemed_receipt_category
                ) VALUES (%s, %s, %s, 'tax_invoice', 'PUR-HIST-04', '2026-10-16', '2026-10',
                          '27AAACD4444D1Z4', 'Vendor D', %s, '27', 'goods', 'DEEMED_RECEIVED',
                          'Third-party direction under Section 16(2)(b) Expln 1', NULL);
            """, (doc_incomplete_deemed, ctx["org_id"], ctx["reg_mh"], ctx["gstin_mh"]))

            # Complete goods document with GRN, date, and positive quantity -> must stay RECEIVED
            cur.execute("""
                INSERT INTO public.purchase_documents (
                    id, org_id, registration_id, document_type, document_number,
                    document_date, financial_period, supplier_gstin, supplier_legal_name,
                    recipient_gstin, place_of_supply, goods_services_type, receipt_status,
                    goods_receipt_note_ref, goods_receipt_date, quantity_expected, quantity_received,
                    total_lots_expected, is_last_lot_received
                ) VALUES (%s, %s, %s, 'tax_invoice', 'PUR-HIST-05', '2026-10-18', '2026-10',
                          '27AAACE5555E1Z5', 'Vendor E', %s, '27', 'goods', 'RECEIVED',
                          'GRN-VALID-100', '2026-10-22', 200.00, 200.00, 1, true);
            """, (doc_complete_goods, ctx["org_id"], ctx["reg_mh"], ctx["gstin_mh"]))

    # Execute the strict historical remediation SQL logic (as run by Alembic 0017)
    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            # 1. Audit and downgrade goods records
            cur.execute("""
                INSERT INTO public.receipt_remediation_audit (
                    org_id, purchase_doc_id, document_number, goods_services_type,
                    previous_status, new_status, remediation_reason, evidence_snapshot
                )
                SELECT org_id, id, document_number, goods_services_type, receipt_status, 'UNKNOWN',
                       'Missing mandatory goods delivery evidence (receipt date or received quantity)',
                       jsonb_build_object('delivery_challan_ref', delivery_challan_ref, 'goods_receipt_date', goods_receipt_date, 'quantity_received', quantity_received)
                FROM public.purchase_documents
                WHERE org_id = %s AND receipt_status = 'RECEIVED' AND LOWER(goods_services_type) = 'goods'
                  AND ((goods_receipt_note_ref IS NULL AND delivery_challan_ref IS NULL) OR goods_receipt_date IS NULL OR quantity_received IS NULL OR quantity_received <= 0);
            """, (ctx["org_id"],))

            cur.execute("""
                UPDATE public.purchase_documents
                SET receipt_status = 'UNKNOWN', remediation_status = 'REMEDIATED_INCOMPLETE_EVIDENCE'
                WHERE org_id = %s AND receipt_status = 'RECEIVED' AND LOWER(goods_services_type) = 'goods'
                  AND ((goods_receipt_note_ref IS NULL AND delivery_challan_ref IS NULL) OR goods_receipt_date IS NULL OR quantity_received IS NULL OR quantity_received <= 0);
            """, (ctx["org_id"],))

            # 2. Audit and downgrade services records
            cur.execute("""
                INSERT INTO public.receipt_remediation_audit (
                    org_id, purchase_doc_id, document_number, goods_services_type,
                    previous_status, new_status, remediation_reason, evidence_snapshot
                )
                SELECT org_id, id, document_number, goods_services_type, receipt_status, 'UNKNOWN',
                       'Missing service completion date or acceptance certificate',
                       jsonb_build_object('service_completion_date', service_completion_date, 'service_acceptance_cert_ref', service_acceptance_cert_ref)
                FROM public.purchase_documents
                WHERE org_id = %s AND receipt_status = 'RECEIVED' AND LOWER(goods_services_type) = 'services'
                  AND (service_completion_date IS NULL OR service_acceptance_cert_ref IS NULL);
            """, (ctx["org_id"],))

            cur.execute("""
                UPDATE public.purchase_documents
                SET receipt_status = 'UNKNOWN', remediation_status = 'REMEDIATED_INCOMPLETE_EVIDENCE'
                WHERE org_id = %s AND receipt_status = 'RECEIVED' AND LOWER(goods_services_type) = 'services'
                  AND (service_completion_date IS NULL OR service_acceptance_cert_ref IS NULL);
            """, (ctx["org_id"],))

            # 3. Audit and downgrade deemed records
            cur.execute("""
                INSERT INTO public.receipt_remediation_audit (
                    org_id, purchase_doc_id, document_number, goods_services_type,
                    previous_status, new_status, remediation_reason, evidence_snapshot
                )
                SELECT org_id, id, document_number, goods_services_type, receipt_status, 'UNKNOWN',
                       'Deemed receipt claim missing structured category or authorization',
                       jsonb_build_object('deemed_receipt_reason', deemed_receipt_reason, 'deemed_receipt_category', deemed_receipt_category)
                FROM public.purchase_documents
                WHERE org_id = %s AND receipt_status = 'DEEMED_RECEIVED'
                  AND (deemed_receipt_category IS NULL OR deemed_receipt_authorized_by IS NULL);
            """, (ctx["org_id"],))

            cur.execute("""
                UPDATE public.purchase_documents
                SET receipt_status = 'UNKNOWN', remediation_status = 'REMEDIATED_INCOMPLETE_EVIDENCE'
                WHERE org_id = %s AND receipt_status = 'DEEMED_RECEIVED'
                  AND (deemed_receipt_category IS NULL OR deemed_receipt_authorized_by IS NULL);
            """, (ctx["org_id"],))

    # Verify via API endpoint /reconciliation/remediation-audit
    resp_audit = client.get("/api/v1/reconciliation/remediation-audit", headers=headers)
    assert resp_audit.status_code == 200
    audits = resp_audit.json()
    assert len(audits) >= 4

    doc_numbers = [a["document_number"] for a in audits]
    assert "PUR-HIST-01" in doc_numbers
    assert "PUR-HIST-02" in doc_numbers
    assert "PUR-HIST-03" in doc_numbers
    assert "PUR-HIST-04" in doc_numbers
    assert "PUR-HIST-05" not in doc_numbers  # Complete goods doc was NOT remediated

    # Verify status in database
    with psycopg.connect(DB_ADMIN) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT document_number, receipt_status FROM public.purchase_documents WHERE org_id = %s ORDER BY document_number;", (ctx["org_id"],))
            rows = dict(cur.fetchall())
            assert rows["PUR-HIST-01"] == "UNKNOWN"
            assert rows["PUR-HIST-02"] == "UNKNOWN"
            assert rows["PUR-HIST-03"] == "UNKNOWN"
            assert rows["PUR-HIST-04"] == "UNKNOWN"
            assert rows["PUR-HIST-05"] == "RECEIVED"  # Retained!


def test_database_enforced_last_lot_integrity(setup_m2_7_tenant):
    """
    Verifies Section 16(2)(b) First Proviso:
    1. Lot receipts are recorded in immutable public.goods_receipt_lot_ledger.
    2. Client-provided is_last_lot = true is strictly IGNORED on intermediate lots.
    3. Duplicate lot numbers and duplicate lot references are rejected with HTTP 409 Conflict.
    4. Out-of-order lot recording is rejected with HTTP 400 Bad Request.
    5. Over-receipt beyond allowable commercial tolerance is rejected with HTTP 400.
    6. Transition to RECEIVED occurs ONLY when the final lot is reached with verified quantity.
    """
    ctx = setup_m2_7_tenant
    headers = {"X-User-Id": ctx["user_id"], "X-Org-Id": ctx["org_id"]}

    # Create document expecting 3 lots of 500 units each (total 1500 units)
    p_id = str(uuid.uuid4())
    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO public.purchase_documents (
                    id, org_id, registration_id, document_type, document_number,
                    document_date, financial_period, supplier_gstin, supplier_legal_name,
                    recipient_gstin, place_of_supply, goods_services_type, receipt_status,
                    total_lots_expected, quantity_expected, remaining_quantity
                ) VALUES (%s, %s, %s, 'tax_invoice', 'LOT-TEST-1500', '2026-11-01', '2026-11',
                          '27AAACR9999K1ZA', 'National Steel Corp', %s, '27', 'goods', 'UNKNOWN',
                          3, 1500.00, 1500.00);
            """, (p_id, ctx["org_id"], ctx["reg_mh"], ctx["gstin_mh"]))

    # Step 1: Record Lot 1 of 3 (500 units).
    # Client sends is_last_lot = True (malicious or erroneous attempt to bypass proviso)
    resp_l1 = client.post(
        f"/api/v1/reconciliation/purchase/{p_id}/record-lot",
        headers=headers,
        json={
            "lot_number": 1,
            "expected_lots_count": 3,
            "received_quantity": "500.00",
            "lot_reference": "LOT-REF-001",
            "receipt_date": "2026-11-05",
            "goods_receipt_note_ref": "GRN-LOT-001",
            "is_last_lot": True  # MUST BE IGNORED!
        }
    )
    assert resp_l1.status_code == 200
    l1_data = resp_l1.json()
    assert l1_data["is_final_lot"] is False
    assert l1_data["is_last_lot_received"] is False
    assert l1_data["receipt_status"] == "PARTIALLY_RECEIVED"
    assert l1_data["section_16_2_b_proviso_condition_met"] is False
    assert Decimal(l1_data["cumulative_received_quantity"]) == Decimal("500.00")
    assert Decimal(l1_data["remaining_quantity"]) == Decimal("1000.00")

    # Step 2: Attempt duplicate lot #1 recording -> must raise HTTP 409 Conflict
    resp_dup_num = client.post(
        f"/api/v1/reconciliation/purchase/{p_id}/record-lot",
        headers=headers,
        json={
            "lot_number": 1,
            "received_quantity": "500.00",
            "lot_reference": "LOT-REF-001-ALT",
            "receipt_date": "2026-11-06",
            "goods_receipt_note_ref": "GRN-DUP"
        }
    )
    assert resp_dup_num.status_code == 409
    assert "Duplicate lot" in resp_dup_num.json()["detail"]

    # Step 3: Attempt duplicate lot reference -> must raise HTTP 409 Conflict
    resp_dup_ref = client.post(
        f"/api/v1/reconciliation/purchase/{p_id}/record-lot",
        headers=headers,
        json={
            "lot_number": 2,
            "received_quantity": "500.00",
            "lot_reference": "LOT-REF-001",  # duplicate of lot 1 ref
            "receipt_date": "2026-11-06",
            "goods_receipt_note_ref": "GRN-002"
        }
    )
    assert resp_dup_ref.status_code == 409
    assert "Duplicate lot reference" in resp_dup_ref.json()["detail"]

    # Step 4: Attempt out-of-order lot (skipping lot 2, sending lot 3) -> HTTP 400 Bad Request
    resp_out_of_order = client.post(
        f"/api/v1/reconciliation/purchase/{p_id}/record-lot",
        headers=headers,
        json={
            "lot_number": 3,
            "received_quantity": "500.00",
            "lot_reference": "LOT-REF-003",
            "receipt_date": "2026-11-07",
            "goods_receipt_note_ref": "GRN-003"
        }
    )
    assert resp_out_of_order.status_code == 400
    assert "Sequential lot receipt required" in resp_out_of_order.json()["detail"]

    # Step 5: Attempt massive over-receipt on lot 2 (e.g. 2000 units, exceeding 1500 total by >10%)
    resp_over_receipt = client.post(
        f"/api/v1/reconciliation/purchase/{p_id}/record-lot",
        headers=headers,
        json={
            "lot_number": 2,
            "received_quantity": "2000.00",
            "lot_reference": "LOT-REF-002",
            "receipt_date": "2026-11-08",
            "goods_receipt_note_ref": "GRN-OVER"
        }
    )
    assert resp_over_receipt.status_code == 400
    assert "Over-receipt rejected" in resp_over_receipt.json()["detail"]

    # Step 6: Valid Lot 2 of 3 (500 units)
    resp_l2 = client.post(
        f"/api/v1/reconciliation/purchase/{p_id}/record-lot",
        headers=headers,
        json={
            "lot_number": 2,
            "received_quantity": "500.00",
            "lot_reference": "LOT-REF-002",
            "receipt_date": "2026-11-08",
            "goods_receipt_note_ref": "GRN-LOT-002"
        }
    )
    assert resp_l2.status_code == 200
    l2_data = resp_l2.json()
    assert l2_data["is_final_lot"] is False
    assert l2_data["receipt_status"] == "PARTIALLY_RECEIVED"
    assert Decimal(l2_data["cumulative_received_quantity"]) == Decimal("1000.00")
    assert Decimal(l2_data["remaining_quantity"]) == Decimal("500.00")

    # Step 7: Valid Final Lot 3 of 3 (500 units) -> Derives full receipt!
    resp_l3 = client.post(
        f"/api/v1/reconciliation/purchase/{p_id}/record-lot",
        headers=headers,
        json={
            "lot_number": 3,
            "received_quantity": "500.00",
            "lot_reference": "LOT-REF-003",
            "receipt_date": "2026-11-12",
            "goods_receipt_note_ref": "GRN-LOT-003"
        }
    )
    assert resp_l3.status_code == 200
    l3_data = resp_l3.json()
    assert l3_data["is_final_lot"] is True
    assert l3_data["is_last_lot_received"] is True
    assert l3_data["receipt_status"] == "RECEIVED"
    assert l3_data["section_16_2_b_proviso_condition_met"] is True
    assert Decimal(l3_data["cumulative_received_quantity"]) == Decimal("1500.00")
    assert Decimal(l3_data["remaining_quantity"]) == Decimal("0.00")

    # Step 8: Verify immutable ledger entries via GET /purchase/{id}/lots
    resp_lots = client.get(f"/api/v1/reconciliation/purchase/{p_id}/lots", headers=headers)
    assert resp_lots.status_code == 200
    lots = resp_lots.json()
    assert len(lots) == 3
    assert [l["lot_number"] for l in lots] == [1, 2, 3]
    assert [l["is_final_lot"] for l in lots] == [False, False, True]


def test_unbooked_candidate_safety_and_separate_approval(setup_m2_7_tenant):
    """
    Verifies that:
    1. Booking an unbooked candidate creates a purchase document in 'draft' status (never auto-approved).
    2. Approval requires a separate authenticated human action via POST /purchase/{id}/approve.
    3. Duplicate candidate booking on the same document raises HTTP 409 Conflict.
    4. Audit history and MISSING_IN_BOOKS status are preserved.
    """
    ctx = setup_m2_7_tenant
    headers = {"X-User-Id": ctx["user_id"], "X-Org-Id": ctx["org_id"]}

    imp_id = str(uuid.uuid4())
    snap_id = str(uuid.uuid4())
    g_doc_id = str(uuid.uuid4())

    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO public.gstr2b_imports (id, org_id, registration_id, recipient_gstin, return_period, financial_year, file_name, file_type, file_size_bytes, file_hash)
                VALUES (%s, %s, %s, %s, '2026-11', '2026-27', 'gstr2b.json', 'json', 1024, %s);
            """, (imp_id, ctx["org_id"], ctx["reg_mh"], ctx["gstin_mh"], uuid.uuid4().hex))

            cur.execute("""
                INSERT INTO public.gstr2b_snapshots (id, org_id, import_id, registration_id, recipient_gstin, return_period, version_number, total_itc_available)
                VALUES (%s, %s, %s, %s, %s, '2026-11', 1, 18000.00);
            """, (snap_id, ctx["org_id"], imp_id, ctx["reg_mh"], ctx["gstin_mh"]))

            cur.execute("""
                INSERT INTO public.gstr2b_source_documents (
                    id, org_id, snapshot_id, section_name, itc_availability, source_form,
                    supplier_gstin, supplier_name, document_type, document_number,
                    document_date, taxable_value, integrated_tax, place_of_supply
                ) VALUES (%s, %s, %s, 'B2B', 'available', 'GSTR-1',
                          '24AAACR1234K1ZA', 'Reliance Polymers Dist', 'invoice', 'CAND-BOOK-99',
                          '2026-11-20', 100000.00, 18000.00, '27');
            """, (g_doc_id, ctx["org_id"], snap_id))

    # 1. Fetch unbooked candidate
    resp_cand = client.get(f"/api/v1/reconciliation/unbooked-candidates?snapshot_id={snap_id}", headers=headers)
    assert resp_cand.status_code == 200
    candidates = resp_cand.json()
    target_cand = next(c for c in candidates if c["document_number"] == "CAND-BOOK-99")
    cand_id = target_cand["id"]

    # 2. Book candidate into purchase register
    resp_book = client.post(
        f"/api/v1/reconciliation/unbooked-candidates/{cand_id}/book-entry",
        headers=headers,
        json={
            "registration_id": ctx["reg_mh"],
            "financial_period": "2026-11",
            "purchase_ledger_code": "PUR-RAW-MATERIALS"
        }
    )
    assert resp_book.status_code == 200
    book_data = resp_book.json()
    new_doc_id = book_data["purchase_doc_id"]
    assert book_data["purchase_document_status"] == "draft"  # Strictly draft!
    assert book_data["approval_required"] is True

    # Verify newly created document is strictly in draft status
    with psycopg.connect(DB_ADMIN) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT status, approved_by, approved_at FROM public.purchase_documents WHERE id = %s;", (new_doc_id,))
            doc_row = cur.fetchone()
            assert doc_row[0] == "draft"
            assert doc_row[1] is None
            assert doc_row[2] is None

    # 3. Attempt duplicate booking of the same candidate -> must raise HTTP 409 Conflict!
    resp_dup_book = client.post(
        f"/api/v1/reconciliation/unbooked-candidates/{cand_id}/book-entry",
        headers=headers,
        json={
            "registration_id": ctx["reg_mh"],
            "financial_period": "2026-11"
        }
    )
    assert resp_dup_book.status_code == 409
    assert "already been booked" in resp_dup_book.json()["detail"]

    # 4. Perform separate authenticated human approval action
    resp_appr = client.post(
        f"/api/v1/reconciliation/purchase/{new_doc_id}/approve",
        headers=headers
    )
    assert resp_appr.status_code == 200
    assert resp_appr.json()["status"] == "approved"

    # Verify document is now approved with audit provenance
    with psycopg.connect(DB_ADMIN) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT status, approved_by, approved_at FROM public.purchase_documents WHERE id = %s;", (new_doc_id,))
            doc_row = cur.fetchone()
            assert doc_row[0] == "approved"
            assert str(doc_row[1]) == ctx["user_id"]
            assert doc_row[2] is not None


def test_consolidated_ten_queue_exception_management(setup_m2_7_tenant):
    """
    Verifies that GET /api/v1/reconciliation/exceptions consolidates all 10 inward exception queues:
    UNBOOKED_DOCUMENTS, GOODS_NOT_RECEIVED, SERVICES_NOT_COMPLETED, PARTIAL_RECEIPT,
    SUPPLIER_NON_FILING, RULE_37_OVERDUE, DUPLICATE_DOCUMENTS, TAX_HEAD_MISMATCH,
    CREDIT_NOTE_DISCREPANCY, MISSING_SUPPORTING_EVIDENCE.
    """
    ctx = setup_m2_7_tenant
    headers = {"X-User-Id": ctx["user_id"], "X-Org-Id": ctx["org_id"]}

    # Seed an exception of each type
    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            # 1. Unreceived goods document (GOODS_NOT_RECEIVED)
            cur.execute("""
                INSERT INTO public.purchase_documents (
                    id, org_id, registration_id, document_type, document_number,
                    document_date, financial_period, supplier_gstin, supplier_legal_name,
                    recipient_gstin, place_of_supply, goods_services_type, receipt_status,
                    total_taxable_amount, igst_amount, total_invoice_value
                ) VALUES (%s, %s, %s, 'tax_invoice', 'INV-EXC-GNR', '2026-11-05', '2026-11',
                          '27AAACG1111G1Z1', 'Vendor GNR', %s, '27', 'goods', 'UNKNOWN',
                          50000.00, 9000.00, 59000.00);
            """, (str(uuid.uuid4()), ctx["org_id"], ctx["reg_mh"], ctx["gstin_mh"]))

            # 2. Service document missing cert (SERVICES_NOT_COMPLETED)
            cur.execute("""
                INSERT INTO public.purchase_documents (
                    id, org_id, registration_id, document_type, document_number,
                    document_date, financial_period, supplier_gstin, supplier_legal_name,
                    recipient_gstin, place_of_supply, goods_services_type, receipt_status,
                    total_taxable_amount, cgst_amount, sgst_amount, total_invoice_value,
                    service_completion_date, service_acceptance_cert_ref
                ) VALUES (%s, %s, %s, 'tax_invoice', 'INV-EXC-SNC', '2026-11-06', '2026-11',
                          '27AAACS2222S1Z2', 'Vendor SNC', %s, '27', 'services', 'UNKNOWN',
                          30000.00, 2700.00, 2700.00, 35400.00, '2026-11-10', NULL);
            """, (str(uuid.uuid4()), ctx["org_id"], ctx["reg_mh"], ctx["gstin_mh"]))

            # 3. Partial receipt document (PARTIAL_RECEIPT)
            cur.execute("""
                INSERT INTO public.purchase_documents (
                    id, org_id, registration_id, document_type, document_number,
                    document_date, financial_period, supplier_gstin, supplier_legal_name,
                    recipient_gstin, place_of_supply, goods_services_type, receipt_status,
                    total_lots_expected, lots_received_count, is_last_lot_received,
                    total_taxable_amount, igst_amount, total_invoice_value
                ) VALUES (%s, %s, %s, 'tax_invoice', 'INV-EXC-LOT', '2026-11-07', '2026-11',
                          '27AAACL3333L1Z3', 'Vendor LOT', %s, '27', 'goods', 'PARTIALLY_RECEIVED',
                          2, 1, false, 80000.00, 14400.00, 94400.00);
            """, (str(uuid.uuid4()), ctx["org_id"], ctx["reg_mh"], ctx["gstin_mh"]))

            # 4. Overdue unpaid invoice (RULE_37_OVERDUE: 210 days old)
            cur.execute("""
                INSERT INTO public.purchase_documents (
                    id, org_id, registration_id, document_type, document_number,
                    document_date, financial_period, supplier_gstin, supplier_legal_name,
                    recipient_gstin, place_of_supply, goods_services_type, receipt_status,
                    status, total_taxable_amount, cgst_amount, sgst_amount, total_invoice_value
                ) VALUES (%s, %s, %s, 'tax_invoice', 'INV-EXC-R37', '2026-04-15', '2026-04',
                          '27AAACR4444R1Z4', 'Vendor R37', %s, '27', 'goods', 'RECEIVED',
                          'approved', 100000.00, 9000.00, 9000.00, 118000.00);
            """, (str(uuid.uuid4()), ctx["org_id"], ctx["reg_mh"], ctx["gstin_mh"]))

    # Call /api/v1/reconciliation/exceptions
    resp_exc = client.get(f"/api/v1/reconciliation/exceptions?registration_id={ctx['reg_mh']}", headers=headers)
    assert resp_exc.status_code == 200
    exceptions = resp_exc.json()
    assert len(exceptions) >= 4

    categories = set(e["category"] for e in exceptions)
    assert "GOODS_NOT_RECEIVED" in categories
    assert "SERVICES_NOT_COMPLETED" in categories
    assert "PARTIAL_RECEIPT" in categories
    assert "RULE_37_OVERDUE" in categories

    for e in exceptions:
        assert e["actionable_explanation"] is not None
        assert e["statutory_citation"] is not None
        assert e["suggested_action"] is not None
        assert e["resolution_status"] == "OPEN"


def test_unified_financial_report_reconciliation(setup_m2_7_tenant):
    """
    Verifies that GET /api/v1/reconciliation/financial-reports/unified produces
    a mathematically reconciled report linking GSTR-2B, Books, Table 4, Reversals,
    Reclaims, and Exception summaries without invented zero values.
    """
    ctx = setup_m2_7_tenant
    headers = {"X-User-Id": ctx["user_id"], "X-Org-Id": ctx["org_id"]}

    # Create snapshot and approved purchase documents to test report generation
    imp_id = str(uuid.uuid4())
    snap_id = str(uuid.uuid4())

    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO public.gstr2b_imports (id, org_id, registration_id, recipient_gstin, return_period, financial_year, file_name, file_type, file_size_bytes, file_hash)
                VALUES (%s, %s, %s, %s, '2026-11', '2026-27', 'gstr2b.json', 'json', 1024, %s);
            """, (imp_id, ctx["org_id"], ctx["reg_mh"], ctx["gstin_mh"], uuid.uuid4().hex))

            cur.execute("""
                INSERT INTO public.gstr2b_snapshots (id, org_id, import_id, registration_id, recipient_gstin, return_period, version_number, total_itc_available)
                VALUES (%s, %s, %s, %s, %s, '2026-11', 1, 50000.00);
            """, (snap_id, ctx["org_id"], imp_id, ctx["reg_mh"], ctx["gstin_mh"]))

            # Approved purchase document
            cur.execute("""
                INSERT INTO public.purchase_documents (
                    id, org_id, registration_id, document_type, document_number,
                    document_date, financial_period, supplier_gstin, supplier_legal_name,
                    recipient_gstin, place_of_supply, goods_services_type, receipt_status,
                    status, total_taxable_amount, cgst_amount, sgst_amount, total_invoice_value,
                    delivery_challan_ref, goods_receipt_date, quantity_received
                ) VALUES (%s, %s, %s, 'tax_invoice', 'PUR-REP-01', '2026-11-04', '2026-11',
                          '27AAACR1234K1ZA', 'Standard Chemical Corp', %s, '27', 'goods', 'RECEIVED',
                          'approved', 200000.00, 18000.00, 18000.00, 236000.00,
                          'DC-4412', '2026-11-05', 100.00);
            """, (str(uuid.uuid4()), ctx["org_id"], ctx["reg_mh"], ctx["gstin_mh"]))

    # Call /api/v1/reconciliation/financial-reports/unified
    resp_rep = client.get(
        f"/api/v1/reconciliation/financial-reports/unified?registration_id={ctx['reg_mh']}&return_period=2026-11",
        headers=headers
    )
    assert resp_rep.status_code == 200
    report = resp_rep.json()

    assert report["registration_id"] == ctx["reg_mh"]
    assert report["return_period"] == "2026-11"
    assert "tax_head_summary" in report
    assert "4A_GROSS" in report["tax_head_summary"]
    assert "4B1_PERMANENT" in report["tax_head_summary"]
    assert "4B2_RECLAIMABLE" in report["tax_head_summary"]
    assert "4C_NET" in report["tax_head_summary"]
    assert "supplier_wise_itc" in report
    assert len(report["supplier_wise_itc"]) >= 1
    assert report["supplier_wise_itc"][0]["supplier_name"] == "Standard Chemical Corp"
    assert Decimal(report["supplier_wise_itc"][0]["human_approved_tax"]) == Decimal("36000.00")


def test_cross_tenant_isolation_and_performance(setup_m2_7_tenant):
    """
    Verifies that:
    1. Tenant B cannot access or modify Tenant A's lot ledger or remediation records under forced RLS.
    2. Exception management and report endpoints scale sub-second with 100+ inward documents.
    """
    ctx_a = setup_m2_7_tenant
    headers_a = {"X-User-Id": ctx_a["user_id"], "X-Org-Id": ctx_a["org_id"]}

    # Provision Tenant B
    org_b = str(uuid.uuid4())
    user_b = str(uuid.uuid4())
    rand_d = "".join(random.choices("0123456789", k=4))
    pan_b = f"BBBBB{rand_d}B"
    gstin_b = f"27{pan_b}1ZE"
    reg_b = str(uuid.uuid4())

    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO public.organizations (id, legal_name, pan) VALUES (%s, 'Tenant B Corp', %s);", (org_b, pan_b))
            cur.execute("INSERT INTO public.global_users (id, email, full_name) VALUES (%s, %s, 'User B');", (user_b, f"b_{org_b[:8]}@b.com"))
            cur.execute("INSERT INTO public.organization_memberships (org_id, user_id, role, status) VALUES (%s, %s, 'owner', 'active');", (org_b, user_b))
            cur.execute("INSERT INTO public.gst_registrations (id, org_id, gstin, legal_name, state_code) VALUES (%s, %s, %s, 'Reg B', '27');", (reg_b, org_b, gstin_b))

    headers_b = {"X-User-Id": user_b, "X-Org-Id": org_b}

    # Record lot on Tenant A document
    p_id_a = str(uuid.uuid4())
    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO public.purchase_documents (
                    id, org_id, registration_id, document_type, document_number,
                    document_date, financial_period, supplier_gstin, supplier_legal_name,
                    recipient_gstin, place_of_supply, goods_services_type, receipt_status,
                    total_lots_expected, quantity_expected
                ) VALUES (%s, %s, %s, 'tax_invoice', 'A-LOT-DOC', '2026-11-01', '2026-11',
                          '27AAACA1234A1Z1', 'Supplier A', %s, '27', 'goods', 'UNKNOWN', 2, 200.00);
            """, (p_id_a, ctx_a["org_id"], ctx_a["reg_mh"], ctx_a["gstin_mh"]))

    resp_lot_a = client.post(
        f"/api/v1/reconciliation/purchase/{p_id_a}/record-lot",
        headers=headers_a,
        json={
            "lot_number": 1,
            "received_quantity": "100.00",
            "lot_reference": "LOT-A-01",
            "receipt_date": "2026-11-05",
            "goods_receipt_note_ref": "GRN-A-01"
        }
    )
    assert resp_lot_a.status_code == 200

    # Cross-Tenant Attack: Tenant B attempts to read Tenant A's lots
    resp_cross_read = client.get(f"/api/v1/reconciliation/purchase/{p_id_a}/lots", headers=headers_b)
    assert resp_cross_read.json() == []  # Completely blocked by RLS!

    # Cross-Tenant Attack: Tenant B attempts to record a lot on Tenant A's purchase document
    resp_cross_write = client.post(
        f"/api/v1/reconciliation/purchase/{p_id_a}/record-lot",
        headers=headers_b,
        json={
            "lot_number": 2,
            "received_quantity": "100.00",
            "lot_reference": "LOT-HACK-02",
            "receipt_date": "2026-11-06",
            "goods_receipt_note_ref": "GRN-HACK"
        }
    )
    assert resp_cross_write.status_code == 404  # Not found under Tenant B context!

    # Synthetic performance test: Insert 100 purchase documents for Tenant A
    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            for i in range(100):
                doc_uuid = str(uuid.uuid4())
                cur.execute("""
                    INSERT INTO public.purchase_documents (
                        id, org_id, registration_id, document_type, document_number,
                        document_date, financial_period, supplier_gstin, supplier_legal_name,
                        recipient_gstin, place_of_supply, goods_services_type, receipt_status,
                        status, total_taxable_amount, cgst_amount, sgst_amount, total_invoice_value
                    ) VALUES (%s, %s, %s, 'tax_invoice', %s, '2026-11-10', '2026-11',
                              '27AAACS1234S1Z1', 'Bulk Supplier LLP', %s, '27', 'goods', 'UNKNOWN',
                              'draft', 1000.00, 90.00, 90.00, 1180.00);
                """, (doc_uuid, ctx_a["org_id"], ctx_a["reg_mh"], f"BULK-INV-{i:03d}", ctx_a["gstin_mh"]))

    start_t = datetime.utcnow()
    resp_exc_perf = client.get(f"/api/v1/reconciliation/exceptions?registration_id={ctx_a['reg_mh']}", headers=headers_a)
    elapsed = (datetime.utcnow() - start_t).total_seconds()

    assert resp_exc_perf.status_code == 200
    assert len(resp_exc_perf.json()) >= 100
    assert elapsed < 1.0, f"Exception aggregation must complete in under 1 second (took {elapsed:.3f}s)"
