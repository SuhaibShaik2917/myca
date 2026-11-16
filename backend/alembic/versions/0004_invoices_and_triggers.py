"""Invoices, Line Items, Transition Rules, and Integrity Triggers

Revision ID: 0004_invoices_and_triggers
Revises: 0003_financial_tables
Create Date: 2026-11-17 04:33:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '0004_invoices_and_triggers'
down_revision: Union[str, None] = '0003_financial_tables'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    # 1. Invoice Transition Rules
    op.execute("""
    CREATE TABLE IF NOT EXISTS public.invoice_transition_rules (
        from_status invoice_status NOT NULL,
        to_status invoice_status NOT NULL,
        required_role text NOT NULL CHECK (required_role IN ('worker', 'human')),
        PRIMARY KEY (from_status, to_status)
    );

    INSERT INTO public.invoice_transition_rules (from_status, to_status, required_role) VALUES
        ('uploaded', 'queued', 'worker'),
        ('queued', 'processing', 'worker'),
        ('processing', 'extracted', 'worker'),
        ('extracted', 'validation_failed', 'worker'),
        ('extracted', 'pending_review', 'worker'),
        ('validation_failed', 'queued', 'human'),
        ('pending_review', 'approved', 'human'),
        ('pending_review', 'rejected', 'human'),
        ('approved', 'export_ready', 'worker')
    ON CONFLICT DO NOTHING;
    """)

    # 2. Invoices Table
    op.execute("""
    CREATE TABLE IF NOT EXISTS public.invoices (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id uuid NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
        profile_id uuid NOT NULL,
        invoice_number text NOT NULL,
        invoice_date date NOT NULL,
        due_date date,
        counterparty_name text NOT NULL,
        counterparty_gstin text CHECK (counterparty_gstin IS NULL OR counterparty_gstin ~ '^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$'),
        total_taxable_amount numeric(15, 2) NOT NULL DEFAULT 0.00,
        cgst_amount numeric(15, 2) NOT NULL DEFAULT 0.00,
        sgst_amount numeric(15, 2) NOT NULL DEFAULT 0.00,
        igst_amount numeric(15, 2) NOT NULL DEFAULT 0.00,
        total_amount numeric(15, 2) NOT NULL,
        currency text NOT NULL DEFAULT 'INR',
        status invoice_status NOT NULL DEFAULT 'uploaded',
        rejection_reason text,
        document_id uuid REFERENCES public.documents(id) ON DELETE SET NULL,
        approved_by uuid REFERENCES public.global_users(id) ON DELETE SET NULL,
        approved_at timestamptz,
        export_ready_at timestamptz,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        CONSTRAINT uq_invoices_id_org UNIQUE (id, org_id),
        CONSTRAINT uq_invoices_org_number UNIQUE (org_id, invoice_number),
        FOREIGN KEY (profile_id, org_id) REFERENCES public.business_profiles(id, org_id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_invoices_org_status ON public.invoices(org_id, status);
    """)

    # 3. Invoice Line Items
    op.execute("""
    CREATE TABLE IF NOT EXISTS public.invoice_line_items (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        invoice_id uuid NOT NULL,
        org_id uuid NOT NULL,
        item_description text NOT NULL,
        hsn_sac_code text,
        quantity numeric(12, 3) NOT NULL DEFAULT 1.000,
        unit_price numeric(15, 2) NOT NULL,
        taxable_amount numeric(15, 2) NOT NULL,
        gst_rate numeric(5, 2) NOT NULL DEFAULT 18.00,
        cgst_amount numeric(15, 2) NOT NULL DEFAULT 0.00,
        sgst_amount numeric(15, 2) NOT NULL DEFAULT 0.00,
        igst_amount numeric(15, 2) NOT NULL DEFAULT 0.00,
        total_amount numeric(15, 2) NOT NULL,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        CONSTRAINT uq_invoice_lines_id_org UNIQUE (id, org_id),
        FOREIGN KEY (invoice_id, org_id) REFERENCES public.invoices(id, org_id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_invoice_lines_inv_org ON public.invoice_line_items(invoice_id, org_id);
    """)

    # 4. Trigger Functions
    op.execute("""
    CREATE OR REPLACE FUNCTION public.enforce_invoice_insert_status()
    RETURNS trigger
    LANGUAGE plpgsql
    AS $$
    BEGIN
        IF NEW.status <> 'uploaded' THEN
            RAISE EXCEPTION 'Initial invoice status must be uploaded, rejected: %', NEW.status;
        END IF;
        RETURN NEW;
    END;
    $$;

    CREATE OR REPLACE FUNCTION public.enforce_invoice_update_status()
    RETURNS trigger
    LANGUAGE plpgsql
    AS $$
    DECLARE
        v_req_role text;
        v_user_id uuid;
        v_user_role public.org_role;
    BEGIN
        -- No status change: allow but block tampering of financial figures on approved/export_ready invoices
        IF OLD.status = NEW.status THEN
            IF OLD.status IN ('approved', 'export_ready') THEN
                IF (OLD.total_taxable_amount <> NEW.total_taxable_amount OR
                    OLD.total_amount <> NEW.total_amount OR
                    OLD.invoice_number <> NEW.invoice_number) THEN
                    RAISE EXCEPTION 'Cannot modify financial data on approved/export-ready invoice %', OLD.id;
                END IF;
            END IF;
            RETURN NEW;
        END IF;

        -- Verify permitted transition
        SELECT required_role INTO v_req_role
        FROM public.invoice_transition_rules
        WHERE from_status = OLD.status AND to_status = NEW.status;

        IF v_req_role IS NULL THEN
            RAISE EXCEPTION 'Illegal status transition from % to % for invoice %', OLD.status, NEW.status, OLD.id;
        END IF;

        -- Worker transition
        IF v_req_role = 'worker' THEN
            IF NOT (session_user = 'worker_owner' OR pg_has_role(session_user, 'worker_owner', 'MEMBER')
                    OR session_user = 'platform_admin' OR pg_has_role(session_user, 'platform_admin', 'MEMBER')
                    OR session_user = 'postgres') THEN
                RAISE EXCEPTION 'Transition % -> % requires worker_owner role, caller is %', OLD.status, NEW.status, session_user;
            END IF;
            IF NEW.status = 'export_ready' THEN
                NEW.export_ready_at := clock_timestamp();
            END IF;
        END IF;

        -- Human approval transition
        IF v_req_role = 'human' THEN
            v_user_id := NULLIF(current_setting('app.current_user', true), '')::uuid;
            IF v_user_id IS NULL THEN
                RAISE EXCEPTION 'Human transition % -> % requires valid app.current_user context', OLD.status, NEW.status;
            END IF;

            SELECT role INTO v_user_role
            FROM public.organization_memberships
            WHERE org_id = NEW.org_id
              AND user_id = v_user_id
              AND status = 'active';

            IF v_user_role IS NULL OR v_user_role NOT IN ('owner', 'admin') THEN
                RAISE EXCEPTION 'Human approval/rejection requires active owner or admin membership in org %, user %', NEW.org_id, v_user_id;
            END IF;

            IF NEW.status = 'approved' THEN
                NEW.approved_by := v_user_id;
                NEW.approved_at := clock_timestamp();
            END IF;
        END IF;

        NEW.updated_at := clock_timestamp();
        RETURN NEW;
    END;
    $$;

    DROP TRIGGER IF EXISTS trg_invoice_insert_enforce ON public.invoices;
    CREATE TRIGGER trg_invoice_insert_enforce
        BEFORE INSERT ON public.invoices
        FOR EACH ROW
        EXECUTE FUNCTION public.enforce_invoice_insert_status();

    DROP TRIGGER IF EXISTS trg_invoice_update_enforce ON public.invoices;
    CREATE TRIGGER trg_invoice_update_enforce
        BEFORE UPDATE ON public.invoices
        FOR EACH ROW
        EXECUTE FUNCTION public.enforce_invoice_update_status();
    """)

def downgrade() -> None:
    op.execute('DROP TRIGGER IF EXISTS trg_invoice_update_enforce ON public.invoices;')
    op.execute('DROP TRIGGER IF EXISTS trg_invoice_insert_enforce ON public.invoices;')
    op.execute('DROP FUNCTION IF EXISTS public.enforce_invoice_update_status();')
    op.execute('DROP FUNCTION IF EXISTS public.enforce_invoice_insert_status();')
    op.execute('DROP TABLE IF EXISTS public.invoice_line_items CASCADE;')
    op.execute('DROP TABLE IF EXISTS public.invoices CASCADE;')
    op.execute('DROP TABLE IF EXISTS public.invoice_transition_rules CASCADE;')
