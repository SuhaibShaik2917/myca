"""Extensions, Roles, Enums, and Hardened Audit Subsystem with Recursive Redaction

Revision ID: 0001_init_core
Revises: None
Create Date: 2026-11-17 04:30:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '0001_init_core'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    # 1. Extensions
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp";')
    op.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto";')

    # 2. Database Roles
    op.execute("""
    DO $$
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'task_queue_owner') THEN
            CREATE ROLE task_queue_owner WITH NOLOGIN;
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'v_org_users_owner') THEN
            CREATE ROLE v_org_users_owner WITH NOLOGIN;
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'audit_func_owner') THEN
            CREATE ROLE audit_func_owner WITH NOLOGIN;
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'platform_admin') THEN
            CREATE ROLE platform_admin WITH NOLOGIN;
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_user') THEN
            CREATE ROLE app_user WITH NOLOGIN;
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'worker_owner') THEN
            CREATE ROLE worker_owner WITH NOLOGIN;
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'audit_writer') THEN
            CREATE ROLE audit_writer WITH NOLOGIN;
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'audit_reader') THEN
            CREATE ROLE audit_reader WITH NOLOGIN;
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'system_auditor') THEN
            CREATE ROLE system_auditor WITH NOLOGIN;
        END IF;

        -- Service Login Accounts
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'platform_admin_user') THEN
            CREATE ROLE platform_admin_user WITH LOGIN IN ROLE platform_admin;
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_service_user') THEN
            CREATE ROLE app_service_user WITH LOGIN IN ROLE app_user;
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'worker_service_user') THEN
            CREATE ROLE worker_service_user WITH LOGIN IN ROLE worker_owner;
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'audit_service_user') THEN
            CREATE ROLE audit_service_user WITH LOGIN IN ROLE audit_writer;
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'auditor_service_user') THEN
            CREATE ROLE auditor_service_user WITH LOGIN IN ROLE audit_reader;
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sys_auditor_service_user') THEN
            CREATE ROLE sys_auditor_service_user WITH LOGIN IN ROLE system_auditor;
        END IF;
    END;
    $$;
    """)

    # 3. Enums
    op.execute("""
    DO $$
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'org_role') THEN
            CREATE TYPE public.org_role AS ENUM ('owner', 'admin', 'accountant', 'viewer');
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'membership_status') THEN
            CREATE TYPE public.membership_status AS ENUM ('active', 'suspended', 'invited');
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'document_type') THEN
            CREATE TYPE public.document_type AS ENUM ('invoice', 'bank_statement', 'gst_return', 'itr', 'other');
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'invoice_status') THEN
            CREATE TYPE public.invoice_status AS ENUM (
                'uploaded', 'queued', 'processing', 'extracted', 
                'validation_failed', 'pending_review', 'approved', 'rejected', 'export_ready'
            );
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'gst_filing_status') THEN
            CREATE TYPE public.gst_filing_status AS ENUM ('filed', 'pending', 'late', 'rejected');
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'task_status') THEN
            CREATE TYPE public.task_status AS ENUM ('queued', 'in_progress', 'completed', 'failed');
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'audit_action') THEN
            CREATE TYPE public.audit_action AS ENUM ('CREATE', 'UPDATE', 'DELETE', 'APPROVE', 'REJECT', 'EXPORT', 'EXECUTE');
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'scheme_category') THEN
            CREATE TYPE public.scheme_category AS ENUM ('subsidy', 'credit_guarantee', 'technology_upgradation', 'export_promotion', 'skill_development');
        END IF;
    END;
    $$;
    """)

    # 4. Audit Schema and Table
    op.execute('CREATE SCHEMA IF NOT EXISTS audit;')
    op.execute("""
    CREATE TABLE IF NOT EXISTS audit.audit_logs (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id uuid,
        entity_name text NOT NULL,
        entity_id text NOT NULL,
        action audit_action NOT NULL,
        actor_role text NOT NULL,
        actor_id uuid,
        previous_state jsonb,
        new_state jsonb,
        metadata jsonb DEFAULT '{}'::jsonb,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp()
    );
    """)

    # 5. Recursive JSON Redaction Function (H3)
    op.execute("""
    CREATE OR REPLACE FUNCTION audit.redact_sensitive_keys(p_data jsonb)
    RETURNS jsonb
    LANGUAGE plpgsql
    IMMUTABLE
    AS $$
    DECLARE
        v_key text;
        v_val jsonb;
        v_result jsonb := '{}'::jsonb;
        v_elem jsonb;
        v_arr jsonb := '[]'::jsonb;
    BEGIN
        IF p_data IS NULL THEN
            RETURN NULL;
        END IF;

        IF jsonb_typeof(p_data) = 'object' THEN
            FOR v_key, v_val IN SELECT * FROM jsonb_each(p_data) LOOP
                IF lower(v_key) ~* '(password|secret|token|pan|aadhaar|bank_account|account_number|key|credentials|auth|private_key|jwt)' THEN
                    v_result := jsonb_set(v_result, ARRAY[v_key], '"[REDACTED]"'::jsonb);
                ELSIF jsonb_typeof(v_val) IN ('object', 'array') THEN
                    v_result := jsonb_set(v_result, ARRAY[v_key], audit.redact_sensitive_keys(v_val));
                ELSE
                    v_result := jsonb_set(v_result, ARRAY[v_key], v_val);
                END IF;
            END LOOP;
            RETURN v_result;
        ELSIF jsonb_typeof(p_data) = 'array' THEN
            FOR v_elem IN SELECT * FROM jsonb_array_elements(p_data) LOOP
                IF jsonb_typeof(v_elem) IN ('object', 'array') THEN
                    v_arr := v_arr || jsonb_build_array(audit.redact_sensitive_keys(v_elem));
                ELSE
                    v_arr := v_arr || jsonb_build_array(v_elem);
                END IF;
            END LOOP;
            RETURN v_arr;
        ELSE
            RETURN p_data;
        END IF;
    END;
    $$;
    """)

    # 6. Hardened Security Definer Logging Function with Redaction (H3) and USAGE Grant (H2)
    op.execute("""
    CREATE OR REPLACE FUNCTION audit.log_audit_event(
        p_org_id uuid,
        p_entity_name text,
        p_entity_id text,
        p_action audit_action,
        p_previous_state jsonb DEFAULT NULL,
        p_new_state jsonb DEFAULT NULL,
        p_metadata jsonb DEFAULT '{}'::jsonb
    )
    RETURNS uuid
    LANGUAGE plpgsql
    SECURITY DEFINER
    SET search_path = audit, public, pg_temp
    AS $$
    DECLARE
        v_audit_id uuid;
        v_actor_user uuid;
    BEGIN
        v_actor_user := NULLIF(current_setting('app.current_user', true), '')::uuid;
        
        INSERT INTO audit.audit_logs (
            org_id, entity_name, entity_id, action, actor_role, actor_id, 
            previous_state, new_state, metadata, created_at
        )
        VALUES (
            p_org_id, p_entity_name, p_entity_id, p_action, session_user, v_actor_user,
            audit.redact_sensitive_keys(p_previous_state),
            audit.redact_sensitive_keys(p_new_state),
            audit.redact_sensitive_keys(p_metadata),
            clock_timestamp()
        )
        RETURNING id INTO v_audit_id;
        
        RETURN v_audit_id;
    END;
    $$;

    ALTER FUNCTION audit.redact_sensitive_keys(jsonb) OWNER TO audit_func_owner;
    ALTER FUNCTION audit.log_audit_event(uuid, text, text, audit_action, jsonb, jsonb, jsonb) OWNER TO audit_func_owner;
    
    REVOKE ALL ON FUNCTION audit.redact_sensitive_keys(jsonb) FROM PUBLIC;
    REVOKE ALL ON FUNCTION audit.log_audit_event(uuid, text, text, audit_action, jsonb, jsonb, jsonb) FROM PUBLIC;
    
    GRANT EXECUTE ON FUNCTION audit.redact_sensitive_keys(jsonb) TO app_user, worker_owner, platform_admin;
    GRANT EXECUTE ON FUNCTION audit.log_audit_event(uuid, text, text, audit_action, jsonb, jsonb, jsonb) TO app_user, worker_owner, platform_admin;
    
    -- Grant USAGE on audit schema (H2)
    GRANT USAGE ON SCHEMA audit TO app_user, worker_owner, platform_admin, audit_func_owner, audit_reader, system_auditor;
    """)

def downgrade() -> None:
    # Explicit dependency-ordered drops (H7)
    op.execute('DROP FUNCTION IF EXISTS audit.log_audit_event(uuid, text, text, audit_action, jsonb, jsonb, jsonb);')
    op.execute('DROP FUNCTION IF EXISTS audit.redact_sensitive_keys(jsonb);')
    op.execute('DROP TABLE IF EXISTS audit.audit_logs;')
    op.execute('DROP SCHEMA IF EXISTS audit;')
    
    # Clean enum drops
    enums = [
        'scheme_category', 'audit_action', 'task_status', 'gst_filing_status',
        'invoice_status', 'document_type', 'membership_status', 'org_role'
    ]
    for e in enums:
        op.execute(f"DROP TYPE IF EXISTS public.{e};")
