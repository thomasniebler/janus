"""Planner trigger events (JAN-362): what the planner sync found new or changed.

``PlannerService.sync_all`` records each new or changed task and calendar
event as a :class:`PendingPlannerEvent` while it syncs an account, then calls
:func:`publish` once that account's sync has committed, so a failed sync
publishes nothing. An account's first sync is a baseline and records nothing:
connecting an account must not start one run per existing item.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from janus import models, trigger_definition_service
from janus.time_utils import to_naive_utc
from janus.trigger_events import TriggerEvent

logger = logging.getLogger(__name__)

TASK_FIELDS = ("title", "status", "due_date")
CALENDAR_EVENT_FIELDS = ("summary", "start_time", "end_time", "location")


def to_utc(data: dict[str, Any]) -> dict[str, Any]:
    """A synced item with every datetime as naive UTC, the form the ``DateTime`` columns store (JAN-394).

    Connectors return aware datetimes in the event's own offset; SQLite would keep
    their wall time and drop the offset, so they are converted before storing.
    """
    return {key: to_naive_utc(value) if isinstance(value, datetime) else value for key, value in data.items()}


def _comparable(value: Any) -> Any:
    """A value in the form the columns store it: enums by value, datetimes as naive UTC."""
    if isinstance(value, datetime):
        return to_naive_utc(value)
    return getattr(value, "value", value)


def _display(value: Any) -> str:
    """Datetimes as explicit UTC (``+00:00``), so a run never mistakes them for local time."""
    value = _comparable(value)
    if isinstance(value, datetime):
        return value.replace(tzinfo=UTC).isoformat()
    return "" if value is None else str(value)


def snapshot(record: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    """The tracked fields of a stored task or calendar event, before the sync overwrites them."""
    return {name: _comparable(getattr(record, name)) for name in fields}


def apply(record: Any, data: dict[str, Any], fields: tuple[str, ...]) -> None:
    """Write the synced values of the tracked fields, the same list ``changes`` compares."""
    for name in fields:
        setattr(record, name, data.get(name))


def changes(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    """``field: old -> new`` lines for every tracked field whose value changed."""
    return [
        f"{name}: {_display(before[name])} -> {_display(after.get(name))}"
        for name in before
        if before[name] != _comparable(after.get(name))
    ]


@dataclass
class PendingPlannerEvent:
    """One event to publish after the account's sync committed."""

    event: TriggerEvent
    record: models.TaskRecord | models.CalendarEvent
    changes: list[str] = field(default_factory=list)


def _task_data(task: models.TaskRecord) -> dict[str, Any]:
    return {
        "task_id": task.id,
        "external_id": task.external_id,
        "title": task.title,
        "status": _display(task.status),
        "due_date": _display(task.due_date),
    }


def _calendar_event_data(event: models.CalendarEvent) -> dict[str, Any]:
    return {
        "calendar_event_id": event.id,
        "external_id": event.external_id,
        "summary": event.summary,
        "start_time": _display(event.start_time),
        "end_time": _display(event.end_time),
        "location": event.location,
    }


def publish(
    db: Session, account: models.PlannerAccount, pending: list[PendingPlannerEvent], *, synced_at: datetime
) -> None:
    """Emit the recorded events for ``account``'s owner; one failure does not stop the rest.

    A change is detected once, by the sync that stores it, so ``synced_at`` makes
    each detected change its own occurrence: a transition that repeats later
    (Todo -> Done, back, and Done again) fires again instead of being taken for
    a duplicate of the first.
    """
    for item in pending:
        try:
            is_task = isinstance(item.record, models.TaskRecord)
            data = {"account": account.name, "provider": _display(account.provider)}
            data.update(_task_data(item.record) if is_task else _calendar_event_data(item.record))
            event_id = f"{account.id}:{item.record.external_id}"
            if item.changes:
                data["changes"] = "\n".join(item.changes)
                digest = hashlib.sha256(data["changes"].encode()).hexdigest()[:16]
                event_id += f":{synced_at.isoformat()}:{digest}"
            trigger_definition_service.emit_event(db, account.user_id, item.event, event_id, data)
        except Exception as exc:
            db.rollback()
            logger.warning("Failed to publish %s for planner account %s: %s", item.event.value, account.id, exc)
