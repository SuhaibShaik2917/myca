import os
import time
from typing import Any, Dict, List, Optional
from fastapi import HTTPException, status
from jose import jwt
from jose.exceptions import JWTError, ExpiredSignatureError, JWTClaimsError
from app.config import settings

ASYMMETRIC_ALGORITHMS = {"RS256", "RS384", "RS512", "ES256", "ES384", "ES512"}
DEFAULT_ALLOWED_PROVIDERS = {"google", "azuread", "okta", "auth0"}

PROVIDER_ISSUER_PATTERNS = {
    "google": ["https://accounts.google.com", "accounts.google.com"],
    "azuread": ["login.microsoftonline.com", "sts.windows.net"],
    "okta": ["okta.com"],
    "auth0": ["auth0.com"],
}


def validate_idp_token(
    idp_provider: str,
    idp_token: str,
    env: Optional[str] = None,
    expected_nonce: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Rigorously validates a federated Identity Provider (IdP) token.

    Enforces:
    1. Provider authorization against allowed providers and configured trust configuration.
    2. Strict token format (compact 3-part JWT).
    3. Production fail-closed trust anchor check (issuer, audience, and public key).
    4. Header inspection, algorithm verification, and algorithm confusion defense.
    5. Cryptographic signature verification using trusted public key.
    6. Token expiration, not-before freshness, issuer, audience claims.
    7. Provider-to-issuer binding check.
    8. Strict email claim (non-empty string with '@'; no sub fallback).
    9. Strict email_verified claim (must be present, boolean type, and True).
    10. Nonce matching when an expected transaction nonce is supplied.
    """
    if env is None:
        raw_env = os.getenv("ENVIRONMENT") or getattr(settings, "ENVIRONMENT", "")
        env = str(raw_env).strip().lower() if raw_env else "development"
    else:
        env = env.strip().lower()

    # 1. Provider validation
    provider_clean = (idp_provider or "").strip().lower()
    allowed_providers = set(getattr(settings, "MYCA_IDP_ALLOWED_PROVIDERS", DEFAULT_ALLOWED_PROVIDERS))
    allowed_providers = {p.lower() for p in allowed_providers}
    if env in ("development", "test"):
        allowed_providers.add("test-idp")

    if not provider_clean or provider_clean not in allowed_providers:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"UNSUPPORTED_IDP_PROVIDER: Provider '{idp_provider}' is not an authorized Identity Provider. Allowed: {sorted(list(allowed_providers))}",
        )

    configured_provider = getattr(settings, "MYCA_IDP_PROVIDER", None)
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
    idp_issuer = os.getenv("MYCA_IDP_ISSUER") or getattr(settings, "MYCA_IDP_ISSUER", None)
    idp_audience = os.getenv("MYCA_IDP_AUDIENCE") or getattr(settings, "MYCA_IDP_AUDIENCE", None)
    idp_pubkey = os.getenv("MYCA_IDP_PUBLIC_KEY") or getattr(settings, "MYCA_IDP_PUBLIC_KEY", None)

    if env in ("production", "staging"):
        if not (idp_issuer and idp_pubkey and idp_audience):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="IDP_INTEGRATION_UNAVAILABLE: External Identity Provider trust anchor (issuer, audience, and public key) is not fully configured in this environment.",
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

    # Defense against JWT Algorithm Confusion attacks (RFC 8725)
    # Never allow symmetric algorithms (HS256/384/512) when validating against public keys
    # Never allow symmetric algorithms in production or staging environments
    if alg_upper.startswith("HS"):
        if env in ("production", "staging"):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"ALGORITHM_CONFUSION_REJECTED: Symmetric algorithm '{alg_upper}' is strictly prohibited in {env} environments. Asymmetric signatures (RS256/ES256) are required.",
            )
        # Even in local/test, if a public key is configured, symmetric algorithm is rejected
        if idp_pubkey and "PUBLIC KEY" in str(idp_pubkey):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"ALGORITHM_CONFUSION_REJECTED: Token specifies symmetric algorithm '{alg_upper}' but asymmetric public key is configured as trust anchor.",
            )

    # In production/staging, only permitted asymmetric algorithms are allowed
    allowed_algs = set(getattr(settings, "MYCA_IDP_ALLOWED_ALGORITHMS", list(ASYMMETRIC_ALGORITHMS)))
    if env in ("production", "staging") and alg_upper not in allowed_algs:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"ASYMMETRIC_ALGORITHM_REQUIRED: Algorithm '{alg_upper}' is not in approved asymmetric algorithms: {sorted(list(allowed_algs))}",
        )

    # Key ID check (if configured)
    expected_kid = os.getenv("MYCA_IDP_KEY_ID") or getattr(settings, "MYCA_IDP_KEY_ID", None)
    token_kid = header.get("kid")
    if expected_kid:
        if not token_kid or token_kid != expected_kid:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"UNTRUSTED_KEY_ID: Token key ID '{token_kid}' does not match trusted key ID '{expected_kid}'.",
            )

    # 5. Resolve verification key and allowed algorithms for decoding
    verification_key: Optional[str] = None
    decoding_algorithms: List[str] = [alg_upper]

    if idp_pubkey:
        verification_key = idp_pubkey
    elif env in ("development", "test"):
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
        elif "audience" in msg.lower():
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"INVALID_IDP_AUDIENCE: Token audience does not match trusted audience '{idp_audience}'.",
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"INVALID_IDP_CLAIMS: {msg}",
        )
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"INVALID_IDP_SIGNATURE: Cryptographic signature verification failed: {str(exc)}",
        )

    # 7. Provider-to-Issuer binding check
    token_iss = str(claims.get("iss", "")).strip().lower()
    if provider_clean in PROVIDER_ISSUER_PATTERNS:
        expected_patterns = PROVIDER_ISSUER_PATTERNS[provider_clean]
        if not any(pattern in token_iss for pattern in expected_patterns):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"PROVIDER_ISSUER_MISMATCH: Token issuer '{claims.get('iss')}' is not a valid issuer for provider '{idp_provider}'.",
            )

    # 8. Strict Email Claim Check (Require actual email claim; do not use sub as email)
    if "email" not in claims or claims.get("email") is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="MISSING_EMAIL_CLAIM: IdP token must contain an explicit 'email' claim. Subject identifier fallback is strictly prohibited.",
        )
    email = claims.get("email")
    if not isinstance(email, str) or not email.strip() or "@" not in email:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="INVALID_EMAIL_FORMAT: IdP token contains a malformed or empty email claim.",
        )

    # 9. Strict email_verified Claim Check
    # Must be explicitly present, must be boolean type, and must be strictly True
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

    # 10. Nonce Matching (if expected_nonce is supplied)
    if expected_nonce is not None:
        token_nonce = claims.get("nonce")
        if not token_nonce or str(token_nonce).strip() != str(expected_nonce).strip():
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"NONCE_MISMATCH: Token nonce '{token_nonce}' does not match authentication transaction nonce '{expected_nonce}'.",
            )

    return claims
