"""Complete RLS Policies and Privilege Grants Across All Roles

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
    # 1. Enable and Force RLS on all Tenant Tables
    tables = [
        'organizations', 'global_users', 'organization_memberships',
        'business_profiles', 'documents', 'invoices', 'invoice_line_items',
        'gst_records', 'financial_transactions', 'financial_summaries',
        'summary_transactions', 'scheme_matches', 'worker_tasks'
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

    -- Global Users (No direct SELECT for app_user/worker_owner; mediated via v_org_users)
    CREATE POLICY global_users_owner_select ON public.global_users
        FOR SELECT TO v_org_users_owner USING (true);
    CREATE POLICY global_users_platform_admin ON public.global_users
        TO platform_admin USING (true) WITH CHECK (true);

    -- Organization Memberships
    CREATE POLICY org_memberships_tenant ON public.organization_memberships
        FOR SELECT TO app_user
        USING (org_id = NULLIF(current_setting('app.current_org', true), '')::uuid);
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

    -- GST Records
    CREATE POLICY gst_records_tenant ON public.gst_records
        FOR ALL TO app_user
        USING (org_id = NULLIF(current_setting('app.current_org', true), '')::uuid)
        WITH CHECK (org_id = NULLIF(current_setting('app.current_org', true), '')::uuid);
    CREATE POLICY gst_records_worker ON public.gst_records
        FOR ALL TO worker_owner
        USING (org_id = NULLIF(current_setting('app.current_org', true), '')::uuid AND is_worker_authorized(org_id))
        WITH CHECK (org_id = NULLIF(current_setting('app.current_org', true), '')::uuid AND is_worker_authorized(org_id));
    CREATE POLICY gst_records_admin ON public.gst_records
        TO platform_admin USING (true) WITH CHECK (true);

    -- Financial Transactions
    CREATE POLICY fin_tx_tenant ON public.financial_transactions
        FOR ALL TO app_user
        USING (org_id = NULLIF(current_setting('app.current_org', true), '')::uuid)
        WITH CHECK (org_id = NULLIF(current_setting('app.current_org', true), '')::uuid);
    CREATE POLICY fin_tx_admin ON public.financial_transactions
        TO platform_admin USING (true) WITH CHECK (true);

    -- Financial Summaries
    CREATE POLICY fin_summaries_tenant ON public.financial_summaries
        FOR ALL TO app_user
        USING (org_id = NULLIF(current_setting('app.current_org', true), '')::uuid)
        WITH CHECK (org_id = NULLIF(current_setting('app.current_org', true), '')::uuid);
    CREATE POLICY fin_summaries_worker ON public.financial_summaries
        FOR ALL TO worker_owner
        USING (org_id = NULLIF(current_setting('app.current_org', true), '')::uuid AND is_worker_authorized(org_id))
        WITH CHECK (org_id = NULLIF(current_setting('app.current_org', true), '')::uuid AND is_worker_authorized(org_id));
    CREATE POLICY fin_summaries_admin ON public.financial_summaries
        TO platform_admin USING (true) WITH CHECK (true);

    -- Summary Transactions (Provenance)
    CREATE POLICY summary_tx_tenant ON public.summary_transactions
        FOR ALL TO app_user
        USING (org_id = NULLIF(current_setting('app.current_org', true), '')::uuid)
        WITH CHECK (org_id = NULLIF(current_setting('app.current_org', true), '')::uuid);
    CREATE POLICY summary_tx_admin ON public.summary_transactions
        TO platform_admin USING (true) WITH CHECK (true);

    -- Scheme Matches
    CREATE POLICY scheme_matches_tenant ON public.scheme_matches
        FOR ALL TO app_user
        USING (org_id = NULLIF(current_setting('app.current_org', true), '')::uuid)
        WITH CHECK (org_id = NULLIF(current_setting('app.current_org', true), '')::uuid);
    CREATE POLICY scheme_matches_worker ON public.scheme_matches
        FOR ALL TO worker_owner
        USING (org_id = NULLIF(current_setting('app.current_org', true), '')::uuid AND is_worker_authorized(org_id))
        WITH CHECK (org_id = NULLIF(current_setting('app.current_org', true), '')::uuid AND is_worker_authorized(org_id));
    CREATE POLICY scheme_matches_admin ON public.scheme_matches
        TO platform_admin USING (true) WITH CHECK (true);

    -- Worker Tasks (Direct mutations blocked for workers; SELECT only with tenant validation)
    CREATE POLICY worker_tasks_queue_owner_policy ON public.worker_tasks
        TO task_queue_owner USING (true) WITH CHECK (true);
    CREATE POLICY worker_tasks_platform_admin_policy ON public.worker_tasks
        TO platform_admin USING (true) WITH CHECK (true);
    CREATE POLICY worker_tasks_worker_select_policy ON public.worker_tasks
        FOR SELECT TO worker_owner
        USING (org_id = NULLIF(current_setting('app.current_org', true), '')::uuid AND is_worker_authorized(org_id));

    -- Audit Logs (Append-only; SELECT strictly controlled)
    CREATE POLICY audit_logs_func_owner_insert_policy ON audit.audit_logs
        FOR INSERT TO audit_func_owner WITH CHECK (true);
    CREATE POLICY audit_logs_reader_policy ON audit.audit_logs
        FOR SELECT TO audit_reader
        USING (org_id = NULLIF(current_setting('app.current_org', true), '')::uuid);
    CREATE POLICY audit_logs_system_auditor_policy ON audit.audit_logs
        FOR SELECT TO system_auditor, platform_admin
        USING (true);
    """)

    # 3. Complete DCL Grants and Revocations
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
    GRANT ALL ON public.summary_transactions TO app_user;
    GRANT ALL ON public.scheme_matches TO app_user;
    GRANT SELECT ON public.government_schemes TO app_user;
    GRANT SELECT ON public.scheme_eligibility_rules TO app_user;
    GRANT SELECT ON public.organization_memberships TO app_user;

    -- Table Privileges for worker_owner
    GRANT SELECT ON public.documents TO worker_owner;
    GRANT SELECT, UPDATE ON public.invoices TO worker_owner;
    GRANT SELECT, INSERT, UPDATE ON public.invoice_line_items TO worker_owner;
    GRANT ALL ON public.gst_records TO worker_owner;
    GRANT ALL ON public.financial_summaries TO worker_owner;
    GRANT ALL ON public.scheme_matches TO worker_owner;
    GRANT SELECT ON public.government_schemes TO worker_owner;
    GRANT SELECT ON public.scheme_eligibility_rules TO worker_owner;
    GRANT SELECT ON public.worker_service_authorizations TO worker_owner;
    GRANT SELECT ON public.worker_tasks TO worker_owner;

    -- Privileges for task_queue_owner
    GRANT ALL ON public.worker_tasks TO task_queue_owner;
    GRANT SELECT ON public.worker_service_authorizations TO task_queue_owner;

    -- Privileges for audit roles
    GRANT USAGE ON SCHEMA audit TO audit_func_owner, audit_reader, system_auditor, platform_admin;
    GRANT INSERT ON audit.audit_logs TO audit_func_owner;
    GRANT SELECT ON audit.audit_logs TO audit_reader, system_auditor, platform_admin;

    -- Privileges for v_org_users_owner
    GRANT SELECT ON public.global_users TO v_org_users_owner;
    GRANT SELECT ON public.organization_memberships TO v_org_users_owner;

    -- Full Platform Admin
    GRANT ALL ON ALL TABLES IN SCHEMA public TO platform_admin;
    GRANT ALL ON ALL TABLES IN SCHEMA audit TO platform_admin;
    """)

def downgrade() -> None:
    pass
