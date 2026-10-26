from functools import lru_cache

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config import ConfigurationError, database_url


@lru_cache(maxsize=1)
def get_engine():
    # Lazy: /health remains available without database credentials.
    return create_engine(database_url(), pool_pre_ping=True, connect_args={"connect_timeout": 5})


def get_session():
    try:
        engine = get_engine()
    except ConfigurationError:
        raise HTTPException(503, detail="DATABASE_URL is not configured correctly; see .env.example.") from None
    with Session(engine) as session:
        yield session
