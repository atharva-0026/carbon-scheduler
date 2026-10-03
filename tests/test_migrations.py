"""
Tests for the Alembic migration setup added to wire up schema migrations
(see README's "Limitations" section: Alembic was in requirements.txt but
not yet used anywhere).

These run entirely against a throwaway SQLite file DB (Alembic's
autogenerate/compare needs a real on-disk or in-memory-with-shared-connection
DB to reflect against, unlike the ORM tests elsewhere which use an
in-memory StaticPool engine) -- no Postgres required, consistent with the
rest of the suite needing no external services.
"""
import os

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine

from app.core.db import Base
from app.models.job import Job  # noqa: F401 -- registers Job on Base.metadata

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "migration_test.db")


@pytest.fixture
def alembic_config(db_path):
    config = Config(os.path.join(REPO_ROOT, "alembic.ini"))
    config.set_main_option("script_location", os.path.join(REPO_ROOT, "migrations"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return config


def test_upgrade_head_creates_jobs_table_matching_the_orm_model(alembic_config, db_path):
    """Regression guard: `alembic upgrade head` on a fresh database must
    produce exactly the schema Base.metadata (i.e. the Job model) expects --
    the migration and the model are two separate sources of truth that
    could silently drift apart after this point."""
    command.upgrade(alembic_config, "head")

    engine = create_engine(f"sqlite:///{db_path}")
    with engine.connect() as connection:
        migration_context = MigrationContext.configure(connection)
        diff = compare_metadata(migration_context, Base.metadata)

    assert diff == [], f"migrated schema differs from the ORM model: {diff}"
    engine.dispose()


def test_downgrade_base_drops_the_jobs_table(alembic_config, db_path):
    command.upgrade(alembic_config, "head")
    command.downgrade(alembic_config, "base")

    engine = create_engine(f"sqlite:///{db_path}")
    with engine.connect() as connection:
        table_names = connection.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table' AND name != 'alembic_version'"
        ).fetchall()
    assert table_names == []
    engine.dispose()
