"""GSTR-3B Same-State Maker and Checker Attribution Immutability

Revision ID: 0036_gstr3b_maker_checker_lock
Revises: 0035_gstr3b_reviewer_binding
Create Date: 2026-11-25 02:00:00.000000
"""
from typing import Sequence, Union
from alembic import op

revision: str = '0036_gstr3b_maker_checker_lock'
down_revision: Union[str, None] = '0035_gstr3b_reviewer_binding'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
    CREATE OR REPLACE FUNCTION public.enforce_gstr3b_filed_immutability()
    RETURNS TRIGGER
    LANGUAGE plpgsql
    SECURITY DEFINER
    SET search_path = public, pg_catalog
    AS $$
    DECLARE
        v_has_valid_checker boolean;
        v_has_verified_token boolean;
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

            -- 4. Maker attribution (maker_id) may ONLY change during permitted preparation/submission from DRAFT or REJECTED_BY_CHECKER.
            --    Same-state updates on SUBMITTED_FOR_REVIEW, APPROVED_BY_CHECKER, or REJECTED_BY_CHECKER cannot rewrite maker_id.
            IF NEW.maker_id IS DISTINCT FROM OLD.maker_id THEN
                IF NOT (
                    OLD.filing_status IN ('DRAFT', 'REJECTED_BY_CHECKER')
                    AND NEW.filing_status IN ('DRAFT', 'SUBMITTED_FOR_REVIEW')
                ) THEN
                    RAISE EXCEPTION 'Maker identity cannot be modified during % -> % for return % (42501).',
                        OLD.filing_status, NEW.filing_status, OLD.id
                        USING ERRCODE = '42501';
                END IF;
            END IF;

            -- 5. Checker attribution (checker_id, checker_remarks) is strictly protected outside:
            --    a) Verified review transition (execute_gstr3b_review_transition), OR
            --    b) Authorized REJECTED_BY_CHECKER -> DRAFT revision clearing checker fields to NULL.
            IF (NEW.checker_id IS DISTINCT FROM OLD.checker_id OR NEW.checker_remarks IS DISTINCT FROM OLD.checker_remarks) THEN
                IF (
                    OLD.filing_status = 'REJECTED_BY_CHECKER'
                    AND NEW.filing_status = 'DRAFT'
                    AND NEW.checker_id IS NULL
                    AND NEW.checker_remarks IS NULL
                ) THEN
                    -- Permitted reset during formal revision after rejection
                    NULL;
                ELSIF (
                    NEW.filing_status IN ('APPROVED_BY_CHECKER', 'REJECTED_BY_CHECKER')
                    AND NEW.filing_status IS DISTINCT FROM OLD.filing_status
                ) THEN
                    -- Validated in section 6 below via gstr3b_verified_review_tokens
                    NULL;
                ELSE
                    RAISE EXCEPTION 'Checker attribution and remarks are immutable during % -> % for return % (42501).',
                        OLD.filing_status, NEW.filing_status, OLD.id
                        USING ERRCODE = '42501';
                END IF;
            END IF;

            -- 6. Review transitions (approval / rejection) require:
            --    a) Server-verified review token issued by execute_gstr3b_review_transition()
            --    b) Session actor matching NEW.checker_id
            --    c) Distinct, active, same-org admin/owner Checker
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

                IF NULLIF(current_setting('app.current_user', true), '') IS DISTINCT FROM NEW.checker_id::text THEN
                    RAISE EXCEPTION 'Review actor mismatch: session actor does not match checker % (42501).', NEW.checker_id
                        USING ERRCODE = '42501';
                END IF;

                SELECT EXISTS (
                    SELECT 1
                    FROM public.gstr3b_verified_review_tokens
                    WHERE tx_id = txid_current()
                      AND pid = pg_backend_pid()
                      AND filing_id = NEW.id
                      AND checker_id = NEW.checker_id
                      AND new_status = NEW.filing_status
                ) INTO v_has_verified_token;

                IF NOT v_has_verified_token THEN
                    RAISE EXCEPTION 'Unauthenticated GSTR-3B review transition: direct SQL approval/rejection without server-verified reviewer attestation is prohibited (42501).'
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
    $$;
    """)


def downgrade() -> None:
    # Restore 0035 trigger function
    op.execute("""
    CREATE OR REPLACE FUNCTION public.enforce_gstr3b_filed_immutability()
    RETURNS TRIGGER
    LANGUAGE plpgsql
    SECURITY DEFINER
    SET search_path = public, pg_catalog
    AS $$
    DECLARE
        v_has_valid_checker boolean;
        v_has_verified_token boolean;
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

            IF NEW.filing_status IN ('APPROVED_BY_CHECKER', 'REJECTED_BY_CHECKER')
               AND NEW.filing_status IS DISTINCT FROM OLD.filing_status THEN
                IF NEW.maker_id IS DISTINCT FROM OLD.maker_id THEN
                    RAISE EXCEPTION 'Maker identity cannot be modified during GSTR-3B review transition for return % (42501).', OLD.id
                        USING ERRCODE = '42501';
                END IF;

                IF NEW.checker_id IS NULL THEN
                    RAISE EXCEPTION 'Checker identity is required for GSTR-3B review transition to % (42501).', NEW.filing_status
                        USING ERRCODE = '42501';
                END IF;

                IF NEW.maker_id IS NOT NULL AND NEW.maker_id = NEW.checker_id THEN
                    RAISE EXCEPTION 'Segregation of duties violation: Maker cannot act as Checker for return % (42501).', OLD.id
                        USING ERRCODE = '42501';
                END IF;

                IF NULLIF(current_setting('app.current_user', true), '') IS DISTINCT FROM NEW.checker_id::text THEN
                    RAISE EXCEPTION 'Review actor mismatch: session actor does not match checker % (42501).', NEW.checker_id
                        USING ERRCODE = '42501';
                END IF;

                SELECT EXISTS (
                    SELECT 1
                    FROM public.gstr3b_verified_review_tokens
                    WHERE tx_id = txid_current()
                      AND pid = pg_backend_pid()
                      AND filing_id = NEW.id
                      AND checker_id = NEW.checker_id
                      AND new_status = NEW.filing_status
                ) INTO v_has_verified_token;

                IF NOT v_has_verified_token THEN
                    RAISE EXCEPTION 'Unauthenticated GSTR-3B review transition: direct SQL approval/rejection without server-verified reviewer attestation is prohibited (42501).'
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
    $$;
    """)
