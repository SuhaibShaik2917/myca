"""Core Multi-Tenant Tables and User Directory View

Revision ID: 0002_core_tables
Revises: 0001_init_core
Create Date: 2026-11-17 04:31:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '0002_core_tables'
down_revision: Union[str, None] = '0001_init_core'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    # 1. Organizations
    op.execute("""
    CREATE TABLE IF NOT EXISTS public.organizations (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        legal_name text NOT NULL,
        trade_name text,
        pan text NOT NULL UNIQUE CHECK (pan ~ '^[A-Z]{5}[0-9]{4}[A-Z]{1}$'),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
    );
    """)

    # 2. Global Users (Direct access restricted; mediated via v_org_users)
    op.execute("""
    CREATE TABLE IF NOT EXISTS public.global_users (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        email text NOT NULL UNIQUE CHECK (position('@' in email) > 1),
        phone text CHECK (phone IS NULL OR phone ~ '^[0-9]{10}$'),
        full_name text NOT NULL,
        is_active boolean NOT NULL DEFAULT true,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
    );
    """)

    # 3. Organization Memberships
    op.execute("""
    CREATE TABLE IF NOT EXISTS public.organization_memberships (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id uuid NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
        user_id uuid NOT NULL REFERENCES public.global_users(id) ON DELETE CASCADE,
        role org_role NOT NULL DEFAULT 'viewer',
        status membership_status NOT NULL DEFAULT 'active',
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        CONSTRAINT uq_org_user UNIQUE (org_id, user_id)
    );
    CREATE INDEX IF NOT EXISTS idx_org_memberships_org_user ON public.organization_memberships(org_id, user_id);
    """)

    # 4. Security Barrier User Directory View
    op.execute("""
    CREATE OR REPLACE VIEW public.v_org_users
    WITH (security_barrier = true)
    AS
    SELECT 
        u.id,
        m.org_id,
        u.email,
        u.phone,
        u.full_name,
        m.role,
        m.status AS membership_status
    FROM public.global_users u
    JOIN public.organization_memberships m ON u.id = m.user_id
    WHERE m.org_id = NULLIF(current_setting('app.current_org', true), '')::uuid
      AND m.status = 'active';

    ALTER VIEW public.v_org_users OWNER TO v_org_users_owner;
    REVOKE ALL ON public.v_org_users FROM PUBLIC;
    REVOKE ALL ON public.v_org_users FROM worker_owner, worker_service_user;
    GRANT SELECT ON public.v_org_users TO app_user, platform_admin;
    """)

def downgrade() -> None:
    # Explicit dependency-ordered drops (H7)
    op.execute('DROP VIEW IF EXISTS public.v_org_users;')
    op.execute('DROP TABLE IF EXISTS public.organization_memberships;')
    op.execute('DROP TABLE IF EXISTS public.global_users;')
    op.execute('DROP TABLE IF EXISTS public.organizations;')
