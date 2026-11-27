#!/usr/bin/env python3
"""
MyCA v1.0.6 — CI Database Connectivity & Role-Permission Diagnostics.
Executed before test runner to diagnose PostgreSQL readiness, verify role authentication,
validate reference catalog population, and ensure zero fabricated identities exist.
"""

import sys
import os
import psycopg
from sqlalchemy import create_engine, text

def _to_psycopg_dsn(url: str) -> str:
    if url.startswith("postgresql+psycopg://"):
        return "postgresql://" + url[len("postgresql+psycopg://"):]
    if url.startswith("postgres://"):
        return "postgresql://" + url[len("postgres://"):]
    return url


def _to_sqlalchemy_dsn(url: str) -> str:
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://"):]
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://"):]
    return url


def run_diagnostics() -> bool:
    print("=" * 70)
    print("MyCA v1.0.6 CI DATABASE & ROLE DIAGNOSTICS")
    print("=" * 70)

    # 1. Resolve connection URLs
    admin_url = (
        os.environ.get("MIGRATION_DATABASE_URL")
        or os.environ.get("DB_ADMIN_URL")
        or os.environ.get("DATABASE_ADMIN_URL")
        or os.environ.get("TEST_DATABASE_URL")
        or "postgresql://postgres:postgres@localhost:5432/myca"
    )
    app_url = (
        os.environ.get("DATABASE_URL")
        or os.environ.get("DB_APP_URL")
        or "postgresql://app_service_user:secret@localhost:5432/myca"
    )
    worker_url = (
        os.environ.get("DATABASE_WORKER_URL")
        or os.environ.get("DB_WORKER_URL")
        or "postgresql://worker_service_user:secret@localhost:5432/myca"
    )
    worker1_url = (
        os.environ.get("DATABASE_WORKER_1_URL")
        or os.environ.get("DB_WORKER_1_URL")
        or "postgresql://worker_service_user_1:secret@localhost:5432/myca"
    )
    worker2_url = (
        os.environ.get("DATABASE_WORKER_2_URL")
        or os.environ.get("DB_WORKER_2_URL")
        or "postgresql://worker_service_user_2:secret@localhost:5432/myca"
    )

    roles_to_check = [
        ("Admin / DDL", _to_psycopg_dsn(admin_url), "postgres"),
        ("App Service User", _to_psycopg_dsn(app_url), "app_service_user"),
        ("Worker Service User", _to_psycopg_dsn(worker_url), "worker_service_user"),
        ("Worker 1 Service User", _to_psycopg_dsn(worker1_url), "worker_service_user_1"),
        ("Worker 2 Service User", _to_psycopg_dsn(worker2_url), "worker_service_user_2"),
    ]

    all_ok = True

    print("\n--- 1. Testing Role Connectivity & Authentication ---")
    for role_label, url, expected_user in roles_to_check:
        try:
            with psycopg.connect(url) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT current_user, current_database();")
                    row = cur.fetchone()
                    actual_user, actual_db = row[0], row[1]
                    if actual_user == expected_user:
                        print(f"  [PASS] {role_label}: connected as '{actual_user}' to '{actual_db}'")
                    else:
                        print(f"  [WARN] {role_label}: connected as '{actual_user}' (expected '{expected_user}')")
        except Exception as ex:
            print(f"  [FAIL] {role_label}: Connection failed: {ex}")
            all_ok = False

    if not all_ok:
        print("\n[ERROR] Role connectivity diagnostic failed.")
        return False

    print("\n--- 2. Verifying Table Schemas & Catalogs ---")
    try:
        engine = create_engine(_to_sqlalchemy_dsn(admin_url))
        with engine.connect() as conn:
            # Check Alembic version
            ver_row = conn.execute(text("SELECT version_num FROM public.alembic_version;")).fetchone()
            current_rev = ver_row[0] if ver_row else "None"
            print(f"  [INFO] Alembic Head Revision: {current_rev}")

            # Check statutory_auditors table
            aud_count = conn.execute(text("SELECT count(*) FROM public.statutory_auditors;")).scalar() or 0
            print(f"  [INFO] Total Statutory Auditors in DB: {aud_count}")

            # Verify no fake identities exist
            fake_check = conn.execute(text("""
                SELECT count(*) FROM public.statutory_auditors 
                WHERE auditor_id IN ('CA-ICAI-098765', 'CA-ICAI-054321', 'CA-REVOKED-011223')
                   OR name IN ('CA Rajesh Kumar', 'CA Priya Sharma', 'CA Suresh Gupta');
            """)).scalar() or 0
            if fake_check > 0:
                print(f"  [FAIL] Detected {fake_check} fabricated auditor seed identities in database!")
                all_ok = False
            else:
                print("  [PASS] Zero fabricated auditor identities detected in database.")

            # Check statutory_audit_signature_replays table
            replay_count = conn.execute(text("SELECT count(*) FROM public.statutory_audit_signature_replays;")).scalar() or 0
            print(f"  [PASS] Replay tracking table exists (recorded tokens: {replay_count}).")

            # Check reference catalogues
            schemes_count = conn.execute(text("SELECT count(*) FROM public.government_schemes;")).scalar() or 0
            rules_count = conn.execute(text("SELECT count(*) FROM public.scheme_eligibility_rules;")).scalar() or 0
            print(f"  [INFO] Government Schemes count: {schemes_count}")
            print(f"  [INFO] Scheme Eligibility Rules count: {rules_count}")
            if schemes_count == 0:
                print("  [WARN] Government schemes reference catalogue is empty (will be auto-seeded before tests).")

    except Exception as ex:
        print(f"  [FAIL] Database schema validation error: {ex}")
        all_ok = False

    print("\n" + "=" * 70)
    if all_ok:
        print("ALL CI DATABASE DIAGNOSTICS PASSED")
    else:
        print("CI DATABASE DIAGNOSTICS FAILED")
    print("=" * 70 + "\n")
    return all_ok

if __name__ == "__main__":
    success = run_diagnostics()
    sys.exit(0 if success else 1)
