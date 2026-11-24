"""GSTR-3B Server-Authenticated Reviewer Identity Binding

Revision ID: 0035_gstr3b_reviewer_binding
Revises: 0034_gstr3b_state_machine
Create Date: 2026-11-25 01:50:00.000000
"""
import hashlib
from typing import Sequence, Union
from alembic import op
from sqlalchemy import text

from app.config import settings

revision: str = '0035_gstr3b_reviewer_binding'
down_revision: Union[str, None] = '0034_gstr3b_state_machine'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create private trust-anchor, nonce-replay, and tx-scoped verified review token tables
    #    Strictly inaccessible to app_service_user and PUBLIC (owner-only / SECURITY DEFINER).
    op.execute("""
    CREATE TABLE IF NOT EXISTS public.gstr3b_review_trust_anchors (
        id text PRIMARY KEY,
        hmac_secret text NOT NULL,
        updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
    );

    CREATE TABLE IF NOT EXISTS public.gstr3b_review_attestation_nonces (
        nonce text PRIMARY KEY,
        filing_id uuid NOT NULL,
        checker_id uuid NOT NULL,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp()
    );

    CREATE TABLE IF NOT EXISTS public.gstr3b_verified_review_tokens (
        tx_id bigint NOT NULL,
        pid integer NOT NULL,
        filing_id uuid NOT NULL,
        checker_id uuid NOT NULL,
        new_status text NOT NULL,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        PRIMARY KEY (tx_id, pid, filing_id)
    );

    REVOKE ALL ON TABLE public.gstr3b_review_trust_anchors FROM PUBLIC, app_service_user, worker_service_user;
    REVOKE ALL ON TABLE public.gstr3b_review_attestation_nonces FROM PUBLIC, app_service_user, worker_service_user;
    REVOKE ALL ON TABLE public.gstr3b_verified_review_tokens FROM PUBLIC, app_service_user, worker_service_user;

    ALTER TABLE public.gstr3b_review_trust_anchors ENABLE ROW LEVEL SECURITY;
    ALTER TABLE public.gstr3b_review_trust_anchors FORCE ROW LEVEL SECURITY;
    ALTER TABLE public.gstr3b_review_attestation_nonces ENABLE ROW LEVEL SECURITY;
    ALTER TABLE public.gstr3b_review_attestation_nonces FORCE ROW LEVEL SECURITY;
    ALTER TABLE public.gstr3b_verified_review_tokens ENABLE ROW LEVEL SECURITY;
    ALTER TABLE public.gstr3b_verified_review_tokens FORCE ROW LEVEL SECURITY;
    """)

    # Seed derived HMAC trust anchor from application SECRET_KEY
    derived_secret = hashlib.sha256(
        f"MYCA_GSTR3B_REVIEW_TRUST_V1:{settings.SECRET_KEY}".encode("utf-8")
    ).hexdigest()

    conn = op.get_bind()
    conn.execute(
        text("""
        INSERT INTO public.gstr3b_review_trust_anchors (id, hmac_secret, updated_at)
        VALUES ('default', :secret, clock_timestamp())
        ON CONFLICT (id) DO UPDATE SET
            hmac_secret = EXCLUDED.hmac_secret,
            updated_at = clock_timestamp();
        """),
        {"secret": derived_secret},
    )

    # 2. Restricted SECURITY DEFINER review transition function
    op.execute("""
    CREATE OR REPLACE FUNCTION public.execute_gstr3b_review_transition(
        p_org_id uuid,
        p_filing_id uuid,
        p_checker_id uuid,
        p_new_status text,
        p_remarks text,
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
        IF p_new_status NOT IN ('APPROVED_BY_CHECKER', 'REJECTED_BY_CHECKER') THEN
            RAISE EXCEPTION 'Invalid review target status % (42501).', p_new_status
                USING ERRCODE = '42501';
        END IF;

        IF p_checker_id IS NULL OR NULLIF(p_nonce, '') IS NULL OR NULLIF(p_signature, '') IS NULL THEN
            RAISE EXCEPTION 'Missing reviewer identity or server attestation proof (42501).'
                USING ERRCODE = '42501';
        END IF;

        v_actor := NULLIF(current_setting('app.current_user', true), '');
        IF v_actor IS DISTINCT FROM p_checker_id::text THEN
            RAISE EXCEPTION 'Review actor mismatch: session actor does not match checker % (42501).', p_checker_id
                USING ERRCODE = '42501';
        END IF;

        SELECT hmac_secret INTO v_secret
        FROM public.gstr3b_review_trust_anchors
        WHERE id = 'default';

        IF v_secret IS NULL THEN
            RAISE EXCEPTION 'Review trust anchor not configured (42501).'
                USING ERRCODE = '42501';
        END IF;

        v_msg := p_org_id::text || ':' || p_filing_id::text || ':' || p_checker_id::text || ':' || p_new_status || ':' || p_nonce;
        v_expected := encode(
            public.hmac(convert_to(v_msg, 'UTF8'), convert_to(v_secret, 'UTF8'), 'sha256'),
            'hex'
        );

        IF length(p_signature) != length(v_expected) THEN
            RAISE EXCEPTION 'Invalid server-authenticated reviewer attestation (42501).'
                USING ERRCODE = '42501';
        END IF;

        v_len := length(v_expected);
        FOR v_i IN 1..v_len LOOP
            IF substr(p_signature, v_i, 1) != substr(v_expected, v_i, 1) THEN
                v_diff := 1;
            END IF;
        END LOOP;

        IF v_diff != 0 THEN
            RAISE EXCEPTION 'Invalid server-authenticated reviewer attestation (42501).'
                USING ERRCODE = '42501';
        END IF;

        -- Prevent attestation replay
        BEGIN
            INSERT INTO public.gstr3b_review_attestation_nonces (nonce, filing_id, checker_id)
            VALUES (p_nonce, p_filing_id, p_checker_id);
        EXCEPTION WHEN unique_violation THEN
            RAISE EXCEPTION 'Replayed reviewer attestation nonce rejected (42501).'
                USING ERRCODE = '42501';
        END;

        -- Issue transaction-scoped verified review token
        INSERT INTO public.gstr3b_verified_review_tokens (tx_id, pid, filing_id, checker_id, new_status)
        VALUES (txid_current(), pg_backend_pid(), p_filing_id, p_checker_id, p_new_status)
        ON CONFLICT (tx_id, pid, filing_id) DO UPDATE SET
            checker_id = EXCLUDED.checker_id,
            new_status = EXCLUDED.new_status,
            created_at = clock_timestamp();

        UPDATE public.gstr3b_filings
        SET filing_status = p_new_status,
            checker_id = p_checker_id,
            checker_remarks = p_remarks,
            updated_at = clock_timestamp()
        WHERE id = p_filing_id AND org_id = p_org_id;

        DELETE FROM public.gstr3b_verified_review_tokens
        WHERE tx_id = txid_current() AND pid = pg_backend_pid() AND filing_id = p_filing_id;
    END;
    $$;

    REVOKE ALL ON FUNCTION public.execute_gstr3b_review_transition(uuid, uuid, uuid, text, text, text, text) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION public.execute_gstr3b_review_transition(uuid, uuid, uuid, text, text, text, text) TO app_service_user;
    """)

    # 3. Upgrade enforce_gstr3b_filed_immutability() to require verified review token & freeze maker_id
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

            -- 4. Review transitions (approval / rejection) require:
            --    a) Preserved maker_id (cannot rewrite maker_id during review)
            --    b) Server-verified review token issued by execute_gstr3b_review_transition()
            --    c) Session actor matching NEW.checker_id
            --    d) Distinct, active, same-org admin/owner Checker
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


def downgrade() -> None:
    op.execute("""
    DROP FUNCTION IF EXISTS public.execute_gstr3b_review_transition(uuid, uuid, uuid, text, text, text, text);
    DROP TABLE IF EXISTS public.gstr3b_verified_review_tokens CASCADE;
    DROP TABLE IF EXISTS public.gstr3b_review_attestation_nonces CASCADE;
    DROP TABLE IF EXISTS public.gstr3b_review_trust_anchors CASCADE;
    """)

    # Restore 0034 trigger function
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
