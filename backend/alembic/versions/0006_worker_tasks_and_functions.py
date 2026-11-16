"""Worker Tasks Queue, Authorizations, and Hardened Lifecycle Functions

Revision ID: 0006_worker_tasks_and_functions
Revises: 0005_government_schemes
Create Date: 2026-11-17 04:35:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '0006_worker_tasks_and_functions'
down_revision: Union[str, None] = '0005_government_schemes'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    # 1. Worker Tasks Table
    op.execute("""
    CREATE TABLE IF NOT EXISTS public.worker_tasks (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id uuid NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
        initiated_by uuid REFERENCES public.global_users(id) ON DELETE SET NULL,
        task_type text NOT NULL,
        status task_status NOT NULL DEFAULT 'queued',
        payload jsonb NOT NULL DEFAULT '{}'::jsonb,
        result jsonb,
        error_message text,
        locked_by text,
        locked_at timestamptz,
        lease_expire timestamptz,
        lease_token_hash text,
        attempts int NOT NULL DEFAULT 0,
        max_attempts int NOT NULL DEFAULT 3,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        CONSTRAINT uq_worker_tasks_id_org UNIQUE (id, org_id)
    );
    CREATE INDEX IF NOT EXISTS idx_worker_tasks_claim_lookup 
        ON public.worker_tasks (org_id, status, created_at) 
        WHERE status IN ('queued', 'in_progress');
    """)

    # 2. Worker Service Authorizations
    op.execute("""
    CREATE TABLE IF NOT EXISTS public.worker_service_authorizations (
        worker_role_name text NOT NULL,
        org_id uuid NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
        is_active boolean NOT NULL DEFAULT true,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        PRIMARY KEY (worker_role_name, org_id)
    );
    CREATE INDEX IF NOT EXISTS idx_worker_service_auth_lookup 
        ON public.worker_service_authorizations (worker_role_name, org_id) 
        WHERE is_active = true;
    """)

    # 3. is_worker_authorized Security Definer Function
    op.execute("""
    CREATE OR REPLACE FUNCTION public.is_worker_authorized(p_org uuid)
    RETURNS boolean
    LANGUAGE plpgsql
    SECURITY DEFINER
    STABLE
    SET search_path = public, pg_temp
    AS $$
    BEGIN
        IF session_user = 'platform_admin' OR pg_has_role(session_user, 'platform_admin', 'MEMBER') OR session_user = 'postgres' THEN
            RETURN true;
        END IF;

        RETURN EXISTS (
            SELECT 1 FROM public.worker_service_authorizations
            WHERE worker_role_name = session_user
              AND org_id = p_org
              AND is_active = true
        );
    END;
    $$;

    ALTER FUNCTION public.is_worker_authorized(uuid) OWNER TO postgres;
    REVOKE ALL ON FUNCTION public.is_worker_authorized(uuid) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION public.is_worker_authorized(uuid) TO worker_owner, task_queue_owner, platform_admin;
    """)

    # 4. reap_expired_tasks Function
    op.execute("""
    CREATE OR REPLACE FUNCTION public.reap_expired_tasks(
        p_org uuid DEFAULT NULL,
        p_limit int DEFAULT 100
    )
    RETURNS integer
    LANGUAGE plpgsql
    SECURITY DEFINER
    SET search_path = public, pg_temp
    AS $$
    DECLARE
        v_reaped_count integer := 0;
        v_now timestamptz := clock_timestamp();
    BEGIN
        IF NOT (session_user = 'worker_owner' OR pg_has_role(session_user, 'worker_owner', 'MEMBER')
                OR session_user = 'platform_admin' OR pg_has_role(session_user, 'platform_admin', 'MEMBER')
                OR session_user = 'task_queue_owner'
                OR session_user = 'postgres') THEN
            RAISE EXCEPTION 'Unauthorized reap_expired_tasks: session_user %', session_user;
        END IF;

        IF p_limit IS NULL OR p_limit < 1 OR p_limit > 100 THEN
            RAISE EXCEPTION 'Invalid p_limit: must be between 1 and 100';
        END IF;

        IF p_org IS NULL THEN
            IF NOT (session_user = 'platform_admin' 
                    OR pg_has_role(session_user, 'platform_admin', 'MEMBER') 
                    OR session_user = 'postgres') THEN
                RAISE EXCEPTION 'Global task reaping is restricted to platform administrators';
            END IF;
        ELSE
            IF NOT (session_user = 'platform_admin' 
                    OR pg_has_role(session_user, 'platform_admin', 'MEMBER') 
                    OR session_user = 'postgres') THEN
                IF NOT public.is_worker_authorized(p_org) THEN
                    RAISE EXCEPTION 'Worker % is not authorized to reap organization %', session_user, p_org;
                END IF;
            END IF;
        END IF;

        WITH reaped AS (
            SELECT id
            FROM public.worker_tasks
            WHERE status = 'in_progress'
              AND lease_expire <= v_now
              AND attempts >= max_attempts
              AND (p_org IS NULL OR org_id = p_org)
            ORDER BY lease_expire ASC
            LIMIT p_limit
            FOR UPDATE SKIP LOCKED
        )
        UPDATE public.worker_tasks t
        SET status = 'failed',
            error_message = 'Task lease expired after maximum retry attempts exhausted (worker timeout/crash)',
            locked_by = NULL,
            locked_at = NULL,
            lease_expire = NULL,
            lease_token_hash = NULL,
            updated_at = v_now
        FROM reaped
        WHERE t.id = reaped.id;

        GET DIAGNOSTICS v_reaped_count = ROW_COUNT;
        RETURN v_reaped_count;
    END;
    $$;

    ALTER FUNCTION public.reap_expired_tasks(uuid, int) OWNER TO task_queue_owner;
    REVOKE ALL ON FUNCTION public.reap_expired_tasks(uuid, int) FROM PUBLIC;
    REVOKE ALL ON FUNCTION public.reap_expired_tasks(uuid, int) FROM worker_owner, worker_service_user;
    REVOKE ALL ON FUNCTION public.reap_expired_tasks(uuid, int) FROM app_user, app_service_user;
    GRANT EXECUTE ON FUNCTION public.reap_expired_tasks(uuid, int) TO platform_admin;
    """)

    # 5. claim_task Function with Strict Retry Limit Check (C4)
    op.execute("""
    CREATE OR REPLACE FUNCTION public.claim_task(
        p_org uuid,
        p_instance_label text DEFAULT NULL,
        OUT o_task_id uuid,
        OUT o_lease_token text
    )
    RETURNS record
    LANGUAGE plpgsql
    SECURITY DEFINER
    SET search_path = public, pg_temp
    AS $$
    DECLARE
        v_task_id uuid;
        v_raw_token text;
        v_token_hash text;
        v_instance_info text;
        v_now timestamptz;
    BEGIN
        IF NOT (session_user = 'worker_owner' OR pg_has_role(session_user, 'worker_owner', 'MEMBER')
                OR session_user = 'platform_admin' OR pg_has_role(session_user, 'platform_admin', 'MEMBER')
                OR session_user = 'postgres') THEN
            RAISE EXCEPTION 'Unauthorized claim_task: session_user % must have role worker_owner', session_user;
        END IF;

        IF NOT public.is_worker_authorized(p_org) THEN
            RAISE EXCEPTION 'Worker % is not authorized to process organization %', session_user, p_org;
        END IF;

        PERFORM set_config('app.current_org', p_org::text, true);

        -- Auto-reap dead tasks stuck at max attempts
        PERFORM public.reap_expired_tasks(p_org, 50);

        v_now := clock_timestamp();

        -- C4 Fix: Ensure attempts < max_attempts for both queued and in_progress claims
        SELECT id INTO v_task_id
        FROM public.worker_tasks
        WHERE org_id = p_org
          AND (
              (status = 'queued' AND locked_by IS NULL AND attempts < max_attempts)
              OR (status = 'in_progress' AND lease_expire <= v_now AND attempts < max_attempts)
          )
        ORDER BY created_at ASC
        LIMIT 1
        FOR UPDATE SKIP LOCKED;

        IF v_task_id IS NOT NULL THEN
            v_now := clock_timestamp();
            v_raw_token := encode(gen_random_bytes(32), 'hex');
            v_token_hash := encode(digest(v_raw_token, 'sha256'), 'hex');
            v_instance_info := session_user || ':' || COALESCE(NULLIF(trim(p_instance_label), ''), 'default');

            UPDATE public.worker_tasks
            SET locked_by = v_instance_info,
                locked_at = v_now,
                lease_expire = v_now + interval '5 minutes',
                lease_token_hash = v_token_hash,
                status = 'in_progress',
                attempts = attempts + 1,
                updated_at = v_now
            WHERE id = v_task_id;

            o_task_id := v_task_id;
            o_lease_token := v_raw_token;
        ELSE
            o_task_id := NULL;
            o_lease_token := NULL;
        END IF;
    END;
    $$;

    ALTER FUNCTION public.claim_task(uuid, text) OWNER TO task_queue_owner;
    REVOKE ALL ON FUNCTION public.claim_task(uuid, text) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION public.claim_task(uuid, text) TO worker_owner, platform_admin;
    """)

    # 6. complete_task Function
    op.execute("""
    CREATE OR REPLACE FUNCTION public.complete_task(
        p_task_id uuid,
        p_lease_token text,
        p_result jsonb
    )
    RETURNS void
    LANGUAGE plpgsql
    SECURITY DEFINER
    SET search_path = public, pg_temp
    AS $$
    DECLARE
        v_task public.worker_tasks%ROWTYPE;
        v_now timestamptz;
    BEGIN
        IF NOT (session_user = 'worker_owner' OR pg_has_role(session_user, 'worker_owner', 'MEMBER')
                OR session_user = 'platform_admin' OR pg_has_role(session_user, 'platform_admin', 'MEMBER')
                OR session_user = 'postgres') THEN
            RAISE EXCEPTION 'Unauthorized complete_task: session_user % must have role worker_owner', session_user;
        END IF;

        IF p_lease_token IS NULL OR trim(p_lease_token) = '' THEN
            RAISE EXCEPTION 'Lease token is required';
        END IF;

        SELECT * INTO v_task 
        FROM public.worker_tasks 
        WHERE id = p_task_id 
        FOR UPDATE;

        IF NOT FOUND THEN
            RAISE EXCEPTION 'Task % not found', p_task_id;
        END IF;

        v_now := clock_timestamp();

        IF v_task.status <> 'in_progress' THEN
            RAISE EXCEPTION 'Task % is not in_progress (status=%)', p_task_id, v_task.status;
        END IF;

        IF NOT (session_user = 'platform_admin' OR pg_has_role(session_user, 'platform_admin', 'MEMBER') OR session_user = 'postgres') THEN
            IF NOT public.is_worker_authorized(v_task.org_id) THEN
                RAISE EXCEPTION 'Worker % is not authorized for task organization %', session_user, v_task.org_id;
            END IF;
        END IF;

        IF v_task.lease_expire IS NULL OR v_task.lease_expire <= v_now THEN
            RAISE EXCEPTION 'Task % lease expired at %', p_task_id, v_task.lease_expire;
        END IF;

        IF v_task.lease_token_hash IS NULL 
           OR encode(digest(p_lease_token, 'sha256'), 'hex') <> v_task.lease_token_hash THEN
            RAISE EXCEPTION 'Invalid lease token for task %', p_task_id;
        END IF;

        UPDATE public.worker_tasks
        SET status = 'completed',
            result = p_result,
            locked_by = NULL,
            locked_at = NULL,
            lease_expire = NULL,
            lease_token_hash = NULL,
            updated_at = v_now
        WHERE id = p_task_id;
    END;
    $$;

    ALTER FUNCTION public.complete_task(uuid, text, jsonb) OWNER TO task_queue_owner;
    REVOKE ALL ON FUNCTION public.complete_task(uuid, text, jsonb) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION public.complete_task(uuid, text, jsonb) TO worker_owner, platform_admin;
    """)

    # 7. renew_task_lease Function with NULL Validation (H1)
    op.execute("""
    CREATE OR REPLACE FUNCTION public.renew_task_lease(
        p_task_id uuid,
        p_lease_token text,
        p_lease_minutes int DEFAULT 5
    )
    RETURNS timestamptz
    LANGUAGE plpgsql
    SECURITY DEFINER
    SET search_path = public, pg_temp
    AS $$
    DECLARE
        v_task public.worker_tasks%ROWTYPE;
        v_now timestamptz;
        v_new_expiry timestamptz;
    BEGIN
        IF NOT (session_user = 'worker_owner' OR pg_has_role(session_user, 'worker_owner', 'MEMBER')
                OR session_user = 'platform_admin' OR pg_has_role(session_user, 'platform_admin', 'MEMBER')
                OR session_user = 'postgres') THEN
            RAISE EXCEPTION 'Unauthorized renew_task_lease: session_user % must have role worker_owner', session_user;
        END IF;

        IF p_lease_token IS NULL OR trim(p_lease_token) = '' THEN
            RAISE EXCEPTION 'Lease token is required';
        END IF;
        
        -- H1 Fix: Explicitly reject NULL or out-of-range lease minutes
        IF p_lease_minutes IS NULL OR p_lease_minutes <= 0 OR p_lease_minutes > 60 THEN
            RAISE EXCEPTION 'Invalid lease minutes: % (must be between 1 and 60)', p_lease_minutes;
        END IF;

        SELECT * INTO v_task 
        FROM public.worker_tasks 
        WHERE id = p_task_id 
        FOR UPDATE;

        IF NOT FOUND THEN
            RAISE EXCEPTION 'Task % not found', p_task_id;
        END IF;

        v_now := clock_timestamp();

        IF v_task.status <> 'in_progress' THEN
            RAISE EXCEPTION 'Task % is not in_progress (status=%)', p_task_id, v_task.status;
        END IF;

        IF NOT (session_user = 'platform_admin' OR pg_has_role(session_user, 'platform_admin', 'MEMBER') OR session_user = 'postgres') THEN
            IF NOT public.is_worker_authorized(v_task.org_id) THEN
                RAISE EXCEPTION 'Worker % is not authorized for task organization %', session_user, v_task.org_id;
            END IF;
        END IF;

        IF v_task.lease_expire IS NULL OR v_task.lease_expire <= v_now THEN
            RAISE EXCEPTION 'Task % lease expired at %', p_task_id, v_task.lease_expire;
        END IF;

        IF v_task.lease_token_hash IS NULL 
           OR encode(digest(p_lease_token, 'sha256'), 'hex') <> v_task.lease_token_hash THEN
            RAISE EXCEPTION 'Invalid lease token for task %', p_task_id;
        END IF;

        v_new_expiry := v_now + (p_lease_minutes || ' minutes')::interval;
        UPDATE public.worker_tasks
        SET lease_expire = v_new_expiry,
            updated_at = v_now
        WHERE id = p_task_id;

        RETURN v_new_expiry;
    END;
    $$;

    ALTER FUNCTION public.renew_task_lease(uuid, text, int) OWNER TO task_queue_owner;
    REVOKE ALL ON FUNCTION public.renew_task_lease(uuid, text, int) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION public.renew_task_lease(uuid, text, int) TO worker_owner, platform_admin;
    """)

    # 8. fail_task Function
    op.execute("""
    CREATE OR REPLACE FUNCTION public.fail_task(
        p_task_id uuid,
        p_lease_token text,
        p_error text
    )
    RETURNS void
    LANGUAGE plpgsql
    SECURITY DEFINER
    SET search_path = public, pg_temp
    AS $$
    DECLARE
        v_task public.worker_tasks%ROWTYPE;
        v_now timestamptz;
    BEGIN
        IF NOT (session_user = 'worker_owner' OR pg_has_role(session_user, 'worker_owner', 'MEMBER')
                OR session_user = 'platform_admin' OR pg_has_role(session_user, 'platform_admin', 'MEMBER')
                OR session_user = 'postgres') THEN
            RAISE EXCEPTION 'Unauthorized fail_task: session_user % must have role worker_owner', session_user;
        END IF;

        IF p_lease_token IS NULL OR trim(p_lease_token) = '' THEN
            RAISE EXCEPTION 'Lease token is required';
        END IF;

        SELECT * INTO v_task 
        FROM public.worker_tasks 
        WHERE id = p_task_id 
        FOR UPDATE;

        IF NOT FOUND THEN
            RAISE EXCEPTION 'Task % not found', p_task_id;
        END IF;

        v_now := clock_timestamp();

        IF v_task.status <> 'in_progress' THEN
            RAISE EXCEPTION 'Task % is not in_progress (status=%)', p_task_id, v_task.status;
        END IF;

        IF NOT (session_user = 'platform_admin' OR pg_has_role(session_user, 'platform_admin', 'MEMBER') OR session_user = 'postgres') THEN
            IF NOT public.is_worker_authorized(v_task.org_id) THEN
                RAISE EXCEPTION 'Worker % is not authorized for task organization %', session_user, v_task.org_id;
            END IF;
        END IF;

        IF v_task.lease_expire IS NULL OR v_task.lease_expire <= v_now THEN
            RAISE EXCEPTION 'Task % lease expired at %', p_task_id, v_task.lease_expire;
        END IF;

        IF v_task.lease_token_hash IS NULL 
           OR encode(digest(p_lease_token, 'sha256'), 'hex') <> v_task.lease_token_hash THEN
            RAISE EXCEPTION 'Invalid lease token for task %', p_task_id;
        END IF;

        IF v_task.attempts >= v_task.max_attempts THEN
            UPDATE public.worker_tasks
            SET status = 'failed',
                error_message = p_error,
                locked_by = NULL,
                locked_at = NULL,
                lease_expire = NULL,
                lease_token_hash = NULL,
                updated_at = v_now
            WHERE id = p_task_id;
        ELSE
            UPDATE public.worker_tasks
            SET status = 'queued',
                error_message = p_error,
                locked_by = NULL,
                locked_at = NULL,
                lease_expire = NULL,
                lease_token_hash = NULL,
                updated_at = v_now
            WHERE id = p_task_id;
        END IF;
    END;
    $$;

    ALTER FUNCTION public.fail_task(uuid, text, text) OWNER TO task_queue_owner;
    REVOKE ALL ON FUNCTION public.fail_task(uuid, text, text) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION public.fail_task(uuid, text, text) TO worker_owner, platform_admin;
    """)

    # 9. release_task Function with Retry Limit Exhaustion Enforcement (C4)
    op.execute("""
    CREATE OR REPLACE FUNCTION public.release_task(
        p_task_id uuid,
        p_lease_token text
    )
    RETURNS void
    LANGUAGE plpgsql
    SECURITY DEFINER
    SET search_path = public, pg_temp
    AS $$
    DECLARE
        v_task public.worker_tasks%ROWTYPE;
        v_now timestamptz;
    BEGIN
        IF NOT (session_user = 'worker_owner' OR pg_has_role(session_user, 'worker_owner', 'MEMBER')
                OR session_user = 'platform_admin' OR pg_has_role(session_user, 'platform_admin', 'MEMBER')
                OR session_user = 'postgres') THEN
            RAISE EXCEPTION 'Unauthorized release_task: session_user % must have role worker_owner', session_user;
        END IF;

        IF p_lease_token IS NULL OR trim(p_lease_token) = '' THEN
            RAISE EXCEPTION 'Lease token is required';
        END IF;

        SELECT * INTO v_task 
        FROM public.worker_tasks 
        WHERE id = p_task_id 
        FOR UPDATE;

        IF NOT FOUND THEN
            RAISE EXCEPTION 'Task % not found', p_task_id;
        END IF;

        v_now := clock_timestamp();

        IF v_task.status <> 'in_progress' THEN
            RAISE EXCEPTION 'Task % is not in_progress (status=%)', p_task_id, v_task.status;
        END IF;

        IF NOT (session_user = 'platform_admin' OR pg_has_role(session_user, 'platform_admin', 'MEMBER') OR session_user = 'postgres') THEN
            IF NOT public.is_worker_authorized(v_task.org_id) THEN
                RAISE EXCEPTION 'Worker % is not authorized for task organization %', session_user, v_task.org_id;
            END IF;
        END IF;

        IF v_task.lease_expire IS NULL OR v_task.lease_expire <= v_now THEN
            RAISE EXCEPTION 'Task % lease expired at %', p_task_id, v_task.lease_expire;
        END IF;

        IF v_task.lease_token_hash IS NULL 
           OR encode(digest(p_lease_token, 'sha256'), 'hex') <> v_task.lease_token_hash THEN
            RAISE EXCEPTION 'Invalid lease token for task %', p_task_id;
        END IF;

        -- C4 Fix: If attempts already reached or exceeded max_attempts, transition to failed
        IF v_task.attempts >= v_task.max_attempts THEN
            UPDATE public.worker_tasks
            SET status = 'failed',
                error_message = 'Task released with maximum retry attempts exhausted',
                locked_by = NULL,
                locked_at = NULL,
                lease_expire = NULL,
                lease_token_hash = NULL,
                updated_at = v_now
            WHERE id = p_task_id;
        ELSE
            UPDATE public.worker_tasks
            SET status = 'queued',
                locked_by = NULL,
                locked_at = NULL,
                lease_expire = NULL,
                lease_token_hash = NULL,
                updated_at = v_now
            WHERE id = p_task_id;
        END IF;
    END;
    $$;

    ALTER FUNCTION public.release_task(uuid, text) OWNER TO task_queue_owner;
    REVOKE ALL ON FUNCTION public.release_task(uuid, text) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION public.release_task(uuid, text) TO worker_owner, platform_admin;
    """)

def downgrade() -> None:
    # Explicit dependency-ordered drops (H7)
    op.execute('DROP FUNCTION IF EXISTS public.release_task(uuid, text);')
    op.execute('DROP FUNCTION IF EXISTS public.fail_task(uuid, text, text);')
    op.execute('DROP FUNCTION IF EXISTS public.renew_task_lease(uuid, text, int);')
    op.execute('DROP FUNCTION IF EXISTS public.complete_task(uuid, text, jsonb);')
    op.execute('DROP FUNCTION IF EXISTS public.claim_task(uuid, text);')
    op.execute('DROP FUNCTION IF EXISTS public.reap_expired_tasks(uuid, int);')
    op.execute('DROP FUNCTION IF EXISTS public.is_worker_authorized(uuid);')
    op.execute('DROP TABLE IF EXISTS public.worker_service_authorizations;')
    op.execute('DROP TABLE IF EXISTS public.worker_tasks;')
