import ipaddress
import os
from typing import Optional, List
from urllib.parse import urlparse
from pydantic_settings import BaseSettings
from pydantic import ConfigDict, Field, field_validator

VALID_ENVIRONMENTS = {"production", "staging", "development", "test"}
LOCAL_TEST_ENVIRONMENTS = {"development", "test"}


def _resolve_env_for_validator(info) -> Optional[str]:
    raw_env = info.data.get("ENVIRONMENT") if hasattr(info, "data") else None
    if not raw_env:
        raw_env = os.getenv("ENVIRONMENT")
    return str(raw_env).strip().lower() if raw_env else None


class Settings(BaseSettings):
    PROJECT_NAME: str = "MyCA"
    VERSION: str = "1.0.0"
    API_V1_STR: str = "/api/v1"
    ENVIRONMENT: Optional[str] = None
    ALLOW_DEMO_TOKENS: bool = False
    ALLOW_DEV_HEADER_AUTH: bool = False

    # Federated Identity Provider (IdP) Settings
    MYCA_IDP_PROVIDER: Optional[str] = None
    MYCA_IDP_ISSUER: Optional[str] = None
    MYCA_IDP_AUDIENCE: Optional[str] = None
    MYCA_IDP_PUBLIC_KEY: Optional[str] = None
    MYCA_IDP_KEY_ID: Optional[str] = None
    MYCA_IDP_JWKS_URL: Optional[str] = None
    MYCA_IDP_DISCOVERY_URL: Optional[str] = None
    MYCA_IDP_TOKEN_URL: Optional[str] = None
    MYCA_IDP_CLIENT_SECRET: Optional[str] = None
    MYCA_IDP_REDIRECT_URIS: List[str] = []
    MYCA_IDP_JWKS_CACHE_TTL_SECONDS: int = 300
    MYCA_IDP_JWKS_TIMEOUT_SECONDS: float = 5.0
    MYCA_IDP_ALLOWED_PROVIDERS: List[str] = ["google", "azuread", "okta", "auth0"]
    MYCA_IDP_ALLOWED_ALGORITHMS: List[str] = ["RS256", "RS384", "RS512", "ES256", "ES384", "ES512"]

    # ICAI Digital Verification Gateway Settings
    ICAI_GATEWAY_JWKS_URL: Optional[str] = None

    # GSP / GSTN Gateway Settings (Sandbox by default; live filing fails closed without credentials)
    GSP_MODE: str = "sandbox"
    GSTN_GSP_BASE_URL: Optional[str] = None
    GSTN_GSP_CLIENT_ID: Optional[str] = None
    GSTN_GSP_CLIENT_SECRET: Optional[str] = None
    GSTN_GSP_TIMEOUT_SECONDS: float = 15.0

    # Document Storage Settings
    STORAGE_BACKEND: str = "local"
    STORAGE_LOCAL_ROOT: str = "storage"
    AWS_S3_BUCKET_NAME: Optional[str] = None
    AWS_REGION: str = "ap-south-1"
    AWS_S3_KMS_KEY_ID: Optional[str] = None

    # Database (Application runtime uses least-privilege app_service_user)
    DATABASE_URL: str = "postgresql+psycopg://app_service_user:secret@localhost:5432/myca"
    DATABASE_WORKER_URL: str = "postgresql+psycopg://worker_service_user:secret@localhost:5432/myca"
    WORKER_ORG_ID: Optional[str] = None

    # CORS: Explicit allowed frontend origins (No wildcard in production)
    CORS_ORIGINS: Optional[List[str]] = Field(default=None, validate_default=True)

    # Security
    SECRET_KEY: str = "dev-insecure-secret-key-replace-in-production-with-min-32-chars"
    GSTR3B_REVIEW_ATTESTATION_KEY: Optional[str] = Field(default=None, validate_default=True)
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24
    TRUSTED_PROXIES: List[str] = ["127.0.0.1", "::1"]
    RATE_LIMIT_CLEANUP_INTERVAL_SECONDS: int = 3600
    RATE_LIMIT_RETENTION_HOURS: int = 1

    # Gemini AI
    GEMINI_API_KEY: Optional[str] = None
    GEMINI_MODEL: str = "gemini-3.8-flash"
    GEMINI_FALLBACK_MODEL: str = "gemini-3.5-flash-lite"

    @field_validator("ENVIRONMENT", mode="before")
    @classmethod
    def validate_environment(cls, v):
        raw_env = os.getenv("ENVIRONMENT") if v is None else v
        if raw_env is None or not str(raw_env).strip():
            raise ValueError(
                "CRITICAL CONFIGURATION ERROR: ENVIRONMENT is not configured. "
                "An explicit valid environment ('production', 'staging', 'development', 'test') is required at startup. "
                "Automatic fallback to development is strictly prohibited."
            )
        norm = str(raw_env).strip().lower()
        if norm not in VALID_ENVIRONMENTS:
            raise ValueError(
                f"CRITICAL CONFIGURATION ERROR: Invalid ENVIRONMENT '{raw_env}'. "
                f"Must be one of: {sorted(list(VALID_ENVIRONMENTS))}."
            )
        return norm

    @field_validator("ALLOW_DEMO_TOKENS")
    @classmethod
    def validate_allow_demo_tokens(cls, v, info):
        env_norm = _resolve_env_for_validator(info)
        if v and env_norm not in LOCAL_TEST_ENVIRONMENTS:
            raise ValueError(
                "CRITICAL SECURITY ERROR: ALLOW_DEMO_TOKENS cannot be enabled in production or staging environments. "
                "Demo token authentication is strictly prohibited outside designated local/test environments."
            )
        return v

    @field_validator("ALLOW_DEV_HEADER_AUTH")
    @classmethod
    def validate_allow_dev_header_auth(cls, v, info):
        env_norm = _resolve_env_for_validator(info)
        if v and env_norm not in LOCAL_TEST_ENVIRONMENTS:
            raise ValueError(
                "CRITICAL SECURITY ERROR: ALLOW_DEV_HEADER_AUTH cannot be enabled in production or staging environments. "
                "Header-based development authentication fallback is strictly prohibited outside designated local/test environments."
            )
        return v

    @field_validator("MYCA_IDP_ALLOWED_ALGORITHMS")
    @classmethod
    def validate_idp_algorithms(cls, v, info):
        env_norm = _resolve_env_for_validator(info)
        normalized = [str(alg).strip().upper() for alg in v if str(alg).strip()]
        if env_norm in {"production", "staging"}:
            forbidden = [alg for alg in normalized if alg == "NONE" or alg.startswith("HS")]
            if forbidden:
                raise ValueError(
                    f"CRITICAL SECURITY ERROR: Symmetric algorithms {forbidden} are strictly prohibited for IdP verification "
                    "in production and staging to prevent JWT algorithm confusion attacks. Use asymmetric algorithms (RS256, ES256, etc.)."
                )
        return normalized

    @field_validator("MYCA_IDP_JWKS_URL", "MYCA_IDP_DISCOVERY_URL", "MYCA_IDP_TOKEN_URL", "ICAI_GATEWAY_JWKS_URL")
    @classmethod
    def validate_idp_urls(cls, v, info):
        if v is None or not str(v).strip():
            return None
        cleaned = str(v).strip()
        parsed = urlparse(cleaned)
        if not parsed.scheme or not parsed.netloc or parsed.username or parsed.password or parsed.fragment:
            raise ValueError(f"CRITICAL CONFIGURATION ERROR: Malformed URL '{cleaned}'.")
        env_norm = _resolve_env_for_validator(info) or "development"
        if env_norm in ("production", "staging") and parsed.scheme.lower() != "https":
            raise ValueError(
                "CRITICAL SECURITY ERROR: MYCA_IDP_JWKS_URL, MYCA_IDP_DISCOVERY_URL, MYCA_IDP_TOKEN_URL, "
                "and ICAI_GATEWAY_JWKS_URL must use https:// in production/staging."
            )
        return cleaned

    @field_validator("MYCA_IDP_REDIRECT_URIS", mode="before")
    @classmethod
    def validate_idp_redirect_uris(cls, v, info):
        if v is None:
            return []
        if isinstance(v, str):
            entries = [u.strip() for u in v.split(",") if u.strip()]
        elif isinstance(v, (list, tuple, set)):
            entries = [str(u).strip() for u in v if str(u).strip()]
        else:
            raise ValueError("MYCA_IDP_REDIRECT_URIS must be a list of URIs or comma-separated string.")
        env_norm = _resolve_env_for_validator(info) or "development"
        for uri in entries:
            parsed = urlparse(uri)
            if not parsed.scheme or not parsed.netloc or parsed.username or parsed.password or parsed.fragment:
                raise ValueError(f"CRITICAL CONFIGURATION ERROR: Invalid OIDC redirect URI '{uri}'.")
            if env_norm in ("production", "staging") and parsed.scheme.lower() != "https":
                raise ValueError(
                    f"CRITICAL SECURITY ERROR: OIDC redirect URI '{uri}' must use https:// in production/staging."
                )
        return entries

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def validate_cors_origins(cls, v, info):
        env_norm = _resolve_env_for_validator(info) or "development"
        if v is None:
            if env_norm in LOCAL_TEST_ENVIRONMENTS:
                return ["http://localhost:5173", "http://127.0.0.1:5173"]
            return []
        if isinstance(v, str):
            raw = v.strip()
            if raw.startswith("["):
                import json
                entries = json.loads(raw)
            else:
                entries = [u.strip() for u in raw.split(",") if u.strip()]
        else:
            entries = list(v)
        cleaned: List[str] = []
        for origin in entries:
            item = str(origin).strip()
            if not item:
                continue
            if item == "*":
                raise ValueError("CRITICAL SECURITY ERROR: Wildcard CORS origin '*' is strictly prohibited.")
            parsed = urlparse(item)
            if not parsed.scheme or not parsed.netloc:
                raise ValueError(f"CRITICAL CONFIGURATION ERROR: Invalid CORS origin '{item}'.")
            if env_norm in ("production", "staging") and parsed.scheme.lower() != "https":
                raise ValueError(
                    f"CRITICAL SECURITY ERROR: Production/staging CORS origin '{item}' must use https://."
                )
            cleaned.append(item)
        return cleaned

    @field_validator("GSP_MODE")
    @classmethod
    def validate_gsp_mode(cls, v):
        norm = str(v or "sandbox").strip().lower()
        if norm not in ("sandbox", "live"):
            raise ValueError("GSP_MODE must be 'sandbox' or 'live'.")
        return norm

    @field_validator("STORAGE_BACKEND")
    @classmethod
    def validate_storage_backend(cls, v):
        norm = str(v or "local").strip().lower()
        if norm not in ("local", "s3"):
            raise ValueError("STORAGE_BACKEND must be 'local' or 's3'.")
        return norm

    @field_validator("RATE_LIMIT_CLEANUP_INTERVAL_SECONDS", "RATE_LIMIT_RETENTION_HOURS")
    @classmethod
    def validate_positive_rate_limit_settings(cls, v):
        if int(v) < 1:
            raise ValueError("Rate-limit maintenance intervals and retention hours must be >= 1.")
        return int(v)

    @field_validator("SECRET_KEY")
    @classmethod
    def validate_secret_key(cls, v, info):
        env_norm = _resolve_env_for_validator(info) or "development"
        if env_norm in ("production", "staging"):
            insecure_markers = (
                "dev-insecure",
                "test_secret",
                "test-secret",
                "myca_jwt_test",
                "default",
                "change-me",
                "changeme",
                "replace_with",
                "prod_ultra_secure",
            )
            if not v or any(marker in v.lower() for marker in insecure_markers) or len(v) < 32:
                raise ValueError(
                    "CRITICAL SECURITY ERROR: Production and staging deployments require a secure SECRET_KEY "
                    "of at least 32 characters (default or test SECRET_KEY is strictly prohibited)."
                )
        return v

    @field_validator("GSTR3B_REVIEW_ATTESTATION_KEY")
    @classmethod
    def validate_gstr3b_attestation_key(cls, v, info):
        env_norm = _resolve_env_for_validator(info) or "development"
        if env_norm in ("production", "staging"):
            insecure_markers = (
                "dev-insecure",
                "test_secret",
                "test-secret",
                "myca_jwt_test",
                "default",
                "change-me",
                "changeme",
                "replace_with",
            )
            if not v or not str(v).strip() or any(marker in str(v).lower() for marker in insecure_markers) or len(str(v)) < 32:
                raise ValueError(
                    "CRITICAL SECURITY ERROR: GSTR3B_REVIEW_ATTESTATION_KEY is required in production/staging, "
                    "must be at least 32 characters, and must be free of default/test markers."
                )
            secret_key = info.data.get("SECRET_KEY") if hasattr(info, "data") else None
            if secret_key and v == secret_key:
                raise ValueError(
                    "CRITICAL SECURITY ERROR: GSTR3B_REVIEW_ATTESTATION_KEY must be distinct from SECRET_KEY in production/staging."
                )
        return v

    @field_validator("DATABASE_URL", "DATABASE_WORKER_URL")
    @classmethod
    def validate_database_urls(cls, v, info):
        env_norm = _resolve_env_for_validator(info) or "development"
        if env_norm in ("production", "staging"):
            lowered = (v or "").lower()
            insecure_db_markers = (":secret@", ":change-me@", ":changeme@", "localhost", "127.0.0.1", "::1", "0.0.0.0")
            if not v or any(m in lowered for m in insecure_db_markers):
                raise ValueError(
                    "CRITICAL SECURITY ERROR: Production and staging deployments require non-default database URLs "
                    "(Insecure or default DATABASE_URL targeting localhost/secret is prohibited; must target a remote/dedicated host)."
                )
        return v

    @field_validator("TRUSTED_PROXIES", mode="before")
    @classmethod
    def validate_trusted_proxies(cls, v):
        if v is None:
            raise ValueError(
                "CRITICAL CONFIGURATION ERROR: TRUSTED_PROXIES cannot be None. "
                "Must configure at least one trusted proxy IP (e.g. ['127.0.0.1', '::1'])."
            )
        if isinstance(v, str):
            raw_entries = [p.strip() for p in v.split(",") if p.strip()]
        elif isinstance(v, (list, tuple, set)):
            raw_entries = [str(p).strip() for p in v if str(p).strip()]
        else:
            raise ValueError(
                "CRITICAL CONFIGURATION ERROR: TRUSTED_PROXIES must be a list of IP/CIDR strings or comma-separated string."
            )

        if not raw_entries:
            raise ValueError(
                "CRITICAL CONFIGURATION ERROR: TRUSTED_PROXIES cannot be empty. "
                "Must configure at least one trusted proxy IP (e.g. ['127.0.0.1', '::1'])."
            )

        validated = []
        for entry in raw_entries:
            if entry in ("*", "0.0.0.0/0", "::/0"):
                raise ValueError(
                    f"CRITICAL CONFIGURATION ERROR: Overly permissive wildcard proxy '{entry}' is strictly prohibited."
                )
            try:
                if "/" in entry:
                    net = ipaddress.ip_network(entry, strict=False)
                    if net.prefixlen == 0:
                        raise ValueError(
                            f"CRITICAL CONFIGURATION ERROR: Overly broad network '{entry}' (/0) is strictly prohibited."
                        )
                    validated.append(str(net))
                else:
                    addr = ipaddress.ip_address(entry)
                    validated.append(str(addr))
            except ValueError as err:
                raise ValueError(
                    f"CRITICAL CONFIGURATION ERROR: Invalid IP address or CIDR in TRUSTED_PROXIES '{entry}': {err}"
                )
        return validated

    model_config = ConfigDict(env_file=".env", case_sensitive=True, extra="ignore")


settings = Settings()
