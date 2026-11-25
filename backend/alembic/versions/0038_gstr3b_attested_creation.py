"""GSTR-3B Server-Authenticated Creation, Legacy NULL Maker Lock, and Trust Anchor Sync

Revision ID: 0038_gstr3b_attested_creation
Revises: 0037_gstr3b_full_boundary_lock
Create Date: 2026-11-25 02:35:00.000000
"""
import hashlib
from typing import Sequence, Union
from alembic import op
from sqlalchemy import text

from app.config import settings

revision: str = '0038_gstr3b_attested_creation'
down_revision: Union[str, None] = '0037_gstr3b_full_boundary_lock'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Synchronize trust anchor with dedicated GSTR3B_REVIEW_ATTESTATION_KEY (or SECRET_KEY fallback)
    #    for existing deployments that already ran 0035 before dedicated key support was added.
    raw_key = settings.GSTR3B_REVIEW_ATTESTATION_KEY or f"MYCA_GSTR3B_REVIEW_TRUST_V1:{settings.SECRET_KEY}"
    derived_secret = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()

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

    # 2. Restricted SECURITY DEFINER creation/preparation function
    op.execute("""
    CREATE OR REPLACE FUNCTION public.execute_gstr3b_prepare_upsert(
        p_id uuid,
        p_org_id uuid,
        p_reg_id uuid,
        p_tax_period text,
        p_t31_text text,
        p_t4_text text,
        p_offset_text text,
        p_maker_id uuid,
        p_maker_remarks text,
        p_rev integer,
        p_prev_snap_text text,
        p_now timestamptz,
        p_payload_digest text,
        p_nonce text,
        p_signature text
    ) RETURNS TABLE (id uuid, filing_status character varying, revision_number integer)
    LANGUAGE plpgsql
    SECURITY DEFINER
    SET search_path = public, pg_catalog
    AS $$
    DECLARE
        v_secret text;
        v_computed_digest text;
        v_msg text;
        v_expected text;
        v_diff integer := 0;
        v_i integer;
        v_len integer;
        v_reg_ok boolean;
        v_maker_ok boolean;
    BEGIN
        IF p_id IS NULL OR p_org_id IS NULL OR p_reg_id IS NULL OR NULLIF(p_tax_period, '') IS NULL
           OR p_maker_id IS NULL OR NULLIF(p_payload_digest, '') IS NULL
           OR NULLIF(p_nonce, '') IS NULL OR NULLIF(p_signature, '') IS NULL THEN
            RAISE EXCEPTION 'Missing required GSTR-3B creation parameters or server attestation proof (42501).'
                USING ERRCODE = '42501';
        END IF;

        -- Validate registration belongs to org_id
        SELECT EXISTS (
            SELECT 1 FROM public.gst_registrations
            WHERE public.gst_registrations.id = p_reg_id AND public.gst_registrations.org_id = p_org_id
        ) INTO v_reg_ok;
        IF NOT v_reg_ok THEN
            RAISE EXCEPTION 'Invalid GST registration % for organization % (42501).', p_reg_id, p_org_id
                USING ERRCODE = '42501';
        END IF;

        -- Validate maker is an active member of org_id
        SELECT EXISTS (
            SELECT 1 FROM public.organization_memberships
            WHERE public.organization_memberships.org_id = p_org_id
              AND public.organization_memberships.user_id = p_maker_id
              AND public.organization_memberships.status = 'active'
        ) INTO v_maker_ok;
        IF NOT v_maker_ok THEN
            RAISE EXCEPTION 'Maker % is not an active member of organization % (42501).', p_maker_id, p_org_id
                USING ERRCODE = '42501';
        END IF;

        -- Verify payload digest matches canonical financial JSON
        v_computed_digest := encode(
            public.digest(convert_to(p_t31_text || ':' || p_t4_text || ':' || p_offset_text, 'UTF8'), 'sha256'),
            'hex'
        );
        IF v_computed_digest IS DISTINCT FROM p_payload_digest THEN
            RAISE EXCEPTION 'Financial snapshot digest mismatch (42501).'
                USING ERRCODE = '42501';
        END IF;

        SELECT hmac_secret INTO v_secret
        FROM public.gstr3b_review_trust_anchors
        WHERE public.gstr3b_review_trust_anchors.id = 'default';

        IF v_secret IS NULL THEN
            RAISE EXCEPTION 'Creation trust anchor not configured (42501).'
                USING ERRCODE = '42501';
        END IF;

        v_msg := p_org_id::text || ':' || p_id::text || ':' || p_reg_id::text || ':' || p_tax_period || ':' ||
                 p_maker_id::text || ':DRAFT:' || p_payload_digest || ':' || p_nonce;
        v_expected := encode(
            public.hmac(convert_to(v_msg, 'UTF8'), convert_to(v_secret, 'UTF8'), 'sha256'),
            'hex'
        );

        IF length(p_signature) != length(v_expected) THEN
            RAISE EXCEPTION 'Invalid server-authenticated creation attestation (42501).'
                USING ERRCODE = '42501';
        END IF;

        v_len := length(v_expected);
        FOR v_i IN 1..v_len LOOP
            IF substr(p_signature, v_i, 1) != substr(v_expected, v_i, 1) THEN
                v_diff := 1;
            END IF;
        END LOOP;

        IF v_diff != 0 THEN
            RAISE EXCEPTION 'Invalid server-authenticated creation attestation (42501).'
                USING ERRCODE = '42501';
        END IF;

        -- Prevent creation attestation replay
        BEGIN
            INSERT INTO public.gstr3b_review_attestation_nonces (nonce, filing_id, checker_id)
            VALUES (p_nonce, p_id, p_maker_id);
        EXCEPTION WHEN unique_violation THEN
            RAISE EXCEPTION 'Replayed creation attestation nonce rejected (42501).'
                USING ERRCODE = '42501';
        END;

        -- Issue transaction-scoped verified creation token
        INSERT INTO public.gstr3b_verified_review_tokens (tx_id, pid, filing_id, checker_id, new_status)
        VALUES (txid_current(), pg_backend_pid(), p_id, p_maker_id, 'DRAFT')
        ON CONFLICT (tx_id, pid, filing_id) DO UPDATE SET
            checker_id = EXCLUDED.checker_id,
            new_status = EXCLUDED.new_status,
            created_at = clock_timestamp();

        RETURN QUERY
        INSERT INTO public.gstr3b_filings (
            id, org_id, registration_id, tax_period, filing_status,
            table_3_1_data, table_4_data, offset_data, maker_id,
            maker_remarks, revision_number, previous_snapshot, updated_at
        ) VALUES (
            p_id, p_org_id, p_reg_id, p_tax_period, 'DRAFT',
            p_t31_text::jsonb, p_t4_text::jsonb, p_offset_text::jsonb, p_maker_id,
            p_maker_remarks, p_rev, CASE WHEN p_prev_snap_text IS NULL THEN NULL ELSE p_prev_snap_text::jsonb END, p_now
        )
        ON CONFLICT (org_id, registration_id, tax_period)
        DO UPDATE SET
            table_3_1_data = EXCLUDED.table_3_1_data,
            table_4_data = EXCLUDED.table_4_data,
            offset_data = EXCLUDED.offset_data,
            filing_status = 'DRAFT',
            maker_id = COALESCE(public.gstr3b_filings.maker_id, EXCLUDED.maker_id),
            maker_remarks = EXCLUDED.maker_remarks,
            revision_number = EXCLUDED.revision_number,
            previous_snapshot = EXCLUDED.previous_snapshot,
            checker_id = NULL,
            checker_remarks = NULL,
            updated_at = EXCLUDED.updated_at
        WHERE public.gstr3b_filings.filing_status IN ('DRAFT', 'REJECTED_BY_CHECKER')
        RETURNING public.gstr3b_filings.id, public.gstr3b_filings.filing_status, public.gstr3b_filings.revision_number;

        DELETE FROM public.gstr3b_verified_review_tokens
        WHERE tx_id = txid_current() AND pid = pg_backend_pid() AND public.gstr3b_verified_review_tokens.filing_id = p_id;
    END;
    $$;

    REVOKE ALL ON FUNCTION public.execute_gstr3b_prepare_upsert(uuid, uuid, uuid, text, text, text, text, uuid, text, integer, text, timestamptz, text, text, text) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION public.execute_gstr3b_prepare_upsert(uuid, uuid, uuid, text, text, text, text, uuid, text, integer, text, timestamptz, text, text, text) TO app_service_user;
    """)

    # 3. Restricted SECURITY DEFINER function for one-time legacy NULL maker_id claim
    op.execute("""
    CREATE OR REPLACE FUNCTION public.claim_legacy_null_maker(
        p_org_id uuid,
        p_filing_id uuid,
        p_maker_id uuid,
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
        v_maker_ok boolean;
    BEGIN
        IF p_org_id IS NULL OR p_filing_id IS NULL OR p_maker_id IS NULL
           OR NULLIF(p_nonce, '') IS NULL OR NULLIF(p_signature, '') IS NULL THEN
            RAISE EXCEPTION 'Missing legacy maker claim parameters or attestation (42501).'
                USING ERRCODE = '42501';
        END IF;

        SELECT EXISTS (
            SELECT 1 FROM public.organization_memberships
            WHERE org_id = p_org_id AND user_id = p_maker_id AND status = 'active'
        ) INTO v_maker_ok;
        IF NOT v_maker_ok THEN
            RAISE EXCEPTION 'Maker % is not an active member of organization % (42501).', p_maker_id, p_org_id
                USING ERRCODE = '42501';
        END IF;

        SELECT hmac_secret INTO v_secret FROM public.gstr3b_review_trust_anchors WHERE id = 'default';
        IF v_secret IS NULL THEN
            RAISE EXCEPTION 'Trust anchor not configured (42501).' USING ERRCODE = '42501';
        END IF;

        v_msg := p_org_id::text || ':' || p_filing_id::text || ':' || p_maker_id::text || ':LEGACY_MAKER_CLAIM:' || p_nonce;
        v_expected := encode(public.hmac(convert_to(v_msg, 'UTF8'), convert_to(v_secret, 'UTF8'), 'sha256'), 'hex');

        IF length(p_signature) != length(v_expected) THEN
            RAISE EXCEPTION 'Invalid legacy maker claim attestation (42501).' USING ERRCODE = '42501';
        END IF;
        v_len := length(v_expected);
        FOR v_i IN 1..v_len LOOP
            IF substr(p_signature, v_i, 1) != substr(v_expected, v_i, 1) THEN
                v_diff := 1;
            END IF;
        END LOOP;
        IF v_diff != 0 THEN
            RAISE EXCEPTION 'Invalid legacy maker claim attestation (42501).' USING ERRCODE = '42501';
        END IF;

        BEGIN
            INSERT INTO public.gstr3b_review_attestation_nonces (nonce, filing_id, checker_id)
            VALUES (p_nonce, p_filing_id, p_maker_id);
        EXCEPTION WHEN unique_violation THEN
            RAISE EXCEPTION 'Replayed legacy maker claim nonce rejected (42501).' USING ERRCODE = '42501';
        END;

        INSERT INTO public.gstr3b_verified_review_tokens (tx_id, pid, filing_id, checker_id, new_status)
        VALUES (txid_current(), pg_backend_pid(), p_filing_id, p_maker_id, 'LEGACY_MAKER_CLAIM')
        ON CONFLICT (tx_id, pid, filing_id) DO UPDATE SET
            checker_id = EXCLUDED.checker_id,
            new_status = EXCLUDED.new_status,
            created_at = clock_timestamp();

        UPDATE public.gstr3b_filings
        SET maker_id = p_maker_id,
            updated_at = clock_timestamp()
        WHERE id = p_filing_id AND org_id = p_org_id AND maker_id IS NULL;

        DELETE FROM public.gstr3b_verified_review_tokens
        WHERE tx_id = txid_current() AND pid = pg_backend_pid() AND filing_id = p_filing_id;
    END;
    $$;

    REVOKE ALL ON FUNCTION public.claim_legacy_null_maker(uuid, uuid, uuid, text, text) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION public.claim_legacy_null_maker(uuid, uuid, uuid, text, text) TO app_service_user;
    """)

    # 4. Upgrade trigger to require server-attested creation token on INSERT and LEGACY_MAKER_CLAIM token on NULL->non-NULL maker_id
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
                IF NEW.maker_id IS NULL THEN
                    RAISE EXCEPTION 'Direct INSERT into gstr3b_filings requires authenticated maker_id (42501).'
                        USING ERRCODE = '42501';
                END IF;

                SELECT EXISTS (
                    SELECT 1
                    FROM public.gstr3b_verified_review_tokens
                    WHERE tx_id = txid_current()
                      AND pid = pg_backend_pid()
                      AND filing_id = NEW.id
                      AND checker_id = NEW.maker_id
                      AND new_status = 'DRAFT'
                ) INTO v_has_verified_token;

                IF NOT v_has_verified_token THEN
                    RAISE EXCEPTION 'Unauthenticated GSTR-3B creation: direct SQL INSERT without server-verified creation attestation is prohibited (42501).'
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

            -- 4. Maker attribution: immutable once established; legacy NULL -> non-NULL requires server-verified LEGACY_MAKER_CLAIM token
            IF OLD.maker_id IS NOT NULL AND NEW.maker_id IS DISTINCT FROM OLD.maker_id THEN
                RAISE EXCEPTION 'Maker identity is immutable once established and cannot be reassigned during % -> % for return % (42501).',
                    OLD.filing_status, NEW.filing_status, OLD.id
                    USING ERRCODE = '42501';
            END IF;

            IF OLD.maker_id IS NULL AND NEW.maker_id IS NOT NULL THEN
                SELECT EXISTS (
                    SELECT 1
                    FROM public.gstr3b_verified_review_tokens
                    WHERE tx_id = txid_current()
                      AND pid = pg_backend_pid()
                      AND filing_id = NEW.id
                      AND checker_id = NEW.maker_id
                      AND new_status = 'LEGACY_MAKER_CLAIM'
                ) INTO v_has_verified_token;

                IF NOT v_has_verified_token THEN
                    RAISE EXCEPTION 'Unauthorized legacy maker_id assignment on return % without server-verified claim attestation (42501).', OLD.id
                        USING ERRCODE = '42501';
                END IF;
            END IF;

            -- 5. Checker attribution protection
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

            -- 6. Review transitions require verified review token + session actor + active admin/owner
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


def downgrade() -> None:
    op.execute("""
    DROP FUNCTION IF EXISTS public.claim_legacy_null_maker(uuid, uuid, uuid, text, text);
    DROP FUNCTION IF EXISTS public.execute_gstr3b_prepare_upsert(uuid, uuid, uuid, text, text, text, text, uuid, text, integer, text, timestamptz, text, text, text);
    """)

    # Restore 0037 trigger function
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
            IF OLD.maker_id IS NOT NULL AND NEW.maker_id IS DISTINCT FROM OLD.maker_id THEN
                RAISE EXCEPTION 'Maker identity is immutable once established and cannot be reassigned during % -> % for return % (42501).',
                    OLD.filing_status, NEW.filing_status, OLD.id
                    USING ERRCODE = '42501';
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
                    SELECT 1 FROM public.gstr3b_verified_review_tokens
                    WHERE tx_id = txid_current() AND pid = pg_backend_pid()
                      AND filing_id = NEW.id AND checker_id = NEW.checker_id AND new_status = NEW.filing_status
                ) INTO v_has_verified_token;
                IF NOT v_has_verified_token THEN
                    RAISE EXCEPTION 'Unauthenticated GSTR-3B review transition: direct SQL approval/rejection without server-verified reviewer attestation is prohibited (42501).'
                        USING ERRCODE = '42501';
                END IF;
                SELECT EXISTS (
                    SELECT 1 FROM public.organization_memberships
                    WHERE org_id = NEW.org_id AND user_id = NEW.checker_id AND status = 'active' AND role IN ('admin', 'owner')
                ) INTO v_has_valid_checker;
                IF NOT v_has_valid_checker THEN
                    RAISE EXCEPTION 'Unauthorized reviewer: Checker % is not an active admin/owner of organization % (42501).',
                        NEW.checker_id, NEW.org_id
                        USING ERRCODE = '42501';
                END IF;
            END IF;
            IF NEW.filing_status = 'FILED' AND OLD.filing_status IS DISTINCT FROM 'FILED' THEN
                IF NULLIF(NEW.arn, '') IS NULL OR NULLIF(NEW.payload_hash, '') IS NULL OR NEW.filing_date IS NULL THEN
                    RAISE EXCEPTION 'GSTR-3B filing transition requires valid ARN, payload_hash, and filing_date (42501).'
                        USING ERRCODE = '42501';
                END IF;
                SELECT EXISTS (
                    SELECT 1 FROM public.gstr3b_verified_review_tokens
                    WHERE tx_id = txid_current() AND pid = pg_backend_pid()
                      AND filing_id = NEW.id AND new_status = 'FILED'
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
