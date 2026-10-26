import os
from pathlib import Path

from dotenv import dotenv_values, load_dotenv
from sqlalchemy.engine import make_url

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class ConfigurationError(RuntimeError):
    pass


def database_url() -> str:
    # Environment variables take precedence; resolve .env independently of cwd.
    load_dotenv(PROJECT_ROOT / ".env", override=False, interpolate=False)
    value = os.getenv("DATABASE_URL", "")
    if not value:
        raise ConfigurationError("DATABASE_URL is not configured; see .env.example.")
    try:
        url = make_url(value)
        if url.drivername != "postgresql+psycopg" or not url.database:
            raise ValueError("PostgreSQL psycopg URL required")
    except Exception:
        raise ConfigurationError("DATABASE_URL must use postgresql+psycopg and name a database.") from None
    return value


def lateness_threshold_seconds() -> int:
    # Read only this setting without importing database credentials into the process.
    value = os.environ.get("LATENESS_THRESHOLD_SECONDS")
    if value is None:
        value = dotenv_values(PROJECT_ROOT / ".env", interpolate=False).get("LATENESS_THRESHOLD_SECONDS", "300")
    try:
        threshold = int(value)
        if not 0 <= threshold <= 31536000:
            raise ValueError
    except (ValueError, TypeError):
        raise ConfigurationError("LATENESS_THRESHOLD_SECONDS must be an integer from 0 to 31536000.") from None
    return threshold
