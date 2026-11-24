"""Shared Distributed Rate Limiting and Query Indexing for Auth Nonces

Revision ID: 0029_auth_rate_limits
Revises: 0028_idp_nonces_rls
Create Date: 2026-11-24 19:00:00.000000
"""
from typing import Sequence, Union
from alembic import op

revision: str = '0029_auth_rate_limits'
down_revision: Union[str, None] = '0028_idp_nonces_rls'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
    -- 1. Create shared distributed rate limiting table
    CREATE TABLE IF NOT EXISTS public.auth_rate_limits (
        rate_key VARCHAR(128) PRIMARY KEY,
        window_start TIMESTAMPTZ NOT NULL,
        request_count INTEGER NOT NULL DEFAULT 1
    );

    -- 2. Add indexing on idp_authentication_nonces for high-throughput concurrency and cleanup
    CREATE INDEX IF NOT EXISTS ix_idp_nonces_ip_created ON public.idp_authentication_nonces (client_ip, created_at);
    CREATE INDEX IF NOT EXISTS ix_idp_nonces_org_created ON public.idp_authentication_nonces (org_id, created_at);

    -- 3. Grant least-privilege DML permissions to app_service_user
    GRANT SELECT, INSERT, UPDATE, DELETE ON public.auth_rate_limits TO app_service_user;
    """)


def downgrade() -> None:
    op.execute("""
    -- Drop indexes and shared rate limiting table
    DROP TABLE IF EXISTS public.auth_rate_limits;
    DROP INDEX IF EXISTS public.ix_idp_nonces_ip_created;
    DROP INDEX IF EXISTS public.ix_idp_nonces_org_created;
    """)
