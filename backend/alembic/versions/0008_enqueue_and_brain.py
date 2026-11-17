"""Enqueue Task Hardening, Restricted Task Status View, and Worker Idempotency

Revision ID: 0008_enqueue_and_brain
Revises: 0007_rls_policies_and_grants
Create Date: 2026-11-17 06:10:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '0008_enqueue_and_brain'
down_revision: Union[str, None] = '0007_rls_policies_and_grants'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    # 1. Hardened Enqueue Task Function (Security Definer, owned by task_queue_owner)
    # Fixes NULL tenant-context bypass via IS DISTINCT FROM and explicit non-null context enforcement
    op.execute("""
    CREATE OR REPLACE FUNCTION public.enqueue_task(
        p_task_type text,
        p_payload jsonb,
        p_org uuid DEFAULT NULL
    )
    RETURNS uuid
    LANGUAGE plpgsql
    SECURITY DEFINER
    SET search_path = public, pg_temp
    AS $$
    DECLARE
        v_context_org uuid;
        v_org uuid;
        v_user_id uuid;
        v_task_id uuid;
        v_now timestamptz;
    BEGIN
        v_now := clock_timestamp();

        -- 1. Explicitly validate task type (reject NULL or unregistered types)
        IF p_task_type IS NULL OR p_task_type NOT IN ('invoice_ocr', 'gst_validation', 'gst_reconciliation', 'scheme_matching') THEN
            RAISE EXCEPTION 'Invalid or missing task type: %', p_task_type;
        END IF;

        -- 2. Validate payload structure and size
        IF p_payload IS NULL OR jsonb_typeof(p_payload) <> 'object' THEN
            RAISE EXCEPTION 'Task payload must be a non-null JSON object';
        END IF;

        IF octet_length(p_payload::text) > 65536 THEN
            RAISE EXCEPTION 'Task payload exceeds maximum permitted size of 64KB';
        END IF;

        -- 3. Resolve trusted application transaction context
        v_context_org := NULLIF(current_setting('app.current_org', true), '')::uuid;

        -- Resolve target organization
        IF p_org IS NOT NULL THEN
            v_org := p_org;
        ELSE
            v_org := v_context_org;
        END IF;

        IF v_org IS NULL THEN
            RAISE EXCEPTION 'Cannot enqueue task without valid organization context'
                USING ERRCODE = '42501';
        END IF;

        -- 4. Strict tenant context authorization check (eliminates NULL comparison bypass)
        IF NOT (
            session_user = 'platform_admin'
            OR pg_has_role(session_user, 'platform_admin', 'MEMBER')
            OR session_user = 'postgres'
        ) THEN
            IF v_context_org IS NULL OR v_org IS DISTINCT FROM v_context_org THEN
                RAISE EXCEPTION 'Caller is not authorized to enqueue tasks for organization % (42501)', v_org
                    USING ERRCODE = '42501';
            END IF;
        END IF;

        v_user_id := NULLIF(current_setting('app.current_user', true), '')::uuid;

        INSERT INTO public.worker_tasks (
            id,
            org_id,
            initiated_by,
            task_type,
            payload,
            status,
            attempts,
            max_attempts,
            created_at,
            updated_at
        ) VALUES (
            gen_random_uuid(),
            v_org,
            v_user_id,
            p_task_type,
            p_payload,
            'queued',
            0,
            3,
            v_now,
            v_now
        ) RETURNING id INTO v_task_id;

        RETURN v_task_id;
    END;
    $$;

    ALTER FUNCTION public.enqueue_task(text, jsonb, uuid) OWNER TO task_queue_owner;
    REVOKE ALL ON FUNCTION public.enqueue_task(text, jsonb, uuid) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION public.enqueue_task(text, jsonb, uuid) TO app_user, platform_admin;
    """)

    # 2. Restricted Tenant Task Status View
    # Revokes direct table SELECT on worker_tasks from app_user (protects lease_token_hash, payload, result, locks)
    # Exposes only minimum non-sensitive status fields via a security barrier view
    op.execute("""
    DROP VIEW IF EXISTS public.v_tenant_task_status;
    CREATE VIEW public.v_tenant_task_status
    WITH (security_barrier = true)
    AS
    SELECT
        id,
        org_id,
        task_type,
        status,
        created_at,
        updated_at
    FROM public.worker_tasks
    WHERE org_id = NULLIF(current_setting('app.current_org', true), '')::uuid;

    ALTER VIEW public.v_tenant_task_status OWNER TO task_queue_owner;
    GRANT SELECT ON public.v_tenant_task_status TO app_user;

    -- Ensure app_user has NO direct table-level SELECT on worker_tasks
    REVOKE ALL ON public.worker_tasks FROM app_user;

    -- Worker permissions: SELECT on business_profiles and DELETE on draft line items for idempotent retry
    GRANT SELECT ON public.business_profiles TO worker_owner;
    GRANT DELETE ON public.invoice_line_items TO worker_owner;

    -- Transition rules for human revalidation and worker task retries
    INSERT INTO public.invoice_transition_rules (from_status, to_status, required_role) VALUES
        ('pending_review', 'validation_failed', 'human'),
        ('validation_failed', 'pending_review', 'human'),
        ('pending_review', 'processing', 'worker'),
        ('validation_failed', 'processing', 'worker')
    ON CONFLICT DO NOTHING;
    """)

def downgrade() -> None:
    op.execute("REVOKE DELETE ON public.invoice_line_items FROM worker_owner;")
    op.execute("REVOKE SELECT ON public.business_profiles FROM worker_owner;")
    op.execute("DROP VIEW IF EXISTS public.v_tenant_task_status;")
    op.execute("DROP FUNCTION IF EXISTS public.enqueue_task(text, jsonb, uuid);")
