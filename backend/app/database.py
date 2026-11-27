from typing import Generator, Optional
from contextlib import contextmanager
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from app.config import settings


def to_sqlalchemy_psycopg_url(url: str) -> str:
    """Normalize PostgreSQL URL to SQLAlchemy psycopg (v3) driver dialect."""
    if not url:
        return url
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def to_psycopg_conninfo(url: str) -> str:
    """Normalize PostgreSQL URL to plain libpq/psycopg URI for direct psycopg.connect()."""
    if not url:
        return url
    if url.startswith("postgresql+psycopg://"):
        return "postgresql://" + url[len("postgresql+psycopg://"):]
    if url.startswith("postgres://"):
        return "postgresql://" + url[len("postgres://"):]
    return url


engine = create_engine(
    to_sqlalchemy_psycopg_url(settings.DATABASE_URL),
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def verify_and_resolve_tenant_membership(db: Session, user_id: str, org_id: str) -> bool:
    """
    Server-side verification of active user account and active org membership before applying tenant context.
    Checks v_org_users strictly (enforcing global_users.is_active = true AND organization_memberships.status = 'active')
    and rejects requests without an active membership row.
    Clears context if candidate membership or user status is invalid or inactive.
    """
    set_tenant_context(db, org_id=org_id, user_id=user_id)
    stmt = text("""
        SELECT role
        FROM public.v_org_users
        WHERE org_id = :org_id
          AND id = :user_id
          AND membership_status = 'active';
    """)
    result = db.execute(stmt, {"org_id": org_id, "user_id": user_id}).scalar()
    if not result:
        db.info.pop("current_org", None)
        db.info.pop("current_user", None)
        db.info.pop("current_role", None)
        db.execute(text("SELECT set_config('app.current_org', '', true), set_config('app.current_user', '', true);"))
        return False
    db.info["current_role"] = str(result)
    return True


def set_tenant_context(session: Session, org_id: Optional[str], user_id: Optional[str]) -> None:
    """
    Explicitly applies tenant context to the current database transaction.
    Enforces that once a session is bound to an authenticated tenant,
    cross-tenant switching within that same session is strictly prohibited.
    """
    info = getattr(session, "info", None)
    if org_id:
        if isinstance(info, dict):
            bound_org = info.get("current_org")
            if bound_org and str(bound_org) != str(org_id):
                raise PermissionError(
                    f"CRITICAL SECURITY VIOLATION: Attempted cross-tenant context switch "
                    f"from {bound_org} to {org_id} within the same session."
                )
            info["current_org"] = str(org_id)
        session.execute(text("SELECT set_config('app.current_org', :org, true);"), {"org": str(org_id)})
    if user_id:
        if isinstance(info, dict):
            info["current_user"] = str(user_id)
        session.execute(text("SELECT set_config('app.current_user', :user, true);"), {"user": str(user_id)})


@contextmanager
def tenant_transaction(session: Session, org_id: Optional[str], user_id: Optional[str]):
    """
    Context manager guaranteeing transaction boundaries and re-applying tenant context
    if a commit or rollback terminates a previous transaction within the same connection.
    """
    if user_id and org_id:
        if not verify_and_resolve_tenant_membership(session, user_id, org_id):
            raise PermissionError(f"User {user_id} does not possess active membership in organization {org_id}")

    try:
        set_tenant_context(session, org_id, user_id)
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise


def get_db(org_id: Optional[str] = None, user_id: Optional[str] = None) -> Generator[Session, None, None]:
    """
    Yields a database session. If org_id and user_id are supplied, verifies active membership first.
    Tenant-scoped FastAPI routes must depend on get_tenant_db instead of get_db.
    """
    db = SessionLocal()
    try:
        if org_id and user_id:
            if not verify_and_resolve_tenant_membership(db, user_id, org_id):
                raise PermissionError(f"User {user_id} does not possess active membership in organization {org_id}")
        elif org_id or user_id:
            raise PermissionError("Incomplete tenant identity context: both org_id and user_id are required.")
        yield db
    finally:
        db.close()
