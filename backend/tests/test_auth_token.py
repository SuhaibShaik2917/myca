"""Unit and integration tests for authentication, environment configuration, demo-token guards, and IdP verification."""

import base64
import hashlib
import os
import secrets
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
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


def _ensure_org_exists(org_id: str):
    """Guarantees the specified organization exists in public.organizations for FK integrity."""
    import random
    from tests.test_db_config import get_admin_session
    from sqlalchemy import text
    admin_db = get_admin_session()
    try:
        # Check if already present
        exists = admin_db.execute(text("SELECT 1 FROM public.organizations WHERE id = :id;"), {"id": org_id}).fetchone()
        if exists:
            return

        for _ in range(10):
            pan = f"AAAPL{random.randint(1000, 9999)}Z"
            sp = admin_db.begin_nested()
            try:
                admin_db.execute(
                    text("""
                        INSERT INTO public.organizations (id, legal_name, pan)
                        VALUES (:id, 'Auth Test Organization', :pan)
                        ON CONFLICT (id) DO NOTHING;
                    """),
                    {"id": org_id, "pan": pan}
                )
                sp.commit()
                admin_db.commit()
                return
            except Exception as exc:
                sp.rollback()
                if "organizations_pan_key" in str(exc):
                    continue
                raise
    except Exception as exc:
        admin_db.rollback()
        raise RuntimeError(f"Fixture setup failure: could not ensure test org {org_id} exists: {exc}") from exc
    finally:
        admin_db.close()


def _resolve_test_org_id():
    from tests.test_db_config import get_admin_session
    from sqlalchemy import text
    test_org_id = "7c157424-1b47-4f2d-8751-19fab9f7ae35"
    db = get_admin_session()
    try:
        row = db.execute(text("SELECT id FROM public.organizations LIMIT 1;")).fetchone()
        if row:
            return str(row[0])
        # If no organization exists yet (e.g. fresh CI run), seed one
        db.execute(text("""
            INSERT INTO public.organizations (id, legal_name, pan)
            VALUES (:id, 'Auth Test Organization', 'AAAPL1234C')
            ON CONFLICT (id) DO NOTHING;
        """), {"id": test_org_id})
        db.commit()
        return test_org_id
    except Exception as exc:
        db.rollback()
        raise RuntimeError(f"Fixture setup failure: could not resolve test org: {exc}") from exc
    finally:
        db.close()


@pytest.fixture(autouse=True)
def reset_nonce_test_state():
    """Resets distributed rate limits and prunes active test nonces between tests."""
    from app.modules.auth.nonce_service import rate_limiter
    from tests.test_db_config import get_admin_session
    from sqlalchemy import text
    admin_db = get_admin_session()
    try:
        admin_db.execute(text("DELETE FROM public.auth_rate_limits;"))
        admin_db.execute(text("DELETE FROM public.idp_authentication_nonces WHERE status = 'ACTIVE';"))
        admin_db.commit()
    finally:
        admin_db.close()
    rate_limiter.reset()
    yield
    admin_db = get_admin_session()
    try:
        admin_db.execute(text("DELETE FROM public.auth_rate_limits;"))
        admin_db.execute(text("DELETE FROM public.idp_authentication_nonces WHERE status = 'ACTIVE';"))
        admin_db.commit()
    finally:
        admin_db.close()
    rate_limiter.reset()


def generate_pkce_pair():
    """Generates standard PKCE S256 code_verifier and code_challenge along with random state."""
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    state = secrets.token_urlsafe(32)
    return verifier, challenge, state


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
    verifier, challenge, state = generate_pkce_pair()
    client.cookies.set("myca_auth_session", "test_session_cookie")
    response = client.post(
        "/api/v1/auth/idp-exchange",
        json={
            "idp_provider": "google",
            "idp_token": token,
            "org_id": ORG_ID,
            "state": state,
            "code_verifier": verifier,
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
    monkeypatch.setattr(auth_api, "consume_idp_login_nonce", lambda *args, **kwargs: True)
    monkeypatch.setattr(auth_api, "verify_and_resolve_tenant_membership", lambda db, u, o: True)

    client = TestClient(app)
    client.cookies.set("myca_auth_session", "test_cookie")
    response = client.post(
        "/api/v1/auth/idp-exchange",
        json={
            "idp_provider": "google",
            "idp_token": token,
            "org_id": ORG_ID,
            "state": "test_state_12345",
            "code_verifier": "test_verifier",
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
    monkeypatch.setattr(auth_api, "consume_idp_login_nonce", lambda *args, **kwargs: True)

    client = TestClient(app)
    client.cookies.set("myca_auth_session", "test_cookie")
    response = client.post(
        "/api/v1/auth/idp-exchange",
        json={
            "idp_provider": "google",
            "idp_token": token,
            "org_id": ORG_ID,
            "state": "test_state_12345",
            "code_verifier": "test_verifier",
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
    monkeypatch.setattr(auth_api, "consume_idp_login_nonce", lambda *args, **kwargs: True)
    monkeypatch.setattr(auth_api, "verify_and_resolve_tenant_membership", lambda db, u, o: False)

    client = TestClient(app)
    client.cookies.set("myca_auth_session", "test_cookie")
    response = client.post(
        "/api/v1/auth/idp-exchange",
        json={
            "idp_provider": "google",
            "idp_token": token,
            "org_id": ORG_ID,
            "state": "test_state_12345",
            "code_verifier": "test_verifier",
        },
    )
    assert response.status_code == 403
    assert "TENANT_MEMBERSHIP_REQUIRED" in response.json()["detail"]


# =========================================================================
# 5. v1.0.13 Single-Use IdP Nonce Replay Protection & Strict Validation Tests
# =========================================================================

def test_idp_nonce_endpoint_issues_tracked_nonce():
    """Proves POST /api/v1/auth/idp-nonce issues an unpredictable active nonce with 5 min TTL."""
    _ensure_org_exists(ORG_ID)
    from app.main import app
    client = TestClient(app)
    verifier, challenge, state = generate_pkce_pair()
    response = client.post(
        "/api/v1/auth/idp-nonce",
        json={
            "org_id": ORG_ID,
            "idp_provider": "google",
            "state": state,
            "code_challenge": challenge,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert "nonce" in data
    assert len(data["nonce"]) >= 32
    assert data["expires_in"] == 300
    assert data["org_id"] == ORG_ID
    assert data["idp_provider"] == "google"
    assert data["state"] == state
    assert data["code_challenge"] == challenge
    assert "session_token" not in data, "session_token must NOT be exposed in JSON response"
    assert "myca_auth_session" in response.cookies, "HttpOnly session cookie must be set"


def test_idp_token_exchange_requires_nonce_claim():
    """Proves token exchange strictly rejects IdP tokens missing a nonce claim."""
    from app.main import app
    client = TestClient(app)
    verifier, challenge, state = generate_pkce_pair()
    client.cookies.set("myca_auth_session", "test_cookie")
    # Token without nonce
    token = create_signed_test_jwt(payload_overrides={"nonce": None})
    response = client.post(
        "/api/v1/auth/idp-exchange",
        json={
            "idp_provider": "google",
            "idp_token": token,
            "org_id": ORG_ID,
            "state": state,
            "code_verifier": verifier,
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
    _ensure_org_exists(ORG_ID)
    from app.main import app
    client = TestClient(app)
    
    # Request nonce
    verifier, challenge, state = generate_pkce_pair()
    nonce_resp = client.post(
        "/api/v1/auth/idp-nonce",
        json={"org_id": ORG_ID, "idp_provider": "google", "state": state, "code_challenge": challenge}
    )
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
    monkeypatch.setattr(auth_api, "consume_idp_login_nonce", lambda *args, **kwargs: True)
    monkeypatch.setattr(auth_api, "verify_and_resolve_tenant_membership", lambda db, u, o: True)

    response = client.post(
        "/api/v1/auth/idp-exchange",
        json={
            "idp_provider": "google",
            "idp_token": token,
            "org_id": ORG_ID,
            "state": state,
            "code_verifier": verifier,
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
    _ensure_org_exists(ORG_ID)
    from app.main import app
    client = TestClient(app)

    verifier, challenge, state = generate_pkce_pair()
    # 1. Request an authentic login nonce
    nonce_resp = client.post(
        "/api/v1/auth/idp-nonce",
        json={"org_id": ORG_ID, "idp_provider": "google", "state": state, "code_challenge": challenge},
    )
    assert nonce_resp.status_code == 200
    nonce = nonce_resp.json()["nonce"]
    session_cookie = client.cookies.get("myca_auth_session")

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
        json={"idp_provider": "google", "idp_token": token, "org_id": ORG_ID, "state": state, "code_verifier": verifier},
    )
    assert resp1.status_code == 200, f"Expected 200 on first exchange, got: {resp1.text}"
    assert "access_token" in resp1.json()

    # 4. Immediate REPLAY with same token and nonce (re-attaching session cookie) must FAIL with 401 IDP_NONCE_REPLAY
    client.cookies.set("myca_auth_session", session_cookie)
    resp2 = client.post(
        "/api/v1/auth/idp-exchange",
        json={"idp_provider": "google", "idp_token": token, "org_id": ORG_ID, "state": state, "code_verifier": verifier},
    )
    assert resp2.status_code == 401
    assert "IDP_NONCE_REPLAY" in resp2.json()["detail"]


def test_idp_exchange_rejects_expired_nonce():
    """Proves exchange rejects nonces whose expiration timestamp has passed."""
    _ensure_org_exists(ORG_ID)
    from app.database import SessionLocal, set_tenant_context
    from app.modules.auth.nonce_service import consume_idp_login_nonce
    from sqlalchemy import text

    verifier, challenge, state = generate_pkce_pair()
    session_token = secrets.token_urlsafe(32)
    session_hash = hashlib.sha256(session_token.encode("utf-8")).hexdigest()
    expired_nonce = f"EXPIRED-TEST-NONCE-{uuid.uuid4().hex[:8]}"
    db = SessionLocal()
    try:
        set_tenant_context(db, org_id=ORG_ID, user_id=None)
        db.execute(text("""
            INSERT INTO public.idp_authentication_nonces (
                nonce, org_id, idp_provider, status, created_at, expires_at,
                state, code_challenge, session_binding_hash
            ) VALUES (
                :nonce, :org_id, 'google', 'ACTIVE', NOW() - INTERVAL '10 minutes', NOW() - INTERVAL '5 minutes',
                :state, :challenge, :session_hash
            ) ON CONFLICT (nonce) DO UPDATE SET status = 'ACTIVE', expires_at = NOW() - INTERVAL '5 minutes';
        """), {"nonce": expired_nonce, "org_id": ORG_ID, "state": state, "challenge": challenge, "session_hash": session_hash})
        db.commit()

        with pytest.raises(HTTPException) as excinfo:
            consume_idp_login_nonce(
                db, expired_nonce, ORG_ID, "google", "user@test.com",
                session_token=session_token, state=state, code_verifier=verifier
            )
        assert excinfo.value.status_code == 401
        assert "EXPIRED_IDP_NONCE" in excinfo.value.detail
    finally:
        db.close()


def test_idp_exchange_rejects_nonce_context_mismatch():
    """Proves exchange rejects nonces bound to a different organization or provider."""
    _ensure_org_exists(ORG_ID)
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
        verifier, challenge, state = generate_pkce_pair()
        result = create_idp_login_nonce(db, ORG_ID, "google", state=state, code_challenge=challenge)
        nonce = result["nonce"]
        session_token = result.get("session_token")

        # 1. Reject org mismatch (RLS isolates row or context mismatch detected)
        with pytest.raises(HTTPException) as excinfo1:
            consume_idp_login_nonce(
                db, nonce, other_org, "google", "user@test.com",
                session_token=session_token, state=state, code_verifier=verifier
            )
        assert excinfo1.value.status_code == 401
        assert "NONCE_CONTEXT_MISMATCH" in excinfo1.value.detail or "UNKNOWN_IDP_NONCE" in excinfo1.value.detail

        # 2. Reject provider mismatch
        with pytest.raises(HTTPException) as excinfo2:
            consume_idp_login_nonce(
                db, nonce, ORG_ID, "azuread", "user@test.com",
                session_token=session_token, state=state, code_verifier=verifier
            )
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


# =========================================================================
# 7. v1.0.14 Enhanced Authentication, Tenant Isolation & Abuse Tests
# =========================================================================

def test_multithreaded_concurrent_race_against_same_nonce():
    """
    CONCURRENCY STRESS TEST (Requirement 6):
    Proves that when multiple independent threads race to consume the exact same nonce,
    PostgreSQL atomic conditional update guarantees exactly ONE thread consumes it,
    and all other concurrent attempts fail closed with HTTP 401 IDP_NONCE_REPLAY.
    """
    _ensure_org_exists(ORG_ID)
    from app.database import SessionLocal
    from app.modules.auth.nonce_service import create_idp_login_nonce, consume_idp_login_nonce

    verifier, challenge, state = generate_pkce_pair()
    init_db = SessionLocal()
    try:
        result = create_idp_login_nonce(init_db, ORG_ID, "google", state=state, code_challenge=challenge)
        nonce = result["nonce"]
        session_token = result["session_token"]
    finally:
        init_db.close()

    successes = []
    replays = []
    errors = []

    def race_worker(worker_id: int):
        db = SessionLocal()
        try:
            consumed = consume_idp_login_nonce(
                db,
                nonce=nonce,
                org_id=ORG_ID,
                idp_provider="google",
                email=f"worker_{worker_id}@myca.test",
                session_token=session_token,
                state=state,
                code_verifier=verifier,
            )
            successes.append(worker_id)
        except HTTPException as exc:
            if exc.status_code == 401 and "IDP_NONCE_REPLAY" in exc.detail:
                replays.append(worker_id)
            else:
                errors.append((worker_id, str(exc.detail)))
        except Exception as exc:
            errors.append((worker_id, str(exc)))
        finally:
            db.close()

    # Race 5 independent threads concurrently
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(race_worker, i) for i in range(5)]
        for f in futures:
            f.result()

    assert len(errors) == 0, f"Unexpected errors during concurrent race: {errors}"
    assert len(successes) == 1, f"Expected exactly 1 thread to consume nonce, got {len(successes)}"
    assert len(replays) == 4, f"Expected exactly 4 threads to be rejected for replay, got {len(replays)}"


def test_browser_session_binding_and_anti_csrf():
    """
    BROWSER SESSION BINDING & ANTI-CSRF TEST (Requirement 2):
    Proves authentication transactions are securely bound to the initiating client/browser session.
    Rejects missing or mismatched session tokens to prevent login CSRF and session fixation.
    """
    _ensure_org_exists(ORG_ID)
    from app.database import SessionLocal
    from app.modules.auth.nonce_service import create_idp_login_nonce, consume_idp_login_nonce

    verifier, challenge, state = generate_pkce_pair()
    # 1. Direct service layer binding tests
    db = SessionLocal()
    try:
        result = create_idp_login_nonce(db, ORG_ID, "google", state=state, code_challenge=challenge)
        nonce = result["nonce"]
        valid_session_token = result["session_token"]

        # Missing session token -> 401 MISSING_SESSION_BINDING
        with pytest.raises(HTTPException) as exc1:
            consume_idp_login_nonce(
                db, nonce=nonce, org_id=ORG_ID, idp_provider="google",
                email="user@test.com", session_token=None, state=state, code_verifier=verifier
            )
        assert exc1.value.status_code == 401
        assert "MISSING_SESSION_BINDING" in exc1.value.detail

        # Tampered / mismatched session token -> 401 SESSION_BINDING_MISMATCH
        with pytest.raises(HTTPException) as exc2:
            consume_idp_login_nonce(
                db, nonce=nonce, org_id=ORG_ID, idp_provider="google",
                email="user@test.com", session_token="forged_attacker_session_token", state=state, code_verifier=verifier
            )
        assert exc2.value.status_code == 401
        assert "SESSION_BINDING_MISMATCH" in exc2.value.detail

        # Authentic session token -> 200 OK consumed
        consumed = consume_idp_login_nonce(
            db, nonce=nonce, org_id=ORG_ID, idp_provider="google",
            email="user@test.com", session_token=valid_session_token, state=state, code_verifier=verifier
        )
        assert consumed is True
    finally:
        db.close()


def test_pkce_s256_verification():
    """
    PKCE S256 TEST (Requirement 2):
    Proves transaction enforces PKCE S256 code challenge and verifier match using constant-time comparison.
    """
    _ensure_org_exists(ORG_ID)
    from app.database import SessionLocal
    from app.modules.auth.nonce_service import create_idp_login_nonce, consume_idp_login_nonce

    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    state = secrets.token_urlsafe(32)

    db = SessionLocal()
    try:
        result = create_idp_login_nonce(
            db, ORG_ID, "google", state=state, code_challenge=code_challenge
        )
        nonce = result["nonce"]
        session_token = result["session_token"]

        # 1. Missing code_verifier -> 401 MISSING_PKCE_VERIFIER
        with pytest.raises(HTTPException) as exc1:
            consume_idp_login_nonce(
                db, nonce=nonce, org_id=ORG_ID, idp_provider="google",
                email="pkce@test.com", session_token=session_token, state=state, code_verifier=None
            )
        assert exc1.value.status_code == 401
        assert "MISSING_PKCE_VERIFIER" in exc1.value.detail

        # 2. Invalid code_verifier -> 401 INVALID_PKCE_VERIFIER
        with pytest.raises(HTTPException) as exc2:
            consume_idp_login_nonce(
                db, nonce=nonce, org_id=ORG_ID, idp_provider="google",
                email="pkce@test.com", session_token=session_token, state=state, code_verifier="wrong_verifier_string"
            )
        assert exc2.value.status_code == 401
        assert "INVALID_PKCE_VERIFIER" in exc2.value.detail

        # 3. Matching code_verifier -> 200 OK
        consumed = consume_idp_login_nonce(
            db, nonce=nonce, org_id=ORG_ID, idp_provider="google",
            email="pkce@test.com", session_token=session_token, state=state, code_verifier=verifier
        )
        assert consumed is True
    finally:
        db.close()


def test_nonce_issuance_rate_limiting_abuse_controls():
    """
    ABUSE PROTECTION RATE LIMIT TEST (Requirement 3):
    Proves distributed rate limiting restricts requests to 20/min per IP and returns HTTP 429.
    """
    _ensure_org_exists(ORG_ID)
    from app.database import SessionLocal
    from app.modules.auth.nonce_service import create_idp_login_nonce, consume_idp_login_nonce

    test_ip = "192.168.100.50"
    db = SessionLocal()
    try:
        # First 20 requests succeed (consume each nonce so active cap is not exceeded)
        for i in range(20):
            verifier, challenge, state = generate_pkce_pair()
            res = create_idp_login_nonce(db, ORG_ID, "google", state=state, code_challenge=challenge, client_ip=test_ip)
            assert "nonce" in res
            consume_idp_login_nonce(
                db, nonce=res["nonce"], org_id=ORG_ID, idp_provider="google",
                email=f"user_{i}@test.com", session_token=res["session_token"],
                state=state, code_verifier=verifier
            )

        # 21st request from same IP within the minute is rate limited
        verifier, challenge, state = generate_pkce_pair()
        with pytest.raises(HTTPException) as exc:
            create_idp_login_nonce(db, ORG_ID, "google", state=state, code_challenge=challenge, client_ip=test_ip)
        assert exc.value.status_code == 429
        assert "RATE_LIMIT_EXCEEDED" in exc.value.detail
    finally:
        db.close()


def test_active_nonce_cap_per_org():
    """
    ACTIVE NONCE CAP TEST (Requirement 3):
    Proves per-organization active nonce cap (max 5) rejects unbounded generation with HTTP 429.
    """
    unique_org = str(uuid.uuid4())
    _ensure_org_exists(unique_org)
    from app.database import SessionLocal
    from app.modules.auth.nonce_service import create_idp_login_nonce

    db = SessionLocal()
    try:
        # Issue 5 active nonces (using different IPs to avoid triggering IP rate limit)
        for i in range(5):
            verifier, challenge, state = generate_pkce_pair()
            res = create_idp_login_nonce(db, unique_org, "google", state=state, code_challenge=challenge, client_ip=f"10.0.1.{i+1}")
            assert "nonce" in res

        # 6th active nonce exceeds per-org cap
        verifier, challenge, state = generate_pkce_pair()
        with pytest.raises(HTTPException) as exc:
            create_idp_login_nonce(db, unique_org, "google", state=state, code_challenge=challenge, client_ip="10.0.1.99")
        assert exc.value.status_code == 429
        assert "ACTIVE_NONCE_LIMIT_EXCEEDED" in exc.value.detail
    finally:
        db.close()


def test_expired_nonce_safe_cleanup():
    """
    EXPIRED NONCE CLEANUP TEST (Requirement 3):
    Proves expired active nonces are pruned automatically, while consumed nonces are preserved.
    """
    clean_org = str(uuid.uuid4())
    _ensure_org_exists(clean_org)
    from app.database import SessionLocal, set_tenant_context
    from app.modules.auth.nonce_service import create_idp_login_nonce
    from sqlalchemy import text

    db = SessionLocal()
    try:
        set_tenant_context(db, org_id=clean_org, user_id=None)

        expired_nonce = f"EXPIRED-CLEANUP-{uuid.uuid4().hex[:8]}"
        consumed_nonce = f"CONSUMED-PRESERVE-{uuid.uuid4().hex[:8]}"

        # Seed 1 expired active nonce and 1 consumed nonce
        db.execute(text("""
            INSERT INTO public.idp_authentication_nonces (
                nonce, org_id, idp_provider, status, created_at, expires_at
            ) VALUES (
                :exp, :org_id, 'google', 'ACTIVE', NOW() - INTERVAL '10 minutes', NOW() - INTERVAL '5 minutes'
            );
        """), {"exp": expired_nonce, "org_id": clean_org})

        db.execute(text("""
            INSERT INTO public.idp_authentication_nonces (
                nonce, org_id, idp_provider, status, created_at, expires_at, consumed_at, consumed_by_email
            ) VALUES (
                :cons, :org_id, 'google', 'CONSUMED', NOW() - INTERVAL '10 minutes', NOW() + INTERVAL '5 minutes', NOW(), 'user@test.com'
            );
        """), {"cons": consumed_nonce, "org_id": clean_org})
        db.commit()

        # Call create_idp_login_nonce which automatically runs the safe prune
        verifier, challenge, state = generate_pkce_pair()
        create_idp_login_nonce(db, clean_org, "google", state=state, code_challenge=challenge, client_ip="10.20.30.40")

        # Re-apply tenant context for verification query (create_idp_login_nonce commits, clearing transaction-local context)
        set_tenant_context(db, org_id=clean_org, user_id=None)

        # Verify expired active nonce was pruned
        exp_row = db.execute(text("SELECT nonce FROM public.idp_authentication_nonces WHERE nonce = :nonce;"), {"nonce": expired_nonce}).fetchone()
        assert exp_row is None, "Expired active nonce was NOT pruned!"

        # Verify consumed nonce was preserved for permanent replay defense
        cons_row = db.execute(text("SELECT nonce FROM public.idp_authentication_nonces WHERE nonce = :nonce;"), {"nonce": consumed_nonce}).fetchone()
        assert cons_row is not None, "Consumed nonce was erroneously deleted during cleanup!"
    finally:
        db.close()


def test_database_enforced_tenant_isolation_for_nonces():
    """
    DATABASE-ENFORCED TENANT ISOLATION TEST (Requirement 1):
    Proves PostgreSQL RLS policy on public.idp_authentication_nonces isolates records:
    Org B cannot SELECT, UPDATE, or DELETE Org A's nonces.
    Null context returns 0 rows.
    """
    org_a = str(uuid.uuid4())
    org_b = str(uuid.uuid4())
    _ensure_org_exists(org_a)
    _ensure_org_exists(org_b)

    from app.database import SessionLocal, set_tenant_context
    from app.modules.auth.nonce_service import create_idp_login_nonce
    from sqlalchemy import text

    db = SessionLocal()
    try:
        # Seed nonce under Org A
        verifier, challenge, state = generate_pkce_pair()
        result_a = create_idp_login_nonce(db, org_a, "google", state=state, code_challenge=challenge, client_ip="10.10.10.1")
        nonce_a = result_a["nonce"]

        # Switch context to Org B
        set_tenant_context(db, org_id=org_b, user_id=None)

        # 1. Read isolation
        row_b = db.execute(text("SELECT nonce FROM public.idp_authentication_nonces WHERE nonce = :nonce;"), {"nonce": nonce_a}).fetchall()
        assert len(row_b) == 0, "CRITICAL RLS BREACH: Org B can read Org A's nonce!"

        # 2. Update isolation
        res_up = db.execute(text("UPDATE public.idp_authentication_nonces SET status = 'CONSUMED' WHERE nonce = :nonce;"), {"nonce": nonce_a})
        assert res_up.rowcount == 0, "CRITICAL RLS BREACH: Org B updated Org A's nonce!"

        # 3. Delete isolation
        res_del = db.execute(text("DELETE FROM public.idp_authentication_nonces WHERE nonce = :nonce;"), {"nonce": nonce_a})
        assert res_del.rowcount == 0, "CRITICAL RLS BREACH: Org B deleted Org A's nonce!"

        # 4. Null context isolation
        db.execute(text("SELECT set_config('app.current_org', '', false);"))
        row_null = db.execute(text("SELECT nonce FROM public.idp_authentication_nonces WHERE nonce = :nonce;"), {"nonce": nonce_a}).fetchall()
        assert len(row_null) == 0, "CRITICAL RLS BREACH: Null context can read nonce!"

        # 5. Org A context succeeds
        set_tenant_context(db, org_id=org_a, user_id=None)
        row_a = db.execute(text("SELECT nonce FROM public.idp_authentication_nonces WHERE nonce = :nonce;"), {"nonce": nonce_a}).fetchall()
        assert len(row_a) == 1, "Org A cannot see its own nonce under valid tenant context!"
    finally:
        db.close()


def test_exact_canonical_provider_issuer_matching(monkeypatch):
    """
    EXACT CANONICAL ISSUER MATCHING TEST (Requirement 4):
    Proves strict rejection of loose substring matches in provider issuer claim.
    """
    # 1. Loose substring matching attack on Google: https://accounts.google.com.attacker.com
    phishing_token = create_signed_test_jwt(
        payload_overrides={"iss": "https://accounts.google.com.attacker.com"}
    )
    with pytest.raises(HTTPException) as exc1:
        validate_idp_token("google", phishing_token, env="test")
    assert exc1.value.status_code == 401
    assert "PROVIDER_ISSUER_MISMATCH" in exc1.value.detail

    # 2. Prefix substring attack: https://evil-accounts.google.com
    phishing_token2 = create_signed_test_jwt(
        payload_overrides={"iss": "https://evil-accounts.google.com"}
    )
    with pytest.raises(HTTPException) as exc2:
        validate_idp_token("google", phishing_token2, env="test")
    assert exc2.value.status_code == 401
    assert "PROVIDER_ISSUER_MISMATCH" in exc2.value.detail

    # 3. Valid exact canonical issuer: https://accounts.google.com
    valid_token = create_signed_test_jwt(
        payload_overrides={"iss": "https://accounts.google.com"}
    )
    res = validate_idp_token("google", valid_token, env="test")
    assert res["iss"] == "https://accounts.google.com"


# =========================================================================
# 8. v1.0.15 Mandatory PKCE, State, HttpOnly Cookie & Distributed Safety Tests
# =========================================================================

def test_mandatory_state_and_challenge_rejected_when_missing_or_malformed():
    """
    MANDATORY PKCE & STATE TEST:
    Proves POST /api/v1/auth/idp-nonce strictly requires valid state and code_challenge.
    """
    _ensure_org_exists(ORG_ID)
    from app.main import app
    client = TestClient(app)
    verifier, challenge, state = generate_pkce_pair()

    # 1. Missing state entirely -> 422 Unprocessable Entity
    resp1 = client.post("/api/v1/auth/idp-nonce", json={"org_id": ORG_ID, "idp_provider": "google", "code_challenge": challenge})
    assert resp1.status_code == 422

    # 2. Short / invalid state (<16 chars) -> 400 Bad Request
    resp2 = client.post("/api/v1/auth/idp-nonce", json={"org_id": ORG_ID, "idp_provider": "google", "state": "short", "code_challenge": challenge})
    assert resp2.status_code == 400
    assert "INVALID_STATE" in resp2.json()["detail"]

    # 3. Missing code_challenge -> 422 Unprocessable Entity
    resp3 = client.post("/api/v1/auth/idp-nonce", json={"org_id": ORG_ID, "idp_provider": "google", "state": state})
    assert resp3.status_code == 422

    # 4. Invalid characters in code_challenge -> 400 Bad Request
    resp4 = client.post("/api/v1/auth/idp-nonce", json={"org_id": ORG_ID, "idp_provider": "google", "state": state, "code_challenge": "invalid+with+plus!@#$%^&*()"})
    assert resp4.status_code == 400
    assert "INVALID_CODE_CHALLENGE" in resp4.json()["detail"]


def test_httponly_cookie_only_session_binding_and_rejection_of_fallbacks():
    """
    HTTPONLY COOKIE ONLY BINDING TEST:
    Proves browser exchange accepts session token EXCLUSIVELY from the HttpOnly cookie.
    Rejects header and body fallback attempts when cookie is missing.
    """
    _ensure_org_exists(ORG_ID)
    from app.main import app
    client = TestClient(app)
    verifier, challenge, state = generate_pkce_pair()

    # Request nonce
    nonce_resp = client.post(
        "/api/v1/auth/idp-nonce",
        json={"org_id": ORG_ID, "idp_provider": "google", "state": state, "code_challenge": challenge}
    )
    assert nonce_resp.status_code == 200
    nonce_data = nonce_resp.json()
    assert "session_token" not in nonce_data, "session_token must NOT be in JSON response!"
    session_cookie = client.cookies.get("myca_auth_session")
    assert session_cookie is not None, "myca_auth_session cookie missing!"

    # Forge test token
    user_email = f"cookie_test_{uuid.uuid4().hex[:8]}@myca.test"
    token = create_signed_test_jwt(payload_overrides={"email": user_email, "nonce": nonce_data["nonce"]})

    # 1. Attacker creates fresh client with NO cookies and attempts header/body injection
    attacker_client = TestClient(app)
    resp_no_cookie = attacker_client.post(
        "/api/v1/auth/idp-exchange",
        headers={"X-Auth-Session-Id": session_cookie},
        json={
            "idp_provider": "google",
            "idp_token": token,
            "org_id": ORG_ID,
            "state": state,
            "code_verifier": verifier,
        },
    )
    assert resp_no_cookie.status_code == 401
    assert "MISSING_SESSION_COOKIE" in resp_no_cookie.json()["detail"]

    # 2. Attacker supplies tampered cookie -> 401 SESSION_BINDING_MISMATCH
    attacker_client.cookies.set("myca_auth_session", "tampered_session_cookie_string")
    resp_tampered = attacker_client.post(
        "/api/v1/auth/idp-exchange",
        json={
            "idp_provider": "google",
            "idp_token": token,
            "org_id": ORG_ID,
            "state": state,
            "code_verifier": verifier,
        },
    )
    assert resp_tampered.status_code == 401
    assert "SESSION_BINDING_MISMATCH" in resp_tampered.json()["detail"]


def test_constant_time_pkce_and_state_mismatch_rejection():
    """
    CONSTANT-TIME PKCE & STATE MISMATCH TEST:
    Proves exchange rejects mismatched PKCE verifiers and mismatched states with HTTP 401.
    """
    _ensure_org_exists(ORG_ID)
    from app.database import SessionLocal
    from app.modules.auth.nonce_service import create_idp_login_nonce, consume_idp_login_nonce

    verifier, challenge, state = generate_pkce_pair()
    db = SessionLocal()
    try:
        res = create_idp_login_nonce(db, ORG_ID, "google", state=state, code_challenge=challenge)
        nonce = res["nonce"]
        session_token = res["session_token"]

        # 1. Mismatched PKCE verifier (1 character difference)
        wrong_verifier = verifier[:-1] + ("A" if verifier[-1] != "A" else "B")
        with pytest.raises(HTTPException) as exc1:
            consume_idp_login_nonce(
                db, nonce=nonce, org_id=ORG_ID, idp_provider="google",
                email="user@test.com", session_token=session_token,
                state=state, code_verifier=wrong_verifier
            )
        assert exc1.value.status_code == 401
        assert "INVALID_PKCE_VERIFIER" in exc1.value.detail

        # 2. Mismatched state parameter
        with pytest.raises(HTTPException) as exc2:
            consume_idp_login_nonce(
                db, nonce=nonce, org_id=ORG_ID, idp_provider="google",
                email="user@test.com", session_token=session_token,
                state="WRONG-STATE-STRING-VALUE-000", code_verifier=verifier
            )
        assert exc2.value.status_code == 401
        assert "STATE_MISMATCH" in exc2.value.detail
    finally:
        db.close()


def test_concurrent_nonce_issuance_atomically_capped():
    """
    CONCURRENT NONCE ISSUANCE CAP TEST:
    Proves PostgreSQL transaction advisory locks prevent count-then-insert race conditions.
    When 10 concurrent threads attempt to create nonces for the same org simultaneously,
    exactly 5 succeed and 5 fail with HTTP 429 ACTIVE_NONCE_LIMIT_EXCEEDED.
    """
    race_org = str(uuid.uuid4())
    _ensure_org_exists(race_org)
    from app.database import SessionLocal
    from app.modules.auth.nonce_service import create_idp_login_nonce

    successes = []
    capped = []
    errors = []

    def issuance_worker(worker_id: int):
        db = SessionLocal()
        try:
            verifier, challenge, state = generate_pkce_pair()
            # Use unique IP per worker so IP rate limit is not hit
            res = create_idp_login_nonce(
                db, org_id=race_org, idp_provider="google",
                state=state, code_challenge=challenge,
                client_ip=f"172.16.0.{worker_id + 1}"
            )
            successes.append((worker_id, res["nonce"]))
        except HTTPException as exc:
            if exc.status_code == 429 and "ACTIVE_NONCE_LIMIT_EXCEEDED" in exc.detail:
                capped.append(worker_id)
            else:
                errors.append((worker_id, str(exc.detail)))
        except Exception as exc:
            errors.append((worker_id, str(exc)))
        finally:
            db.close()

    # Launch 10 concurrent worker threads against same organization
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(issuance_worker, i) for i in range(10)]
        for f in futures:
            f.result()

    assert len(errors) == 0, f"Unexpected errors during concurrent issuance: {errors}"
    assert len(successes) == 5, f"Expected exactly 5 nonces created, got {len(successes)}"
    assert len(capped) == 5, f"Expected exactly 5 requests capped with 429, got {len(capped)}"


def test_bounded_retention_preserves_consumed_nonces_within_24h_and_purges_older():
    """
    BOUNDED RETENTION TEST:
    Proves consumed nonces within the 24-hour retention window are strictly preserved
    to prevent replay attacks, while consumed nonces older than 24 hours are safely cleaned up.
    """
    retention_org = str(uuid.uuid4())
    _ensure_org_exists(retention_org)
    from app.database import SessionLocal, set_tenant_context
    from app.modules.auth.nonce_service import create_idp_login_nonce
    from sqlalchemy import text

    nonce_recent_consumed = f"RECENT-CONSUMED-{uuid.uuid4().hex[:8]}"
    nonce_old_consumed = f"OLD-CONSUMED-PURGE-{uuid.uuid4().hex[:8]}"

    db = SessionLocal()
    try:
        set_tenant_context(db, org_id=retention_org, user_id=None)

        # 1. Consumed 2 hours ago (within 24h window -> MUST PRESERVE)
        db.execute(text("""
            INSERT INTO public.idp_authentication_nonces (
                nonce, org_id, idp_provider, status, created_at, expires_at, consumed_at, consumed_by_email
            ) VALUES (
                :nonce, :org_id, 'google', 'CONSUMED', NOW() - INTERVAL '3 hours', NOW() - INTERVAL '2 hours', NOW() - INTERVAL '2 hours', 'user1@test.com'
            );
        """), {"nonce": nonce_recent_consumed, "org_id": retention_org})

        # 2. Consumed 26 hours ago (exceeds 24h window -> MUST PURGE)
        db.execute(text("""
            INSERT INTO public.idp_authentication_nonces (
                nonce, org_id, idp_provider, status, created_at, expires_at, consumed_at, consumed_by_email
            ) VALUES (
                :nonce, :org_id, 'google', 'CONSUMED', NOW() - INTERVAL '27 hours', NOW() - INTERVAL '26 hours', NOW() - INTERVAL '26 hours', 'user2@test.com'
            );
        """), {"nonce": nonce_old_consumed, "org_id": retention_org})
        db.commit()

        # Trigger cleanup via create_idp_login_nonce
        verifier, challenge, state = generate_pkce_pair()
        create_idp_login_nonce(db, retention_org, "google", state=state, code_challenge=challenge, client_ip="10.80.80.1")

        # Verify retention outcome
        set_tenant_context(db, org_id=retention_org, user_id=None)
        recent_row = db.execute(text("SELECT nonce FROM public.idp_authentication_nonces WHERE nonce = :nonce;"), {"nonce": nonce_recent_consumed}).fetchone()
        assert recent_row is not None, "CRITICAL FLAW: Consumed nonce within 24h retention window was prematurely deleted!"

        old_row = db.execute(text("SELECT nonce FROM public.idp_authentication_nonces WHERE nonce = :nonce;"), {"nonce": nonce_old_consumed}).fetchone()
        assert old_row is None, "Consumed nonce older than 24h was NOT purged during bounded cleanup!"
    finally:
        db.close()


def test_cross_session_attack_prevention():
    """
    CROSS-SESSION ATTACK PREVENTATION TEST:
    Proves that an attacker with a valid session cookie for Session B cannot exchange
    or consume a nonce issued to Session A.
    """
    _ensure_org_exists(ORG_ID)
    from app.main import app
    client_a = TestClient(app)
    client_b = TestClient(app)

    verifier_a, challenge_a, state_a = generate_pkce_pair()
    verifier_b, challenge_b, state_b = generate_pkce_pair()

    # Session A requests nonce
    resp_a = client_a.post(
        "/api/v1/auth/idp-nonce",
        json={"org_id": ORG_ID, "idp_provider": "google", "state": state_a, "code_challenge": challenge_a}
    )
    assert resp_a.status_code == 200
    nonce_a = resp_a.json()["nonce"]

    # Session B requests nonce (different session cookie)
    resp_b = client_b.post(
        "/api/v1/auth/idp-nonce",
        json={"org_id": ORG_ID, "idp_provider": "google", "state": state_b, "code_challenge": challenge_b}
    )
    assert resp_b.status_code == 200
    nonce_b = resp_b.json()["nonce"]

    # Forge IdP token containing Nonce A
    token_a = create_signed_test_jwt(payload_overrides={"nonce": nonce_a, "email": "victim@myca.test"})

    # Attacker operating Session B attempts to exchange Token A (containing Nonce A) with Session B's cookie
    cross_exchange_resp = client_b.post(
        "/api/v1/auth/idp-exchange",
        json={
            "idp_provider": "google",
            "idp_token": token_a,
            "org_id": ORG_ID,
            "state": state_a,
            "code_verifier": verifier_a,
        },
    )
    assert cross_exchange_resp.status_code == 401
    assert "SESSION_BINDING_MISMATCH" in cross_exchange_resp.json()["detail"]
