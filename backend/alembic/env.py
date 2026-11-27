import os
import sys
from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool
from alembic import context

# Append current directory to sys.path so app models are discoverable
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.models.base import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Dynamic database URL override honoring MIGRATION_DATABASE_URL -> DB_ADMIN_URL -> DATABASE_ADMIN_URL -> DATABASE_URL
env_db_url = (
    os.environ.get("MIGRATION_DATABASE_URL")
    or os.environ.get("DB_ADMIN_URL")
    or os.environ.get("DATABASE_ADMIN_URL")
    or os.environ.get("DATABASE_URL")
)
if env_db_url:
    cleaned_url = env_db_url.strip()
    if cleaned_url.startswith("postgres://"):
        cleaned_url = "postgresql+psycopg://" + cleaned_url[len("postgres://"):]
    elif cleaned_url.startswith("postgresql://"):
        cleaned_url = "postgresql+psycopg://" + cleaned_url[len("postgresql://"):]
    config.set_main_option("sqlalchemy.url", cleaned_url)
else:
    cfg_url = config.get_main_option("sqlalchemy.url") or ""
    if cfg_url.startswith("postgres://"):
        config.set_main_option("sqlalchemy.url", "postgresql+psycopg://" + cfg_url[len("postgres://"):])
    elif cfg_url.startswith("postgresql://"):
        config.set_main_option("sqlalchemy.url", "postgresql+psycopg://" + cfg_url[len("postgresql://"):])


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_schemas=True,
        version_table_schema="public",
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_schemas=True,
            version_table_schema="public",
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
