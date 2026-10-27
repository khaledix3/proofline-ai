from contextlib import contextmanager
from copy import deepcopy

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.models import Base

INPUT_TABLES = {"pipeline_runs", "raw_orders", "pipeline_contracts", "record_arrivals", "dataset_imports", "dataset_source_records"}


@contextmanager
def isolated_database(source, *, inputs_only=False, lifecycle=None):
    """A fresh private SQLite connection; no filesystem or external DB URL accepted."""
    engine = create_engine("sqlite+pysqlite:///:memory:", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})

    if lifecycle is not None:
        lifecycle["created"] += 1

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    try:
        Base.metadata.create_all(engine)
        with engine.begin() as connection:
            for table in Base.metadata.sorted_tables:
                if inputs_only and table.name not in INPUT_TABLES:
                    continue
                rows = source.tables.get(table.name, [])
                if rows:
                    connection.execute(table.insert(), deepcopy(rows))
        with Session(engine) as session:
            yield session
    finally:
        engine.dispose()  # Also runs after partial cloning, transformation, or verification failures.
        if lifecycle is not None:
            lifecycle["disposed"] += 1
