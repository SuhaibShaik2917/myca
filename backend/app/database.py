from typing import AsyncGenerator, Generator
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from app.config import settings

engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def get_db(org_id: str = None, user_id: str = None) -> Generator[Session, None, None]:
    """
    Yields an active database session with tenant context (app.current_org, app.current_user)
    strictly applied within the transaction for PostgreSQL Row-Level Security.
    """
    db = SessionLocal()
    try:
        if org_id:
            db.execute(text("SELECT set_config('app.current_org', :org, true);"), {"org": str(org_id)})
        if user_id:
            db.execute(text("SELECT set_config('app.current_user', :user, true);"), {"user": str(user_id)})
        yield db
    finally:
        db.close()
