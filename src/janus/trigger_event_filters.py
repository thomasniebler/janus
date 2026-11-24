"""Payload filters and rate limits on event-activated trigger definitions (JAN-363).

A definition's ``event_filter`` is a list of conditions on the catalogued
payload fields of its event (:mod:`janus.trigger_events`); every condition
must match for an event to start a run. ``max_runs_per_hour`` caps how many
event runs one definition may start in any rolling hour. Both are evaluated
by ``trigger_definition_service.emit_event`` before a run is enqueued, so an
event that does not match, or is over the limit, never costs a model call.

Filter values are always user-authored (or approved by the user); they are
matched against event content and never read from it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from janus import models
from janus.trigger_events import EVENTS, TriggerEvent
from janus.triggers import TriggerKind

#: How a condition compares a payload field with its values (case-insensitive).
OPERATORS = ("in", "not_in", "contains")
MAX_CONDITIONS = 10
MAX_VALUES = 50
MAX_VALUE_CHARS = 200
MAX_RUNS_PER_HOUR_LIMIT = 1000


#: Pass as ``max_runs_per_hour`` to get the event's catalog default (a new definition).
CATALOG_DEFAULT: Any = object()

#: Column values of a definition that is not event-activated.
NO_EVENT_OPTIONS: dict[str, Any] = {"event_filter": None, "max_runs_per_hour": None}


class EventFilterError(ValueError):
    """A filter or rate limit that cannot be saved."""


def normalize_event_options(event: TriggerEvent, event_filter: Any, max_runs_per_hour: Any) -> dict[str, Any]:
    """Validate an event definition's filter and limit; ``CATALOG_DEFAULT`` takes the event's default limit."""
    if max_runs_per_hour is CATALOG_DEFAULT:
        max_runs_per_hour = EVENTS[event].default_max_runs_per_hour
    return {
        "event_filter": validate_event_filter(event_filter, event),
        "max_runs_per_hour": validate_max_runs_per_hour(max_runs_per_hour),
    }


def validate_event_filter(conditions: Sequence[Mapping[str, Any]] | None, event: TriggerEvent) -> list[dict] | None:
    """Return the normalized conditions for ``event``, or ``None`` for no filter.

    Each condition is ``{"field": <catalogued payload field>, "op": "in" |
    "not_in" | "contains", "values": [<non-blank strings>]}``.
    """
    if not conditions:
        return None
    if len(conditions) > MAX_CONDITIONS:
        raise EventFilterError(f"an event filter has at most {MAX_CONDITIONS} conditions")
    fields = {field.name for field in EVENTS[event].payload}
    normalized: list[dict] = []
    for condition in conditions:
        if not isinstance(condition, Mapping):
            raise EventFilterError("each filter condition must be an object with field, op and values")
        field = str(condition.get("field") or "").strip()
        op = str(condition.get("op") or "").strip()
        if field not in fields:
            raise EventFilterError(
                f"'{field}' is not a field of {event.value}; filterable fields: {', '.join(sorted(fields))}"
            )
        if op not in OPERATORS:
            raise EventFilterError(f"op must be one of: {', '.join(OPERATORS)}")
        raw_values = condition.get("values")
        if isinstance(raw_values, str) or not isinstance(raw_values, Sequence):
            raise EventFilterError("values must be a list of strings")
        values = [str(value).strip() for value in raw_values if str(value).strip()]
        if not values:
            raise EventFilterError(f"the condition on '{field}' needs at least one value")
        if len(values) > MAX_VALUES:
            raise EventFilterError(f"a condition has at most {MAX_VALUES} values")
        if any(len(value) > MAX_VALUE_CHARS for value in values):
            raise EventFilterError(f"a filter value has at most {MAX_VALUE_CHARS} characters")
        normalized.append({"field": field, "op": op, "values": values})
    return normalized


def validate_max_runs_per_hour(value: Any) -> int | None:
    """Return the limit as an int, or ``None`` for no limit."""
    if value is None or value == "":
        return None
    try:
        limit = int(value)
    except (TypeError, ValueError) as error:
        raise EventFilterError("max_runs_per_hour must be a whole number") from error
    if isinstance(value, float) and value != limit:
        raise EventFilterError("max_runs_per_hour must be a whole number")
    if not 1 <= limit <= MAX_RUNS_PER_HOUR_LIMIT:
        raise EventFilterError(f"max_runs_per_hour must be between 1 and {MAX_RUNS_PER_HOUR_LIMIT}")
    return limit


def _condition_matches(condition: Mapping[str, Any], data: Mapping[str, Any]) -> bool:
    raw = data.get(condition["field"])
    actual = "" if raw is None else str(raw).strip().casefold()
    values = [str(value).casefold() for value in condition["values"]]
    if condition["op"] == "in":
        return actual in values
    if condition["op"] == "not_in":
        return actual not in values
    return bool(actual) and any(value in actual for value in values)  # contains


def matches(conditions: Sequence[Mapping[str, Any]] | None, data: Mapping[str, Any]) -> bool:
    """Whether an event's raw payload passes every condition (no filter passes everything)."""
    return all(_condition_matches(condition, data) for condition in conditions or ())


def admit(
    db: Session,
    definition: models.TriggerDefinition,
    data: Mapping[str, Any],
    now: datetime | None = None,
    *,
    record: bool = True,
) -> bool:
    """Whether an event may start a run of ``definition``: its filter matches and it is under its limit.

    An event over the limit is recorded on the definition (``rate_limited_count``,
    ``last_rate_limited_at``) and committed, so dropped events stay visible;
    ``record=False`` only asks (the mail processor checks before it stores).
    """
    if not matches(definition.event_filter, data):
        return False
    if not definition.max_runs_per_hour:
        return True
    moment = (now or datetime.now(UTC)).astimezone(UTC).replace(tzinfo=None)
    runs = models.RuntimeTrigger
    recent = db.scalar(
        select(func.count(runs.id)).where(
            runs.definition_id == definition.id,
            runs.kind == TriggerKind.EVENT.value,
            runs.created_at > moment - timedelta(hours=1),
        )
    )
    if (recent or 0) < definition.max_runs_per_hour or not record:
        return (recent or 0) < definition.max_runs_per_hour
    definition.rate_limited_count = (definition.rate_limited_count or 0) + 1
    definition.last_rate_limited_at = moment
    db.commit()
    return False
