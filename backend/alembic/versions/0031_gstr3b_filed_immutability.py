"""GSTR-3B Filed Return Immutability Trigger and Maker-Checker Constraint

Revision ID: 0031_gstr3b_filed_immutability
Revises: 0030_auth_rate_limits_rolling
Create Date: 2026-11-24 23:25:00.000000
"""
from typing import Sequence, Union
from alembic import op

revision: str = '0031_gstr3b_filed_immutability'
down_revision: Union[str, None] = '0030_auth_rate_limits_rolling'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Database trigger function enforcing strict immutability of FILED GSTR-3B returns
    op.execute("""
    CREATE OR REPLACE FUNCTION public.enforce_gstr3b_filed_immutability()
    RETURNS TRIGGER AS $$
    BEGIN
        IF TG_OP = 'DELETE' THEN
            IF OLD.filing_status = 'FILED' THEN
                RAISE EXCEPTION 'Filed GSTR-3B return % is strictly immutable. Deletion prohibited (42501).', OLD.id
                    USING ERRCODE = '42501';
            END IF;
            RETURN OLD;
        ELSIF TG_OP = 'UPDATE' THEN
            IF OLD.filing_status = 'FILED' THEN
                RAISE EXCEPTION 'Filed GSTR-3B return % is strictly immutable. Modifications prohibited (42501).', OLD.id
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """)

    # 2. Attach trigger to public.gstr3b_filings
    op.execute("""
    DROP TRIGGER IF EXISTS trg_prevent_gstr3b_filed_mutations ON public.gstr3b_filings;
    CREATE TRIGGER trg_prevent_gstr3b_filed_mutations
    BEFORE UPDATE OR DELETE ON public.gstr3b_filings
    FOR EACH ROW
    EXECUTE FUNCTION public.enforce_gstr3b_filed_immutability();
    """)

    # 3. Add Maker-Checker segregation check constraint (Maker and Checker must be distinct for approved/filed returns)
    op.execute("""
    ALTER TABLE public.gstr3b_filings
    DROP CONSTRAINT IF EXISTS chk_gstr3b_maker_checker_distinct;

    ALTER TABLE public.gstr3b_filings
    ADD CONSTRAINT chk_gstr3b_maker_checker_distinct
    CHECK (
        filing_status NOT IN ('APPROVED_BY_CHECKER', 'FILED')
        OR (maker_id IS NOT NULL AND checker_id IS NOT NULL AND maker_id IS DISTINCT FROM checker_id)
    );
    """)


def downgrade() -> None:
    op.execute("""
    ALTER TABLE public.gstr3b_filings
    DROP CONSTRAINT IF EXISTS chk_gstr3b_maker_checker_distinct;
    """)

    op.execute("""
    DROP TRIGGER IF EXISTS trg_prevent_gstr3b_filed_mutations ON public.gstr3b_filings;
    DROP FUNCTION IF EXISTS public.enforce_gstr3b_filed_immutability();
    """)
