import logging
import os
import re
from typing import Any, Dict, List, Optional, Set
from fastapi import HTTPException, status
from jose import jwt
from jose.exceptions import JWTError, ExpiredSignatureError, JWTClaimsError
from app.config import settings
from app.core.jwks import (
    JWKSKeyNotFoundError,
    JWKSUnavailableError,
    default_jwks_client,
)

logger = logging.getLogger(__name__)

ASYMMETRIC_ALGORITHMS: Set[str] = {"RS256", "RS384", "RS512", "ES256", "ES384", "ES512"}
DEFAULT_ALLOWED_PROVIDERS: Set[str] = {"google", "azuread", "okta", "auth0"}
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Canonical exact trusted issuers per provider (strictly prohibiting loose substring matching).
# Per-tenant providers (okta, auth0, tenant-scoped azuread) require explicit MYCA_IDP_ISSUER.
PROVIDER_CANONICAL_ISSUERS: Dict[str, Set[str]] = {
    "google": {"https://accounts.google.com", "accounts.google.com"},
    "azuread": {
        "https://login.microsoftonline.com/common/v2.0",
        "https://sts.windows.net/common/",
    },
    "test-idp": {
        "https://idp.test.myca.internal",
        "myca-test-idp",
    },
}

TENANT_SCOPED_PROVIDERS: Set[str] = {"okta", "auth0"}

# Strict approved signing algorithms per provider
PROVIDER_ALLOWED_ALGORITHMS: Dict[str, Set[str]] = {
    "google": {"RS256"},
    "azuread": {"RS256"},
    "okta": {"RS256", "ES256"},
    "auth0": {"RS256", "ES256"},
    "test-idp": {"RS256", "HS256"},
}


def _resolve_trust_settings():
    idp_issuer = getattr(settings, "MYCA_IDP_ISSUER", None) or os.getenv("MYCA_IDP_ISSUER")
    idp_audience = getattr(settings, "MYCA_IDP_AUDIENCE", None) or os.getenv("MYCA_IDP_AUDIENCE")
    idp_pubkey = getattr(settings, "MYCA_IDP_PUBLIC_KEY", None) or os.getenv("MYCA_IDP_PUBLIC_KEY")
    jwks_url = getattr(settings, "MYCA_IDP_JWKS_URL", None) or os.getenv("MYCA_IDP_JWKS_URL")
    discovery_url = getattr(settings, "MYCA_IDP_DISCOVERY_URL", None) or os.getenv("MYCA_IDP_DISCOVERY_URL")
    # Preserve monkeypatch.setenv overrides in test harnesses when present
    if os.getenv("MYCA_IDP_ISSUER"):
        idp_issuer = os.getenv("MYCA_IDP_ISSUER")
    if os.getenv("MYCA_IDP_AUDIENCE"):
        idp_audience = os.getenv("MYCA_IDP_AUDIENCE")
    if os.getenv("MYCA_IDP_PUBLIC_KEY"):
        idp_pubkey = os.getenv("MYCA_IDP_PUBLIC_KEY")
    if os.getenv("MYCA_IDP_JWKS_URL"):
        jwks_url = os.getenv("MYCA_IDP_JWKS_URL")
    if os.getenv("MYCA_IDP_DISCOVERY_URL"):
        discovery_url = os.getenv("MYCA_IDP_DISCOVERY_URL")
    return (
        idp_issuer.strip() if idp_issuer else None,
        idp_audience.strip() if idp_audience else None,
        idp_pubkey.strip() if idp_pubkey else None,
        jwks_url.strip() if jwks_url else None,
        discovery_url.strip() if discovery_url else None,
    )


def validate_idp_token(
    idp_provider: str,
    idp_token: str,
    env: Optional[str] = None,
    expected_nonce: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Rigorously validates a federated Identity Provider (IdP) token.
    Enforces trusted provider, 3-part compact JWT, fail-closed trust anchors,
    algorithm confusion defense, rotating JWKS / discovery key resolution,
    expiry/nbf/iat/iss/aud/azp/sub claims, strict email + verified boolean,
    and transaction nonce equality.
    """
    if env is None:
        raw_env = getattr(settings, "ENVIRONMENT", None) or os.getenv("ENVIRONMENT", "")
        env = str(raw_env).strip().lower() if raw_env else "development"
    else:
        env = env.strip().lower()

    is_prod_like = env in ("production", "staging")

    # 1. Provider validation
    provider_clean = (idp_provider or "").strip().lower()
    allowed_providers = {p.lower() for p in getattr(settings, "MYCA_IDP_ALLOWED_PROVIDERS", DEFAULT_ALLOWED_PROVIDERS)}
    if not is_prod_like:
        allowed_providers.add("test-idp")

    if not provider_clean or provider_clean not in allowed_providers:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"UNSUPPORTED_IDP_PROVIDER: Provider '{idp_provider}' is not an authorized Identity Provider. Allowed: {sorted(list(allowed_providers))}",
        )

    configured_provider = os.getenv("MYCA_IDP_PROVIDER") or getattr(settings, "MYCA_IDP_PROVIDER", None)
    if configured_provider and provider_clean != configured_provider.strip().lower():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"PROVIDER_MISMATCH: Requested provider '{idp_provider}' does not match configured Identity Provider '{configured_provider}'.",
        )

    # 2. Token format validation
    token_str = (idp_token or "").strip()
    if not token_str or token_str.count(".") != 2:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="MALFORMED_IDP_TOKEN: The provided IdP token is not a valid 3-part compact JWT.",
        )

    # 3. Resolve Trust Anchors & Production Fail-Closed Check
    idp_issuer, idp_audience, idp_pubkey, jwks_url, discovery_url = _resolve_trust_settings()
    has_key_source = bool(idp_pubkey or jwks_url or discovery_url)

    if is_prod_like:
        if not (idp_issuer and idp_audience and has_key_source):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="IDP_INTEGRATION_UNAVAILABLE: External Identity Provider trust anchor (issuer, audience, and public key or JWKS/discovery URL) is not fully configured in this environment.",
            )
        if provider_clean in TENANT_SCOPED_PROVIDERS and not idp_issuer.lower().startswith("https://"):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"IDP_INTEGRATION_UNAVAILABLE: Provider '{provider_clean}' requires an explicit https:// MYCA_IDP_ISSUER in {env}.",
            )

    # 4. Header inspection & algorithm confusion defense
    try:
        header = jwt.get_unverified_header(token_str)
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"INVALID_IDP_HEADER: Failed to parse token header: {str(exc)}",
        )

    alg = header.get("alg")
    if not alg or alg.strip().lower() in ("none", ""):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="ALGORITHM_NOT_PERMITTED: The 'none' or missing signing algorithm is strictly prohibited.",
        )
    alg_upper = alg.strip().upper()

    if alg_upper.startswith("HS"):
        if is_prod_like:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"ALGORITHM_CONFUSION_REJECTED: Symmetric algorithm '{alg_upper}' is strictly prohibited in {env} environments. Asymmetric signatures (RS256/ES256) are required.",
            )
        if (idp_pubkey and "PUBLIC KEY" in str(idp_pubkey)) or jwks_url or discovery_url:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"ALGORITHM_CONFUSION_REJECTED: Token specifies symmetric algorithm '{alg_upper}' but asymmetric public key/JWKS is configured as trust anchor.",
            )

    allowed_provider_algs = set(PROVIDER_ALLOWED_ALGORITHMS.get(provider_clean, {"RS256"}))
    if not is_prod_like and not ((idp_pubkey and "PUBLIC KEY" in str(idp_pubkey)) or jwks_url or discovery_url):
        allowed_provider_algs.add("HS256")
    if alg_upper not in allowed_provider_algs:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"ALGORITHM_NOT_PERMITTED: Algorithm '{alg_upper}' is not permitted for provider '{idp_provider}'. Allowed: {sorted(list(allowed_provider_algs))}",
        )

    allowed_algs = {a.upper() for a in getattr(settings, "MYCA_IDP_ALLOWED_ALGORITHMS", list(ASYMMETRIC_ALGORITHMS))}
    if is_prod_like and alg_upper not in allowed_algs:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"ASYMMETRIC_ALGORITHM_REQUIRED: Algorithm '{alg_upper}' is not in approved asymmetric algorithms: {sorted(list(allowed_algs))}",
        )

    expected_kid = os.getenv("MYCA_IDP_KEY_ID") or getattr(settings, "MYCA_IDP_KEY_ID", None)
    token_kid = header.get("kid")
    if expected_kid and (not token_kid or token_kid != expected_kid):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"UNTRUSTED_KEY_ID: Token key ID '{token_kid}' does not match trusted key ID '{expected_kid}'.",
        )

    # 5. Resolve verification key
    verification_key: Optional[str] = None
    decoding_algorithms: List[str] = [alg_upper]
    ttl_seconds = int(getattr(settings, "MYCA_IDP_JWKS_CACHE_TTL_SECONDS", 300) or 300)
    timeout_seconds = float(getattr(settings, "MYCA_IDP_JWKS_TIMEOUT_SECONDS", 5.0) or 5.0)

    if jwks_url or discovery_url:
        try:
            resolved_jwks_url = jwks_url
            if not resolved_jwks_url and discovery_url:
                disc = default_jwks_client.resolve_discovery(
                    discovery_url,
                    require_https=is_prod_like,
                    expected_issuer=idp_issuer,
                    ttl_seconds=ttl_seconds,
                    timeout_seconds=timeout_seconds,
                )
                resolved_jwks_url = disc["jwks_uri"]
            assert resolved_jwks_url is not None
            jwk_meta = default_jwks_client.get_signing_key(
                resolved_jwks_url,
                kid=token_kid,
                require_https=is_prod_like,
                ttl_seconds=ttl_seconds,
                timeout_seconds=timeout_seconds,
            )
            if jwk_meta.get("alg") and jwk_meta["alg"] != alg_upper:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail=f"ALGORITHM_MISMATCH: Token algorithm '{alg_upper}' does not match JWKS key algorithm '{jwk_meta['alg']}'.",
                )
            verification_key = jwk_meta["key"]
        except HTTPException:
            raise
        except JWKSKeyNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"UNTRUSTED_KEY_ID: {exc}") from exc
        except JWKSUnavailableError as exc:
            if not idp_pubkey:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=f"IDP_INTEGRATION_UNAVAILABLE: Unable to retrieve IdP JWKS keys: {exc}",
                ) from exc
            logger.warning("Falling back to static MYCA_IDP_PUBLIC_KEY after JWKS unavailability: %s", exc)
            verification_key = idp_pubkey
    elif idp_pubkey:
        verification_key = idp_pubkey
    elif not is_prod_like:
        if alg_upper == "HS256":
            verification_key = getattr(settings, "SECRET_KEY", "dev-insecure-secret-key-replace-in-production-with-min-32-chars")
        else:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"IDP_KEY_UNAVAILABLE: No verification key configured for algorithm '{alg_upper}'.",
            )
    else:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="IDP_INTEGRATION_UNAVAILABLE: No Identity Provider trust anchor configured.",
        )

    # 6. Cryptographic signature and claims verification
    decode_options = {
        "verify_signature": True,
        "verify_exp": True,
        "verify_nbf": True,
        "verify_iat": True,
        "verify_aud": bool(idp_audience),
        "verify_iss": bool(idp_issuer),
        "require_exp": True,
        "require_iat": True,
    }

    try:
        claims = jwt.decode(
            token_str,
            verification_key,
            algorithms=decoding_algorithms,
            issuer=idp_issuer if idp_issuer else None,
            audience=idp_audience if idp_audience else None,
            options=decode_options,
        )
    except ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="EXPIRED_IDP_TOKEN: The Identity Provider token has expired.",
        )
    except JWTClaimsError as exc:
        msg = str(exc)
        if "issuer" in msg.lower():
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"INVALID_IDP_ISSUER: Token issuer does not match trusted issuer '{idp_issuer}'.",
            )
        if "audience" in msg.lower():
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"INVALID_IDP_AUDIENCE: Token audience does not match trusted audience '{idp_audience}'.",
            )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"INVALID_IDP_CLAIMS: {msg}")
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"INVALID_IDP_SIGNATURE: Cryptographic signature verification failed: {str(exc)}",
        )

    # Multi-audience authorized party check per OIDC Core 1.0 Section 3.1.3.7
    aud_claim = claims.get("aud")
    if isinstance(aud_claim, list) and len(aud_claim) > 1 and idp_audience:
        azp = str(claims.get("azp") or "").strip()
        if not azp or azp != idp_audience:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="INVALID_IDP_AZP: Multi-audience ID token requires matching 'azp' claim.",
            )

    # Mandatory subject claim
    sub_claim = claims.get("sub")
    if not isinstance(sub_claim, str) or not sub_claim.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="MISSING_SUB_CLAIM: IdP token must contain a non-empty subject ('sub') claim.",
        )

    # 7. Exact Provider-to-Issuer binding check
    token_iss = str(claims.get("iss", "")).strip().lower()
    if provider_clean in PROVIDER_CANONICAL_ISSUERS:
        canonical_issuers = {iss.lower() for iss in PROVIDER_CANONICAL_ISSUERS[provider_clean]}
        if idp_issuer:
            canonical_issuers.add(idp_issuer.strip().lower())
        if token_iss not in canonical_issuers:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"PROVIDER_ISSUER_MISMATCH: Token issuer '{claims.get('iss')}' is not a valid canonical issuer for provider '{idp_provider}'.",
            )
    elif idp_issuer:
        if token_iss != idp_issuer.strip().lower():
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"PROVIDER_ISSUER_MISMATCH: Token issuer '{claims.get('iss')}' does not match configured issuer '{idp_issuer}'.",
            )
    elif is_prod_like:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="IDP_INTEGRATION_UNAVAILABLE: Configured provider requires explicit MYCA_IDP_ISSUER.",
        )

    # 8. Strict Email Claim Check
    if "email" not in claims or claims.get("email") is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="MISSING_EMAIL_CLAIM: IdP token must contain an explicit 'email' claim. Subject identifier fallback is strictly prohibited.",
        )
    email = claims.get("email")
    if not isinstance(email, str) or not _EMAIL_RE.match(email.strip()):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="INVALID_EMAIL_FORMAT: IdP token contains a malformed or empty email claim.",
        )

    # 9. Strict email_verified Claim Check
    if "email_verified" not in claims or claims.get("email_verified") is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="MISSING_EMAIL_VERIFIED_CLAIM: IdP token must contain an explicit 'email_verified' claim.",
        )
    email_verified = claims["email_verified"]
    if not isinstance(email_verified, bool) or email_verified is not True:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="UNVERIFIED_EMAIL: The email address asserted by the Identity Provider is not verified (email_verified must be boolean True).",
        )

    # 10. Nonce Matching
    if expected_nonce is not None:
        token_nonce = claims.get("nonce")
        if not token_nonce or not str(token_nonce).strip():
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="MISSING_IDP_NONCE: ID token is missing required nonce claim.",
            )
        if str(token_nonce).strip() != str(expected_nonce).strip():
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"NONCE_MISMATCH: Token nonce '{token_nonce}' does not match authentication transaction nonce '{expected_nonce}'.",
            )

    return claims
