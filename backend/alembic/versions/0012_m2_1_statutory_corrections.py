"""Milestone 2.1 Statutory Corrections: Primary Registration Constraint, PAN Consistency, Purchase Register Uniqueness, Rule 37A Statutory Deadlines, and IMS Lifecycle

Revision ID: 0012_m2_1_statutory_corrections
Revises: 0011_m2_foundation
Create Date: 2026-11-18 01:45:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '0012_m2_1_statutory_corrections'
down_revision: Union[str, None] = '0011_m2_foundation'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -------------------------------------------------------------------------
    # 1. Primary GST Registration Constraint (Partial Unique Index)
    # -------------------------------------------------------------------------
    op.execute("""
    CREATE UNIQUE INDEX IF NOT EXISTS uq_gst_registrations_org_primary
        ON public.gst_registrations(org_id)
        WHERE is_primary = true;
    """)

    # -------------------------------------------------------------------------
    # 2. PAN-Level Consistency Enforcement Trigger
    # -------------------------------------------------------------------------
    op.execute("""
    CREATE OR REPLACE FUNCTION public.fn_validate_gstin_pan_consistency()
    RETURNS trigger
    LANGUAGE plpgsql
    AS $$
    DECLARE
        v_pan text;
    BEGIN
        SELECT pan INTO v_pan FROM public.organizations WHERE id = NEW.org_id;
        IF v_pan IS NOT NULL AND v_pan <> '' THEN
            IF substring(NEW.gstin FROM 3 FOR 10) <> v_pan THEN
                RAISE EXCEPTION 'GSTIN % PAN % does not match Organization PAN %',
                    NEW.gstin, substring(NEW.gstin FROM 3 FOR 10), v_pan
                    USING ERRCODE = '23514';
            END IF;
        END IF;
        RETURN NEW;
    END;
    $$;

    DROP TRIGGER IF EXISTS trg_validate_gstin_pan ON public.gst_registrations;
    CREATE TRIGGER trg_validate_gstin_pan
        BEFORE INSERT OR UPDATE OF gstin, org_id ON public.gst_registrations
        FOR EACH ROW EXECUTE FUNCTION public.fn_validate_gstin_pan_consistency();
    """)

    # -------------------------------------------------------------------------
    # 3. Purchase Document Uniqueness with Registration & Document Type
    # -------------------------------------------------------------------------
    op.execute("""
    ALTER TABLE public.purchase_documents
        DROP CONSTRAINT IF EXISTS uq_purchase_docs_org_supp_num_date;

    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint WHERE conname = 'uq_purchase_docs_org_reg_supp_type_num_date'
        ) THEN
            ALTER TABLE public.purchase_documents
                ADD CONSTRAINT uq_purchase_docs_org_reg_supp_type_num_date
                UNIQUE (org_id, registration_id, supplier_gstin, document_type, document_number, document_date);
        END IF;
    END
    $$;

    ALTER TABLE public.purchase_documents
        ADD COLUMN IF NOT EXISTS is_availed_in_3b boolean NOT NULL DEFAULT false,
        ADD COLUMN IF NOT EXISTS availed_period text;
    """)

    # -------------------------------------------------------------------------
    # 4. IMS Deemed Acceptance & Recomputation Lifecycle Tracking
    # -------------------------------------------------------------------------
    op.execute("""
    ALTER TABLE public.ims_document_actions
        ADD COLUMN IF NOT EXISTS deemed_accepted_at_generation boolean NOT NULL DEFAULT true,
        ADD COLUMN IF NOT EXISTS recomputed_at timestamptz;
    """)

    # -------------------------------------------------------------------------
    # 5. Expanded Statutory Rule Catalogue Seed Data
    # -------------------------------------------------------------------------
    op.execute("""
    INSERT INTO public.itc_rule_catalogue (rule_code, statutory_section, title, description, effective_date, is_blocking) VALUES
        ('SEC_16_2_D', 'Section 16(2)(d)', 'Return Furnishing by Recipient', 'Recipient must furnish return under Section 39 (Form GSTR-3B).', '2017-07-01', true),
        ('RULE_38', 'Rule 38', 'Banking / Financial Institution ITC Reversal', 'Banking companies and financial institutions reverse 50% of eligible input tax credit.', '2017-07-01', true),
        ('RULE_42', 'Rule 42', 'Reversal for Non-Business or Exempt Supplies (Inputs & Services)', 'Statutory apportionment of input tax credit between taxable, exempt, and personal use.', '2017-07-01', true),
        ('RULE_43', 'Rule 43', 'Reversal for Capital Goods used for Exempt Supplies', 'Statutory formula-based reversal over 60-month useful life for capital goods used partly for exempt supplies.', '2017-07-01', true),
        ('SEC_17_5_EXCEPT', 'Section 17(5) Proviso', 'Statutory Exceptions to Ineligible Credit', 'Credit permissible for motor vehicles used in passenger transport, goods transport, or driving school, or works contract for plant and machinery.', '2017-07-01', false)
    ON CONFLICT (rule_code) DO NOTHING;
    """)

    # -------------------------------------------------------------------------
    # 6. Privilege Maintenance for app_service_user
    # -------------------------------------------------------------------------
    op.execute("""
    GRANT SELECT, INSERT, UPDATE, DELETE ON public.purchase_documents TO app_service_user;
    GRANT SELECT, INSERT, UPDATE, DELETE ON public.ims_document_actions TO app_service_user;
    GRANT SELECT ON public.itc_rule_catalogue TO app_service_user;
    """)


def downgrade() -> None:
    op.execute("""
    DELETE FROM public.itc_rule_catalogue
    WHERE rule_code IN ('SEC_16_2_D', 'RULE_38', 'RULE_42', 'RULE_43', 'SEC_17_5_EXCEPT');

    ALTER TABLE public.ims_document_actions
        DROP COLUMN IF EXISTS recomputed_at,
        DROP COLUMN IF EXISTS deemed_accepted_at_generation;

    ALTER TABLE public.purchase_documents
        DROP CONSTRAINT IF EXISTS uq_purchase_docs_org_reg_supp_type_num_date,
        DROP COLUMN IF EXISTS availed_period,
        DROP COLUMN IF EXISTS is_availed_in_3b;

    DO $$
    BEGIN
        -- Deduplicate any purchase documents that violate the old constraint before re-adding it
        DELETE FROM public.purchase_documents a
        USING public.purchase_documents b
        WHERE a.id > b.id
          AND a.org_id = b.org_id
          AND a.supplier_gstin = b.supplier_gstin
          AND a.document_number = b.document_number
          AND a.document_date = b.document_date;

        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint WHERE conname = 'uq_purchase_docs_org_supp_num_date'
        ) THEN
            ALTER TABLE public.purchase_documents
                ADD CONSTRAINT uq_purchase_docs_org_supp_num_date
                UNIQUE (org_id, supplier_gstin, document_number, document_date);
        END IF;
    END
    $$;

    DROP TRIGGER IF EXISTS trg_validate_gstin_pan ON public.gst_registrations;
    DROP FUNCTION IF EXISTS public.fn_validate_gstin_pan_consistency();

    DROP INDEX IF EXISTS public.uq_gst_registrations_org_primary;
    """)
