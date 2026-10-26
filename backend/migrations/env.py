from alembic import context
from sqlalchemy import create_engine, pool

from app.config import database_url
from app.models import Base

config = context.config


def run_migrations():
    if context.is_offline_mode():
        context.configure(url=database_url(), target_metadata=Base.metadata,
                          literal_binds=True, dialect_opts={"paramstyle": "named"})
        with context.begin_transaction():
            context.run_migrations()
        return
    # Tests inject a connection to a freshly isolated database/schema. No env used.
    connection = config.attributes.get("connection")
    if connection is not None:
        context.configure(connection=connection, target_metadata=Base.metadata)
        with context.begin_transaction():
            context.run_migrations()
        return
    engine = create_engine(database_url(), poolclass=pool.NullPool,
                           connect_args={"connect_timeout": 5})
    try:
        with engine.connect() as connection:
            context.configure(connection=connection, target_metadata=Base.metadata)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


run_migrations()
