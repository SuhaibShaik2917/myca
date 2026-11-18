"""Milestone 2.2: ITC Ledger Integrity, Event-Based Reversals/Reclaims, IMS Action History, and Statutory Precision

Revision ID: 0013_m2_2_itc_ledger
Revises: 0012_m2_1_statutory_corrections
Create Date: 2026-11-18 16:30:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '0013_m2_2_itc_ledger'
down_revision: Union[str, None] = '0012_m2_1_statutory_corrections'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -------------------------------------------------------------------------
    # 1. Update PAN Consistency Trigger to explicitly use SQLSTATE 23514
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
    """)

    # -------------------------------------------------------------------------
    # 2. Immutable Event-Based ITC Ledger Table
    # -------------------------------------------------------------------------
    op.execute("""
    CREATE TABLE IF NOT EXISTS public.itc_ledger_entries (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id uuid NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
        purchase_doc_id uuid NOT NULL,
        gstr2b_doc_id uuid,
        event_type text NOT NULL CHECK (event_type IN (
            'AVAIL', 'REVERSAL_RULE_37', 'REVERSAL_RULE_37A',
            'REVERSAL_PERMANENT_17_5', 'REVERSAL_RULE_38', 'REVERSAL_RULE_42_43',
            'REVERSAL_OTHER_TEMP', 'RECLAIM'
        )),
        tax_period text NOT NULL,
        cgst_amount numeric(15, 2) NOT NULL DEFAULT 0.00,
        sgst_amount numeric(15, 2) NOT NULL DEFAULT 0.00,
        igst_amount numeric(15, 2) NOT NULL DEFAULT 0.00,
        cess_amount numeric(15, 2) NOT NULL DEFAULT 0.00,
        total_tax numeric(15, 2) NOT NULL DEFAULT 0.00,
        reversal_event_id uuid REFERENCES public.itc_reversal_events(id) ON DELETE SET NULL,
        interest_amount numeric(15, 2) NOT NULL DEFAULT 0.00,
        interest_basis text,
        provenance jsonb NOT NULL DEFAULT '{}',
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        FOREIGN KEY (purchase_doc_id, org_id) REFERENCES public.purchase_documents(id, org_id) ON DELETE CASCADE,
        FOREIGN KEY (gstr2b_doc_id, org_id) REFERENCES public.gstr2b_source_documents(id, org_id) ON DELETE SET NULL,
        CONSTRAINT uq_itc_ledger_id_org UNIQUE (id, org_id)
    );

    ALTER TABLE public.itc_ledger_entries ENABLE ROW LEVEL SECURITY;
    CREATE POLICY itc_ledger_tenant ON public.itc_ledger_entries
        FOR ALL TO app_service_user
        USING (org_id = (NULLIF(current_setting('app.current_org', true), ''))::uuid)
        WITH CHECK (org_id = (NULLIF(current_setting('app.current_org', true), ''))::uuid);
    CREATE POLICY itc_ledger_admin ON public.itc_ledger_entries
        FOR ALL TO platform_admin USING (true) WITH CHECK (true);
    """)

    # -------------------------------------------------------------------------
    # 3. IMS Action Audit History Table
    # -------------------------------------------------------------------------
    op.execute("""
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint WHERE conname = 'uq_ims_actions_id_org'
        ) THEN
            ALTER TABLE public.ims_document_actions
                ADD CONSTRAINT uq_ims_actions_id_org UNIQUE (id, org_id);
        END IF;
    END
    $$;

    CREATE TABLE IF NOT EXISTS public.ims_action_history (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id uuid NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
        ims_action_id uuid NOT NULL,
        previous_action text,
        new_action text NOT NULL,
        action_reason text,
        actor_id uuid REFERENCES public.global_users(id) ON DELETE SET NULL,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        FOREIGN KEY (ims_action_id, org_id) REFERENCES public.ims_document_actions(id, org_id) ON DELETE CASCADE,
        CONSTRAINT uq_ims_action_history_id_org UNIQUE (id, org_id)
    );

    ALTER TABLE public.ims_action_history ENABLE ROW LEVEL SECURITY;
    CREATE POLICY ims_history_tenant ON public.ims_action_history
        FOR ALL TO app_service_user
        USING (org_id = (NULLIF(current_setting('app.current_org', true), ''))::uuid)
        WITH CHECK (org_id = (NULLIF(current_setting('app.current_org', true), ''))::uuid);
    CREATE POLICY ims_history_admin ON public.ims_action_history
        FOR ALL TO platform_admin USING (true) WITH CHECK (true);
    """)

    # -------------------------------------------------------------------------
    # 4. GSTR-2B Snapshot Filing Confirmation Columns
    # -------------------------------------------------------------------------
    op.execute("""
    ALTER TABLE public.gstr2b_snapshots
        ADD COLUMN IF NOT EXISTS is_frozen boolean NOT NULL DEFAULT false,
        ADD COLUMN IF NOT EXISTS filing_confirmation_arn text,
        ADD COLUMN IF NOT EXISTS filed_at timestamptz;

    ALTER TABLE public.itc_reversal_events
        ADD COLUMN IF NOT EXISTS interest_basis text;

    ALTER TABLE public.itc_reavailment_events
        ADD COLUMN IF NOT EXISTS reavailment_reason text;
    """)

    # -------------------------------------------------------------------------
    # 5. Table-Level Grants for App Service Role
    # -------------------------------------------------------------------------
    op.execute("""
    GRANT SELECT, INSERT, UPDATE, DELETE ON public.itc_ledger_entries TO app_service_user;
    GRANT SELECT, INSERT, UPDATE, DELETE ON public.ims_action_history TO app_service_user;
    GRANT SELECT, UPDATE ON public.gstr2b_snapshots TO app_service_user;
    GRANT SELECT, INSERT, UPDATE, DELETE ON public.itc_reversal_events TO app_service_user;
    GRANT SELECT, INSERT, UPDATE, DELETE ON public.itc_reavailment_events TO app_service_user;
    """)


def downgrade() -> None:
    op.execute("""
    DROP TABLE IF EXISTS public.ims_action_history CASCADE;
    DROP TABLE IF EXISTS public.itc_ledger_entries CASCADE;

    ALTER TABLE public.ims_document_actions
        DROP CONSTRAINT IF EXISTS uq_ims_actions_id_org;

    ALTER TABLE public.itc_reavailment_events
        DROP COLUMN IF EXISTS reavailment_reason;

    ALTER TABLE public.itc_reversal_events
        DROP COLUMN IF EXISTS interest_basis;

    ALTER TABLE public.gstr2b_snapshots
        DROP COLUMN IF EXISTS filed_at,
        DROP COLUMN IF EXISTS filing_confirmation_arn,
        DROP COLUMN IF EXISTS is_frozen;
    """)
