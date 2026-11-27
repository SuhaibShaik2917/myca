"""Unit tests for OIDC Discovery, remote JWKS caching, key rotation, and fail-closed verification."""

import base64
import hashlib
import json
import secrets
import threading
import uuid
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from fastapi.testclient import TestClient
from jose import jwt
from sqlalchemy import text

from app.config import Settings, settings
from app.core.jwks import JWKSUnavailableError, default_jwks_client
from app.main import app
from app.modules.auth.idp_verifier import validate_idp_token
from tests.test_db_config import get_admin_session


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
    """Local HTTP test server serving mutable discovery, JWKS, and token endpoints."""

    def __init__(self):
        self.jwks_payload = {"keys": []}
        self.discovery_issuer = "https://accounts.google.com"
        self.discovery_override = None
        self.status_code = 200
        self.jwks_hits = 0
        self.token_hits = 0
        self.issued_codes = {}
        self.token_status = 200
        parent = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                path = urlparse(self.path).path
                if parent.status_code != 200:
                    self.send_response(parent.status_code)
                    self.end_headers()
                    return
                if path == "/.well-known/openid-configuration":
                    doc = (
                        parent.discovery_override
                        if parent.discovery_override is not None
                        else {
                            "issuer": parent.discovery_issuer,
                            "jwks_uri": f"http://127.0.0.1:{self.server.server_port}/jwks.json",
                            "token_endpoint": f"http://127.0.0.1:{self.server.server_port}/token",
                        }
                    )
                    body = json.dumps(doc).encode("utf-8")
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

            def do_POST(self):
                path = urlparse(self.path).path
                length = int(self.headers.get("Content-Length", "0"))
                raw_body = self.rfile.read(length).decode("utf-8")
                params = {k: v[0] for k, v in parse_qs(raw_body).items()}
                if path != "/token":
                    self.send_response(404)
                    self.end_headers()
                    return
                parent.token_hits += 1
                if parent.token_status != 200:
                    self.send_response(parent.token_status)
                    self.end_headers()
                    return
                entry = parent.issued_codes.get(params.get("code", ""))
                if not entry or params.get("grant_type") != "authorization_code":
                    self.send_response(400)
                    self.end_headers()
                    return
                # Verify PKCE challenge & redirect_uri at IdP token endpoint
                verifier = params.get("code_verifier", "")
                computed = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")
                if computed != entry["code_challenge"] or params.get("redirect_uri") != entry["redirect_uri"]:
                    self.send_response(400)
                    self.end_headers()
                    return
                body = json.dumps({"id_token": entry["id_token"], "token_type": "Bearer"}).encode("utf-8")
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
        "MYCA_IDP_TOKEN_URL",
        "MYCA_IDP_REDIRECT_URIS",
        "MYCA_IDP_PUBLIC_KEY",
        "MYCA_IDP_KEY_ID",
        "MYCA_IDP_ISSUER",
        "MYCA_IDP_AUDIENCE",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(settings, "MYCA_IDP_JWKS_URL", None)
    monkeypatch.setattr(settings, "MYCA_IDP_DISCOVERY_URL", None)
    monkeypatch.setattr(settings, "MYCA_IDP_TOKEN_URL", None)
    monkeypatch.setattr(settings, "MYCA_IDP_REDIRECT_URIS", [])
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
        srv.discovery_issuer = f"http://127.0.0.1:{srv.port}"
        monkeypatch.setattr(settings, "MYCA_IDP_DISCOVERY_URL", srv.discovery_url)
        monkeypatch.setattr(settings, "MYCA_IDP_ISSUER", srv.discovery_issuer)
        monkeypatch.setattr(settings, "MYCA_IDP_AUDIENCE", "myca-client")

        tok1 = _sign_idp_jwt(priv1, "key-v1", iss=srv.discovery_issuer)
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

        tok2 = _sign_idp_jwt(priv2, "key-v2", iss=srv.discovery_issuer)
        claims2 = validate_idp_token("google", tok2, env="test", expected_nonce="nonce-abc-123")
        assert claims2["email"] == "alice@example.com"
        assert srv.jwks_hits == 2

        # Unknown kid returns 401 UNTRUSTED_KEY_ID
        for cache_entry in default_jwks_client._jwks_cache.values():
            cache_entry["last_refresh_attempt"] -= 60.0
        tok_unknown = _sign_idp_jwt(priv2, "unknown-kid", iss=srv.discovery_issuer)
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


def test_discovery_rejects_missing_empty_mismatched_issuer_and_invalid_keys():
    priv, jwk_sig = _generate_rsa_key_and_jwk("k-sig")
    _, jwk_enc = _generate_rsa_key_and_jwk("k-enc", use="enc")
    _, jwk_hs = _generate_rsa_key_and_jwk("k-hs", alg="HS256")

    with _MockOIDCServer() as srv:
        base_jwks = f"http://127.0.0.1:{srv.port}/jwks.json"

        # 1. Missing issuer in discovery document
        srv.discovery_override = {"jwks_uri": base_jwks}
        default_jwks_client.clear_cache()
        with pytest.raises(JWKSUnavailableError, match="issuer"):
            default_jwks_client.resolve_discovery(srv.discovery_url, require_https=False)

        # 2. Empty issuer in discovery document
        srv.discovery_override = {"issuer": "   ", "jwks_uri": base_jwks}
        default_jwks_client.clear_cache()
        with pytest.raises(JWKSUnavailableError, match="issuer"):
            default_jwks_client.resolve_discovery(srv.discovery_url, require_https=False)

        # 3. Mismatched issuer against expected MYCA_IDP_ISSUER
        srv.discovery_override = {"issuer": "https://evil-idp.example.org", "jwks_uri": base_jwks}
        default_jwks_client.clear_cache()
        with pytest.raises(JWKSUnavailableError, match="does not match expected"):
            default_jwks_client.resolve_discovery(
                srv.discovery_url, require_https=False, expected_issuer="https://accounts.google.com"
            )

        # 4. Invalid JWKS URI (non-loopback HTTP)
        srv.discovery_override = {"issuer": "https://accounts.google.com", "jwks_uri": "http://evil-idp.example.org/jwks.json"}
        default_jwks_client.clear_cache()
        with pytest.raises(JWKSUnavailableError):
            default_jwks_client.resolve_discovery(srv.discovery_url, require_https=False)

        # 5. Encryption-only (use=enc) and HS256 keys inside JWKS must be rejected
        srv.jwks_payload = {"keys": [jwk_enc, jwk_hs, {"kty": "RSA", "kid": "broken"}]}
        default_jwks_client.clear_cache()
        with pytest.raises(JWKSUnavailableError, match="no valid asymmetric signing keys"):
            default_jwks_client.get_signing_key(srv.jwks_url, kid="k-enc", require_https=False)

        # Verify valid signing key works cleanly
        srv.jwks_payload = {"keys": [jwk_sig]}
        default_jwks_client.clear_cache()
        assert default_jwks_client.get_signing_key(srv.jwks_url, kid="k-sig", require_https=False)["kid"] == "k-sig"


def test_oidc_authorization_code_pkce_end_to_end_and_security_failures(monkeypatch):
    import random

    priv, jwk_sig = _generate_rsa_key_and_jwk("code-k1")
    org_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    email = f"oidc_user_{uuid.uuid4().hex[:8]}@example.com"
    pan = f"AAACO{random.randint(1000, 9999)}Z"
    admin_db = get_admin_session()
    try:
        admin_db.execute(
            text("INSERT INTO public.organizations (id, legal_name, pan) VALUES (:id, 'OIDC Org', :pan)"),
            {"id": org_id, "pan": pan},
        )
        admin_db.execute(
            text("INSERT INTO public.global_users (id, email, full_name, is_active) VALUES (:uid, :em, 'OIDC User', true)"),
            {"uid": user_id, "em": email},
        )
        admin_db.execute(
            text("INSERT INTO public.organization_memberships (id, org_id, user_id, role, status) VALUES (:mid, :oid, :uid, 'accountant', 'active')"),
            {"mid": str(uuid.uuid4()), "oid": org_id, "uid": user_id},
        )
        admin_db.execute(text("DELETE FROM public.auth_rate_limits"))
        admin_db.commit()
    finally:
        admin_db.close()

    with _MockOIDCServer() as srv:
        srv.discovery_issuer = f"http://127.0.0.1:{srv.port}"
        srv.jwks_payload = {"keys": [jwk_sig]}
        redirect_uri = "http://localhost:5173/"
        monkeypatch.setattr(settings, "MYCA_IDP_DISCOVERY_URL", srv.discovery_url)
        monkeypatch.setattr(settings, "MYCA_IDP_ISSUER", srv.discovery_issuer)
        monkeypatch.setattr(settings, "MYCA_IDP_AUDIENCE", "myca-web-client")
        monkeypatch.setattr(settings, "MYCA_IDP_REDIRECT_URIS", [redirect_uri])

        client = TestClient(app)
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")
        state = secrets.token_urlsafe(24)

        nonce_resp = client.post(
            "/api/v1/auth/idp-nonce",
            json={"org_id": org_id, "idp_provider": "google", "state": state, "code_challenge": challenge},
        )
        assert nonce_resp.status_code == 200
        issued_nonce = nonce_resp.json()["nonce"]
        assert "myca_auth_session" in client.cookies

        auth_code = f"code_{secrets.token_urlsafe(16)}"
        id_token = _sign_idp_jwt(
            priv,
            "code-k1",
            iss=srv.discovery_issuer,
            aud="myca-web-client",
            extra={"email": email, "email_verified": True, "nonce": issued_nonce},
        )
        srv.issued_codes[auth_code] = {
            "code_challenge": challenge,
            "redirect_uri": redirect_uri,
            "id_token": id_token,
        }

        # Negative 1: Wrong PKCE verifier rejected BEFORE outbound /token call
        bad_verifier = secrets.token_urlsafe(48)
        r_bad_pkce = client.post(
            "/api/v1/auth/idp-code-exchange",
            json={
                "idp_provider": "google",
                "org_id": org_id,
                "code": auth_code,
                "state": state,
                "code_verifier": bad_verifier,
                "redirect_uri": redirect_uri,
            },
        )
        assert r_bad_pkce.status_code == 401
        assert srv.token_hits == 0

        # Negative 2: Disallowed redirect_uri rejected BEFORE outbound /token call
        r_bad_redir = client.post(
            "/api/v1/auth/idp-code-exchange",
            json={
                "idp_provider": "google",
                "org_id": org_id,
                "code": auth_code,
                "state": state,
                "code_verifier": verifier,
                "redirect_uri": "http://localhost:9999/steal",
            },
        )
        assert r_bad_redir.status_code == 400
        assert srv.token_hits == 0

        # Positive: Full Authorization Code + PKCE S256 + JWKS exchange succeeds
        r_ok = client.post(
            "/api/v1/auth/idp-code-exchange",
            json={
                "idp_provider": "google",
                "org_id": org_id,
                "code": auth_code,
                "state": state,
                "code_verifier": verifier,
                "redirect_uri": redirect_uri,
            },
        )
        assert r_ok.status_code == 200
        assert srv.token_hits == 1
        assert r_ok.json()["user_id"] == user_id
        assert r_ok.json()["org_id"] == org_id
        assert r_ok.json()["access_token"]

        # Negative 3: Replaying the consumed authorization code/transaction fails closed
        r_replay = client.post(
            "/api/v1/auth/idp-code-exchange",
            json={
                "idp_provider": "google",
                "org_id": org_id,
                "code": auth_code,
                "state": state,
                "code_verifier": verifier,
                "redirect_uri": redirect_uri,
            },
        )
        assert r_replay.status_code == 401

        # Negative 4: Legacy direct ID-token endpoint /idp-exchange is permanently retired (410 Gone)
        r_retired = client.post(
            "/api/v1/auth/idp-exchange",
            json={
                "idp_provider": "google",
                "idp_token": id_token,
                "org_id": org_id,
                "state": state,
                "code_verifier": verifier,
            },
        )
        assert r_retired.status_code == 410
        assert "IDP_EXCHANGE_RETIRED" in r_retired.json()["detail"]

        # Negative 5: End-to-end wrong issuer, wrong audience, HS256 alg confusion, and missing nonce via /idp-code-exchange
        for bad_label, extra_claims, bad_alg in (
            ("wrong_iss", {"iss": "https://evil.example.org", "email": email, "email_verified": True}, "RS256"),
            ("wrong_aud", {"aud": "other-client", "email": email, "email_verified": True}, "RS256"),
            ("missing_nonce", {"nonce": None, "email": email, "email_verified": True}, "RS256"),
        ):
            st_neg = secrets.token_urlsafe(24)
            ver_neg = secrets.token_urlsafe(48)
            chal_neg = base64.urlsafe_b64encode(hashlib.sha256(ver_neg.encode("ascii")).digest()).decode("ascii").rstrip("=")
            nr = client.post(
                "/api/v1/auth/idp-nonce",
                json={"org_id": org_id, "idp_provider": "google", "state": st_neg, "code_challenge": chal_neg},
            )
            assert nr.status_code == 200
            nonce_neg = nr.json()["nonce"]
            code_neg = f"code_{bad_label}_{secrets.token_urlsafe(8)}"
            merged_extra = {"nonce": nonce_neg, **extra_claims}
            tok_neg = _sign_idp_jwt(
                priv,
                "code-k1",
                iss=merged_extra.pop("iss", srv.discovery_issuer),
                aud=merged_extra.pop("aud", "myca-web-client"),
                alg=bad_alg,
                extra=merged_extra,
            )
            srv.issued_codes[code_neg] = {
                "code_challenge": chal_neg,
                "redirect_uri": redirect_uri,
                "id_token": tok_neg,
            }
            r_neg = client.post(
                "/api/v1/auth/idp-code-exchange",
                json={
                    "idp_provider": "google",
                    "org_id": org_id,
                    "code": code_neg,
                    "state": st_neg,
                    "code_verifier": ver_neg,
                    "redirect_uri": redirect_uri,
                },
            )
            assert r_neg.status_code == 401, f"{bad_label} expected 401, got {r_neg.status_code}: {r_neg.text}"

    # Cleanup DB records
    admin_db = get_admin_session()
    try:
        admin_db.execute(text("DELETE FROM public.idp_authentication_nonces WHERE org_id = :oid"), {"oid": org_id})
        admin_db.execute(text("DELETE FROM public.organization_memberships WHERE org_id = :oid"), {"oid": org_id})
        admin_db.execute(text("DELETE FROM public.global_users WHERE id = :uid"), {"uid": user_id})
        admin_db.execute(text("DELETE FROM public.organizations WHERE id = :oid"), {"oid": org_id})
        admin_db.execute(text("DELETE FROM public.auth_rate_limits"))
        admin_db.commit()
    finally:
        admin_db.close()
