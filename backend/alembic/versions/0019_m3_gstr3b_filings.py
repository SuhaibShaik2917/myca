"""Milestone 3: GSTR-3B Filings, Electronic Ledger Offset & Maker-Checker State Machine

Revision ID: 0019_m3_gstr3b_filings
Revises: 0018_m2_7_1_last_lot_integrity
Create Date: 2026-11-22 22:40:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '0019_m3_gstr3b_filings'
down_revision: Union[str, None] = '0018_m2_7_1_last_lot_integrity'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
    -- 1. Create GSTR-3B Filings Table
    CREATE TABLE IF NOT EXISTS public.gstr3b_filings (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id UUID NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
        registration_id UUID NOT NULL REFERENCES public.gst_registrations(id) ON DELETE CASCADE,
        tax_period VARCHAR(16) NOT NULL,
        filing_status VARCHAR(32) NOT NULL DEFAULT 'DRAFT',
        table_3_1_data JSONB NOT NULL DEFAULT '{}'::jsonb,
        table_4_data JSONB NOT NULL DEFAULT '{}'::jsonb,
        table_5_data JSONB NOT NULL DEFAULT '{}'::jsonb,
        offset_data JSONB NOT NULL DEFAULT '{}'::jsonb,
        maker_id UUID,
        checker_id UUID,
        maker_remarks TEXT,
        checker_remarks TEXT,
        arn VARCHAR(64),
        cin VARCHAR(64),
        filing_date TIMESTAMPTZ,
        payload_hash VARCHAR(64),
        created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        CONSTRAINT chk_gstr3b_filing_status CHECK (filing_status IN (
            'DRAFT',
            'SUBMITTED_FOR_REVIEW',
            'APPROVED_BY_CHECKER',
            'REJECTED_BY_CHECKER',
            'FILED'
        )),
        CONSTRAINT uq_gstr3b_filings_reg_period UNIQUE (org_id, registration_id, tax_period)
    );

    -- 2. Indexes
    CREATE INDEX IF NOT EXISTS idx_gstr3b_filings_org_period 
        ON public.gstr3b_filings (org_id, registration_id, tax_period);
    CREATE INDEX IF NOT EXISTS idx_gstr3b_filings_status 
        ON public.gstr3b_filings (org_id, filing_status);

    -- 3. Row Level Security Policies
    ALTER TABLE public.gstr3b_filings ENABLE ROW LEVEL SECURITY;
    ALTER TABLE public.gstr3b_filings FORCE ROW LEVEL SECURITY;

    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_policies WHERE tablename = 'gstr3b_filings' AND policyname = 'gstr3b_filings_tenant'
        ) THEN
            CREATE POLICY gstr3b_filings_tenant ON public.gstr3b_filings
                FOR ALL
                TO app_service_user
                USING (org_id = (NULLIF(current_setting('app.current_org', true), ''))::uuid)
                WITH CHECK (org_id = (NULLIF(current_setting('app.current_org', true), ''))::uuid);
        END IF;

        IF NOT EXISTS (
            SELECT 1 FROM pg_policies WHERE tablename = 'gstr3b_filings' AND policyname = 'gstr3b_filings_admin'
        ) THEN
            CREATE POLICY gstr3b_filings_admin ON public.gstr3b_filings
                FOR ALL
                TO platform_admin
                USING (true)
                WITH CHECK (true);
        END IF;
    END $$;

    -- 4. Permissions
    GRANT SELECT, INSERT, UPDATE, DELETE ON public.gstr3b_filings TO app_service_user;
    """)


def downgrade() -> None:
    op.execute("""
    DROP TABLE IF EXISTS public.gstr3b_filings CASCADE;
    """)
