"""Milestone 2 Foundation: Explicit Statutory Classifications, Multi-Registration GSTR-2B, Purchase Register, Deterministic Reconciliation, and ITC Risk Engine

Revision ID: 0011_m2_foundation
Revises: 0010_m1_3_security_provenance
Create Date: 2026-11-18 01:10:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '0011_m2_foundation'
down_revision: Union[str, None] = '0010_m1_3_security_provenance'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -------------------------------------------------------------------------
    # 1. Priority 1: Add Explicit Statutory Classification Fields to Invoices
    # -------------------------------------------------------------------------
    op.execute("""
    ALTER TABLE public.invoices
        ADD COLUMN IF NOT EXISTS supply_category text NOT NULL DEFAULT 'B2B_REGULAR',
        ADD COLUMN IF NOT EXISTS is_sez_supply boolean NOT NULL DEFAULT false,
        ADD COLUMN IF NOT EXISTS is_deemed_export boolean NOT NULL DEFAULT false,
        ADD COLUMN IF NOT EXISTS is_reverse_charge boolean NOT NULL DEFAULT false,
        ADD COLUMN IF NOT EXISTS document_type text NOT NULL DEFAULT 'invoice',
        ADD COLUMN IF NOT EXISTS recipient_registration_type text DEFAULT 'regular',
        ADD COLUMN IF NOT EXISTS classification_provenance text NOT NULL DEFAULT 'source_document';

    DO $$
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_invoices_doc_type') THEN
            ALTER TABLE public.invoices
            ADD CONSTRAINT chk_invoices_doc_type CHECK (
                document_type IN ('invoice', 'credit_note', 'debit_note', 'revised_invoice', 'revised_credit_note', 'revised_debit_note')
            );
        END IF;

        IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_invoices_supply_category') THEN
            ALTER TABLE public.invoices
            ADD CONSTRAINT chk_invoices_supply_category CHECK (
                supply_category IN (
                    'B2B_REGULAR', 'B2B_REVERSE_CHARGE', 'SEZ_WITH_PAYMENT', 'SEZ_WITHOUT_PAYMENT',
                    'DEEMED_EXPORT', 'B2C_LARGE', 'B2C_SMALL', 'NIL_EXEMPT', 'ECO_SUPPLY', 'ECO_AMENDMENT'
                )
            );
        END IF;
    END
    $$;
    """)

    # -------------------------------------------------------------------------
    # 2. Multi-Registration GST Scoping under PAN
    # -------------------------------------------------------------------------
    op.execute("""
    CREATE TABLE IF NOT EXISTS public.gst_registrations (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id uuid NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
        gstin text NOT NULL CHECK (gstin ~ '^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$'),
        state_code text NOT NULL,
        trade_name text,
        legal_name text NOT NULL,
        registration_type text NOT NULL DEFAULT 'regular',
        is_primary boolean NOT NULL DEFAULT false,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        CONSTRAINT uq_gst_registrations_org_gstin UNIQUE (org_id, gstin),
        CONSTRAINT uq_gst_registrations_id_org UNIQUE (id, org_id)
    );

    ALTER TABLE public.gst_registrations ENABLE ROW LEVEL SECURITY;
    CREATE POLICY gst_reg_tenant_isolation ON public.gst_registrations
        FOR ALL TO app_service_user
        USING (org_id = (NULLIF(current_setting('app.current_org', true), ''))::uuid)
        WITH CHECK (org_id = (NULLIF(current_setting('app.current_org', true), ''))::uuid);

    CREATE POLICY gst_reg_platform_admin ON public.gst_registrations
        FOR ALL TO platform_admin USING (true) WITH CHECK (true);
    """)

    # -------------------------------------------------------------------------
    # 3. GSTR-2B Ingestion & Immutable Statement Snapshots
    # -------------------------------------------------------------------------
    op.execute("""
    CREATE TABLE IF NOT EXISTS public.gstr2b_imports (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id uuid NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
        registration_id uuid NOT NULL,
        recipient_gstin text NOT NULL,
        return_period text NOT NULL CHECK (return_period ~ '^\d{4}-\d{2}$'),
        financial_year text NOT NULL,
        file_name text NOT NULL,
        file_type text NOT NULL CHECK (file_type IN ('json', 'excel', 'csv')),
        file_hash text NOT NULL,
        file_size_bytes bigint NOT NULL,
        parser_version text NOT NULL DEFAULT 'v2.1',
        import_status text NOT NULL DEFAULT 'completed',
        error_report jsonb,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        CONSTRAINT uq_gstr2b_imports_org_hash UNIQUE (org_id, file_hash),
        FOREIGN KEY (registration_id, org_id) REFERENCES public.gst_registrations(id, org_id) ON DELETE CASCADE,
        CONSTRAINT uq_gstr2b_imports_id_org UNIQUE (id, org_id)
    );

    ALTER TABLE public.gstr2b_imports ENABLE ROW LEVEL SECURITY;
    CREATE POLICY gstr2b_imports_tenant ON public.gstr2b_imports
        FOR ALL TO app_service_user
        USING (org_id = (NULLIF(current_setting('app.current_org', true), ''))::uuid)
        WITH CHECK (org_id = (NULLIF(current_setting('app.current_org', true), ''))::uuid);
    CREATE POLICY gstr2b_imports_admin ON public.gstr2b_imports
        FOR ALL TO platform_admin USING (true) WITH CHECK (true);

    CREATE TABLE IF NOT EXISTS public.gstr2b_snapshots (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id uuid NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
        import_id uuid NOT NULL,
        registration_id uuid NOT NULL,
        recipient_gstin text NOT NULL,
        return_period text NOT NULL,
        version_number int NOT NULL DEFAULT 1,
        previous_snapshot_id uuid,
        total_itc_available numeric(15, 2) NOT NULL DEFAULT 0.00,
        total_itc_not_available numeric(15, 2) NOT NULL DEFAULT 0.00,
        generation_date timestamptz,
        is_active boolean NOT NULL DEFAULT true,
        raw_summary jsonb,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        FOREIGN KEY (import_id, org_id) REFERENCES public.gstr2b_imports(id, org_id) ON DELETE CASCADE,
        FOREIGN KEY (registration_id, org_id) REFERENCES public.gst_registrations(id, org_id) ON DELETE CASCADE,
        FOREIGN KEY (previous_snapshot_id, org_id) REFERENCES public.gstr2b_snapshots(id, org_id) ON DELETE SET NULL,
        CONSTRAINT uq_gstr2b_snapshots_org_period_ver UNIQUE (org_id, registration_id, return_period, version_number),
        CONSTRAINT uq_gstr2b_snapshots_id_org UNIQUE (id, org_id)
    );

    ALTER TABLE public.gstr2b_snapshots ENABLE ROW LEVEL SECURITY;
    CREATE POLICY gstr2b_snapshots_tenant ON public.gstr2b_snapshots
        FOR ALL TO app_service_user
        USING (org_id = (NULLIF(current_setting('app.current_org', true), ''))::uuid)
        WITH CHECK (org_id = (NULLIF(current_setting('app.current_org', true), ''))::uuid);
    CREATE POLICY gstr2b_snapshots_admin ON public.gstr2b_snapshots
        FOR ALL TO platform_admin USING (true) WITH CHECK (true);

    CREATE TABLE IF NOT EXISTS public.gstr2b_source_documents (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id uuid NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
        snapshot_id uuid NOT NULL,
        section_name text NOT NULL,
        itc_availability text NOT NULL CHECK (itc_availability IN ('available', 'not_available')),
        source_form text NOT NULL,
        supplier_gstin text,
        supplier_name text,
        document_type text NOT NULL,
        document_number text NOT NULL,
        document_date date NOT NULL,
        invoice_value numeric(15, 2) NOT NULL DEFAULT 0.00,
        place_of_supply text NOT NULL,
        supply_attract_reverse_charge boolean NOT NULL DEFAULT false,
        taxable_value numeric(15, 2) NOT NULL DEFAULT 0.00,
        integrated_tax numeric(15, 2) NOT NULL DEFAULT 0.00,
        central_tax numeric(15, 2) NOT NULL DEFAULT 0.00,
        state_ut_tax numeric(15, 2) NOT NULL DEFAULT 0.00,
        cess_amount numeric(15, 2) NOT NULL DEFAULT 0.00,
        applicable_percentage_rate numeric(5, 2),
        source_gstr1_filing_date date,
        source_gstr1_filing_period text,
        boe_number text,
        boe_date date,
        port_code text,
        is_amended boolean NOT NULL DEFAULT false,
        original_document_number text,
        original_document_date date,
        raw_payload jsonb,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        FOREIGN KEY (snapshot_id, org_id) REFERENCES public.gstr2b_snapshots(id, org_id) ON DELETE CASCADE,
        CONSTRAINT uq_gstr2b_docs_id_org UNIQUE (id, org_id)
    );

    ALTER TABLE public.gstr2b_source_documents ENABLE ROW LEVEL SECURITY;
    CREATE POLICY gstr2b_docs_tenant ON public.gstr2b_source_documents
        FOR ALL TO app_service_user
        USING (org_id = (NULLIF(current_setting('app.current_org', true), ''))::uuid)
        WITH CHECK (org_id = (NULLIF(current_setting('app.current_org', true), ''))::uuid);
    CREATE POLICY gstr2b_docs_admin ON public.gstr2b_source_documents
        FOR ALL TO platform_admin USING (true) WITH CHECK (true);
    """)

    # -------------------------------------------------------------------------
    # 4. IMS (Invoice Management System) Action Tracking
    # -------------------------------------------------------------------------
    op.execute("""
    CREATE TABLE IF NOT EXISTS public.ims_document_actions (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id uuid NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
        gstr2b_doc_id uuid NOT NULL,
        supplier_doc_reference text NOT NULL,
        current_action text NOT NULL CHECK (current_action IN ('ACCEPTED', 'REJECTED', 'PENDING', 'NO_ACTION', 'DEEMED_ACCEPTED', 'NOT_AVAILABLE_IN_IMS')),
        previous_action text,
        action_timestamp timestamptz NOT NULL DEFAULT clock_timestamp(),
        action_by uuid REFERENCES public.global_users(id) ON DELETE SET NULL,
        gstr2b_return_period text NOT NULL,
        is_frozen_by_gstr3b boolean NOT NULL DEFAULT false,
        gstr3b_filing_date timestamptz,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        FOREIGN KEY (gstr2b_doc_id, org_id) REFERENCES public.gstr2b_source_documents(id, org_id) ON DELETE CASCADE,
        CONSTRAINT uq_ims_doc_actions_org_doc UNIQUE (org_id, gstr2b_doc_id)
    );

    ALTER TABLE public.ims_document_actions ENABLE ROW LEVEL SECURITY;
    CREATE POLICY ims_actions_tenant ON public.ims_document_actions
        FOR ALL TO app_service_user
        USING (org_id = (NULLIF(current_setting('app.current_org', true), ''))::uuid)
        WITH CHECK (org_id = (NULLIF(current_setting('app.current_org', true), ''))::uuid);
    CREATE POLICY ims_actions_admin ON public.ims_document_actions
        FOR ALL TO platform_admin USING (true) WITH CHECK (true);
    """)

    # -------------------------------------------------------------------------
    # 5. Independent Purchase Register & Payment Allocations
    # -------------------------------------------------------------------------
    op.execute("""
    CREATE TABLE IF NOT EXISTS public.purchase_documents (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id uuid NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
        registration_id uuid NOT NULL,
        document_type text NOT NULL CHECK (document_type IN ('tax_invoice', 'debit_note', 'credit_note', 'bill_of_entry', 'isd_document', 'rcm_self_invoice')),
        document_number text NOT NULL,
        document_date date NOT NULL,
        financial_period text NOT NULL,
        supplier_gstin text,
        supplier_legal_name text NOT NULL,
        recipient_gstin text NOT NULL,
        place_of_supply text NOT NULL,
        is_reverse_charge boolean NOT NULL DEFAULT false,
        supply_category text NOT NULL DEFAULT 'B2B_REGULAR',
        total_taxable_amount numeric(15, 2),
        cgst_amount numeric(15, 2),
        sgst_amount numeric(15, 2),
        igst_amount numeric(15, 2),
        cess_amount numeric(15, 2),
        total_invoice_value numeric(15, 2),
        goods_services_type text CHECK (goods_services_type IN ('goods', 'services', 'both', 'capital_goods')),
        goods_receipt_date date,
        goods_receipt_note_ref text,
        purchase_ledger_code text,
        status text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'pending_approval', 'approved', 'rejected')),
        ocr_confidence numeric(4, 3),
        raw_ocr_payload jsonb,
        source_document_id uuid,
        approved_by uuid REFERENCES public.global_users(id) ON DELETE SET NULL,
        approved_at timestamptz,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        FOREIGN KEY (registration_id, org_id) REFERENCES public.gst_registrations(id, org_id) ON DELETE CASCADE,
        FOREIGN KEY (source_document_id, org_id) REFERENCES public.documents(id, org_id) ON DELETE SET NULL (source_document_id),
        CONSTRAINT uq_purchase_docs_org_supp_num_date UNIQUE (org_id, supplier_gstin, document_number, document_date),
        CONSTRAINT uq_purchase_docs_id_org UNIQUE (id, org_id)
    );

    ALTER TABLE public.purchase_documents ENABLE ROW LEVEL SECURITY;
    CREATE POLICY purchase_docs_tenant ON public.purchase_documents
        FOR ALL TO app_service_user
        USING (org_id = (NULLIF(current_setting('app.current_org', true), ''))::uuid)
        WITH CHECK (org_id = (NULLIF(current_setting('app.current_org', true), ''))::uuid);
    CREATE POLICY purchase_docs_admin ON public.purchase_documents
        FOR ALL TO platform_admin USING (true) WITH CHECK (true);

    CREATE TABLE IF NOT EXISTS public.purchase_line_items (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id uuid NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
        purchase_doc_id uuid NOT NULL,
        item_description text NOT NULL,
        hsn_sac_code text,
        quantity numeric(12, 3),
        unit_price numeric(15, 2),
        taxable_amount numeric(15, 2),
        gst_rate numeric(5, 2),
        cgst_amount numeric(15, 2),
        sgst_amount numeric(15, 2),
        igst_amount numeric(15, 2),
        cess_amount numeric(15, 2),
        total_amount numeric(15, 2),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        FOREIGN KEY (purchase_doc_id, org_id) REFERENCES public.purchase_documents(id, org_id) ON DELETE CASCADE,
        CONSTRAINT uq_purchase_lines_id_org UNIQUE (id, org_id)
    );

    ALTER TABLE public.purchase_line_items ENABLE ROW LEVEL SECURITY;
    CREATE POLICY purchase_lines_tenant ON public.purchase_line_items
        FOR ALL TO app_service_user
        USING (org_id = (NULLIF(current_setting('app.current_org', true), ''))::uuid)
        WITH CHECK (org_id = (NULLIF(current_setting('app.current_org', true), ''))::uuid);
    CREATE POLICY purchase_lines_admin ON public.purchase_line_items
        FOR ALL TO platform_admin USING (true) WITH CHECK (true);

    CREATE TABLE IF NOT EXISTS public.purchase_payments (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id uuid NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
        payment_reference text NOT NULL,
        payment_date date NOT NULL,
        payment_mode text NOT NULL,
        bank_reference text,
        total_amount_paid numeric(15, 2) NOT NULL,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        CONSTRAINT uq_purchase_payments_id_org UNIQUE (id, org_id)
    );

    ALTER TABLE public.purchase_payments ENABLE ROW LEVEL SECURITY;
    CREATE POLICY purchase_payments_tenant ON public.purchase_payments
        FOR ALL TO app_service_user
        USING (org_id = (NULLIF(current_setting('app.current_org', true), ''))::uuid)
        WITH CHECK (org_id = (NULLIF(current_setting('app.current_org', true), ''))::uuid);
    CREATE POLICY purchase_payments_admin ON public.purchase_payments
        FOR ALL TO platform_admin USING (true) WITH CHECK (true);

    CREATE TABLE IF NOT EXISTS public.purchase_payment_allocations (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id uuid NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
        payment_id uuid NOT NULL,
        purchase_doc_id uuid NOT NULL,
        allocated_taxable_amount numeric(15, 2) NOT NULL,
        allocated_tax_amount numeric(15, 2) NOT NULL,
        allocated_total_amount numeric(15, 2) NOT NULL,
        allocation_date timestamptz NOT NULL DEFAULT clock_timestamp(),
        FOREIGN KEY (payment_id, org_id) REFERENCES public.purchase_payments(id, org_id) ON DELETE CASCADE,
        FOREIGN KEY (purchase_doc_id, org_id) REFERENCES public.purchase_documents(id, org_id) ON DELETE CASCADE,
        CONSTRAINT uq_pay_allocations_id_org UNIQUE (id, org_id)
    );

    ALTER TABLE public.purchase_payment_allocations ENABLE ROW LEVEL SECURITY;
    CREATE POLICY purchase_pay_alloc_tenant ON public.purchase_payment_allocations
        FOR ALL TO app_service_user
        USING (org_id = (NULLIF(current_setting('app.current_org', true), ''))::uuid)
        WITH CHECK (org_id = (NULLIF(current_setting('app.current_org', true), ''))::uuid);
    CREATE POLICY purchase_pay_alloc_admin ON public.purchase_payment_allocations
        FOR ALL TO platform_admin USING (true) WITH CHECK (true);
    """)

    # -------------------------------------------------------------------------
    # 6. Deterministic Reconciliation Engine Tables
    # -------------------------------------------------------------------------
    op.execute("""
    CREATE TABLE IF NOT EXISTS public.reconciliation_runs (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id uuid NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
        registration_id uuid NOT NULL,
        return_period text NOT NULL,
        snapshot_id uuid NOT NULL,
        engine_version text NOT NULL DEFAULT 'v2.1_deterministic',
        taxable_tolerance numeric(10, 2) NOT NULL DEFAULT 1.00,
        tax_tolerance numeric(10, 2) NOT NULL DEFAULT 1.00,
        total_tolerance numeric(10, 2) NOT NULL DEFAULT 2.00,
        matched_count int NOT NULL DEFAULT 0,
        variance_count int NOT NULL DEFAULT 0,
        missing_in_gstr2b_count int NOT NULL DEFAULT 0,
        missing_in_books_count int NOT NULL DEFAULT 0,
        run_status text NOT NULL DEFAULT 'completed',
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        FOREIGN KEY (registration_id, org_id) REFERENCES public.gst_registrations(id, org_id) ON DELETE CASCADE,
        FOREIGN KEY (snapshot_id, org_id) REFERENCES public.gstr2b_snapshots(id, org_id) ON DELETE CASCADE,
        CONSTRAINT uq_recon_runs_id_org UNIQUE (id, org_id)
    );

    ALTER TABLE public.reconciliation_runs ENABLE ROW LEVEL SECURITY;
    CREATE POLICY recon_runs_tenant ON public.reconciliation_runs
        FOR ALL TO app_service_user
        USING (org_id = (NULLIF(current_setting('app.current_org', true), ''))::uuid)
        WITH CHECK (org_id = (NULLIF(current_setting('app.current_org', true), ''))::uuid);
    CREATE POLICY recon_runs_admin ON public.reconciliation_runs
        FOR ALL TO platform_admin USING (true) WITH CHECK (true);

    CREATE TABLE IF NOT EXISTS public.reconciliation_matches (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id uuid NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
        run_id uuid NOT NULL,
        purchase_doc_id uuid NOT NULL,
        gstr2b_doc_id uuid NOT NULL,
        match_pass int NOT NULL,
        match_score numeric(4, 3) NOT NULL,
        is_exact boolean NOT NULL DEFAULT true,
        taxable_difference numeric(15, 2) NOT NULL DEFAULT 0.00,
        tax_difference numeric(15, 2) NOT NULL DEFAULT 0.00,
        total_difference numeric(15, 2) NOT NULL DEFAULT 0.00,
        match_evidence jsonb NOT NULL,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        FOREIGN KEY (run_id, org_id) REFERENCES public.reconciliation_runs(id, org_id) ON DELETE CASCADE,
        FOREIGN KEY (purchase_doc_id, org_id) REFERENCES public.purchase_documents(id, org_id) ON DELETE CASCADE,
        FOREIGN KEY (gstr2b_doc_id, org_id) REFERENCES public.gstr2b_source_documents(id, org_id) ON DELETE CASCADE,
        CONSTRAINT uq_recon_matches_run_docs UNIQUE (org_id, run_id, purchase_doc_id, gstr2b_doc_id),
        CONSTRAINT uq_recon_matches_id_org UNIQUE (id, org_id)
    );

    ALTER TABLE public.reconciliation_matches ENABLE ROW LEVEL SECURITY;
    CREATE POLICY recon_matches_tenant ON public.reconciliation_matches
        FOR ALL TO app_service_user
        USING (org_id = (NULLIF(current_setting('app.current_org', true), ''))::uuid)
        WITH CHECK (org_id = (NULLIF(current_setting('app.current_org', true), ''))::uuid);
    CREATE POLICY recon_matches_admin ON public.reconciliation_matches
        FOR ALL TO platform_admin USING (true) WITH CHECK (true);

    CREATE TABLE IF NOT EXISTS public.reconciliation_variances (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id uuid NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
        run_id uuid NOT NULL,
        variance_category text NOT NULL CHECK (variance_category IN (
            'MATCHED', 'MISSING_IN_GSTR2B', 'MISSING_IN_BOOKS', 'TAX_MISMATCH',
            'DATE_MISMATCH', 'COUNTERPARTY_MISMATCH', 'AMENDED_BY_SUPPLIER',
            'DUPLICATE_DOCUMENT', 'POSSIBLE_MATCH_REQUIRES_REVIEW',
            'IMPORT_OR_ISD_MISMATCH', 'RCM_MISMATCH'
        )),
        purchase_doc_id uuid,
        gstr2b_doc_id uuid,
        difference_details jsonb NOT NULL,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        FOREIGN KEY (run_id, org_id) REFERENCES public.reconciliation_runs(id, org_id) ON DELETE CASCADE,
        FOREIGN KEY (purchase_doc_id, org_id) REFERENCES public.purchase_documents(id, org_id) ON DELETE SET NULL,
        FOREIGN KEY (gstr2b_doc_id, org_id) REFERENCES public.gstr2b_source_documents(id, org_id) ON DELETE SET NULL,
        CONSTRAINT uq_recon_variances_id_org UNIQUE (id, org_id)
    );

    ALTER TABLE public.reconciliation_variances ENABLE ROW LEVEL SECURITY;
    CREATE POLICY recon_variances_tenant ON public.reconciliation_variances
        FOR ALL TO app_service_user
        USING (org_id = (NULLIF(current_setting('app.current_org', true), ''))::uuid)
        WITH CHECK (org_id = (NULLIF(current_setting('app.current_org', true), ''))::uuid);
    CREATE POLICY recon_variances_admin ON public.reconciliation_variances
        FOR ALL TO platform_admin USING (true) WITH CHECK (true);
    """)

    # -------------------------------------------------------------------------
    # 7. Versioned ITC Risk & Statutory Rule Engine Tables
    # -------------------------------------------------------------------------
    op.execute("""
    CREATE TABLE IF NOT EXISTS public.itc_rule_catalogue (
        rule_code text PRIMARY KEY,
        statutory_section text NOT NULL,
        title text NOT NULL,
        description text NOT NULL,
        effective_date date NOT NULL,
        expiry_date date,
        is_blocking boolean NOT NULL DEFAULT false,
        rule_version text NOT NULL DEFAULT 'v2.1'
    );

    -- Seed statutory rules
    INSERT INTO public.itc_rule_catalogue (rule_code, statutory_section, title, description, effective_date, is_blocking) VALUES
        ('SEC_16_2_A', 'Section 16(2)(a)', 'Possession of Valid Tax Invoice', 'Buyer must possess valid statutory invoice or debit note.', '2017-07-01', true),
        ('SEC_16_2_AA', 'Section 16(2)(aa)', 'Supplier GSTR-1 Communication in GSTR-2B', 'Details must be communicated by supplier in GSTR-1/IFF and appear in GSTR-2B.', '2022-01-01', true),
        ('SEC_16_2_B', 'Section 16(2)(b)', 'Receipt of Goods or Services', 'Taxpayer must have actually received goods and/or services.', '2017-07-01', true),
        ('SEC_16_2_C', 'Section 16(2)(c)', 'Tax Paid to Government', 'Tax charged must have been actually paid to government by supplier.', '2017-07-01', false),
        ('SEC_16_4', 'Section 16(4)', 'ITC Availment Time Limit', 'Must be availed before 30th November following end of FY or annual return date.', '2022-10-01', true),
        ('RULE_37_180_DAYS', 'Rule 37', 'Reversal on Non-Payment within 180 Days', 'ITC reversed proportionately if vendor is not paid within 180 days from invoice date.', '2017-07-01', true),
        ('RULE_37A_NON_FILING', 'Rule 37A', 'Reversal for Supplier GSTR-3B Non-Filing', 'ITC reversed by 30th Nov if supplier reported in GSTR-1 but failed to file GSTR-3B.', '2022-12-26', true),
        ('SEC_17_5_MOTOR', 'Section 17(5)(a)', 'Motor Vehicles Blocked Credit', 'Ineligible unless used for transportation of goods, passenger transport or driving school.', '2019-02-01', true),
        ('SEC_17_5_FOOD', 'Section 17(5)(b)', 'Food, Beverages and Catering', 'Ineligible unless used as outward supply of same category or statutory obligation.', '2017-07-01', true),
        ('SEC_17_5_PERSONAL', 'Section 17(5)(g)', 'Personal Consumption Blocked Credit', 'Goods or services used for personal consumption are strictly ineligible.', '2017-07-01', true),
        ('SEC_17_5_WORKS_CONTR', 'Section 17(5)(c)', 'Works Contract for Immovable Property', 'Ineligible when supplied for construction of immovable property (except plant and machinery).', '2017-07-01', true)
    ON CONFLICT (rule_code) DO NOTHING;

    CREATE TABLE IF NOT EXISTS public.itc_assessments (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id uuid NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
        purchase_doc_id uuid NOT NULL,
        gstr2b_doc_id uuid,
        rule_code text NOT NULL REFERENCES public.itc_rule_catalogue(rule_code),
        eligibility_status text NOT NULL CHECK (eligibility_status IN (
            'ELIGIBLE_EVIDENCE_COMPLETE', 'POTENTIALLY_ELIGIBLE', 'REVIEW_REQUIRED',
            'BLOCKED_CREDIT_INDICATOR', 'PENDING_GOODS_OR_SERVICE_RECEIPT',
            'PAYMENT_180_DAY_RISK', 'RULE_37A_RISK', 'TIME_LIMIT_RISK',
            'SUPPLIER_STATUS_UNKNOWN', 'REVERSED', 'REAVAILMENT_REVIEW_REQUIRED'
        )),
        potential_itc_cgst numeric(15, 2) NOT NULL DEFAULT 0.00,
        potential_itc_sgst numeric(15, 2) NOT NULL DEFAULT 0.00,
        potential_itc_igst numeric(15, 2) NOT NULL DEFAULT 0.00,
        potential_itc_cess numeric(15, 2) NOT NULL DEFAULT 0.00,
        risk_reversal_amount numeric(15, 2) NOT NULL DEFAULT 0.00,
        days_since_invoice int,
        evaluation_date date NOT NULL DEFAULT CURRENT_DATE,
        findings_explanation text NOT NULL,
        missing_evidence_required text,
        evaluated_by_engine text NOT NULL DEFAULT 'v2.1_statutory',
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        FOREIGN KEY (purchase_doc_id, org_id) REFERENCES public.purchase_documents(id, org_id) ON DELETE CASCADE,
        FOREIGN KEY (gstr2b_doc_id, org_id) REFERENCES public.gstr2b_source_documents(id, org_id) ON DELETE SET NULL,
        CONSTRAINT uq_itc_assessments_id_org UNIQUE (id, org_id)
    );

    ALTER TABLE public.itc_assessments ENABLE ROW LEVEL SECURITY;
    CREATE POLICY itc_assessments_tenant ON public.itc_assessments
        FOR ALL TO app_service_user
        USING (org_id = (NULLIF(current_setting('app.current_org', true), ''))::uuid)
        WITH CHECK (org_id = (NULLIF(current_setting('app.current_org', true), ''))::uuid);
    CREATE POLICY itc_assessments_admin ON public.itc_assessments
        FOR ALL TO platform_admin USING (true) WITH CHECK (true);

    CREATE TABLE IF NOT EXISTS public.itc_reversal_events (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id uuid NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
        purchase_doc_id uuid NOT NULL,
        rule_code text NOT NULL REFERENCES public.itc_rule_catalogue(rule_code),
        reversal_reason text NOT NULL,
        reversal_period text NOT NULL,
        reversed_taxable numeric(15, 2) NOT NULL,
        reversed_cgst numeric(15, 2) NOT NULL,
        reversed_sgst numeric(15, 2) NOT NULL,
        reversed_igst numeric(15, 2) NOT NULL,
        reversed_cess numeric(15, 2) NOT NULL,
        interest_liability numeric(15, 2) NOT NULL DEFAULT 0.00,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        FOREIGN KEY (purchase_doc_id, org_id) REFERENCES public.purchase_documents(id, org_id) ON DELETE CASCADE,
        CONSTRAINT uq_itc_reversals_id_org UNIQUE (id, org_id)
    );

    ALTER TABLE public.itc_reversal_events ENABLE ROW LEVEL SECURITY;
    CREATE POLICY itc_reversals_tenant ON public.itc_reversal_events
        FOR ALL TO app_service_user
        USING (org_id = (NULLIF(current_setting('app.current_org', true), ''))::uuid)
        WITH CHECK (org_id = (NULLIF(current_setting('app.current_org', true), ''))::uuid);
    CREATE POLICY itc_reversals_admin ON public.itc_reversal_events
        FOR ALL TO platform_admin USING (true) WITH CHECK (true);

    CREATE TABLE IF NOT EXISTS public.itc_reavailment_events (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id uuid NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
        purchase_doc_id uuid NOT NULL,
        reversal_event_id uuid NOT NULL REFERENCES public.itc_reversal_events(id),
        reavailment_period text NOT NULL,
        reavailed_cgst numeric(15, 2) NOT NULL,
        reavailed_sgst numeric(15, 2) NOT NULL,
        reavailed_igst numeric(15, 2) NOT NULL,
        reavailed_cess numeric(15, 2) NOT NULL,
        subsequent_payment_id uuid REFERENCES public.purchase_payments(id),
        supplier_filing_evidence jsonb,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        FOREIGN KEY (purchase_doc_id, org_id) REFERENCES public.purchase_documents(id, org_id) ON DELETE CASCADE,
        CONSTRAINT uq_itc_reavailments_id_org UNIQUE (id, org_id)
    );

    ALTER TABLE public.itc_reavailment_events ENABLE ROW LEVEL SECURITY;
    CREATE POLICY itc_reavailments_tenant ON public.itc_reavailment_events
        FOR ALL TO app_service_user
        USING (org_id = (NULLIF(current_setting('app.current_org', true), ''))::uuid)
        WITH CHECK (org_id = (NULLIF(current_setting('app.current_org', true), ''))::uuid);
    CREATE POLICY itc_reavailments_admin ON public.itc_reavailment_events
        FOR ALL TO platform_admin USING (true) WITH CHECK (true);

    CREATE TABLE IF NOT EXISTS public.human_resolution_events (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id uuid NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
        variance_id uuid REFERENCES public.reconciliation_variances(id),
        purchase_doc_id uuid REFERENCES public.purchase_documents(id),
        gstr2b_doc_id uuid REFERENCES public.gstr2b_source_documents(id),
        resolution_action text NOT NULL CHECK (resolution_action IN (
            'FORCE_MATCH', 'FORCE_UNLINK', 'ACCEPT_BOOK_FIGURE',
            'ACCEPT_PORTAL_FIGURE', 'MARK_BLOCKED_CREDIT', 'MARK_ELIGIBLE_WITH_REASON',
            'DEFER_TO_NEXT_PERIOD', 'REQUEST_SUPPLIER_AMENDMENT'
        )),
        resolution_notes text NOT NULL,
        supporting_document_id uuid REFERENCES public.documents(id),
        resolved_by uuid NOT NULL REFERENCES public.global_users(id),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        CONSTRAINT uq_human_resolutions_id_org UNIQUE (id, org_id)
    );

    ALTER TABLE public.human_resolution_events ENABLE ROW LEVEL SECURITY;
    CREATE POLICY human_resolutions_tenant ON public.human_resolution_events
        FOR ALL TO app_service_user
        USING (org_id = (NULLIF(current_setting('app.current_org', true), ''))::uuid)
        WITH CHECK (org_id = (NULLIF(current_setting('app.current_org', true), ''))::uuid);
    CREATE POLICY human_resolutions_admin ON public.human_resolution_events
        FOR ALL TO platform_admin USING (true) WITH CHECK (true);

    -- -------------------------------------------------------------------------
    -- 8. Secure Quarantine Table against Unprotected Cross-Tenant Data Access
    -- -------------------------------------------------------------------------
    DO $$
    BEGIN
        IF EXISTS (SELECT FROM pg_tables WHERE schemaname = 'public' AND tablename = 'archived_m1_3_incomplete_extractions') THEN
            ALTER TABLE public.archived_m1_3_incomplete_extractions ENABLE ROW LEVEL SECURITY;
            DROP POLICY IF EXISTS quarantine_tenant_policy ON public.archived_m1_3_incomplete_extractions;
            CREATE POLICY quarantine_tenant_policy ON public.archived_m1_3_incomplete_extractions
                FOR ALL TO app_service_user
                USING (org_id = (NULLIF(current_setting('app.current_org', true), ''))::uuid);
        END IF;
    END
    $$;

    -- -------------------------------------------------------------------------
    -- 9. Table-Level Grants for App Service Role (Enforced by RLS)
    -- -------------------------------------------------------------------------
    GRANT SELECT, INSERT, UPDATE, DELETE ON public.gst_registrations TO app_service_user;
    GRANT SELECT, INSERT, UPDATE, DELETE ON public.gstr2b_imports TO app_service_user;
    GRANT SELECT, INSERT, UPDATE, DELETE ON public.gstr2b_snapshots TO app_service_user;
    GRANT SELECT, INSERT, UPDATE, DELETE ON public.gstr2b_source_documents TO app_service_user;
    GRANT SELECT, INSERT, UPDATE, DELETE ON public.ims_document_actions TO app_service_user;
    GRANT SELECT, INSERT, UPDATE, DELETE ON public.purchase_documents TO app_service_user;
    GRANT SELECT, INSERT, UPDATE, DELETE ON public.purchase_line_items TO app_service_user;
    GRANT SELECT, INSERT, UPDATE, DELETE ON public.purchase_payments TO app_service_user;
    GRANT SELECT, INSERT, UPDATE, DELETE ON public.purchase_payment_allocations TO app_service_user;
    GRANT SELECT, INSERT, UPDATE, DELETE ON public.reconciliation_runs TO app_service_user;
    GRANT SELECT, INSERT, UPDATE, DELETE ON public.reconciliation_matches TO app_service_user;
    GRANT SELECT, INSERT, UPDATE, DELETE ON public.reconciliation_variances TO app_service_user;
    GRANT SELECT, INSERT, UPDATE, DELETE ON public.itc_assessments TO app_service_user;
    GRANT SELECT, INSERT, UPDATE, DELETE ON public.itc_reversal_events TO app_service_user;
    GRANT SELECT, INSERT, UPDATE, DELETE ON public.itc_reavailment_events TO app_service_user;
    GRANT SELECT, INSERT, UPDATE, DELETE ON public.human_resolution_events TO app_service_user;
    GRANT SELECT ON public.itc_rule_catalogue TO app_service_user;
    """)


def downgrade() -> None:
    # Drop in strict reverse dependency order
    op.execute("""
    DROP TABLE IF EXISTS public.human_resolution_events CASCADE;
    DROP TABLE IF EXISTS public.itc_reavailment_events CASCADE;
    DROP TABLE IF EXISTS public.itc_reversal_events CASCADE;
    DROP TABLE IF EXISTS public.itc_assessments CASCADE;
    DROP TABLE IF EXISTS public.itc_rule_catalogue CASCADE;

    DROP TABLE IF EXISTS public.reconciliation_variances CASCADE;
    DROP TABLE IF EXISTS public.reconciliation_matches CASCADE;
    DROP TABLE IF EXISTS public.reconciliation_runs CASCADE;

    DROP TABLE IF EXISTS public.purchase_payment_allocations CASCADE;
    DROP TABLE IF EXISTS public.purchase_payments CASCADE;
    DROP TABLE IF EXISTS public.purchase_line_items CASCADE;
    DROP TABLE IF EXISTS public.purchase_documents CASCADE;

    DROP TABLE IF EXISTS public.ims_document_actions CASCADE;
    DROP TABLE IF EXISTS public.gstr2b_source_documents CASCADE;
    DROP TABLE IF EXISTS public.gstr2b_snapshots CASCADE;
    DROP TABLE IF EXISTS public.gstr2b_imports CASCADE;

    DROP TABLE IF EXISTS public.gst_registrations CASCADE;

    ALTER TABLE public.invoices
        DROP CONSTRAINT IF EXISTS chk_invoices_doc_type,
        DROP CONSTRAINT IF EXISTS chk_invoices_supply_category,
        DROP COLUMN IF EXISTS classification_provenance,
        DROP COLUMN IF EXISTS recipient_registration_type,
        DROP COLUMN IF EXISTS document_type,
        DROP COLUMN IF EXISTS is_reverse_charge,
        DROP COLUMN IF EXISTS is_deemed_export,
        DROP COLUMN IF EXISTS is_sez_supply,
        DROP COLUMN IF EXISTS supply_category;
    """)
