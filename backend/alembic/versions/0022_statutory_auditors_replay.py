"""Statutory Auditors Controlled Registry and Persistent Replay Protection

Revision ID: 0022_statutory_auditors_replay
Revises: 0021_enforce_tenant_force_rls
Create Date: 2026-11-23 17:50:00.000000
"""
from typing import Sequence, Union
from alembic import op

revision: str = '0022_statutory_auditors_replay'
down_revision: Union[str, None] = '0021_enforce_tenant_force_rls'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Controlled Database Table: Verified Statutory Auditors
    op.execute("""
    CREATE TABLE IF NOT EXISTS public.statutory_auditors (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        auditor_id text NOT NULL UNIQUE,
        membership_no text NOT NULL UNIQUE CHECK (membership_no ~ '^[0-9]{6}$'),
        firm_registration_no text CHECK (firm_registration_no IS NULL OR firm_registration_no ~ '^[0-9]{6}[A-Z]$'),
        name text NOT NULL,
        role text NOT NULL CHECK (role IN ('CHARTERED_ACCOUNTANT', 'TAX_AUDITOR', 'STATUTORY_AUDITOR')),
        cop_status text NOT NULL DEFAULT 'ACTIVE' CHECK (cop_status IN ('ACTIVE', 'INACTIVE', 'SUSPENDED')),
        cop_valid_until date NOT NULL,
        approved_by text NOT NULL,
        approved_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        is_revoked boolean NOT NULL DEFAULT false,
        revoked_at timestamptz,
        revocation_reason text,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
    );
    CREATE INDEX IF NOT EXISTS idx_statutory_auditors_lookup ON public.statutory_auditors(auditor_id, is_revoked, cop_status);

    -- 2. Persistent Database Table: Auditor Signature Replay Defense
    CREATE TABLE IF NOT EXISTS public.statutory_audit_signature_replays (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        token_hash text NOT NULL UNIQUE,
        doc_id text NOT NULL,
        doc_number text NOT NULL,
        verifier_id text NOT NULL,
        certified_rate numeric(5, 2) NOT NULL,
        total_tax numeric(15, 2) NOT NULL,
        recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        expires_at timestamptz NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_audit_replay_lookup ON public.statutory_audit_signature_replays(token_hash, doc_id, doc_number);

    -- 3. Least-Privilege Role Grants for app_service_user
    -- statutory_auditors is public reference catalogue: SELECT ONLY
    REVOKE ALL ON public.statutory_auditors FROM app_service_user;
    GRANT SELECT ON public.statutory_auditors TO app_service_user;

    -- statutory_audit_signature_replays: SELECT and INSERT only (no UPDATE, no DELETE: immutable ledger)
    REVOKE ALL ON public.statutory_audit_signature_replays FROM app_service_user;
    GRANT SELECT, INSERT ON public.statutory_audit_signature_replays TO app_service_user;

    -- 4. In accordance with statutory auditor provenance defense, production database must never
    -- seed fabricated demonstration auditor identities. Registry is populated exclusively via authenticated
    -- onboarding workflows with verified ICAI/COP credentials.
    """)


def downgrade() -> None:
    op.execute("""
    REVOKE ALL ON public.statutory_audit_signature_replays FROM app_service_user;
    REVOKE ALL ON public.statutory_auditors FROM app_service_user;
    DROP TABLE IF EXISTS public.statutory_audit_signature_replays CASCADE;
    DROP TABLE IF EXISTS public.statutory_auditors CASCADE;
    """)
