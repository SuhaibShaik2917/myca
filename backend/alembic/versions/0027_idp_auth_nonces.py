"""Dedicated IdP Authentication Nonces and Replay Protection Registry

Revision ID: 0027_idp_auth_nonces
Revises: 0026_gateway_nonce_and_audit
Create Date: 2026-11-24 15:00:00.000000
"""
from typing import Sequence, Union
from alembic import op

revision: str = '0027_idp_auth_nonces'
down_revision: Union[str, None] = '0026_gateway_nonce_and_audit'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
    -- Dedicated PostgreSQL single-use transaction nonce registry for OIDC/IdP login replay protection
    CREATE TABLE IF NOT EXISTS public.idp_authentication_nonces (
        nonce VARCHAR(128) PRIMARY KEY,
        org_id UUID NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
        idp_provider VARCHAR(64) NOT NULL,
        status VARCHAR(32) NOT NULL DEFAULT 'ACTIVE',
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        expires_at TIMESTAMPTZ NOT NULL,
        consumed_at TIMESTAMPTZ NULL,
        consumed_by_email VARCHAR(255) NULL
    );

    CREATE INDEX IF NOT EXISTS ix_idp_auth_nonces_status_expires 
        ON public.idp_authentication_nonces(status, expires_at);
    CREATE INDEX IF NOT EXISTS ix_idp_auth_nonces_org_provider 
        ON public.idp_authentication_nonces(org_id, idp_provider);

    REVOKE ALL ON public.idp_authentication_nonces FROM app_service_user;
    GRANT SELECT, INSERT, UPDATE ON public.idp_authentication_nonces TO app_service_user;
    """)


def downgrade() -> None:
    op.execute("""
    -- SAFE DOWNGRADE POLICY FOR NONCE REPLAY DEFENSE:
    -- Consumed authentication nonces are preserved across downgrade to prevent replay of unexpired tokens.
    -- Revoke runtime privileges on rollback to match earlier security context.
    REVOKE ALL ON public.idp_authentication_nonces FROM app_service_user;
    """)
