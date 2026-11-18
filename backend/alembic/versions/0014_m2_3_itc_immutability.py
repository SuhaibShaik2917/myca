"""Milestone 2.3: ITC Ledger Database Immutability Trigger and Section 50(3) Utilization Tracking

Revision ID: 0014_m2_3_itc_immutability
Revises: 0013_m2_2_itc_ledger
Create Date: 2026-11-18 17:45:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '0014_m2_3_itc_immutability'
down_revision: Union[str, None] = '0013_m2_2_itc_ledger'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -------------------------------------------------------------------------
    # 1. Database Immutability Trigger on itc_ledger_entries (Prevent UPDATE / DELETE)
    # -------------------------------------------------------------------------
    op.execute("""
    CREATE OR REPLACE FUNCTION public.fn_prevent_itc_ledger_mutations()
    RETURNS trigger
    LANGUAGE plpgsql
    AS $$
    BEGIN
        RAISE EXCEPTION 'ITC Ledger entries are strictly immutable. UPDATE and DELETE operations are prohibited (42501).'
            USING ERRCODE = '42501';
    END;
    $$;

    DROP TRIGGER IF EXISTS trg_prevent_itc_ledger_mutations ON public.itc_ledger_entries;
    CREATE TRIGGER trg_prevent_itc_ledger_mutations
        BEFORE UPDATE OR DELETE ON public.itc_ledger_entries
        FOR EACH ROW
        EXECUTE FUNCTION public.fn_prevent_itc_ledger_mutations();
    """)

    # -------------------------------------------------------------------------
    # 2. Add Section 50(3) Read with Rule 88B(3) ECL Utilization Tracking Columns
    # -------------------------------------------------------------------------
    op.execute("""
    ALTER TABLE public.itc_ledger_entries
        ADD COLUMN IF NOT EXISTS ecl_minimum_balance numeric(15, 2),
        ADD COLUMN IF NOT EXISTS utilised_amount numeric(15, 2) NOT NULL DEFAULT 0.00,
        ADD COLUMN IF NOT EXISTS utilisation_date date,
        ADD COLUMN IF NOT EXISTS reversal_date date,
        ADD COLUMN IF NOT EXISTS interest_days integer NOT NULL DEFAULT 0,
        ADD COLUMN IF NOT EXISTS interest_rate_pct numeric(5, 2) NOT NULL DEFAULT 18.00;

    ALTER TABLE public.itc_reversal_events
        ADD COLUMN IF NOT EXISTS ecl_minimum_balance numeric(15, 2),
        ADD COLUMN IF NOT EXISTS utilised_amount numeric(15, 2) NOT NULL DEFAULT 0.00,
        ADD COLUMN IF NOT EXISTS utilisation_date date,
        ADD COLUMN IF NOT EXISTS reversal_date date;
    """)

    # -------------------------------------------------------------------------
    # 3. Restrict App Service Role Privileges (Least Privilege on Immutable Ledger)
    # -------------------------------------------------------------------------
    op.execute("""
    REVOKE UPDATE, DELETE ON public.itc_ledger_entries FROM app_service_user;
    GRANT SELECT, INSERT ON public.itc_ledger_entries TO app_service_user;
    """)


def downgrade() -> None:
    op.execute("""
    GRANT UPDATE, DELETE ON public.itc_ledger_entries TO app_service_user;

    DROP TRIGGER IF EXISTS trg_prevent_itc_ledger_mutations ON public.itc_ledger_entries;
    DROP FUNCTION IF EXISTS public.fn_prevent_itc_ledger_mutations();

    ALTER TABLE public.itc_reversal_events
        DROP COLUMN IF EXISTS reversal_date,
        DROP COLUMN IF EXISTS utilisation_date,
        DROP COLUMN IF EXISTS utilised_amount,
        DROP COLUMN IF EXISTS ecl_minimum_balance;

    ALTER TABLE public.itc_ledger_entries
        DROP COLUMN IF EXISTS interest_rate_pct,
        DROP COLUMN IF EXISTS interest_days,
        DROP COLUMN IF EXISTS reversal_date,
        DROP COLUMN IF EXISTS utilisation_date,
        DROP COLUMN IF EXISTS utilised_amount,
        DROP COLUMN IF EXISTS ecl_minimum_balance;
    """)
