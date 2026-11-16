"""Business Profiles and Financial Provenance Tables

Revision ID: 0003_financial_tables
Revises: 0002_core_tables
Create Date: 2026-11-17 04:32:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '0003_financial_tables'
down_revision: Union[str, None] = '0002_core_tables'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    # 1. Business Profiles
    op.execute("""
    CREATE TABLE IF NOT EXISTS public.business_profiles (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id uuid NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
        gstin text UNIQUE CHECK (gstin ~ '^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$'),
        udyam_number text UNIQUE CHECK (udyam_number IS NULL OR udyam_number ~ '^UDYAM-[A-Z]{2}-[0-9]{2}-[0-9]{7}$'),
        business_type text NOT NULL,
        industry_sector text NOT NULL,
        turnover_bracket text,
        annual_turnover numeric(15, 2) DEFAULT 0.00,
        credit_score int CHECK (credit_score IS NULL OR (credit_score >= 300 AND credit_score <= 900)),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        CONSTRAINT uq_business_profile_org UNIQUE (id, org_id)
    );
    CREATE INDEX IF NOT EXISTS idx_business_profiles_org ON public.business_profiles(org_id);
    """)

    # 2. Documents
    op.execute("""
    CREATE TABLE IF NOT EXISTS public.documents (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id uuid NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
        doc_type document_type NOT NULL,
        file_name text NOT NULL,
        file_path text NOT NULL,
        mime_type text NOT NULL,
        file_size_bytes bigint NOT NULL,
        sha256_checksum text NOT NULL,
        uploaded_by uuid REFERENCES public.global_users(id) ON DELETE SET NULL,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        CONSTRAINT uq_documents_id_org UNIQUE (id, org_id)
    );
    CREATE INDEX IF NOT EXISTS idx_documents_org ON public.documents(org_id);
    """)

    # 3. GST Records
    op.execute("""
    CREATE TABLE IF NOT EXISTS public.gst_records (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id uuid NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
        gstin text NOT NULL,
        return_type text NOT NULL,
        tax_period text NOT NULL,
        filing_status gst_filing_status NOT NULL DEFAULT 'pending',
        filing_date date,
        total_taxable_value numeric(15, 2) NOT NULL DEFAULT 0.00,
        total_tax_paid numeric(15, 2) NOT NULL DEFAULT 0.00,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        CONSTRAINT uq_gst_records_id_org UNIQUE (id, org_id)
    );
    CREATE INDEX IF NOT EXISTS idx_gst_records_org ON public.gst_records(org_id);
    """)

    # 4. Financial Transactions
    op.execute("""
    CREATE TABLE IF NOT EXISTS public.financial_transactions (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id uuid NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
        transaction_date date NOT NULL,
        amount numeric(15, 2) NOT NULL,
        transaction_type text NOT NULL CHECK (transaction_type IN ('credit', 'debit')),
        category text NOT NULL,
        counterparty text,
        reference_number text,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        CONSTRAINT uq_fin_tx_id_org UNIQUE (id, org_id)
    );
    CREATE INDEX IF NOT EXISTS idx_fin_tx_org_date ON public.financial_transactions(org_id, transaction_date);
    """)

    # 5. Financial Summaries
    op.execute("""
    CREATE TABLE IF NOT EXISTS public.financial_summaries (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id uuid NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
        summary_period text NOT NULL,
        total_inflow numeric(15, 2) NOT NULL DEFAULT 0.00,
        total_outflow numeric(15, 2) NOT NULL DEFAULT 0.00,
        net_cash_flow numeric(15, 2) NOT NULL DEFAULT 0.00,
        calculated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        CONSTRAINT uq_fin_summaries_id_org UNIQUE (id, org_id)
    );
    CREATE INDEX IF NOT EXISTS idx_fin_summaries_org ON public.financial_summaries(org_id);
    """)

    # 6. Financial Provenance Junction Table (Enforced Immutability: H4)
    op.execute("""
    CREATE TABLE IF NOT EXISTS public.summary_transactions (
        summary_id uuid NOT NULL,
        transaction_id uuid NOT NULL,
        org_id uuid NOT NULL,
        PRIMARY KEY (summary_id, transaction_id, org_id),
        FOREIGN KEY (summary_id, org_id) REFERENCES public.financial_summaries(id, org_id) ON DELETE RESTRICT,
        FOREIGN KEY (transaction_id, org_id) REFERENCES public.financial_transactions(id, org_id) ON DELETE RESTRICT
    );
    CREATE INDEX IF NOT EXISTS idx_summary_tx_org ON public.summary_transactions(org_id);

    -- Database trigger enforcing strict immutability (H4)
    CREATE OR REPLACE FUNCTION public.prevent_summary_transactions_mutation()
    RETURNS trigger
    LANGUAGE plpgsql
    AS $$
    BEGIN
        RAISE EXCEPTION 'summary_transactions is strictly immutable: modifications and deletions are prohibited (42501)';
    END;
    $$;

    DROP TRIGGER IF EXISTS trg_summary_transactions_immutable ON public.summary_transactions;
    CREATE TRIGGER trg_summary_transactions_immutable
        BEFORE UPDATE OR DELETE ON public.summary_transactions
        FOR EACH ROW
        EXECUTE FUNCTION public.prevent_summary_transactions_mutation();
    """)

def downgrade() -> None:
    # Explicit dependency-ordered drops (H7)
    op.execute('DROP TRIGGER IF EXISTS trg_summary_transactions_immutable ON public.summary_transactions;')
    op.execute('DROP FUNCTION IF EXISTS public.prevent_summary_transactions_mutation();')
    op.execute('DROP TABLE IF EXISTS public.summary_transactions;')
    op.execute('DROP TABLE IF EXISTS public.financial_summaries;')
    op.execute('DROP TABLE IF EXISTS public.financial_transactions;')
    op.execute('DROP TABLE IF EXISTS public.gst_records;')
    op.execute('DROP TABLE IF EXISTS public.documents;')
    op.execute('DROP TABLE IF EXISTS public.business_profiles;')
