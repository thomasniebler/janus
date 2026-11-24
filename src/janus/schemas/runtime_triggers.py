"""Schemas for authenticated runtime-trigger ingress and observability."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, StrictInt, field_validator, model_validator

from janus.models import (
    RuntimeTriggerKind,
    RuntimeTriggerStatus,
    TriggerDefinitionKind,
    TriggerDefinitionOrigin,
    TriggerProposalStatus,
)
from janus.trigger_events import TriggerEvent, TriggerSubsystem


class RuntimeTriggerCreate(BaseModel):
    """Narrow user-authored context accepted for one runtime trigger."""

    task_id: str
    idempotency_key: str | None = None
    kind: RuntimeTriggerKind
    source: str = "api"
    objective: str
    task_mode: str = "default"
    max_steps: StrictInt = 100
    schedule_id: str | None = None
    scheduled_for: datetime | None = None
    event_id: str | None = None
    occurred_at: datetime | None = None

    @field_validator(
        "task_id",
        "idempotency_key",
        "source",
        "objective",
        "task_mode",
        "schedule_id",
        "event_id",
    )
    @classmethod
    def text_must_not_be_blank(cls, value: str | None) -> str | None:
        """Reject whitespace-only identifiers and context fields."""
        if value is None:
            return None
        if not value.strip():
            raise ValueError("value must not be blank")
        return value.strip()

    @field_validator("scheduled_for", "occurred_at")
    @classmethod
    def timestamps_must_be_timezone_aware(cls, value: datetime | None) -> datetime | None:
        """Avoid interpreting browser/provider timestamps in the server timezone."""
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("timestamp must include a timezone offset")
        return value

    @field_validator("max_steps")
    @classmethod
    def max_steps_must_be_bounded(cls, value: int) -> int:
        """Keep API-triggered graph work within the stable runtime limit."""
        if isinstance(value, bool) or value < 1 or value > 100:
            raise ValueError("max_steps must be between 1 and 100")
        return value

    @model_validator(mode="after")
    def validate_kind_specific_identity(self) -> "RuntimeTriggerCreate":
        """Require exactly the identity fields owned by the selected ingress kind."""
        fields_by_kind = {
            RuntimeTriggerKind.MANUAL: {"idempotency_key"},
            RuntimeTriggerKind.SCHEDULED: {"schedule_id", "scheduled_for"},
            RuntimeTriggerKind.EVENT: {"event_id", "occurred_at"},
        }
        present = {
            name
            for name in ("idempotency_key", "schedule_id", "scheduled_for", "event_id", "occurred_at")
            if getattr(self, name) is not None
        }
        required = fields_by_kind[self.kind]
        missing = required - present
        if missing:
            raise ValueError(f"{self.kind.value} triggers require {', '.join(sorted(missing))}")
        incompatible = present - required
        if incompatible:
            raise ValueError(f"{self.kind.value} triggers do not accept {', '.join(sorted(incompatible))}")
        return self


class RuntimeTriggerResponse(BaseModel):
    """Safe user-scoped view of a durable trigger lifecycle record."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    trigger_id: str
    task_id: str
    kind: RuntimeTriggerKind
    source: str
    status: RuntimeTriggerStatus
    created_at: datetime
    claimed_at: datetime | None
    completed_at: datetime | None
    last_error: str | None
    attempt_count: int
    next_attempt_at: datetime | None
    #: What the run actually produced, decoded from the trigger's persisted
    #: plan state (JAN-192): the automation's summary plus the trace of tools
    #: it invoked. Deliberately not sourced from ``RuntimeTrigger.payload``,
    #: which stays hidden from every history surface. ``None`` for a trigger
    #: that has not executed yet, or whose plan state predates this field.
    result: str | None = None
    #: The recorded plan-step failure, which outlives ``last_error`` -- a retry
    #: clears ``last_error`` when it re-claims the row, so a human looking at
    #: an exhausted trigger would otherwise see no reason at all.
    step_error: str | None = None
    #: The saved definition this run came from, if any (JAN-228).
    definition_id: int | None = None
    #: The model's final answer alone, without the audit header in ``result``.
    output: str | None = None
    #: Name and author of the definition this run came from (JAN-256), so a
    #: history view can label and link a run without a second lookup.
    definition_name: str | None = None
    definition_origin: TriggerDefinitionOrigin | None = None


class RuntimeTriggerPage(BaseModel):
    """One filtered, newest-first page of trigger runs plus pagination metadata (JAN-256)."""

    items: list[RuntimeTriggerResponse]
    total: int
    limit: int
    offset: int
    has_more: bool


class TriggerSchedule(BaseModel):
    """Recurring schedule of a trigger definition."""

    type: Literal["daily", "interval"]
    #: ``HH:MM`` (24h) in the definition's timezone; daily schedules only.
    time: str | None = None
    #: Weekdays 0 (Monday) .. 6 (Sunday); empty means every day. Daily only.
    days: list[int] = []
    #: Interval schedules only.
    minutes: int | None = None


class TriggerEventCondition(BaseModel):
    """One condition of an event definition's payload filter (JAN-363).

    ``field`` must be a payload field of the definition's event; ``in`` /
    ``not_in`` compare the whole value, ``contains`` a substring (both
    case-insensitive) against any of ``values``.
    """

    field: str
    op: Literal["in", "not_in", "contains"]
    values: list[str]


class TriggerDefinitionCreate(BaseModel):
    """A new user-authored trigger definition."""

    name: str
    objective: str
    tool_ids: list[str] = []
    kind: TriggerDefinitionKind = TriggerDefinitionKind.MANUAL
    schedule: TriggerSchedule | None = None
    timezone: str = "UTC"
    #: Required for ``kind == "event"``; only catalogued events (JAN-210) are accepted.
    event_type: TriggerEvent | None = None
    #: Event definitions only (JAN-363): every condition must match for an event to start a run.
    event_filter: list[TriggerEventCondition] | None = None
    #: Event definitions only (JAN-363). Omitted: the event's catalog default; null: no limit.
    max_runs_per_hour: int | None = None
    enabled: bool = True
    #: Optional model override (JAN-233); omit/blank for the default model.
    llm_provider: str | None = None
    llm_model: str | None = None


class TriggerDefinitionUpdate(BaseModel):
    """Partial update of a trigger definition."""

    name: str | None = None
    objective: str | None = None
    tool_ids: list[str] | None = None
    kind: TriggerDefinitionKind | None = None
    schedule: TriggerSchedule | None = None
    timezone: str | None = None
    event_type: TriggerEvent | None = None
    event_filter: list[TriggerEventCondition] | None = None
    max_runs_per_hour: int | None = None
    enabled: bool | None = None
    llm_provider: str | None = None
    llm_model: str | None = None


class TriggerDefinitionResponse(BaseModel):
    """A saved trigger definition."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    builtin_key: str | None
    name: str
    objective: str
    tool_ids: list[str]
    kind: TriggerDefinitionKind
    schedule: dict[str, Any] | None
    timezone: str
    event_type: str | None
    event_filter: list[dict[str, Any]] | None = None
    max_runs_per_hour: int | None = None
    #: Events the rate limit dropped, and the latest such drop (JAN-363).
    rate_limited_count: int = 0
    last_rate_limited_at: datetime | None = None
    enabled: bool
    origin: TriggerDefinitionOrigin
    llm_provider: str | None = None
    llm_model: str | None = None
    next_run_at: datetime | None
    last_run_at: datetime | None
    created_at: datetime
    updated_at: datetime
    #: JAN-235: ``None`` unless Janus drafted this definition; only approved ones are listed here.
    proposal_status: TriggerProposalStatus | None = None
    #: Janus's reason for the draft -- untrusted text, render it escaped.
    proposal_rationale: str | None = None
    proposed_at: datetime | None = None
    proposal_source: dict[str, Any] | None = None
    supersedes_id: int | None = None
    approved_at: datetime | None = None
    rejected_at: datetime | None = None
    rejection_reason: str | None = None


class TriggerProposalSupersedes(BaseModel):
    """The approved definition a proposal would replace, for the review diff."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    objective: str
    tool_ids: list[str]
    kind: TriggerDefinitionKind
    schedule: dict[str, Any] | None
    timezone: str
    event_type: str | None
    enabled: bool


class TriggerProposalResponse(TriggerDefinitionResponse):
    """A Janus-drafted definition with the data its review card needs (JAN-235)."""

    #: Tools on the draft that are off for unattended runs; approving lets this definition use them.
    blocked_tool_ids: list[str] = []
    #: Untrusted-content toolboxes the drafting chat turn had already read (mail, web_search, ...).
    untrusted_context: list[str] = []
    #: Something read in the drafting turn looked like a prompt injection (JAN-127); labelled, not blocked.
    injection_suspected: bool = False
    #: The first occurrence the draft would have if approved now (schedules only).
    next_occurrence: datetime | None = None
    supersedes: TriggerProposalSupersedes | None = None


class TriggerProposalApprove(BaseModel):
    """Approve a proposal, optionally with the user's edits (each edit is part of the approval)."""

    name: str | None = None
    objective: str | None = None
    #: The final tool list; a blocked tool the user did not tick is simply left out.
    tool_ids: list[str] | None = None
    kind: TriggerDefinitionKind | None = None
    schedule: TriggerSchedule | None = None
    timezone: str | None = None
    event_type: str | None = None
    event_filter: list[TriggerEventCondition] | None = None
    max_runs_per_hour: int | None = None
    llm_provider: str | None = None
    llm_model: str | None = None
    enable: bool = True


class TriggerProposalReject(BaseModel):
    """Reject a proposal with an optional reason Janus will see."""

    reason: str | None = None


class TriggerProposalCount(BaseModel):
    """How many proposals await review, for the navigation badge."""

    open: int


class TriggerEventPayloadFieldResponse(BaseModel):
    """One field of an event's payload."""

    name: str
    description: str


class TriggerEventResponse(BaseModel):
    """An event a trigger definition can subscribe to (JAN-210)."""

    event_type: TriggerEvent
    label: str
    description: str
    payload: list[TriggerEventPayloadFieldResponse]
    #: Rate limit a new definition on this event starts with (JAN-363); null = none.
    default_max_runs_per_hour: int | None = None


class TriggerSubsystemResponse(BaseModel):
    """A subsystem and the events it publishes (JAN-210)."""

    subsystem: TriggerSubsystem
    label: str
    description: str
    events: list[TriggerEventResponse]


class RuntimeTriggerWorkerStatus(BaseModel):
    """Whether this Janus process will execute queued runtime triggers."""

    enabled: bool
    running: bool
