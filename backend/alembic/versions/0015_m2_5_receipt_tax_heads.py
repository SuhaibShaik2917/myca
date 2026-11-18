"""Milestone 2.5: Section 16(2)(b) Receipt Gate & Component Tax-Head Integrity

Revision ID: 0015_m2_5_receipt_tax_heads
Revises: 0014_m2_3_itc_immutability
Create Date: 2026-11-18 18:35:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '0015_m2_5_receipt_tax_heads'
down_revision: Union[str, None] = '0014_m2_3_itc_immutability'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -------------------------------------------------------------------------
    # 1. Add Section 16(2)(b) Receipt Gate Tracking to purchase_documents
    # -------------------------------------------------------------------------
    op.execute("""
    ALTER TABLE public.purchase_documents
        ADD COLUMN IF NOT EXISTS receipt_status TEXT NOT NULL DEFAULT 'UNKNOWN',
        ADD COLUMN IF NOT EXISTS delivery_challan_ref TEXT,
        ADD COLUMN IF NOT EXISTS service_completion_date DATE,
        ADD COLUMN IF NOT EXISTS deemed_receipt_reason TEXT,
        ADD COLUMN IF NOT EXISTS receipt_verified_by UUID REFERENCES public.global_users(id) ON DELETE SET NULL,
        ADD COLUMN IF NOT EXISTS receipt_verified_at TIMESTAMPTZ;

    ALTER TABLE public.purchase_documents
        DROP CONSTRAINT IF EXISTS chk_purchase_doc_receipt_status;

    ALTER TABLE public.purchase_documents
        ADD CONSTRAINT chk_purchase_doc_receipt_status
        CHECK (receipt_status IN ('RECEIVED', 'PARTIALLY_RECEIVED', 'NOT_RECEIVED', 'DEEMED_RECEIVED', 'UNKNOWN'));
    """)

    # -------------------------------------------------------------------------
    # 2. Grant permissions to app_service_user
    # -------------------------------------------------------------------------
    op.execute("""
    GRANT SELECT, INSERT, UPDATE ON public.purchase_documents TO app_service_user;
    """)


def downgrade() -> None:
    op.execute("""
    ALTER TABLE public.purchase_documents
        DROP CONSTRAINT IF EXISTS chk_purchase_doc_receipt_status;

    ALTER TABLE public.purchase_documents
        DROP COLUMN IF EXISTS receipt_status,
        DROP COLUMN IF EXISTS delivery_challan_ref,
        DROP COLUMN IF EXISTS service_completion_date,
        DROP COLUMN IF EXISTS deemed_receipt_reason,
        DROP COLUMN IF EXISTS receipt_verified_by,
        DROP COLUMN IF EXISTS receipt_verified_at;
    """)
