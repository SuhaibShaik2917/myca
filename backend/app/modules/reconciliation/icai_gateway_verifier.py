"""
MyCA Statutory Auditor Verification — ICAI Digital Verification Gateway Protocol Verifier.

Implements rigorous cryptographic verification of digital verification gateway responses:
1. Validates digital signatures against trusted public keys (trust anchor).
2. Loads independently provisioned JWKS (ICAI_GATEWAY_JWKS_PATH) or pinned public key (ICAI_GATEWAY_PUBLIC_KEY).
3. Strictly binds key identifier (kid) to trusted registry; rejects unknown kid without trusting token headers.
4. Enforces asymmetric algorithms (RS256, ES256) in production; rejects symmetric HS256 in production.
5. Cryptographically binds claims:
   - membership_no
   - firm_registration_no
   - cop_status == 'ACTIVE'
   - cop_valid_until > date.today()
   - evidence_ref
   - issuer == 'https://gateway.icai.org'
   - audience == 'myca-statutory-verification'
   - iat (within 24 hours), exp (future), and jti (replay nonce).
6. Prevents replay using database-backed atomic nonce tracking; fails closed if DB unavailable in production.
7. Strictly isolates test keys (rejected in production mode).
"""

import os
import re
import json
import hashlib
from datetime import datetime, timezone, timedelta, date
from typing import Dict, Any, Optional, Tuple
from jose import jwt, jwk, JWTError
from sqlalchemy import text
from app.core.jwks import JWKSKeyNotFoundError, JWKSUnavailableError, default_jwks_client

# Configured trusted ICAI gateway parameters
OFFICIAL_ICAI_GATEWAY_ISSUER = "https://gateway.icai.org"
OFFICIAL_ICAI_AUDIENCE = "myca-statutory-verification"
SUPPORTED_GATEWAY_ALGORITHMS = ["RS256", "ES256", "HS256"]
PRODUCTION_ALLOWED_ALGORITHMS = ["RS256", "ES256"]

# In-memory test trust store for automated tests only (KID -> Key Metadata)
_TEST_TRUST_STORE: Dict[str, Dict[str, Any]] = {}


def register_test_gateway_key(
    kid: str,
    key: str,
    algorithm: str = "HS256",
    issuer: str = OFFICIAL_ICAI_GATEWAY_ISSUER,
    valid_from: Optional[datetime] = None,
    valid_until: Optional[datetime] = None,
    is_revoked: bool = False,
    is_test_key: bool = True
) -> None:
    """Registers a key in the test-scoped trust store for test fixtures."""
    now = datetime.now(timezone.utc)
    _TEST_TRUST_STORE[kid] = {
        "kid": kid,
        "key": key,
        "algorithm": algorithm,
        "issuer": issuer,
        "valid_from": valid_from or (now - timedelta(days=30)),
        "valid_until": valid_until or (now + timedelta(days=365)),
        "is_revoked": is_revoked,
        "is_test_key": is_test_key,
    }


def clear_test_gateway_trust_store() -> None:
    """Clears the test trust store."""
    _TEST_TRUST_STORE.clear()


def load_official_icai_trust_store() -> Dict[str, Dict[str, Any]]:
    """
    Loads and parses official ICAI trust anchor keys from configured JWKS path
    or environment public key. Strictly rejects symmetric ('oct') or malformed keys.
    """
    registry: Dict[str, Dict[str, Any]] = {}
    jwks_path = os.environ.get("ICAI_GATEWAY_JWKS_PATH")
    if jwks_path and os.path.exists(jwks_path):
        try:
            with open(jwks_path, "r", encoding="utf-8") as f:
                jwks_data = json.load(f)
            keys = jwks_data.get("keys", []) if isinstance(jwks_data, dict) else []
            for k in keys:
                if not isinstance(k, dict):
                    continue
                kty = str(k.get("kty") or "").upper()
                if kty not in ("RSA", "EC"):
                    continue
                kid = k.get("kid")
                if not kid:
                    continue
                alg = str(k.get("alg") or "RS256").upper()
                if alg == "NONE" or alg.startswith("HS"):
                    continue
                try:
                    key_obj = jwk.construct(k)
                    pem_key = key_obj.to_pem(is_private=False).decode("utf-8")
                except Exception:
                    continue
                registry[str(kid)] = {
                    "kid": str(kid),
                    "key": pem_key,
                    "algorithm": alg,
                    "issuer": OFFICIAL_ICAI_GATEWAY_ISSUER,
                    "valid_from": datetime(2025, 1, 1, tzinfo=timezone.utc),
                    "valid_until": datetime(2035, 1, 1, tzinfo=timezone.utc),
                    "is_revoked": False,
                    "is_test_key": False,
                }
        except Exception as exc:
            raise ValueError(f"MALFORMED_ICAI_JWKS_FILE: Failed to parse ICAI_GATEWAY_JWKS_PATH: {exc}") from exc

    prod_key = os.environ.get("ICAI_GATEWAY_PUBLIC_KEY")
    if prod_key and prod_key.strip():
        kid = os.environ.get("ICAI_GATEWAY_KEY_ID", "icai-gateway-root-2026").strip()
        alg = os.environ.get("ICAI_GATEWAY_KEY_ALG", "RS256").strip().upper()
        registry[kid] = {
            "kid": kid,
            "key": prod_key.strip(),
            "algorithm": alg,
            "issuer": OFFICIAL_ICAI_GATEWAY_ISSUER,
            "valid_from": datetime(2025, 1, 1, tzinfo=timezone.utc),
            "valid_until": datetime(2035, 1, 1, tzinfo=timezone.utc),
            "is_revoked": False,
            "is_test_key": False
        }

    return registry


def is_official_icai_gateway_configured() -> bool:
    """
    Checks if an official production ICAI trust anchor (local JWKS, PEM key, or remote JWKS URL) is configured.
    """
    if os.environ.get("ICAI_GATEWAY_JWKS_URL", "").strip():
        return True
    registry = load_official_icai_trust_store()
    return len(registry) > 0


def create_test_signed_gateway_response(
    membership_no: str,
    firm_registration_no: Optional[str],
    cop_status: str,
    cop_valid_until: date,
    evidence_ref: str,
    kid: str,
    secret_or_key: str,
    algorithm: str = "HS256",
    issuer: str = OFFICIAL_ICAI_GATEWAY_ISSUER,
    audience: str = OFFICIAL_ICAI_AUDIENCE,
    nonce: Optional[str] = None,
    issued_at: Optional[datetime] = None,
    expires_at: Optional[datetime] = None,
    verification_status: str = "INDEPENDENTLY_VERIFIED"
) -> str:
    """Helper to generate a cryptographically signed test response for unit/integration tests."""
    now = datetime.now(timezone.utc)
    iat = issued_at or now
    exp = expires_at or (iat + timedelta(hours=24))

    payload = {
        "iss": issuer,
        "aud": audience,
        "kid": kid,
        "jti": nonce or f"NONCE-{hashlib.sha256(f'{membership_no}:{iat.isoformat()}'.encode()).hexdigest()[:16]}",
        "iat": int(iat.timestamp()),
        "exp": int(exp.timestamp()),
        "membership_no": str(membership_no).strip(),
        "firm_registration_no": str(firm_registration_no).strip() if firm_registration_no else None,
        "cop_status": str(cop_status).strip().upper(),
        "cop_valid_until": cop_valid_until.isoformat() if isinstance(cop_valid_until, (date, datetime)) else str(cop_valid_until),
        "evidence_ref": str(evidence_ref).strip(),
        "verification_status": verification_status,
        "verification_source": "ICAI_DIGITAL_VERIFICATION_GATEWAY"
    }

    headers = {"kid": kid, "alg": algorithm}
    return jwt.encode(payload, secret_or_key, algorithm=algorithm, headers=headers)


def verify_icai_gateway_signed_response(
    signed_token: str,
    expected_membership_no: str,
    expected_firm_registration_no: Optional[str] = None,
    expected_cop_valid_until: Optional[date] = None,
    expected_evidence_ref: Optional[str] = None,
    environment: Optional[str] = None,
    db: Optional[Any] = None,
    auditor_id: Optional[str] = None
) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Rigorously verifies a signed ICAI digital verification gateway response token.

    Enforces:
    1. Trust Anchor Availability:
       In production, if official trust anchor is not configured, returns TRUST_ANCHOR_UNAVAILABLE.
    2. Test-Key Isolation:
       Test keys are strictly rejected in production mode.
    3. Asymmetric Algorithm in Production:
       Symmetric algorithms (HS256) are rejected in production.
    4. Cryptographic Signature:
       Validates digital signature against trusted public key matching header 'kid'.
    5. Algorithm Matching:
       Token algorithm must match configured trusted key algorithm.
    6. Token Claims:
       Verifies issuer, audience, and timestamps (iat within 24h, exp in future, clock skew <= 5m).
    7. Persistent Nonce Replay:
       Ensures 'jti' nonce has not been consumed previously (checks DB and fails closed).
    8. Statutory Claim Binding:
       Binds membership_no, firm_registration_no, cop_status == 'ACTIVE',
       cop_valid_until > today, and evidence_ref.
    """
    env = (environment or os.environ.get("ENVIRONMENT", "production")).strip().lower()

    if not signed_token or not isinstance(signed_token, str) or len(signed_token.strip()) < 20:
        return False, "INVALID_TOKEN: Gateway response token is missing or malformed.", {}

    raw_token = signed_token.strip()

    # 1. Unpack unverified header to obtain kid and alg
    try:
        unverified_headers = jwt.get_unverified_header(raw_token)
    except Exception as ex:
        return False, f"MALFORMED_HEADER: Unable to parse token headers: {ex}", {}

    kid = unverified_headers.get("kid")
    alg = unverified_headers.get("alg")

    if not kid or not isinstance(kid, str):
        return False, "MISSING_KEY_ID: Token header lacks required key identifier ('kid').", {}

    if not alg or alg.upper() == "NONE" or alg not in SUPPORTED_GATEWAY_ALGORITHMS:
        return False, f"UNSUPPORTED_ALGORITHM: Algorithm '{alg}' is unsupported or prohibited (alg=none rejected).", {}

    # Strict algorithm check for production: HS256 is prohibited
    if env not in ("test", "testing", "development"):
        if alg not in PRODUCTION_ALLOWED_ALGORITHMS:
            return False, f"ASYMMETRIC_ALGORITHM_REQUIRED: Algorithm '{alg}' is prohibited for production gateway verification; must use RS256/ES256 asymmetric public keys.", {}

    # 2. Resolve trusted key from Trust Store
    trusted_key_meta: Optional[Dict[str, Any]] = None

    if env in ("test", "testing", "development"):
        trusted_key_meta = _TEST_TRUST_STORE.get(kid)

    if not trusted_key_meta:
        official_store = load_official_icai_trust_store()
        trusted_key_meta = official_store.get(kid)

    if not trusted_key_meta:
        jwks_url = os.environ.get("ICAI_GATEWAY_JWKS_URL", "").strip()
        if jwks_url:
            is_prod_like = env not in ("test", "testing", "development")
            try:
                jwk_meta = default_jwks_client.get_signing_key(
                    jwks_url,
                    kid=kid,
                    require_https=is_prod_like,
                )
                trusted_key_meta = {
                    "kid": jwk_meta.get("kid") or kid,
                    "key": jwk_meta["key"],
                    "algorithm": jwk_meta.get("alg") or alg.upper(),
                    "issuer": OFFICIAL_ICAI_GATEWAY_ISSUER,
                    "valid_from": datetime(2025, 1, 1, tzinfo=timezone.utc),
                    "valid_until": datetime(2035, 1, 1, tzinfo=timezone.utc),
                    "is_revoked": False,
                    "is_test_key": False,
                }
            except JWKSKeyNotFoundError:
                return False, f"UNKNOWN_KEY_ID: Key identifier '{kid}' is not recognized in the trusted gateway JWKS.", {}
            except JWKSUnavailableError as exc:
                return False, f"TRUST_ANCHOR_UNAVAILABLE: Unable to retrieve official ICAI JWKS: {exc}", {}

    if not trusted_key_meta:
        if not is_official_icai_gateway_configured() and env not in ("test", "testing", "development"):
            return False, "TRUST_ANCHOR_UNAVAILABLE: Official ICAI digital verification gateway public keys are not configured in production environment.", {}
        return False, f"UNKNOWN_KEY_ID: Key identifier '{kid}' is not recognized in the trusted gateway trust store.", {}

    # 3. Test key isolation in production
    if trusted_key_meta.get("is_test_key") and env not in ("test", "testing", "development"):
        return False, "TEST_KEY_REJECTED: Test cryptographic key cannot be accepted in production verification mode.", {}

    # 4. Strict algorithm matching against trusted key configuration
    configured_alg = trusted_key_meta.get("algorithm", "RS256").upper()
    if alg.upper() != configured_alg:
        return False, f"ALGORITHM_MISMATCH: Token algorithm '{alg}' does not match configured trusted key algorithm '{configured_alg}'.", {}

    # 5. Key lifecycle: check revocation and expiration
    if trusted_key_meta.get("is_revoked"):
        return False, f"REVOKED_SIGNING_KEY: Gateway signing key '{kid}' has been revoked by the issuing authority.", {}

    now_utc = datetime.now(timezone.utc)
    k_from = trusted_key_meta.get("valid_from")
    k_until = trusted_key_meta.get("valid_until")

    if k_from and now_utc < k_from:
        return False, f"KEY_NOT_YET_VALID: Gateway signing key '{kid}' is not valid until {k_from.isoformat()}.", {}
    if k_until and now_utc > k_until:
        return False, f"EXPIRED_SIGNING_KEY: Gateway signing key '{kid}' expired on {k_until.isoformat()}.", {}

    # 6. Cryptographic signature and standard claims verification
    signing_key = trusted_key_meta["key"]
    try:
        claims = jwt.decode(
            raw_token,
            signing_key,
            algorithms=[alg],
            issuer=trusted_key_meta.get("issuer", OFFICIAL_ICAI_GATEWAY_ISSUER),
            audience=OFFICIAL_ICAI_AUDIENCE,
            options={
                "verify_signature": True,
                "verify_aud": True,
                "verify_iat": True,
                "verify_exp": True,
                "verify_iss": True,
                "leeway": 300  # 5 minutes clock skew permitted
            }
        )
    except JWTError as ex:
        return False, f"CRYPTOGRAPHIC_VERIFICATION_FAILED: Signature or claim verification failed: {ex}", {}

    # 7. Freshness policy: reject future-dated (> 5m clock skew) or > 24h stale tokens
    iat_ts = claims.get("iat")
    if not iat_ts:
        return False, "MISSING_TIMESTAMP: Token lacks issued-at ('iat') claim.", {}
    iat_dt = datetime.fromtimestamp(iat_ts, tz=timezone.utc)
    if iat_dt > now_utc + timedelta(minutes=5):
        return False, f"FUTURE_TIMESTAMP: Gateway response iat {iat_dt.isoformat()} exceeds 5-minute clock skew.", {}
    if now_utc - iat_dt > timedelta(hours=24):
        return False, f"STALE_VERIFICATION: Gateway response was issued at {iat_dt.isoformat()}, exceeding maximum 24-hour freshness window.", {}

    # 8. Nonce replay protection (jti)
    # 8. Nonce extraction and mandatory expiry validation
    jti = claims.get("jti")
    if not jti or not isinstance(jti, str):
        return False, "MISSING_NONCE: Gateway response token lacks required unique response identifier ('jti').", {}

    exp_ts = claims.get("exp")
    if not exp_ts:
        return False, "MISSING_EXPIRY: Gateway response token lacks expiration ('exp') claim.", {}
    exp_dt = datetime.fromtimestamp(exp_ts, tz=timezone.utc)
    if now_utc > exp_dt:
        return False, f"EXPIRED_GATEWAY_TOKEN: Gateway response token expired at {exp_dt.isoformat()}.", {}

    # 9. Active database session strictly required for atomic persistent replay defense
    if db is None:
        return False, "DATABASE_UNAVAILABLE: Active database session required for atomic persistent replay defense.", {}

    # 10. Statutory claim binding (validated before consuming nonce)
    signed_mem_no = str(claims.get("membership_no") or "").strip()
    if signed_mem_no != expected_membership_no.strip():
        return False, f"MEMBERSHIP_MISMATCH: Signed membership number '{signed_mem_no}' does not match expected '{expected_membership_no}'.", {}

    if expected_firm_registration_no is not None:
        signed_frn = str(claims.get("firm_registration_no") or "").strip()
        if signed_frn != str(expected_firm_registration_no).strip():
            return False, f"FRN_MISMATCH: Signed firm registration number '{signed_frn}' does not match expected '{expected_firm_registration_no}'.", {}

    signed_cop_status = str(claims.get("cop_status") or "").strip().upper()
    if signed_cop_status != "ACTIVE":
        return False, f"INACTIVE_COP: Signed Certificate of Practice status is '{signed_cop_status}'; must be ACTIVE.", {}

    signed_valid_until_str = claims.get("cop_valid_until")
    if not signed_valid_until_str:
        return False, "MISSING_COP_VALIDITY: Signed payload lacks cop_valid_until date.", {}

    try:
        signed_valid_until = date.fromisoformat(str(signed_valid_until_str))
    except Exception:
        return False, f"INVALID_COP_DATE: Unable to parse signed cop_valid_until '{signed_valid_until_str}'.", {}

    if signed_valid_until <= date.today():
        return False, f"EXPIRED_COP: Signed Certificate of Practice expired on {signed_valid_until}.", {}

    if expected_cop_valid_until:
        exp_date = expected_cop_valid_until if isinstance(expected_cop_valid_until, date) else date.fromisoformat(str(expected_cop_valid_until))
        if signed_valid_until != exp_date:
            return False, f"COP_DATE_MISMATCH: Signed validity date '{signed_valid_until}' does not match expected '{exp_date}'.", {}

    signed_ev_ref = str(claims.get("evidence_ref") or "").strip()
    if expected_evidence_ref:
        if signed_ev_ref != expected_evidence_ref.strip():
            return False, f"EVIDENCE_REF_MISMATCH: Signed evidence reference '{signed_ev_ref}' does not match expected '{expected_evidence_ref}'.", {}

    # 11. Atomic persistent database consumption into dedicated PostgreSQL nonce registry
    try:
        nonce_stmt = text("""
            INSERT INTO public.statutory_gateway_nonce_registry (
                nonce, issuer, kid, membership_no, evidence_ref,
                issued_at, expires_at, consumed_at, consumed_by
            ) VALUES (
                :nonce, :issuer, :kid, :mem, :ev_ref,
                :iat, :exp, clock_timestamp(), :auditor_id
            )
            ON CONFLICT (nonce) DO NOTHING;
        """)
        nonce_res = db.execute(nonce_stmt, {
            "nonce": jti,
            "issuer": claims.get("iss", OFFICIAL_ICAI_GATEWAY_ISSUER),
            "kid": kid,
            "mem": signed_mem_no,
            "ev_ref": signed_ev_ref,
            "iat": iat_dt,
            "exp": exp_dt,
            "auditor_id": auditor_id or signed_mem_no,
        })
        if hasattr(db, "commit"):
            db.commit()
        if nonce_res.rowcount == 0:
            return False, f"GATEWAY_NONCE_REPLAY: Gateway response nonce '{jti}' has already been consumed.", {}
    except Exception as ex:
        if hasattr(db, "rollback"):
            db.rollback()
        return False, f"DATABASE_ERROR: Replay check failed: {ex}", {}

    verified_data = {
        "membership_no": signed_mem_no,
        "firm_registration_no": claims.get("firm_registration_no"),
        "cop_status": signed_cop_status,
        "cop_valid_until": signed_valid_until,
        "evidence_ref": claims.get("evidence_ref"),
        "gateway_kid": kid,
        "gateway_response_id": jti,
        "kid": kid,
        "jti": jti,
        "verified_at": iat_dt,
        "verification_source": "ICAI_DIGITAL_VERIFICATION_GATEWAY",
        "verification_status": "INDEPENDENTLY_VERIFIED"
    }

    return True, f"ICAI digital gateway response cryptographically authenticated via key {kid}.", verified_data
