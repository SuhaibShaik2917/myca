"""Statutory Auditor Provenance and Verification Defense

Revision ID: 0023_auditor_provenance
Revises: 0022_statutory_auditors_replay
Create Date: 2026-11-23 21:05:00.000000
"""
from typing import Sequence, Union
from alembic import op

revision: str = '0023_auditor_provenance'
down_revision: Union[str, None] = '0022_statutory_auditors_replay'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Cleanse all fabricated demonstration auditor identities from production registry
    op.execute("""
    DELETE FROM public.statutory_auditors 
    WHERE auditor_id IN ('CA-ICAI-098765', 'CA-ICAI-054321', 'CA-REVOKED-011223');
    """)

    # 2. Add verification provenance and independent ICAI verification columns
    op.execute("""
    ALTER TABLE public.statutory_auditors
        ADD COLUMN IF NOT EXISTS verification_source text NOT NULL DEFAULT 'MANUAL_REVIEW',
        ADD COLUMN IF NOT EXISTS verification_status text NOT NULL DEFAULT 'PENDING_INDEPENDENT_VERIFICATION',
        ADD COLUMN IF NOT EXISTS is_icai_verified boolean NOT NULL DEFAULT false,
        ADD COLUMN IF NOT EXISTS is_cop_verified boolean NOT NULL DEFAULT false,
        ADD COLUMN IF NOT EXISTS verified_at timestamptz,
        ADD COLUMN IF NOT EXISTS verification_evidence_ref text;

    DO $$
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_statutory_auditors_verification_source') THEN
            ALTER TABLE public.statutory_auditors
                ADD CONSTRAINT chk_statutory_auditors_verification_source
                CHECK (verification_source IN ('MANUAL_REVIEW', 'ICAI_PORTAL_MANUAL_SEARCH', 'ICAI_DIGITAL_VERIFICATION_GATEWAY', 'MOCK_TEST_FIXTURE'));
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_statutory_auditors_verification_status') THEN
            ALTER TABLE public.statutory_auditors
                ADD CONSTRAINT chk_statutory_auditors_verification_status
                CHECK (verification_status IN ('PENDING_INDEPENDENT_VERIFICATION', 'INDEPENDENTLY_VERIFIED', 'VERIFICATION_FAILED', 'SUSPENDED', 'REVOKED'));
        END IF;
    END $$;
    """)


def downgrade() -> None:
    # 1. Drop constraints and provenance columns
    op.execute("""
    ALTER TABLE public.statutory_auditors
        DROP CONSTRAINT IF EXISTS chk_statutory_auditors_verification_source,
        DROP CONSTRAINT IF EXISTS chk_statutory_auditors_verification_status,
        DROP COLUMN IF EXISTS verification_source,
        DROP COLUMN IF EXISTS verification_status,
        DROP COLUMN IF EXISTS is_icai_verified,
        DROP COLUMN IF EXISTS is_cop_verified,
        DROP COLUMN IF EXISTS verified_at,
        DROP COLUMN IF EXISTS verification_evidence_ref;
    """)

    # 2. Restore 0022 historical seed records for downgrade state parity
    op.execute("""
    INSERT INTO public.statutory_auditors (
        auditor_id, membership_no, firm_registration_no, name, role, cop_status, cop_valid_until, approved_by, is_revoked
    ) VALUES 
    (
        'CA-ICAI-098765', '098765', '123456N', 'CA Rajesh Kumar', 'CHARTERED_ACCOUNTANT', 'ACTIVE', '2028-03-31', 'ICAI-VERIFIED-ADMIN-OFFICER-SYS', false
    ),
    (
        'CA-ICAI-054321', '054321', '654321W', 'CA Priya Sharma', 'TAX_AUDITOR', 'ACTIVE', '2027-12-31', 'ICAI-VERIFIED-ADMIN-OFFICER-SYS', false
    ),
    (
        'CA-REVOKED-011223', '011223', '112233S', 'CA Suresh Gupta', 'CHARTERED_ACCOUNTANT', 'SUSPENDED', '2024-03-31', 'ICAI-VERIFIED-ADMIN-OFFICER-SYS', true
    )
    ON CONFLICT (auditor_id) DO NOTHING;
    """)
