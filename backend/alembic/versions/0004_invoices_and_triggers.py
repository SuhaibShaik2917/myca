"""Invoices, Line Items, Transition Rules, and Hardened Integrity Triggers

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

    # 2. Invoices Table with Composite Document FK (C2)
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
        document_id uuid,
        approved_by uuid REFERENCES public.global_users(id) ON DELETE SET NULL,
        approved_at timestamptz,
        export_ready_at timestamptz,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        CONSTRAINT uq_invoices_id_org UNIQUE (id, org_id),
        CONSTRAINT uq_invoices_org_number UNIQUE (org_id, invoice_number),
        FOREIGN KEY (profile_id, org_id) REFERENCES public.business_profiles(id, org_id) ON DELETE CASCADE,
        FOREIGN KEY (document_id, org_id) REFERENCES public.documents(id, org_id) ON DELETE SET NULL (document_id)
    );
    CREATE INDEX IF NOT EXISTS idx_invoices_org_status ON public.invoices(org_id, status);
    CREATE INDEX IF NOT EXISTS idx_invoices_doc_org ON public.invoices(document_id, org_id);
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

    # 4. Hardened Trigger Functions (C1)
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
    SECURITY DEFINER
    SET search_path = public, pg_temp
    AS $$
    DECLARE
        v_req_role text;
        v_user_id uuid;
        v_user_role public.org_role;
        v_financial_changed boolean;
    BEGIN
        -- Detect whether any protected financial or identifying fields were changed (NULL-safe IS DISTINCT FROM)
        v_financial_changed := (
            OLD.total_taxable_amount IS DISTINCT FROM NEW.total_taxable_amount OR
            OLD.cgst_amount IS DISTINCT FROM NEW.cgst_amount OR
            OLD.sgst_amount IS DISTINCT FROM NEW.sgst_amount OR
            OLD.igst_amount IS DISTINCT FROM NEW.igst_amount OR
            OLD.total_amount IS DISTINCT FROM NEW.total_amount OR
            OLD.invoice_number IS DISTINCT FROM NEW.invoice_number OR
            OLD.counterparty_name IS DISTINCT FROM NEW.counterparty_name OR
            OLD.counterparty_gstin IS DISTINCT FROM NEW.counterparty_gstin OR
            OLD.invoice_date IS DISTINCT FROM NEW.invoice_date OR
            OLD.profile_id IS DISTINCT FROM NEW.profile_id OR
            OLD.currency IS DISTINCT FROM NEW.currency
        );

        -- 1. Strict Immutability once Approved or Export-Ready across ALL updates and transitions (C1)
        IF OLD.status IN ('approved', 'export_ready') THEN
            IF v_financial_changed THEN
                RAISE EXCEPTION 'Cannot modify financial or identifying fields on approved/export-ready invoice % (42501)', OLD.id;
            END IF;
        END IF;

        -- 2. Strict Immutability during the Approval/Rejection transition itself (C1)
        IF OLD.status = 'pending_review' AND NEW.status IN ('approved', 'rejected') THEN
            IF v_financial_changed THEN
                RAISE EXCEPTION 'Cannot modify financial figures during approval/rejection transition for invoice % (42501)', OLD.id;
            END IF;
        END IF;

        -- 3. If no status change, validate financial modification eligibility
        IF OLD.status = NEW.status THEN
            -- Non-approved invoices can be edited during correction phases before approval
            RETURN NEW;
        END IF;

        -- 4. Verify permitted transition in state machine
        SELECT required_role INTO v_req_role
        FROM public.invoice_transition_rules
        WHERE from_status = OLD.status AND to_status = NEW.status;

        IF v_req_role IS NULL THEN
            RAISE EXCEPTION 'Illegal status transition from % to % for invoice %', OLD.status, NEW.status, OLD.id;
        END IF;

        -- 5. Worker transition authorization
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

        -- 6. Human approval transition authorization
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

    -- Line Item Immutability Trigger (C1 Fix: Prevents moving line items out of or into approved/export-ready invoices with deterministic FOR UPDATE locking)
    CREATE OR REPLACE FUNCTION public.enforce_invoice_line_item_immutability()
    RETURNS trigger
    LANGUAGE plpgsql
    SECURITY DEFINER
    SET search_path = public, pg_temp
    AS $$
    DECLARE
        v_old_status public.invoice_status;
        v_new_status public.invoice_status;
    BEGIN
        IF TG_OP = 'INSERT' THEN
            SELECT status INTO v_new_status
            FROM public.invoices
            WHERE id = NEW.invoice_id
            FOR UPDATE;

            IF v_new_status IS NULL THEN
                RAISE EXCEPTION 'Parent invoice % does not exist', NEW.invoice_id;
            END IF;

            IF v_new_status IN ('approved', 'export_ready') THEN
                RAISE EXCEPTION 'Cannot insert line items for approved/export-ready invoice % (42501)', NEW.invoice_id;
            END IF;
            RETURN NEW;

        ELSIF TG_OP = 'DELETE' THEN
            SELECT status INTO v_old_status
            FROM public.invoices
            WHERE id = OLD.invoice_id
            FOR UPDATE;

            IF v_old_status IN ('approved', 'export_ready') THEN
                RAISE EXCEPTION 'Cannot delete line items from approved/export-ready invoice % (42501)', OLD.invoice_id;
            END IF;
            RETURN OLD;

        ELSIF TG_OP = 'UPDATE' THEN
            IF OLD.invoice_id = NEW.invoice_id THEN
                SELECT status INTO v_old_status
                FROM public.invoices
                WHERE id = OLD.invoice_id
                FOR UPDATE;

                IF v_old_status IN ('approved', 'export_ready') THEN
                    RAISE EXCEPTION 'Cannot modify or move line items out of approved/export-ready invoice % (42501)', OLD.invoice_id;
                END IF;
            ELSE
                -- Moving between two distinct invoices: acquire locks in deterministic ID order to prevent deadlocks
                IF OLD.invoice_id < NEW.invoice_id THEN
                    SELECT status INTO v_old_status FROM public.invoices WHERE id = OLD.invoice_id FOR UPDATE;
                    SELECT status INTO v_new_status FROM public.invoices WHERE id = NEW.invoice_id FOR UPDATE;
                ELSE
                    SELECT status INTO v_new_status FROM public.invoices WHERE id = NEW.invoice_id FOR UPDATE;
                    SELECT status INTO v_old_status FROM public.invoices WHERE id = OLD.invoice_id FOR UPDATE;
                END IF;

                IF v_old_status IN ('approved', 'export_ready') THEN
                    RAISE EXCEPTION 'Cannot modify or move line items out of approved/export-ready invoice % (42501)', OLD.invoice_id;
                END IF;

                IF v_new_status IN ('approved', 'export_ready') THEN
                    RAISE EXCEPTION 'Cannot move line items into approved/export-ready invoice % (42501)', NEW.invoice_id;
                END IF;
            END IF;

            RETURN NEW;
        END IF;

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

    DROP TRIGGER IF EXISTS trg_invoice_lines_immutable ON public.invoice_line_items;
    CREATE TRIGGER trg_invoice_lines_immutable
        BEFORE INSERT OR UPDATE OR DELETE ON public.invoice_line_items
        FOR EACH ROW
        EXECUTE FUNCTION public.enforce_invoice_line_item_immutability();
    """)

def downgrade() -> None:
    # Explicit dependency-ordered drops (H7)
    op.execute('DROP TRIGGER IF EXISTS trg_invoice_lines_immutable ON public.invoice_line_items;')
    op.execute('DROP FUNCTION IF EXISTS public.enforce_invoice_line_item_immutability();')
    op.execute('DROP TRIGGER IF EXISTS trg_invoice_update_enforce ON public.invoices;')
    op.execute('DROP TRIGGER IF EXISTS trg_invoice_insert_enforce ON public.invoices;')
    op.execute('DROP FUNCTION IF EXISTS public.enforce_invoice_update_status();')
    op.execute('DROP FUNCTION IF EXISTS public.enforce_invoice_insert_status();')
    op.execute('DROP TABLE IF EXISTS public.invoice_line_items;')
    op.execute('DROP TABLE IF EXISTS public.invoices;')
    op.execute('DROP TABLE IF EXISTS public.invoice_transition_rules;')
