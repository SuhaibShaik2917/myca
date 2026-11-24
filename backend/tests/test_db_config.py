"""
Unified Test Database Configuration Module for MyCA.
Provides standardized connection strings and credentials for admin, app, and worker roles.
Eliminates fragmented hardcoded database strings across test files.
"""

import os
from typing import Generator
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

def _to_psycopg_dsn(url: str) -> str:
    """Normalize SQLAlchemy DSN to standard psycopg connection URI."""
    if url and url.startswith("postgresql+psycopg://"):
        return "postgresql://" + url[len("postgresql+psycopg://"):]
    return url

# Admin connection string (superuser / DDL / test setup)
DB_ADMIN = _to_psycopg_dsn(
    os.environ.get("DB_ADMIN_URL")
    or os.environ.get("DATABASE_ADMIN_URL")
    or os.environ.get("TEST_DATABASE_URL")
    or "postgresql://postgres:postgres@localhost:5432/myca"
)

# App service role connection string
DB_APP = _to_psycopg_dsn(
    os.environ.get("DATABASE_URL")
    or os.environ.get("DB_APP_URL")
    or "postgresql://app_service_user:secret@localhost:5432/myca"
)

# Shared worker service role connection string
DB_WORKER = _to_psycopg_dsn(
    os.environ.get("DATABASE_WORKER_URL")
    or os.environ.get("DB_WORKER_URL")
    or "postgresql://worker_service_user:secret@localhost:5432/myca"
)

# Distinct worker 1 service role connection string
DB_WORKER_1 = _to_psycopg_dsn(
    os.environ.get("DATABASE_WORKER_1_URL")
    or os.environ.get("DB_WORKER_1_URL")
    or "postgresql://worker_service_user_1:secret@localhost:5432/myca"
)

# Distinct worker 2 service role connection string
DB_WORKER_2 = _to_psycopg_dsn(
    os.environ.get("DATABASE_WORKER_2_URL")
    or os.environ.get("DB_WORKER_2_URL")
    or "postgresql://worker_service_user_2:secret@localhost:5432/myca"
)

# Admin engine and session factory for tests
_admin_engine = None
_AdminSessionLocal = None

def get_admin_engine():
    global _admin_engine
    if _admin_engine is None:
        _admin_engine = create_engine(DB_ADMIN, pool_pre_ping=True)
    return _admin_engine

def get_admin_session() -> Session:
    global _AdminSessionLocal
    if _AdminSessionLocal is None:
        _AdminSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=get_admin_engine())
    return _AdminSessionLocal()
