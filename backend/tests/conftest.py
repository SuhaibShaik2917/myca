"""
Global Pytest Configuration and Shared Test Fixtures for MyCA.
Ensures reference data catalogs are seeded and manages isolated test statutory auditors.
"""

import os
os.environ.setdefault("ENVIRONMENT", "test")
import pytest
from datetime import datetime, timezone
from sqlalchemy import text
from tests.test_db_config import DB_ADMIN, get_admin_session
from app.modules.scheme_navigator.service import seed_schemes_catalog_if_needed

@pytest.fixture(scope="session", autouse=True)
def ensure_reference_catalogs_seeded():
    """
    Session-level autouse fixture ensuring reference tables such as
    public.government_schemes and public.scheme_eligibility_rules
    are seeded before any tests execute.
    """
    session = None
    try:
        session = get_admin_session()
        count = session.execute(text("SELECT count(*) FROM public.government_schemes;")).scalar() or 0
        if count == 0:
            seed_schemes_catalog_if_needed(session)
    except Exception as ex:
        # Fail visibly unless explicitly in offline unit test mode
        if os.environ.get("MYCA_OFFLINE_TEST_MODE") == "1":
            print(f"INFO [conftest]: Skipping reference catalog seeding in offline test mode: {ex}")
        else:
            raise RuntimeError(
                f"Failed to seed reference catalogs in conftest.py. Ensure PostgreSQL is reachable or set "
                f"MYCA_OFFLINE_TEST_MODE=1 for pure offline tests. Error: {ex}"
            ) from ex
    finally:
        if session is not None:
            session.close()

@pytest.fixture(scope="function")
def admin_db_session():
    """Provides a dedicated SQLAlchemy session bound to DB_ADMIN with automatic cleanup."""
    session = get_admin_session()
    try:
        yield session
    finally:
        session.close()

@pytest.fixture(scope="function")
def isolated_verified_auditor(admin_db_session):
    """
    Test fixture creating a strictly verified statutory auditor with complete provenance.
    Cleans up all associated records and replay logs on teardown.
    """
    from app.modules.reconciliation.itc_engine import onboard_verified_statutory_auditor
    verifier_id = "CA-FIXTURE-VERIFIED-999"
    now_utc = datetime.now(timezone.utc)

    onboard_verified_statutory_auditor(
        db=admin_db_session,
        auditor_id=verifier_id,
        membership_no="999888",
        firm_registration_no="999888N",
        name="CA Independent Auditor",
        role="CHARTERED_ACCOUNTANT",
        approved_by="ADMIN_TEST_FIXTURE",
        cop_status="ACTIVE",
        cop_valid_until=datetime(2030, 12, 31).date(),
        verification_source="MOCK_TEST_FIXTURE",
        verification_status="INDEPENDENTLY_VERIFIED",
        is_icai_verified=True,
        is_cop_verified=True,
        verified_at=now_utc,
        verification_evidence_ref="ICAI-EVIDENCE-FIXTURE-999",
        environment="test"
    )

    yield verifier_id

    # Teardown: purge test auditor and any replay logs created with this auditor
    try:
        admin_db_session.execute(text("DELETE FROM public.statutory_audit_signature_replays WHERE verifier_id = :vid;"), {"vid": verifier_id})
        admin_db_session.execute(text("DELETE FROM public.statutory_auditors WHERE auditor_id = :vid;"), {"vid": verifier_id})
        admin_db_session.commit()
    except Exception:
        admin_db_session.rollback()
