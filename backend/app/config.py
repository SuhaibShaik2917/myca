from pydantic_settings import BaseSettings
from typing import Optional

class Settings(BaseSettings):
    PROJECT_NAME: str = "MyCA"
    VERSION: str = "1.0.0"
    API_V1_STR: str = "/api/v1"
    
    # Database
    DATABASE_URL: str = "postgresql+psycopg://app_service_user:secret@localhost:5432/myca"
    DATABASE_ADMIN_URL: str = "postgresql+psycopg://postgres:postgres@localhost:5432/myca"
    DATABASE_WORKER_URL: str = "postgresql+psycopg://worker_service_user:secret@localhost:5432/myca"
    
    # Security
    SECRET_KEY: str = "dev-insecure-secret-key-replace-in-production-with-min-32-chars"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24
    
    # Gemini AI
    GEMINI_API_KEY: Optional[str] = None
    GEMINI_MODEL: str = "gemini-2.5-flash"
    
    class Config:
        env_file = ".env"
        case_sensitive = True

settings = Settings()
