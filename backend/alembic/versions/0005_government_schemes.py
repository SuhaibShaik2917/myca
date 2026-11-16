"""Government Schemes and Eligibility Matching Tables

Revision ID: 0005_government_schemes
Revises: 0004_invoices_and_triggers
Create Date: 2026-11-17 04:34:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '0005_government_schemes'
down_revision: Union[str, None] = '0004_invoices_and_triggers'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    # 1. Global Government Schemes Catalog (Shared across all MSMEs)
    op.execute("""
    CREATE TABLE IF NOT EXISTS public.government_schemes (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        scheme_code text NOT NULL UNIQUE,
        name text NOT NULL,
        ministry text NOT NULL,
        category scheme_category NOT NULL,
        description text NOT NULL,
        benefits_summary text NOT NULL,
        max_subsidy_amount numeric(15, 2),
        is_active boolean NOT NULL DEFAULT true,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
    );
    CREATE INDEX IF NOT EXISTS idx_gov_schemes_category ON public.government_schemes(category);
    """)

    # 2. Scheme Eligibility Rules (Evaluated deterministically)
    op.execute("""
    CREATE TABLE IF NOT EXISTS public.scheme_eligibility_rules (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        scheme_id uuid NOT NULL REFERENCES public.government_schemes(id) ON DELETE CASCADE,
        rule_key text NOT NULL,
        operator text NOT NULL CHECK (operator IN ('=', '!=', '>', '>=', '<', '<=', 'IN', 'CONTAINS')),
        target_value text NOT NULL,
        rule_description text NOT NULL,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp()
    );
    CREATE INDEX IF NOT EXISTS idx_scheme_rules_scheme ON public.scheme_eligibility_rules(scheme_id);
    """)

    # 3. Scheme Matches (Tenant-scoped evaluation results)
    op.execute("""
    CREATE TABLE IF NOT EXISTS public.scheme_matches (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id uuid NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
        scheme_id uuid NOT NULL REFERENCES public.government_schemes(id) ON DELETE CASCADE,
        match_score numeric(5, 2) NOT NULL CHECK (match_score >= 0.00 AND match_score <= 100.00),
        status text NOT NULL DEFAULT 'recommended' CHECK (status IN ('recommended', 'applied', 'approved', 'rejected', 'ineligible')),
        evaluated_criteria jsonb NOT NULL DEFAULT '{}'::jsonb,
        application_reference text,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        CONSTRAINT uq_scheme_matches_org_scheme UNIQUE (org_id, scheme_id)
    );
    CREATE INDEX IF NOT EXISTS idx_scheme_matches_org ON public.scheme_matches(org_id);
    """)

def downgrade() -> None:
    # Explicit dependency-ordered drops (H7)
    op.execute('DROP TABLE IF EXISTS public.scheme_matches;')
    op.execute('DROP TABLE IF EXISTS public.scheme_eligibility_rules;')
    op.execute('DROP TABLE IF EXISTS public.government_schemes;')
