"""Milestone 4 & 5: Grants and Tenant Policies for Credit Builder and Scheme Navigator

Revision ID: 0020_m4_m5_grants_and_policies
Revises: 0019_m3_gstr3b_filings
Create Date: 2026-11-22 22:46:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '0020_m4_m5_grants_and_policies'
down_revision: Union[str, None] = '0019_m3_gstr3b_filings'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
    -- 1. Grants to app_service_user
    GRANT SELECT, INSERT, UPDATE, DELETE ON public.business_profiles TO app_service_user;
    GRANT SELECT, INSERT, UPDATE, DELETE ON public.financial_transactions TO app_service_user;
    GRANT SELECT, INSERT, UPDATE, DELETE ON public.financial_summaries TO app_service_user;
    GRANT SELECT, INSERT, UPDATE, DELETE ON public.summary_transactions TO app_service_user;
    GRANT SELECT, INSERT, UPDATE, DELETE ON public.government_schemes TO app_service_user;
    GRANT SELECT, INSERT, UPDATE, DELETE ON public.scheme_eligibility_rules TO app_service_user;
    GRANT SELECT, INSERT, UPDATE, DELETE ON public.scheme_matches TO app_service_user;
    GRANT SELECT, INSERT, UPDATE, DELETE ON public.gst_records TO app_service_user;

    -- 2. Add RLS Policies for app_service_user if not already present
    DO $$
    BEGIN
        -- business_profiles
        IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'business_profiles' AND policyname = 'business_profiles_service') THEN
            CREATE POLICY business_profiles_service ON public.business_profiles
                FOR ALL TO app_service_user
                USING (org_id = (NULLIF(current_setting('app.current_org', true), ''))::uuid)
                WITH CHECK (org_id = (NULLIF(current_setting('app.current_org', true), ''))::uuid);
        END IF;

        -- financial_transactions
        IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'financial_transactions' AND policyname = 'financial_transactions_service') THEN
            CREATE POLICY financial_transactions_service ON public.financial_transactions
                FOR ALL TO app_service_user
                USING (org_id = (NULLIF(current_setting('app.current_org', true), ''))::uuid)
                WITH CHECK (org_id = (NULLIF(current_setting('app.current_org', true), ''))::uuid);
        END IF;

        -- financial_summaries
        IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'financial_summaries' AND policyname = 'financial_summaries_service') THEN
            CREATE POLICY financial_summaries_service ON public.financial_summaries
                FOR ALL TO app_service_user
                USING (org_id = (NULLIF(current_setting('app.current_org', true), ''))::uuid)
                WITH CHECK (org_id = (NULLIF(current_setting('app.current_org', true), ''))::uuid);
        END IF;

        -- summary_transactions
        IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'summary_transactions' AND policyname = 'summary_transactions_service') THEN
            CREATE POLICY summary_transactions_service ON public.summary_transactions
                FOR ALL TO app_service_user
                USING (org_id = (NULLIF(current_setting('app.current_org', true), ''))::uuid)
                WITH CHECK (org_id = (NULLIF(current_setting('app.current_org', true), ''))::uuid);
        END IF;

        -- scheme_matches
        IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'scheme_matches' AND policyname = 'scheme_matches_service') THEN
            CREATE POLICY scheme_matches_service ON public.scheme_matches
                FOR ALL TO app_service_user
                USING (org_id = (NULLIF(current_setting('app.current_org', true), ''))::uuid)
                WITH CHECK (org_id = (NULLIF(current_setting('app.current_org', true), ''))::uuid);
        END IF;

        -- gst_records
        IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'gst_records' AND policyname = 'gst_records_service') THEN
            CREATE POLICY gst_records_service ON public.gst_records
                FOR ALL TO app_service_user
                USING (org_id = (NULLIF(current_setting('app.current_org', true), ''))::uuid)
                WITH CHECK (org_id = (NULLIF(current_setting('app.current_org', true), ''))::uuid);
        END IF;

        -- government_schemes is public reference catalogue: all authenticated tenants can view
        IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'government_schemes' AND policyname = 'gov_schemes_read_all') THEN
            CREATE POLICY gov_schemes_read_all ON public.government_schemes
                FOR SELECT TO app_service_user USING (true);
        END IF;

        -- scheme_eligibility_rules is public reference catalogue
        IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'scheme_eligibility_rules' AND policyname = 'scheme_rules_read_all') THEN
            CREATE POLICY scheme_rules_read_all ON public.scheme_eligibility_rules
                FOR SELECT TO app_service_user USING (true);
        END IF;
    END $$;
    """)


def downgrade() -> None:
    pass
