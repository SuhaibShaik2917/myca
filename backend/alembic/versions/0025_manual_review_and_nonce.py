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

    -- Restore any previously downgraded manual review records back to MANUAL_DOCUMENT_REVIEWED
    UPDATE public.statutory_auditors
        SET verification_status = 'MANUAL_DOCUMENT_REVIEWED'
        WHERE verification_source = 'MANUAL_EVIDENCE_REVIEW'
          AND evidence_sha256 IS NOT NULL
          AND reviewer_session_ref IS NOT NULL
          AND verification_status = 'PENDING_INDEPENDENT_VERIFICATION';

    -- 2. Create partial unique index on gateway_response_id for atomic persistent nonce replay defense
    CREATE UNIQUE INDEX IF NOT EXISTS uq_statutory_auditors_gateway_nonce
        ON public.statutory_auditors (gateway_response_id)
        WHERE gateway_response_id IS NOT NULL;

    -- 3. Dedicated PostgreSQL nonce/JTI registry for atomic gateway replay protection
    CREATE TABLE IF NOT EXISTS public.statutory_gateway_nonce_registry (
        nonce VARCHAR(128) PRIMARY KEY,
        issuer VARCHAR(255) NOT NULL,
        kid VARCHAR(128) NOT NULL,
        membership_no VARCHAR(32) NOT NULL,
        evidence_ref VARCHAR(255) NOT NULL,
        issued_at TIMESTAMPTZ NOT NULL,
        expires_at TIMESTAMPTZ NOT NULL,
        consumed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        consumed_by VARCHAR(128) NULL
    );

    REVOKE ALL ON public.statutory_gateway_nonce_registry FROM app_service_user;
    GRANT SELECT, INSERT ON public.statutory_gateway_nonce_registry TO app_service_user;

    -- 4. Ensure least-privilege role grants on statutory_auditor_verification_events (SELECT, INSERT only)
    REVOKE ALL ON public.statutory_auditor_verification_events FROM app_service_user;
    GRANT SELECT, INSERT ON public.statutory_auditor_verification_events TO app_service_user;
    """)


def downgrade() -> None:
    op.execute("""
    -- SAFE DOWNGRADE POLICY FOR POPULATED RECORDS:
    -- 1. Preserve all documentary evidence, SHA-256 hashes, reviewer session references,
    --    timestamps, and audit ledger entries in statutory_auditor_verification_events intact.
    -- 2. Safely transition verification_status from MANUAL_DOCUMENT_REVIEWED to
    --    PENDING_INDEPENDENT_VERIFICATION so records satisfy the reverted constraint.
    -- 3. Never delete records, never discard evidence, and never convert to independent verification.
    UPDATE public.statutory_auditors
        SET verification_status = 'PENDING_INDEPENDENT_VERIFICATION'
        WHERE verification_status = 'MANUAL_DOCUMENT_REVIEWED';

    DROP INDEX IF EXISTS public.uq_statutory_auditors_gateway_nonce;
    DROP TABLE IF EXISTS public.statutory_gateway_nonce_registry CASCADE;

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
