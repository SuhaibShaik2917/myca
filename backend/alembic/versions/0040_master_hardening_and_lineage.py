"""Master Hardening & Schema Lineage: Filing Modes, Auth Revocation, Scheme Catalog Seed, MSME Attributes, Least-Privilege Cleanup, and Composite Indexes

Revision ID: 0040_master_hardening_and_lineage
Revises: 0039_gstr3b_legacy_claim_meta
Create Date: 2026-11-26 03:45:00.000000
"""
from typing import Sequence, Union
import uuid
from alembic import op
from sqlalchemy import text

revision: str = '0040_master_hardening'
down_revision: Union[str, None] = '0039_gstr3b_legacy_claim_meta'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_SCHEMES_SEED = [
    (
        "PMMY-MUDRA",
        "Pradhan Mantri Mudra Yojana (PMMY)",
        "Ministry of Finance",
        "credit_guarantee",
        "Refinance and credit facility up to ₹10 Lakh for micro/small non-corporate, non-farm enterprises across Shishu (up to ₹50k), Kishore (₹50k to ₹5L), and Tarun (₹5L to ₹10L).",
        "Collateral-free institutional credit up to ₹10 Lakh with subsidized interest rates and zero processing fees for Shishu category.",
        "1000000.00",
        [
            ("annual_turnover", "<=", "50000000.00", "Annual turnover must not exceed ₹5 Crore (Micro classification)"),
            ("industry_sector", "IN", "Manufacturing, Services, Trading, Retail", "Enterprise must operate in non-farm manufacturing, services, or trade"),
        ],
    ),
    (
        "CGTMSE",
        "Credit Guarantee Fund Trust for Micro and Small Enterprises",
        "Ministry of MSME",
        "credit_guarantee",
        "Collateral-free credit facility up to ₹200 Lakh (₹2 Crore) for new and existing Micro and Small Enterprises backed by 75% to 85% credit guarantee cover.",
        "Access to bank credit without offering third-party collateral or personal asset guarantees. Guarantee fee subsidized for women and micro units.",
        "20000000.00",
        [
            ("turnover_bracket", "IN", "Micro (< 5 Cr), Small (5-50 Cr)", "Enterprise must be classified as Micro or Small under the MSMED Act"),
            ("industry_sector", "IN", "Manufacturing, Services", "Eligible for manufacturing and service sectors (retail trade covered up to ₹1 Cr)"),
            ("has_udyam", "=", "true", "Valid Udyam Registration Certificate is mandatory"),
        ],
    ),
    (
        "PMEGP",
        "Prime Minister's Employment Generation Programme",
        "Ministry of MSME",
        "subsidy",
        "Credit-linked subsidy programme to generate self-employment. Provides 15% to 35% margin money subsidy on project cost up to ₹50 Lakh for manufacturing and ₹20 Lakh for services.",
        "Direct government grant (margin money) up to 35% of project cost deposited in beneficiary escrow account with 3-year lock-in.",
        "1750000.00",
        [
            ("business_type", "IN", "Proprietorship, Partnership, Self Help Group", "Eligible for individual proprietors, partnership firms, and SHGs"),
            ("industry_sector", "IN", "Manufacturing, Services", "Applicable for new manufacturing or service greenfield projects"),
            ("vintage_years", "<=", "3", "Prioritized for new or recently established business ventures"),
        ],
    ),
    (
        "PM-SVANIDHI",
        "PM Street Vendor's AtmaNirbhar Nidhi",
        "Ministry of Housing and Urban Affairs",
        "subsidy",
        "Special micro-credit facility providing affordable working capital loans of ₹10,000 (1st tranche), ₹20,000 (2nd tranche), and ₹50,000 (3rd tranche) with 7% interest subsidy.",
        "Collateral-free working capital loan, 7% annual interest subsidy credited quarterly, and cash-back rewards up to ₹1,200/year on digital transactions.",
        "50000.00",
        [
            ("annual_turnover", "<=", "1500000.00", "Applicable for micro enterprises with annual revenue under ₹15 Lakh"),
            ("industry_sector", "IN", "Retail, Services, Trading", "Vending or micro trade in urban or peri-urban localities"),
        ],
    ),
    (
        "STAND-UP-INDIA",
        "Stand-Up India Scheme for Greenfield Ventures",
        "Ministry of Finance",
        "credit_guarantee",
        "Bank loans between ₹10 Lakh and ₹1 Crore to at least one Scheduled Caste (SC) or Scheduled Tribe (ST) borrower and at least one woman borrower per bank branch.",
        "Composite loan covering term loan and working capital up to 85% of project cost with margin money requirements as low as 15%.",
        "10000000.00",
        [
            ("social_category", "IN", "SC, ST, Women", "Borrower must be SC, ST, or a Woman entrepreneur (or hold >= 51% stake)"),
            ("vintage_years", "<=", "2", "Applicable exclusively to Greenfield enterprises (first-time venture in sector)"),
            ("industry_sector", "IN", "Manufacturing, Services, Trading", "Eligible in manufacturing, services, agri-allied, or trading sectors"),
        ],
    ),
    (
        "MSME-ZED",
        "MSME Sustainable (ZED) Certification Scheme",
        "Ministry of MSME",
        "subsidy",
        "Provides financial support up to 80% subsidy on certification costs across Bronze, Silver, and Gold levels to promote Zero Defect, Zero Effect manufacturing.",
        "Up to 80% subsidy for Micro (60% Small, 50% Medium) on ZED assessment fee, plus ₹5 Lakh financial assistance for technology upgradation and green testing.",
        "500000.00",
        [
            ("industry_sector", "IN", "Manufacturing", "Must be an active manufacturing unit"),
            ("has_udyam", "=", "true", "Must possess a valid Udyam Registration"),
            ("turnover_bracket", "IN", "Micro (< 5 Cr), Small (5-50 Cr), Medium (50-250 Cr)", "Must qualify as Micro, Small, or Medium manufacturing enterprise"),
        ],
    ),
]


def upgrade() -> None:
    # 1. Persistent Sandbox vs Live Portal Discrimination on gstr3b_filings and gst_records
    op.execute("""
    ALTER TABLE public.gstr3b_filings
        ADD COLUMN IF NOT EXISTS filing_mode varchar(24) NOT NULL DEFAULT 'SANDBOX',
        ADD COLUMN IF NOT EXISTS is_live_portal_filing boolean NOT NULL DEFAULT false,
        ADD COLUMN IF NOT EXISTS gsp_reference_id text;

    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint WHERE conname = 'chk_gstr3b_filings_mode_consistent'
        ) THEN
            ALTER TABLE public.gstr3b_filings
                ADD CONSTRAINT chk_gstr3b_filings_mode_consistent CHECK (
                    filing_mode IN ('SANDBOX', 'LIVE_PORTAL')
                    AND (
                        (filing_mode = 'SANDBOX' AND is_live_portal_filing = false)
                        OR (filing_mode = 'LIVE_PORTAL' AND is_live_portal_filing = true)
                    )
                );
        END IF;
    END $$;

    ALTER TABLE public.gst_records
        ADD COLUMN IF NOT EXISTS filing_mode varchar(24) NOT NULL DEFAULT 'SANDBOX',
        ADD COLUMN IF NOT EXISTS is_live_portal_filing boolean NOT NULL DEFAULT false,
        ADD COLUMN IF NOT EXISTS gsp_reference_id text,
        ADD COLUMN IF NOT EXISTS gstr3b_filing_id uuid REFERENCES public.gstr3b_filings(id) ON DELETE SET NULL,
        ADD COLUMN IF NOT EXISTS registration_id uuid REFERENCES public.gst_registrations(id) ON DELETE SET NULL,
        ADD COLUMN IF NOT EXISTS arn varchar(32);

    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint WHERE conname = 'chk_gst_records_mode_consistent'
        ) THEN
            ALTER TABLE public.gst_records
                ADD CONSTRAINT chk_gst_records_mode_consistent CHECK (
                    filing_mode IN ('SANDBOX', 'LIVE_PORTAL')
                    AND (
                        (filing_mode = 'SANDBOX' AND is_live_portal_filing = false)
                        OR (filing_mode = 'LIVE_PORTAL' AND is_live_portal_filing = true)
                    )
                );
        END IF;
    END $$;

    -- Deduplicate pre-existing rows before adding unique index (safely bypassing 0032 immutability trigger on upgrade)
    DO $$
    BEGIN
        IF EXISTS (
            SELECT 1 FROM pg_trigger t
            JOIN pg_class c ON c.oid = t.tgrelid
            WHERE c.relname = 'gst_records' AND t.tgname = 'trg_prevent_gst_records_filed_mutations'
        ) THEN
            ALTER TABLE public.gst_records DISABLE TRIGGER trg_prevent_gst_records_filed_mutations;
        END IF;

        DELETE FROM public.gst_records a
        USING public.gst_records b
        WHERE a.ctid < b.ctid
          AND a.org_id = b.org_id
          AND a.gstin = b.gstin
          AND a.return_type = b.return_type
          AND a.tax_period = b.tax_period
          AND a.filing_mode = b.filing_mode;

        IF EXISTS (
            SELECT 1 FROM pg_trigger t
            JOIN pg_class c ON c.oid = t.tgrelid
            WHERE c.relname = 'gst_records' AND t.tgname = 'trg_prevent_gst_records_filed_mutations'
        ) THEN
            ALTER TABLE public.gst_records ENABLE TRIGGER trg_prevent_gst_records_filed_mutations;
        END IF;
    END $$;

    CREATE UNIQUE INDEX IF NOT EXISTS uq_gst_records_org_gstin_period_mode
        ON public.gst_records(org_id, gstin, return_type, tax_period, filing_mode);
    """)

    # 2. Attested filing transition RPC supporting explicit SANDBOX vs LIVE_PORTAL persistence
    op.execute("""
    CREATE OR REPLACE FUNCTION public.execute_gstr3b_filing_transition_v2(
        p_org_id uuid,
        p_filing_id uuid,
        p_actor_id uuid,
        p_arn text,
        p_cin text,
        p_filing_date timestamptz,
        p_payload_hash text,
        p_nonce text,
        p_signature text,
        p_filing_mode text DEFAULT 'SANDBOX',
        p_gsp_reference_id text DEFAULT NULL
    ) RETURNS void
    LANGUAGE plpgsql
    SECURITY DEFINER
    SET search_path = public, pg_catalog
    AS $$
    DECLARE
        v_mode text;
        v_is_live boolean;
    BEGIN
        v_mode := upper(COALESCE(NULLIF(btrim(p_filing_mode), ''), 'SANDBOX'));
        IF v_mode NOT IN ('SANDBOX', 'LIVE_PORTAL') THEN
            RAISE EXCEPTION 'Invalid filing_mode % (42501).', p_filing_mode USING ERRCODE = '42501';
        END IF;

        IF v_mode = 'LIVE_PORTAL' AND NULLIF(btrim(COALESCE(p_gsp_reference_id, '')), '') IS NULL THEN
            RAISE EXCEPTION 'LIVE_PORTAL filing requires verified gsp_reference_id (42501).' USING ERRCODE = '42501';
        END IF;

        v_is_live := (v_mode = 'LIVE_PORTAL');

        -- Stamp mode while status is still APPROVED_BY_CHECKER before immutable FILED transition
        UPDATE public.gstr3b_filings
        SET filing_mode = v_mode,
            is_live_portal_filing = v_is_live,
            gsp_reference_id = NULLIF(btrim(COALESCE(p_gsp_reference_id, '')), '')
        WHERE id = p_filing_id AND org_id = p_org_id;

        PERFORM public.execute_gstr3b_filing_transition(
            p_org_id,
            p_filing_id,
            p_actor_id,
            p_arn,
            p_cin,
            p_filing_date,
            p_payload_hash,
            p_nonce,
            p_signature
        );
    END;
    $$;

    REVOKE ALL ON FUNCTION public.execute_gstr3b_filing_transition_v2(
        uuid, uuid, uuid, text, text, timestamptz, text, text, text, text, text
    ) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION public.execute_gstr3b_filing_transition_v2(
        uuid, uuid, uuid, text, text, timestamptz, text, text, text, text, text
    ) TO app_service_user;
    """)

    # 3. Enforce global_users.is_active = true in v_org_users security-barrier view
    op.execute("""
    CREATE OR REPLACE VIEW public.v_org_users
    WITH (security_barrier = true)
    AS
    SELECT
        u.id,
        m.org_id,
        u.email,
        u.phone,
        u.full_name,
        m.role,
        m.status AS membership_status
    FROM public.global_users u
    JOIN public.organization_memberships m ON u.id = m.user_id
    WHERE m.org_id = NULLIF(current_setting('app.current_org', true), '')::uuid
      AND m.status = 'active'
      AND u.is_active = true;
    """)

    # 4. JWT Session Revocation Table (Tenant RLS + FORCE RLS)
    op.execute("""
    CREATE TABLE IF NOT EXISTS public.auth_revoked_tokens (
        jti text PRIMARY KEY,
        org_id uuid NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
        user_id uuid NOT NULL REFERENCES public.global_users(id) ON DELETE CASCADE,
        revoked_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        expires_at timestamptz NOT NULL,
        reason text NOT NULL DEFAULT 'logout'
    );
    CREATE INDEX IF NOT EXISTS idx_auth_revoked_tokens_org_exp
        ON public.auth_revoked_tokens(org_id, expires_at);

    ALTER TABLE public.auth_revoked_tokens ENABLE ROW LEVEL SECURITY;
    ALTER TABLE public.auth_revoked_tokens FORCE ROW LEVEL SECURITY;

    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_policies WHERE tablename = 'auth_revoked_tokens' AND policyname = 'auth_revoked_tokens_tenant'
        ) THEN
            CREATE POLICY auth_revoked_tokens_tenant ON public.auth_revoked_tokens
                FOR ALL TO app_service_user
                USING (org_id = NULLIF(current_setting('app.current_org', true), '')::uuid)
                WITH CHECK (org_id = NULLIF(current_setting('app.current_org', true), '')::uuid);
        END IF;

        IF NOT EXISTS (
            SELECT 1 FROM pg_policies WHERE tablename = 'auth_revoked_tokens' AND policyname = 'auth_revoked_tokens_admin'
        ) THEN
            CREATE POLICY auth_revoked_tokens_admin ON public.auth_revoked_tokens
                FOR ALL TO platform_admin
                USING (true) WITH CHECK (true);
        END IF;
    END $$;

    GRANT SELECT, INSERT, DELETE ON public.auth_revoked_tokens TO app_service_user;
    GRANT ALL ON public.auth_revoked_tokens TO platform_admin;
    """)

    # 5. Persist MSME Attributes on public.business_profiles
    op.execute("""
    ALTER TABLE public.business_profiles
        ADD COLUMN IF NOT EXISTS vintage_years integer,
        ADD COLUMN IF NOT EXISTS social_category varchar(32),
        ADD COLUMN IF NOT EXISTS has_udyam boolean NOT NULL DEFAULT false;

    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint WHERE conname = 'chk_business_profiles_vintage_nonneg'
        ) THEN
            ALTER TABLE public.business_profiles
                ADD CONSTRAINT chk_business_profiles_vintage_nonneg
                CHECK (vintage_years IS NULL OR vintage_years >= 0);
        END IF;
    END $$;

    UPDATE public.business_profiles
    SET has_udyam = true
    WHERE udyam_number IS NOT NULL AND btrim(udyam_number) <> '' AND has_udyam = false;
    """)

    # 6. Tighten 0017 PUBLIC grants + grant SELECT on alembic_version for /ready probe
    op.execute("""
    REVOKE ALL ON public.receipt_remediation_audit FROM PUBLIC;
    GRANT SELECT, INSERT, UPDATE ON public.receipt_remediation_audit TO app_service_user;
    GRANT ALL ON public.receipt_remediation_audit TO platform_admin;
    DROP POLICY IF EXISTS receipt_remediation_audit_tenant ON public.receipt_remediation_audit;
    CREATE POLICY receipt_remediation_audit_tenant ON public.receipt_remediation_audit
        FOR ALL TO app_service_user
        USING (org_id = NULLIF(current_setting('app.current_org', true), '')::uuid)
        WITH CHECK (org_id = NULLIF(current_setting('app.current_org', true), '')::uuid);

    REVOKE ALL ON public.goods_receipt_lot_ledger FROM PUBLIC;
    GRANT SELECT, INSERT, UPDATE ON public.goods_receipt_lot_ledger TO app_service_user;
    GRANT ALL ON public.goods_receipt_lot_ledger TO platform_admin;
    DROP POLICY IF EXISTS goods_receipt_lot_tenant ON public.goods_receipt_lot_ledger;
    CREATE POLICY goods_receipt_lot_tenant ON public.goods_receipt_lot_ledger
        FOR ALL TO app_service_user
        USING (org_id = NULLIF(current_setting('app.current_org', true), '')::uuid)
        WITH CHECK (org_id = NULLIF(current_setting('app.current_org', true), '')::uuid);

    GRANT SELECT ON public.alembic_version TO app_service_user;
    """)

    # 7. Composite Indexes for Bounded Query Performance
    op.execute("""
    CREATE INDEX IF NOT EXISTS idx_invoices_org_status_date
        ON public.invoices(org_id, status, invoice_date);
    CREATE INDEX IF NOT EXISTS idx_gstr3b_legacy_maker_claims_org_claimed
        ON public.gstr3b_legacy_maker_claims(org_id, claimed_at);
    CREATE INDEX IF NOT EXISTS idx_purchase_docs_org_reg_period
        ON public.purchase_documents(org_id, registration_id, financial_period);
    """)

    # 8. Idempotently seed Government Schemes & Rules in Migration DDL
    conn = op.get_bind()
    for code, name, ministry, category, desc, benefits, max_sub, rules in _SCHEMES_SEED:
        scheme_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"myca.scheme.{code}"))
        existing_id = conn.execute(
            text("SELECT id::text FROM public.government_schemes WHERE scheme_code = :code"),
            {"code": code},
        ).scalar()
        if not existing_id:
            conn.execute(
                text(
                    """
                    INSERT INTO public.government_schemes (
                        id, scheme_code, name, ministry, category, description,
                        benefits_summary, max_subsidy_amount, is_active, created_at, updated_at
                    ) VALUES (
                        CAST(:id AS uuid), :code, :name, :ministry, CAST(:category AS scheme_category),
                        :desc, :benefits, CAST(:max_sub AS numeric), true, clock_timestamp(), clock_timestamp()
                    )
                    ON CONFLICT (scheme_code) DO NOTHING
                    """
                ),
                {
                    "id": scheme_id,
                    "code": code,
                    "name": name,
                    "ministry": ministry,
                    "category": category,
                    "desc": desc,
                    "benefits": benefits,
                    "max_sub": max_sub,
                },
            )
            target_scheme_id = scheme_id
        else:
            target_scheme_id = existing_id

        existing_rule_count = conn.execute(
            text("SELECT count(*) FROM public.scheme_eligibility_rules WHERE scheme_id = CAST(:sid AS uuid)"),
            {"sid": target_scheme_id},
        ).scalar() or 0
        if existing_rule_count == 0:
            for r_idx, (r_key, op_str, target_val, r_desc) in enumerate(rules):
                rule_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"myca.scheme.{code}.rule.{r_idx}.{r_key}"))
                conn.execute(
                    text(
                        """
                        INSERT INTO public.scheme_eligibility_rules (
                            id, scheme_id, rule_key, operator, target_value, rule_description, created_at
                        ) VALUES (
                            CAST(:rid AS uuid), CAST(:sid AS uuid), :rkey, :op, :tval, :rdesc, clock_timestamp()
                        )
                        ON CONFLICT DO NOTHING
                        """
                    ),
                    {
                        "rid": rule_id,
                        "sid": target_scheme_id,
                        "rkey": r_key,
                        "op": op_str,
                        "tval": target_val,
                        "rdesc": r_desc,
                    },
                )


def downgrade() -> None:
    conn = op.get_bind()
    for code, *_ in _SCHEMES_SEED:
        seeded_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"myca.scheme.{code}"))
        conn.execute(
            text("DELETE FROM public.government_schemes WHERE id = CAST(:sid AS uuid);"),
            {"sid": seeded_id},
        )

    op.execute("""
    DROP INDEX IF EXISTS public.idx_purchase_docs_org_reg_period;
    DROP INDEX IF EXISTS public.idx_gstr3b_legacy_maker_claims_org_claimed;
    DROP INDEX IF EXISTS public.idx_invoices_org_status_date;

    REVOKE SELECT ON public.alembic_version FROM app_service_user;

    -- Restore 0017 grants/policies cleanly
    DROP POLICY IF EXISTS goods_receipt_lot_tenant ON public.goods_receipt_lot_ledger;
    CREATE POLICY goods_receipt_lot_tenant ON public.goods_receipt_lot_ledger
        FOR ALL TO public
        USING (org_id = (NULLIF(current_setting('app.current_org', true), ''))::uuid)
        WITH CHECK (org_id = (NULLIF(current_setting('app.current_org', true), ''))::uuid);
    REVOKE ALL ON public.goods_receipt_lot_ledger FROM app_service_user, platform_admin;
    GRANT ALL ON public.goods_receipt_lot_ledger TO postgres, public;

    DROP POLICY IF EXISTS receipt_remediation_audit_tenant ON public.receipt_remediation_audit;
    CREATE POLICY receipt_remediation_audit_tenant ON public.receipt_remediation_audit
        FOR ALL TO public
        USING (org_id = (NULLIF(current_setting('app.current_org', true), ''))::uuid)
        WITH CHECK (org_id = (NULLIF(current_setting('app.current_org', true), ''))::uuid);
    REVOKE ALL ON public.receipt_remediation_audit FROM app_service_user, platform_admin;
    GRANT ALL ON public.receipt_remediation_audit TO postgres, public;

    ALTER TABLE public.business_profiles DROP CONSTRAINT IF EXISTS chk_business_profiles_vintage_nonneg;
    ALTER TABLE public.business_profiles
        DROP COLUMN IF EXISTS has_udyam,
        DROP COLUMN IF EXISTS social_category,
        DROP COLUMN IF EXISTS vintage_years;

    DROP POLICY IF EXISTS auth_revoked_tokens_admin ON public.auth_revoked_tokens;
    DROP POLICY IF EXISTS auth_revoked_tokens_tenant ON public.auth_revoked_tokens;
    DROP TABLE IF EXISTS public.auth_revoked_tokens;

    CREATE OR REPLACE VIEW public.v_org_users
    WITH (security_barrier = true)
    AS
    SELECT
        u.id,
        m.org_id,
        u.email,
        u.phone,
        u.full_name,
        m.role,
        m.status AS membership_status
    FROM public.global_users u
    JOIN public.organization_memberships m ON u.id = m.user_id
    WHERE m.org_id = NULLIF(current_setting('app.current_org', true), '')::uuid
      AND m.status = 'active';

    DROP FUNCTION IF EXISTS public.execute_gstr3b_filing_transition_v2(
        uuid, uuid, uuid, text, text, timestamptz, text, text, text, text, text
    );

    DROP INDEX IF EXISTS public.uq_gst_records_org_gstin_period_mode;
    ALTER TABLE public.gst_records DROP CONSTRAINT IF EXISTS chk_gst_records_mode_consistent;
    ALTER TABLE public.gst_records
        DROP COLUMN IF EXISTS arn,
        DROP COLUMN IF EXISTS registration_id,
        DROP COLUMN IF EXISTS gstr3b_filing_id,
        DROP COLUMN IF EXISTS gsp_reference_id,
        DROP COLUMN IF EXISTS is_live_portal_filing,
        DROP COLUMN IF EXISTS filing_mode;

    ALTER TABLE public.gstr3b_filings DROP CONSTRAINT IF EXISTS chk_gstr3b_filings_mode_consistent;
    ALTER TABLE public.gstr3b_filings
        DROP COLUMN IF EXISTS gsp_reference_id,
        DROP COLUMN IF EXISTS is_live_portal_filing,
        DROP COLUMN IF EXISTS filing_mode;
    """)
