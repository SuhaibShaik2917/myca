"""GSTR-3B Legacy Maker Claim Provenance and Full Creation Metadata Attestation

Revision ID: 0039_gstr3b_legacy_claim_meta
Revises: 0038_gstr3b_attested_creation
Create Date: 2026-11-25 14:55:00.000000
"""
from typing import Sequence, Union
from alembic import op

revision: str = '0039_gstr3b_legacy_claim_meta'
down_revision: Union[str, None] = '0038_gstr3b_attested_creation'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Append-only legacy Maker claim provenance table
    op.execute("""
    CREATE TABLE IF NOT EXISTS public.gstr3b_legacy_maker_claims (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        filing_id uuid NOT NULL REFERENCES public.gstr3b_filings(id) ON DELETE CASCADE,
        org_id uuid NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
        original_maker_state text NOT NULL DEFAULT 'UNKNOWN_LEGACY_NULL',
        provenance_type text NOT NULL DEFAULT 'LEGACY_SUBMISSION_CLAIM',
        claimed_by_user_id uuid NOT NULL,
        claim_reason text NOT NULL,
        attestation_nonce text NOT NULL,
        claimed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        UNIQUE (filing_id)
    );

    ALTER TABLE public.gstr3b_legacy_maker_claims ENABLE ROW LEVEL SECURITY;
    ALTER TABLE public.gstr3b_legacy_maker_claims FORCE ROW LEVEL SECURITY;

    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_policies WHERE tablename = 'gstr3b_legacy_maker_claims' AND policyname = 'gstr3b_legacy_maker_claims_tenant'
        ) THEN
            CREATE POLICY gstr3b_legacy_maker_claims_tenant ON public.gstr3b_legacy_maker_claims
                FOR ALL
                TO app_service_user
                USING (org_id = (NULLIF(current_setting('app.current_org', true), ''))::uuid)
                WITH CHECK (org_id = (NULLIF(current_setting('app.current_org', true), ''))::uuid);
        END IF;

        IF NOT EXISTS (
            SELECT 1 FROM pg_policies WHERE tablename = 'gstr3b_legacy_maker_claims' AND policyname = 'gstr3b_legacy_maker_claims_admin'
        ) THEN
            CREATE POLICY gstr3b_legacy_maker_claims_admin ON public.gstr3b_legacy_maker_claims
                FOR ALL
                TO platform_admin
                USING (true)
                WITH CHECK (true);
        END IF;
    END $$;

    GRANT SELECT ON public.gstr3b_legacy_maker_claims TO app_service_user;

    CREATE OR REPLACE FUNCTION public.enforce_gstr3b_legacy_claim_immutability()
    RETURNS TRIGGER AS $$
    BEGIN
        RAISE EXCEPTION 'GSTR-3B legacy maker claim record % is append-only and strictly immutable. % prohibited (42501).', OLD.id, TG_OP
            USING ERRCODE = '42501';
    END;
    $$ LANGUAGE plpgsql;

    DROP TRIGGER IF EXISTS trg_prevent_gstr3b_legacy_claim_mutations ON public.gstr3b_legacy_maker_claims;
    CREATE TRIGGER trg_prevent_gstr3b_legacy_claim_mutations
    BEFORE UPDATE OR DELETE ON public.gstr3b_legacy_maker_claims
    FOR EACH ROW
    EXECUTE FUNCTION public.enforce_gstr3b_legacy_claim_immutability();
    """)

    # 2. Upgrade claim_legacy_null_maker to record provenance event
    op.execute("""
    DROP FUNCTION IF EXISTS public.claim_legacy_null_maker(uuid, uuid, uuid, text, text);

    CREATE OR REPLACE FUNCTION public.claim_legacy_null_maker(
        p_org_id uuid,
        p_filing_id uuid,
        p_maker_id uuid,
        p_reason text,
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
        v_filing_id uuid;
        v_filing_org_id uuid;
        v_existing_maker_id uuid;
        v_filing_status text;
        v_rows_updated integer := 0;
    BEGIN
        IF p_org_id IS NULL OR p_filing_id IS NULL OR p_maker_id IS NULL
           OR NULLIF(p_reason, '') IS NULL OR NULLIF(p_nonce, '') IS NULL OR NULLIF(p_signature, '') IS NULL THEN
            RAISE EXCEPTION 'Missing legacy maker claim parameters, reason, or attestation (42501).'
                USING ERRCODE = '42501';
        END IF;

        -- Lock target filing first and validate invariants before recording provenance or consuming nonce
        SELECT id, org_id, maker_id, filing_status
        INTO v_filing_id, v_filing_org_id, v_existing_maker_id, v_filing_status
        FROM public.gstr3b_filings
        WHERE id = p_filing_id
        FOR UPDATE;

        IF NOT FOUND THEN
            RAISE EXCEPTION 'GSTR-3B filing % not found for legacy maker claim (42501).', p_filing_id
                USING ERRCODE = '42501';
        END IF;

        IF v_filing_org_id IS DISTINCT FROM p_org_id THEN
            RAISE EXCEPTION 'GSTR-3B filing % does not belong to organization % (42501).', p_filing_id, p_org_id
                USING ERRCODE = '42501';
        END IF;

        IF v_existing_maker_id IS NOT NULL THEN
            RAISE EXCEPTION 'GSTR-3B filing % already has an established maker_id % (42501).', p_filing_id, v_existing_maker_id
                USING ERRCODE = '42501';
        END IF;

        IF v_filing_status NOT IN ('DRAFT', 'REJECTED_BY_CHECKER') THEN
            RAISE EXCEPTION 'GSTR-3B filing % in status % is not permitted for legacy maker claim (42501).', p_filing_id, v_filing_status
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

        v_msg := p_org_id::text || ':' || p_filing_id::text || ':' || p_maker_id::text || ':LEGACY_MAKER_CLAIM:' || p_reason || ':' || p_nonce;
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

        BEGIN
            INSERT INTO public.gstr3b_legacy_maker_claims (
                filing_id, org_id, original_maker_state, provenance_type,
                claimed_by_user_id, claim_reason, attestation_nonce
            ) VALUES (
                p_filing_id, p_org_id, 'UNKNOWN_LEGACY_NULL', 'LEGACY_SUBMISSION_CLAIM',
                p_maker_id, p_reason, p_nonce
            );
        EXCEPTION WHEN unique_violation THEN
            RAISE EXCEPTION 'Duplicate legacy maker claim for filing % rejected (42501).', p_filing_id
                USING ERRCODE = '42501';
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
        WHERE id = p_filing_id
          AND org_id = p_org_id
          AND maker_id IS NULL
          AND filing_status IN ('DRAFT', 'REJECTED_BY_CHECKER');

        GET DIAGNOSTICS v_rows_updated = ROW_COUNT;
        IF v_rows_updated != 1 THEN
            RAISE EXCEPTION 'Legacy maker claim update failed: expected 1 row updated, got % (42501).', v_rows_updated
                USING ERRCODE = '42501';
        END IF;

        DELETE FROM public.gstr3b_verified_review_tokens
        WHERE tx_id = txid_current() AND pid = pg_backend_pid() AND filing_id = p_filing_id;
    END;
    $$;

    REVOKE ALL ON FUNCTION public.claim_legacy_null_maker(uuid, uuid, uuid, text, text, text) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION public.claim_legacy_null_maker(uuid, uuid, uuid, text, text, text) TO app_service_user;
    """)

    # 3. Upgrade execute_gstr3b_prepare_upsert to bind all persisted metadata and use server clock_timestamp()
    op.execute("""
    DROP FUNCTION IF EXISTS public.execute_gstr3b_prepare_upsert(uuid, uuid, uuid, text, text, text, text, uuid, text, integer, text, timestamptz, text, text, text);

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
        p_payload_digest text,
        p_meta_digest text,
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
        v_computed_meta_digest text;
        v_msg text;
        v_expected text;
        v_diff integer := 0;
        v_i integer;
        v_len integer;
        v_reg_ok boolean;
        v_maker_ok boolean;
    BEGIN
        IF p_id IS NULL OR p_org_id IS NULL OR p_reg_id IS NULL OR NULLIF(p_tax_period, '') IS NULL
           OR p_maker_id IS NULL OR p_rev IS NULL OR NULLIF(p_payload_digest, '') IS NULL
           OR NULLIF(p_meta_digest, '') IS NULL OR NULLIF(p_nonce, '') IS NULL OR NULLIF(p_signature, '') IS NULL THEN
            RAISE EXCEPTION 'Missing required GSTR-3B creation parameters or server attestation proof (42501).'
                USING ERRCODE = '42501';
        END IF;

        SELECT EXISTS (
            SELECT 1 FROM public.gst_registrations
            WHERE public.gst_registrations.id = p_reg_id AND public.gst_registrations.org_id = p_org_id
        ) INTO v_reg_ok;
        IF NOT v_reg_ok THEN
            RAISE EXCEPTION 'Invalid GST registration % for organization % (42501).', p_reg_id, p_org_id
                USING ERRCODE = '42501';
        END IF;

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

        v_computed_digest := encode(
            public.digest(convert_to(p_t31_text || ':' || p_t4_text || ':' || p_offset_text, 'UTF8'), 'sha256'),
            'hex'
        );
        IF v_computed_digest IS DISTINCT FROM p_payload_digest THEN
            RAISE EXCEPTION 'Financial snapshot digest mismatch (42501).'
                USING ERRCODE = '42501';
        END IF;

        v_computed_meta_digest := encode(
            public.digest(convert_to(p_rev::text || ':' || COALESCE(p_maker_remarks, '') || ':' || COALESCE(p_prev_snap_text, ''), 'UTF8'), 'sha256'),
            'hex'
        );
        IF v_computed_meta_digest IS DISTINCT FROM p_meta_digest THEN
            RAISE EXCEPTION 'Creation metadata digest mismatch (42501).'
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
                 p_maker_id::text || ':DRAFT:' || p_payload_digest || ':' || p_meta_digest || ':' || p_nonce;
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

        BEGIN
            INSERT INTO public.gstr3b_review_attestation_nonces (nonce, filing_id, checker_id)
            VALUES (p_nonce, p_id, p_maker_id);
        EXCEPTION WHEN unique_violation THEN
            RAISE EXCEPTION 'Replayed creation attestation nonce rejected (42501).'
                USING ERRCODE = '42501';
        END;

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
            p_maker_remarks, p_rev, CASE WHEN p_prev_snap_text IS NULL THEN NULL ELSE p_prev_snap_text::jsonb END, clock_timestamp()
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
            updated_at = clock_timestamp()
        WHERE public.gstr3b_filings.filing_status IN ('DRAFT', 'REJECTED_BY_CHECKER')
        RETURNING public.gstr3b_filings.id, public.gstr3b_filings.filing_status, public.gstr3b_filings.revision_number;

        DELETE FROM public.gstr3b_verified_review_tokens
        WHERE tx_id = txid_current() AND pid = pg_backend_pid() AND public.gstr3b_verified_review_tokens.filing_id = p_id;
    END;
    $$;

    REVOKE ALL ON FUNCTION public.execute_gstr3b_prepare_upsert(uuid, uuid, uuid, text, text, text, text, uuid, text, integer, text, text, text, text, text) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION public.execute_gstr3b_prepare_upsert(uuid, uuid, uuid, text, text, text, text, uuid, text, integer, text, text, text, text, text) TO app_service_user;

    -- 4. Constant-time trust-anchor verification helper (does not expose hmac_secret)
    CREATE OR REPLACE FUNCTION public.verify_gstr3b_trust_anchor_match(
        p_expected_digest text
    ) RETURNS boolean
    LANGUAGE plpgsql
    SECURITY DEFINER
    SET search_path = public, pg_catalog
    AS $$
    DECLARE
        v_secret text;
        v_diff integer := 0;
        v_i integer;
        v_len integer;
    BEGIN
        IF NULLIF(p_expected_digest, '') IS NULL THEN
            RETURN false;
        END IF;
        SELECT hmac_secret INTO v_secret FROM public.gstr3b_review_trust_anchors WHERE id = 'default';
        IF v_secret IS NULL OR length(p_expected_digest) != length(v_secret) THEN
            RETURN false;
        END IF;
        v_len := length(v_secret);
        FOR v_i IN 1..v_len LOOP
            IF substr(p_expected_digest, v_i, 1) != substr(v_secret, v_i, 1) THEN
                v_diff := 1;
            END IF;
        END LOOP;
        RETURN v_diff = 0;
    END;
    $$;

    REVOKE ALL ON FUNCTION public.verify_gstr3b_trust_anchor_match(text) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION public.verify_gstr3b_trust_anchor_match(text) TO app_service_user;
    """)


def downgrade() -> None:
    # 1. Refuse downgrade if recorded legacy Maker provenance audit evidence exists
    op.execute("""
    DO $$
    DECLARE
        v_claim_count bigint;
    BEGIN
        IF EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = 'gstr3b_legacy_maker_claims'
        ) THEN
            SELECT count(*) INTO v_claim_count FROM public.gstr3b_legacy_maker_claims;
            IF v_claim_count > 0 THEN
                RAISE EXCEPTION 'Refusing to downgrade migration 0039: % legacy maker claim provenance record(s) exist in public.gstr3b_legacy_maker_claims. Export or archive audit evidence before rolling back (42501).', v_claim_count
                    USING ERRCODE = '42501';
            END IF;
        END IF;
    END $$;

    DROP FUNCTION IF EXISTS public.verify_gstr3b_trust_anchor_match(text);
    DROP TRIGGER IF EXISTS trg_prevent_gstr3b_legacy_claim_mutations ON public.gstr3b_legacy_maker_claims;
    DROP FUNCTION IF EXISTS public.enforce_gstr3b_legacy_claim_immutability();
    DROP TABLE IF EXISTS public.gstr3b_legacy_maker_claims CASCADE;

    DROP FUNCTION IF EXISTS public.claim_legacy_null_maker(uuid, uuid, uuid, text, text, text);
    DROP FUNCTION IF EXISTS public.execute_gstr3b_prepare_upsert(uuid, uuid, uuid, text, text, text, text, uuid, text, integer, text, text, text, text, text);

    -- 2. Restore exact Phase B.7 (0038) execute_gstr3b_prepare_upsert definition
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

        SELECT EXISTS (
            SELECT 1 FROM public.gst_registrations
            WHERE public.gst_registrations.id = p_reg_id AND public.gst_registrations.org_id = p_org_id
        ) INTO v_reg_ok;
        IF NOT v_reg_ok THEN
            RAISE EXCEPTION 'Invalid GST registration % for organization % (42501).', p_reg_id, p_org_id
                USING ERRCODE = '42501';
        END IF;

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

        BEGIN
            INSERT INTO public.gstr3b_review_attestation_nonces (nonce, filing_id, checker_id)
            VALUES (p_nonce, p_id, p_maker_id);
        EXCEPTION WHEN unique_violation THEN
            RAISE EXCEPTION 'Replayed creation attestation nonce rejected (42501).'
                USING ERRCODE = '42501';
        END;

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

    -- 3. Restore exact Phase B.7 (0038) 5-argument claim_legacy_null_maker definition
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
