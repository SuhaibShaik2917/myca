"""Statutory Auditor Manual Review Status and Nonce Defense

Revision ID: 0025_manual_review_and_nonce
Revises: 0024_auditor_authentic_evidence
Create Date: 2026-11-24 01:00:00.000000
"""
from typing import Sequence, Union
from alembic import op

revision: str = '0025_manual_review_and_nonce'
down_revision: Union[str, None] = '0024_auditor_authentic_evidence'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Update verification_status constraint to include MANUAL_DOCUMENT_REVIEWED
    op.execute("""
    ALTER TABLE public.statutory_auditors
        DROP CONSTRAINT IF EXISTS chk_statutory_auditors_verification_status;

    ALTER TABLE public.statutory_auditors
        ADD CONSTRAINT chk_statutory_auditors_verification_status
        CHECK (verification_status IN (
            'MANUAL_DOCUMENT_REVIEWED',
            'PENDING_INDEPENDENT_VERIFICATION',
            'INDEPENDENTLY_VERIFIED',
            'VERIFICATION_FAILED',
            'SUSPENDED',
            'REVOKED'
        ));

    -- 2. Create partial unique index on gateway_response_id for atomic persistent nonce replay defense
    CREATE UNIQUE INDEX IF NOT EXISTS uq_statutory_auditors_gateway_nonce
        ON public.statutory_auditors (gateway_response_id)
        WHERE gateway_response_id IS NOT NULL;

    -- 3. Ensure least-privilege role grants on statutory_auditor_verification_events (SELECT, INSERT only)
    REVOKE ALL ON public.statutory_auditor_verification_events FROM app_service_user;
    GRANT SELECT, INSERT ON public.statutory_auditor_verification_events TO app_service_user;
    """)


def downgrade() -> None:
    op.execute("""
    DROP INDEX IF EXISTS public.uq_statutory_auditors_gateway_nonce;

    ALTER TABLE public.statutory_auditors
        DROP CONSTRAINT IF EXISTS chk_statutory_auditors_verification_status;

    ALTER TABLE public.statutory_auditors
        ADD CONSTRAINT chk_statutory_auditors_verification_status
        CHECK (verification_status IN (
            'PENDING_INDEPENDENT_VERIFICATION',
            'INDEPENDENTLY_VERIFIED',
            'VERIFICATION_FAILED',
            'SUSPENDED',
            'REVOKED'
        ));
    """)
