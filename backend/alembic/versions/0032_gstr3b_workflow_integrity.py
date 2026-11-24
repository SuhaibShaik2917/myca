"""GSTR-3B Workflow Integrity, Revision Tracking, and GST Records Immutability

Revision ID: 0032_gstr3b_workflow_integrity
Revises: 0031_gstr3b_filed_immutability
Create Date: 2026-11-25 00:00:00.000000
"""
from typing import Sequence, Union
from alembic import op

revision: str = '0032_gstr3b_workflow_integrity'
down_revision: Union[str, None] = '0031_gstr3b_filed_immutability'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add revision_number and previous_snapshot columns to public.gstr3b_filings
    op.execute("""
    ALTER TABLE public.gstr3b_filings
    ADD COLUMN IF NOT EXISTS revision_number integer NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS previous_snapshot jsonb NULL;
    """)

    # 2. Upgrade trigger function on public.gstr3b_filings
    op.execute("""
    CREATE OR REPLACE FUNCTION public.enforce_gstr3b_filed_immutability()
    RETURNS TRIGGER AS $$
    BEGIN
        IF TG_OP = 'DELETE' THEN
            IF OLD.filing_status = 'FILED' THEN
                RAISE EXCEPTION 'Filed GSTR-3B return % is strictly immutable. Deletion prohibited (42501).', OLD.id
                    USING ERRCODE = '42501';
            ELSIF OLD.filing_status IN ('SUBMITTED_FOR_REVIEW', 'APPROVED_BY_CHECKER') THEN
                RAISE EXCEPTION 'GSTR-3B return % in status % cannot be deleted. Deletion prohibited (42501).', OLD.id, OLD.filing_status
                    USING ERRCODE = '42501';
            END IF;
            RETURN OLD;
        ELSIF TG_OP = 'UPDATE' THEN
            -- Strictly immutable once FILED
            IF OLD.filing_status = 'FILED' THEN
                RAISE EXCEPTION 'Filed GSTR-3B return % is strictly immutable. Modifications prohibited (42501).', OLD.id
                    USING ERRCODE = '42501';
            END IF;

            -- If APPROVED_BY_CHECKER, financial data cannot be mutated unless formally transitioning to REJECTED_BY_CHECKER
            IF OLD.filing_status = 'APPROVED_BY_CHECKER' THEN
                IF NEW.filing_status != 'REJECTED_BY_CHECKER' AND (
                    NEW.table_3_1_data IS DISTINCT FROM OLD.table_3_1_data OR
                    NEW.table_4_data IS DISTINCT FROM OLD.table_4_data OR
                    NEW.offset_data IS DISTINCT FROM OLD.offset_data
                ) THEN
                    RAISE EXCEPTION 'Approved GSTR-3B return % financial data is immutable. Modifications prohibited (42501).', OLD.id
                        USING ERRCODE = '42501';
                END IF;
            END IF;

            -- If SUBMITTED_FOR_REVIEW, financial data cannot be mutated directly while under review
            IF OLD.filing_status = 'SUBMITTED_FOR_REVIEW' THEN
                IF NEW.filing_status NOT IN ('APPROVED_BY_CHECKER', 'REJECTED_BY_CHECKER') AND (
                    NEW.table_3_1_data IS DISTINCT FROM OLD.table_3_1_data OR
                    NEW.table_4_data IS DISTINCT FROM OLD.table_4_data OR
                    NEW.offset_data IS DISTINCT FROM OLD.offset_data
                ) THEN
                    RAISE EXCEPTION 'Submitted GSTR-3B return % is under statutory review. Financial mutations prohibited (42501).', OLD.id
                        USING ERRCODE = '42501';
                END IF;
            END IF;

            RETURN NEW;
        END IF;
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """)

    # 3. Add immutability trigger for public.gst_records
    op.execute("""
    CREATE OR REPLACE FUNCTION public.enforce_gst_records_filed_immutability()
    RETURNS TRIGGER AS $$
    BEGIN
        IF TG_OP = 'DELETE' THEN
            IF OLD.return_type = 'GSTR-3B' AND OLD.filing_status::text = 'filed' THEN
                RAISE EXCEPTION 'Filed GST compliance record % is strictly immutable. Deletion prohibited (42501).', OLD.id
                    USING ERRCODE = '42501';
            END IF;
            RETURN OLD;
        ELSIF TG_OP = 'UPDATE' THEN
            IF OLD.return_type = 'GSTR-3B' AND OLD.filing_status::text = 'filed' THEN
                RAISE EXCEPTION 'Filed GST compliance record % is strictly immutable. Modifications prohibited (42501).', OLD.id
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """)

    op.execute("""
    DROP TRIGGER IF EXISTS trg_prevent_gst_records_filed_mutations ON public.gst_records;
    CREATE TRIGGER trg_prevent_gst_records_filed_mutations
    BEFORE UPDATE OR DELETE ON public.gst_records
    FOR EACH ROW
    EXECUTE FUNCTION public.enforce_gst_records_filed_immutability();
    """)


def downgrade() -> None:
    op.execute("""
    DROP TRIGGER IF EXISTS trg_prevent_gst_records_filed_mutations ON public.gst_records;
    DROP FUNCTION IF EXISTS public.enforce_gst_records_filed_immutability();
    """)

    op.execute("""
    CREATE OR REPLACE FUNCTION public.enforce_gstr3b_filed_immutability()
    RETURNS TRIGGER AS $$
    BEGIN
        IF TG_OP = 'DELETE' THEN
            IF OLD.filing_status = 'FILED' THEN
                RAISE EXCEPTION 'Filed GSTR-3B return % is strictly immutable. Deletion prohibited (42501).', OLD.id
                    USING ERRCODE = '42501';
            END IF;
            RETURN OLD;
        ELSIF TG_OP = 'UPDATE' THEN
            IF OLD.filing_status = 'FILED' THEN
                RAISE EXCEPTION 'Filed GSTR-3B return % is strictly immutable. Modifications prohibited (42501).', OLD.id
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """)

    op.execute("""
    ALTER TABLE public.gstr3b_filings
    DROP COLUMN IF EXISTS previous_snapshot,
    DROP COLUMN IF EXISTS revision_number;
    """)
