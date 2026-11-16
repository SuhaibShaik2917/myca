import pytest
import psycopg

DB_ADMIN = "postgresql://postgres:postgres@localhost:5432/myca"
DB_WORKER = "postgresql://worker_service_user:secret@localhost:5432/myca"
DB_APP = "postgresql://app_service_user:secret@localhost:5432/myca"

def test_effective_function_privileges():
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
