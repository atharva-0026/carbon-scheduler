from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings

settings = get_settings()

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Create any tables that don't exist yet.

    No Alembic migrations are set up for this project -- this is the
    portfolio-project equivalent (idempotent, safe to call from both the
    API and the Celery worker on every startup). Models must already be
    imported (so they're registered on Base.metadata) before this runs.
    """
    Base.metadata.create_all(bind=engine)
