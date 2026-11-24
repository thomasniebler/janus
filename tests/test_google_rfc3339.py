"""Google Calendar time bounds must be single-suffix RFC3339 UTC strings."""

from datetime import UTC, datetime, timedelta, timezone

from janus.connectors.google import _rfc3339_utc


def test_aware_utc_datetime_has_one_suffix():
    """An aware UTC datetime yields `Z`, not `+00:00Z`."""
    assert _rfc3339_utc(datetime(2026, 11, 15, tzinfo=UTC)) == "2026-11-15T00:00:00Z"


def test_naive_datetime_is_treated_as_utc():
    """A naive datetime is read as UTC."""
    assert _rfc3339_utc(datetime(2026, 11, 15)) == "2026-11-15T00:00:00Z"


def test_other_offsets_are_converted_to_utc():
    """Non-UTC offsets are converted before formatting."""
    cet = timezone(timedelta(hours=1))
    assert _rfc3339_utc(datetime(2026, 11, 15, 1, tzinfo=cet)) == "2026-11-15T00:00:00Z"
