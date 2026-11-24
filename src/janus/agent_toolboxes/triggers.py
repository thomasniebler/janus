"""Self-service runtime-trigger tools (JAN-184).

These tools let Janus schedule, inspect, and cancel its *own* follow-up work
instead of requiring a human HTTP call to ``/api/runtime/triggers``. They are a
thin, owner-scoped wrapper over the existing durable trigger machinery
(:mod:`janus.trigger_adapters` for envelope identity, :mod:`janus.trigger_service`
for persistence and claims) -- no new persistence or dispatch path is
introduced here.

Two invariants matter more than convenience:

* **Ownership.** Every read and write is filtered by ``owner_id=user.id`` inside
  :mod:`janus.trigger_service`, and the owner comes from the authenticated
  :class:`~janus.agent_toolboxes.shared.ToolContext`, never from a model
  argument. A user can neither see nor cancel another user's trigger, and a
  trigger owned by somebody else reads as "not found" rather than "forbidden"
  so trigger IDs cannot be probed.
* **Bounded self-scheduling.** ``create_scheduled_trigger`` refuses to create a
  new trigger once the owner already has
  :data:`~janus.trigger_service.MAX_ACTIVE_TRIGGERS_PER_OWNER` claimable
  triggers outstanding, from any surface.
* **Bounded chains (JAN-274).** In an unattended run (``trigger`` or
  ``automation`` mode) a follow-up is recorded as a child of the durable run
  that scheduled it (``RuntimeTrigger.parent_trigger_id``/``chain_depth``),
  and the toolbox refuses -- in code, whatever the prompt says -- a follow-up
  that would be more than :data:`MAX_UNATTENDED_CHAIN_DEPTH` runs deep, a
  run's :data:`MAX_FOLLOW_UPS_PER_RUN`-plus-first follow-up, the owner's
  :data:`MAX_UNATTENDED_FOLLOW_UPS_PER_DAY`-plus-first follow-up scheduled by
  unattended runs within a day, and any follow-up due sooner than
  :data:`MIN_UNATTENDED_FOLLOW_UP_DELAY`. The toolbox can only create one-time
  follow-ups, never a recurring schedule, so a run cannot set up a loop that
  outlives the chain. An unattended run may cancel only Janus's own
  follow-ups, never a run of a definition the user saved or an API trigger.
  An unattended step with no durable run identity schedules nothing.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from langchain_core.tools import BaseTool, tool
from sqlalchemy import select
from sqlalchemy.orm import Session

from janus import models
from janus import trigger_definition_service as definitions
from janus.agent_toolboxes.shared import ToolContext, iso, run_isolated_tool_mutation
from janus.tool_policy import UNATTENDED_TASK_MODES
from janus.trigger_adapters import ScheduledTriggerSpec
from janus.trigger_service import (
    MAX_ACTIVE_TRIGGERS_PER_OWNER,
    cancel_owned_trigger,
    count_active_triggers,
    count_follow_ups_of,
    count_unattended_follow_ups_since,
    enqueue_trigger,
    get_owned_trigger,
    list_owned_triggers,
)

#: Trigger source recorded for everything this toolbox creates, so
#: agent-authored triggers stay distinguishable from API/scheduler ingress in
#: the durable ``runtime_triggers`` table.
AGENT_TRIGGER_SOURCE = "agent-tool"

#: Upper bound on how far ahead Janus may schedule itself. Anything further out
#: is far more likely to be a model timestamp mistake than a real intention.
MAX_SCHEDULE_HORIZON = timedelta(days=365)

#: Loop guard for unattended runs (JAN-274). A root run (scheduled in chat, by
#: a person, the API or a definition's schedule) has depth 0; each follow-up an
#: unattended run schedules is one deeper. Two levels allow "check again
#: tomorrow, and once more after that" but never an open-ended polling chain.
MAX_UNATTENDED_CHAIN_DEPTH = 2
#: Follow-ups a single unattended run may schedule (cancelled ones count too,
#: so cancel-and-reschedule cannot get round it).
MAX_FOLLOW_UPS_PER_RUN = 3
#: Follow-ups one owner's unattended runs may schedule per rolling day, across
#: every chain -- the cap that also bounds a recurring definition whose every
#: run schedules something.
MAX_UNATTENDED_FOLLOW_UPS_PER_DAY = 10
UNATTENDED_FOLLOW_UP_WINDOW = timedelta(days=1)
#: Earliest an unattended run may schedule a follow-up for, so a chain can
#: never spin at worker-poll speed.
MIN_UNATTENDED_FOLLOW_UP_DELAY = timedelta(minutes=15)


def _parse_due_at(value: str) -> datetime:
    """Parse a future, timezone-aware ISO-8601 due timestamp."""
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError as error:
        raise ValueError(f"due_at must be an ISO-8601 timestamp (e.g. 2026-01-31T09:00:00+00:00): {error}") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("due_at must include a timezone offset so it is not read in the server timezone")
    due_at = parsed.astimezone(UTC)
    now = datetime.now(UTC)
    if due_at <= now:
        raise ValueError("due_at must be in the future; use it to schedule follow-up work, not to backdate it")
    if due_at - now > MAX_SCHEDULE_HORIZON:
        raise ValueError(f"due_at must be within {MAX_SCHEDULE_HORIZON.days} days from now")
    return due_at


def _parse_status(value: str) -> str:
    """Parse a trigger lifecycle status filter from its enum name or value."""
    cleaned = value.strip()
    for status in models.RuntimeTriggerStatus:
        if cleaned.lower() in {status.value.lower(), status.name.lower()}:
            return status.value
    options = ", ".join(status.value for status in models.RuntimeTriggerStatus)
    raise ValueError(f"status must be one of: {options}")


def _require_known_task_mode(value: str) -> str:
    """Reject task modes that no allowlist would ever authorize.

    Imported lazily because :mod:`janus.runtime_adapter` imports the registry
    that registers this toolbox.

    Note that this value is advisory: ``trigger_runtime`` deliberately forces
    trigger dispatch to the ``"trigger"`` task mode and never trusts the
    payload's mode, so recording it here is for the author's own auditing and
    for a future dispatcher that honors it -- it can never widen a trigger's
    effective tool allowlist.
    """
    from janus.runtime_adapter import KNOWN_TASK_MODES

    cleaned = value.strip()
    if cleaned not in KNOWN_TASK_MODES:
        options = ", ".join(sorted(KNOWN_TASK_MODES))
        raise ValueError(f"task_mode must be one of: {options}")
    return cleaned


def _require_trigger_budget(db: Session, owner_id: int) -> None:
    """Refuse to extend an owner's trigger backlog past the safeguard cap.

    The backlog cap (JAN-184) bounds every surface, a chat turn looping on
    ``create_scheduled_trigger`` included. Chains scheduled by unattended runs
    are bounded separately, by :func:`_unattended_lineage` (JAN-274).
    """
    active = count_active_triggers(db, owner_id)
    if active >= MAX_ACTIVE_TRIGGERS_PER_OWNER:
        raise ValueError(
            f"you already have {active} active scheduled triggers "
            f"(limit {MAX_ACTIVE_TRIGGERS_PER_OWNER}); cancel one before scheduling more"
        )


def _require_unattended_delay(due_at: datetime) -> None:
    """Refuse an unattended follow-up due sooner than :data:`MIN_UNATTENDED_FOLLOW_UP_DELAY`."""
    if due_at - datetime.now(UTC) < MIN_UNATTENDED_FOLLOW_UP_DELAY:
        minutes = int(MIN_UNATTENDED_FOLLOW_UP_DELAY.total_seconds() // 60)
        raise ValueError(f"an automation may only schedule a follow-up at least {minutes} minutes ahead")


def _unattended_lineage(db: Session, owner_id: int, run_trigger_id: str | None) -> tuple[str, int]:
    """Return ``(parent_trigger_id, chain_depth)`` for a follow-up an unattended run schedules, or refuse it.

    Enforced here, in the tool, rather than in the run's prompt (JAN-274):
    the chain depth, the per-run fan-out and the owner's daily cap. Fails
    closed when the run has no durable identity to chain from.
    """
    if run_trigger_id is None:
        raise PermissionError("this unattended step has no trigger run to schedule a follow-up from")
    parent = get_owned_trigger(db, owner_id, run_trigger_id)
    if parent is None:
        raise PermissionError("this unattended step's trigger run could not be found")
    depth = (parent.chain_depth or 0) + 1
    if depth > MAX_UNATTENDED_CHAIN_DEPTH:
        raise ValueError(
            f"this run is already a follow-up {parent.chain_depth} level(s) deep; an automation may chain "
            f"at most {MAX_UNATTENDED_CHAIN_DEPTH} follow-ups, so it cannot schedule another one"
        )
    if count_follow_ups_of(db, owner_id, parent.trigger_id) >= MAX_FOLLOW_UPS_PER_RUN:
        raise ValueError(f"this run has already scheduled {MAX_FOLLOW_UPS_PER_RUN} follow-ups, the most one run may")
    since = datetime.now(UTC) - UNATTENDED_FOLLOW_UP_WINDOW
    if count_unattended_follow_ups_since(db, owner_id, since) >= MAX_UNATTENDED_FOLLOW_UPS_PER_DAY:
        raise ValueError(
            f"automations have already scheduled {MAX_UNATTENDED_FOLLOW_UPS_PER_DAY} follow-ups in the last day, "
            "the most allowed; try again later or schedule it from chat"
        )
    return parent.trigger_id, depth


def _schedule_id(owner_id: int, task_id: str, objective: str) -> str:
    """Derive a stable schedule identity so an identical re-ask deduplicates.

    The durable store deduplicates on ``(task_id, idempotency_key)`` and
    ``ScheduledTriggerSpec`` builds that key from ``schedule_id`` plus the due
    timestamp. Deriving the schedule ID from the owner, task, and objective
    means a model that repeats the same request for the same moment reuses the
    existing row instead of stacking duplicates.
    """
    return str(uuid5(NAMESPACE_URL, f"janus://agent-schedule/{owner_id}/{task_id}/{objective}"))


#: Longest definition name derived from a follow-up's objective.
_FOLLOW_UP_NAME_CHARS = 80


def _follow_up_name(objective: str) -> str:
    """Name a follow-up definition after the first line of its objective."""
    first_line = (objective.strip().splitlines() or [""])[0].strip() or "Follow-up"
    if len(first_line) > _FOLLOW_UP_NAME_CHARS:
        first_line = first_line[: _FOLLOW_UP_NAME_CHARS - 1].rstrip() + "…"
    return f"Follow-up: {first_line}"


def _discard_definition(db: Session, definition_id: int) -> None:
    """Delete a follow-up definition that ended up without a run of its own."""
    definition = db.get(models.TriggerDefinition, definition_id)
    if definition is not None:
        db.delete(definition)
        db.commit()


def _iso_utc(value: datetime | None) -> str | None:
    """Serialize a ``RuntimeTrigger`` timestamp as an explicitly UTC-aware string.

    These columns are stored timezone-naive (see ``trigger_service._database_time``)
    but are always UTC; the shared ``iso()`` helper would serialize them without an
    offset, which reads as local time and cannot round-trip back into
    ``_parse_due_at`` (which requires an explicit offset). Attach it here instead.
    """
    if value is None:
        return None
    return iso(value.replace(tzinfo=UTC) if value.tzinfo is None else value)


def serialize_trigger(record: models.RuntimeTrigger) -> dict[str, Any]:
    """Return a tool-friendly, owner-safe view of one durable trigger.

    ``created_at`` carries the scheduled occurrence for a scheduled trigger and
    the ingress/occurrence time for every other kind (that is how
    ``ScheduledTriggerSpec`` encodes a due time), so ``scheduled_for`` is only
    populated for scheduled triggers rather than relabelling an unrelated
    timestamp on the manual and event triggers this listing also returns.
    """
    payload = record.payload or {}
    is_scheduled = record.kind == models.RuntimeTriggerKind.SCHEDULED.value
    return {
        "trigger_id": record.trigger_id,
        "task_id": record.task_id,
        "kind": record.kind,
        "source": record.source,
        "status": record.status,
        "objective": payload.get("objective"),
        "task_mode": payload.get("task_mode"),
        "scheduled_for": _iso_utc(record.created_at) if is_scheduled else None,
        "created_at": _iso_utc(record.created_at),
        "claimed_at": _iso_utc(record.claimed_at),
        "completed_at": _iso_utc(record.completed_at),
        "attempt_count": record.attempt_count,
        "next_attempt_at": _iso_utc(record.next_attempt_at),
        "last_error": record.last_error,
        "parent_trigger_id": record.parent_trigger_id,
        "chain_depth": record.chain_depth or 0,
    }


def build_trigger_tools(  # noqa: C901
    context: ToolContext, *, task_mode: str | None = None, run_trigger_id: str | None = None
) -> list[BaseTool]:
    """Build the owner-scoped self-service trigger toolbox.

    ``task_mode`` and ``run_trigger_id`` come from the plan step being built
    (``ToolBuildContext``), never from the model: in an unattended mode the
    chained-scheduling guard applies, with ``run_trigger_id`` as the parent.
    """
    user_id = context.user.id
    unattended = task_mode in UNATTENDED_TASK_MODES

    @tool("create_scheduled_trigger")
    def create_scheduled_trigger(
        due_at: str,
        objective: str,
        task_id: str,
        task_mode: str = "trigger",
    ) -> dict[str, Any]:
        """Schedule follow-up work for yourself at a future time.

        ``due_at`` is an ISO-8601 timestamp that must include a timezone offset
        and lie in the future. ``objective`` is what should be done when the
        trigger fires, and ``task_id`` groups related runs. Re-requesting the
        same objective for the same task and moment returns the existing
        trigger instead of creating a duplicate.
        """
        parsed_due_at = _parse_due_at(due_at)
        if unattended:
            _require_unattended_delay(parsed_due_at)
        parsed_task_mode = _require_known_task_mode(task_mode)
        spec = ScheduledTriggerSpec(
            schedule_id=_schedule_id(user_id, task_id, objective),
            owner_id=user_id,
            task_id=task_id,
            objective=objective,
            due_at=parsed_due_at,
            source=AGENT_TRIGGER_SOURCE,
            task_mode=parsed_task_mode,
        )

        def create(db: Session) -> dict[str, Any]:
            """Enqueue inside one isolated session, enforcing the backlog cap only for a genuinely new trigger.

            A retry of an identical request must dedupe to the existing row even
            when the owner is currently at the cap -- it does not enlarge the
            backlog, so it must not be rejected by it.
            """
            envelope = spec.as_envelope()
            existing = db.scalar(
                select(models.RuntimeTrigger).where(
                    models.RuntimeTrigger.task_id == envelope.task_id,
                    models.RuntimeTrigger.idempotency_key == envelope.idempotency_key,
                )
            )
            if existing is not None:
                if existing.user_id != user_id:
                    raise PermissionError("that scheduled trigger is already owned by another user")
                return {**serialize_trigger(existing), "duplicate": True}
            parent_trigger_id, chain_depth = (
                _unattended_lineage(db, user_id, run_trigger_id) if unattended else (None, 0)
            )
            _require_trigger_budget(db, user_id)
            # Every follow-up is also an agent-authored, one-time definition
            # (JAN-257) so the user can see what Janus scheduled and why; the
            # run is linked to it in the same insert that queues it.
            definition = definitions.create_definition(
                db,
                user_id,
                origin=models.TriggerDefinitionOrigin.AGENT.value,
                name=_follow_up_name(objective),
                objective=objective,
                tool_ids=[],
                kind=models.TriggerDefinitionKind.SCHEDULE.value,
                schedule={"type": definitions.SCHEDULE_ONCE, "at": parsed_due_at.isoformat()},
                timezone="UTC",
            )
            # ``create_definition`` and ``enqueue_trigger`` each commit, so a
            # concurrent identical request (the enqueue dedupes to the other
            # call's run) or a failed enqueue would strand this definition:
            # remove it again so a follow-up never has a definition without its run.
            try:
                record, duplicate = enqueue_trigger(
                    db,
                    envelope,
                    definition_id=definition.id,
                    parent_trigger_id=parent_trigger_id,
                    chain_depth=chain_depth,
                )
            except Exception:
                db.rollback()
                _discard_definition(db, definition.id)
                raise
            if duplicate:
                _discard_definition(db, definition.id)
            if record.user_id != user_id:
                raise PermissionError("that scheduled trigger is already owned by another user")
            return {**serialize_trigger(record), "duplicate": duplicate}

        return run_isolated_tool_mutation(context, lambda db: db, create)

    @tool("list_my_triggers")
    def list_my_triggers(status: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        """List your own runtime triggers, newest first, optionally by status."""
        parsed_status = _parse_status(status) if status is not None else None
        bounded_limit = max(1, min(limit, 100))
        return [
            serialize_trigger(record)
            for record in list_owned_triggers(context.db, user_id, status=parsed_status, limit=bounded_limit)
        ]

    @tool("get_trigger")
    def get_trigger(trigger_id: str) -> dict[str, Any]:
        """Return one of your own runtime triggers by its trigger ID."""
        record = get_owned_trigger(context.db, user_id, trigger_id.strip())
        if record is None:
            raise ValueError(f"no trigger of yours has the ID: {trigger_id}")
        return serialize_trigger(record)

    @tool("cancel_trigger")
    def cancel_trigger(trigger_id: str) -> dict[str, Any]:
        """Cancel one of your own queued or retrying runtime triggers."""
        cleaned_id = trigger_id.strip()

        def cancel(db: Session) -> dict[str, Any]:
            """Cancel and re-read the row inside one isolated session."""
            if unattended:
                # With nobody watching, a run may withdraw Janus's own
                # follow-ups but never a run the user or an integration queued.
                target = get_owned_trigger(db, user_id, cleaned_id)
                if target is None:
                    raise ValueError(f"no trigger of yours has the ID: {cleaned_id}")
                if target.source != AGENT_TRIGGER_SOURCE:
                    raise PermissionError("an automation may only cancel follow-ups Janus scheduled itself")
            cancelled = cancel_owned_trigger(db, user_id, cleaned_id)
            record = get_owned_trigger(db, user_id, cleaned_id)
            if record is None:
                raise ValueError(f"no trigger of yours has the ID: {cleaned_id}")
            if cancelled and record.definition_id is not None:
                # Keep a follow-up's definition in step with its only run.
                definition = definitions.get_owned_definition(db, user_id, record.definition_id)
                if definitions.is_agent_follow_up(definition) and definition.enabled:
                    definitions.update_definition(db, definition, enabled=False)
            if not cancelled:
                raise ValueError(
                    f"trigger {cleaned_id} cannot be cancelled while it is {record.status}; "
                    "only queued or retrying triggers can be cancelled"
                )
            return serialize_trigger(record)

        return run_isolated_tool_mutation(context, lambda db: db, cancel)

    return [create_scheduled_trigger, list_my_triggers, get_trigger, cancel_trigger]
