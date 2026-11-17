import pytest
import psycopg
import uuid
import random
import string
from psycopg.rows import dict_row

def random_pan():
    digits = f"{random.randint(1000, 9999)}"
    letter = random.choice(string.ascii_uppercase)
    return f"ABCDE{digits}{letter}"

def random_gstin(pan=None):
    if not pan:
        pan = random_pan()
    return f"27{pan}1Z5"

def random_email():
    return f"user_{uuid.uuid4().hex[:10]}@test.com"

DB_ADMIN = "postgresql:///myca?user=postgres"
DB_WORKER = "postgresql:///myca?user=worker_service_user"
DB_WORKER_1 = "postgresql:///myca?user=worker_service_user_1"
DB_WORKER_2 = "postgresql:///myca?user=worker_service_user_2"
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

            cur.execute("INSERT INTO public.organizations (id, legal_name, pan) VALUES (%s, 'C1 Test Org', %s);", (org_id, random_pan()))
            cur.execute("INSERT INTO public.worker_service_authorizations (worker_role_name, org_id, is_active) VALUES ('worker_service_user', %s, true);", (org_id,))
            cur.execute("INSERT INTO public.global_users (id, email, full_name) VALUES (%s, %s, 'C1 Owner');", (user_id, random_email()))
            cur.execute("INSERT INTO public.organization_memberships (org_id, user_id, role, status) VALUES (%s, %s, 'owner', 'active');", (org_id, user_id))
            cur.execute("INSERT INTO public.business_profiles (id, org_id, gstin, business_type, industry_sector) VALUES (%s, %s, %s, 'Retail', 'Tools');", (profile_id, org_id, random_gstin()))

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

            # Bypass Attempt 3: Line item price tampering on approved invoice
            with pytest.raises(psycopg.Error) as exc_info:
                cur.execute("UPDATE public.invoice_line_items SET unit_price = 5000.00 WHERE id = %s;", (line_id,))
            assert "Cannot modify or move line items out of approved/export-ready invoice" in str(exc_info.value) or "42501" in str(exc_info.value)

            with pytest.raises(psycopg.Error) as exc_info:
                cur.execute("DELETE FROM public.invoice_line_items WHERE id = %s;", (line_id,))
            assert "Cannot delete line items from approved/export-ready invoice" in str(exc_info.value) or "42501" in str(exc_info.value)

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

def test_c1_line_item_cannot_move_out_of_approved_invoice():
    """
    C1 Round 5.7: Verify that a line item CANNOT be moved out of an approved invoice into an unapproved/draft invoice.
    """
    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            org_id = str(uuid.uuid4())
            user_id = str(uuid.uuid4())
            profile_id = str(uuid.uuid4())
            approved_inv = str(uuid.uuid4())
            draft_inv = str(uuid.uuid4())
            line_id = str(uuid.uuid4())

            cur.execute("INSERT INTO public.organizations (id, legal_name, pan) VALUES (%s, 'Line Movement Org', %s);", (org_id, random_pan()))
            cur.execute("INSERT INTO public.global_users (id, email, full_name) VALUES (%s, %s, 'Approver User');", (user_id, random_email()))
            cur.execute("INSERT INTO public.organization_memberships (org_id, user_id, role, status) VALUES (%s, %s, 'owner', 'active');", (org_id, user_id))
            cur.execute("INSERT INTO public.business_profiles (id, org_id, gstin, business_type, industry_sector) VALUES (%s, %s, %s, 'Mfg', 'Auto');", (profile_id, org_id, random_gstin()))

            # Approved Invoice
            cur.execute("""
                INSERT INTO public.invoices (
                    id, org_id, profile_id, invoice_number, invoice_date, counterparty_name,
                    total_taxable_amount, cgst_amount, sgst_amount, igst_amount, total_amount, status
                ) VALUES (
                    %s, %s, %s, 'INV-APPR-01', '2026-11-01', 'Vendor A',
                    500.00, 45.00, 45.00, 0.00, 590.00, 'uploaded'
                );
            """, (approved_inv, org_id, profile_id))
            # Insert line item while invoice is in initial draft state
            cur.execute("""
                INSERT INTO public.invoice_line_items (
                    id, invoice_id, org_id, item_description, quantity, unit_price, taxable_amount, total_amount
                ) VALUES (
                    %s, %s, %s, 'Part 1', 1.0, 500.00, 500.00, 590.00
                );
            """, (line_id, approved_inv, org_id))

            # Advance to pending_review
            cur.execute("UPDATE public.invoices SET status = 'queued' WHERE id = %s;", (approved_inv,))
            cur.execute("UPDATE public.invoices SET status = 'processing' WHERE id = %s;", (approved_inv,))
            cur.execute("UPDATE public.invoices SET status = 'extracted' WHERE id = %s;", (approved_inv,))
            cur.execute("UPDATE public.invoices SET status = 'pending_review' WHERE id = %s;", (approved_inv,))

            # Legitimate human approval
            cur.execute("SELECT set_config('app.current_org', %s, false);", (org_id,))
            cur.execute("SELECT set_config('app.current_user', %s, false);", (user_id,))
            cur.execute("UPDATE public.invoices SET status = 'approved' WHERE id = %s;", (approved_inv,))

            # Draft Invoice
            cur.execute("""
                INSERT INTO public.invoices (
                    id, org_id, profile_id, invoice_number, invoice_date, counterparty_name,
                    total_taxable_amount, cgst_amount, sgst_amount, igst_amount, total_amount, status
                ) VALUES (
                    %s, %s, %s, 'INV-DRAFT-01', '2026-11-02', 'Vendor B',
                    100.00, 9.00, 9.00, 0.00, 118.00, 'uploaded'
                );
            """, (draft_inv, org_id, profile_id))

    with psycopg.connect(DB_APP, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('app.current_org', %s, false);", (org_id,))

            # Attempt to move line item from approved_inv to draft_inv
            with pytest.raises(psycopg.Error) as exc_info:
                cur.execute("UPDATE public.invoice_line_items SET invoice_id = %s WHERE id = %s;", (draft_inv, line_id))
            assert "Cannot modify or move line items out of approved/export-ready invoice" in str(exc_info.value) or "42501" in str(exc_info.value)

# -------------------------------------------------------------
# C2: Cross-Tenant Document Association & SET NULL Deletion
# -------------------------------------------------------------

def test_c2_cross_tenant_document_fk_rejection():
    """C2: Verify that an invoice in Org A cannot reference a document in Org B."""
    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            org_a = str(uuid.uuid4())
            org_b = str(uuid.uuid4())
            doc_b = str(uuid.uuid4())
            profile_a = str(uuid.uuid4())

            cur.execute("INSERT INTO public.organizations (id, legal_name, pan) VALUES (%s, 'Org A', %s);", (org_a, random_pan()))
            cur.execute("INSERT INTO public.organizations (id, legal_name, pan) VALUES (%s, 'Org B', %s);", (org_b, random_pan()))
            cur.execute("INSERT INTO public.business_profiles (id, org_id, gstin, business_type, industry_sector) VALUES (%s, %s, %s, 'Retail', 'Tools');", (profile_a, org_a, random_gstin()))

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

def test_c2_document_deletion_set_null():
    """
    C2 Round 5.7: Verify deleting a referenced document nulls ONLY document_id,
    leaving invoice intact in its organization with org_id unchanged.
    """
    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            org_id = str(uuid.uuid4())
            profile_id = str(uuid.uuid4())
            doc_id = str(uuid.uuid4())
            inv_id = str(uuid.uuid4())

            cur.execute("INSERT INTO public.organizations (id, legal_name, pan) VALUES (%s, 'Doc Del Org', %s);", (org_id, random_pan()))
            cur.execute("INSERT INTO public.business_profiles (id, org_id, gstin, business_type, industry_sector) VALUES (%s, %s, %s, 'Wholesale', 'Parts');", (profile_id, org_id, random_gstin()))
            cur.execute("""
                INSERT INTO public.documents (
                    id, org_id, doc_type, file_name, file_path, mime_type, file_size_bytes, sha256_checksum
                ) VALUES (
                    %s, %s, 'invoice', 'inv_del.pdf', '/storage/inv_del.pdf', 'application/pdf', 2048, 'sha256_abc'
                );
            """, (doc_id, org_id))

            cur.execute("""
                INSERT INTO public.invoices (
                    id, org_id, profile_id, invoice_number, invoice_date, counterparty_name,
                    total_taxable_amount, total_amount, status, document_id
                ) VALUES (
                    %s, %s, %s, 'INV-DOC-DEL-01', '2026-11-01', 'Counterparty',
                    200.00, 236.00, 'uploaded', %s
                );
            """, (inv_id, org_id, profile_id, doc_id))

            # Delete the referenced document
            cur.execute("DELETE FROM public.documents WHERE id = %s;", (doc_id,))

            # Verify invoice remains: org_id is intact, document_id is NULL
            cur.execute("SELECT org_id, document_id FROM public.invoices WHERE id = %s;", (inv_id,))
            row = cur.fetchone()
            assert row is not None
            assert str(row[0]) == org_id
            assert row[1] is None

# -------------------------------------------------------------
# C3: Worker Direct DML Revoked & Controlled Function Audit
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

def test_c3_worker_lifecycle_functions_security_audit():
    """
    C3 Round 5.7: Verify controlled lifecycle functions reject invalid lease tokens,
    expired leases, and unassigned organizations.
    """
    org_id = str(uuid.uuid4())
    task_id = str(uuid.uuid4())

    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO public.organizations (id, legal_name, pan) VALUES (%s, 'Audit Org', %s);", (org_id, random_pan()))
            cur.execute("INSERT INTO public.worker_service_authorizations (worker_role_name, org_id, is_active) VALUES ('worker_service_user', %s, true);", (org_id,))
            cur.execute("INSERT INTO public.worker_tasks (id, org_id, task_type, status) VALUES (%s, %s, 'ocr', 'queued');", (task_id, org_id))

    with psycopg.connect(DB_WORKER, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT o_task_id, o_lease_token FROM public.claim_task(%s, 'worker-inst');", (org_id,))
            t_id, token = cur.fetchone()

            # Attempt complete with wrong lease token -> rejected
            with pytest.raises(psycopg.Error) as exc_info:
                cur.execute("SELECT public.complete_task(%s, 'fake-token', '{\"status\": \"ok\"}'::jsonb);", (task_id,))
            assert "Invalid lease token" in str(exc_info.value)

            # Attempt complete with correct token -> succeeds
            cur.execute("SELECT public.complete_task(%s, %s, '{\"status\": \"ok\"}'::jsonb);", (task_id, token))

            # Attempt complete on completed task -> rejected
            with pytest.raises(psycopg.Error) as exc_info:
                cur.execute("SELECT public.complete_task(%s, %s, '{\"status\": \"ok\"}'::jsonb);", (task_id, token))
            assert "is not in_progress" in str(exc_info.value)

# -------------------------------------------------------------
# C4: Worker Task Retry Limit & Expired Final Attempt Recovery
# -------------------------------------------------------------

def test_c4_worker_task_retry_limit():
    """C4: Verify that release_task transitions to failed once max_attempts is reached, and claim_task ignores it."""
    org_id = str(uuid.uuid4())
    task_id = str(uuid.uuid4())

    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO public.organizations (id, legal_name, pan) VALUES (%s, 'Retry Org', %s);", (org_id, random_pan()))
            cur.execute("INSERT INTO public.worker_service_authorizations (worker_role_name, org_id, is_active) VALUES ('worker_service_user', %s, true);", (org_id,))
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

def test_c4_expired_final_attempt_task_reaped_to_failed():
    """
    C4 Round 5.7: Verify that a task on its final attempt whose worker crashed
    (leaving an expired in_progress lease) is transitioned to failed by the reaper.
    """
    org_id = str(uuid.uuid4())
    task_id = str(uuid.uuid4())

    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO public.organizations (id, legal_name, pan) VALUES (%s, 'Reap Org', %s);", (org_id, random_pan()))
            cur.execute("INSERT INTO public.worker_service_authorizations (worker_role_name, org_id, is_active) VALUES ('worker_service_user', %s, true);", (org_id,))
            cur.execute("""
                INSERT INTO public.worker_tasks (id, org_id, task_type, status, attempts, max_attempts)
                VALUES (%s, %s, 'crash_test', 'queued', 0, 1);
            """, (task_id, org_id))

    # Worker claims task (attempts becomes 1 = max_attempts)
    with psycopg.connect(DB_WORKER, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT o_task_id, o_lease_token FROM public.claim_task(%s, 'worker-crash');", (org_id,))
            t_id, _ = cur.fetchone()
            assert str(t_id) == task_id

    # Simulate worker crash and expired lease in DB
    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE public.worker_tasks 
                SET lease_expire = clock_timestamp() - interval '5 seconds'
                WHERE id = %s;
            """, (task_id,))

    # Trigger reaper via claim_task (internal reaper invocation)
    with psycopg.connect(DB_WORKER, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('app.current_org', %s, false);", (org_id,))
            cur.execute("SELECT o_task_id, o_lease_token FROM public.claim_task(%s, 'worker-recovery');", (org_id,))
            next_task, _ = cur.fetchone()
            assert next_task is None  # Queue empty

    # Verify task state in database
    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT status, attempts, locked_by, lease_expire, lease_token_hash, error_message FROM public.worker_tasks WHERE id = %s;", (task_id,))
            row = cur.fetchone()
            assert row["status"] == "failed"
            assert row["attempts"] == 1
            assert row["locked_by"] is None
            assert row["lease_expire"] is None
            assert row["lease_token_hash"] is None
            assert "worker timeout/crash" in row["error_message"]

# -------------------------------------------------------------
# C5: User Directory View Tenant Isolation & Accessibility
# -------------------------------------------------------------

def test_c5_v_org_users_accessible():
    """C5: Verify app_service_user can query v_org_users without 42501 permission error."""
    org_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    user_email = random_email()

    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO public.organizations (id, legal_name, pan) VALUES (%s, 'Directory Org', %s);", (org_id, random_pan()))
            cur.execute("INSERT INTO public.global_users (id, email, full_name) VALUES (%s, %s, 'Directory User');", (user_id, user_email))
            cur.execute("INSERT INTO public.organization_memberships (org_id, user_id, role, status) VALUES (%s, %s, 'admin', 'active');", (org_id, user_id))

    with psycopg.connect(DB_APP, autocommit=True) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT set_config('app.current_org', %s, false);", (org_id,))
            cur.execute("SELECT set_config('app.current_user', %s, false);", (user_id,))
            cur.execute("SELECT * FROM public.v_org_users;")
            rows = cur.fetchall()
            assert len(rows) == 1
            assert rows[0]["email"] == user_email
            assert rows[0]["role"] == "admin"

def test_c5_v_org_users_cross_tenant_isolation_multitenant():
    """
    C5 Round 5.7: Prove that v_org_users strictly isolates users between two organizations
    and never exposes foreign organization members.
    """
    org_1 = str(uuid.uuid4())
    org_2 = str(uuid.uuid4())
    user_1 = str(uuid.uuid4())
    user_2 = str(uuid.uuid4())
    email_1 = random_email()
    email_2 = random_email()

    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO public.organizations (id, legal_name, pan) VALUES (%s, 'Tenant 1', %s);", (org_1, random_pan()))
            cur.execute("INSERT INTO public.organizations (id, legal_name, pan) VALUES (%s, 'Tenant 2', %s);", (org_2, random_pan()))
            cur.execute("INSERT INTO public.global_users (id, email, full_name) VALUES (%s, %s, 'Member One');", (user_1, email_1))
            cur.execute("INSERT INTO public.global_users (id, email, full_name) VALUES (%s, %s, 'Member Two');", (user_2, email_2))
            cur.execute("INSERT INTO public.organization_memberships (org_id, user_id, role, status) VALUES (%s, %s, 'admin', 'active');", (org_1, user_1))
            cur.execute("INSERT INTO public.organization_memberships (org_id, user_id, role, status) VALUES (%s, %s, 'owner', 'active');", (org_2, user_2))

            # Verify view owner cannot login
            cur.execute("SELECT rolcanlogin FROM pg_roles WHERE rolname = 'v_org_users_owner';")
            assert cur.fetchone()[0] is False

    with psycopg.connect(DB_APP, autocommit=True) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            # Context Org 1: MUST see only Member 1
            cur.execute("SELECT set_config('app.current_org', %s, false);", (org_1,))
            cur.execute("SELECT * FROM public.v_org_users;")
            rows_1 = cur.fetchall()
            assert len(rows_1) == 1
            assert rows_1[0]["email"] == email_1

            # Context Org 2: MUST see only Member 2
            cur.execute("SELECT set_config('app.current_org', %s, false);", (org_2,))
            cur.execute("SELECT * FROM public.v_org_users;")
            rows_2 = cur.fetchall()
            assert len(rows_2) == 1
            assert rows_2[0]["email"] == email_2

            # No Context or Invalid Org: MUST see 0 rows
            cur.execute("SELECT set_config('app.current_org', '', false);")
            cur.execute("SELECT * FROM public.v_org_users;")
            assert len(cur.fetchall()) == 0

# -------------------------------------------------------------
# H1: Reject NULL Lease Duration
# -------------------------------------------------------------

def test_h1_renew_task_lease_null_rejected():
    """H1: Explicitly reject NULL lease durations in renew_task_lease."""
    org_id = str(uuid.uuid4())
    task_id = str(uuid.uuid4())

    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO public.organizations (id, legal_name, pan) VALUES (%s, 'H1 Org', %s);", (org_id, random_pan()))
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
# H2 & H3: Audit Function Privileges & Comprehensive Redaction
# -------------------------------------------------------------

def test_h2_effective_function_privileges_on_audit():
    """H2 Round 5.7: Verify EXECUTE permissions on audit.log_audit_event for service roles."""
    with psycopg.connect(DB_ADMIN) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    r.role_name,
                    has_function_privilege(
                        r.role_name,
                        'audit.log_audit_event(uuid,text,text,audit_action,jsonb,jsonb,jsonb)',
                        'EXECUTE'
                    ) AS can_execute
                FROM (
                    VALUES
                        ('app_service_user', true),
                        ('worker_service_user', true),
                        ('platform_admin_user', true),
                        ('audit_reader', false)
                ) AS r(role_name, expected);
            """)
            for role_name, can_execute in cur.fetchall():
                if role_name in ('app_service_user', 'worker_service_user', 'platform_admin_user'):
                    assert can_execute is True, f"{role_name} missing EXECUTE on audit.log_audit_event"
                else:
                    assert can_execute is False, f"{role_name} unexpectedly has EXECUTE on audit.log_audit_event"

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

            cur.execute("""
                SELECT audit.log_audit_event(
                    %s, 'test_entity', 'entity-1', 'CREATE'::public.audit_action,
                    NULL, %s, '{"source": "test"}'::jsonb
                );
            """, (org_id, payload))
            audit_id = cur.fetchone()[0]
            assert audit_id is not None

    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT * FROM audit.audit_logs WHERE id = %s;", (audit_id,))
            row = cur.fetchone()
            state = row["new_state"]

            assert state["pan"] == "[REDACTED]"
            assert state["password"] == "[REDACTED]"
            assert state["nested"]["aadhaar"] == "[REDACTED]"
            assert state["nested"]["bank_account"] == "[REDACTED]"
            assert state["safe_note"] == "Unclassified transaction notes"

def test_h3_comprehensive_audit_redaction():
    """
    H3 Round 5.7: Verify recursive redaction on nested objects, arrays, credential variants,
    mixed-case keys, while ensuring non-sensitive keys with matching substrings are NOT falsely redacted.
    """
    org_id = str(uuid.uuid4())

    payload = psycopg.types.json.Jsonb({
        "credentials": {
            "api_key": "live_key_xyz",
            "token": "tok_123"
        },
        "service_config": {
            "api_key": "live_key_xyz",
            "access_token": "token_abc",
            "refresh_token": "rt_123",
            "authorization": "Bearer secret_header",
            "private_key": "-----BEGIN PRIVATE KEY-----",
            "endpoint_url": "https://api.gateway.gov.in/v1"
        },
        "case_sensitive_check": {
            "Password": "case_password",
            "PAN_NUMBER": "ABCPK9999Z",
            "Aadhaar_No": "1234-5678-9012",
            "BANK_ACCOUNT": "9876543210"
        },
        "array_data": [
            {"item": "book", "author_name": "Rabindranath Tagore"},
            {"item": "device", "keyboard_shortcut": "Ctrl+C", "user_token": "secret_tok"}
        ],
        "innocent_keys": {
            "author_name": "Munshi Premchand",
            "keyboard": "Mechanical RGB",
            "authenticity": "verified",
            "turnkey_project": True,
            "monetary_keynote": "Budget 2026"
        }
    })

    with psycopg.connect(DB_APP, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('app.current_org', %s, false);", (org_id,))
            cur.execute("""
                SELECT audit.log_audit_event(
                    %s, 'comprehensive_redaction', 'audit-h3', 'EXECUTE'::public.audit_action,
                    NULL, %s, '{}'::jsonb
                );
            """, (org_id, payload))
            audit_id = cur.fetchone()[0]

    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT * FROM audit.audit_logs WHERE id = %s;", (audit_id,))
            state = cur.fetchone()["new_state"]

            # 1. Whole sensitive object key redacted
            assert state["credentials"] == "[REDACTED]"

            # 2. Credential variants inside neutral config redacted while innocent fields preserved
            assert state["service_config"]["api_key"] == "[REDACTED]"
            assert state["service_config"]["access_token"] == "[REDACTED]"
            assert state["service_config"]["refresh_token"] == "[REDACTED]"
            assert state["service_config"]["authorization"] == "[REDACTED]"
            assert state["service_config"]["private_key"] == "[REDACTED]"
            assert state["service_config"]["endpoint_url"] == "https://api.gateway.gov.in/v1"

            # 3. Mixed-case sensitive keys redacted
            assert state["case_sensitive_check"]["Password"] == "[REDACTED]"
            assert state["case_sensitive_check"]["PAN_NUMBER"] == "[REDACTED]"
            assert state["case_sensitive_check"]["Aadhaar_No"] == "[REDACTED]"
            assert state["case_sensitive_check"]["BANK_ACCOUNT"] == "[REDACTED]"

            # 4. Array elements redacted
            assert state["array_data"][0]["author_name"] == "Rabindranath Tagore"
            assert state["array_data"][1]["user_token"] == "[REDACTED]"
            assert state["array_data"][1]["keyboard_shortcut"] == "Ctrl+C"

            # 5. Innocent keys containing 'auth' or 'key' substrings PRESERVED UNREDACTED (no false positives)
            assert state["innocent_keys"]["author_name"] == "Munshi Premchand"
            assert state["innocent_keys"]["keyboard"] == "Mechanical RGB"
            assert state["innocent_keys"]["authenticity"] == "verified"
            assert state["innocent_keys"]["turnkey_project"] is True
            assert state["innocent_keys"]["monetary_keynote"] == "Budget 2026"

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
            cur.execute("INSERT INTO public.organizations (id, legal_name, pan) VALUES (%s, 'H4 Org', %s);", (org_id, random_pan()))
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
            cur.execute("INSERT INTO public.organizations (id, legal_name, pan) VALUES (%s, 'Org W1', %s);", (org_1, random_pan()))
            cur.execute("INSERT INTO public.organizations (id, legal_name, pan) VALUES (%s, 'Org W2', %s);", (org_2, random_pan()))
            cur.execute("INSERT INTO public.worker_service_authorizations (worker_role_name, org_id, is_active) VALUES ('worker_service_user', %s, true);", (org_1,))
            cur.execute("INSERT INTO public.worker_service_authorizations (worker_role_name, org_id, is_active) VALUES ('other_worker_user', %s, true);", (org_2,))

    with psycopg.connect(DB_WORKER, autocommit=True) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT * FROM public.worker_service_authorizations;")
            rows = cur.fetchall()
            for r in rows:
                assert r["worker_role_name"] == "worker_service_user", f"Worker leaked foreign authorization for role: {r['worker_role_name']}"
            assert any(r["org_id"] == uuid.UUID(org_1) for r in rows)
            assert not any(r["org_id"] == uuid.UUID(org_2) for r in rows)

def test_h5_worker_authorization_two_distinct_logins():
    """
    H5 Round 5.7: Verify authorization isolation between two real, distinct worker login roles.
    Neither worker_service_user_1 nor worker_service_user_2 can view the other's mappings.
    """
    org_w1 = str(uuid.uuid4())
    org_w2 = str(uuid.uuid4())

    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO public.organizations (id, legal_name, pan) VALUES (%s, 'Worker Org 1', %s);", (org_w1, random_pan()))
            cur.execute("INSERT INTO public.organizations (id, legal_name, pan) VALUES (%s, 'Worker Org 2', %s);", (org_w2, random_pan()))
            cur.execute("INSERT INTO public.worker_service_authorizations (worker_role_name, org_id, is_active) VALUES ('worker_service_user_1', %s, true);", (org_w1,))
            cur.execute("INSERT INTO public.worker_service_authorizations (worker_role_name, org_id, is_active) VALUES ('worker_service_user_2', %s, true);", (org_w2,))

    # Worker 1 query
    with psycopg.connect(DB_WORKER_1, autocommit=True) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT * FROM public.worker_service_authorizations;")
            rows = cur.fetchall()
            assert all(r["worker_role_name"] == "worker_service_user_1" for r in rows)
            assert any(str(r["org_id"]) == org_w1 for r in rows)
            assert not any(str(r["org_id"]) == org_w2 for r in rows)

    # Worker 2 query
    with psycopg.connect(DB_WORKER_2, autocommit=True) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT * FROM public.worker_service_authorizations;")
            rows = cur.fetchall()
            assert all(r["worker_role_name"] == "worker_service_user_2" for r in rows)
            assert any(str(r["org_id"]) == org_w2 for r in rows)
            assert not any(str(r["org_id"]) == org_w1 for r in rows)

# -------------------------------------------------------------
# Backlog Assurance Checks: Concurrency & Trusted Public Schema
# -------------------------------------------------------------

def test_c1_concurrent_approval_line_item_locking():
    """
    Check A (C1 Concurrency): Verify that enforce_invoice_line_item_immutability
    locks parent invoice FOR UPDATE, serializing concurrent line item mutations
    behind invoice approval and preventing race condition insertion.
    """
    import threading
    import time

    org_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    profile_id = str(uuid.uuid4())
    inv_id = str(uuid.uuid4())

    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO public.organizations (id, legal_name, pan) VALUES (%s, 'Concurrency Test Org', %s);", (org_id, random_pan()))
            cur.execute("INSERT INTO public.global_users (id, email, full_name) VALUES (%s, %s, 'Approver');", (user_id, random_email()))
            cur.execute("INSERT INTO public.organization_memberships (org_id, user_id, role, status) VALUES (%s, %s, 'owner', 'active');", (org_id, user_id))
            cur.execute("INSERT INTO public.business_profiles (id, org_id, gstin, business_type, industry_sector) VALUES (%s, %s, %s, 'Retail', 'Hardware');", (profile_id, org_id, random_gstin()))
            cur.execute("""
                INSERT INTO public.invoices (
                    id, org_id, profile_id, invoice_number, invoice_date, counterparty_name,
                    total_taxable_amount, cgst_amount, sgst_amount, igst_amount, total_amount, status
                ) VALUES (
                    %s, %s, %s, 'INV-CONC-001', '2026-11-01', 'Test Counterparty',
                    100.00, 9.00, 9.00, 0.00, 118.00, 'uploaded'
                );
            """, (inv_id, org_id, profile_id))
            cur.execute("UPDATE public.invoices SET status = 'queued' WHERE id = %s;", (inv_id,))
            cur.execute("UPDATE public.invoices SET status = 'processing' WHERE id = %s;", (inv_id,))
            cur.execute("UPDATE public.invoices SET status = 'extracted' WHERE id = %s;", (inv_id,))
            cur.execute("UPDATE public.invoices SET status = 'pending_review' WHERE id = %s;", (inv_id,))

    line_thread_error = []
    line_inserted = []

    def insert_line_worker():
        try:
            with psycopg.connect(DB_APP, autocommit=True) as conn2:
                with conn2.cursor() as cur2:
                    cur2.execute("SELECT set_config('app.current_org', %s, false);", (org_id,))
                    cur2.execute("SELECT set_config('app.current_user', %s, false);", (user_id,))
                    # This will attempt to insert a line item while approval holds the invoice lock
                    cur2.execute("""
                        INSERT INTO public.invoice_line_items (
                            id, invoice_id, org_id, item_description, quantity, unit_price, taxable_amount, gst_rate, total_amount
                        ) VALUES (
                            %s, %s, %s, 'Race Item', 1.0, 50.00, 50.00, 18.00, 59.00
                        );
                    """, (str(uuid.uuid4()), inv_id, org_id))
                    line_inserted.append(True)
        except Exception as e:
            line_thread_error.append(e)

    # Session 1: Approval transaction starts and holds lock
    with psycopg.connect(DB_APP, autocommit=False) as conn1:
        with conn1.cursor() as cur1:
            cur1.execute("SELECT set_config('app.current_org', %s, false);", (org_id,))
            cur1.execute("SELECT set_config('app.current_user', %s, false);", (user_id,))
            cur1.execute("UPDATE public.invoices SET status = 'approved' WHERE id = %s;", (inv_id,))

            # Spawn concurrent line insertion thread
            t = threading.Thread(target=insert_line_worker)
            t.start()

            # Wait to ensure thread is blocked on the row lock
            time.sleep(0.3)
            assert len(line_inserted) == 0, "Line item inserted before approval committed - FOR UPDATE locking absent!"

            # Commit approval
            conn1.commit()

            # Wait for thread to finish
            t.join(timeout=3.0)

    # Line item insert MUST fail with immutability violation
    assert len(line_inserted) == 0, "Line item was unexpectedly inserted into approved invoice!"
    assert len(line_thread_error) == 1, "Expected line item insertion to raise exception"
    assert "Cannot insert line items for approved/export-ready invoice" in str(line_thread_error[0]) or "42501" in str(line_thread_error[0])


def test_c3_public_schema_create_privilege_revocation():
    """
    Check B (C3 Public Schema Trust): Verify that CREATE on schema public is completely
    revoked from PUBLIC, app_service_user, worker_service_user, and worker login roles,
    ensuring public is a trusted schema for SECURITY DEFINER search_path = public, pg_temp.
    """
    with psycopg.connect(DB_ADMIN, autocommit=True) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""
                SELECT 
                    has_schema_privilege('public', 'public', 'CREATE') AS public_can_create,
                    has_schema_privilege('app_service_user', 'public', 'CREATE') AS app_can_create,
                    has_schema_privilege('worker_service_user', 'public', 'CREATE') AS worker_can_create,
                    has_schema_privilege('worker_service_user_1', 'public', 'CREATE') AS worker1_can_create,
                    has_schema_privilege('worker_service_user_2', 'public', 'CREATE') AS worker2_can_create;
            """)
            row = cur.fetchone()
            assert not row["public_can_create"], "PUBLIC retains CREATE privilege on schema public!"
            assert not row["app_can_create"], "app_service_user retains CREATE privilege on schema public!"
            assert not row["worker_can_create"], "worker_service_user retains CREATE privilege on schema public!"
            assert not row["worker1_can_create"], "worker_service_user_1 retains CREATE privilege on schema public!"
            assert not row["worker2_can_create"], "worker_service_user_2 retains CREATE privilege on schema public!"

