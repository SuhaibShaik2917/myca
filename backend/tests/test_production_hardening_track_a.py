"""Unit and API tests for Track A production hardening controls and complete S3 storage lifecycle."""

import io
import os
import sys
import types
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.modules.gstr3b.gsp_client import (
    GSPIntegrationUnavailableError,
    dispatch_gstr3b_filing,
)
from app.modules.gstr3b.gsp_sandbox import generate_cin
from app.modules.invoice_brain.ocr import extract_invoice_mock
from app.modules.invoice_brain.service import DuplicateDocumentError, upload_and_enqueue_invoice
from app.modules.invoice_brain.storage import (
    StorageError,
    compute_stream_sha256_and_save,
    delete_stored_document,
    read_document_bytes,
    reconcile_storage_orphans,
    stat_document_size,
)


class _InMemoryS3Client:
    """Realistic stateful mock of boto3 S3 client verifying bucket/key/SSE/cleanup behavior."""

    def __init__(self):
        self.objects = {}
        self.fail_on_put = False

    def put_object(self, **kwargs):
        if self.fail_on_put:
            raise RuntimeError("Simulated S3 PutObject outage")
        bucket = kwargs["Bucket"]
        key = kwargs["Key"]
        body = kwargs["Body"].read()
        self.objects[(bucket, key)] = {
            "BodyBytes": body,
            "ContentType": kwargs.get("ContentType"),
            "ServerSideEncryption": kwargs.get("ServerSideEncryption"),
            "SSEKMSKeyId": kwargs.get("SSEKMSKeyId"),
            "LastModified": datetime.now(timezone.utc),
            "Size": len(body),
        }

    def get_object(self, Bucket, Key):
        item = self.objects[(Bucket, Key)]
        return {"Body": io.BytesIO(item["BodyBytes"])}

    def head_object(self, Bucket, Key):
        item = self.objects[(Bucket, Key)]
        return {"ContentLength": item["Size"]}

    def delete_object(self, Bucket, Key):
        self.objects.pop((Bucket, Key), None)

    def get_paginator(self, op_name):
        assert op_name == "list_objects_v2"
        parent = self

        class _Paginator:
            def paginate(self, Bucket):
                contents = [
                    {"Key": k, "Size": v["Size"], "LastModified": v["LastModified"]}
                    for (b, k), v in parent.objects.items()
                    if b == Bucket
                ]
                return [{"Contents": contents}]

        return _Paginator()


def test_gsp_live_mode_fails_closed_without_credentials(monkeypatch):
    monkeypatch.setattr(settings, "GSP_MODE", "live")
    monkeypatch.setattr(settings, "GSTN_GSP_BASE_URL", None)
    monkeypatch.setattr(settings, "GSTN_GSP_CLIENT_ID", None)
    monkeypatch.setattr(settings, "GSTN_GSP_CLIENT_SECRET", None)
    monkeypatch.delenv("GSP_MODE", raising=False)
    monkeypatch.delenv("GSTN_GSP_BASE_URL", raising=False)
    monkeypatch.delenv("GSTN_GSP_CLIENT_ID", raising=False)
    monkeypatch.delenv("GSTN_GSP_CLIENT_SECRET", raising=False)

    with pytest.raises(GSPIntegrationUnavailableError, match="GSP_LIVE_INTEGRATION_UNAVAILABLE"):
        dispatch_gstr3b_filing(
            gstin="27AABCB1234M1ZT",
            tax_period="2026-11",
            payload_dict={"sample": 1},
            checker_approved=True,
        )


def test_gsp_live_mode_rejects_placeholder_or_non_https_and_never_falls_back(monkeypatch):
    monkeypatch.setattr(settings, "GSP_MODE", "live")
    monkeypatch.setattr(settings, "GSTN_GSP_BASE_URL", "https://gsp.authorized-partner.in/v1")
    monkeypatch.setattr(settings, "GSTN_GSP_CLIENT_ID", "real-client-id")
    monkeypatch.setattr(settings, "GSTN_GSP_CLIENT_SECRET", "real-client-secret")

    with pytest.raises(GSPIntegrationUnavailableError, match="GSP_LIVE_TRANSPORT_NOT_IMPLEMENTED"):
        dispatch_gstr3b_filing(
            gstin="27AABCB1234M1ZT",
            tax_period="2026-11",
            payload_dict={"sample": 1},
            checker_approved=True,
        )

    monkeypatch.setattr(settings, "GSTN_GSP_CLIENT_ID", "REPLACE_WITH_AUTHORIZED_GSP_CLIENT_ID")
    with pytest.raises(GSPIntegrationUnavailableError, match="Placeholder"):
        dispatch_gstr3b_filing(
            gstin="27AABCB1234M1ZT",
            tax_period="2026-11",
            payload_dict={"sample": 1},
            checker_approved=True,
        )


def test_generate_cin_is_deterministic_across_calls():
    cin1 = generate_cin("SBIN", 12345)
    cin2 = generate_cin("SBIN", 12345)
    assert cin1 == cin2
    assert len(cin1) == 26
    assert "SBIN" in cin1


def test_s3_storage_backend_fails_closed_without_bucket(monkeypatch):
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "s3")
    monkeypatch.setattr(settings, "AWS_S3_BUCKET_NAME", None)
    monkeypatch.delenv("STORAGE_BACKEND", raising=False)
    monkeypatch.delenv("AWS_S3_BUCKET_NAME", raising=False)

    pdf_stream = io.BytesIO(b"%PDF-1.4 sample")
    with pytest.raises(StorageError, match="S3_STORAGE_UNAVAILABLE"):
        compute_stream_sha256_and_save(pdf_stream, "org-1", "doc-1")


def test_complete_s3_storage_lifecycle_with_mock_boto3(monkeypatch, tmp_path):
    s3_mock = _InMemoryS3Client()
    fake_boto3 = types.ModuleType("boto3")
    fake_boto3.client = lambda service, region_name=None: s3_mock
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)

    monkeypatch.setattr(settings, "STORAGE_BACKEND", "s3")
    monkeypatch.setattr(settings, "AWS_S3_BUCKET_NAME", "myca-prod-docs")
    monkeypatch.setattr(settings, "AWS_S3_KMS_KEY_ID", "arn:aws:kms:ap-south-1:111122223333:key/abc")
    monkeypatch.setattr(settings, "STORAGE_LOCAL_ROOT", str(tmp_path))
    monkeypatch.delenv("STORAGE_BACKEND", raising=False)

    org_a = str(uuid.uuid4())
    org_b = str(uuid.uuid4())
    doc_id = str(uuid.uuid4())
    payload = b"%PDF-1.4\n%MyCA S3 lifecycle invoice test payload"

    # 1. Upload with KMS encryption
    uri, size, sha, mime = compute_stream_sha256_and_save(io.BytesIO(payload), org_a, doc_id)
    assert uri.startswith(f"s3://myca-prod-docs/{org_a}/")
    assert size == len(payload)
    assert mime == "application/pdf"
    assert list(tmp_path.rglob("*.bin")) == []

    bucket, key = "myca-prod-docs", uri.split("s3://myca-prod-docs/")[1]
    stored = s3_mock.objects[(bucket, key)]
    assert stored["ServerSideEncryption"] == "aws:kms"
    assert stored["SSEKMSKeyId"] == "arn:aws:kms:ap-south-1:111122223333:key/abc"

    # 2. Retrieval & OCR stat work via S3 URI (without calling os.stat/open on s3://)
    assert read_document_bytes(uri, expected_org_id=org_a) == payload
    assert stat_document_size(uri, expected_org_id=org_a) == len(payload)
    extracted = extract_invoice_mock(uri, mime)
    assert extracted.invoice_number.startswith("INV-2026-")

    # 3. Tenant isolation enforcement blocks cross-org read/delete
    with pytest.raises(StorageError, match="TENANT_STORAGE_ISOLATION_VIOLATION"):
        read_document_bytes(uri, expected_org_id=org_b)
    with pytest.raises(StorageError, match="TENANT_STORAGE_ISOLATION_VIOLATION"):
        delete_stored_document(uri, expected_org_id=org_b)

    # 4. Partial-upload failure cleans up temp file and leaves zero orphaned S3 objects
    s3_mock.fail_on_put = True
    with pytest.raises(RuntimeError, match="Simulated S3 PutObject outage"):
        compute_stream_sha256_and_save(io.BytesIO(payload), org_a, str(uuid.uuid4()))
    assert list(tmp_path.rglob("tmp_*.bin")) == []
    s3_mock.fail_on_put = False

    # 5. Atomic DB failure cleanup in upload_and_enqueue_invoice deletes S3 object
    class _DupFailSession:
        def execute(self, stmt, params=None):
            class _R:
                def fetchone(self_inner):
                    return ("existing-doc-id",)
            return _R()

    with pytest.raises(DuplicateDocumentError):
        upload_and_enqueue_invoice(
            session=_DupFailSession(),
            org_id=org_a,
            user_id=str(uuid.uuid4()),
            profile_id=str(uuid.uuid4()),
            file_stream=io.BytesIO(payload),
            file_name="dup.pdf",
        )
    assert len(s3_mock.objects) == 1  # duplicate upload object was deleted from S3!

    # 6. Orphan reconciliation preserves active DB URI & fresh in-flight upload, deletes stale S3 orphan
    orphan_stale_key = f"{org_a}/ff/stale.bin"
    orphan_fresh_key = f"{org_a}/ff/inflight.bin"
    s3_mock.objects[(bucket, orphan_stale_key)] = {
        "BodyBytes": b"stale",
        "LastModified": datetime.now(timezone.utc) - timedelta(hours=2),
        "Size": 5,
    }
    s3_mock.objects[(bucket, orphan_fresh_key)] = {
        "BodyBytes": b"fresh",
        "LastModified": datetime.now(timezone.utc),
        "Size": 5,
    }

    class _ActiveDocsSession:
        def execute(self, stmt):
            class _R:
                def fetchall(self_inner):
                    return [(uri,)]
            return _R()

    rec = reconcile_storage_orphans(_ActiveDocsSession(), grace_period_seconds=1800)
    assert rec["scanned_files"] == 3
    assert rec["active_db_files"] == 1
    assert rec["in_flight_files"] == 1
    assert rec["orphaned_files_deleted"] == 1
    assert rec["deleted_file_paths"] == [f"s3://{bucket}/{orphan_stale_key}"]
    assert (bucket, key) in s3_mock.objects
    assert (bucket, orphan_fresh_key) in s3_mock.objects
    assert (bucket, orphan_stale_key) not in s3_mock.objects

    # 7. Deletion removes S3 object cleanly
    assert delete_stored_document(uri, expected_org_id=org_a) is True
    assert (bucket, key) not in s3_mock.objects


def test_ready_endpoint_fails_closed_when_db_unavailable():
    client = TestClient(app)
    resp_health = client.get("/health")
    assert resp_health.status_code == 200
    assert "X-Request-ID" in resp_health.headers

    class _BrokenSession:
        def execute(self, *args, **kwargs):
            raise RuntimeError("DB offline")

        def close(self):
            pass

    with patch("app.database.SessionLocal", return_value=_BrokenSession()):
        resp_ready = client.get("/ready")
    assert resp_ready.status_code == 503
    assert "DEPENDENCY_UNREADY" in resp_ready.json()["detail"]
