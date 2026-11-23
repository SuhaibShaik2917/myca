"""Unit tests for signed demo bearer-token authentication (no database required)."""

import uuid

import pytest
from fastapi import HTTPException

from app.config import settings
from app.core import dependencies
from app.core.security import create_access_token, decode_access_token


USER_ID = str(uuid.uuid4())
ORG_ID = str(uuid.uuid4())


def test_access_token_round_trip_preserves_identity_claims():
    token = create_access_token(subject=USER_ID, org_id=ORG_ID)
    claims = decode_access_token(token)
    assert claims["sub"] == USER_ID
    assert claims["org_id"] == ORG_ID


@pytest.mark.parametrize("token", ["", "not-a-jwt", "a.b.c"])
def test_invalid_tokens_are_rejected(token):
    with pytest.raises(ValueError):
        decode_access_token(token)


def test_bearer_token_identity_overrides_spoofed_headers(monkeypatch):
    captured = {}
    def mock_verify(db, user_id, org_id):
        captured["user_id"] = user_id
        captured["org_id"] = org_id
        return True

    monkeypatch.setattr(dependencies, "verify_and_resolve_tenant_membership", mock_verify)
    monkeypatch.setattr(dependencies, "set_tenant_context", lambda db, org_id, user_id: None)

    token = create_access_token(subject=USER_ID, org_id=ORG_ID)
    spoofed_user = str(uuid.uuid4())
    spoofed_org = str(uuid.uuid4())
    generator = dependencies.get_tenant_db(
        authorization=f"Bearer {token}",
        x_user_id=spoofed_user,
        x_org_id=spoofed_org,
    )
    next(generator)
    assert captured["user_id"] == USER_ID
    assert captured["org_id"] == ORG_ID
    assert captured["user_id"] != spoofed_user
    assert captured["org_id"] != spoofed_org


def test_invalid_bearer_token_returns_401():
    generator = dependencies.get_tenant_db(
        authorization="Bearer invalid.token.value",
        x_user_id=USER_ID,
        x_org_id=ORG_ID,
    )
    with pytest.raises(HTTPException) as excinfo:
        next(generator)
    assert excinfo.value.status_code == 401


def test_production_requires_bearer_token(monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    generator = dependencies.get_tenant_db(
        authorization=None,
        x_user_id=USER_ID,
        x_org_id=ORG_ID,
    )
    with pytest.raises(HTTPException) as excinfo:
        next(generator)
    assert excinfo.value.status_code == 401


def test_demo_token_strictly_prohibited_in_production(monkeypatch):
    """
    CRITICAL SECURITY CHECK:
    Proves that /auth/demo-token cannot issue tokens when ENVIRONMENT is production,
    even if an attacker supplies valid UUIDs of existing users/organizations.
    """
    from fastapi.testclient import TestClient
    from app.main import app
    import app.modules.auth.api as auth_api

    client = TestClient(app)
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")

    response = client.post(
        "/api/v1/auth/demo-token",
        json={"user_id": USER_ID, "org_id": ORG_ID},
    )
    assert response.status_code == 403
    assert "DEMO_TOKEN_PROHIBITED_IN_PRODUCTION" in response.json()["detail"]


def test_demo_token_allowed_in_development_and_test(monkeypatch):
    """Verifies that demo-token remains functional in test/development modes."""
    from fastapi.testclient import TestClient
    from app.main import app
    import app.modules.auth.api as auth_api

    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setattr(settings, "ENVIRONMENT", "test")
    monkeypatch.setattr(auth_api, "verify_and_resolve_tenant_membership", lambda db, u, o: True)

    client = TestClient(app)
    response = client.post(
        "/api/v1/auth/demo-token",
        json={"user_id": USER_ID, "org_id": ORG_ID},
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["user_id"] == USER_ID
    assert data["org_id"] == ORG_ID

    claims = decode_access_token(data["access_token"])
    assert claims["sub"] == USER_ID
    assert claims["org_id"] == ORG_ID


def test_idp_token_exchange_fails_closed_in_production_without_trust_anchor(monkeypatch):
    """Proves that production IdP exchange fails closed (HTTP 503) when IdP trust anchor is unconfigured."""
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.delenv("MYCA_IDP_ISSUER", raising=False)
    monkeypatch.delenv("MYCA_IDP_PUBLIC_KEY", raising=False)

    response = client.post(
        "/api/v1/auth/idp-exchange",
        json={
            "idp_provider": "google",
            "idp_token": "some-assertion",
            "org_id": ORG_ID,
            "user_email": "auditor@firm.test",
        },
    )
    assert response.status_code == 503
    assert "IDP_INTEGRATION_UNAVAILABLE" in response.json()["detail"]


def test_idp_token_exchange_in_test_environment(monkeypatch):
    """Proves genuine IdP token exchange flow in non-production environments."""
    from fastapi.testclient import TestClient
    from app.main import app
    import app.modules.auth.api as auth_api
    from unittest.mock import MagicMock

    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setattr(settings, "ENVIRONMENT", "test")

    mock_db = MagicMock()
    # Mock user found in global_users
    mock_db.execute.return_value.fetchone.return_value = (USER_ID, "auditor@firm.test", True)
    monkeypatch.setattr(auth_api, "SessionLocal", lambda: mock_db)
    monkeypatch.setattr(auth_api, "verify_and_resolve_tenant_membership", lambda db, u, o: True)

    client = TestClient(app)
    response = client.post(
        "/api/v1/auth/idp-exchange",
        json={
            "idp_provider": "google",
            "idp_token": "test-idp-assertion",
            "org_id": ORG_ID,
            "user_email": "auditor@firm.test",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["user_id"] == USER_ID
    assert data["org_id"] == ORG_ID
    claims = decode_access_token(data["access_token"])
    assert claims["sub"] == USER_ID


def test_idp_token_exchange_rejects_suspended_user(monkeypatch):
    """Proves IdP exchange rejects inactive/suspended users."""
    from fastapi.testclient import TestClient
    from app.main import app
    import app.modules.auth.api as auth_api
    from unittest.mock import MagicMock

    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setattr(settings, "ENVIRONMENT", "test")

    mock_db = MagicMock()
    # is_active = False
    mock_db.execute.return_value.fetchone.return_value = (USER_ID, "suspended@firm.test", False)
    monkeypatch.setattr(auth_api, "SessionLocal", lambda: mock_db)

    client = TestClient(app)
    response = client.post(
        "/api/v1/auth/idp-exchange",
        json={
            "idp_provider": "google",
            "idp_token": "test-idp-assertion",
            "org_id": ORG_ID,
            "user_email": "suspended@firm.test",
        },
    )
    assert response.status_code == 403
    assert "SUSPENDED_USER" in response.json()["detail"]
