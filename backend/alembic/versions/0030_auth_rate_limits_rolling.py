"""Rolling 60-Second Sliding Window Distributed Rate Limiting & Bounded Retention

Revision ID: 0030_auth_rate_limits_rolling
Revises: 0029_auth_rate_limits
Create Date: 2026-11-24 20:50:00.000000
"""
from typing import Sequence, Union
from alembic import op

revision: str = '0030_auth_rate_limits_rolling'
down_revision: Union[str, None] = '0029_auth_rate_limits'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
    -- 1. Upgrade public.auth_rate_limits to support genuine rolling 60-second sliding windows
    ALTER TABLE public.auth_rate_limits
        DROP COLUMN IF EXISTS window_start,
        DROP COLUMN IF EXISTS request_count,
        ADD COLUMN timestamps TIMESTAMPTZ[] NOT NULL DEFAULT '{}'::timestamptz[],
        ADD COLUMN updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp();

    -- 2. Index updated_at for fast, non-blocking bounded retention cleanup
    CREATE INDEX IF NOT EXISTS ix_auth_rate_limits_updated_at ON public.auth_rate_limits (updated_at);
    """)


def downgrade() -> None:
    op.execute("""
    -- Revert back to calendar-minute fixed window structure
    DROP INDEX IF EXISTS public.ix_auth_rate_limits_updated_at;

    ALTER TABLE public.auth_rate_limits
        DROP COLUMN IF EXISTS timestamps,
        DROP COLUMN IF EXISTS updated_at,
        ADD COLUMN window_start TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        ADD COLUMN request_count INTEGER NOT NULL DEFAULT 1;
    """)
