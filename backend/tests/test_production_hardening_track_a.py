"""Unit and API tests for Track A production hardening controls."""

import io
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
from app.modules.invoice_brain.storage import StorageError, compute_stream_sha256_and_save


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
