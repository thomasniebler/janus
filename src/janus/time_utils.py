"""Shared timestamp helpers."""

import logging
from datetime import UTC, datetime

_IMAP_MONTHS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)


logger = logging.getLogger(__name__)


def utcnow() -> datetime:
    """Returns the current UTC timestamp."""
    return datetime.now(UTC)


def format_imap_date(dt: datetime) -> str:
    """Formats a datetime as an IMAP-compliant SINCE date string (DD-Mon-YYYY).

    Uses a hard-coded English month mapping so the result is locale-independent.
    """
    return f"{dt.day:02d}-{_IMAP_MONTHS[dt.month - 1]}-{dt.year}"


def format_english_month_day(dt: datetime) -> str:
    """Formats a datetime as ``Mon DD`` using stable English month abbreviations."""
    return f"{_IMAP_MONTHS[dt.month - 1]} {dt.day:02d}"


def normalize_timestamp(value: datetime | str | None) -> datetime | None:
    """Normalizes timestamps into timezone-aware UTC datetimes."""
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def to_naive_utc(value: datetime | None) -> datetime | None:
    """Converts a datetime to naive UTC for comparison against stored ``DateTime`` columns.

    Naive inputs are assumed to already be UTC; aware inputs are converted first.
    """
    if value is None or value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


#: Epoch values above this are milliseconds; in seconds they would lie in the year 5138.
_MILLISECOND_EPOCH_THRESHOLD = 100_000_000_000
#: Provider timestamps later than this are implausible and treated as junk.
_MAX_PLAUSIBLE_EPOCH_SECONDS = 4_102_444_800  # 2100-01-01T00:00:00Z


def epoch_to_utc(raw: object) -> datetime:
    """Convert a provider epoch value, in seconds or milliseconds, to an aware UTC datetime.

    Neonize reports live ``MessageInfo.Timestamp`` in milliseconds and history-sync
    ``messageTimestamp`` in seconds (JAN-346). Non-numeric, non-positive, or implausibly
    late values fall back to now, with a warning so a unit change does not go unnoticed.
    """
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        logger.warning("Ignoring non-numeric provider timestamp %r; using the current time", raw)
        return utcnow()
    value = float(raw)
    if value > _MILLISECOND_EPOCH_THRESHOLD:
        value /= 1000.0
    if not 0 < value <= _MAX_PLAUSIBLE_EPOCH_SECONDS:
        logger.warning("Ignoring implausible provider timestamp %r; using the current time", raw)
        return utcnow()
    return datetime.fromtimestamp(value, tz=UTC)
