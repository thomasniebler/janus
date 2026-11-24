"""Planner sync stores synced times as UTC (JAN-394)."""

from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from janus import models, planner_service
from janus import trigger_definition_service as definitions
from janus.agent_toolboxes.shared import iso
from janus.trigger_events import TriggerEvent

WINTER = timezone(timedelta(hours=1))  # Europe/Berlin, CET
SUMMER = timezone(timedelta(hours=2))  # Europe/Berlin, CEST


class _FakeConnector:
    def __init__(self):
        self.tasks: list[dict] = []
        self.events: list[dict] = []

    def fetch_tasks(self):
        return [dict(task) for task in self.tasks]

    def fetch_events(self, start, end):
        del start, end
        return [dict(event) for event in self.events]


@pytest.fixture
def planner(db, monkeypatch):
    """A Google Calendar account on a fake connector, subscribed to every planner event."""
    user = models.User(username="owner", hashed_password="x", is_admin=True)
    db.add(user)
    db.commit()
    account = models.PlannerAccount(name="Work", provider=models.Provider.GOOGLE_CALENDAR, user_id=user.id, token="x")
    db.add(account)
    db.commit()
    fake = _FakeConnector()
    monkeypatch.setattr(planner_service.PlannerService, "get_connector", lambda self, account: fake)
    for event in TriggerEvent:
        if event.value.startswith("planner."):
            definitions.create_definition(
                db,
                user.id,
                name=event.value,
                objective="React.",
                tool_ids=["core"],
                kind="event",
                event_type=event.value,
                timezone="UTC",
            )
    return SimpleNamespace(fake=fake, sync=lambda: planner_service.PlannerService(db).sync_all())


def _event(start: datetime) -> dict:
    return {"external_id": "E-1", "summary": "Standup", "start_time": start, "end_time": start + timedelta(minutes=15)}


@pytest.mark.parametrize(
    ("local_start", "utc_start"),
    [
        (datetime(2026, 11, 10, 10, 0, tzinfo=WINTER), datetime(2026, 11, 10, 9, 0)),
        (datetime(2026, 7, 10, 10, 0, tzinfo=SUMMER), datetime(2026, 7, 10, 8, 0)),
    ],
)
def test_synced_event_times_are_stored_as_utc(db, planner, local_start, utc_start):
    """A 10:00 Berlin event is stored as 09:00 (winter) or 08:00 (summer) UTC, not as its wall time."""
    planner.fake.events = [_event(local_start)]
    planner.sync()
    db.expire_all()
    stored = db.query(models.CalendarEvent).one()
    assert stored.start_time == utc_start
    assert stored.end_time == utc_start + timedelta(minutes=15)


def test_synced_task_due_dates_are_stored_as_utc(db, planner):
    """Task due dates with an offset are converted too."""
    planner.fake.tasks = [
        {
            "external_id": "T-1",
            "title": "File taxes",
            "status": models.TaskStatus.TODO,
            "due_date": datetime(2026, 11, 10, 18, 0, tzinfo=WINTER),
        }
    ]
    planner.sync()
    db.expire_all()
    assert db.query(models.TaskRecord).one().due_date == datetime(2026, 11, 10, 17, 0)


def test_offset_times_are_stable_across_syncs_and_reported_in_utc(db, planner):
    """An unchanged offset event fires no change; a moved one reports old and new times as explicit UTC."""
    planner.fake.events = [_event(datetime(2026, 11, 10, 10, 0, tzinfo=WINTER))]
    planner.sync()  # baseline
    planner.sync()
    planner.fake.events = [_event(datetime(2026, 11, 10, 11, 0, tzinfo=WINTER))]
    planner.sync()
    db.expire_all()
    runs = [
        run
        for run in db.query(models.RuntimeTrigger).all()
        if run.payload.get("event", {}).get("type") == TriggerEvent.PLANNER_CALENDAR_EVENT_CHANGED
    ]
    assert len(runs) == 1
    event = runs[0].payload["event"]
    assert event["start_time"] == "2026-11-10T10:00:00+00:00"
    assert "start_time: 2026-11-10T09:00:00+00:00 -> 2026-11-10T10:00:00+00:00" in event["changes"]


def test_tool_output_marks_stored_times_as_utc():
    """Janus's tools see stored (naive UTC) times with an explicit offset."""
    assert iso(datetime(2026, 11, 10, 9, 0)) == "2026-11-10T09:00:00+00:00"
    assert iso(datetime(2026, 11, 10, 10, 0, tzinfo=WINTER)) == "2026-11-10T10:00:00+01:00"
    assert iso(None) is None
    assert iso(datetime(2026, 11, 10, 9, 0, tzinfo=UTC)) == "2026-11-10T09:00:00+00:00"
