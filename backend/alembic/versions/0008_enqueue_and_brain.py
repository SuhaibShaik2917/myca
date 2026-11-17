"""Enqueue Task Function and Invoice Brain Queue Policies

Revision ID: 0008_enqueue_task_and_invoice_brain
Revises: 0007_rls_policies_and_grants
Create Date: 2026-11-17 05:45:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '0008_enqueue_and_brain'
down_revision: Union[str, None] = '0007_rls_policies_and_grants'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    # 1. Controlled Enqueue Task Function (Security Definer, owned by task_queue_owner)
    # Allows authenticated app users to enqueue background tasks for their active tenant context
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
        v_org uuid;
        v_user_id uuid;
        v_task_id uuid;
        v_now timestamptz;
    BEGIN
        v_now := clock_timestamp();

        -- Validate task type
        IF p_task_type NOT IN ('invoice_ocr', 'gst_validation', 'gst_reconciliation', 'scheme_matching') THEN
            RAISE EXCEPTION 'Unsupported task type: %', p_task_type;
        END IF;

        -- Resolve and validate tenant organization
        IF p_org IS NOT NULL THEN
            v_org := p_org;
        ELSE
            v_org := NULLIF(current_setting('app.current_org', true), '')::uuid;
        END IF;

        IF v_org IS NULL THEN
            RAISE EXCEPTION 'Cannot enqueue task without valid organization context';
        END IF;

        -- For non-admin callers, enforce that p_org matches session tenant context
        IF NOT (session_user = 'platform_admin' OR pg_has_role(session_user, 'platform_admin', 'MEMBER') OR session_user = 'postgres') THEN
            IF v_org <> NULLIF(current_setting('app.current_org', true), '')::uuid THEN
                RAISE EXCEPTION 'Caller cannot enqueue task for foreign organization % (42501)', v_org;
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
            COALESCE(p_payload, '{}'::jsonb),
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

    # 2. Add SELECT policy and grant for app_user on worker_tasks so tenants can poll task status
    # And grant SELECT on business_profiles to worker_owner (enabling business_profiles_worker policy)
    op.execute("""
    CREATE POLICY worker_tasks_tenant_select ON public.worker_tasks
        FOR SELECT TO app_user
        USING (org_id = NULLIF(current_setting('app.current_org', true), '')::uuid);

    GRANT SELECT ON public.worker_tasks TO app_user;
    GRANT SELECT ON public.business_profiles TO worker_owner;
    """)

def downgrade() -> None:
    op.execute("REVOKE SELECT ON public.business_profiles FROM worker_owner;")
    op.execute("DROP POLICY IF EXISTS worker_tasks_tenant_select ON public.worker_tasks;")
    op.execute("REVOKE SELECT ON public.worker_tasks FROM app_user;")
    op.execute("DROP FUNCTION IF EXISTS public.enqueue_task(text, jsonb, uuid);")
