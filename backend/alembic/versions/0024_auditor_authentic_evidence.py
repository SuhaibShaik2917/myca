"""Statutory Auditor Authentic Evidence and Verification Provenance

Revision ID: 0024_auditor_authentic_evidence
Revises: 0023_auditor_provenance
Create Date: 2026-11-24 00:05:00.000000
"""
from typing import Sequence, Union
from alembic import op

revision: str = '0024_auditor_authentic_evidence'
down_revision: Union[str, None] = '0023_auditor_provenance'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Update verification_source constraint to include MANUAL_EVIDENCE_REVIEW
    op.execute("""
    ALTER TABLE public.statutory_auditors
        DROP CONSTRAINT IF EXISTS chk_statutory_auditors_verification_source;

    ALTER TABLE public.statutory_auditors
        ADD CONSTRAINT chk_statutory_auditors_verification_source
        CHECK (verification_source IN (
            'MANUAL_REVIEW',
            'MANUAL_EVIDENCE_REVIEW',
            'ICAI_PORTAL_MANUAL_SEARCH',
            'ICAI_DIGITAL_VERIFICATION_GATEWAY',
            'MOCK_TEST_FIXTURE'
        ));

    -- 2. Add authentic evidence tracking columns to statutory_auditors
    ALTER TABLE public.statutory_auditors
        ADD COLUMN IF NOT EXISTS evidence_sha256 text,
        ADD COLUMN IF NOT EXISTS reviewer_session_ref text,
        ADD COLUMN IF NOT EXISTS gateway_kid text,
        ADD COLUMN IF NOT EXISTS gateway_response_id text;

    -- 3. Create immutable statutory auditor verification events ledger
    CREATE TABLE IF NOT EXISTS public.statutory_auditor_verification_events (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        auditor_id text NOT NULL,
        membership_no text NOT NULL,
        verification_method text NOT NULL CHECK (verification_method IN ('ICAI_GATEWAY', 'MANUAL_REVIEW', 'MOCK_TEST_FIXTURE')),
        verification_status text NOT NULL,
        evidence_ref text,
        evidence_sha256 text,
        reviewer_id text,
        reviewer_session_ref text,
        decision text NOT NULL CHECK (decision IN ('APPROVED', 'REJECTED', 'PENDING', 'REVOKED')),
        decision_reason text,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp()
    );

    CREATE INDEX IF NOT EXISTS idx_auditor_verif_events 
        ON public.statutory_auditor_verification_events(auditor_id, created_at DESC);

    -- 4. Least-privilege role grants for app_service_user (immutable ledger: SELECT and INSERT only)
    REVOKE ALL ON public.statutory_auditor_verification_events FROM app_service_user;
    GRANT SELECT, INSERT ON public.statutory_auditor_verification_events TO app_service_user;
    """)


def downgrade() -> None:
    op.execute("""
    REVOKE ALL ON public.statutory_auditor_verification_events FROM app_service_user;
    DROP TABLE IF EXISTS public.statutory_auditor_verification_events CASCADE;

    ALTER TABLE public.statutory_auditors
        DROP COLUMN IF EXISTS evidence_sha256,
        DROP COLUMN IF EXISTS reviewer_session_ref,
        DROP COLUMN IF EXISTS gateway_kid,
        DROP COLUMN IF EXISTS gateway_response_id;

    ALTER TABLE public.statutory_auditors
        DROP CONSTRAINT IF EXISTS chk_statutory_auditors_verification_source;

    ALTER TABLE public.statutory_auditors
        ADD CONSTRAINT chk_statutory_auditors_verification_source
        CHECK (verification_source IN (
            'MANUAL_REVIEW',
            'ICAI_PORTAL_MANUAL_SEARCH',
            'ICAI_DIGITAL_VERIFICATION_GATEWAY',
            'MOCK_TEST_FIXTURE'
        ));
    """)
