import os
from pydantic_settings import BaseSettings
from pydantic import field_validator
from typing import Optional, List

class Settings(BaseSettings):
    PROJECT_NAME: str = "MyCA"
    VERSION: str = "1.0.0"
    API_V1_STR: str = "/api/v1"
    ENVIRONMENT: str = "development"
    
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
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24
    
    # Gemini AI
    GEMINI_API_KEY: Optional[str] = None
    GEMINI_MODEL: str = "gemini-2.5-flash"
    
    @field_validator("SECRET_KEY")
    def validate_secret_key(cls, v, info):
        env = os.getenv("ENVIRONMENT", "development")
        if env == "production":
            if not v or "dev-insecure" in v or len(v) < 32:
                raise ValueError("CRITICAL SECURITY ERROR: Production deployment requires a secure SECRET_KEY of at least 32 characters.")
        return v

    class Config:
        env_file = ".env"
        case_sensitive = True

settings = Settings()
