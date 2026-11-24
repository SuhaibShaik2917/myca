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


def test_bearer_token_identity_rejects_spoofed_headers(monkeypatch):
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
    with pytest.raises(HTTPException) as excinfo:
        next(generator)
    assert excinfo.value.status_code == 403
    assert "Cross-tenant header tampering detected" in excinfo.value.detail

    # Matching headers are accepted
    generator_valid = dependencies.get_tenant_db(
        authorization=f"Bearer {token}",
        x_user_id=USER_ID,
        x_org_id=ORG_ID,
    )
    next(generator_valid)
    assert captured["user_id"] == USER_ID
    assert captured["org_id"] == ORG_ID


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

    verifier, challenge, state = generate_pkce_pair()
    client = TestClient(app)
    client.cookies.set("myca_auth_session", "test_cookie")
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

    verifier, challenge, state = generate_pkce_pair()
    client = TestClient(app)
    client.cookies.set("myca_auth_session", "test_cookie")
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

    verifier, challenge, state = generate_pkce_pair()
    client = TestClient(app)
    client.cookies.set("myca_auth_session", "test_cookie")
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

        # 1. Missing code_verifier -> 400 or 401
        with pytest.raises(HTTPException) as exc1:
            consume_idp_login_nonce(
                db, nonce=nonce, org_id=ORG_ID, idp_provider="google",
                email="pkce@test.com", session_token=session_token, state=state, code_verifier=None
            )
        assert exc1.value.status_code in (400, 401)
        assert "MISSING_PKCE_VERIFIER" in exc1.value.detail

        # 2. Invalid code_verifier -> 400 or 401
        with pytest.raises(HTTPException) as exc2:
            consume_idp_login_nonce(
                db, nonce=nonce, org_id=ORG_ID, idp_provider="google",
                email="pkce@test.com", session_token=session_token, state=state, code_verifier="wrong_verifier_string"
            )
        assert exc2.value.status_code in (400, 401)
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

    # 2. Short / invalid state (<16 chars) -> 400 or 422
    resp2 = client.post("/api/v1/auth/idp-nonce", json={"org_id": ORG_ID, "idp_provider": "google", "state": "short", "code_challenge": challenge})
    assert resp2.status_code in (400, 422)

    # 3. Missing code_challenge -> 422 Unprocessable Entity
    resp3 = client.post("/api/v1/auth/idp-nonce", json={"org_id": ORG_ID, "idp_provider": "google", "state": state})
    assert resp3.status_code == 422

    # 4. Invalid characters in code_challenge -> 400 or 422
    resp4 = client.post("/api/v1/auth/idp-nonce", json={"org_id": ORG_ID, "idp_provider": "google", "state": state, "code_challenge": "invalid+with+plus!@#$%^&*()"})
    assert resp4.status_code in (400, 422)


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


# =========================================================================
# 7. v1.0.16 Rolling Rate Limiter, PKCE Validation & Trusted Proxy Tests
# =========================================================================

def test_pkce_verifier_length_and_charset_enforcement():
    """
    PKCE VERIFIER LENGTH & CHARSET TEST (Finding 1):
    Proves code_verifier must be between 43 and 128 characters containing
    strictly RFC 7636 unreserved characters [A-Za-z0-9_.~-].
    Rejects short, long, and invalid-character verifiers with HTTP 400 or HTTP 422.
    """
    _ensure_org_exists(ORG_ID)
    from app.main import app
    from app.modules.auth.nonce_service import create_idp_login_nonce, consume_idp_login_nonce
    from app.database import SessionLocal
    client = TestClient(app)
    db = SessionLocal()

    try:
        # Issue valid nonce
        verifier, challenge, state = generate_pkce_pair()
        res = create_idp_login_nonce(db, ORG_ID, "google", state=state, code_challenge=challenge)
        nonce = res["nonce"]
        sess_token = res["session_token"]

        # 1. Reject verifier shorter than 43 chars (e.g. 42 chars)
        short_verifier = "A" * 42
        with pytest.raises(HTTPException) as exc1:
            consume_idp_login_nonce(
                db, nonce=nonce, org_id=ORG_ID, idp_provider="google",
                email="pkce@test.com", session_token=sess_token,
                state=state, code_verifier=short_verifier
            )
        assert exc1.value.status_code == 400
        assert "INVALID_PKCE_VERIFIER" in exc1.value.detail
        assert "between 43 and 128 characters" in exc1.value.detail

        # API endpoint schema validation for short verifier -> 422
        resp_short = client.post(
            "/api/v1/auth/idp-exchange",
            json={"idp_provider": "google", "idp_token": "mock", "org_id": ORG_ID, "state": state, "code_verifier": short_verifier}
        )
        assert resp_short.status_code == 422

        # 2. Reject verifier longer than 128 chars (e.g. 129 chars)
        long_verifier = "B" * 129
        with pytest.raises(HTTPException) as exc2:
            consume_idp_login_nonce(
                db, nonce=nonce, org_id=ORG_ID, idp_provider="google",
                email="pkce@test.com", session_token=sess_token,
                state=state, code_verifier=long_verifier
            )
        assert exc2.value.status_code == 400
        assert "INVALID_PKCE_VERIFIER" in exc2.value.detail

        resp_long = client.post(
            "/api/v1/auth/idp-exchange",
            json={"idp_provider": "google", "idp_token": "mock", "org_id": ORG_ID, "state": state, "code_verifier": long_verifier}
        )
        assert resp_long.status_code == 422

        # 3. Reject verifier with invalid characters (e.g. spaces, $, @, !, Unicode)
        invalid_chars_verifier = ("C" * 40) + " @$!"
        with pytest.raises(HTTPException) as exc3:
            consume_idp_login_nonce(
                db, nonce=nonce, org_id=ORG_ID, idp_provider="google",
                email="pkce@test.com", session_token=sess_token,
                state=state, code_verifier=invalid_chars_verifier
            )
        assert exc3.value.status_code == 400
        assert "INVALID_PKCE_VERIFIER" in exc3.value.detail
        assert "unreserved characters" in exc3.value.detail

        resp_invalid_chars = client.post(
            "/api/v1/auth/idp-exchange",
            json={"idp_provider": "google", "idp_token": "mock", "org_id": ORG_ID, "state": state, "code_verifier": invalid_chars_verifier}
        )
        assert resp_invalid_chars.status_code == 422

        # 4. Valid verifiers at boundaries (43 chars, 128 chars) with unreserved characters
        valid_43 = "A" * 40 + "_.~"
        digest_43 = hashlib.sha256(valid_43.encode("ascii")).digest()
        chal_43 = base64.urlsafe_b64encode(digest_43).decode("ascii").rstrip("=")
        state_43 = secrets.token_urlsafe(32)
        res_43 = create_idp_login_nonce(db, ORG_ID, "google", state=state_43, code_challenge=chal_43)
        assert consume_idp_login_nonce(
            db, nonce=res_43["nonce"], org_id=ORG_ID, idp_provider="google",
            email="pkce43@test.com", session_token=res_43["session_token"],
            state=state_43, code_verifier=valid_43
        ) is True

        valid_128 = ("A-Z_0-9.~" * 16)[:128]
        digest_128 = hashlib.sha256(valid_128.encode("ascii")).digest()
        chal_128 = base64.urlsafe_b64encode(digest_128).decode("ascii").rstrip("=")
        state_128 = secrets.token_urlsafe(32)
        res_128 = create_idp_login_nonce(db, ORG_ID, "google", state=state_128, code_challenge=chal_128)
        assert consume_idp_login_nonce(
            db, nonce=res_128["nonce"], org_id=ORG_ID, idp_provider="google",
            email="pkce128@test.com", session_token=res_128["session_token"],
            state=state_128, code_verifier=valid_128
        ) is True
    finally:
        db.close()


def test_rolling_60s_rate_limiter_boundary_burst_defense():
    """
    ROLLING 60-SECOND BOUNDARY BURST DEFENSE TEST (Finding 2):
    Proves that 20 requests at 10:00:59 followed by a request at 10:01:01 (across minute boundary)
    are strictly rate-limited with HTTP 429, proving genuine rolling 60-second enforcement.
    """
    _ensure_org_exists(ORG_ID)
    from app.database import SessionLocal
    from app.modules.auth.nonce_service import check_distributed_rate_limit
    from datetime import datetime, timezone, timedelta

    db = SessionLocal()
    test_ip = "198.51.100.99"
    test_org = str(uuid.uuid4())

    try:
        # Time t0: 10:00:59 UTC (1 second before calendar minute boundary)
        t0 = datetime(2026, 11, 24, 10, 0, 59, 0, tzinfo=timezone.utc)

        # 20 requests at t0 succeed
        for i in range(20):
            req_time = t0 + timedelta(milliseconds=i * 10)
            check_distributed_rate_limit(db, client_ip=test_ip, org_id=test_org, max_ip=20, max_org=30, current_time=req_time)

        # Request 21 at t1: 10:01:00.500 UTC (1.5 seconds later, crossing the minute boundary)
        # In a fixed calendar minute limiter this would succeed; in our rolling 60s limiter it MUST FAIL!
        t1 = t0 + timedelta(milliseconds=1500)
        with pytest.raises(HTTPException) as exc_boundary:
            check_distributed_rate_limit(db, client_ip=test_ip, org_id=test_org, max_ip=20, max_org=30, current_time=t1)
        assert exc_boundary.value.status_code == 429
        assert "RATE_LIMIT_EXCEEDED" in exc_boundary.value.detail
        assert "for IP" in exc_boundary.value.detail

        # Request 22 at t2: 10:02:00.000 UTC (61 seconds after t0)
        # The 20 requests from t0 have aged out of the rolling 60-second window -> MUST SUCCEED!
        t2 = t0 + timedelta(seconds=61)
        check_distributed_rate_limit(db, client_ip=test_ip, org_id=test_org, max_ip=20, max_org=30, current_time=t2)
    finally:
        db.close()


def test_rate_limiter_across_separate_db_sessions():
    """
    CROSS-SESSION RATE LIMITING TEST (Requirement 4):
    Proves rate limiting is shared across independent database sessions and processes via PostgreSQL.
    """
    _ensure_org_exists(ORG_ID)
    from app.database import SessionLocal
    from app.modules.auth.nonce_service import check_distributed_rate_limit

    db1 = SessionLocal()
    db2 = SessionLocal()
    test_ip = "203.0.113.88"
    test_org = str(uuid.uuid4())

    try:
        # Alternating requests between db1 and db2 for the same IP
        for i in range(10):
            check_distributed_rate_limit(db1, client_ip=test_ip, org_id=test_org, max_ip=20, max_org=30)
            check_distributed_rate_limit(db2, client_ip=test_ip, org_id=test_org, max_ip=20, max_org=30)

        # 21st request from db2 must be rate limited
        with pytest.raises(HTTPException) as exc:
            check_distributed_rate_limit(db2, client_ip=test_ip, org_id=test_org, max_ip=20, max_org=30)
        assert exc.value.status_code == 429
        assert "RATE_LIMIT_EXCEEDED" in exc.value.detail
    finally:
        db1.close()
        db2.close()


def test_concurrent_rate_limiting_across_threads():
    """
    CONCURRENT RATE LIMITING TEST:
    Proves concurrent worker threads hitting the same IP are atomically throttled to exactly 20.
    """
    _ensure_org_exists(ORG_ID)
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from app.database import SessionLocal
    from app.modules.auth.nonce_service import check_distributed_rate_limit

    test_ip = "192.0.2.77"
    test_org = str(uuid.uuid4())

    def worker_attempt():
        db = SessionLocal()
        try:
            check_distributed_rate_limit(db, client_ip=test_ip, org_id=test_org, max_ip=20, max_org=30)
            return "SUCCESS"
        except HTTPException as e:
            if e.status_code == 429:
                return "THROTTLED"
            return f"ERROR_{e.status_code}"
        finally:
            db.close()

    results = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(worker_attempt) for _ in range(25)]
        for fut in as_completed(futures):
            results.append(fut.result())

    success_count = results.count("SUCCESS")
    throttled_count = results.count("THROTTLED")
    assert success_count == 20, f"Expected exactly 20 successful requests, got {success_count}"
    assert throttled_count == 5, f"Expected exactly 5 throttled requests, got {throttled_count}"


def test_rate_limits_bounded_retention_and_cleanup():
    """
    BOUNDED RETENTION & CLEANUP TEST (Finding 3):
    Proves inactive auth_rate_limits records older than retention threshold (1 hour)
    are purged cleanly without interfering with active rolling rate limit enforcement.
    """
    _ensure_org_exists(ORG_ID)
    from app.database import SessionLocal
    from app.modules.auth.nonce_service import cleanup_expired_auth_rate_limits, check_distributed_rate_limit
    from sqlalchemy import text
    from datetime import datetime, timezone, timedelta

    db = SessionLocal()
    try:
        # 1. Insert stale rate limit record (updated 3 hours ago)
        old_key = "ip:10.99.99.99"
        old_time = datetime.now(timezone.utc) - timedelta(hours=3)
        db.execute(
            text("""
                INSERT INTO public.auth_rate_limits (rate_key, timestamps, updated_at)
                VALUES (:key, ARRAY[:old_time], :old_time)
                ON CONFLICT (rate_key) DO UPDATE
                SET timestamps = ARRAY[:old_time], updated_at = :old_time;
            """),
            {"key": old_key, "old_time": old_time},
        )
        db.commit()

        # 2. Insert active rate limit record (updated now)
        active_key = "ip:10.88.88.88"
        check_distributed_rate_limit(db, client_ip="10.88.88.88", org_id=ORG_ID)

        # 3. Run bounded retention cleanup (retention_hours=1)
        pruned_count = cleanup_expired_auth_rate_limits(db, retention_hours=1)
        assert pruned_count >= 1, "Expected at least 1 stale record to be cleaned up"

        # Verify old record was purged
        old_exists = db.execute(text("SELECT 1 FROM public.auth_rate_limits WHERE rate_key = :k;"), {"k": old_key}).scalar()
        assert old_exists is None, "Stale rate limit record older than 1 hour was not purged!"

        # Verify active record was preserved
        active_exists = db.execute(text("SELECT 1 FROM public.auth_rate_limits WHERE rate_key = :k;"), {"k": active_key}).scalar()
        assert active_exists is not None, "Active rate limit record was improperly purged!"

        # Verify active rate limiting continues to work normally
        check_distributed_rate_limit(db, client_ip="10.88.88.88", org_id=ORG_ID)
    finally:
        db.close()


def test_trusted_proxy_client_ip_extraction(monkeypatch):
    """
    TRUSTED PROXY & CLIENT IP SPOOFING DEFENSE TEST (Finding 1):
    Proves that:
    1. Direct connections from untrusted peers ignore spoofed X-Forwarded-For headers.
    2. Connections through a trusted proxy traverse X-Forwarded-For from right to left,
       selecting the first untrusted IP and completely ignoring spoofed addresses on the left.
    3. Multi-hop trusted proxy chains (e.g. CDN -> Ingress) traverse trusted hops until reaching the client.
    4. CIDR network definitions in TRUSTED_PROXIES are correctly honored.
    5. Malformed/malicious header injection attempts are safely rejected and sanitized.
    """
    from app.modules.auth.api import extract_client_ip
    from unittest.mock import MagicMock

    # 1. Direct connection from untrusted peer attempting to spoof X-Forwarded-For
    untrusted_req = MagicMock()
    untrusted_req.client.host = "203.0.113.195"  # Public untrusted client
    untrusted_req.headers = {"X-Forwarded-For": "1.1.1.1"}
    ip = extract_client_ip(untrusted_req)
    assert ip == "203.0.113.195", "CRITICAL FLAW: Untrusted peer successfully spoofed IP via X-Forwarded-For header!"

    # 2. Connection from trusted proxy (127.0.0.1) with spoofed X-Forwarded-For
    # Attacker prepended '1.1.1.1', but real client connecting to proxy was '198.51.100.42'
    # Walking right-to-left: '198.51.100.42' is untrusted -> selected. '1.1.1.1' is ignored.
    trusted_req = MagicMock()
    trusted_req.client.host = "127.0.0.1"
    trusted_req.headers = {"X-Forwarded-For": "1.1.1.1, 198.51.100.42"}
    ip = extract_client_ip(trusted_req)
    assert ip == "198.51.100.42", f"Expected real client IP 198.51.100.42 from right-to-left traversal, got {ip}"

    # 3. Multi-hop proxy chain with trusted intermediate proxy (10.0.0.1):
    # Attacker spoofs '1.1.1.1', connects from '198.51.100.42' through CDN '10.0.0.1' to Ingress '127.0.0.1'
    # Header: "1.1.1.1, 198.51.100.42, 10.0.0.1"
    chain_req = MagicMock()
    chain_req.client.host = "127.0.0.1"
    chain_req.headers = {"X-Forwarded-For": "1.1.1.1, 198.51.100.42, 10.0.0.1"}
    with monkeypatch.context() as m:
        m.setattr(settings, "TRUSTED_PROXIES", ["127.0.0.1", "10.0.0.1"])
        ip = extract_client_ip(chain_req)
        assert ip == "198.51.100.42", f"Expected untrusted client 198.51.100.42, got {ip}"

    # 4. CIDR network block in TRUSTED_PROXIES:
    cidr_req = MagicMock()
    cidr_req.client.host = "127.0.0.1"
    cidr_req.headers = {"X-Forwarded-For": "1.1.1.1, 203.0.113.88, 10.5.20.1"}
    with monkeypatch.context() as m:
        m.setattr(settings, "TRUSTED_PROXIES", ["127.0.0.1", "10.0.0.0/8"])
        ip = extract_client_ip(cidr_req)
        assert ip == "203.0.113.88", f"Expected untrusted client 203.0.113.88, got {ip}"

    # 5. Connection from trusted proxy with malicious/malformed header injection (FAILS CLOSED)
    malicious_req = MagicMock()
    malicious_req.client.host = "127.0.0.1"
    malicious_req.headers = {"X-Forwarded-For": "'; DROP TABLE users; --, 127.0.0.1"}
    with pytest.raises(HTTPException) as excinfo:
        extract_client_ip(malicious_req)
    assert excinfo.value.status_code == 400
    assert "MALFORMED_FORWARDED_HEADER" in excinfo.value.detail

    # 6. Connection from trusted proxy with all-trusted hops (FAILS CLOSED: never selects proxy)
    all_trusted_req = MagicMock()
    all_trusted_req.client.host = "127.0.0.1"
    all_trusted_req.headers = {"X-Forwarded-For": "127.0.0.1, ::1"}
    with pytest.raises(HTTPException) as exc_all_trusted:
        extract_client_ip(all_trusted_req)
    assert exc_all_trusted.value.status_code == 400
    assert "UNTRUSTED_CLIENT_NOT_FOUND" in exc_all_trusted.value.detail

    # 7. Connection from trusted proxy missing X-Forwarded-For (FAILS CLOSED)
    missing_req = MagicMock()
    missing_req.client.host = "127.0.0.1"
    missing_req.headers = {}
    with pytest.raises(HTTPException) as exc_missing:
        extract_client_ip(missing_req)
    assert exc_missing.value.status_code == 400
    assert "MISSING_FORWARDED_HEADER" in exc_missing.value.detail


def test_rate_limiter_explicit_acceptance_boolean_identical_timestamps():
    """
    RATE LIMITER EXPLICIT BOOLEAN ACCEPTANCE TEST (Finding 3):
    Proves that the rate limiter evaluates acceptance via explicit boolean SQL return,
    preventing microsecond timestamp collisions or identical timestamps from misclassifying
    a rejected request as accepted.
    """
    _ensure_org_exists(ORG_ID)
    from app.database import SessionLocal
    from app.modules.auth.nonce_service import check_distributed_rate_limit, reset_auth_rate_limits
    from datetime import datetime, timezone

    db = SessionLocal()
    try:
        reset_auth_rate_limits(db)
        test_ip = "198.51.100.99"
        test_org = str(uuid.uuid4())
        fixed_now = datetime(2026, 11, 24, 12, 0, 0, tzinfo=timezone.utc)
        limit = 3

        # First 3 requests with the EXACT same timestamp must be accepted
        for i in range(limit):
            check_distributed_rate_limit(
                db, client_ip=test_ip, org_id=test_org, max_ip=limit, max_org=10, current_time=fixed_now
            )

        # 4th request with the EXACT same timestamp MUST be rejected with HTTP 429
        # (Even though fixed_now is already in the array, explicit boolean says False!)
        with pytest.raises(HTTPException) as exc_info:
            check_distributed_rate_limit(
                db, client_ip=test_ip, org_id=test_org, max_ip=limit, max_org=10, current_time=fixed_now
            )
        assert exc_info.value.status_code == 429
        assert "RATE_LIMIT_EXCEEDED" in exc_info.value.detail
    finally:
        db.close()


@pytest.mark.asyncio
async def test_rate_limit_scheduled_cleanup_operational():
    """
    RATE LIMIT SCHEDULED CLEANUP OPERATIONAL TEST (Finding 2):
    Proves that rate-limit cleanup is fully operational across all execution paths:
    1. Direct operational execution via run_rate_limit_cleanup_job().
    2. Continuous background periodic task run_periodic_rate_limit_cleanup() with stop_event.
    3. In-flow opportunistic scheduling via schedule_rate_limit_cleanup_if_due().
    4. Bounded retention verification: expired records are purged while active records remain intact.
    """
    _ensure_org_exists(ORG_ID)
    from app.database import SessionLocal
    from app.modules.auth.nonce_service import (
        cleanup_expired_auth_rate_limits,
        run_rate_limit_cleanup_job,
        run_periodic_rate_limit_cleanup,
        schedule_rate_limit_cleanup_if_due,
        check_distributed_rate_limit,
        reset_auth_rate_limits,
    )
    from sqlalchemy import text
    from datetime import datetime, timezone, timedelta
    from fastapi import BackgroundTasks
    import asyncio

    db = SessionLocal()
    try:
        reset_auth_rate_limits(db)

        # 1. Insert stale record (2 hours old) and active record (current)
        stale_key = "ip:10.77.77.77"
        active_key = "ip:10.88.88.88"
        two_hours_ago = datetime.now(timezone.utc) - timedelta(hours=2)

        db.execute(
            text("""
                INSERT INTO public.auth_rate_limits (rate_key, timestamps, updated_at)
                VALUES (:k, ARRAY[:t], :t);
            """),
            {"k": stale_key, "t": two_hours_ago},
        )
        db.commit()

        # Insert active record
        check_distributed_rate_limit(db, client_ip="10.88.88.88", org_id=ORG_ID)

        # Verify both records exist initially
        assert db.execute(text("SELECT 1 FROM public.auth_rate_limits WHERE rate_key = :k;"), {"k": stale_key}).scalar() == 1
        assert db.execute(text("SELECT 1 FROM public.auth_rate_limits WHERE rate_key = :k;"), {"k": active_key}).scalar() == 1

        # 2. Test operational runner run_rate_limit_cleanup_job
        pruned = run_rate_limit_cleanup_job(retention_hours=1)
        assert pruned >= 1, f"Expected at least 1 record pruned, got {pruned}"

        # Verify stale record deleted, active record retained
        assert db.execute(text("SELECT 1 FROM public.auth_rate_limits WHERE rate_key = :k;"), {"k": stale_key}).scalar() is None
        assert db.execute(text("SELECT 1 FROM public.auth_rate_limits WHERE rate_key = :k;"), {"k": active_key}).scalar() == 1

        # 3. Test opportunistic scheduling with BackgroundTasks
        # Insert another stale record
        stale_key_2 = "ip:10.66.66.66"
        db.execute(
            text("""
                INSERT INTO public.auth_rate_limits (rate_key, timestamps, updated_at)
                VALUES (:k, ARRAY[:t], :t);
            """),
            {"k": stale_key_2, "t": two_hours_ago},
        )
        db.commit()

        bg_tasks = BackgroundTasks()
        dispatched = schedule_rate_limit_cleanup_if_due(bg_tasks, min_interval_seconds=0, retention_hours=1)
        assert dispatched is True, "Expected cleanup dispatch to succeed when due"
        assert len(bg_tasks.tasks) == 1, "Expected 1 cleanup task to be scheduled on BackgroundTasks"
        # Execute the background task
        await bg_tasks()
        assert db.execute(text("SELECT 1 FROM public.auth_rate_limits WHERE rate_key = :k;"), {"k": stale_key_2}).scalar() is None

        # 4. Test async continuous periodic runner with stop_event
        stale_key_3 = "ip:10.55.55.55"
        db.execute(
            text("""
                INSERT INTO public.auth_rate_limits (rate_key, timestamps, updated_at)
                VALUES (:k, ARRAY[:t], :t);
            """),
            {"k": stale_key_3, "t": two_hours_ago},
        )
        db.commit()

        stop_ev = asyncio.Event()
        cleanup_task = asyncio.create_task(
            run_periodic_rate_limit_cleanup(interval_seconds=10, retention_hours=1, stop_event=stop_ev)
        )
        # Give worker a brief moment to run iteration
        await asyncio.sleep(0.1)
        stop_ev.set()
        await cleanup_task

        assert db.execute(text("SELECT 1 FROM public.auth_rate_limits WHERE rate_key = :k;"), {"k": stale_key_3}).scalar() is None
    finally:
        db.close()


def test_idp_nonce_triggers_background_cleanup():
    """
    IN-FLOW CLEANUP TRIGGER TEST:
    Proves that calling /api/v1/auth/idp-nonce through TestClient executes normally
    and triggers background cleanup without raising exceptions or impacting response.
    """
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
    assert "myca_auth_session" in response.cookies


def test_trusted_proxies_startup_validation():
    """
    STARTUP VALIDATION TEST:
    Proves that invalid, empty, or overly broad wildcard TRUSTED_PROXIES configurations
    fail closed at application startup with a clear configuration error.
    """
    from app.config import Settings
    import pytest

    # 1. Empty list rejected
    with pytest.raises(ValueError) as exc1:
        Settings(ENVIRONMENT="test", TRUSTED_PROXIES=[])
    assert "TRUSTED_PROXIES cannot be empty" in str(exc1.value)

    # 2. Wildcard '*' rejected
    with pytest.raises(ValueError) as exc2:
        Settings(ENVIRONMENT="test", TRUSTED_PROXIES=["*"])
    assert "Overly permissive wildcard proxy" in str(exc2.value)

    # 3. Overly broad '0.0.0.0/0' rejected
    with pytest.raises(ValueError) as exc3:
        Settings(ENVIRONMENT="test", TRUSTED_PROXIES=["0.0.0.0/0"])
    assert "Overly permissive wildcard proxy" in str(exc3.value) or "Overly broad network" in str(exc3.value)

    # 4. Invalid IP syntax rejected
    with pytest.raises(ValueError) as exc4:
        Settings(ENVIRONMENT="test", TRUSTED_PROXIES=["not-an-ip"])
    assert "Invalid IP address or CIDR" in str(exc4.value)


def test_concurrent_first_time_rate_limit_keys_across_connections():
    """
    CONCURRENT FIRST-TIME KEY CREATION TEST:
    Proves that concurrent database connections racing against brand-new IP and Organization
    keys (that do not exist in public.auth_rate_limits) execute atomically via ON CONFLICT DO NOTHING
    and row-level locking without duplicate key errors, deadlocks, or quota breaches.
    """
    _ensure_org_exists(ORG_ID)
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from app.database import SessionLocal
    from app.modules.auth.nonce_service import check_distributed_rate_limit
    import random

    # Generate unique brand-new IP and Org that definitely do not exist in DB
    new_ip = f"198.51.100.{random.randint(100, 250)}"
    new_org = str(uuid.uuid4())
    limit = 10
    total_workers = 15

    def worker_attempt():
        db = SessionLocal()
        try:
            check_distributed_rate_limit(db, client_ip=new_ip, org_id=new_org, max_ip=limit, max_org=limit)
            return "SUCCESS"
        except HTTPException as e:
            if e.status_code == 429:
                return "THROTTLED"
            return f"ERROR_{e.status_code}"
        finally:
            db.close()

    results = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(worker_attempt) for _ in range(total_workers)]
        for fut in as_completed(futures):
            results.append(fut.result())

    success_count = results.count("SUCCESS")
    throttled_count = results.count("THROTTLED")
    assert success_count == limit, f"Expected exactly {limit} successes on first-time key, got {success_count}"
    assert throttled_count == (total_workers - limit), f"Expected exactly {total_workers - limit} throttles, got {throttled_count}"


def test_ip_and_organization_quota_interaction_transaction_rollback():
    """
    QUOTA INTERACTION & TRANSACTION ROLLBACK TEST:
    Proves that when a request is accepted by the IP quota but rejected by the Organization quota:
    1. The transaction executes an atomic rollback.
    2. The client IP's timestamps are NOT incremented for the denied request.
    3. The client IP is not penalized for an organization-wide quota exhaustion.
    """
    _ensure_org_exists(ORG_ID)
    from app.database import SessionLocal
    from app.modules.auth.nonce_service import check_distributed_rate_limit, reset_auth_rate_limits
    from sqlalchemy import text

    db = SessionLocal()
    try:
        reset_auth_rate_limits(db)
        test_ip = "192.0.2.88"
        test_org = str(uuid.uuid4())

        # Configure limits: IP max = 5, Org max = 2
        ip_limit = 5
        org_limit = 2

        # Request 1: IP (1/5), Org (1/2) -> Accepted
        check_distributed_rate_limit(db, client_ip=test_ip, org_id=test_org, max_ip=ip_limit, max_org=org_limit)

        # Request 2: IP (2/5), Org (2/2) -> Accepted
        check_distributed_rate_limit(db, client_ip=test_ip, org_id=test_org, max_ip=ip_limit, max_org=org_limit)

        # Verify initial counts: IP has 2 timestamps, Org has 2 timestamps
        ip_count_pre = db.execute(
            text("SELECT cardinality(timestamps) FROM public.auth_rate_limits WHERE rate_key = :k;"),
            {"k": f"ip:{test_ip}"}
        ).scalar()
        org_count_pre = db.execute(
            text("SELECT cardinality(timestamps) FROM public.auth_rate_limits WHERE rate_key = :k;"),
            {"k": f"org:{test_org}"}
        ).scalar()
        assert ip_count_pre == 2
        assert org_count_pre == 2

        # Request 3: IP would be (3/5) [OK], but Org is (3/2) [EXCEEDED!]
        # Must raise HTTP 429
        with pytest.raises(HTTPException) as exc_info:
            check_distributed_rate_limit(db, client_ip=test_ip, org_id=test_org, max_ip=ip_limit, max_org=org_limit)
        assert exc_info.value.status_code == 429
        assert "exceeded for organization" in exc_info.value.detail

        # TRANSACTION VERIFICATION:
        # Verify that IP timestamps count was ROLLED BACK and remains strictly 2 (not 3!)
        ip_count_post = db.execute(
            text("SELECT cardinality(timestamps) FROM public.auth_rate_limits WHERE rate_key = :k;"),
            {"k": f"ip:{test_ip}"}
        ).scalar()
        org_count_post = db.execute(
            text("SELECT cardinality(timestamps) FROM public.auth_rate_limits WHERE rate_key = :k;"),
            {"k": f"org:{test_org}"}
        ).scalar()

        assert ip_count_post == 2, (
            f"TRANSACTION DEFECT: IP count was incremented to {ip_count_post} despite Organization rejection! "
            "IP update must be rolled back."
        )
        assert org_count_post == 2
    finally:
        db.close()


def test_cleanup_telemetry_metrics_and_retry_resilience(monkeypatch):
    """
    CLEANUP TELEMETRY & RETRY RESILIENCE TEST:
    Proves that:
    1. Operational metrics accurately record successful runs, pruned counts, and failure tracking.
    2. Transient database errors trigger automatic retries with exponential backoff before failing closed.
    3. Multi-worker concurrent cleanup invocations execute safely without deadlocks or conflicting state.
    """
    from app.modules.auth.nonce_service import (
        run_rate_limit_cleanup_job,
        get_rate_limit_cleanup_metrics,
        cleanup_metrics,
    )
    from concurrent.futures import ThreadPoolExecutor
    import app.database as app_db

    # 1. Reset metrics baseline
    cleanup_metrics.reset()
    baseline = get_rate_limit_cleanup_metrics()
    assert baseline["total_runs"] == 0
    assert baseline["total_failures"] == 0

    # 2. Test successful run
    pruned = run_rate_limit_cleanup_job(retention_hours=1)
    metrics = get_rate_limit_cleanup_metrics()
    assert metrics["total_runs"] == 1
    assert metrics["total_failures"] == 0
    assert metrics["last_run_timestamp"] is not None

    # 3. Test retry on transient failure
    call_count = 0

    def mock_failing_session():
        nonlocal call_count
        call_count += 1
        raise RuntimeError(f"Simulated transient DB connection failure attempt {call_count}")

    monkeypatch.setattr(app_db, "SessionLocal", mock_failing_session)
    pruned_failed = run_rate_limit_cleanup_job(retention_hours=1, max_retries=3, retry_delay=0.01)
    assert pruned_failed == 0
    assert call_count == 3, f"Expected exactly 3 retry attempts, got {call_count}"

    failed_metrics = get_rate_limit_cleanup_metrics()
    assert failed_metrics["total_failures"] == 1
    assert "Simulated transient DB connection failure" in failed_metrics["last_error"]

    # Restore session
    monkeypatch.undo()

    # 4. Multi-worker concurrent cleanup non-interference
    def concurrent_cleanup_worker():
        return run_rate_limit_cleanup_job(retention_hours=1)

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(concurrent_cleanup_worker) for _ in range(5)]
        results = [f.result() for f in futures]
    assert len(results) == 5

