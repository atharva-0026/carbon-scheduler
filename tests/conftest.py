import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.db import Base
from app.models.job import Job  # noqa: F401 -- ensure the model is registered on Base.metadata


@pytest.fixture
def test_db():
    """An isolated in-memory SQLite database for one test.

    Uses StaticPool so every `SessionLocal()` call in the test shares the
    same underlying connection/schema (plain sqlite:// otherwise gives each
    connection its own throwaway in-memory database).
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    yield session_factory
    Base.metadata.drop_all(engine)
    engine.dispose()
