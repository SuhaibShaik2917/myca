"""Enforce RLS and Browser Session Binding on IdP Authentication Nonces

Revision ID: 0028_idp_nonces_rls
Revises: 0027_idp_auth_nonces
Create Date: 2026-11-24 17:00:00.000000
"""
from typing import Sequence, Union
from alembic import op

revision: str = '0028_idp_nonces_rls'
down_revision: Union[str, None] = '0027_idp_auth_nonces'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
    -- 1. Add schema columns for browser-session binding and PKCE S256
    ALTER TABLE public.idp_authentication_nonces
        ADD COLUMN IF NOT EXISTS state VARCHAR(128) NULL,
        ADD COLUMN IF NOT EXISTS code_challenge VARCHAR(128) NULL,
        ADD COLUMN IF NOT EXISTS session_binding_hash VARCHAR(128) NULL,
        ADD COLUMN IF NOT EXISTS client_ip VARCHAR(64) NULL;

    CREATE INDEX IF NOT EXISTS ix_idp_auth_nonces_session_hash
        ON public.idp_authentication_nonces(session_binding_hash);

    -- 2. Enable RLS and FORCE RLS on idp_authentication_nonces
    ALTER TABLE public.idp_authentication_nonces ENABLE ROW LEVEL SECURITY;
    ALTER TABLE public.idp_authentication_nonces FORCE ROW LEVEL SECURITY;

    -- 3. Create tenant isolation policy for app_service_user
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_policies
            WHERE tablename = 'idp_authentication_nonces'
              AND policyname = 'idp_auth_nonces_service_policy'
        ) THEN
            CREATE POLICY idp_auth_nonces_service_policy ON public.idp_authentication_nonces
                FOR ALL TO app_service_user
                USING (org_id = (NULLIF(current_setting('app.current_org', true), ''))::uuid)
                WITH CHECK (org_id = (NULLIF(current_setting('app.current_org', true), ''))::uuid);
        END IF;
    END $$;

    -- 4. Ensure least privilege grants including DELETE for safe expired nonce cleanup
    GRANT SELECT, INSERT, UPDATE, DELETE ON public.idp_authentication_nonces TO app_service_user;
    """)


def downgrade() -> None:
    op.execute("""
    -- Drop policy and disable RLS
    DROP POLICY IF EXISTS idp_auth_nonces_service_policy ON public.idp_authentication_nonces;
    ALTER TABLE public.idp_authentication_nonces NO FORCE ROW LEVEL SECURITY;
    ALTER TABLE public.idp_authentication_nonces DISABLE ROW LEVEL SECURITY;

    DROP INDEX IF EXISTS public.ix_idp_auth_nonces_session_hash;

    ALTER TABLE public.idp_authentication_nonces
        DROP COLUMN IF EXISTS state,
        DROP COLUMN IF EXISTS code_challenge,
        DROP COLUMN IF EXISTS session_binding_hash,
        DROP COLUMN IF EXISTS client_ip;

    -- Nonces preserved on downgrade; revoke DELETE to match 0027 baseline
    REVOKE DELETE ON public.idp_authentication_nonces FROM app_service_user;
    """)
