"""Enforce Tenant FORCE ROW LEVEL SECURITY and Revoke Reference Catalogue Writes

Revision ID: 0021_enforce_tenant_force_rls
Revises: 0020_m4_m5_grants_and_policies
Create Date: 2026-11-23 00:10:00.000000
"""
from typing import Sequence, Union
from alembic import op

revision: str = '0021_enforce_tenant_force_rls'
down_revision: Union[str, None] = '0020_m4_m5_grants_and_policies'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TENANT_TABLES_TO_FORCE_RLS = [
    "gst_registrations",
    "itc_ledger_entries",
    "itc_assessments",
    "ims_document_actions",
    "purchase_documents",
    "purchase_line_items",
    "purchase_payments",
    "purchase_payment_allocations",
    "goods_receipt_lot_ledger",
    "gstr2b_imports",
    "gstr2b_snapshots",
    "gstr2b_source_documents",
    "gstr2b_unbooked_candidates",
    "human_resolution_events",
    "ims_action_history",
    "itc_reavailment_events",
    "itc_reversal_events",
    "receipt_remediation_audit",
    "reconciliation_matches",
    "reconciliation_runs",
    "reconciliation_variances"
]

def upgrade() -> None:
    # 1. Enforce least privilege on public reference catalogues: REVOKE any write permissions
    op.execute("""
    REVOKE INSERT, UPDATE, DELETE ON public.government_schemes FROM app_service_user;
    REVOKE INSERT, UPDATE, DELETE ON public.scheme_eligibility_rules FROM app_service_user;
    GRANT SELECT ON public.government_schemes TO app_service_user;
    GRANT SELECT ON public.scheme_eligibility_rules TO app_service_user;
    """)

    # 2. Enforce FORCE ROW LEVEL SECURITY on all remaining tenant-scoped tables
    for tbl in TENANT_TABLES_TO_FORCE_RLS:
        op.execute(f"ALTER TABLE public.{tbl} FORCE ROW LEVEL SECURITY;")


def downgrade() -> None:
    for tbl in TENANT_TABLES_TO_FORCE_RLS:
        op.execute(f"ALTER TABLE public.{tbl} NO FORCE ROW LEVEL SECURITY;")
