"""Dedicated Gateway Nonce Registry and Audit Grants

Revision ID: 0026_gateway_nonce_and_audit
Revises: 0025_manual_review_and_nonce
Create Date: 2026-11-24 02:00:00.000000
"""
from typing import Sequence, Union
from alembic import op

revision: str = '0026_gateway_nonce_and_audit'
down_revision: Union[str, None] = '0025_manual_review_and_nonce'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
    -- 1. Restore any previously downgraded manual review records back to MANUAL_DOCUMENT_REVIEWED
    UPDATE public.statutory_auditors
        SET verification_status = 'MANUAL_DOCUMENT_REVIEWED'
        WHERE verification_source = 'MANUAL_EVIDENCE_REVIEW'
          AND evidence_sha256 IS NOT NULL
          AND reviewer_session_ref IS NOT NULL
          AND verification_status = 'PENDING_INDEPENDENT_VERIFICATION';

    -- 2. Dedicated PostgreSQL nonce/JTI registry for atomic gateway replay protection
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

    -- 3. Ensure least-privilege role grants on statutory_auditor_verification_events (SELECT, INSERT only)
    REVOKE ALL ON public.statutory_auditor_verification_events FROM app_service_user;
    GRANT SELECT, INSERT ON public.statutory_auditor_verification_events TO app_service_user;
    """)


def downgrade() -> None:
    op.execute("""
    -- SAFE DOWNGRADE POLICY FOR POPULATED RECORDS & NONCE REPLAY DEFENSE:
    -- 1. Preserve all documentary evidence, SHA-256 hashes, reviewer session references,
    --    timestamps, and audit ledger entries in statutory_auditor_verification_events intact.
    -- 2. Safely transition verification_status from MANUAL_DOCUMENT_REVIEWED to
    --    PENDING_INDEPENDENT_VERIFICATION if reverting to earlier status constraints.
    -- 3. NONCE PRESERVATION: NEVER DROP OR TRUNCATE public.statutory_gateway_nonce_registry!
    --    Consumed gateway nonces are permanently preserved across downgrade and re-upgrade
    --    cycles to prevent replay of unexpired gateway verification tokens.
    -- 4. Revoke runtime privileges to reflect downgraded role schema without losing nonce data.
    UPDATE public.statutory_auditors
        SET verification_status = 'PENDING_INDEPENDENT_VERIFICATION'
        WHERE verification_status = 'MANUAL_DOCUMENT_REVIEWED';

    REVOKE ALL ON public.statutory_gateway_nonce_registry FROM app_service_user;
    """)
