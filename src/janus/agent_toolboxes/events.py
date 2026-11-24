"""Introspection tools over the system event log and runtime trigger outcomes.

These tools are the read side of infrastructure that was previously
write-mostly: ``event_logger`` appends to ``models.Event`` and the plan runtime
appends outcomes to ``models.RuntimeTrigger``, but nothing except the human
Settings UI ever read them back. An agent reasoning about "did my last
automation actually work" needs the same view.

The two tools back two separate registry entries (JAN-195):

* ``list_my_trigger_history`` (entry ``trigger-history``) is scoped to the
  authenticated user's own rows and never includes the trigger's raw payload
  (scheduled/event integrations can attach arbitrary custom data to it -- only
  the safe, documented lifecycle fields are surfaced).
* ``list_recent_events`` (entry ``events``) reads the event log with the same
  visibility rule as ``GET /api/events``
  (:func:`janus.services.event_service.visible_events_condition`, JAN-272).
  The log is PII-masked on write by :func:`janus.event_logger.mask_pii`, but
  that only redacts email-address-shaped strings -- not subject lines or a
  classification failure's ``email_data`` -- so who may read a row is what
  keeps it private. In chat, an admin sees the whole log and anyone else
  their own rows; in every other task mode (every unattended run, and any
  caller that names no mode) the tool returns only rows owned by the run's
  owner -- never system rows (``user_id`` ``None``) and never another user's.

Both operations are strictly read-only.
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import BaseTool, tool
from sqlalchemy.orm import Session

from janus import models
from janus.agent_toolboxes.shared import (
    ToolContext,
    clamp_limit,
    iso,
    parse_event_type,
    parse_runtime_trigger_status,
    run_isolated_tool_mutation,
)
from janus.plan_state import PlanOutcome, load_plan_outcomes
from janus.services.event_service import visible_events_condition

#: Upper bound on rows any single introspection call may return.
MAX_INTROSPECTION_LIMIT = 100
DEFAULT_INTROSPECTION_LIMIT = 20


def _clamp_limit(limit: int) -> int:
    """Clamp a caller-supplied row limit into the supported range."""
    return clamp_limit(int(limit), MAX_INTROSPECTION_LIMIT)


def _decode_details(raw: str | None) -> Any:
    """Return event details as parsed JSON, falling back to the raw string."""
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw


def _serialize_event(event: models.Event) -> dict[str, Any]:
    """Return a tool-friendly payload for one system event."""
    event_type = event.event_type
    return {
        "id": event.id,
        "timestamp": iso(event.timestamp),
        "event_type": event_type.value if isinstance(event_type, models.EventType) else event_type,
        "source": event.source,
        "message": event.message,
        "details": _decode_details(event.details),
    }


def _serialize_trigger(trigger: models.RuntimeTrigger, outcome: PlanOutcome | None = None) -> dict[str, Any]:
    """Return a tool-friendly, payload-free view of one runtime trigger outcome.

    Deliberately omits the raw ``payload`` column: scheduled/event integrations
    can attach arbitrary custom data to it, and the documented guarantee (and
    the existing safe ``RuntimeTriggerResponse`` REST schema) is that history
    never exposes payload details to the model. ``objective`` is the one
    payload field that's always safe and useful to surface here, matching
    ``agent_toolboxes.triggers.serialize_trigger``'s equivalent lifecycle view.

    ``result``/``step_error`` come from the trigger's persisted plan state
    (JAN-192), not from the payload: they are what the run *produced* --
    the model's summary plus the trace of tools that actually ran -- which is
    a deliberately-surfaced execution record, categorically different from the
    untrusted ingress payload that stays hidden. ``last_error`` alone is not
    enough, because a retry clears it at claim time.
    """
    payload = trigger.payload or {}
    return {
        "trigger_id": trigger.trigger_id,
        "task_id": trigger.task_id,
        "kind": trigger.kind,
        "source": trigger.source,
        "status": trigger.status,
        "objective": payload.get("objective"),
        "attempt_count": trigger.attempt_count,
        "last_error": trigger.last_error,
        "result": outcome.result if outcome else None,
        "step_error": outcome.error if outcome else None,
        "created_at": iso(trigger.created_at),
        "claimed_at": iso(trigger.claimed_at),
        "completed_at": iso(trigger.completed_at),
        "next_attempt_at": iso(trigger.next_attempt_at),
    }


def build_event_tools(context: ToolContext, *, own_rows_only: bool = True) -> list[BaseTool]:
    """Build the read-only event-log tool, scoped to what the user may read (see the module docstring).

    ``own_rows_only`` (the fail-closed default) limits even an admin to their
    own rows; only a human-watched chat turn passes ``False``.
    """
    visibility = visible_events_condition(context.user, own_rows_only=own_rows_only)

    @tool("list_recent_events")
    def list_recent_events(
        event_type: str | None = None,
        source: str | None = None,
        limit: int = DEFAULT_INTROSPECTION_LIMIT,
    ) -> list[dict[str, Any]]:
        """List recent Janus event-log entries about you, newest first.

        Use this to find out what the system actually did or failed to do.
        ``event_type`` accepts INFO, WARNING, ERROR or SUCCESS. ``source``
        matches the logging component exactly (for example "PlannerService").
        """
        parsed_type = parse_event_type(event_type) if event_type is not None else None
        resolved_limit = _clamp_limit(limit)

        def query_events(db: Session) -> list[dict[str, Any]]:
            """Read the most recent matching events in an isolated session."""
            statement = db.query(models.Event)
            if visibility is not None:
                statement = statement.filter(visibility)
            if parsed_type is not None:
                statement = statement.filter(models.Event.event_type == parsed_type)
            if source:
                statement = statement.filter(models.Event.source == source)
            events = statement.order_by(models.Event.timestamp.desc(), models.Event.id.desc())
            return [_serialize_event(event) for event in events.limit(resolved_limit).all()]

        return run_isolated_tool_mutation(context, lambda db: db, query_events)

    return [list_recent_events]


def build_trigger_history_tools(context: ToolContext) -> list[BaseTool]:
    """Build the read-only, owner-scoped trigger-history tool."""
    user_id = context.user.id

    @tool("list_my_trigger_history")
    def list_my_trigger_history(
        status: str | None = None,
        limit: int = DEFAULT_INTROSPECTION_LIMIT,
    ) -> list[dict[str, Any]]:
        """List your own recent runtime triggers and how each one ended.

        Returns status, attempt_count, last_error and completed_at so you can
        check whether a previous automation succeeded before repeating it, plus
        ``result`` -- what the run actually produced, including which tools it
        invoked -- and ``step_error`` for a run that failed mid-execution.
        ``status`` accepts queued, running, waiting (paused until its delegated tasks
        finish), completed, failed, exhausted or cancelled.
        Only triggers owned by the current user are ever returned.
        """
        parsed_status = parse_runtime_trigger_status(status) if status is not None else None
        resolved_limit = _clamp_limit(limit)

        def query_triggers(db: Session) -> list[dict[str, Any]]:
            """Read this user's most recent trigger rows in an isolated session."""
            statement = db.query(models.RuntimeTrigger).filter(models.RuntimeTrigger.user_id == user_id)
            if parsed_status is not None:
                statement = statement.filter(models.RuntimeTrigger.status == parsed_status.value)
            triggers = statement.order_by(models.RuntimeTrigger.created_at.desc(), models.RuntimeTrigger.id.desc())
            records = triggers.limit(resolved_limit).all()
            # One batched lookup for the whole page rather than one per row.
            outcomes = load_plan_outcomes(db, user_id, [record.task_id for record in records])
            return [_serialize_trigger(record, outcomes.get(record.task_id)) for record in records]

        return run_isolated_tool_mutation(context, lambda db: db, query_triggers)

    return [list_my_trigger_history]
