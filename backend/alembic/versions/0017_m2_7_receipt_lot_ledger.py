"""Milestone 2.7: Strict Historical Receipt Evidence Remediation & Goods Receipt Lot Ledger

Revision ID: 0017_m2_7_strict_receipt_evidence_and_lot_ledger
Revises: 0016_m2_6_receipt_gate_reversal
Create Date: 2026-11-18 19:40:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '0017_m2_7_receipt_lot_ledger'
down_revision: Union[str, None] = '0016_m2_6_receipt_gate_reversal'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -------------------------------------------------------------------------
    # 1. Add Deemed Receipt & Remediation Tracking Columns to purchase_documents
    # -------------------------------------------------------------------------
    op.execute("""
    ALTER TABLE public.purchase_documents
        ADD COLUMN IF NOT EXISTS deemed_receipt_category TEXT,
        ADD COLUMN IF NOT EXISTS deemed_receipt_authorized_by UUID,
        ADD COLUMN IF NOT EXISTS remediation_status TEXT,
        ADD COLUMN IF NOT EXISTS remediation_notes TEXT;
    """)

    # -------------------------------------------------------------------------
    # 2. Create Historical Receipt Remediation Audit Table
    # -------------------------------------------------------------------------
    op.execute("""
    CREATE TABLE IF NOT EXISTS public.receipt_remediation_audit (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id UUID NOT NULL,
        purchase_doc_id UUID NOT NULL,
        document_number TEXT NOT NULL,
        goods_services_type TEXT,
        previous_status TEXT NOT NULL,
        new_status TEXT NOT NULL,
        remediation_reason TEXT NOT NULL,
        evidence_snapshot JSONB NOT NULL,
        remediated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
    );

    ALTER TABLE public.receipt_remediation_audit ENABLE ROW LEVEL SECURITY;

    DROP POLICY IF EXISTS receipt_remediation_audit_admin ON public.receipt_remediation_audit;
    CREATE POLICY receipt_remediation_audit_admin ON public.receipt_remediation_audit
        FOR ALL TO postgres USING (true) WITH CHECK (true);

    DROP POLICY IF EXISTS receipt_remediation_audit_tenant ON public.receipt_remediation_audit;
    CREATE POLICY receipt_remediation_audit_tenant ON public.receipt_remediation_audit
        FOR ALL TO public
        USING (org_id = (NULLIF(current_setting('app.current_org', true), ''))::uuid)
        WITH CHECK (org_id = (NULLIF(current_setting('app.current_org', true), ''))::uuid);

    GRANT ALL ON public.receipt_remediation_audit TO postgres, public;
    """)

    # -------------------------------------------------------------------------
    # 3. Audit & Remediate Historical Rows with Incomplete Evidence
    # -------------------------------------------------------------------------
    op.execute("""
    -- Step 3A: Log and Downgrade Goods records lacking mandatory evidence
    INSERT INTO public.receipt_remediation_audit (
        org_id, purchase_doc_id, document_number, goods_services_type,
        previous_status, new_status, remediation_reason, evidence_snapshot
    )
    SELECT
        org_id, id, document_number, goods_services_type,
        receipt_status,
        CASE
            WHEN total_lots_expected > 1 AND (lots_received_count > 0 OR COALESCE(quantity_received, 0) > 0)
                 AND is_last_lot_received = FALSE THEN 'PARTIALLY_RECEIVED'
            ELSE 'UNKNOWN'
        END,
        CASE
            WHEN total_lots_expected > 1 AND (lots_received_count > 0 OR COALESCE(quantity_received, 0) > 0)
                 AND is_last_lot_received = FALSE THEN 'Incomplete multi-lot delivery: final lot not yet verified'
            WHEN goods_receipt_date IS NULL THEN 'Goods document missing mandatory receipt date'
            WHEN delivery_challan_ref IS NULL AND goods_receipt_note_ref IS NULL THEN 'Goods document missing GRN or delivery challan reference'
            WHEN quantity_received IS NULL OR quantity_received <= 0 THEN 'Goods document missing positive verified received quantity'
            ELSE 'Incomplete goods statutory receipt evidence under Section 16(2)(b)'
        END,
        jsonb_build_object(
            'delivery_challan_ref', delivery_challan_ref,
            'goods_receipt_note_ref', goods_receipt_note_ref,
            'goods_receipt_date', goods_receipt_date,
            'quantity_expected', quantity_expected,
            'quantity_received', quantity_received,
            'total_lots_expected', total_lots_expected,
            'lots_received_count', lots_received_count,
            'is_last_lot_received', is_last_lot_received
        )
    FROM public.purchase_documents
    WHERE receipt_status = 'RECEIVED'
      AND LOWER(COALESCE(goods_services_type, 'goods')) = 'goods'
      AND (
          (delivery_challan_ref IS NULL AND goods_receipt_note_ref IS NULL)
          OR goods_receipt_date IS NULL
          OR quantity_received IS NULL
          OR quantity_received <= 0
          OR (total_lots_expected > 1 AND is_last_lot_received = FALSE)
      );

    -- Apply update for Goods records
    UPDATE public.purchase_documents
    SET receipt_status = CASE
            WHEN total_lots_expected > 1 AND (lots_received_count > 0 OR COALESCE(quantity_received, 0) > 0)
                 AND is_last_lot_received = FALSE THEN 'PARTIALLY_RECEIVED'
            ELSE 'UNKNOWN'
        END,
        remediation_status = 'REMEDIATED_INCOMPLETE_EVIDENCE',
        remediation_notes = 'Historical audit downgraded receipt status due to incomplete goods delivery evidence'
    WHERE receipt_status = 'RECEIVED'
      AND LOWER(COALESCE(goods_services_type, 'goods')) = 'goods'
      AND (
          (delivery_challan_ref IS NULL AND goods_receipt_note_ref IS NULL)
          OR goods_receipt_date IS NULL
          OR quantity_received IS NULL
          OR quantity_received <= 0
          OR (total_lots_expected > 1 AND is_last_lot_received = FALSE)
      );

    -- Step 3B: Log and Downgrade Services records lacking mandatory evidence
    INSERT INTO public.receipt_remediation_audit (
        org_id, purchase_doc_id, document_number, goods_services_type,
        previous_status, new_status, remediation_reason, evidence_snapshot
    )
    SELECT
        org_id, id, document_number, goods_services_type,
        receipt_status,
        'UNKNOWN',
        CASE
            WHEN service_completion_date IS NULL AND service_acceptance_cert_ref IS NULL THEN 'Service document missing both completion date and acceptance cert'
            WHEN service_completion_date IS NULL THEN 'Service document missing completion date'
            WHEN service_acceptance_cert_ref IS NULL THEN 'Service document missing acceptance certificate reference'
            ELSE 'Incomplete service receipt evidence under Section 16(2)(b)'
        END,
        jsonb_build_object(
            'service_completion_date', service_completion_date,
            'service_acceptance_cert_ref', service_acceptance_cert_ref
        )
    FROM public.purchase_documents
    WHERE receipt_status = 'RECEIVED'
      AND LOWER(goods_services_type) = 'services'
      AND (service_completion_date IS NULL OR service_acceptance_cert_ref IS NULL);

    -- Apply update for Services records
    UPDATE public.purchase_documents
    SET receipt_status = 'UNKNOWN',
        remediation_status = 'REMEDIATED_INCOMPLETE_EVIDENCE',
        remediation_notes = 'Historical audit downgraded receipt status due to missing service completion certificate/date'
    WHERE receipt_status = 'RECEIVED'
      AND LOWER(goods_services_type) = 'services'
      AND (service_completion_date IS NULL OR service_acceptance_cert_ref IS NULL);

    -- Step 3C: Log and Downgrade Deemed Receipt records lacking mandatory categorization/authorization
    INSERT INTO public.receipt_remediation_audit (
        org_id, purchase_doc_id, document_number, goods_services_type,
        previous_status, new_status, remediation_reason, evidence_snapshot
    )
    SELECT
        org_id, id, document_number, goods_services_type,
        receipt_status,
        'UNKNOWN',
        'Deemed receipt claim missing structured category or authorized reviewer sign-off',
        jsonb_build_object(
            'deemed_receipt_reason', deemed_receipt_reason,
            'deemed_receipt_category', deemed_receipt_category,
            'deemed_receipt_authorized_by', deemed_receipt_authorized_by
        )
    FROM public.purchase_documents
    WHERE receipt_status = 'DEEMED_RECEIVED'
      AND (deemed_receipt_category IS NULL OR deemed_receipt_authorized_by IS NULL);

    UPDATE public.purchase_documents
    SET receipt_status = 'UNKNOWN',
        remediation_status = 'REMEDIATED_INCOMPLETE_EVIDENCE',
        remediation_notes = 'Historical audit downgraded deemed receipt claim due to missing category or authorization'
    WHERE receipt_status = 'DEEMED_RECEIVED'
      AND (deemed_receipt_category IS NULL OR deemed_receipt_authorized_by IS NULL);
    """)

    # -------------------------------------------------------------------------
    # 4. Create Database-Enforced Immutable Goods Receipt Lot Ledger
    # -------------------------------------------------------------------------
    op.execute("""
    CREATE TABLE IF NOT EXISTS public.goods_receipt_lot_ledger (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id UUID NOT NULL,
        purchase_doc_id UUID NOT NULL REFERENCES public.purchase_documents(id) ON DELETE CASCADE,
        line_item_id UUID,
        lot_number INT NOT NULL,
        expected_lots_count INT NOT NULL,
        expected_quantity NUMERIC(15, 4) NOT NULL,
        received_quantity NUMERIC(15, 4) NOT NULL,
        cumulative_received_quantity NUMERIC(15, 4) NOT NULL,
        lot_reference TEXT NOT NULL,
        receipt_date DATE NOT NULL,
        goods_receipt_note_ref TEXT NOT NULL,
        carrier_name TEXT,
        vehicle_number TEXT,
        eway_bill_number TEXT,
        verifier_id UUID NOT NULL,
        evidence_location TEXT,
        verification_status TEXT NOT NULL DEFAULT 'VERIFIED',
        is_final_lot BOOLEAN NOT NULL DEFAULT FALSE,
        rejection_reason TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        CONSTRAINT chk_lot_number_pos CHECK (lot_number >= 1),
        CONSTRAINT chk_exp_lots_pos CHECK (expected_lots_count >= 1),
        CONSTRAINT chk_lot_num_le_exp CHECK (lot_number <= expected_lots_count),
        CONSTRAINT chk_rec_qty_pos CHECK (received_quantity > 0),
        CONSTRAINT chk_lot_ver_status CHECK (verification_status IN ('VERIFIED', 'REJECTED')),
        CONSTRAINT uq_doc_lot_num UNIQUE (org_id, purchase_doc_id, lot_number),
        CONSTRAINT uq_doc_lot_ref UNIQUE (org_id, purchase_doc_id, lot_reference)
    );

    CREATE INDEX IF NOT EXISTS idx_lot_ledger_doc ON public.goods_receipt_lot_ledger(org_id, purchase_doc_id);
    CREATE INDEX IF NOT EXISTS idx_lot_ledger_status ON public.goods_receipt_lot_ledger(org_id, verification_status);

    ALTER TABLE public.goods_receipt_lot_ledger ENABLE ROW LEVEL SECURITY;

    DROP POLICY IF EXISTS goods_receipt_lot_admin ON public.goods_receipt_lot_ledger;
    CREATE POLICY goods_receipt_lot_admin ON public.goods_receipt_lot_ledger
        FOR ALL TO postgres USING (true) WITH CHECK (true);

    DROP POLICY IF EXISTS goods_receipt_lot_tenant ON public.goods_receipt_lot_ledger;
    CREATE POLICY goods_receipt_lot_tenant ON public.goods_receipt_lot_ledger
        FOR ALL TO public
        USING (org_id = (NULLIF(current_setting('app.current_org', true), ''))::uuid)
        WITH CHECK (org_id = (NULLIF(current_setting('app.current_org', true), ''))::uuid);

    GRANT ALL ON public.goods_receipt_lot_ledger TO postgres, public;
    """)


def downgrade() -> None:
    op.execute("""
    DROP TABLE IF EXISTS public.goods_receipt_lot_ledger CASCADE;
    DROP TABLE IF EXISTS public.receipt_remediation_audit CASCADE;

    ALTER TABLE public.purchase_documents
        DROP COLUMN IF EXISTS deemed_receipt_category,
        DROP COLUMN IF EXISTS deemed_receipt_authorized_by,
        DROP COLUMN IF EXISTS remediation_status,
        DROP COLUMN IF EXISTS remediation_notes;
    """)
