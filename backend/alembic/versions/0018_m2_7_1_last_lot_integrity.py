"""Milestone 2.7.1: Last-Lot Statutory Integrity, Accounting States & Unsafe Tolerance Removal

Revision ID: 0018_m2_7_1_last_lot_integrity
Revises: 0017_m2_7_receipt_lot_ledger
Create Date: 2026-11-18 20:45:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '0018_m2_7_1_last_lot_integrity'
down_revision: Union[str, None] = '0017_m2_7_receipt_lot_ledger'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -------------------------------------------------------------------------
    # 1. Add Explicit Accounting States & Quarantined Excess Columns
    # -------------------------------------------------------------------------
    op.execute("""
    ALTER TABLE public.purchase_documents
        ADD COLUMN IF NOT EXISTS itc_accounting_state VARCHAR(32) NOT NULL DEFAULT 'NOT_YET_ELIGIBLE',
        ADD COLUMN IF NOT EXISTS quarantined_excess_quantity NUMERIC(15,2) NOT NULL DEFAULT 0.00;

    -- Add Check Constraint for Statutory ITC Accounting States
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint WHERE conname = 'chk_purchase_itc_accounting_state'
        ) THEN
            ALTER TABLE public.purchase_documents
                ADD CONSTRAINT chk_purchase_itc_accounting_state
                CHECK (itc_accounting_state IN (
                    'NOT_YET_ELIGIBLE',
                    'ELIGIBLE_NOT_AVAILED',
                    'AVAILED',
                    'REVERSED',
                    'RECLAIMABLE',
                    'RECLAIMED'
                ));
        END IF;
    END $$;

    -- Add Accounting State to ITC Ledger Entries
    ALTER TABLE public.itc_ledger_entries
        ADD COLUMN IF NOT EXISTS itc_accounting_state VARCHAR(32);
    """)

    # -------------------------------------------------------------------------
    # 2. Strict Check Constraint on Goods Receipt Lot Ledger
    # -------------------------------------------------------------------------
    op.execute("""
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint WHERE conname = 'chk_lot_ledger_positive_quantity'
        ) THEN
            ALTER TABLE public.goods_receipt_lot_ledger
                ADD CONSTRAINT chk_lot_ledger_positive_quantity
                CHECK (received_quantity > 0.00);
        END IF;
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint WHERE conname = 'chk_lot_ledger_positive_number'
        ) THEN
            ALTER TABLE public.goods_receipt_lot_ledger
                ADD CONSTRAINT chk_lot_ledger_positive_number
                CHECK (lot_number > 0);
        END IF;
    END $$;
    """)

    # -------------------------------------------------------------------------
    # 3. Backfill Existing Purchase Documents with Accurate Accounting States
    # -------------------------------------------------------------------------
    op.execute("""
    -- 1. Availed in GSTR-3B (and not reversed) -> AVAILED
    UPDATE public.purchase_documents
    SET itc_accounting_state = 'AVAILED'
    WHERE is_availed_in_3b = true AND status != 'reversed';

    -- 2. Approved with verified receipt but not yet availed in GSTR-3B -> ELIGIBLE_NOT_AVAILED
    UPDATE public.purchase_documents
    SET itc_accounting_state = 'ELIGIBLE_NOT_AVAILED'
    WHERE status = 'approved' AND receipt_status = 'RECEIVED' AND is_availed_in_3b = false;

    -- 3. Reversed in books -> REVERSED
    UPDATE public.purchase_documents
    SET itc_accounting_state = 'REVERSED'
    WHERE status = 'reversed';

    -- 4. Incomplete receipt or unapproved -> NOT_YET_ELIGIBLE
    UPDATE public.purchase_documents
    SET itc_accounting_state = 'NOT_YET_ELIGIBLE'
    WHERE (receipt_status IN ('UNKNOWN', 'NOT_RECEIVED', 'PARTIALLY_RECEIVED') OR status = 'draft')
      AND is_availed_in_3b = false;
    """)


def downgrade() -> None:
    # -------------------------------------------------------------------------
    # Downgrade: Revert Constraints and Columns Cleanly
    # -------------------------------------------------------------------------
    op.execute("""
    ALTER TABLE public.goods_receipt_lot_ledger
        DROP CONSTRAINT IF EXISTS chk_lot_ledger_positive_quantity,
        DROP CONSTRAINT IF EXISTS chk_lot_ledger_positive_number;

    ALTER TABLE public.itc_ledger_entries
        DROP COLUMN IF EXISTS itc_accounting_state;

    ALTER TABLE public.purchase_documents
        DROP CONSTRAINT IF EXISTS chk_purchase_itc_accounting_state,
        DROP COLUMN IF EXISTS quarantined_excess_quantity,
        DROP COLUMN IF EXISTS itc_accounting_state;
    """)
