"""Shared helpers for Janus agent toolboxes."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, TypeVar

from sqlalchemy.orm import Session, sessionmaker

from janus import event_logger, models

MANAGEMENT_TOOL_SOURCE = "SkillManagementTools"


@dataclass(frozen=True)
class TurnToolCall:
    """One successful tool call of the current step (no arguments, no results)."""

    tool_id: str
    tool_name: str
    reads_untrusted_content: bool = False
    #: Prompt-injection rules that matched this call's output (JAN-127).
    injection_rules: tuple[str, ...] = ()


@dataclass
class TurnTrace:
    """Per-step record of the tool calls that actually ran (JAN-127).

    One trace exists per ``RuntimeAdapter.prepare_step`` -- one chat turn, or a
    whole unattended run -- and is never persisted. It is the shared "this
    turn has read untrusted content" signal: the runtime adapter uses
    :meth:`injection_suspected` to escalate consequential tools, and
    agent-proposed automations (JAN-235) can use
    :meth:`untrusted_tool_ids` to label drafts made after untrusted reads.
    """

    calls: list[TurnToolCall] = field(default_factory=list)

    def record(self, call: TurnToolCall) -> None:
        """Append one successful call."""
        self.calls.append(call)

    def tool_ids(self) -> tuple[str, ...]:
        """Return the registry ids of every successful call, first-seen order."""
        return tuple(dict.fromkeys(call.tool_id for call in self.calls))

    def untrusted_tool_ids(self) -> tuple[str, ...]:
        """Return the ids of successful calls whose output was untrusted content."""
        return tuple(dict.fromkeys(call.tool_id for call in self.calls if call.reads_untrusted_content))

    def has_read_untrusted_content(self) -> bool:
        """Return whether this step has read any untrusted content."""
        return any(call.reads_untrusted_content for call in self.calls)

    def detections(self) -> tuple[TurnToolCall, ...]:
        """Return the calls whose output matched a prompt-injection rule."""
        return tuple(call for call in self.calls if call.injection_rules)

    def injection_suspected(self) -> bool:
        """Return whether anything read in this step looked like a prompt injection."""
        return any(call.injection_rules for call in self.calls)


@dataclass(frozen=True)
class ToolContext:
    """Immutable context shared by all toolboxes in a tool set."""

    db: Session
    user: models.User
    conversation_id: int | None = None
    #: The real per-user connection-state snapshot (see
    #: `capability_profile.user_connected_capabilities`), computed once by
    #: the caller building this context. Every registry entry's `enabled_when`
    #: must read this instead of recomputing it, so every entry evaluated in
    #: one `authorize()`/`build()` sweep sees the same snapshot -- otherwise a
    #: connection added/removed mid-sweep could make different entries in the
    #: same request disagree about what's connected. This is intentionally a
    #: separate value from `ToolSelectionRequest.granted_capabilities` (the
    #: caller's own policy input for the capability-intersection tier), not a
    #: shared/aliased one -- see `ToolRegistry.authorize()`.
    connected_capabilities: frozenset[str] = frozenset()
    #: The step's trace of successful tool calls, set by
    #: ``RuntimeAdapter.prepare_step``; ``None`` outside a plan step.
    turn_trace: TurnTrace | None = field(default=None, compare=False)
    #: The chat turn's page-action channel (``services.ui_actions.PageActionTurn``,
    #: JAN-251), set by ``RuntimeAdapter.prepare_step`` for ``/ws/chat`` only;
    #: ``None`` everywhere else, where the ``ui_actions`` tools answer an error.
    page_actions: Any = field(default=None, compare=False)
    #: The chat turn's UI-block collector (``services.ui_blocks.UIBlockTurn``,
    #: JAN-252), set by ``RuntimeAdapter.prepare_step`` for ``/ws/chat`` only.
    ui_blocks: Any = field(default=None, compare=False)
    #: The chat connection's UI-introspection broker
    #: (``services.ui_introspection.UIIntrospectionBroker``, JAN-284), set by
    #: ``RuntimeAdapter.prepare_step`` for ``/ws/chat`` only; ``None``
    #: everywhere else, where the ``ui_introspection`` tools answer an error.
    ui_introspection: Any = field(default=None, compare=False)

    def turn_tool_ids(self) -> tuple[str, ...]:
        """Return the ids of the tools that ran successfully earlier in this step."""
        return self.turn_trace.tool_ids() if self.turn_trace is not None else ()


ServiceT = TypeVar("ServiceT")
ResultT = TypeVar("ResultT")


def run_isolated_tool_mutation(  # noqa: UP047
    context: ToolContext,
    service_factory: Callable[[Session], ServiceT],
    callback: Callable[[ServiceT], ResultT],
) -> ResultT:
    """Run a tool mutation in a short-lived session bound to the current engine."""
    mutation_session_factory = sessionmaker(bind=context.db.get_bind(), autocommit=False, autoflush=False)
    mutation_db = mutation_session_factory()
    try:
        return callback(service_factory(mutation_db))
    finally:
        mutation_db.close()


def clamp_limit(limit: int, maximum: int) -> int:
    """Clamp a caller-supplied row limit into ``1..maximum``."""
    return max(1, min(limit, maximum))


def iso(value: datetime | None) -> str | None:
    """Convert datetimes to ISO-8601 strings with an explicit offset (JAN-394).

    Stored datetimes are naive UTC, so a naive value gets ``+00:00``; without it
    Janus could read a stored time as the user's local time.
    """
    if value is None:
        return None
    return (value if value.tzinfo is not None else value.replace(tzinfo=UTC)).isoformat()


def parse_priority(value: str) -> models.Priority:
    """Parse a priority from enum name or value."""
    cleaned = value.strip()
    for priority in models.Priority:
        if cleaned.lower() in {priority.value.lower(), priority.name.lower()}:
            return priority
    raise ValueError("priority must be one of: important, digest, ignore, spam")


def parse_task_status(value: str) -> models.TaskStatus:
    """Parse a task status from enum name or value."""
    cleaned = value.strip()
    for status in models.TaskStatus:
        if cleaned.lower() in {status.value.lower(), status.name.lower()}:
            return status
    raise ValueError("status must be one of: Todo, In Progress, Done")


def parse_event_type(value: str) -> models.EventType:
    """Parse a system event type from enum name or value."""
    cleaned = value.strip()
    for event_type in models.EventType:
        if cleaned.lower() in {event_type.value.lower(), event_type.name.lower()}:
            return event_type
    raise ValueError("event_type must be one of: " + ", ".join(item.value for item in models.EventType))


def parse_runtime_trigger_status(value: str) -> models.RuntimeTriggerStatus:
    """Parse a runtime trigger status from enum name or value."""
    cleaned = value.strip()
    for trigger_status in models.RuntimeTriggerStatus:
        if cleaned.lower() in {trigger_status.value.lower(), trigger_status.name.lower()}:
            return trigger_status
    raise ValueError("status must be one of: " + ", ".join(item.value for item in models.RuntimeTriggerStatus))


def parse_dashboard_widget_key(value: str) -> models.DashboardWidgetKey:
    """Parse a dashboard widget key from enum name or value."""
    cleaned = value.strip()
    for widget_key in models.DashboardWidgetKey:
        if cleaned.lower() in {widget_key.value.lower(), widget_key.name.lower()}:
            return widget_key
    raise ValueError(f"widget_key must be one of: {format_dashboard_widget_key_options()}")


def format_dashboard_widget_key_options() -> str:
    """Return the supported dashboard widget keys for error messages."""
    return ", ".join(widget_key.value for widget_key in models.DashboardWidgetKey)


def require_prompt_override_widget_key(widget_key: models.DashboardWidgetKey) -> None:
    """Raise when a widget does not support prompt overrides."""
    if widget_key != models.DashboardWidgetKey.BRIEFING:
        raise ValueError("prompt_override is only supported for: briefing")


def require_widget_preference(
    preferences: Iterable[Any],
    widget_key: models.DashboardWidgetKey,
) -> Any:
    """Return one widget preference or raise a clear error for missing updates."""
    for preference in preferences:
        if getattr(preference, "widget_key", None) == widget_key:
            return preference
    raise ValueError(f"updated widget preference not found for: {widget_key.value}")


def log_management_event(
    db: Session,
    *,
    event_type: models.EventType,
    action: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> None:
    """Persist a skill-management audit event in the shared events table."""
    masked_details = event_logger.mask_pii(details) if details else None
    db.add(
        models.Event(
            event_type=event_type,
            source=MANAGEMENT_TOOL_SOURCE,
            message=f"{action}: {message}",
            details=json.dumps(masked_details) if masked_details else None,
            # The acting user, when known (JAN-272); a skill-library change
            # outside any owned context stays a system row.
            user_id=event_logger.current_owner(),
        )
    )
    db.commit()


def require_management_access(user: models.User) -> None:
    """Raise when the user is not allowed to mutate the shared skill library."""
    if not user.is_admin:
        raise PermissionError("admin access is required to modify skills")


def serialize_skill(db: Session, skill: models.AgentSkill) -> dict[str, Any]:
    """Return a tool-friendly payload for a skill."""
    return {
        "id": skill.id,
        "name": skill.name,
        "description": skill.description,
        "license": skill.license,
        "content": skill.content,
        "created_at": iso(skill.created_at),
        "updated_at": iso(skill.updated_at),
    }


def serialize_skill_summary(db: Session, skill: models.AgentSkill) -> dict[str, Any]:
    """Return a list-friendly payload for a skill without embedding full content."""
    payload = serialize_skill(db, skill)
    return {key: payload[key] for key in ("id", "name", "description", "license", "created_at", "updated_at")}
