"""GSTR-3B Strict Transition Immutability Trigger and Append-Only Revision History

Revision ID: 0033_gstr3b_trigger_and_revision_history
Revises: 0032_gstr3b_workflow_integrity
Create Date: 2026-11-25 01:30:00.000000
"""
from typing import Sequence, Union
from alembic import op

revision: str = '0033_gstr3b_history_and_trigger'
down_revision: Union[str, None] = '0032_gstr3b_workflow_integrity'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Harden enforce_gstr3b_filed_immutability() to close all transition-based financial mutation bypasses
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

            -- Financial snapshot fields must NEVER change while in SUBMITTED_FOR_REVIEW or APPROVED_BY_CHECKER,
            -- regardless of target workflow status (including APPROVED_BY_CHECKER or REJECTED_BY_CHECKER transitions).
            IF OLD.filing_status IN ('SUBMITTED_FOR_REVIEW', 'APPROVED_BY_CHECKER') THEN
                IF (
                    NEW.table_3_1_data IS DISTINCT FROM OLD.table_3_1_data OR
                    NEW.table_4_data IS DISTINCT FROM OLD.table_4_data OR
                    NEW.offset_data IS DISTINCT FROM OLD.offset_data
                ) THEN
                    RAISE EXCEPTION 'GSTR-3B return % financial snapshot is immutable in status %. Modifications prohibited (42501).', OLD.id, OLD.filing_status
                        USING ERRCODE = '42501';
                END IF;
            END IF;

            RETURN NEW;
        END IF;
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """)

    # 2. Create durable, append-only public.gstr3b_revision_history table
    op.execute("""
    CREATE TABLE IF NOT EXISTS public.gstr3b_revision_history (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        filing_id uuid NOT NULL REFERENCES public.gstr3b_filings(id) ON DELETE CASCADE,
        org_id uuid NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
        revision_number integer NOT NULL,
        table_3_1_data jsonb NOT NULL,
        table_4_data jsonb NOT NULL,
        offset_data jsonb NOT NULL,
        maker_id uuid NULL,
        checker_id uuid NULL,
        maker_remarks text NULL,
        checker_remarks text NULL,
        archived_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        UNIQUE (filing_id, revision_number)
    );

    CREATE INDEX IF NOT EXISTS ix_gstr3b_revision_history_filing_id
        ON public.gstr3b_revision_history (filing_id, revision_number);
    """)

    # 3. Row-Level Security & Least-Privilege Append-Only Grants
    op.execute("""
    ALTER TABLE public.gstr3b_revision_history ENABLE ROW LEVEL SECURITY;
    ALTER TABLE public.gstr3b_revision_history FORCE ROW LEVEL SECURITY;

    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_policies WHERE tablename = 'gstr3b_revision_history' AND policyname = 'gstr3b_revision_history_tenant'
        ) THEN
            CREATE POLICY gstr3b_revision_history_tenant ON public.gstr3b_revision_history
                FOR ALL
                TO app_service_user
                USING (org_id = (NULLIF(current_setting('app.current_org', true), ''))::uuid)
                WITH CHECK (org_id = (NULLIF(current_setting('app.current_org', true), ''))::uuid);
        END IF;

        IF NOT EXISTS (
            SELECT 1 FROM pg_policies WHERE tablename = 'gstr3b_revision_history' AND policyname = 'gstr3b_revision_history_admin'
        ) THEN
            CREATE POLICY gstr3b_revision_history_admin ON public.gstr3b_revision_history
                FOR ALL
                TO platform_admin
                USING (true)
                WITH CHECK (true);
        END IF;
    END $$;

    GRANT SELECT, INSERT ON public.gstr3b_revision_history TO app_service_user;
    """)

    # 4. Append-only immutability trigger on public.gstr3b_revision_history
    op.execute("""
    CREATE OR REPLACE FUNCTION public.enforce_gstr3b_revision_history_immutability()
    RETURNS TRIGGER AS $$
    BEGIN
        RAISE EXCEPTION 'GSTR-3B revision history record % is append-only and strictly immutable. % prohibited (42501).', OLD.id, TG_OP
            USING ERRCODE = '42501';
    END;
    $$ LANGUAGE plpgsql;

    DROP TRIGGER IF EXISTS trg_prevent_gstr3b_revision_history_mutations ON public.gstr3b_revision_history;
    CREATE TRIGGER trg_prevent_gstr3b_revision_history_mutations
    BEFORE UPDATE OR DELETE ON public.gstr3b_revision_history
    FOR EACH ROW
    EXECUTE FUNCTION public.enforce_gstr3b_revision_history_immutability();
    """)


def downgrade() -> None:
    op.execute("""
    DROP TRIGGER IF EXISTS trg_prevent_gstr3b_revision_history_mutations ON public.gstr3b_revision_history;
    DROP FUNCTION IF EXISTS public.enforce_gstr3b_revision_history_immutability();
    DROP TABLE IF EXISTS public.gstr3b_revision_history CASCADE;
    """)

    # Restore 0032 trigger function
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
            IF OLD.filing_status = 'FILED' THEN
                RAISE EXCEPTION 'Filed GSTR-3B return % is strictly immutable. Modifications prohibited (42501).', OLD.id
                    USING ERRCODE = '42501';
            END IF;

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
