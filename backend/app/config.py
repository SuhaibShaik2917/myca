import ipaddress
import os
from pydantic_settings import BaseSettings
from pydantic import ConfigDict, field_validator
from typing import Optional, List

VALID_ENVIRONMENTS = {"production", "staging", "development", "test"}
LOCAL_TEST_ENVIRONMENTS = {"development", "test"}

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
    MYCA_IDP_ALLOWED_PROVIDERS: List[str] = ["google", "azuread", "okta", "auth0"]
    MYCA_IDP_ALLOWED_ALGORITHMS: List[str] = ["RS256", "RS384", "RS512", "ES256", "ES384", "ES512"]
    
    # Database (Application runtime uses least-privilege app_service_user)
    DATABASE_URL: str = "postgresql+psycopg://app_service_user:secret@localhost:5432/myca"
    DATABASE_WORKER_URL: str = "postgresql+psycopg://worker_service_user:secret@localhost:5432/myca"
    
    # CORS: Explicit allowed frontend origins (No wildcard in production)
    CORS_ORIGINS: List[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173"
    ]
    
    # Security
    SECRET_KEY: str = "dev-insecure-secret-key-replace-in-production-with-min-32-chars"
    GSTR3B_REVIEW_ATTESTATION_KEY: Optional[str] = None
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24
    TRUSTED_PROXIES: List[str] = ["127.0.0.1", "::1"]
    RATE_LIMIT_CLEANUP_INTERVAL_SECONDS: int = 3600
    RATE_LIMIT_RETENTION_HOURS: int = 1
    
    # Gemini AI (Currently operational models for late 2026: Gemini 3.8 Flash & Gemini 3.5 Flash-Lite)
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
        raw_env = os.getenv("ENVIRONMENT")
        if not raw_env and hasattr(info, "data"):
            raw_env = info.data.get("ENVIRONMENT")
        env_norm = str(raw_env).strip().lower() if raw_env else None
        if v and env_norm not in LOCAL_TEST_ENVIRONMENTS:
            raise ValueError(
                "CRITICAL SECURITY ERROR: ALLOW_DEMO_TOKENS cannot be enabled in production or staging environments. "
                "Demo token authentication is strictly prohibited outside designated local/test environments."
            )
        return v

    @field_validator("ALLOW_DEV_HEADER_AUTH")
    @classmethod
    def validate_allow_dev_header_auth(cls, v, info):
        raw_env = os.getenv("ENVIRONMENT")
        if not raw_env and hasattr(info, "data"):
            raw_env = info.data.get("ENVIRONMENT")
        env_norm = str(raw_env).strip().lower() if raw_env else None
        if v and env_norm not in LOCAL_TEST_ENVIRONMENTS:
            raise ValueError(
                "CRITICAL SECURITY ERROR: ALLOW_DEV_HEADER_AUTH cannot be enabled in production or staging environments. "
                "Header-based development authentication fallback is strictly prohibited outside designated local/test environments."
            )
        return v

    @field_validator("MYCA_IDP_ALLOWED_ALGORITHMS")
    @classmethod
    def validate_idp_algorithms(cls, v, info):
        raw_env = os.getenv("ENVIRONMENT")
        if not raw_env and hasattr(info, "data"):
            raw_env = info.data.get("ENVIRONMENT")
        env_norm = str(raw_env).strip().lower() if raw_env else None
        if env_norm in {"production", "staging"}:
            symmetric = [alg for alg in v if alg.upper().startswith("HS")]
            if symmetric:
                raise ValueError(
                    f"CRITICAL SECURITY ERROR: Symmetric algorithms {symmetric} are strictly prohibited for IdP verification "
                    "in production and staging to prevent JWT algorithm confusion attacks. Use asymmetric algorithms (RS256, ES256, etc.)."
                )
        return v
    
    @field_validator("SECRET_KEY")
    @classmethod
    def validate_secret_key(cls, v, info):
        raw_env = os.getenv("ENVIRONMENT")
        if not raw_env and hasattr(info, "data"):
            raw_env = info.data.get("ENVIRONMENT")
        env_norm = str(raw_env).strip().lower() if raw_env else "development"
        if env_norm in ("production", "staging"):
            insecure_markers = ("dev-insecure", "test_secret", "test-secret", "myca_jwt_test", "default")
            if not v or any(marker in v.lower() for marker in insecure_markers) or len(v) < 32:
                raise ValueError("CRITICAL SECURITY ERROR: Production and staging deployments require a secure SECRET_KEY of at least 32 characters (default or test SECRET_KEY is strictly prohibited).")
        return v

    @field_validator("GSTR3B_REVIEW_ATTESTATION_KEY")
    @classmethod
    def validate_gstr3b_attestation_key(cls, v, info):
        if v is None:
            return v
        raw_env = os.getenv("ENVIRONMENT")
        if not raw_env and hasattr(info, "data"):
            raw_env = info.data.get("ENVIRONMENT")
        env_norm = str(raw_env).strip().lower() if raw_env else "development"
        if env_norm in ("production", "staging"):
            insecure_markers = ("dev-insecure", "test_secret", "test-secret", "myca_jwt_test", "default")
            if not v.strip() or any(marker in v.lower() for marker in insecure_markers) or len(v) < 32:
                raise ValueError(
                    "CRITICAL SECURITY ERROR: GSTR3B_REVIEW_ATTESTATION_KEY in production/staging must be at least 32 characters and free of default/test markers."
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
        raw_env = os.getenv("ENVIRONMENT")
        if not raw_env and hasattr(info, "data"):
            raw_env = info.data.get("ENVIRONMENT")
        env_norm = str(raw_env).strip().lower() if raw_env else "development"
        if env_norm in ("production", "staging"):
            if not v or ":secret@" in v or "localhost" in v or "127.0.0.1" in v:
                raise ValueError("CRITICAL SECURITY ERROR: Production and staging deployments require non-default database URLs (Insecure or default DATABASE_URL targeting localhost/secret is prohibited; must target a remote/dedicated host).")
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
