from typing import Generator, Optional
from contextlib import contextmanager
from sqlalchemy import create_engine, text, event
from sqlalchemy.orm import sessionmaker, Session
from app.config import settings

engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def verify_and_resolve_tenant_membership(db: Session, user_id: str, org_id: str) -> bool:
    """
    Server-side verification of active user membership before applying tenant context.
    Sets candidate org context so RLS policy org_memberships_tenant permits reading.
    Clears context if candidate membership is invalid or inactive.
    """
    set_tenant_context(db, org_id=org_id, user_id=user_id)
    stmt = text("""
        SELECT role 
        FROM public.organization_memberships
        WHERE org_id = :org_id 
          AND user_id = :user_id 
          AND status = 'active';
    """)
    result = db.execute(stmt, {"org_id": org_id, "user_id": user_id}).scalar()
    if not result:
        db.info.pop("current_org", None)
        db.info.pop("current_user", None)
        db.info.pop("current_role", None)
        session_clear_sql = "SELECT set_config('app.current_org', '', true), set_config('app.current_user', '', true);"
        db.execute(text(session_clear_sql))
        return False
    db.info["current_role"] = str(result)
    return True

def set_tenant_context(session: Session, org_id: Optional[str], user_id: Optional[str]) -> None:
    """
    Explicitly applies tenant context to the current database transaction.
    Enforces that once a session is bound to an authenticated tenant,
    cross-tenant switching within that same session is strictly prohibited.
    """
    if org_id:
        if isinstance(getattr(session, "info", None), dict):
            bound_org = session.info.get("current_org")
            if bound_org and str(bound_org) != str(org_id):
                raise PermissionError(
                    f"CRITICAL SECURITY VIOLATION: Attempted cross-tenant context switch "
                    f"from {bound_org} to {org_id} within the same session."
                )
        session.execute(text("SELECT set_config('app.current_org', :org, true);"), {"org": str(org_id)})
    if user_id:
        session.execute(text("SELECT set_config('app.current_user', :user, true);"), {"user": str(user_id)})

@contextmanager
def tenant_transaction(session: Session, org_id: Optional[str], user_id: Optional[str]):
    """
    Context manager guaranteeing transaction boundaries and re-applying tenant context
    if a commit or rollback terminates a previous transaction within the same connection.
    """
    if user_id and org_id:
        # Validate active membership server-side
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
    Yields an active database session. Tenant-scoped operations should be executed
    within tenant_transaction() to guarantee context persistence across transaction boundaries.
    """
    db = SessionLocal()
    try:
        if org_id or user_id:
            set_tenant_context(db, org_id, user_id)
        yield db
    finally:
        db.close()
