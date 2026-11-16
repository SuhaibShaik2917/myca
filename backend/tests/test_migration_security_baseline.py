import pytest
import psycopg
import uuid
from psycopg.rows import dict_row

DB_ADMIN = "postgresql:///myca?user=postgres"
DB_WORKER = "postgresql:///myca?user=worker_service_user"
DB_APP = "postgresql:///myca?user=app_service_user"

# -------------------------------------------------------------
# Baseline Security & Transitive Privilege Checks
# -------------------------------------------------------------

def test_effective_function_privileges():
    """Verify that non-admin roles have NO execute privileges on internal administrative functions."""
    with psycopg.connect(DB_ADMIN) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    role_name,
                    has_function_privilege(
                        role_name,
                        'public.reap_expired_tasks(uuid,integer)',
                        'EXECUTE'
                    ) AS can_execute
                FROM (
                    VALUES
                        ('worker_service_user'),
                        ('app_service_user'),
                        ('audit_service_user'),
                        ('auditor_service_user'),
                        ('sys_auditor_service_user')
                ) AS roles(role_name);
            """)
            rows = cur.fetchall()
            for role_name, can_execute in rows:
                assert not can_execute, f"Role {role_name} unexpectedly has EXECUTE on reap_expired_tasks!"

def test_transitive_role_memberships():
    """Verify that service login roles do not inherit platform administrative privileges."""
    with psycopg.connect(DB_ADMIN) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT 
                    r.rolname AS login_user,
                    target.rolname AS target_privileged_role,
                    pg_has_role(r.rolname, target.rolname, 'MEMBER') AS has_transitive_membership
                FROM (
                    VALUES
                        ('worker_service_user'),
                        ('app_service_user'),
                        ('audit_service_user'),
                        ('auditor_service_user'),
                        ('sys_auditor_service_user')
                ) AS logins(rolname)
                CROSS JOIN (
                    VALUES
                        ('platform_admin'),
                        ('task_queue_owner')
                ) AS target(rolname)
                JOIN pg_roles r ON r.rolname = logins.rolname;
            """)
            rows = cur.fetchall()
            for login_user, target_role, has_member in rows:
                assert not has_member, f"Login {login_user} unexpectedly inherits {target_role}!"

# -------------------------------------------------------------
# C1: Invoice Financial Immutability & Line Items Protection
# -------------------------------------------------------------

def test_c1_invoice_financial_immutability():
    """
    C1: Verify financial fields cannot be modified during approval transition,
    once approved, during export_ready transition, and line items cannot be mutated on approved invoices.
    """
    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            org_id = str(uuid.uuid4())
            user_id = str(uuid.uuid4())
            profile_id = str(uuid.uuid4())
            inv_id = str(uuid.uuid4())

            # Seed organization and active owner membership
            cur.execute("INSERT INTO public.organizations (id, legal_name, pan) VALUES (%s, 'C1 Test Org', 'ABCDE1234F');", (org_id,))
            cur.execute("INSERT INTO public.worker_service_authorizations (worker_role_name, org_id, is_active) VALUES ('worker_service_user', %s, true);", (org_id,))
            cur.execute("INSERT INTO public.global_users (id, email, full_name) VALUES (%s, 'c1owner@test.com', 'C1 Owner');", (user_id,))
            cur.execute("INSERT INTO public.organization_memberships (org_id, user_id, role, status) VALUES (%s, %s, 'owner', 'active');", (org_id, user_id))
            cur.execute("INSERT INTO public.business_profiles (id, org_id, gstin, business_type, industry_sector) VALUES (%s, %s, '27ABCDE1234F1Z5', 'Retail', 'Tools');", (profile_id, org_id))

            # Initial invoice in uploaded status
            cur.execute("""
                INSERT INTO public.invoices (
                    id, org_id, profile_id, invoice_number, invoice_date, counterparty_name,
                    total_taxable_amount, cgst_amount, sgst_amount, igst_amount, total_amount, status
                ) VALUES (
                    %s, %s, %s, 'INV-C1-001', '2026-11-01', 'Counterparty Ltd',
                    1000.00, 90.00, 90.00, 0.00, 1180.00, 'uploaded'
                );
            """, (inv_id, org_id, profile_id))

            # Add line item
            line_id = str(uuid.uuid4())
            cur.execute("""
                INSERT INTO public.invoice_line_items (
                    id, invoice_id, org_id, item_description, quantity, unit_price, taxable_amount, gst_rate, total_amount
                ) VALUES (
                    %s, %s, %s, 'Item A', 1.0, 1000.00, 1000.00, 18.00, 1180.00
                );
            """, (line_id, inv_id, org_id))

            # Advance invoice to pending_review
            cur.execute("UPDATE public.invoices SET status = 'queued' WHERE id = %s;", (inv_id,))
            cur.execute("UPDATE public.invoices SET status = 'processing' WHERE id = %s;", (inv_id,))
            cur.execute("UPDATE public.invoices SET status = 'extracted' WHERE id = %s;", (inv_id,))
            cur.execute("UPDATE public.invoices SET status = 'pending_review' WHERE id = %s;", (inv_id,))

    # Connect as app_service_user with human user context
    with psycopg.connect(DB_APP, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('app.current_org', %s, false);", (org_id,))
            cur.execute("SELECT set_config('app.current_user', %s, false);", (user_id,))

            # Bypass Attempt 1: Human attempts to alter total_amount during the approval update itself
            with pytest.raises(psycopg.Error) as exc_info:
                cur.execute("""
                    UPDATE public.invoices 
                    SET status = 'approved', total_amount = 2000.00 
                    WHERE id = %s;
                """, (inv_id,))
            assert "Cannot modify financial figures during approval/rejection transition" in str(exc_info.value) or "42501" in str(exc_info.value)

            # Legitimate approval: exact matching figures
            cur.execute("UPDATE public.invoices SET status = 'approved' WHERE id = %s;", (inv_id,))

            # Bypass Attempt 2: Alter total_amount on approved invoice without status change
            with pytest.raises(psycopg.Error) as exc_info:
                cur.execute("UPDATE public.invoices SET total_amount = 9999.00 WHERE id = %s;", (inv_id,))
            assert "Cannot modify financial or identifying fields on approved/export-ready invoice" in str(exc_info.value) or "42501" in str(exc_info.value)

            # Bypass Attempt 3: Line item tampering on approved invoice
            with pytest.raises(psycopg.Error) as exc_info:
                cur.execute("UPDATE public.invoice_line_items SET unit_price = 5000.00 WHERE id = %s;", (line_id,))
            assert "Cannot insert, update, or delete line items for approved/export-ready invoice" in str(exc_info.value) or "42501" in str(exc_info.value)

            with pytest.raises(psycopg.Error) as exc_info:
                cur.execute("DELETE FROM public.invoice_line_items WHERE id = %s;", (line_id,))
            assert "Cannot insert, update, or delete line items for approved/export-ready invoice" in str(exc_info.value) or "42501" in str(exc_info.value)

    # Worker transition to export_ready
    with psycopg.connect(DB_WORKER, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('app.current_org', %s, false);", (org_id,))

            # Bypass Attempt 4: Worker attempts to alter invoice_number during transition to export_ready
            with pytest.raises(psycopg.Error) as exc_info:
                cur.execute("UPDATE public.invoices SET status = 'export_ready', invoice_number = 'TAMPERED-01' WHERE id = %s;", (inv_id,))
            assert "Cannot modify financial or identifying fields on approved/export-ready invoice" in str(exc_info.value) or "42501" in str(exc_info.value)

            # Legitimate export_ready transition: succeeds
            cur.execute("UPDATE public.invoices SET status = 'export_ready' WHERE id = %s;", (inv_id,))

# -------------------------------------------------------------
# C2: Cross-Tenant Document Association Prevention
# -------------------------------------------------------------

def test_c2_cross_tenant_document_fk_rejection():
    """C2: Verify that an invoice in Org A cannot reference a document in Org B."""
    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            org_a = str(uuid.uuid4())
            org_b = str(uuid.uuid4())
            doc_b = str(uuid.uuid4())
            profile_a = str(uuid.uuid4())

            cur.execute("INSERT INTO public.organizations (id, legal_name, pan) VALUES (%s, 'Org A', 'ABCDE1111A');", (org_a,))
            cur.execute("INSERT INTO public.organizations (id, legal_name, pan) VALUES (%s, 'Org B', 'ABCDE2222B');", (org_b,))
            cur.execute("INSERT INTO public.business_profiles (id, org_id, gstin, business_type, industry_sector) VALUES (%s, %s, '27ABCDE1111A1Z1', 'Retail', 'Tools');", (profile_a, org_a))

            # Document uploaded in Org B
            cur.execute("""
                INSERT INTO public.documents (
                    id, org_id, doc_type, file_name, file_path, mime_type, file_size_bytes, sha256_checksum
                ) VALUES (
                    %s, %s, 'invoice', 'doc_b.pdf', '/storage/doc_b.pdf', 'application/pdf', 1024, 'dummy_sha256'
                );
            """, (doc_b, org_b))

            # Attempt to create invoice in Org A referencing Document in Org B
            with pytest.raises(psycopg.Error) as exc_info:
                cur.execute("""
                    INSERT INTO public.invoices (
                        id, org_id, profile_id, invoice_number, invoice_date, counterparty_name,
                        total_taxable_amount, cgst_amount, sgst_amount, igst_amount, total_amount, 
                        status, document_id
                    ) VALUES (
                        gen_random_uuid(), %s, %s, 'INV-A-001', '2026-11-01', 'Supplier',
                        100.00, 9.00, 9.00, 0.00, 118.00, 'uploaded', %s
                    );
                """, (org_a, profile_a, doc_b))
            assert "violates foreign key constraint" in str(exc_info.value).lower()

# -------------------------------------------------------------
# C3: Worker Direct DML Revoked on Financial & Scheme Tables
# -------------------------------------------------------------

def test_c3_worker_direct_dml_revoked():
    """C3: Verify that worker_service_user has no direct INSERT/UPDATE/DELETE on financial_summaries, gst_records, scheme_matches."""
    with psycopg.connect(DB_WORKER, autocommit=True) as conn:
        with conn.cursor() as cur:
            dummy_org = str(uuid.uuid4())
            cur.execute("SELECT set_config('app.current_org', %s, false);", (dummy_org,))

            # 1. gst_records INSERT
            with pytest.raises(psycopg.Error) as exc_info:
                cur.execute("""
                    INSERT INTO public.gst_records (
                        id, org_id, gstin, return_type, tax_period, total_taxable_value, total_tax_paid
                    ) VALUES (
                        gen_random_uuid(), %s, '27ABCDE1234F1Z5', 'GSTR-3B', '102026', 1000.00, 180.00
                    );
                """, (dummy_org,))
            assert "permission denied" in str(exc_info.value).lower() or "42501" in str(exc_info.value)

            # 2. financial_summaries INSERT
            with pytest.raises(psycopg.Error) as exc_info:
                cur.execute("""
                    INSERT INTO public.financial_summaries (
                        id, org_id, summary_period, total_inflow, total_outflow, net_cash_flow
                    ) VALUES (
                        gen_random_uuid(), %s, '2026-10', 50000.00, 30000.00, 20000.00
                    );
                """, (dummy_org,))
            assert "permission denied" in str(exc_info.value).lower() or "42501" in str(exc_info.value)

            # 3. scheme_matches INSERT
            with pytest.raises(psycopg.Error) as exc_info:
                cur.execute("""
                    INSERT INTO public.scheme_matches (
                        id, org_id, scheme_id, match_score
                    ) VALUES (
                        gen_random_uuid(), %s, gen_random_uuid(), 95.00
                    );
                """, (dummy_org,))
            assert "permission denied" in str(exc_info.value).lower() or "42501" in str(exc_info.value)

# -------------------------------------------------------------
# C4: Worker Task Retry Limit & Release Enforcement
# -------------------------------------------------------------

def test_c4_worker_task_retry_limit():
    """C4: Verify that release_task transitions to failed once max_attempts is reached, and claim_task ignores it."""
    org_id = str(uuid.uuid4())
    task_id = str(uuid.uuid4())

    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO public.organizations (id, legal_name, pan) VALUES (%s, 'Retry Org', 'ABCDE3333C');", (org_id,))
            cur.execute("INSERT INTO public.worker_service_authorizations (worker_role_name, org_id, is_active) VALUES ('worker_service_user', %s, true);", (org_id,))
            # Create task with max_attempts = 2
            cur.execute("""
                INSERT INTO public.worker_tasks (id, org_id, task_type, status, attempts, max_attempts)
                VALUES (%s, %s, 'ocr_processing', 'queued', 0, 2);
            """, (task_id, org_id))

    with psycopg.connect(DB_WORKER, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('app.current_org', %s, false);", (org_id,))
            # Claim 1: attempts becomes 1
            cur.execute("SELECT o_task_id, o_lease_token FROM public.claim_task(%s, 'inst-1');", (org_id,))
            t_id, token1 = cur.fetchone()
            assert str(t_id) == task_id

            # Release 1: attempts (1) < max_attempts (2) -> status becomes queued
            cur.execute("SELECT public.release_task(%s, %s);", (task_id, token1))
            
            # Verify status in database
            cur.execute("SELECT status, attempts FROM public.worker_tasks WHERE id = %s;", (task_id,))
            st, att = cur.fetchone()
            assert st == "queued"
            assert att == 1

            # Claim 2: attempts becomes 2
            cur.execute("SELECT o_task_id, o_lease_token FROM public.claim_task(%s, 'inst-1');", (org_id,))
            t_id, token2 = cur.fetchone()
            assert str(t_id) == task_id

            # Release 2: attempts (2) >= max_attempts (2) -> MUST transition directly to failed!
            cur.execute("SELECT public.release_task(%s, %s);", (task_id, token2))

            cur.execute("SELECT status, attempts, error_message FROM public.worker_tasks WHERE id = %s;", (task_id,))
            st, att, err = cur.fetchone()
            assert st == "failed"
            assert att == 2
            assert "maximum retry attempts exhausted" in err

            # Claim 3: Should return NULL (task is failed and attempts limit exhausted)
            cur.execute("SELECT o_task_id, o_lease_token FROM public.claim_task(%s, 'inst-1');", (org_id,))
            t_id, token3 = cur.fetchone()
            assert t_id is None
            assert token3 is None

# -------------------------------------------------------------
# C5: User Directory View Accessible under Membership RLS
# -------------------------------------------------------------

def test_c5_v_org_users_accessible():
    """C5: Verify app_service_user can query v_org_users without 42501 permission error."""
    org_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())

    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO public.organizations (id, legal_name, pan) VALUES (%s, 'Directory Org', 'ABCDE4444D');", (org_id,))
            cur.execute("INSERT INTO public.global_users (id, email, full_name) VALUES (%s, 'diruser@test.com', 'Directory User');", (user_id,))
            cur.execute("INSERT INTO public.organization_memberships (org_id, user_id, role, status) VALUES (%s, %s, 'admin', 'active');", (org_id, user_id))

    with psycopg.connect(DB_APP, autocommit=True) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT set_config('app.current_org', %s, false);", (org_id,))
            cur.execute("SELECT set_config('app.current_user', %s, false);", (user_id,))
            cur.execute("SELECT * FROM public.v_org_users;")
            rows = cur.fetchall()
            assert len(rows) == 1
            assert rows[0]["email"] == "diruser@test.com"
            assert rows[0]["role"] == "admin"

# -------------------------------------------------------------
# H1: Reject NULL Lease Duration
# -------------------------------------------------------------

def test_h1_renew_task_lease_null_rejected():
    """H1: Explicitly reject NULL lease durations in renew_task_lease."""
    org_id = str(uuid.uuid4())
    task_id = str(uuid.uuid4())

    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO public.organizations (id, legal_name, pan) VALUES (%s, 'H1 Org', 'ABCDE5555E');", (org_id,))
            cur.execute("INSERT INTO public.worker_service_authorizations (worker_role_name, org_id, is_active) VALUES ('worker_service_user', %s, true);", (org_id,))
            cur.execute("INSERT INTO public.worker_tasks (id, org_id, task_type, status) VALUES (%s, %s, 'test_task', 'queued');", (task_id, org_id))

    with psycopg.connect(DB_WORKER, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT o_task_id, o_lease_token FROM public.claim_task(%s, 'h1-worker');", (org_id,))
            t_id, token = cur.fetchone()

            # Attempt renewal with NULL minutes -> must be rejected
            with pytest.raises(psycopg.Error) as exc_info:
                cur.execute("SELECT public.renew_task_lease(%s, %s, NULL);", (task_id, token))
            assert "Invalid lease minutes: NULL" in str(exc_info.value) or "between 1 and 60" in str(exc_info.value)

# -------------------------------------------------------------
# H2 & H3: Audit Schema USAGE & Recursive JSON Redaction
# -------------------------------------------------------------

def test_h2_and_h3_audit_logging_and_redaction():
    """H2 & H3: Verify USAGE on audit schema and recursive sensitive key redaction in audit_logs."""
    org_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())

    with psycopg.connect(DB_APP, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('app.current_org', %s, false);", (org_id,))
            cur.execute("SELECT set_config('app.current_user', %s, false);", (user_id,))

            payload = psycopg.types.json.Jsonb({
                "pan": "ABCDE1234F",
                "password": "supersecretpassword",
                "nested": {
                    "aadhaar": "999988887777",
                    "bank_account": "1234567890"
                },
                "safe_note": "Unclassified transaction notes"
            })

            # H2: Calling audit.log_audit_event succeeds from app_service_user (USAGE granted)
            cur.execute("""
                SELECT audit.log_audit_event(
                    %s, 'test_entity', 'entity-1', 'CREATE'::public.audit_action,
                    NULL, %s, '{"source": "test"}'::jsonb
                );
            """, (org_id, payload))
            audit_id = cur.fetchone()[0]
            assert audit_id is not None

    # Verify redaction as admin (H3)
    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT * FROM audit.audit_logs WHERE id = %s;", (audit_id,))
            row = cur.fetchone()
            state = row["new_state"]

            # Redacted fields
            assert state["pan"] == "[REDACTED]"
            assert state["password"] == "[REDACTED]"
            assert state["nested"]["aadhaar"] == "[REDACTED]"
            assert state["nested"]["bank_account"] == "[REDACTED]"

            # Safe fields unredacted
            assert state["safe_note"] == "Unclassified transaction notes"

# -------------------------------------------------------------
# H4: Summary Transactions Immutability
# -------------------------------------------------------------

def test_h4_summary_transactions_immutability():
    """H4: Verify summary_transactions rejects UPDATE and DELETE operations."""
    org_id = str(uuid.uuid4())
    summary_id = str(uuid.uuid4())
    tx_id = str(uuid.uuid4())

    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO public.organizations (id, legal_name, pan) VALUES (%s, 'H4 Org', 'ABCDE6666F');", (org_id,))
            cur.execute("INSERT INTO public.financial_summaries (id, org_id, summary_period) VALUES (%s, %s, '2026-10');", (summary_id, org_id))
            cur.execute("INSERT INTO public.financial_transactions (id, org_id, transaction_date, amount, transaction_type, category) VALUES (%s, %s, '2026-10-15', 500.00, 'credit', 'sales');", (tx_id, org_id))
            cur.execute("INSERT INTO public.summary_transactions (summary_id, transaction_id, org_id) VALUES (%s, %s, %s);", (summary_id, tx_id, org_id))

    with psycopg.connect(DB_APP, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('app.current_org', %s, false);", (org_id,))

            # Attempt UPDATE -> Rejected
            with pytest.raises(psycopg.Error) as exc_info:
                cur.execute("UPDATE public.summary_transactions SET org_id = %s WHERE summary_id = %s;", (org_id, summary_id))
            assert "permission denied" in str(exc_info.value).lower() or "42501" in str(exc_info.value) or "strictly immutable" in str(exc_info.value)

            # Attempt DELETE -> Rejected
            with pytest.raises(psycopg.Error) as exc_info:
                cur.execute("DELETE FROM public.summary_transactions WHERE summary_id = %s;", (summary_id,))
            assert "permission denied" in str(exc_info.value).lower() or "42501" in str(exc_info.value) or "strictly immutable" in str(exc_info.value)

# -------------------------------------------------------------
# H5: Worker Service Authorizations RLS Isolation
# -------------------------------------------------------------

def test_h5_worker_service_authorizations_rls_isolation():
    """H5: Verify worker_owner can only SELECT its own authorization row."""
    org_1 = str(uuid.uuid4())
    org_2 = str(uuid.uuid4())

    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO public.organizations (id, legal_name, pan) VALUES (%s, 'Org W1', 'ABCDE7777G');", (org_1,))
            cur.execute("INSERT INTO public.organizations (id, legal_name, pan) VALUES (%s, 'Org W2', 'ABCDE8888H');", (org_2,))
            cur.execute("INSERT INTO public.worker_service_authorizations (worker_role_name, org_id, is_active) VALUES ('worker_service_user', %s, true);", (org_1,))
            cur.execute("INSERT INTO public.worker_service_authorizations (worker_role_name, org_id, is_active) VALUES ('other_worker_user', %s, true);", (org_2,))

    with psycopg.connect(DB_WORKER, autocommit=True) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT * FROM public.worker_service_authorizations;")
            rows = cur.fetchall()
            # Must only see the row for worker_service_user
            for r in rows:
                assert r["worker_role_name"] == "worker_service_user", f"Worker leaked foreign authorization for role: {r['worker_role_name']}"
            assert any(r["org_id"] == uuid.UUID(org_1) for r in rows)
            assert not any(r["org_id"] == uuid.UUID(org_2) for r in rows)
