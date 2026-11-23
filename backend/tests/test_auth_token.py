"""Unit and integration tests for authentication, environment configuration, demo-token guards, and IdP verification."""

import os
import time
import uuid
from unittest.mock import MagicMock

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from fastapi.testclient import TestClient
from jose import jwt

from app.config import Settings, settings
from app.core import dependencies
from app.core.security import create_access_token, decode_access_token
import app.modules.auth.api as auth_api
from app.modules.auth.idp_verifier import validate_idp_token


USER_ID = str(uuid.uuid4())
ORG_ID = str(uuid.uuid4())


def generate_test_rsa_keypair():
    """Generates an ephemeral RSA 2048 keypair in PEM format for asymmetric IdP testing."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    pub_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")
    return priv_pem, pub_pem


def create_signed_test_jwt(
    payload_overrides=None,
    key=None,
    algorithm="HS256",
    headers=None,
):
    now = int(time.time())
    claims = {
        "sub": "user-sub-12345",
        "email": "auditor@firm.test",
        "email_verified": True,
        "iss": "https://accounts.google.com",
        "aud": "myca-test-aud",
        "iat": now,
        "exp": now + 3600,
    }
    if payload_overrides:
        claims.update(payload_overrides)
    sign_key = key or settings.SECRET_KEY
    return jwt.encode(claims, sign_key, algorithm=algorithm, headers=headers)


# =========================================================================
# 1. Access Token Identity & Dependency Tests
# =========================================================================

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


# =========================================================================
# 2. Environment Configuration & Startup Enforcement Tests
# =========================================================================

def test_settings_requires_explicit_valid_environment(monkeypatch):
    """Proves that Settings refuses to initialize when ENVIRONMENT is unset or invalid."""
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    with pytest.raises(ValueError) as excinfo:
        Settings(ENVIRONMENT=None, _env_file=None)
    assert "ENVIRONMENT is not configured" in str(excinfo.value)

    with pytest.raises(ValueError) as excinfo2:
        Settings(ENVIRONMENT="invalid_env", _env_file=None)
    assert "Invalid ENVIRONMENT 'invalid_env'" in str(excinfo2.value)


def test_settings_prohibits_allow_demo_tokens_in_production(monkeypatch):
    """Proves that ALLOW_DEMO_TOKENS=True is strictly rejected in production and staging."""
    monkeypatch.setenv("ENVIRONMENT", "production")
    with pytest.raises(ValueError) as excinfo:
        Settings(ENVIRONMENT="production", ALLOW_DEMO_TOKENS=True, _env_file=None)
    assert "ALLOW_DEMO_TOKENS cannot be enabled in production" in str(excinfo.value)


def test_settings_prohibits_symmetric_algorithms_in_production(monkeypatch):
    """Proves that symmetric algorithms for IdP verification are strictly rejected in production."""
    monkeypatch.setenv("ENVIRONMENT", "production")
    with pytest.raises(ValueError) as excinfo:
        Settings(
            ENVIRONMENT="production",
            MYCA_IDP_ALLOWED_ALGORITHMS=["RS256", "HS256"],
            _env_file=None,
        )
    assert "Symmetric algorithms ['HS256'] are strictly prohibited" in str(excinfo.value)


# =========================================================================
# 3. Demo Token Lockdown & Environment Guard Tests
# =========================================================================

@pytest.mark.parametrize("disallowed_env", ["production", "staging", "unknown", ""])
def test_demo_token_prohibited_in_non_local_environments(monkeypatch, disallowed_env):
    """Proves that /auth/demo-token is unconditionally rejected in production, staging, or unknown envs."""
    from app.main import app
    client = TestClient(app)
    monkeypatch.setenv("ENVIRONMENT", disallowed_env)
    monkeypatch.setattr(settings, "ENVIRONMENT", disallowed_env)

    response = client.post(
        "/api/v1/auth/demo-token",
        json={"user_id": USER_ID, "org_id": ORG_ID},
    )
    assert response.status_code == 403
    assert "DEMO_TOKEN_PROHIBITED" in response.json()["detail"]


def test_demo_token_disabled_by_default_in_local_environments(monkeypatch):
    """Proves that even in dev/test, demo-token is disabled by default if ALLOW_DEMO_TOKENS is False."""
    from app.main import app
    client = TestClient(app)
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setattr(settings, "ENVIRONMENT", "test")
    monkeypatch.setenv("ALLOW_DEMO_TOKENS", "false")
    monkeypatch.setattr(settings, "ALLOW_DEMO_TOKENS", False)

    response = client.post(
        "/api/v1/auth/demo-token",
        json={"user_id": USER_ID, "org_id": ORG_ID},
    )
    assert response.status_code == 403
    assert "DEMO_TOKEN_DISABLED_BY_DEFAULT" in response.json()["detail"]


def test_demo_token_allowed_when_explicitly_configured_in_test(monkeypatch):
    """Verifies that demo-token succeeds in test mode only when ALLOW_DEMO_TOKENS=True."""
    from app.main import app
    client = TestClient(app)
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setattr(settings, "ENVIRONMENT", "test")
    monkeypatch.setenv("ALLOW_DEMO_TOKENS", "true")
    monkeypatch.setattr(settings, "ALLOW_DEMO_TOKENS", True)
    monkeypatch.setattr(auth_api, "verify_and_resolve_tenant_membership", lambda db, u, o: True)

    response = client.post(
        "/api/v1/auth/demo-token",
        json={"user_id": USER_ID, "org_id": ORG_ID},
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["user_id"] == USER_ID
    assert data["org_id"] == ORG_ID


# =========================================================================
# 4. Strict IdP Cryptographic & Claims Verification Tests
# =========================================================================

def test_idp_token_exchange_fails_closed_in_production_without_trust_anchors(monkeypatch):
    """Proves that production IdP exchange fails closed (HTTP 503) when trust anchors are missing."""
    from app.main import app
    client = TestClient(app)
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.delenv("MYCA_IDP_ISSUER", raising=False)
    monkeypatch.delenv("MYCA_IDP_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("MYCA_IDP_AUDIENCE", raising=False)

    token = create_signed_test_jwt()
    response = client.post(
        "/api/v1/auth/idp-exchange",
        json={
            "idp_provider": "google",
            "idp_token": token,
            "org_id": ORG_ID,
        },
    )
    assert response.status_code == 503
    assert "IDP_INTEGRATION_UNAVAILABLE" in response.json()["detail"]


def test_idp_rejects_unauthorized_provider():
    """Proves that unauthorized identity providers are rejected with HTTP 400."""
    with pytest.raises(HTTPException) as excinfo:
        validate_idp_token("untrusted_idp", "a.b.c", env="test")
    assert excinfo.value.status_code == 400
    assert "UNSUPPORTED_IDP_PROVIDER" in excinfo.value.detail


def test_idp_rejects_none_algorithm():
    """Proves that tokens with 'none' algorithm are strictly rejected."""
    import base64
    import json
    header = base64.urlsafe_b64encode(json.dumps({"alg": "none", "typ": "JWT"}).encode()).decode().rstrip("=")
    payload = base64.urlsafe_b64encode(json.dumps({"sub": "user", "email": "a@b.com", "iat": int(time.time()), "exp": int(time.time()) + 1000}).encode()).decode().rstrip("=")
    token_none = f"{header}.{payload}."
    with pytest.raises(HTTPException) as excinfo:
        validate_idp_token("google", token_none, env="test")
    assert excinfo.value.status_code == 401
    assert "ALGORITHM_NOT_PERMITTED" in excinfo.value.detail


def test_idp_rejects_algorithm_confusion_in_production(monkeypatch):
    """Proves that symmetric HS256 tokens in production are rejected under algorithm confusion defenses."""
    _, pub_pem = generate_test_rsa_keypair()
    monkeypatch.setattr(settings, "MYCA_IDP_ISSUER", "https://accounts.google.com")
    monkeypatch.setattr(settings, "MYCA_IDP_AUDIENCE", "myca-app")
    monkeypatch.setattr(settings, "MYCA_IDP_PUBLIC_KEY", pub_pem)

    token = create_signed_test_jwt(algorithm="HS256")
    with pytest.raises(HTTPException) as excinfo:
        validate_idp_token("google", token, env="production")
    assert excinfo.value.status_code == 401
    assert "ALGORITHM_CONFUSION_REJECTED" in excinfo.value.detail


def test_idp_rejects_algorithm_confusion_with_public_key_anchor(monkeypatch):
    """Proves that if an asymmetric public key is configured, HS256 is strictly rejected even in test."""
    _, pub_pem = generate_test_rsa_keypair()
    monkeypatch.setattr(settings, "MYCA_IDP_PUBLIC_KEY", pub_pem)
    token = create_signed_test_jwt(algorithm="HS256")
    with pytest.raises(HTTPException) as excinfo:
        validate_idp_token("google", token, env="test")
    assert excinfo.value.status_code == 401
    assert "ALGORITHM_CONFUSION_REJECTED" in excinfo.value.detail


def test_idp_rejects_wrong_issuer(monkeypatch):
    """Proves that token with mismatched issuer is rejected."""
    monkeypatch.setattr(settings, "MYCA_IDP_ISSUER", "https://accounts.google.com")
    token = create_signed_test_jwt(payload_overrides={"iss": "https://evil-attacker.com"})
    with pytest.raises(HTTPException) as excinfo:
        validate_idp_token("google", token, env="test")
    assert excinfo.value.status_code == 401
    assert "INVALID_IDP_ISSUER" in excinfo.value.detail


def test_idp_rejects_wrong_audience(monkeypatch):
    """Proves that token with mismatched audience is rejected."""
    monkeypatch.setattr(settings, "MYCA_IDP_AUDIENCE", "expected-myca-aud")
    token = create_signed_test_jwt(payload_overrides={"aud": "wrong-audience"})
    with pytest.raises(HTTPException) as excinfo:
        validate_idp_token("google", token, env="test")
    assert excinfo.value.status_code == 401
    assert "INVALID_IDP_AUDIENCE" in excinfo.value.detail


def test_idp_rejects_expired_token():
    """Proves that expired IdP tokens are strictly rejected."""
    past = int(time.time()) - 3600
    token = create_signed_test_jwt(payload_overrides={"iat": past - 100, "exp": past})
    with pytest.raises(HTTPException) as excinfo:
        validate_idp_token("google", token, env="test")
    assert excinfo.value.status_code == 401
    assert "EXPIRED_IDP_TOKEN" in excinfo.value.detail


def test_idp_rejects_unverified_email():
    """Proves that tokens with email_verified=False are rejected."""
    token = create_signed_test_jwt(payload_overrides={"email_verified": False})
    with pytest.raises(HTTPException) as excinfo:
        validate_idp_token("google", token, env="test")
    assert excinfo.value.status_code == 401
    assert "UNVERIFIED_EMAIL" in excinfo.value.detail


def test_idp_rejects_untrusted_key_id(monkeypatch):
    """Proves that token with non-matching key ID is rejected when key ID is configured."""
    monkeypatch.setattr(settings, "MYCA_IDP_KEY_ID", "trusted-key-v1")
    token = create_signed_test_jwt(headers={"kid": "untrusted-key-v99"})
    with pytest.raises(HTTPException) as excinfo:
        validate_idp_token("google", token, env="test")
    assert excinfo.value.status_code == 401
    assert "UNTRUSTED_KEY_ID" in excinfo.value.detail


def test_idp_token_exchange_with_authentic_asymmetric_rsa_in_production(monkeypatch):
    """
    AUTHENTIC PRODUCTION ASYMMETRIC IDP VERIFICATION TEST:
    Demonstrates end-to-end token exchange in production mode using authentic RS256
    cryptographic keypair, valid issuer, audience, and active user lookup.
    """
    from app.main import app
    priv_pem, pub_pem = generate_test_rsa_keypair()

    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.setenv("MYCA_IDP_ISSUER", "https://accounts.google.com")
    monkeypatch.setattr(settings, "MYCA_IDP_ISSUER", "https://accounts.google.com")
    monkeypatch.setenv("MYCA_IDP_AUDIENCE", "myca-prod-client-id")
    monkeypatch.setattr(settings, "MYCA_IDP_AUDIENCE", "myca-prod-client-id")
    monkeypatch.setenv("MYCA_IDP_PUBLIC_KEY", pub_pem)
    monkeypatch.setattr(settings, "MYCA_IDP_PUBLIC_KEY", pub_pem)

    token = create_signed_test_jwt(
        payload_overrides={
            "iss": "https://accounts.google.com",
            "aud": "myca-prod-client-id",
            "email": "prod.auditor@firm.test",
            "email_verified": True,
        },
        key=priv_pem,
        algorithm="RS256",
    )

    mock_db = MagicMock()
    mock_db.execute.return_value.fetchone.return_value = (USER_ID, "prod.auditor@firm.test", True)
    monkeypatch.setattr(auth_api, "SessionLocal", lambda: mock_db)
    monkeypatch.setattr(auth_api, "verify_and_resolve_tenant_membership", lambda db, u, o: True)

    client = TestClient(app)
    response = client.post(
        "/api/v1/auth/idp-exchange",
        json={
            "idp_provider": "google",
            "idp_token": token,
            "org_id": ORG_ID,
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
    from app.main import app
    token = create_signed_test_jwt(payload_overrides={"email": "suspended@firm.test"})

    mock_db = MagicMock()
    # is_active = False
    mock_db.execute.return_value.fetchone.return_value = (USER_ID, "suspended@firm.test", False)
    monkeypatch.setattr(auth_api, "SessionLocal", lambda: mock_db)

    client = TestClient(app)
    response = client.post(
        "/api/v1/auth/idp-exchange",
        json={
            "idp_provider": "google",
            "idp_token": token,
            "org_id": ORG_ID,
        },
    )
    assert response.status_code == 403
    assert "SUSPENDED_USER" in response.json()["detail"]


def test_idp_token_exchange_rejects_non_member_user(monkeypatch):
    """Proves IdP exchange rejects authenticated users without active tenant membership."""
    from app.main import app
    token = create_signed_test_jwt(payload_overrides={"email": "nonmember@firm.test"})

    mock_db = MagicMock()
    mock_db.execute.return_value.fetchone.return_value = (USER_ID, "nonmember@firm.test", True)
    monkeypatch.setattr(auth_api, "SessionLocal", lambda: mock_db)
    monkeypatch.setattr(auth_api, "verify_and_resolve_tenant_membership", lambda db, u, o: False)

    client = TestClient(app)
    response = client.post(
        "/api/v1/auth/idp-exchange",
        json={
            "idp_provider": "google",
            "idp_token": token,
            "org_id": ORG_ID,
        },
    )
    assert response.status_code == 403
    assert "TENANT_MEMBERSHIP_REQUIRED" in response.json()["detail"]
