"""Milestone 2.6: Receipt Gate Hardening, Last-Lot Tracking & Unbooked Candidates

Revision ID: 0016_m2_6_receipt_gate_reversal
Revises: 0015_m2_5_receipt_tax_heads
Create Date: 2026-11-18 18:55:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '0016_m2_6_receipt_gate_reversal'
down_revision: Union[str, None] = '0015_m2_5_receipt_tax_heads'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -------------------------------------------------------------------------
    # 1. Hardened Defaults & Historical Audit on purchase_documents
    # -------------------------------------------------------------------------
    op.execute("""
    -- Explicitly change column default from 'RECEIVED' to 'UNKNOWN'
    ALTER TABLE public.purchase_documents
        ALTER COLUMN receipt_status SET DEFAULT 'UNKNOWN';

    -- Audit historical rows: records without verified evidence must be UNKNOWN
    UPDATE public.purchase_documents
    SET receipt_status = 'UNKNOWN'
    WHERE delivery_challan_ref IS NULL
      AND goods_receipt_note_ref IS NULL
      AND service_completion_date IS NULL
      AND deemed_receipt_reason IS NULL;

    -- Add lot/instalment tracking, service certificate, and delivery evidence columns
    ALTER TABLE public.purchase_documents
        ADD COLUMN IF NOT EXISTS total_lots_expected INT NOT NULL DEFAULT 1,
        ADD COLUMN IF NOT EXISTS lots_received_count INT NOT NULL DEFAULT 0,
        ADD COLUMN IF NOT EXISTS is_last_lot_received BOOLEAN NOT NULL DEFAULT FALSE,
        ADD COLUMN IF NOT EXISTS quantity_expected NUMERIC(15, 4),
        ADD COLUMN IF NOT EXISTS quantity_received NUMERIC(15, 4),
        ADD COLUMN IF NOT EXISTS remaining_quantity NUMERIC(15, 4),
        ADD COLUMN IF NOT EXISTS service_acceptance_cert_ref TEXT,
        ADD COLUMN IF NOT EXISTS supplier_delivery_evidence JSONB DEFAULT '{}'::jsonb,
        ADD COLUMN IF NOT EXISTS lot_details JSONB DEFAULT '[]'::jsonb;
    """)

    # -------------------------------------------------------------------------
    # 2. Add REVERSAL_SECTION_16_2_B to itc_ledger_entries event_type check
    # -------------------------------------------------------------------------
    op.execute("""
    ALTER TABLE public.itc_ledger_entries
        DROP CONSTRAINT IF EXISTS itc_ledger_entries_event_type_check,
        DROP CONSTRAINT IF EXISTS chk_itc_ledger_entries_event_type;

    ALTER TABLE public.itc_ledger_entries
        ADD CONSTRAINT itc_ledger_entries_event_type_check
        CHECK (event_type IN (
            'AVAIL', 'REVERSAL_RULE_37', 'REVERSAL_RULE_37A',
            'REVERSAL_PERMANENT_17_5', 'REVERSAL_RULE_38', 'REVERSAL_RULE_42_43',
            'REVERSAL_OTHER_TEMP', 'REVERSAL_SECTION_16_2_B', 'RECLAIM'
        ));
    """)

    # -------------------------------------------------------------------------
    # 3. Unbooked GSTR-2B Candidate Tracking Table
    # -------------------------------------------------------------------------
    op.execute("""
    CREATE TABLE IF NOT EXISTS public.gstr2b_unbooked_candidates (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id UUID NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
        snapshot_id UUID NOT NULL,
        gstr2b_doc_id UUID NOT NULL,
        supplier_gstin TEXT NOT NULL,
        supplier_name TEXT,
        document_number TEXT NOT NULL,
        document_date DATE NOT NULL,
        taxable_value NUMERIC(15, 2) NOT NULL DEFAULT 0.00,
        cgst_amount NUMERIC(15, 2) NOT NULL DEFAULT 0.00,
        sgst_amount NUMERIC(15, 2) NOT NULL DEFAULT 0.00,
        igst_amount NUMERIC(15, 2) NOT NULL DEFAULT 0.00,
        cess_amount NUMERIC(15, 2) NOT NULL DEFAULT 0.00,
        total_tax NUMERIC(15, 2) NOT NULL DEFAULT 0.00,
        receipt_status TEXT NOT NULL DEFAULT 'UNKNOWN',
        delivery_challan_ref TEXT,
        goods_receipt_note_ref TEXT,
        goods_receipt_date DATE,
        service_completion_date DATE,
        service_acceptance_cert_ref TEXT,
        deemed_receipt_reason TEXT,
        receipt_verified_by UUID REFERENCES public.global_users(id) ON DELETE SET NULL,
        receipt_verified_at TIMESTAMPTZ,
        reversal_event_id UUID,
        linked_purchase_doc_id UUID REFERENCES public.purchase_documents(id) ON DELETE SET NULL,
        reconciliation_status TEXT NOT NULL DEFAULT 'MISSING_IN_BOOKS',
        is_reversed_under_4b2 BOOLEAN NOT NULL DEFAULT TRUE,
        is_reclaimed BOOLEAN NOT NULL DEFAULT FALSE,
        reclaimed_period TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        FOREIGN KEY (snapshot_id, org_id) REFERENCES public.gstr2b_snapshots(id, org_id) ON DELETE CASCADE,
        FOREIGN KEY (gstr2b_doc_id, org_id) REFERENCES public.gstr2b_source_documents(id, org_id) ON DELETE CASCADE,
        CONSTRAINT uq_unbooked_candidates_org_gstr2b UNIQUE (org_id, gstr2b_doc_id),
        CONSTRAINT chk_unbooked_cand_receipt_status
            CHECK (receipt_status IN ('RECEIVED', 'PARTIALLY_RECEIVED', 'NOT_RECEIVED', 'DEEMED_RECEIVED', 'UNKNOWN'))
    );

    ALTER TABLE public.gstr2b_unbooked_candidates ENABLE ROW LEVEL SECURITY;

    DROP POLICY IF EXISTS gstr2b_unbooked_candidates_tenant ON public.gstr2b_unbooked_candidates;
    CREATE POLICY gstr2b_unbooked_candidates_tenant ON public.gstr2b_unbooked_candidates
        FOR ALL TO app_service_user
        USING (org_id = (NULLIF(current_setting('app.current_org', true), ''))::uuid)
        WITH CHECK (org_id = (NULLIF(current_setting('app.current_org', true), ''))::uuid);

    DROP POLICY IF EXISTS gstr2b_unbooked_candidates_admin ON public.gstr2b_unbooked_candidates;
    CREATE POLICY gstr2b_unbooked_candidates_admin ON public.gstr2b_unbooked_candidates
        FOR ALL TO platform_admin USING (true) WITH CHECK (true);

    GRANT SELECT, INSERT, UPDATE, DELETE ON public.gstr2b_unbooked_candidates TO app_service_user;
    """)


def downgrade() -> None:
    op.execute("""
    DROP TABLE IF EXISTS public.gstr2b_unbooked_candidates CASCADE;

    ALTER TABLE public.purchase_documents
        DROP COLUMN IF EXISTS total_lots_expected,
        DROP COLUMN IF EXISTS lots_received_count,
        DROP COLUMN IF EXISTS is_last_lot_received,
        DROP COLUMN IF EXISTS quantity_expected,
        DROP COLUMN IF EXISTS quantity_received,
        DROP COLUMN IF EXISTS remaining_quantity,
        DROP COLUMN IF EXISTS service_acceptance_cert_ref,
        DROP COLUMN IF EXISTS supplier_delivery_evidence,
        DROP COLUMN IF EXISTS lot_details;
    """)
