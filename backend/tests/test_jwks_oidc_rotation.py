"""Unit tests for OIDC Discovery, remote JWKS caching, key rotation, and fail-closed verification."""

import base64
import json
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from jose import jwt

from app.config import Settings, settings
from app.core.jwks import JWKSUnavailableError, default_jwks_client
from app.modules.auth.idp_verifier import validate_idp_token


def _int_to_base64url(val: int) -> str:
    byte_len = (val.bit_length() + 7) // 8
    raw = val.to_bytes(byte_len, "big")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _generate_rsa_key_and_jwk(kid: str, alg: str = "RS256", use: str = "sig"):
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv_pem = priv.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("utf-8")
    nums = priv.public_key().public_numbers()
    jwk_dict = {
        "kty": "RSA",
        "kid": kid,
        "use": use,
        "alg": alg,
        "n": _int_to_base64url(nums.n),
        "e": _int_to_base64url(nums.e),
    }
    return priv_pem, jwk_dict


class _MockOIDCServer:
    """Local HTTP test server serving mutable discovery + JWKS documents."""

    def __init__(self):
        self.jwks_payload = {"keys": []}
        self.discovery_issuer = "https://accounts.google.com"
        self.status_code = 200
        self.jwks_hits = 0
        parent = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                path = urlparse(self.path).path
                if parent.status_code != 200:
                    self.send_response(parent.status_code)
                    self.end_headers()
                    return
                if path == "/.well-known/openid-configuration":
                    body = json.dumps(
                        {
                            "issuer": parent.discovery_issuer,
                            "jwks_uri": f"http://127.0.0.1:{self.server.server_port}/jwks.json",
                        }
                    ).encode("utf-8")
                elif path == "/jwks.json":
                    parent.jwks_hits += 1
                    body = json.dumps(parent.jwks_payload).encode("utf-8")
                else:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):
                return

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def jwks_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/jwks.json"

    @property
    def discovery_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/.well-known/openid-configuration"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture(autouse=True)
def clear_jwks_state(monkeypatch):
    default_jwks_client.clear_cache()
    for var in (
        "MYCA_IDP_JWKS_URL",
        "MYCA_IDP_DISCOVERY_URL",
        "MYCA_IDP_PUBLIC_KEY",
        "MYCA_IDP_KEY_ID",
        "MYCA_IDP_ISSUER",
        "MYCA_IDP_AUDIENCE",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(settings, "MYCA_IDP_JWKS_URL", None)
    monkeypatch.setattr(settings, "MYCA_IDP_DISCOVERY_URL", None)
    monkeypatch.setattr(settings, "MYCA_IDP_PUBLIC_KEY", None)
    monkeypatch.setattr(settings, "MYCA_IDP_KEY_ID", None)
    monkeypatch.setattr(settings, "MYCA_IDP_ISSUER", None)
    monkeypatch.setattr(settings, "MYCA_IDP_AUDIENCE", None)
    yield
    default_jwks_client.clear_cache()


def _sign_idp_jwt(priv_pem: str, kid: str, *, iss="https://accounts.google.com", aud="myca-client", alg="RS256", extra=None):
    now = datetime.now(timezone.utc)
    payload = {
        "iss": iss,
        "aud": aud,
        "sub": "user-123",
        "email": "alice@example.com",
        "email_verified": True,
        "nonce": "nonce-abc-123",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=10)).timestamp()),
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, priv_pem, algorithm=alg, headers={"kid": kid, "alg": alg})


def test_jwks_verification_and_key_rotation_rollover(monkeypatch):
    priv1, jwk1 = _generate_rsa_key_and_jwk("key-v1")
    priv2, jwk2 = _generate_rsa_key_and_jwk("key-v2")

    with _MockOIDCServer() as srv:
        srv.jwks_payload = {"keys": [jwk1]}
        monkeypatch.setattr(settings, "MYCA_IDP_DISCOVERY_URL", srv.discovery_url)
        monkeypatch.setattr(settings, "MYCA_IDP_ISSUER", "https://accounts.google.com")
        monkeypatch.setattr(settings, "MYCA_IDP_AUDIENCE", "myca-client")

        tok1 = _sign_idp_jwt(priv1, "key-v1")
        claims1 = validate_idp_token("google", tok1, env="test", expected_nonce="nonce-abc-123")
        assert claims1["email"] == "alice@example.com"
        assert srv.jwks_hits == 1

        # Second call with same kid hits cache without network refetch
        validate_idp_token("google", tok1, env="test", expected_nonce="nonce-abc-123")
        assert srv.jwks_hits == 1

        # Rotate key on IdP JWKS endpoint; force refresh by resetting last_refresh_attempt cooldown
        srv.jwks_payload = {"keys": [jwk1, jwk2]}
        for cache_entry in default_jwks_client._jwks_cache.values():
            cache_entry["last_refresh_attempt"] -= 60.0

        tok2 = _sign_idp_jwt(priv2, "key-v2")
        claims2 = validate_idp_token("google", tok2, env="test", expected_nonce="nonce-abc-123")
        assert claims2["email"] == "alice@example.com"
        assert srv.jwks_hits == 2

        # Unknown kid returns 401 UNTRUSTED_KEY_ID
        for cache_entry in default_jwks_client._jwks_cache.values():
            cache_entry["last_refresh_attempt"] -= 60.0
        tok_unknown = _sign_idp_jwt(priv2, "unknown-kid")
        with pytest.raises(HTTPException) as exc:
            validate_idp_token("google", tok_unknown, env="test")
        assert exc.value.status_code == 401
        assert "UNTRUSTED_KEY_ID" in exc.value.detail


def test_jwks_rejects_symmetric_oct_keys_and_unavailable_endpoint(monkeypatch):
    with _MockOIDCServer() as srv:
        # JWKS containing only symmetric oct key must fail closed with 503
        srv.jwks_payload = {"keys": [{"kty": "oct", "kid": "sym-1", "k": "secret"}]}
        priv, _ = _generate_rsa_key_and_jwk("sym-1")
        monkeypatch.setattr(settings, "MYCA_IDP_JWKS_URL", srv.jwks_url)
        monkeypatch.setattr(settings, "MYCA_IDP_ISSUER", "https://accounts.google.com")
        monkeypatch.setattr(settings, "MYCA_IDP_AUDIENCE", "myca-client")

        tok = _sign_idp_jwt(priv, "sym-1")
        with pytest.raises(HTTPException) as exc_sym:
            validate_idp_token("google", tok, env="test")
        assert exc_sym.value.status_code == 503

        # Upstream 500 from JWKS fails closed with 503
        default_jwks_client.clear_cache()
        srv.status_code = 500
        with pytest.raises(HTTPException) as exc_down:
            validate_idp_token("google", tok, env="test")
        assert exc_down.value.status_code == 503


def test_production_requires_https_jwks_and_tenant_scoped_issuer():
    # Settings rejects http:// JWKS in production
    with pytest.raises(ValueError, match="https://"):
        Settings(
            ENVIRONMENT="production",
            SECRET_KEY="prod-secret-key-at-least-32-characters-long-valid",
            GSTR3B_REVIEW_ATTESTATION_KEY="prod-gstr3b-attest-key-distinct-at-least-32-chars",
            DATABASE_URL="postgresql+psycopg://app_service_user:strongpw@db.internal:5432/myca",
            DATABASE_WORKER_URL="postgresql+psycopg://worker_service_user:strongpw@db.internal:5432/myca",
            MYCA_IDP_JWKS_URL="http://idp.internal/jwks.json",
        )

    # validate_idp_token fails closed in production for missing okta issuer and http JWKS
    priv, _ = _generate_rsa_key_and_jwk("k1")
    tok = _sign_idp_jwt(priv, "k1", iss="https://example.okta.com")
    with pytest.raises(HTTPException) as exc_okta:
        validate_idp_token("okta", tok, env="production")
    assert exc_okta.value.status_code == 503

    with pytest.raises(JWKSUnavailableError):
        default_jwks_client.get_signing_key("http://127.0.0.1:9999/jwks.json", kid="k1", require_https=True)
