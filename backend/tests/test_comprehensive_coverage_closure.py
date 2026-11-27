"""
Comprehensive coverage closure tests verifying:
1. /api/v1/auth/me context hydration and /api/v1/auth/logout server-side JWT jti revocation
2. RBAC role matrix across viewer, accountant, admin, and owner roles
3. Multi-GSTIN registration scoping within a single organization
4. Previously uncovered routes (/export/audit-report, /gstr3b/status, POST /gstr3b/{id}/file)
5. End-to-end S3 storage upload -> worker OCR processing via S3 URI
6. WorkerRunner & HeartbeatManager lifecycle behaviors
"""

import io
import json
import random
import string
import sys
import types
import uuid
from unittest.mock import MagicMock

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.core.security import create_access_token
from app.database import SessionLocal, set_tenant_context
from app.main import app
from app.modules.invoice_brain.service import process_invoice_ocr_task, upload_and_enqueue_invoice
from tests.test_db_config import DB_ADMIN, DB_WORKER
from worker.runner import HeartbeatManager, WorkerRunner


def _make_gstin(pan: str, state: str = "27") -> str:
    return f"{state}{pan}1ZT"


@pytest.fixture
def rbac_org():
    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            org_id = str(uuid.uuid4())
            prefix = "".join(random.choices(string.ascii_uppercase, k=5))
            digits = f"{random.randint(1000, 9999)}"
            pan = f"{prefix}{digits}K"
            gstin_mh = _make_gstin(pan, "27")
            gstin_ka = _make_gstin(pan, "29")
            reg_mh = str(uuid.uuid4())
            reg_ka = str(uuid.uuid4())

            users = {}
            for role in ("owner", "admin", "accountant", "viewer"):
                uid = str(uuid.uuid4())
                users[role] = uid

            cur.execute(
                "INSERT INTO public.organizations (id, legal_name, pan) VALUES (%s, 'Coverage Org Ltd', %s);",
                (org_id, pan),
            )
            for role, uid in users.items():
                cur.execute(
                    "INSERT INTO public.global_users (id, email, full_name, is_active) VALUES (%s, %s, %s, true);",
                    (uid, f"{role}_{uuid.uuid4().hex}@example.com", f"Test {role.title()}"),
                )
                cur.execute(
                    "INSERT INTO public.organization_memberships (org_id, user_id, role, status) VALUES (%s, %s, %s, 'active');",
                    (org_id, uid, role),
                )

            cur.execute(
                """
                INSERT INTO public.gst_registrations (id, org_id, gstin, state_code, legal_name, is_primary)
                VALUES (%s, %s, %s, '27', 'Coverage Org MH', true),
                       (%s, %s, %s, '29', 'Coverage Org KA', false);
                """,
                (reg_mh, org_id, gstin_mh, reg_ka, org_id, gstin_ka),
            )
            cur.execute(
                """
                INSERT INTO public.business_profiles (id, org_id, gstin, business_type, industry_sector)
                VALUES (%s, %s, %s, 'Private Limited', 'Manufacturing'),
                       (%s, %s, %s, 'Private Limited', 'Manufacturing');
                """,
                (reg_mh, org_id, gstin_mh, reg_ka, org_id, gstin_ka),
            )
            cur.execute(
                "INSERT INTO public.worker_service_authorizations (worker_role_name, org_id, is_active) VALUES ('worker_service_user', %s, true);",
                (org_id,),
            )

    return {
        "org_id": org_id,
        "pan": pan,
        "reg_mh": reg_mh,
        "reg_ka": reg_ka,
        "gstin_mh": gstin_mh,
        "gstin_ka": gstin_ka,
        "users": users,
    }


def test_auth_me_and_logout_jwt_revocation(rbac_org):
    client = TestClient(app)
    org_id = rbac_org["org_id"]
    owner_id = rbac_org["users"]["owner"]
    token = create_access_token(subject=owner_id, org_id=org_id)
    headers = {"Authorization": f"Bearer {token}"}

    me_res = client.get("/api/v1/auth/me", headers=headers)
    assert me_res.status_code == 200
    body = me_res.json()
    assert body["org_id"] == org_id
    assert body["user_id"] == owner_id
    assert body["role"] == "owner"
    assert body["organization"]["pan"] == rbac_org["pan"]
    gstins = {r["gstin"] for r in body["registrations"]}
    assert gstins == {rbac_org["gstin_mh"], rbac_org["gstin_ka"]}

    # Logout must revoke jti server-side and reject reuse of the same JWT
    logout_res = client.post("/api/v1/auth/logout", headers=headers)
    assert logout_res.status_code == 200
    after_res = client.get("/api/v1/auth/me", headers=headers)
    assert after_res.status_code == 401


def test_rbac_enforcement_and_gstr3b_http_filing_flow(rbac_org):
    client = TestClient(app)
    org_id = rbac_org["org_id"]
    reg_id = rbac_org["reg_mh"]

    viewer_hdr = {"Authorization": f"Bearer {create_access_token(subject=rbac_org['users']['viewer'], org_id=org_id)}"}
    acct_hdr = {"Authorization": f"Bearer {create_access_token(subject=rbac_org['users']['accountant'], org_id=org_id)}"}
    admin_hdr = {"Authorization": f"Bearer {create_access_token(subject=rbac_org['users']['admin'], org_id=org_id)}"}

    # 1. Viewer cannot mutate purchase docs, prepare GSTR-3B, upload statements, or update profiles
    assert client.post(
        "/api/v1/gstr3b/prepare",
        json={"registration_id": reg_id, "tax_period": "2026-11"},
        headers=viewer_hdr,
    ).status_code == 403
    assert client.post(
        "/api/v1/credit-builder/statements/upload",
        data={"csv_text": "Date,Description,Type,Amount\n2026-11-01,Test,credit,100\n"},
        headers=viewer_hdr,
    ).status_code == 403
    assert client.post(
        "/api/v1/scheme-navigator/profile",
        json={"business_type": "Proprietorship", "industry_sector": "Retail"},
        headers=viewer_hdr,
    ).status_code == 403

    # 2. Accountant prepares and submits GSTR-3B return via HTTP
    prep = client.post(
        "/api/v1/gstr3b/prepare",
        json={"registration_id": reg_id, "tax_period": "2026-11"},
        headers=acct_hdr,
    )
    assert prep.status_code == 200
    filing_id = prep.json()["id"]

    status_get = client.get(
        f"/api/v1/gstr3b/status?registration_id={reg_id}&tax_period=2026-11",
        headers=viewer_hdr,
    )
    assert status_get.status_code == 200
    assert status_get.json()["filing_status"] == "DRAFT"

    sub = client.post(
        f"/api/v1/gstr3b/{filing_id}/submit-review",
        json={"remarks": "Accountant submission"},
        headers=acct_hdr,
    )
    assert sub.status_code == 200

    # Admin checker approves
    app_res = client.post(
        f"/api/v1/gstr3b/{filing_id}/review-decision",
        json={"decision": "APPROVED", "remarks": "Checked and verified"},
        headers=admin_hdr,
    )
    assert app_res.status_code == 200

    # Accountant CANNOT invoke /file (403); Admin CAN file (200)
    assert client.post(f"/api/v1/gstr3b/{filing_id}/file", headers=acct_hdr).status_code == 403
    filed = client.post(f"/api/v1/gstr3b/{filing_id}/file", headers=admin_hdr)
    assert filed.status_code == 200
    filed_json = filed.json()
    assert filed_json["status"] == "FILED"
    assert filed_json["filing_mode"] == "SANDBOX"
    assert filed_json["is_live_portal_filing"] is False
    assert filed_json["arn"].startswith("AA")


def test_multi_gstin_isolation_and_audit_export(rbac_org):
    from decimal import Decimal
    from tests.test_milestone1_security_closure import seed_invoice

    client = TestClient(app)
    org_id = rbac_org["org_id"]
    owner_hdr = {"Authorization": f"Bearer {create_access_token(subject=rbac_org['users']['owner'], org_id=org_id)}"}

    # Seed approved outward invoice ONLY on MH GSTIN (reg_mh) respecting invoice workflow triggers
    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            seed_invoice(
                cur=cur,
                org_id=org_id,
                profile_id=rbac_org["reg_mh"],
                user_id=rbac_org["users"]["owner"],
                inv_num="INV-MH-ONLY",
                inv_date="2026-11-10",
                cp_name="MH Customer",
                cp_gstin=None,
                taxable=Decimal("10000.00"),
                cgst=Decimal("900.00"),
                sgst=Decimal("900.00"),
                igst=Decimal("0.00"),
                total=Decimal("11800.00"),
                status="approved",
                supply_category="B2C_SMALL",
            )

    mh_prep = client.post(
        "/api/v1/gstr3b/prepare",
        json={"registration_id": rbac_org["reg_mh"], "tax_period": "2026-11"},
        headers=owner_hdr,
    ).json()
    ka_prep = client.post(
        "/api/v1/gstr3b/prepare",
        json={"registration_id": rbac_org["reg_ka"], "tax_period": "2026-11"},
        headers=owner_hdr,
    ).json()

    assert float(mh_prep["table_3_1"]["a_taxable_supplies"]["taxable_value"]) == 10000.0
    assert float(ka_prep["table_3_1"]["a_taxable_supplies"]["taxable_value"]) == 0.0

    # Upload minimal GSTR-2B JSON for MH and verify /export/audit-report endpoint returns 200
    gstr2b_payload = json.dumps({
        "data": {
            "rtnprd": "112026",
            "gstin": rbac_org["gstin_mh"],
            "docdata": {"b2b": []},
        }
    }).encode("utf-8")
    up_res = client.post(
        "/api/v1/reconciliation/gstr2b/upload",
        data={"registration_id": rbac_org["reg_mh"], "return_period": "2026-11"},
        files={"file": ("gstr2b.json", io.BytesIO(gstr2b_payload), "application/json")},
        headers=owner_hdr,
    )
    assert up_res.status_code == 200
    snap_id = up_res.json()["snapshot_id"]

    exp_res = client.get(
        f"/api/v1/reconciliation/export/audit-report?registration_id={rbac_org['reg_mh']}&return_period=2026-11&snapshot_id={snap_id}",
        headers=owner_hdr,
    )
    assert exp_res.status_code == 200
    exp_body = exp_res.json()
    assert exp_body["report_type"] == "ITC_RECONCILIATION_AUDIT_REPORT"
    assert exp_body["registration_id"] == rbac_org["reg_mh"]


def test_s3_upload_to_worker_ocr_end_to_end(rbac_org, monkeypatch):
    from tests.test_production_hardening_track_a import _InMemoryS3Client

    s3_mock = _InMemoryS3Client()
    fake_boto3 = types.ModuleType("boto3")
    fake_boto3.client = lambda service, region_name=None: s3_mock
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "s3")
    monkeypatch.setattr(settings, "AWS_S3_BUCKET_NAME", "myca-e2e-bucket")
    monkeypatch.setattr(settings, "AWS_S3_KMS_KEY_ID", None)
    monkeypatch.delenv("STORAGE_BACKEND", raising=False)

    org_id = rbac_org["org_id"]
    owner_id = rbac_org["users"]["owner"]
    pdf_bytes = b"%PDF-1.4\n%S3 end-to-end OCR upload payload"

    with SessionLocal() as session:
        set_tenant_context(session, org_id=org_id, user_id=owner_id)
        out = upload_and_enqueue_invoice(
            session=session,
            org_id=org_id,
            user_id=owner_id,
            profile_id=rbac_org["reg_mh"],
            file_stream=io.BytesIO(pdf_bytes),
            file_name="s3_invoice.pdf",
        )
        session.commit()

    invoice_id = str(out.invoice_id)

    with psycopg.connect(DB_WORKER, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT o_task_id, o_lease_token FROM public.claim_task(%s, 'worker-s3');", (org_id,))
            task_id, lease_token = cur.fetchone()
            assert task_id is not None
            cur.execute("SELECT set_config('app.current_org', %s, false);", (org_id,))
            cur.execute("SELECT payload FROM public.worker_tasks WHERE id = %s;", (task_id,))
            payload = cur.fetchone()[0]
            document_id = payload["document_id"]
            s3_uri = payload["file_path"]
            assert s3_uri.startswith(f"s3://myca-e2e-bucket/{org_id}/")

        res = process_invoice_ocr_task(
            db_conn=conn,
            task_id=str(task_id),
            invoice_id=invoice_id,
            document_id=document_id,
            org_id=org_id,
            file_path=s3_uri,
            mime_type="application/pdf",
            lease_token=str(lease_token),
        )
        assert res["status"] in ("pending_review", "validation_failed")


def test_worker_runner_and_heartbeat_lifecycle(monkeypatch):
    with pytest.raises(RuntimeError, match="WORKER_CONFIGURATION_ERROR"):
        runner_no_org = WorkerRunner(org_id="", instance_label="test-w")
        runner_no_org.run_once(MagicMock())

    runner = WorkerRunner(org_id=str(uuid.uuid4()), instance_label="test-w")
    mock_conn = MagicMock()

    # Case 1: Empty queue -> run_once returns False
    monkeypatch.setattr(runner, "claim", lambda conn: (None, None))
    assert runner.run_once(mock_conn) is False

    # Case 2: Task fails during processing -> runner records failure via fail()
    monkeypatch.setattr(runner, "claim", lambda conn: ("task-1", "lease-1"))
    recorded_failures = []
    monkeypatch.setattr(runner, "process_task", lambda tid, hb: (_ for _ in ()).throw(ValueError("boom")))
    monkeypatch.setattr(runner, "fail", lambda conn, tid, tok, msg: recorded_failures.append((tid, msg)))

    assert runner.run_once(mock_conn) is True
    assert recorded_failures == [("task-1", "boom")]

    # Case 3: HeartbeatManager stop() terminates cleanly
    hb = HeartbeatManager("postgresql://u:p@localhost:5432/myca", "task-1", "lease-1", interval_sec=10.0)
    hb.start()
    hb.stop()
    assert hb.stop_event.is_set() is True
