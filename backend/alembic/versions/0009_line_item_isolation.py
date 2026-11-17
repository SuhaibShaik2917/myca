"""Worker Line-Item Write Isolation, Deduplication Constraint, and FY AATO

Revision ID: 0009_line_item_isolation
Revises: 0008_enqueue_and_brain
Create Date: 2026-11-17 13:55:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '0009_line_item_isolation'
down_revision: Union[str, None] = '0008_enqueue_and_brain'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    # 1. Database-Level Unique Constraint for Document Deduplication (org_id, sha256_checksum)
    op.execute("""
    ALTER TABLE public.documents
    DROP CONSTRAINT IF EXISTS uq_documents_org_sha256;
    ALTER TABLE public.documents
    ADD CONSTRAINT uq_documents_org_sha256 UNIQUE (org_id, sha256_checksum);
    """)

    # 2. Add preceding_fy_aato to business_profiles (Default ₹5 Crore / 50,000,000.00)
    op.execute("""
    ALTER TABLE public.business_profiles
    ADD COLUMN IF NOT EXISTS preceding_fy_aato numeric(15, 2) NOT NULL DEFAULT 50000000.00;
    """)

    # 3. Clean up invoice_transition_rules: Workers can NEVER transition pending_review -> processing!
    op.execute("""
    DELETE FROM public.invoice_transition_rules
    WHERE from_status = 'pending_review' AND to_status = 'processing' AND required_role = 'worker';
    DELETE FROM public.invoice_transition_rules
    WHERE from_status = 'validation_failed' AND to_status = 'processing' AND required_role = 'worker';
    """)

    # 4. Revoke broad direct worker DML on invoice_line_items
    op.execute("""
    REVOKE INSERT, UPDATE, DELETE ON public.invoice_line_items FROM worker_owner;
    """)

    # 5. Controlled Narrow SECURITY DEFINER Function for Worker Line-Item Replacement
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

        -- 3. Lock and verify worker task: active lease, valid token, in_progress status, and task_type = 'invoice_ocr'
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

        -- 6. Lock and verify invoice: must exist, belong to task organization, and NOT have entered human review or immutable states
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

        -- 7. Atomic line-item replacement
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
                total_amount
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

def downgrade() -> None:
    op.execute("REVOKE EXECUTE ON FUNCTION public.replace_ocr_line_items(uuid, text, uuid, jsonb) FROM worker_owner, platform_admin;")
    op.execute("DROP FUNCTION IF EXISTS public.replace_ocr_line_items(uuid, text, uuid, jsonb);")
    op.execute("GRANT INSERT, UPDATE, DELETE ON public.invoice_line_items TO worker_owner;")
    op.execute("ALTER TABLE public.business_profiles DROP COLUMN IF EXISTS preceding_fy_aato;")
    op.execute("ALTER TABLE public.documents DROP CONSTRAINT IF EXISTS uq_documents_org_sha256;")
