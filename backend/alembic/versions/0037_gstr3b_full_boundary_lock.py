"""GSTR-3B Full Database Boundary Lock: INSERT Guard, Immutable Maker, and Verified Filing Token

Revision ID: 0037_gstr3b_full_boundary_lock
Revises: 0036_gstr3b_maker_checker_lock
Create Date: 2026-11-25 02:20:00.000000
"""
from typing import Sequence, Union
from alembic import op

revision: str = '0037_gstr3b_full_boundary_lock'
down_revision: Union[str, None] = '0036_gstr3b_maker_checker_lock'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Restricted SECURITY DEFINER filing transition function (APPROVED_BY_CHECKER -> FILED)
    op.execute("""
    CREATE OR REPLACE FUNCTION public.execute_gstr3b_filing_transition(
        p_org_id uuid,
        p_filing_id uuid,
        p_actor_id uuid,
        p_arn text,
        p_cin text,
        p_filing_date timestamptz,
        p_payload_hash text,
        p_nonce text,
        p_signature text
    ) RETURNS void
    LANGUAGE plpgsql
    SECURITY DEFINER
    SET search_path = public, pg_catalog
    AS $$
    DECLARE
        v_secret text;
        v_msg text;
        v_expected text;
        v_diff integer := 0;
        v_i integer;
        v_len integer;
        v_actor text;
    BEGIN
        IF p_actor_id IS NULL OR NULLIF(p_arn, '') IS NULL OR NULLIF(p_payload_hash, '') IS NULL
           OR p_filing_date IS NULL OR NULLIF(p_nonce, '') IS NULL OR NULLIF(p_signature, '') IS NULL THEN
            RAISE EXCEPTION 'Missing filing actor, statutory evidence, or server attestation proof (42501).'
                USING ERRCODE = '42501';
        END IF;

        v_actor := NULLIF(current_setting('app.current_user', true), '');
        IF v_actor IS DISTINCT FROM p_actor_id::text THEN
            RAISE EXCEPTION 'Filing actor mismatch: session actor does not match % (42501).', p_actor_id
                USING ERRCODE = '42501';
        END IF;

        SELECT hmac_secret INTO v_secret
        FROM public.gstr3b_review_trust_anchors
        WHERE id = 'default';

        IF v_secret IS NULL THEN
            RAISE EXCEPTION 'Filing trust anchor not configured (42501).'
                USING ERRCODE = '42501';
        END IF;

        v_msg := p_org_id::text || ':' || p_filing_id::text || ':' || p_actor_id::text || ':FILED:' ||
                 p_arn || ':' || COALESCE(p_cin, '') || ':' || p_payload_hash || ':' || p_nonce;
        v_expected := encode(
            public.hmac(convert_to(v_msg, 'UTF8'), convert_to(v_secret, 'UTF8'), 'sha256'),
            'hex'
        );

        IF length(p_signature) != length(v_expected) THEN
            RAISE EXCEPTION 'Invalid server-authenticated filing attestation (42501).'
                USING ERRCODE = '42501';
        END IF;

        v_len := length(v_expected);
        FOR v_i IN 1..v_len LOOP
            IF substr(p_signature, v_i, 1) != substr(v_expected, v_i, 1) THEN
                v_diff := 1;
            END IF;
        END LOOP;

        IF v_diff != 0 THEN
            RAISE EXCEPTION 'Invalid server-authenticated filing attestation (42501).'
                USING ERRCODE = '42501';
        END IF;

        -- Prevent attestation replay
        BEGIN
            INSERT INTO public.gstr3b_review_attestation_nonces (nonce, filing_id, checker_id)
            VALUES (p_nonce, p_filing_id, p_actor_id);
        EXCEPTION WHEN unique_violation THEN
            RAISE EXCEPTION 'Replayed filing attestation nonce rejected (42501).'
                USING ERRCODE = '42501';
        END;

        -- Issue transaction-scoped verified filing token
        INSERT INTO public.gstr3b_verified_review_tokens (tx_id, pid, filing_id, checker_id, new_status)
        VALUES (txid_current(), pg_backend_pid(), p_filing_id, p_actor_id, 'FILED')
        ON CONFLICT (tx_id, pid, filing_id) DO UPDATE SET
            checker_id = EXCLUDED.checker_id,
            new_status = EXCLUDED.new_status,
            created_at = clock_timestamp();

        UPDATE public.gstr3b_filings
        SET filing_status = 'FILED',
            arn = p_arn,
            cin = p_cin,
            filing_date = p_filing_date,
            payload_hash = p_payload_hash,
            updated_at = clock_timestamp()
        WHERE id = p_filing_id AND org_id = p_org_id;

        DELETE FROM public.gstr3b_verified_review_tokens
        WHERE tx_id = txid_current() AND pid = pg_backend_pid() AND filing_id = p_filing_id;
    END;
    $$;

    REVOKE ALL ON FUNCTION public.execute_gstr3b_filing_transition(uuid, uuid, uuid, text, text, timestamptz, text, text, text) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION public.execute_gstr3b_filing_transition(uuid, uuid, uuid, text, text, timestamptz, text, text, text) TO app_service_user;
    """)

    # 2. Upgrade trigger function to enforce INSERT guard, immutable Maker, and verified FILED token
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
        IF TG_OP = 'INSERT' THEN
            -- For application roles (session_user != 'postgres'), enforce strict DRAFT-only creation
            -- on same-tenant inserts (cross-tenant / null-context inserts are rejected by RLS WITH CHECK).
            IF session_user != 'postgres'
               AND NULLIF(current_setting('app.current_org', true), '') IS NOT NULL
               AND NEW.org_id = NULLIF(current_setting('app.current_org', true), '')::uuid THEN
                IF NEW.filing_status IS DISTINCT FROM 'DRAFT' THEN
                    RAISE EXCEPTION 'Direct INSERT into gstr3b_filings with status % is prohibited. New filings must enter as DRAFT (42501).',
                        NEW.filing_status
                        USING ERRCODE = '42501';
                END IF;
                IF NEW.checker_id IS NOT NULL OR NEW.checker_remarks IS NOT NULL THEN
                    RAISE EXCEPTION 'Direct INSERT into gstr3b_filings cannot set checker attribution or remarks (42501).'
                        USING ERRCODE = '42501';
                END IF;
                IF NEW.arn IS NOT NULL OR NEW.cin IS NOT NULL OR NEW.filing_date IS NOT NULL OR NEW.payload_hash IS NOT NULL THEN
                    RAISE EXCEPTION 'Direct INSERT into gstr3b_filings cannot set statutory filing evidence (42501).'
                        USING ERRCODE = '42501';
                END IF;
                IF NEW.maker_id IS NULL OR NULLIF(current_setting('app.current_user', true), '') IS DISTINCT FROM NEW.maker_id::text THEN
                    RAISE EXCEPTION 'Direct INSERT into gstr3b_filings requires maker_id matching authenticated session actor (42501).'
                        USING ERRCODE = '42501';
                END IF;
            END IF;
            RETURN NEW;

        ELSIF TG_OP = 'DELETE' THEN
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

            -- 4. Maker attribution (maker_id) is immutable once established (even in DRAFT or REJECTED_BY_CHECKER)
            IF OLD.maker_id IS NOT NULL AND NEW.maker_id IS DISTINCT FROM OLD.maker_id THEN
                RAISE EXCEPTION 'Maker identity is immutable once established and cannot be reassigned during % -> % for return % (42501).',
                    OLD.filing_status, NEW.filing_status, OLD.id
                    USING ERRCODE = '42501';
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
                    NULL;
                ELSIF (
                    NEW.filing_status IN ('APPROVED_BY_CHECKER', 'REJECTED_BY_CHECKER')
                    AND NEW.filing_status IS DISTINCT FROM OLD.filing_status
                ) THEN
                    NULL;
                ELSE
                    RAISE EXCEPTION 'Checker attribution and remarks are immutable during % -> % for return % (42501).',
                        OLD.filing_status, NEW.filing_status, OLD.id
                        USING ERRCODE = '42501';
                END IF;
            END IF;

            -- 6. Review transitions (approval / rejection) require verified review token + session actor + active admin/owner
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

            -- 7. Filing transition (APPROVED_BY_CHECKER -> FILED) requires server-verified filing token & evidence
            IF NEW.filing_status = 'FILED' AND OLD.filing_status IS DISTINCT FROM 'FILED' THEN
                IF NULLIF(NEW.arn, '') IS NULL OR NULLIF(NEW.payload_hash, '') IS NULL OR NEW.filing_date IS NULL THEN
                    RAISE EXCEPTION 'GSTR-3B filing transition requires valid ARN, payload_hash, and filing_date (42501).'
                        USING ERRCODE = '42501';
                END IF;

                SELECT EXISTS (
                    SELECT 1
                    FROM public.gstr3b_verified_review_tokens
                    WHERE tx_id = txid_current()
                      AND pid = pg_backend_pid()
                      AND filing_id = NEW.id
                      AND new_status = 'FILED'
                ) INTO v_has_verified_token;

                IF NOT v_has_verified_token THEN
                    RAISE EXCEPTION 'Unauthenticated GSTR-3B filing transition: direct SQL update to FILED without server-verified filing attestation is prohibited (42501).'
                        USING ERRCODE = '42501';
                END IF;
            END IF;

            RETURN NEW;
        END IF;
        RETURN NEW;
    END;
    $$;
    """)

    # 3. Attach trigger to BEFORE INSERT OR UPDATE OR DELETE
    op.execute("""
    DROP TRIGGER IF EXISTS trg_prevent_gstr3b_filed_mutations ON public.gstr3b_filings;
    CREATE TRIGGER trg_prevent_gstr3b_filed_mutations
    BEFORE INSERT OR UPDATE OR DELETE ON public.gstr3b_filings
    FOR EACH ROW
    EXECUTE FUNCTION public.enforce_gstr3b_filed_immutability();
    """)


def downgrade() -> None:
    op.execute("""
    DROP TRIGGER IF EXISTS trg_prevent_gstr3b_filed_mutations ON public.gstr3b_filings;
    CREATE TRIGGER trg_prevent_gstr3b_filed_mutations
    BEFORE UPDATE OR DELETE ON public.gstr3b_filings
    FOR EACH ROW
    EXECUTE FUNCTION public.enforce_gstr3b_filed_immutability();

    DROP FUNCTION IF EXISTS public.execute_gstr3b_filing_transition(uuid, uuid, uuid, text, text, timestamptz, text, text, text);
    """)

    # Restore 0036 trigger function
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

            IF (NEW.checker_id IS DISTINCT FROM OLD.checker_id OR NEW.checker_remarks IS DISTINCT FROM OLD.checker_remarks) THEN
                IF (
                    OLD.filing_status = 'REJECTED_BY_CHECKER'
                    AND NEW.filing_status = 'DRAFT'
                    AND NEW.checker_id IS NULL
                    AND NEW.checker_remarks IS NULL
                ) THEN
                    NULL;
                ELSIF (
                    NEW.filing_status IN ('APPROVED_BY_CHECKER', 'REJECTED_BY_CHECKER')
                    AND NEW.filing_status IS DISTINCT FROM OLD.filing_status
                ) THEN
                    NULL;
                ELSE
                    RAISE EXCEPTION 'Checker attribution and remarks are immutable during % -> % for return % (42501).',
                        OLD.filing_status, NEW.filing_status, OLD.id
                        USING ERRCODE = '42501';
                END IF;
            END IF;

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
