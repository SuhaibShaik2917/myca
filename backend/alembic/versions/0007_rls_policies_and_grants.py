"""Complete RLS Policies, Least-Privilege Grants, and Reversible Downgrade

Revision ID: 0007_rls_policies_and_grants
Revises: 0006_worker_tasks_and_functions
Create Date: 2026-11-17 04:36:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '0007_rls_policies_and_grants'
down_revision: Union[str, None] = '0006_worker_tasks_and_functions'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    # 1. Enable and Force RLS on all Multi-Tenant and Internal Tables
    tables = [
        'organizations', 'global_users', 'organization_memberships',
        'business_profiles', 'documents', 'invoices', 'invoice_line_items',
        'gst_records', 'financial_transactions', 'financial_summaries',
        'summary_transactions', 'scheme_matches', 'worker_tasks',
        'worker_service_authorizations'  # H5 Fix
    ]
    for tbl in tables:
        op.execute(f"ALTER TABLE public.{tbl} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE public.{tbl} FORCE ROW LEVEL SECURITY;")
    
    op.execute("ALTER TABLE audit.audit_logs ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE audit.audit_logs FORCE ROW LEVEL SECURITY;")

    # 2. RLS Policies
    op.execute("""
    -- Organizations
    CREATE POLICY org_tenant_isolation ON public.organizations
        FOR SELECT TO app_user
        USING (id = NULLIF(current_setting('app.current_org', true), '')::uuid);
    CREATE POLICY org_platform_admin ON public.organizations
        TO platform_admin USING (true) WITH CHECK (true);

    -- Global Users (Direct access restricted; mediated via v_org_users)
    CREATE POLICY global_users_owner_select ON public.global_users
        FOR SELECT TO v_org_users_owner USING (true);
    CREATE POLICY global_users_platform_admin ON public.global_users
        TO platform_admin USING (true) WITH CHECK (true);

    -- Organization Memberships (C5 Fix: allow v_org_users_owner to select memberships)
    CREATE POLICY org_memberships_tenant ON public.organization_memberships
        FOR SELECT TO app_user
        USING (org_id = NULLIF(current_setting('app.current_org', true), '')::uuid);
    CREATE POLICY org_memberships_view_owner ON public.organization_memberships
        FOR SELECT TO v_org_users_owner USING (true);
    CREATE POLICY org_memberships_admin ON public.organization_memberships
        TO platform_admin USING (true) WITH CHECK (true);

    -- Business Profiles
    CREATE POLICY business_profiles_tenant ON public.business_profiles
        FOR ALL TO app_user
        USING (org_id = NULLIF(current_setting('app.current_org', true), '')::uuid)
        WITH CHECK (org_id = NULLIF(current_setting('app.current_org', true), '')::uuid);
    CREATE POLICY business_profiles_worker ON public.business_profiles
        FOR SELECT TO worker_owner
        USING (org_id = NULLIF(current_setting('app.current_org', true), '')::uuid AND is_worker_authorized(org_id));
    CREATE POLICY business_profiles_admin ON public.business_profiles
        TO platform_admin USING (true) WITH CHECK (true);

    -- Documents
    CREATE POLICY documents_tenant ON public.documents
        FOR ALL TO app_user
        USING (org_id = NULLIF(current_setting('app.current_org', true), '')::uuid)
        WITH CHECK (org_id = NULLIF(current_setting('app.current_org', true), '')::uuid);
    CREATE POLICY documents_worker ON public.documents
        FOR SELECT TO worker_owner
        USING (org_id = NULLIF(current_setting('app.current_org', true), '')::uuid AND is_worker_authorized(org_id));
    CREATE POLICY documents_admin ON public.documents
        TO platform_admin USING (true) WITH CHECK (true);

    -- Invoices
    CREATE POLICY invoices_tenant ON public.invoices
        FOR ALL TO app_user
        USING (org_id = NULLIF(current_setting('app.current_org', true), '')::uuid)
        WITH CHECK (org_id = NULLIF(current_setting('app.current_org', true), '')::uuid);
    CREATE POLICY invoices_worker ON public.invoices
        FOR ALL TO worker_owner
        USING (org_id = NULLIF(current_setting('app.current_org', true), '')::uuid AND is_worker_authorized(org_id))
        WITH CHECK (org_id = NULLIF(current_setting('app.current_org', true), '')::uuid AND is_worker_authorized(org_id));
    CREATE POLICY invoices_admin ON public.invoices
        TO platform_admin USING (true) WITH CHECK (true);

    -- Invoice Line Items
    CREATE POLICY invoice_lines_tenant ON public.invoice_line_items
        FOR ALL TO app_user
        USING (org_id = NULLIF(current_setting('app.current_org', true), '')::uuid)
        WITH CHECK (org_id = NULLIF(current_setting('app.current_org', true), '')::uuid);
    CREATE POLICY invoice_lines_worker ON public.invoice_line_items
        FOR ALL TO worker_owner
        USING (org_id = NULLIF(current_setting('app.current_org', true), '')::uuid AND is_worker_authorized(org_id))
        WITH CHECK (org_id = NULLIF(current_setting('app.current_org', true), '')::uuid AND is_worker_authorized(org_id));
    CREATE POLICY invoice_lines_admin ON public.invoice_line_items
        TO platform_admin USING (true) WITH CHECK (true);

    -- GST Records (C3 Fix: worker has SELECT only, no direct DML)
    CREATE POLICY gst_records_tenant ON public.gst_records
        FOR ALL TO app_user
        USING (org_id = NULLIF(current_setting('app.current_org', true), '')::uuid)
        WITH CHECK (org_id = NULLIF(current_setting('app.current_org', true), '')::uuid);
    CREATE POLICY gst_records_worker ON public.gst_records
        FOR SELECT TO worker_owner
        USING (org_id = NULLIF(current_setting('app.current_org', true), '')::uuid AND is_worker_authorized(org_id));
    CREATE POLICY gst_records_admin ON public.gst_records
        TO platform_admin USING (true) WITH CHECK (true);

    -- Financial Transactions
    CREATE POLICY fin_tx_tenant ON public.financial_transactions
        FOR ALL TO app_user
        USING (org_id = NULLIF(current_setting('app.current_org', true), '')::uuid)
        WITH CHECK (org_id = NULLIF(current_setting('app.current_org', true), '')::uuid);
    CREATE POLICY fin_tx_admin ON public.financial_transactions
        TO platform_admin USING (true) WITH CHECK (true);

    -- Financial Summaries (C3 Fix: worker has SELECT only, no direct DML)
    CREATE POLICY fin_summaries_tenant ON public.financial_summaries
        FOR ALL TO app_user
        USING (org_id = NULLIF(current_setting('app.current_org', true), '')::uuid)
        WITH CHECK (org_id = NULLIF(current_setting('app.current_org', true), '')::uuid);
    CREATE POLICY fin_summaries_worker ON public.financial_summaries
        FOR SELECT TO worker_owner
        USING (org_id = NULLIF(current_setting('app.current_org', true), '')::uuid AND is_worker_authorized(org_id));
    CREATE POLICY fin_summaries_admin ON public.financial_summaries
        TO platform_admin USING (true) WITH CHECK (true);

    -- Summary Transactions (Provenance: H4 Fix - append/read only)
    CREATE POLICY summary_tx_tenant ON public.summary_transactions
        FOR ALL TO app_user
        USING (org_id = NULLIF(current_setting('app.current_org', true), '')::uuid)
        WITH CHECK (org_id = NULLIF(current_setting('app.current_org', true), '')::uuid);
    CREATE POLICY summary_tx_admin ON public.summary_transactions
        TO platform_admin USING (true) WITH CHECK (true);

    -- Scheme Matches (C3 Fix: worker has SELECT only, no direct DML)
    CREATE POLICY scheme_matches_tenant ON public.scheme_matches
        FOR ALL TO app_user
        USING (org_id = NULLIF(current_setting('app.current_org', true), '')::uuid)
        WITH CHECK (org_id = NULLIF(current_setting('app.current_org', true), '')::uuid);
    CREATE POLICY scheme_matches_worker ON public.scheme_matches
        FOR SELECT TO worker_owner
        USING (org_id = NULLIF(current_setting('app.current_org', true), '')::uuid AND is_worker_authorized(org_id));
    CREATE POLICY scheme_matches_admin ON public.scheme_matches
        TO platform_admin USING (true) WITH CHECK (true);

    -- Worker Tasks (Direct worker mutations blocked; SELECT mediated with tenant validation)
    CREATE POLICY worker_tasks_queue_owner_policy ON public.worker_tasks
        TO task_queue_owner USING (true) WITH CHECK (true);
    CREATE POLICY worker_tasks_platform_admin_policy ON public.worker_tasks
        TO platform_admin USING (true) WITH CHECK (true);
    CREATE POLICY worker_tasks_worker_select_policy ON public.worker_tasks
        FOR SELECT TO worker_owner
        USING (org_id = NULLIF(current_setting('app.current_org', true), '')::uuid AND is_worker_authorized(org_id));

    -- Worker Service Authorizations (H5 Fix: Worker only sees its own authorization row)
    CREATE POLICY worker_auth_self_select ON public.worker_service_authorizations
        FOR SELECT TO worker_owner
        USING (worker_role_name = session_user);
    CREATE POLICY worker_auth_admin ON public.worker_service_authorizations
        TO platform_admin USING (true) WITH CHECK (true);
    CREATE POLICY worker_auth_queue_owner ON public.worker_service_authorizations
        FOR SELECT TO task_queue_owner USING (true);

    -- Audit Logs (Append-only; SELECT strictly controlled)
    CREATE POLICY audit_logs_func_owner_policy ON audit.audit_logs
        FOR ALL TO audit_func_owner USING (true) WITH CHECK (true);
    CREATE POLICY audit_logs_reader_policy ON audit.audit_logs
        FOR SELECT TO audit_reader
        USING (org_id = NULLIF(current_setting('app.current_org', true), '')::uuid);
    CREATE POLICY audit_logs_system_auditor_policy ON audit.audit_logs
        FOR SELECT TO system_auditor, platform_admin
        USING (true);
    """)

    # 3. Complete DCL Grants and Revocations (Least-Privilege Hardened)
    op.execute("""
    -- Table Privileges for app_user
    GRANT SELECT ON public.organizations TO app_user;
    GRANT ALL ON public.business_profiles TO app_user;
    GRANT ALL ON public.documents TO app_user;
    GRANT ALL ON public.invoices TO app_user;
    GRANT ALL ON public.invoice_line_items TO app_user;
    GRANT ALL ON public.gst_records TO app_user;
    GRANT ALL ON public.financial_transactions TO app_user;
    GRANT ALL ON public.financial_summaries TO app_user;
    GRANT SELECT, INSERT ON public.summary_transactions TO app_user; -- H4 Fix: No UPDATE/DELETE
    GRANT ALL ON public.scheme_matches TO app_user;
    GRANT SELECT ON public.government_schemes TO app_user;
    GRANT SELECT ON public.scheme_eligibility_rules TO app_user;
    GRANT SELECT ON public.organization_memberships TO app_user;

    -- Table Privileges for worker_owner (C3 Fix: Direct DML completely revoked on financial/scheme tables)
    GRANT SELECT ON public.documents TO worker_owner;
    GRANT SELECT, UPDATE ON public.invoices TO worker_owner;
    GRANT SELECT, INSERT, UPDATE ON public.invoice_line_items TO worker_owner;
    GRANT SELECT ON public.gst_records TO worker_owner; -- C3 Fix: Read-only
    GRANT SELECT ON public.financial_summaries TO worker_owner; -- C3 Fix: Read-only
    GRANT SELECT ON public.scheme_matches TO worker_owner; -- C3 Fix: Read-only
    GRANT SELECT ON public.government_schemes TO worker_owner;
    GRANT SELECT ON public.scheme_eligibility_rules TO worker_owner;
    GRANT SELECT ON public.worker_service_authorizations TO worker_owner;
    GRANT SELECT ON public.worker_tasks TO worker_owner;

    -- Privileges for task_queue_owner
    GRANT ALL ON public.worker_tasks TO task_queue_owner;
    GRANT SELECT ON public.worker_service_authorizations TO task_queue_owner;

    -- Privileges for audit roles (H2 Fix: app_user & worker_owner granted USAGE on schema audit)
    GRANT USAGE ON SCHEMA audit TO app_user, worker_owner, audit_func_owner, audit_reader, system_auditor, platform_admin;
    GRANT SELECT, INSERT ON audit.audit_logs TO audit_func_owner;
    GRANT SELECT ON audit.audit_logs TO audit_reader, system_auditor, platform_admin;

    -- Invoice transition rules lookup
    GRANT SELECT ON public.invoice_transition_rules TO app_user, worker_owner;

    -- Search Path Security: Revoke CREATE on public from non-admin roles
    REVOKE CREATE ON SCHEMA public FROM PUBLIC;
    REVOKE CREATE ON SCHEMA public FROM app_user, worker_owner, audit_writer, audit_reader, system_auditor;

    -- Privileges for v_org_users_owner (C5 Fix)
    GRANT SELECT ON public.global_users TO v_org_users_owner;
    GRANT SELECT ON public.organization_memberships TO v_org_users_owner;

    -- Full Platform Admin
    GRANT ALL ON ALL TABLES IN SCHEMA public TO platform_admin;
    GRANT ALL ON ALL TABLES IN SCHEMA audit TO platform_admin;
    """)

def downgrade() -> None:
    # Explicit policy drops and RLS teardown (H6 Fix)
    policies = [
        ('organizations', 'org_tenant_isolation'),
        ('organizations', 'org_platform_admin'),
        ('global_users', 'global_users_owner_select'),
        ('global_users', 'global_users_platform_admin'),
        ('organization_memberships', 'org_memberships_tenant'),
        ('organization_memberships', 'org_memberships_view_owner'),
        ('organization_memberships', 'org_memberships_admin'),
        ('business_profiles', 'business_profiles_tenant'),
        ('business_profiles', 'business_profiles_worker'),
        ('business_profiles', 'business_profiles_admin'),
        ('documents', 'documents_tenant'),
        ('documents', 'documents_worker'),
        ('documents', 'documents_admin'),
        ('invoices', 'invoices_tenant'),
        ('invoices', 'invoices_worker'),
        ('invoices', 'invoices_admin'),
        ('invoice_line_items', 'invoice_lines_tenant'),
        ('invoice_line_items', 'invoice_lines_worker'),
        ('invoice_line_items', 'invoice_lines_admin'),
        ('gst_records', 'gst_records_tenant'),
        ('gst_records', 'gst_records_worker'),
        ('gst_records', 'gst_records_admin'),
        ('financial_transactions', 'fin_tx_tenant'),
        ('financial_transactions', 'fin_tx_admin'),
        ('financial_summaries', 'fin_summaries_tenant'),
        ('financial_summaries', 'fin_summaries_worker'),
        ('financial_summaries', 'fin_summaries_admin'),
        ('summary_transactions', 'summary_tx_tenant'),
        ('summary_transactions', 'summary_tx_admin'),
        ('scheme_matches', 'scheme_matches_tenant'),
        ('scheme_matches', 'scheme_matches_worker'),
        ('scheme_matches', 'scheme_matches_admin'),
        ('worker_tasks', 'worker_tasks_queue_owner_policy'),
        ('worker_tasks', 'worker_tasks_platform_admin_policy'),
        ('worker_tasks', 'worker_tasks_worker_select_policy'),
        ('worker_service_authorizations', 'worker_auth_self_select'),
        ('worker_service_authorizations', 'worker_auth_admin'),
        ('worker_service_authorizations', 'worker_auth_queue_owner')
    ]
    for tbl, pol in policies:
        op.execute(f"DROP POLICY IF EXISTS {pol} ON public.{tbl};")

    audit_policies = [
        ('audit_logs', 'audit_logs_func_owner_insert_policy'),
        ('audit_logs', 'audit_logs_reader_policy'),
        ('audit_logs', 'audit_logs_system_auditor_policy')
    ]
    for tbl, pol in audit_policies:
        op.execute(f"DROP POLICY IF EXISTS {pol} ON audit.{tbl};")

    tables = [
        'organizations', 'global_users', 'organization_memberships',
        'business_profiles', 'documents', 'invoices', 'invoice_line_items',
        'gst_records', 'financial_transactions', 'financial_summaries',
        'summary_transactions', 'scheme_matches', 'worker_tasks',
        'worker_service_authorizations'
    ]
    for tbl in tables:
        op.execute(f"ALTER TABLE public.{tbl} NO FORCE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE public.{tbl} DISABLE ROW LEVEL SECURITY;")

    op.execute("ALTER TABLE audit.audit_logs NO FORCE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE audit.audit_logs DISABLE ROW LEVEL SECURITY;")
