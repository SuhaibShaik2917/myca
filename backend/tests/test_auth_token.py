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


def _resolve_test_org_id():
    from tests.test_db_config import get_admin_session
    from sqlalchemy import text
    try:
        db = get_admin_session()
        row = db.execute(text("SELECT id FROM public.organizations LIMIT 1;")).fetchone()
        db.close()
        if row:
            return str(row[0])
    except Exception:
        pass
    return "7c157424-1b47-4f2d-8751-19fab9f7ae35"


USER_ID = str(uuid.uuid4())
ORG_ID = _resolve_test_org_id()


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
            "nonce": "test-rsa-prod-nonce-001",
        },
        key=priv_pem,
        algorithm="RS256",
    )

    mock_db = MagicMock()
    mock_db.execute.return_value.fetchone.return_value = (USER_ID, "prod.auditor@firm.test", True)
    monkeypatch.setattr(auth_api, "SessionLocal", lambda: mock_db)
    monkeypatch.setattr(auth_api, "consume_idp_login_nonce", lambda db, nonce, org_id, idp_provider, email: True)
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
    token = create_signed_test_jwt(
        payload_overrides={
            "email": "suspended@firm.test",
            "nonce": "test-suspended-nonce-001",
        }
    )

    mock_db = MagicMock()
    # is_active = False
    mock_db.execute.return_value.fetchone.return_value = (USER_ID, "suspended@firm.test", False)
    monkeypatch.setattr(auth_api, "SessionLocal", lambda: mock_db)
    monkeypatch.setattr(auth_api, "consume_idp_login_nonce", lambda db, nonce, org_id, idp_provider, email: True)

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
    token = create_signed_test_jwt(
        payload_overrides={
            "email": "nonmember@firm.test",
            "nonce": "test-nonmember-nonce-001",
        }
    )

    mock_db = MagicMock()
    mock_db.execute.return_value.fetchone.return_value = (USER_ID, "nonmember@firm.test", True)
    monkeypatch.setattr(auth_api, "SessionLocal", lambda: mock_db)
    monkeypatch.setattr(auth_api, "consume_idp_login_nonce", lambda db, nonce, org_id, idp_provider, email: True)
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


# =========================================================================
# 5. v1.0.13 Single-Use IdP Nonce Replay Protection & Strict Validation Tests
# =========================================================================

def test_idp_nonce_endpoint_issues_tracked_nonce():
    """Proves POST /api/v1/auth/idp-nonce issues an unpredictable active nonce with 5 min TTL."""
    from app.main import app
    client = TestClient(app)
    response = client.post(
        "/api/v1/auth/idp-nonce",
        json={
            "org_id": ORG_ID,
            "idp_provider": "google",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert "nonce" in data
    assert len(data["nonce"]) >= 32
    assert data["expires_in"] == 300
    assert data["org_id"] == ORG_ID
    assert data["idp_provider"] == "google"


def test_idp_token_exchange_requires_nonce_claim():
    """Proves token exchange strictly rejects IdP tokens missing a nonce claim."""
    from app.main import app
    client = TestClient(app)
    # Token without nonce
    token = create_signed_test_jwt(payload_overrides={"nonce": None})
    response = client.post(
        "/api/v1/auth/idp-exchange",
        json={
            "idp_provider": "google",
            "idp_token": token,
            "org_id": ORG_ID,
        },
    )
    assert response.status_code == 401
    assert "MISSING_IDP_NONCE" in response.json()["detail"]


def test_idp_token_exchange_rejects_missing_email_claim():
    """Proves token exchange strictly rejects tokens with missing email (never falls back to sub)."""
    with pytest.raises(HTTPException) as excinfo:
        validate_idp_token("google", create_signed_test_jwt(payload_overrides={"email": None, "sub": "attacker-sub"}), env="test")
    assert excinfo.value.status_code == 401
    assert "MISSING_EMAIL_CLAIM" in excinfo.value.detail or "INVALID_EMAIL" in excinfo.value.detail


@pytest.mark.parametrize("invalid_email", ["", "   ", "notanemail", "noatsign.com"])
def test_idp_token_exchange_rejects_invalid_email_format(invalid_email):
    """Proves token exchange rejects malformed or empty email claims."""
    with pytest.raises(HTTPException) as excinfo:
        validate_idp_token("google", create_signed_test_jwt(payload_overrides={"email": invalid_email}), env="test")
    assert excinfo.value.status_code == 401
    assert "INVALID_EMAIL_FORMAT" in excinfo.value.detail or "INVALID_EMAIL_CLAIM" in excinfo.value.detail


def test_idp_token_exchange_rejects_missing_email_verified_claim():
    """Proves token exchange strictly rejects tokens missing the email_verified claim."""
    with pytest.raises(HTTPException) as excinfo:
        validate_idp_token("google", create_signed_test_jwt(payload_overrides={"email_verified": None}), env="test")
    assert excinfo.value.status_code == 401
    assert "MISSING_EMAIL_VERIFIED_CLAIM" in excinfo.value.detail


@pytest.mark.parametrize("non_bool_value", ["true", "True", 1, 0, "1", "false", [], {}])
def test_idp_token_exchange_rejects_non_boolean_email_verified(non_bool_value):
    """Proves token exchange strictly requires email_verified to be Python bool True (rejects truthy strings/ints)."""
    with pytest.raises(HTTPException) as excinfo:
        validate_idp_token("google", create_signed_test_jwt(payload_overrides={"email_verified": non_bool_value}), env="test")
    assert excinfo.value.status_code == 401
    assert "UNVERIFIED_EMAIL" in excinfo.value.detail


def test_idp_token_exchange_rejects_provider_issuer_mismatch():
    """Proves token exchange strictly enforces provider-to-issuer binding."""
    # Google provider but token issued by Microsoft
    token = create_signed_test_jwt(payload_overrides={"iss": "https://login.microsoftonline.com/common/v2.0"})
    with pytest.raises(HTTPException) as excinfo:
        validate_idp_token("google", token, env="test")
    assert excinfo.value.status_code == 401
    assert "PROVIDER_ISSUER_MISMATCH" in excinfo.value.detail


def test_idp_token_exchange_normalizes_email(monkeypatch):
    """Proves that email claim is normalized (lowercased and whitespace trimmed) before user lookup."""
    from app.main import app
    client = TestClient(app)
    
    # Request nonce
    nonce_resp = client.post("/api/v1/auth/idp-nonce", json={"org_id": ORG_ID, "idp_provider": "google"})
    assert nonce_resp.status_code == 200
    nonce = nonce_resp.json()["nonce"]

    token = create_signed_test_jwt(
        payload_overrides={
            "email": "   Auditor.Normal@Firm.TEST   ",
            "nonce": nonce,
        }
    )

    lookup_email = []
    mock_db = MagicMock()
    def mock_execute(query, params=None):
        if params and "email" in params:
            lookup_email.append(params["email"])
        mock_res = MagicMock()
        mock_res.fetchone.return_value = (USER_ID, "auditor.normal@firm.test", True)
        return mock_res
    mock_db.execute = mock_execute

    monkeypatch.setattr(auth_api, "SessionLocal", lambda: mock_db)
    monkeypatch.setattr(auth_api, "consume_idp_login_nonce", lambda db, nonce, org_id, idp_provider, email: True)
    monkeypatch.setattr(auth_api, "verify_and_resolve_tenant_membership", lambda db, u, o: True)

    response = client.post(
        "/api/v1/auth/idp-exchange",
        json={
            "idp_provider": "google",
            "idp_token": token,
            "org_id": ORG_ID,
        },
    )
    assert response.status_code == 200
    assert len(lookup_email) > 0
    assert lookup_email[0] == "auditor.normal@firm.test"


def test_end_to_end_idp_nonce_issuance_and_atomic_replay_prevention():
    """
    INTEGRATION REPLAY DEFENSE TEST:
    Proves that a real database-tracked nonce can be consumed exactly once,
    and subsequent replay attempts with the same token/nonce are rejected (HTTP 401 IDP_NONCE_REPLAY).
    """
    from app.main import app
    client = TestClient(app)

    # 1. Request an authentic login nonce
    nonce_resp = client.post(
        "/api/v1/auth/idp-nonce",
        json={"org_id": ORG_ID, "idp_provider": "google"},
    )
    assert nonce_resp.status_code == 200
    nonce = nonce_resp.json()["nonce"]

    # 2. Forge signed token with the issued nonce and registered user email
    user_email = f"ci_replay_{uuid.uuid4().hex[:8]}@myca.test"
    token = create_signed_test_jwt(
        payload_overrides={
            "email": user_email,
            "nonce": nonce,
        }
    )

    # Provision user and membership in DB for exchange
    from tests.test_db_config import get_admin_session
    from sqlalchemy import text
    admin_db = get_admin_session()
    try:
        admin_db.execute(
            text("INSERT INTO public.global_users (id, email, full_name, is_active) VALUES (:id, :email, 'Replay Test User', true) ON CONFLICT (email) DO NOTHING;"),
            {"id": USER_ID, "email": user_email}
        )
        u_id = admin_db.execute(text("SELECT id FROM public.global_users WHERE email = :email;"), {"email": user_email}).fetchone()[0]
        admin_db.execute(
            text("INSERT INTO public.organization_memberships (id, org_id, user_id, role, status) VALUES (gen_random_uuid(), :oid, :uid, 'owner', 'active') ON CONFLICT (org_id, user_id) DO UPDATE SET status='active';"),
            {"oid": ORG_ID, "uid": u_id}
        )
        admin_db.commit()
    finally:
        admin_db.close()

    # 3. First exchange must SUCCEED (200 OK)
    resp1 = client.post(
        "/api/v1/auth/idp-exchange",
        json={"idp_provider": "google", "idp_token": token, "org_id": ORG_ID},
    )
    assert resp1.status_code == 200, f"Expected 200 on first exchange, got: {resp1.text}"
    assert "access_token" in resp1.json()

    # 4. Immediate REPLAY with same token and nonce must FAIL with 401 IDP_NONCE_REPLAY
    resp2 = client.post(
        "/api/v1/auth/idp-exchange",
        json={"idp_provider": "google", "idp_token": token, "org_id": ORG_ID},
    )
    assert resp2.status_code == 401
    assert "IDP_NONCE_REPLAY" in resp2.json()["detail"]


def test_idp_exchange_rejects_expired_nonce():
    """Proves exchange rejects nonces whose expiration timestamp has passed."""
    from app.database import SessionLocal
    from app.modules.auth.nonce_service import consume_idp_login_nonce
    from sqlalchemy import text

    expired_nonce = f"EXPIRED-TEST-NONCE-{uuid.uuid4().hex[:8]}"
    db = SessionLocal()
    try:
        db.execute(text("""
            INSERT INTO public.idp_authentication_nonces (
                nonce, org_id, idp_provider, status, created_at, expires_at
            ) VALUES (
                :nonce, :org_id, 'google', 'ACTIVE', NOW() - INTERVAL '10 minutes', NOW() - INTERVAL '5 minutes'
            ) ON CONFLICT (nonce) DO UPDATE SET status = 'ACTIVE', expires_at = NOW() - INTERVAL '5 minutes';
        """), {"nonce": expired_nonce, "org_id": ORG_ID})
        db.commit()

        with pytest.raises(HTTPException) as excinfo:
            consume_idp_login_nonce(db, expired_nonce, ORG_ID, "google", "user@test.com")
        assert excinfo.value.status_code == 401
        assert "EXPIRED_IDP_NONCE" in excinfo.value.detail
    finally:
        db.close()


def test_idp_exchange_rejects_nonce_context_mismatch():
    """Proves exchange rejects nonces bound to a different organization or provider."""
    from tests.test_db_config import get_admin_session
    from app.database import SessionLocal
    from app.modules.auth.nonce_service import consume_idp_login_nonce, create_idp_login_nonce
    from sqlalchemy import text

    other_org = str(uuid.uuid4())
    admin_db = get_admin_session()
    try:
        admin_db.execute(
            text("INSERT INTO public.organizations (id, legal_name, pan) VALUES (:id, 'Other Org Legal', 'XYZAB9876C') ON CONFLICT DO NOTHING;"),
            {"id": other_org}
        )
        admin_db.commit()
    finally:
        admin_db.close()

    db = SessionLocal()
    try:
        result = create_idp_login_nonce(db, ORG_ID, "google")
        nonce = result["nonce"]

        # 1. Reject org mismatch
        with pytest.raises(HTTPException) as excinfo1:
            consume_idp_login_nonce(db, nonce, other_org, "google", "user@test.com")
        assert excinfo1.value.status_code == 401
        assert "NONCE_CONTEXT_MISMATCH" in excinfo1.value.detail

        # 2. Reject provider mismatch
        with pytest.raises(HTTPException) as excinfo2:
            consume_idp_login_nonce(db, nonce, ORG_ID, "azuread", "user@test.com")
        assert excinfo2.value.status_code == 401
        assert "NONCE_CONTEXT_MISMATCH" in excinfo2.value.detail
    finally:
        db.close()


# =========================================================================
# 6. Staging Environment Hardening Configuration Tests
# =========================================================================

def test_settings_prohibits_default_secret_key_in_staging(monkeypatch):
    """Proves Settings rejects default or insecure SECRET_KEY in staging environment."""
    monkeypatch.setenv("ENVIRONMENT", "staging")
    monkeypatch.setenv("DATABASE_URL", "postgresql://app:secure_pass@staging-db.internal:5432/myca_staging")
    monkeypatch.setenv("DATABASE_WORKER_URL", "postgresql://worker:secure_pass@staging-db.internal:5432/myca_staging")
    with pytest.raises(ValueError) as excinfo:
        Settings(
            ENVIRONMENT="staging",
            SECRET_KEY="myca_jwt_test_secret_key_minimum_32_characters_long_for_security",
            DATABASE_URL="postgresql://app:secure_pass@staging-db.internal:5432/myca_staging",
            DATABASE_WORKER_URL="postgresql://worker:secure_pass@staging-db.internal:5432/myca_staging",
            _env_file=None,
        )
    assert "default or test SECRET_KEY is strictly prohibited" in str(excinfo.value)


@pytest.mark.parametrize("insecure_db_url", [
    "postgresql://app_service_user:secret@staging-db.internal:5432/myca_staging",
    "postgresql://app:pass@localhost:5432/myca_staging",
    "postgresql://app:pass@127.0.0.1:5432/myca_staging",
])
def test_settings_prohibits_insecure_database_url_in_staging(monkeypatch, insecure_db_url):
    """Proves Settings rejects default passwords or localhost URLs in staging environment."""
    monkeypatch.setenv("ENVIRONMENT", "staging")
    monkeypatch.setenv("DATABASE_WORKER_URL", "postgresql://worker:secure_pass@staging-db.internal:5432/myca_staging")
    with pytest.raises(ValueError) as excinfo:
        Settings(
            ENVIRONMENT="staging",
            SECRET_KEY="staging_secure_random_key_with_high_entropy_32_chars!",
            DATABASE_URL=insecure_db_url,
            DATABASE_WORKER_URL="postgresql://worker:secure_pass@staging-db.internal:5432/myca_staging",
            _env_file=None,
        )
    assert "Insecure or default DATABASE_URL" in str(excinfo.value) or "must target a remote/dedicated host" in str(excinfo.value)


def test_settings_prohibits_allow_demo_tokens_in_staging(monkeypatch):
    """Proves Settings strictly rejects ALLOW_DEMO_TOKENS=True in staging environment."""
    monkeypatch.setenv("ENVIRONMENT", "staging")
    monkeypatch.setenv("DATABASE_WORKER_URL", "postgresql://worker:secure_pass@staging-db.internal:5432/myca_staging")
    with pytest.raises(ValueError) as excinfo:
        Settings(
            ENVIRONMENT="staging",
            SECRET_KEY="staging_secure_random_key_with_high_entropy_32_chars!",
            DATABASE_URL="postgresql://app:secure_pass@staging-db.internal:5432/myca_staging",
            DATABASE_WORKER_URL="postgresql://worker:secure_pass@staging-db.internal:5432/myca_staging",
            ALLOW_DEMO_TOKENS=True,
            _env_file=None,
        )
    assert "ALLOW_DEMO_TOKENS cannot be enabled in production or staging" in str(excinfo.value)
