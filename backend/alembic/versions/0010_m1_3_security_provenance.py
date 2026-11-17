"""Milestone 1.3: Least-Privilege Role Ownership, Task-Type Enforcement, and Provenance

Revision ID: 0010_m1_3_security_provenance
Revises: 0009_line_item_isolation
Create Date: 2026-11-17 14:30:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '0010_m1_3_security_provenance'
down_revision: Union[str, None] = '0009_line_item_isolation'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    # 1. Create Dedicated NOLOGIN Least-Privilege Role for Function Ownership
    op.execute("""
    DO $$
    BEGIN
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'invoice_ocr_writer') THEN
            CREATE ROLE invoice_ocr_writer NOLOGIN NOINHERIT;
        END IF;
    END
    $$;

    GRANT USAGE ON SCHEMA public TO invoice_ocr_writer;
    GRANT SELECT, UPDATE ON public.worker_tasks TO invoice_ocr_writer;
    GRANT SELECT, UPDATE ON public.invoices TO invoice_ocr_writer;
    GRANT SELECT, INSERT, UPDATE, DELETE ON public.invoice_line_items TO invoice_ocr_writer;
    GRANT SELECT ON public.worker_service_authorizations TO invoice_ocr_writer;
    GRANT EXECUTE ON FUNCTION public.is_worker_authorized TO invoice_ocr_writer;

    -- Scoped RLS Policies for invoice_ocr_writer preserving forced tenant isolation
    DROP POLICY IF EXISTS invoices_ocr_writer_policy ON public.invoices;
    CREATE POLICY invoices_ocr_writer_policy ON public.invoices
    FOR ALL TO invoice_ocr_writer
    USING (
        (org_id = (NULLIF(current_setting('app.current_org', true), ''))::uuid)
        AND is_worker_authorized(org_id)
    )
    WITH CHECK (
        (org_id = (NULLIF(current_setting('app.current_org', true), ''))::uuid)
        AND is_worker_authorized(org_id)
    );

    DROP POLICY IF EXISTS invoice_lines_ocr_writer_policy ON public.invoice_line_items;
    CREATE POLICY invoice_lines_ocr_writer_policy ON public.invoice_line_items
    FOR ALL TO invoice_ocr_writer
    USING (
        (org_id = (NULLIF(current_setting('app.current_org', true), ''))::uuid)
        AND is_worker_authorized(org_id)
    )
    WITH CHECK (
        (org_id = (NULLIF(current_setting('app.current_org', true), ''))::uuid)
        AND is_worker_authorized(org_id)
    );

    DROP POLICY IF EXISTS worker_tasks_ocr_writer_policy ON public.worker_tasks;
    DROP POLICY IF EXISTS worker_tasks_ocr_writer_select_policy ON public.worker_tasks;
    DROP POLICY IF EXISTS worker_tasks_ocr_writer_update_policy ON public.worker_tasks;

    CREATE POLICY worker_tasks_ocr_writer_select_policy ON public.worker_tasks
    FOR SELECT TO invoice_ocr_writer
    USING (
        (org_id = (NULLIF(current_setting('app.current_org', true), ''))::uuid)
        AND is_worker_authorized(org_id)
    );

    CREATE POLICY worker_tasks_ocr_writer_update_policy ON public.worker_tasks
    FOR UPDATE TO invoice_ocr_writer
    USING (
        (org_id = (NULLIF(current_setting('app.current_org', true), ''))::uuid)
        AND is_worker_authorized(org_id)
    )
    WITH CHECK (
        (org_id = (NULLIF(current_setting('app.current_org', true), ''))::uuid)
        AND is_worker_authorized(org_id)
    );

    DROP POLICY IF EXISTS worker_auth_ocr_writer ON public.worker_service_authorizations;
    CREATE POLICY worker_auth_ocr_writer ON public.worker_service_authorizations
    FOR SELECT TO invoice_ocr_writer
    USING (worker_role_name = SESSION_USER);
    """)

    # 2. Drop unsafe numeric defaults on invoice_line_items to eliminate silent financial fabrication
    op.execute("""
    ALTER TABLE public.invoice_line_items ALTER COLUMN quantity DROP DEFAULT;
    ALTER TABLE public.invoice_line_items ALTER COLUMN gst_rate DROP DEFAULT;
    ALTER TABLE public.invoice_line_items ALTER COLUMN cgst_amount DROP DEFAULT;
    ALTER TABLE public.invoice_line_items ALTER COLUMN sgst_amount DROP DEFAULT;
    ALTER TABLE public.invoice_line_items ALTER COLUMN igst_amount DROP DEFAULT;

    -- Allow nullable values for genuine incomplete extractions (preserves NULL without inventing 18% or zeroes)
    ALTER TABLE public.invoice_line_items ALTER COLUMN quantity DROP NOT NULL;
    ALTER TABLE public.invoice_line_items ALTER COLUMN unit_price DROP NOT NULL;
    ALTER TABLE public.invoice_line_items ALTER COLUMN taxable_amount DROP NOT NULL;
    ALTER TABLE public.invoice_line_items ALTER COLUMN gst_rate DROP NOT NULL;
    ALTER TABLE public.invoice_line_items ALTER COLUMN cgst_amount DROP NOT NULL;
    ALTER TABLE public.invoice_line_items ALTER COLUMN sgst_amount DROP NOT NULL;
    ALTER TABLE public.invoice_line_items ALTER COLUMN igst_amount DROP NOT NULL;
    ALTER TABLE public.invoice_line_items ALTER COLUMN total_amount DROP NOT NULL;

    ALTER TABLE public.invoice_line_items
    ADD COLUMN IF NOT EXISTS extraction_confidence numeric(4, 3);
    """)

    # 3. Add OCR raw response and confidence provenance to invoices
    op.execute("""
    ALTER TABLE public.invoices
    ADD COLUMN IF NOT EXISTS ocr_raw_response jsonb,
    ADD COLUMN IF NOT EXISTS ocr_confidence numeric(4, 3);
    """)

    # 4. Remove assumed ₹5 Crore default on preceding_fy_aato and clean historical placeholders
    op.execute("""
    ALTER TABLE public.business_profiles
    ALTER COLUMN preceding_fy_aato DROP DEFAULT,
    ALTER COLUMN preceding_fy_aato DROP NOT NULL;

    -- Historical data cleanup:
    -- Records created with the previous default 50000000.00 must be cleansed to NULL unless PAN-level verification exists
    UPDATE public.business_profiles
    SET preceding_fy_aato = NULL
    WHERE preceding_fy_aato = 50000000.00;
    """)

    # 5. Recreate replace_ocr_line_items with task-type enforcement, worker lock check, and owner invoice_ocr_writer
    op.execute("""
    CREATE OR REPLACE FUNCTION public.replace_ocr_line_items(
        p_task_id uuid,
        p_lease_token text,
        p_invoice_id uuid,
        p_lines jsonb
    )
    RETURNS int
    LANGUAGE plpgsql
    SECURITY DEFINER
    SET search_path = public, pg_temp
    AS $$
    DECLARE
        v_task record;
        v_inv record;
        v_auth_org uuid;
        v_line jsonb;
        v_count int := 0;
        v_hashed_token text;
        v_line_id uuid;
    BEGIN
        -- 1. Verify caller session role (worker_owner, platform_admin, postgres)
        IF NOT (
            session_user = 'worker_owner'
            OR pg_has_role(session_user, 'worker_owner', 'MEMBER')
            OR session_user = 'platform_admin'
            OR pg_has_role(session_user, 'platform_admin', 'MEMBER')
            OR session_user = 'postgres'
        ) THEN
            RAISE EXCEPTION 'Caller is not authorized to replace OCR line items (42501)'
                USING ERRCODE = '42501';
        END IF;

        -- 2. Validate input parameters
        IF p_task_id IS NULL OR p_lease_token IS NULL OR p_invoice_id IS NULL THEN
            RAISE EXCEPTION 'Task ID, lease token, and invoice ID are required'
                USING ERRCODE = '22023';
        END IF;

        IF p_lines IS NULL OR jsonb_typeof(p_lines) <> 'array' THEN
            RAISE EXCEPTION 'Line items payload must be a JSON array'
                USING ERRCODE = '22023';
        END IF;

        v_hashed_token := encode(digest(p_lease_token, 'sha256'), 'hex');

        -- 3. Lock and verify worker task
        SELECT id, org_id, task_type, payload, status, lease_token_hash, lease_expire, locked_by
        INTO v_task
        FROM public.worker_tasks
        WHERE id = p_task_id
        FOR UPDATE;

        IF NOT FOUND THEN
            RAISE EXCEPTION 'Task % does not exist (42501)', p_task_id
                USING ERRCODE = '42501';
        END IF;

        -- CRITICAL TASK TYPE CHECK: Only 'invoice_ocr' tasks are authorized to replace line items
        IF v_task.task_type IS DISTINCT FROM 'invoice_ocr' THEN
            RAISE EXCEPTION 'Task % of type % is not authorized for OCR line-item replacement (42501)',
                p_task_id, v_task.task_type
                USING ERRCODE = '42501';
        END IF;

        -- CRITICAL LOCK IDENTITY CHECK: Worker login must match locked_by database identity
        IF NOT (session_user = 'platform_admin' OR session_user = 'postgres') THEN
            IF split_part(v_task.locked_by, ':', 1) IS DISTINCT FROM session_user THEN
                RAISE EXCEPTION 'Task % is not locked by current worker % (42501)',
                    p_task_id, session_user
                    USING ERRCODE = '42501';
            END IF;
        END IF;

        IF v_task.status <> 'in_progress' THEN
            RAISE EXCEPTION 'Task % is not in in_progress state (current: %) (42501)', p_task_id, v_task.status
                USING ERRCODE = '42501';
        END IF;

        IF v_task.lease_token_hash IS DISTINCT FROM v_hashed_token THEN
            RAISE EXCEPTION 'Invalid lease token for task % (42501)', p_task_id
                USING ERRCODE = '42501';
        END IF;

        IF v_task.lease_expire IS NULL OR v_task.lease_expire <= clock_timestamp() THEN
            RAISE EXCEPTION 'Lease token for task % has expired (42501)', p_task_id
                USING ERRCODE = '42501';
        END IF;

        -- 4. Verify task-to-invoice association: task payload must specifically reference p_invoice_id
        IF (v_task.payload->>'invoice_id') IS DISTINCT FROM p_invoice_id::text THEN
            RAISE EXCEPTION 'Task % is not associated with invoice % (42501)', p_task_id, p_invoice_id
                USING ERRCODE = '42501';
        END IF;

        -- 5. Verify worker organization authorization (for worker_service_user)
        IF NOT (session_user = 'platform_admin' OR session_user = 'postgres') THEN
            SELECT org_id INTO v_auth_org
            FROM public.worker_service_authorizations
            WHERE worker_role_name = session_user
              AND org_id = v_task.org_id
              AND is_active = true;

            IF v_auth_org IS NULL THEN
                RAISE EXCEPTION 'Worker % is not authorized for task organization % (42501)', session_user, v_task.org_id
                    USING ERRCODE = '42501';
            END IF;
        END IF;

        -- 6. Lock and verify invoice: must exist, belong to task organization, and NOT have entered human review or terminal states
        SELECT id, org_id, status
        INTO v_inv
        FROM public.invoices
        WHERE id = p_invoice_id AND org_id = v_task.org_id
        FOR UPDATE;

        IF NOT FOUND THEN
            RAISE EXCEPTION 'Invoice % does not exist in organization % (42501)', p_invoice_id, v_task.org_id
                USING ERRCODE = '42501';
        END IF;

        -- CRITICAL INTEGRITY CHECK: Worker line item replacement is strictly forbidden once human review has started
        IF v_inv.status IN ('pending_review', 'validation_failed', 'approved', 'rejected', 'export_ready') THEN
            RAISE EXCEPTION 'Cannot modify line items on invoice % in status % (human review or final state protected) (42501)',
                p_invoice_id, v_inv.status
                USING ERRCODE = '42501';
        END IF;

        IF v_inv.status NOT IN ('uploaded', 'queued', 'processing', 'extracted') THEN
            RAISE EXCEPTION 'Invoice % is in invalid status % for OCR line item replacement (42501)',
                p_invoice_id, v_inv.status
                USING ERRCODE = '42501';
        END IF;

        -- 7. Atomic line-item replacement without fabricated defaults
        DELETE FROM public.invoice_line_items
        WHERE invoice_id = p_invoice_id AND org_id = v_task.org_id;

        FOR v_line IN SELECT * FROM jsonb_array_elements(p_lines)
        LOOP
            v_line_id := COALESCE((v_line->>'id')::uuid, gen_random_uuid());
            INSERT INTO public.invoice_line_items (
                id,
                invoice_id,
                org_id,
                item_description,
                hsn_sac_code,
                quantity,
                unit_price,
                taxable_amount,
                gst_rate,
                cgst_amount,
                sgst_amount,
                igst_amount,
                total_amount,
                extraction_confidence
            ) VALUES (
                v_line_id,
                p_invoice_id,
                v_task.org_id,
                COALESCE(v_line->>'item_description', 'Unspecified Extracted Item'),
                v_line->>'hsn_sac_code',
                (v_line->>'quantity')::numeric,
                (v_line->>'unit_price')::numeric,
                (v_line->>'taxable_amount')::numeric,
                (v_line->>'gst_rate')::numeric,
                (v_line->>'cgst_amount')::numeric,
                (v_line->>'sgst_amount')::numeric,
                (v_line->>'igst_amount')::numeric,
                (v_line->>'total_amount')::numeric,
                (v_line->>'confidence')::numeric
            );
            v_count := v_count + 1;
        END LOOP;

        RETURN v_count;
    END;
    $$;

    ALTER FUNCTION public.replace_ocr_line_items(uuid, text, uuid, jsonb) OWNER TO invoice_ocr_writer;
    REVOKE ALL ON FUNCTION public.replace_ocr_line_items(uuid, text, uuid, jsonb) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION public.replace_ocr_line_items(uuid, text, uuid, jsonb) TO worker_owner, platform_admin;
    """)

def downgrade() -> None:
    # 1. Restore previous replace_ocr_line_items definition from 0009 with postgres ownership
    op.execute("""
    CREATE OR REPLACE FUNCTION public.replace_ocr_line_items(
        p_task_id uuid,
        p_lease_token text,
        p_invoice_id uuid,
        p_lines jsonb
    )
    RETURNS int
    LANGUAGE plpgsql
    SECURITY DEFINER
    SET search_path = public, pg_temp
    AS $$
    DECLARE
        v_task record;
        v_inv record;
        v_auth_org uuid;
        v_line jsonb;
        v_count int := 0;
        v_hashed_token text;
        v_line_id uuid;
    BEGIN
        IF NOT (
            session_user = 'worker_owner'
            OR pg_has_role(session_user, 'worker_owner', 'MEMBER')
            OR session_user = 'platform_admin'
            OR pg_has_role(session_user, 'platform_admin', 'MEMBER')
            OR session_user = 'postgres'
        ) THEN
            RAISE EXCEPTION 'Caller is not authorized to replace OCR line items (42501)'
                USING ERRCODE = '42501';
        END IF;

        IF p_task_id IS NULL OR p_lease_token IS NULL OR p_invoice_id IS NULL THEN
            RAISE EXCEPTION 'Task ID, lease token, and invoice ID are required'
                USING ERRCODE = '22023';
        END IF;

        IF p_lines IS NULL OR jsonb_typeof(p_lines) <> 'array' THEN
            RAISE EXCEPTION 'Line items payload must be a JSON array'
                USING ERRCODE = '22023';
        END IF;

        v_hashed_token := encode(digest(p_lease_token, 'sha256'), 'hex');

        SELECT id, org_id, task_type, payload, status, lease_token_hash, lease_expire
        INTO v_task
        FROM public.worker_tasks
        WHERE id = p_task_id
        FOR UPDATE;

        IF NOT FOUND THEN
            RAISE EXCEPTION 'Task % does not exist (42501)', p_task_id
                USING ERRCODE = '42501';
        END IF;

        IF v_task.status <> 'in_progress' THEN
            RAISE EXCEPTION 'Task % is not in in_progress state (current: %) (42501)', p_task_id, v_task.status
                USING ERRCODE = '42501';
        END IF;

        IF v_task.lease_token_hash IS DISTINCT FROM v_hashed_token THEN
            RAISE EXCEPTION 'Invalid lease token for task % (42501)', p_task_id
                USING ERRCODE = '42501';
        END IF;

        IF v_task.lease_expire IS NULL OR v_task.lease_expire <= clock_timestamp() THEN
            RAISE EXCEPTION 'Lease token for task % has expired (42501)', p_task_id
                USING ERRCODE = '42501';
        END IF;

        IF (v_task.payload->>'invoice_id') IS DISTINCT FROM p_invoice_id::text THEN
            RAISE EXCEPTION 'Task % is not associated with invoice % (42501)', p_task_id, p_invoice_id
                USING ERRCODE = '42501';
        END IF;

        IF NOT (session_user = 'platform_admin' OR session_user = 'postgres') THEN
            SELECT org_id INTO v_auth_org
            FROM public.worker_service_authorizations
            WHERE worker_role_name = session_user
              AND org_id = v_task.org_id
              AND is_active = true;

            IF v_auth_org IS NULL THEN
                RAISE EXCEPTION 'Worker % is not authorized for task organization % (42501)', session_user, v_task.org_id
                    USING ERRCODE = '42501';
            END IF;
        END IF;

        SELECT id, org_id, status
        INTO v_inv
        FROM public.invoices
        WHERE id = p_invoice_id AND org_id = v_task.org_id
        FOR UPDATE;

        IF NOT FOUND THEN
            RAISE EXCEPTION 'Invoice % does not exist in organization % (42501)', p_invoice_id, v_task.org_id
                USING ERRCODE = '42501';
        END IF;

        IF v_inv.status IN ('pending_review', 'approved', 'rejected', 'export_ready') THEN
            RAISE EXCEPTION 'Cannot modify line items on invoice % in status % (human review or final state protected) (42501)',
                p_invoice_id, v_inv.status
                USING ERRCODE = '42501';
        END IF;

        IF v_inv.status NOT IN ('uploaded', 'queued', 'processing', 'extracted') THEN
            RAISE EXCEPTION 'Invoice % is in invalid status % for OCR line item replacement (42501)',
                p_invoice_id, v_inv.status
                USING ERRCODE = '42501';
        END IF;

        DELETE FROM public.invoice_line_items
        WHERE invoice_id = p_invoice_id AND org_id = v_task.org_id;

        FOR v_line IN SELECT * FROM jsonb_array_elements(p_lines)
        LOOP
            v_line_id := COALESCE((v_line->>'id')::uuid, gen_random_uuid());
            INSERT INTO public.invoice_line_items (
                id, invoice_id, org_id, item_description, hsn_sac_code,
                quantity, unit_price, taxable_amount, gst_rate,
                cgst_amount, sgst_amount, igst_amount, total_amount
            ) VALUES (
                v_line_id,
                p_invoice_id,
                v_task.org_id,
                COALESCE(v_line->>'item_description', 'Item'),
                v_line->>'hsn_sac_code',
                COALESCE((v_line->>'quantity')::numeric, 1.000),
                COALESCE((v_line->>'unit_price')::numeric, 0.00),
                COALESCE((v_line->>'taxable_amount')::numeric, 0.00),
                COALESCE((v_line->>'gst_rate')::numeric, 18.00),
                COALESCE((v_line->>'cgst_amount')::numeric, 0.00),
                COALESCE((v_line->>'sgst_amount')::numeric, 0.00),
                COALESCE((v_line->>'igst_amount')::numeric, 0.00),
                COALESCE((v_line->>'total_amount')::numeric, 0.00)
            );
            v_count := v_count + 1;
        END LOOP;

        RETURN v_count;
    END;
    $$;

    ALTER FUNCTION public.replace_ocr_line_items(uuid, text, uuid, jsonb) OWNER TO postgres;
    REVOKE ALL ON FUNCTION public.replace_ocr_line_items(uuid, text, uuid, jsonb) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION public.replace_ocr_line_items(uuid, text, uuid, jsonb) TO worker_owner, platform_admin;
    """)

    # 2. Drop OCR-writer RLS policies and role
    op.execute("""
    DROP POLICY IF EXISTS worker_auth_ocr_writer ON public.worker_service_authorizations;
    DROP POLICY IF EXISTS worker_tasks_ocr_writer_update_policy ON public.worker_tasks;
    DROP POLICY IF EXISTS worker_tasks_ocr_writer_select_policy ON public.worker_tasks;
    DROP POLICY IF EXISTS worker_tasks_ocr_writer_policy ON public.worker_tasks;
    DROP POLICY IF EXISTS invoice_lines_ocr_writer_policy ON public.invoice_line_items;
    DROP POLICY IF EXISTS invoices_ocr_writer_policy ON public.invoices;

    REVOKE ALL ON public.worker_service_authorizations FROM invoice_ocr_writer;
    REVOKE ALL ON public.invoice_line_items FROM invoice_ocr_writer;
    REVOKE ALL ON public.invoices FROM invoice_ocr_writer;
    REVOKE ALL ON public.worker_tasks FROM invoice_ocr_writer;
    REVOKE USAGE ON SCHEMA public FROM invoice_ocr_writer;
    DROP OWNED BY invoice_ocr_writer;
    DROP ROLE IF EXISTS invoice_ocr_writer;
    """)

    # 3. Restore previous column definitions, defaults, and NOT NULL constraints
    op.execute("""
    ALTER TABLE public.invoice_line_items DISABLE TRIGGER trg_invoice_lines_immutable;

    -- Data preservation fallback before restoring NOT NULL
    UPDATE public.invoice_line_items SET quantity = 1.000 WHERE quantity IS NULL;
    UPDATE public.invoice_line_items SET unit_price = 0.00 WHERE unit_price IS NULL;
    UPDATE public.invoice_line_items SET taxable_amount = 0.00 WHERE taxable_amount IS NULL;
    UPDATE public.invoice_line_items SET gst_rate = 18.00 WHERE gst_rate IS NULL;
    UPDATE public.invoice_line_items SET cgst_amount = 0.00 WHERE cgst_amount IS NULL;
    UPDATE public.invoice_line_items SET sgst_amount = 0.00 WHERE sgst_amount IS NULL;
    UPDATE public.invoice_line_items SET igst_amount = 0.00 WHERE igst_amount IS NULL;
    UPDATE public.invoice_line_items SET total_amount = 0.00 WHERE total_amount IS NULL;

    ALTER TABLE public.invoice_line_items
        ALTER COLUMN quantity SET DEFAULT 1.000,
        ALTER COLUMN quantity SET NOT NULL,
        ALTER COLUMN unit_price SET NOT NULL,
        ALTER COLUMN taxable_amount SET NOT NULL,
        ALTER COLUMN gst_rate SET DEFAULT 18.00,
        ALTER COLUMN gst_rate SET NOT NULL,
        ALTER COLUMN cgst_amount SET DEFAULT 0.00,
        ALTER COLUMN cgst_amount SET NOT NULL,
        ALTER COLUMN sgst_amount SET DEFAULT 0.00,
        ALTER COLUMN sgst_amount SET NOT NULL,
        ALTER COLUMN igst_amount SET DEFAULT 0.00,
        ALTER COLUMN igst_amount SET NOT NULL,
        ALTER COLUMN total_amount SET NOT NULL;

    ALTER TABLE public.invoice_line_items
        DROP COLUMN IF EXISTS extraction_confidence;

    ALTER TABLE public.invoice_line_items ENABLE TRIGGER trg_invoice_lines_immutable;

    ALTER TABLE public.invoices
        DROP COLUMN IF EXISTS ocr_raw_response,
        DROP COLUMN IF EXISTS ocr_confidence;

    UPDATE public.business_profiles SET preceding_fy_aato = 50000000.00 WHERE preceding_fy_aato IS NULL;
    ALTER TABLE public.business_profiles
        ALTER COLUMN preceding_fy_aato SET DEFAULT 50000000.00,
        ALTER COLUMN preceding_fy_aato SET NOT NULL;
    """)
