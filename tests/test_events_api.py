"""Tests for the filterable, paginated system event log API (JAN-198)."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from janus import models, security
from janus.main import api

BASE = datetime(2026, 9, 1, 12, 0, 0)


@pytest.fixture
def authed_client(client):
    """Client authenticated as a stub admin, who reads the whole log (JAN-272)."""
    api.dependency_overrides[security.get_current_user] = lambda: SimpleNamespace(id=1, is_admin=True)
    try:
        yield client
    finally:
        api.dependency_overrides.pop(security.get_current_user, None)


@pytest.fixture
def seeded_events(db):
    """Seed five events one hour apart across two sources and types."""
    specs = [
        (models.EventType.INFO, "IMAPMonitor"),
        (models.EventType.ERROR, "IMAPMonitor"),
        (models.EventType.INFO, "PlannerService"),
        (models.EventType.ERROR, "PlannerService"),
        (models.EventType.SUCCESS, "IMAPMonitor"),
    ]
    rows = [
        models.Event(timestamp=BASE + timedelta(hours=hour), event_type=event_type, source=source, message=f"e{hour}")
        for hour, (event_type, source) in enumerate(specs)
    ]
    db.add_all(rows)
    db.commit()
    return rows


def _messages(body):
    return [item["message"] for item in body["items"]]


def test_events_require_authentication(client):
    """The event log is not readable anonymously."""
    assert client.get("/api/events").status_code == 401


def test_events_default_page_is_newest_first_with_metadata(authed_client, seeded_events):
    """Unfiltered listing returns newest first plus total/has_more."""
    body = authed_client.get("/api/events").json()
    assert _messages(body) == ["e4", "e3", "e2", "e1", "e0"]
    assert body["total"] == 5
    assert body["limit"] == 100
    assert body["offset"] == 0
    assert body["has_more"] is False
    assert body["items"][0]["event_type"] == "SUCCESS"


def test_events_pagination_limit_offset(authed_client, seeded_events):
    """limit/offset slice the ordered result and has_more reflects the remainder."""
    first = authed_client.get("/api/events", params={"limit": 2}).json()
    assert _messages(first) == ["e4", "e3"]
    assert first["has_more"] is True
    assert first["total"] == 5

    last = authed_client.get("/api/events", params={"limit": 2, "offset": 4}).json()
    assert _messages(last) == ["e0"]
    assert last["has_more"] is False


def test_events_filter_by_type_and_source(authed_client, seeded_events):
    """event_type and source filters combine and scope the total count."""
    body = authed_client.get("/api/events", params={"event_type": "ERROR", "source": "PlannerService"}).json()
    assert _messages(body) == ["e3"]
    assert body["total"] == 1

    by_source = authed_client.get("/api/events", params={"source": "IMAPMonitor"}).json()
    assert _messages(by_source) == ["e4", "e1", "e0"]


def test_events_filter_by_time_range(authed_client, seeded_events):
    """Start is inclusive, end is exclusive; aware bounds are converted to UTC."""
    body = authed_client.get(
        "/api/events",
        params={"start": (BASE + timedelta(hours=1)).isoformat(), "end": (BASE + timedelta(hours=3)).isoformat()},
    ).json()
    assert _messages(body) == ["e2", "e1"]

    # 14:00+02:00 == 12:00 UTC, so e0 (12:00 UTC) is included and nothing earlier.
    aware_start = datetime(2026, 9, 1, 14, 0, 0, tzinfo=timezone(timedelta(hours=2)))
    aware = authed_client.get("/api/events", params={"start": aware_start.isoformat(), "limit": 200}).json()
    assert aware["total"] == 5


@pytest.mark.parametrize(
    "params",
    [
        {"limit": 0},
        {"limit": 201},
        {"offset": -1},
        {"event_type": "BOGUS"},
        {"start": "2026-09-02T00:00:00", "end": "2026-09-01T00:00:00"},
    ],
)
def test_events_reject_invalid_params(authed_client, params):
    """Out-of-range pagination, unknown types, and inverted ranges are rejected."""
    assert authed_client.get("/api/events", params=params).status_code == 422


def test_event_sources_are_distinct_and_sorted(authed_client, seeded_events):
    """The sources endpoint feeds the frontend filter dropdown."""
    assert authed_client.get("/api/events/sources").json() == ["IMAPMonitor", "PlannerService"]


def test_a_non_admin_reads_only_their_own_events(client, db):
    """JAN-272: a non-admin sees only rows recorded as theirs -- no system rows, no other user's."""
    db.add_all(
        [
            models.Event(timestamp=BASE, source="Mine", message="mine", user_id=7),
            models.Event(timestamp=BASE + timedelta(hours=1), source="Theirs", message="theirs", user_id=8),
            models.Event(timestamp=BASE + timedelta(hours=2), source="System", message="system", user_id=None),
        ]
    )
    db.commit()
    api.dependency_overrides[security.get_current_user] = lambda: SimpleNamespace(id=7, is_admin=False)
    try:
        body = client.get("/api/events").json()
        sources = client.get("/api/events/sources").json()
    finally:
        api.dependency_overrides.pop(security.get_current_user, None)

    assert _messages(body) == ["mine"]
    assert body["total"] == 1
    assert sources == ["Mine"]


def test_an_admin_reads_every_owners_events_and_system_rows(authed_client, db):
    """Admins keep the whole-log view the System log page always had."""
    db.add_all(
        [
            models.Event(timestamp=BASE, source="A", message="user", user_id=7),
            models.Event(timestamp=BASE + timedelta(hours=1), source="B", message="system", user_id=None),
        ]
    )
    db.commit()

    assert _messages(authed_client.get("/api/events").json()) == ["system", "user"]
