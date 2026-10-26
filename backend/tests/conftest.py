import os
from pathlib import Path
from uuid import uuid4

import pytest
from dotenv import dotenv_values
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import SQLAlchemyError

ROOT = Path(__file__).resolve().parents[2]


def migration_config(connection):
    config = Config(str(ROOT / "alembic.ini"))
    config.attributes["connection"] = connection
    return config


def load_test_database_url() -> str:
    # Whitelist this one setting. Never load DATABASE_URL or mutate os.environ.
    # Explicit environment values (including blank) override the local test file.
    value = os.environ.get("TEST_DATABASE_URL")
    if value is not None:
        return value
    values = dotenv_values(ROOT / ".env.test", interpolate=False)
    return values.get("TEST_DATABASE_URL") or ""


def require_test_database(value: str):
    try:
        url = make_url(value)
        if url.drivername != "postgresql+psycopg" or url.database != "proofline_test":
            raise ValueError
    except Exception:
        raise ValueError("Test URL must use postgresql+psycopg and database proofline_test") from None
    return url


@pytest.fixture(params=["sqlite", "postgresql"])
def db_engine(request, tmp_path):
    """Never read DATABASE_URL. Own each SQLite file or random PostgreSQL schema."""
    admin = None
    schema = None
    engine = None
    if request.param == "sqlite":
        engine = create_engine(f"sqlite:///{(tmp_path / 'pipeline.sqlite').as_posix()}",
                               connect_args={"check_same_thread": False})

        @event.listens_for(engine, "connect")
        def foreign_keys(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")
    else:
        value = load_test_database_url()
        if not value:
            pytest.skip("TEST_DATABASE_URL is blank/unset in environment and .env.test")
        try:
            url = require_test_database(value)  # Refuse unsafe URLs before any connection.
        except ValueError:
            pytest.fail("Unsafe/invalid TEST_DATABASE_URL: use PostgreSQL database proofline_test only.", pytrace=False)
        schema = "proofline_test_" + uuid4().hex
        admin = create_engine(url, connect_args={"connect_timeout": 5})
        try:
            with admin.begin() as connection:
                connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        except SQLAlchemyError:
            admin.dispose()
            pytest.fail("PostgreSQL test setup failed: check local credentials, server, and schema permissions. Connection details withheld.", pytrace=False)
        engine = create_engine(url, connect_args={"connect_timeout": 5,
                               "options": f"-csearch_path={schema}"})
    try:
        with engine.begin() as connection:
            command.upgrade(migration_config(connection), "head")
        yield engine
    finally:
        if engine is not None:
            engine.dispose()
        if admin is not None:
            # Only the schema this fixture created; never public or other schemas.
            with admin.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            admin.dispose()


@pytest.fixture(scope="session", autouse=True)
def protect_postgresql_test_connections():
    """Fail before network IO if any SQLAlchemy test tries another database."""
    def guard(dialect, connection_record, connection_args, connection_params):
        if dialect.name == "postgresql" and connection_params.get("dbname") != "proofline_test":
            raise RuntimeError("PostgreSQL tests are restricted to proofline_test")
    event.listen(Engine, "do_connect", guard)
    try:
        yield
    finally:
        event.remove(Engine, "do_connect", guard)


@pytest.fixture(autouse=True)
def disable_live_ai_by_default(monkeypatch):
    # No automated test may accidentally spend money or disclose data via local secrets.
    monkeypatch.setenv("AI_PROVIDER", "disabled")
