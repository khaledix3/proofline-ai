from datetime import datetime, timezone
import re

from sqlalchemy import DateTime
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator):
    """Timezone-aware UTC in Python, including SQLite's timezone-less storage."""
    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return as_utc(value) if value is not None else None

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:  # SQLite strips tzinfo from values normalized at write.
            value = value.replace(tzinfo=timezone.utc)
        return as_utc(value)


def as_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Timestamp must include a timezone")
    return value.astimezone(timezone.utc)


def parse_event_timestamp(value: str) -> datetime:
    text = value.strip()
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?(?:Z|[+-][0-9]{2}:[0-9]{2})", text):
        raise ValueError("Event timestamp must be ISO 8601 with explicit timezone")
    try:
        return as_utc(datetime.fromisoformat(text))
    except (ValueError, OverflowError):
        raise ValueError("Event timestamp cannot be represented in UTC") from None
