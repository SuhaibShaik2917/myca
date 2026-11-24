"""GSTR-3B Database State Machine Enforcement, Rejected Timestamp, and Snapshot Backfill

Revision ID: 0034_gstr3b_state_machine
Revises: 0033_gstr3b_history_and_trigger
Create Date: 2026-11-25 01:40:00.000000
"""
from typing import Sequence, Union
from alembic import op

revision: str = '0034_gstr3b_state_machine'
down_revision: Union[str, None] = '0033_gstr3b_history_and_trigger'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Enforce complete GSTR-3B database state machine & checker authorization
    op.execute("""
    CREATE OR REPLACE FUNCTION public.enforce_gstr3b_filed_immutability()
    RETURNS TRIGGER AS $$
    DECLARE
        v_has_valid_checker boolean;
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
            -- 1. Strictly immutable once FILED
            IF OLD.filing_status = 'FILED' THEN
                RAISE EXCEPTION 'Filed GSTR-3B return % is strictly immutable. Modifications prohibited (42501).', OLD.id
                    USING ERRCODE = '42501';
            END IF;

            -- 2. Enforce strict state-transition graph
            IF NEW.filing_status IS DISTINCT FROM OLD.filing_status THEN
                IF NOT (
                    (OLD.filing_status = 'DRAFT' AND NEW.filing_status = 'SUBMITTED_FOR_REVIEW') OR
                    (OLD.filing_status = 'REJECTED_BY_CHECKER' AND NEW.filing_status IN ('DRAFT', 'SUBMITTED_FOR_REVIEW')) OR
                    (OLD.filing_status = 'SUBMITTED_FOR_REVIEW' AND NEW.filing_status IN ('APPROVED_BY_CHECKER', 'REJECTED_BY_CHECKER')) OR
                    (OLD.filing_status = 'APPROVED_BY_CHECKER' AND NEW.filing_status IN ('FILED', 'REJECTED_BY_CHECKER'))
                ) THEN
                    RAISE EXCEPTION 'Invalid GSTR-3B state transition from % to % for return % (42501).',
                        OLD.filing_status, NEW.filing_status, OLD.id
                        USING ERRCODE = '42501';
                END IF;
            END IF;

            -- 3. Financial snapshot fields may ONLY change during explicitly permitted preparation into DRAFT
            IF (
                NEW.table_3_1_data IS DISTINCT FROM OLD.table_3_1_data OR
                NEW.table_4_data IS DISTINCT FROM OLD.table_4_data OR
                NEW.offset_data IS DISTINCT FROM OLD.offset_data
            ) THEN
                IF NOT (OLD.filing_status IN ('DRAFT', 'REJECTED_BY_CHECKER') AND NEW.filing_status = 'DRAFT') THEN
                    RAISE EXCEPTION 'GSTR-3B return % financial snapshot is immutable during transition % -> %. Modifications prohibited (42501).',
                        OLD.id, OLD.filing_status, NEW.filing_status
                        USING ERRCODE = '42501';
                END IF;
            END IF;

            -- 4. Approval / rejection transitions require a distinct, active, same-org Checker with admin or owner role
            IF NEW.filing_status IN ('APPROVED_BY_CHECKER', 'REJECTED_BY_CHECKER')
               AND NEW.filing_status IS DISTINCT FROM OLD.filing_status THEN
                IF NEW.checker_id IS NULL THEN
                    RAISE EXCEPTION 'Checker identity is required for GSTR-3B review transition to % (42501).', NEW.filing_status
                        USING ERRCODE = '42501';
                END IF;
                IF NEW.maker_id IS NOT NULL AND NEW.maker_id = NEW.checker_id THEN
                    RAISE EXCEPTION 'Segregation of duties violation: Maker cannot act as Checker for return % (42501).', OLD.id
                        USING ERRCODE = '42501';
                END IF;

                SELECT EXISTS (
                    SELECT 1
                    FROM public.organization_memberships
                    WHERE org_id = NEW.org_id
                      AND user_id = NEW.checker_id
                      AND status = 'active'
                      AND role IN ('admin', 'owner')
                ) INTO v_has_valid_checker;

                IF NOT v_has_valid_checker THEN
                    RAISE EXCEPTION 'Unauthorized reviewer: Checker % is not an active admin/owner of organization % (42501).',
                        NEW.checker_id, NEW.org_id
                        USING ERRCODE = '42501';
                END IF;
            END IF;

            RETURN NEW;
        END IF;
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """)

    # 2. Add rejected_at to public.gstr3b_revision_history
    op.execute("""
    ALTER TABLE public.gstr3b_revision_history
    ADD COLUMN IF NOT EXISTS rejected_at timestamptz NULL;
    """)

    # 3. Idempotent backfill of existing non-null previous_snapshot records from public.gstr3b_filings
    op.execute("""
    INSERT INTO public.gstr3b_revision_history (
        filing_id, org_id, revision_number,
        table_3_1_data, table_4_data, offset_data,
        maker_id, checker_id, maker_remarks, checker_remarks,
        rejected_at, archived_at
    )
    SELECT
        f.id,
        f.org_id,
        COALESCE((f.previous_snapshot->>'revision')::integer, GREATEST(COALESCE(f.revision_number, 1) - 1, 1)),
        COALESCE(f.previous_snapshot->'table_3_1', '{}'::jsonb),
        COALESCE(f.previous_snapshot->'table_4', '{}'::jsonb),
        COALESCE(f.previous_snapshot->'offset', '{}'::jsonb),
        COALESCE(NULLIF(f.previous_snapshot->>'maker_id', '')::uuid, f.maker_id),
        NULLIF(f.previous_snapshot->>'checker_id', '')::uuid,
        f.previous_snapshot->>'maker_remarks',
        f.previous_snapshot->>'checker_remarks',
        NULLIF(f.previous_snapshot->>'rejected_at', '')::timestamptz,
        COALESCE(f.updated_at, clock_timestamp())
    FROM public.gstr3b_filings f
    WHERE f.previous_snapshot IS NOT NULL
    ON CONFLICT (filing_id, revision_number) DO NOTHING;
    """)


def downgrade() -> None:
    op.execute("""
    ALTER TABLE public.gstr3b_revision_history
    DROP COLUMN IF EXISTS rejected_at;
    """)

    # Restore 0033 trigger function
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
