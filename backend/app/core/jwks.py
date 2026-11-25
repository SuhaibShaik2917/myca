"""Shared HTTPS JWKS & OIDC Discovery Client with TTL caching and rotation refresh."""

import ipaddress
import threading
import time
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import httpx
from jose import jwk

ALLOWED_ASYMMETRIC_KTYS = {"RSA", "EC"}


class JWKSUnavailableError(RuntimeError):
    """Raised when remote JWKS or OIDC discovery metadata cannot be fetched securely."""


class JWKSKeyNotFoundError(LookupError):
    """Raised when a requested key ID is absent from a valid JWKS document."""


def validate_jwks_endpoint_url(url: str, *, require_https: bool) -> str:
    """
    Validates that a JWKS or OIDC discovery URL is well-formed HTTP(S) without
    embedded credentials and enforces HTTPS in production/staging environments.
    """
    cleaned = (url or "").strip()
    parsed = urlparse(cleaned)
    scheme = parsed.scheme.lower()
    if scheme not in ("https", "http") or not parsed.netloc or not parsed.hostname:
        raise JWKSUnavailableError(f"Invalid JWKS/discovery URL: '{url}'")
    if parsed.username or parsed.password or parsed.fragment:
        raise JWKSUnavailableError("JWKS/discovery URL must not contain credentials or fragments")
    if require_https and scheme != "https":
        raise JWKSUnavailableError(
            f"HTTPS is required for JWKS/discovery URL in production/staging (got '{scheme}')"
        )
    if scheme == "http":
        host = parsed.hostname.lower()
        is_local = host in ("localhost", "127.0.0.1", "::1")
        if not is_local:
            try:
                is_local = ipaddress.ip_address(host).is_loopback
            except ValueError:
                is_local = False
        if not is_local:
            raise JWKSUnavailableError(
                f"Plain HTTP JWKS/discovery URL is allowed only on loopback in local/test mode (got '{host}')"
            )
    return cleaned


def _parse_jwks_payload(payload: Any) -> Dict[str, Dict[str, Any]]:
    """
    Parses a JWKS JSON object into {kid: key_metadata}.
    Strictly rejects symmetric keys ('oct') and non-signature keys ('use' != 'sig').
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("keys"), list):
        raise JWKSUnavailableError("Malformed JWKS payload: expected JSON object with 'keys' list")

    parsed_keys: Dict[str, Dict[str, Any]] = {}
    for idx, entry in enumerate(payload["keys"]):
        if not isinstance(entry, dict):
            continue
        kty = str(entry.get("kty") or "").upper()
        if kty not in ALLOWED_ASYMMETRIC_KTYS:
            # Never ingest symmetric ('oct') or unknown keys into asymmetric verification trust store
            continue
        use = entry.get("use")
        if use is not None and str(use).lower() != "sig":
            continue
        try:
            key_obj = jwk.construct(entry)
            pem = key_obj.to_pem().decode("utf-8")
        except Exception:
            continue

        raw_alg = entry.get("alg")
        alg_upper = str(raw_alg).strip().upper() if raw_alg else None
        if alg_upper and (alg_upper == "NONE" or alg_upper.startswith("HS")):
            continue

        raw_kid = entry.get("kid")
        kid_str = str(raw_kid).strip() if raw_kid else ""
        map_key = kid_str if kid_str else f"__nokid_{idx}"
        parsed_keys[map_key] = {
            "kid": kid_str or None,
            "key": pem,
            "alg": alg_upper,
            "kty": kty,
            "has_explicit_kid": bool(kid_str),
        }

    if not parsed_keys:
        raise JWKSUnavailableError("JWKS contains no valid asymmetric signing keys")
    return parsed_keys


class JWKSClient:
    """Thread-safe OIDC Discovery + JWKS fetcher with bounded TTL and rotation refresh."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._discovery_cache: Dict[str, Dict[str, Any]] = {}
        self._jwks_cache: Dict[str, Dict[str, Any]] = {}

    def clear_cache(self) -> None:
        with self._lock:
            self._discovery_cache.clear()
            self._jwks_cache.clear()

    def _fetch_json(self, url: str, *, require_https: bool, timeout_seconds: float) -> Any:
        safe_url = validate_jwks_endpoint_url(url, require_https=require_https)
        try:
            with httpx.Client(timeout=timeout_seconds, follow_redirects=False) as client:
                resp = client.get(safe_url, headers={"Accept": "application/json"})
        except Exception as exc:
            raise JWKSUnavailableError(f"Failed fetching '{safe_url}': {exc}") from exc

        if resp.status_code != 200:
            raise JWKSUnavailableError(
                f"Unexpected HTTP {resp.status_code} fetching '{safe_url}'"
            )
        try:
            return resp.json()
        except Exception as exc:
            raise JWKSUnavailableError(f"Invalid JSON returned from '{safe_url}': {exc}") from exc

    def resolve_discovery(
        self,
        discovery_url: str,
        *,
        require_https: bool,
        expected_issuer: Optional[str] = None,
        ttl_seconds: int = 300,
        timeout_seconds: float = 5.0,
    ) -> Dict[str, Optional[str]]:
        safe_url = validate_jwks_endpoint_url(discovery_url, require_https=require_https)
        now = time.monotonic()
        with self._lock:
            cached = self._discovery_cache.get(safe_url)
            if cached and (now - cached["fetched_at"]) < ttl_seconds:
                resolved = cached["data"]
                if expected_issuer and resolved["issuer"].lower() != expected_issuer.strip().lower():
                    raise JWKSUnavailableError(
                        f"OIDC discovery issuer '{resolved['issuer']}' does not match expected '{expected_issuer}'"
                    )
                return resolved

        doc = self._fetch_json(safe_url, require_https=require_https, timeout_seconds=timeout_seconds)
        if not isinstance(doc, dict):
            raise JWKSUnavailableError("OIDC discovery document must be a JSON object")

        raw_issuer = doc.get("issuer")
        if not isinstance(raw_issuer, str) or not raw_issuer.strip():
            raise JWKSUnavailableError("OIDC discovery document missing required 'issuer'")
        issuer = raw_issuer.strip()
        validate_jwks_endpoint_url(issuer, require_https=require_https)
        if expected_issuer and issuer.lower() != expected_issuer.strip().lower():
            raise JWKSUnavailableError(
                f"OIDC discovery issuer '{issuer}' does not match expected '{expected_issuer}'"
            )

        raw_jwks_uri = doc.get("jwks_uri")
        if not isinstance(raw_jwks_uri, str) or not raw_jwks_uri.strip():
            raise JWKSUnavailableError("OIDC discovery document missing required 'jwks_uri'")
        jwks_uri = validate_jwks_endpoint_url(raw_jwks_uri, require_https=require_https)

        raw_token_ep = doc.get("token_endpoint")
        token_endpoint = None
        if raw_token_ep is not None:
            if not isinstance(raw_token_ep, str) or not raw_token_ep.strip():
                raise JWKSUnavailableError("OIDC discovery document contains invalid 'token_endpoint'")
            token_endpoint = validate_jwks_endpoint_url(raw_token_ep, require_https=require_https)

        resolved: Dict[str, Optional[str]] = {
            "jwks_uri": jwks_uri,
            "issuer": issuer,
            "token_endpoint": token_endpoint,
        }
        with self._lock:
            self._discovery_cache[safe_url] = {"fetched_at": time.monotonic(), "data": resolved}
        return resolved

    def get_signing_key(
        self,
        jwks_url: str,
        *,
        kid: Optional[str],
        require_https: bool,
        ttl_seconds: int = 300,
        refresh_cooldown_seconds: float = 30.0,
        timeout_seconds: float = 5.0,
    ) -> Dict[str, Any]:
        safe_url = validate_jwks_endpoint_url(jwks_url, require_https=require_https)
        now = time.monotonic()
        need_fetch = False

        with self._lock:
            cached = self._jwks_cache.get(safe_url)
            if cached is None or (now - cached["fetched_at"]) >= ttl_seconds:
                need_fetch = True
            elif kid and kid in cached["keys"]:
                return cached["keys"][kid]
            elif kid and (now - cached["last_refresh_attempt"]) >= refresh_cooldown_seconds:
                # Unknown kid: allow bounded rotation refresh after cooldown
                need_fetch = True
            elif not kid:
                return self._select_unkeyed_entry(cached["keys"])

        if need_fetch:
            with self._lock:
                # Re-check under lock to avoid thundering herd across concurrent logins
                cached = self._jwks_cache.get(safe_url)
                now_inner = time.monotonic()
                if cached is not None and (now_inner - cached["fetched_at"]) < ttl_seconds:
                    if kid and kid in cached["keys"]:
                        return cached["keys"][kid]
                    if kid and (now_inner - cached["last_refresh_attempt"]) < refresh_cooldown_seconds:
                        raise JWKSKeyNotFoundError(f"Key ID '{kid}' not present in cached JWKS")
                if cached is not None:
                    cached["last_refresh_attempt"] = now_inner

            payload = self._fetch_json(
                safe_url, require_https=require_https, timeout_seconds=timeout_seconds
            )
            keys = _parse_jwks_payload(payload)
            with self._lock:
                self._jwks_cache[safe_url] = {
                    "fetched_at": time.monotonic(),
                    "last_refresh_attempt": time.monotonic(),
                    "keys": keys,
                }
                cached = self._jwks_cache[safe_url]

        assert cached is not None
        if kid:
            match = cached["keys"].get(kid)
            if not match:
                raise JWKSKeyNotFoundError(f"Key ID '{kid}' not present in JWKS")
            return match
        return self._select_unkeyed_entry(cached["keys"])

    @staticmethod
    def _select_unkeyed_entry(keys: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        if len(keys) != 1:
            raise JWKSKeyNotFoundError("Token header missing 'kid' for multi-key JWKS")
        entry = next(iter(keys.values()))
        if entry.get("has_explicit_kid"):
            raise JWKSKeyNotFoundError("Token header missing required 'kid' matching JWKS key")
        return entry


default_jwks_client = JWKSClient()
