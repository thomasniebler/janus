"""Normalized trigger envelopes and process-local dispatch guarantees."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from threading import Lock
from types import MappingProxyType
from typing import Any


class TriggerKind(str, Enum):
    """Supported ingress categories for future task activation."""

    MANUAL = "manual"
    SCHEDULED = "scheduled"
    EVENT = "event"


class TriggerDispatchStatus(str, Enum):
    """Result of attempting to dispatch one trigger envelope."""

    COMPLETED = "completed"
    IN_PROGRESS = "in_progress"
    DUPLICATE = "duplicate"
    FAILED = "failed"
    #: The handler parked the run until external work finishes (JAN-207).
    WAITING = "waiting"


def handler_result_is_waiting(value: object) -> bool:
    """Whether a handler's return value asks the durable boundary to park the run.

    Duck-typed so the trigger boundary stays independent of the plan runtime:
    ``agent_runtime.PlanRunResult.is_waiting`` is the production source.
    """
    return getattr(value, "is_waiting", False) is True


class TerminalTriggerError(Exception):
    """A handler failure that no retry of the same trigger can fix (JAN-194).

    ``DurableTriggerWorker`` treats any other handler exception as retryable
    and spends the configured attempt budget on it. Raising this instead marks
    the trigger ``exhausted`` on the first attempt -- the right outcome for a
    malformed payload or a policy denial, where a second attempt would fail
    identically and only burn attempts.
    """


class InvalidTriggerPayloadError(TerminalTriggerError, ValueError):
    """The trigger's (user- or model-authored) payload cannot be executed as given."""


@dataclass(frozen=True)
class TriggerEnvelope:
    """Transport-independent, auditable input to the task runtime."""

    trigger_id: str
    idempotency_key: str
    task_id: str
    kind: TriggerKind
    source: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    owner_id: int | None = None

    def __post_init__(self) -> None:
        """Validate identity and freeze the untrusted payload boundary."""
        for name, value in (
            ("trigger_id", self.trigger_id),
            ("idempotency_key", self.idempotency_key),
            ("task_id", self.task_id),
            ("source", self.source),
        ):
            if not value.strip():
                raise ValueError(f"trigger {name} must not be blank")
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("trigger created_at must be timezone-aware")
        if self.owner_id is not None and (
            isinstance(self.owner_id, bool) or not isinstance(self.owner_id, int) or self.owner_id < 1
        ):
            raise ValueError("trigger owner_id must be a positive integer")
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))


@dataclass(frozen=True)
class TriggerDispatchResult:
    """Observable result of one dispatch attempt."""

    trigger: TriggerEnvelope
    status: TriggerDispatchStatus
    value: Any = None
    error: str | None = None


TriggerHandler = Callable[[TriggerEnvelope], Any]


class TriggerDispatcher:
    """Dispatch triggers once per idempotency key within one process.

    This is intentionally process-local. Durable claims, retries after process
    loss, and scheduled-event storage belong to the later persistence slice.
    """

    def __init__(self, handler: TriggerHandler) -> None:
        """Create a dispatcher around a task-runtime handoff callback."""
        self._handler = handler
        self._lock = Lock()
        self._active: set[str] = set()
        self._completed: set[str] = set()

    def dispatch(self, trigger: TriggerEnvelope) -> TriggerDispatchResult:
        """Claim, execute, and record one trigger without duplicate execution."""
        key = f"{trigger.task_id}:{trigger.idempotency_key}"
        with self._lock:
            if key in self._completed:
                return TriggerDispatchResult(trigger, TriggerDispatchStatus.DUPLICATE)
            if key in self._active:
                return TriggerDispatchResult(trigger, TriggerDispatchStatus.IN_PROGRESS)
            self._active.add(key)

        try:
            value = self._handler(trigger)
        except Exception as error:
            with self._lock:
                self._active.remove(key)
            return TriggerDispatchResult(
                trigger, TriggerDispatchStatus.FAILED, error=str(error) or error.__class__.__name__
            )

        with self._lock:
            self._active.remove(key)
            self._completed.add(key)
        return TriggerDispatchResult(trigger, TriggerDispatchStatus.COMPLETED, value=value)


__all__ = [
    "InvalidTriggerPayloadError",
    "TerminalTriggerError",
    "TriggerDispatchResult",
    "TriggerDispatchStatus",
    "TriggerDispatcher",
    "TriggerEnvelope",
    "TriggerKind",
    "handler_result_is_waiting",
]
