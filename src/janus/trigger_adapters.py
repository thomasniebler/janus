"""Deterministic adapters for scheduled and external-event activations."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from janus.triggers import TriggerEnvelope, TriggerKind


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must not be blank")
    return value.strip()


def _owner_id(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("owner_id must be a positive integer")
    return value


def _max_steps(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1 or value > 100:
        raise ValueError("max_steps must be between 1 and 100")
    return value


def _utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _stable_trigger_id(task_id: str, idempotency_key: str) -> str:
    """Build a retry-stable UUID without trusting caller-supplied IDs."""
    return str(uuid5(NAMESPACE_URL, f"janus://trigger/{task_id}/{idempotency_key}"))


@dataclass(frozen=True)
class ScheduledTriggerSpec:
    """One scheduled occurrence mapped to a retry-stable trigger envelope."""

    schedule_id: str
    owner_id: int
    task_id: str
    objective: str
    due_at: datetime
    source: str = "scheduler"
    task_mode: str = "default"
    max_steps: int = 100
    payload: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validate scheduler-owned identity and runtime context."""
        _text(self.schedule_id, "schedule_id")
        _owner_id(self.owner_id)
        _text(self.task_id, "task_id")
        _text(self.objective, "objective")
        _text(self.source, "source")
        _text(self.task_mode, "task_mode")
        _max_steps(self.max_steps)
        _utc(self.due_at, "due_at")
        object.__setattr__(self, "payload", dict(self.payload))

    def as_envelope(self) -> TriggerEnvelope:
        """Create the canonical envelope for this exact scheduled occurrence."""
        occurrence = _utc(self.due_at, "due_at").isoformat()
        idempotency_key = f"schedule:{self.schedule_id}:{occurrence}"
        payload = {
            **self.payload,
            "objective": self.objective,
            "task_mode": self.task_mode,
            "max_steps": self.max_steps,
        }
        return TriggerEnvelope(
            trigger_id=_stable_trigger_id(self.task_id, idempotency_key),
            idempotency_key=idempotency_key,
            task_id=self.task_id,
            kind=TriggerKind.SCHEDULED,
            source=self.source,
            payload=payload,
            created_at=_utc(self.due_at, "due_at"),
            owner_id=self.owner_id,
        )


@dataclass(frozen=True)
class EventTriggerSpec:
    """One external event mapped to a retry-stable trigger envelope."""

    event_id: str
    owner_id: int
    task_id: str
    objective: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    source: str = "event"
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    task_mode: str = "default"
    max_steps: int = 100

    def __post_init__(self) -> None:
        """Validate event identity and keep the external payload separate."""
        _text(self.event_id, "event_id")
        _owner_id(self.owner_id)
        _text(self.task_id, "task_id")
        _text(self.objective, "objective")
        _text(self.source, "source")
        _text(self.task_mode, "task_mode")
        _max_steps(self.max_steps)
        _utc(self.occurred_at, "occurred_at")
        object.__setattr__(self, "payload", dict(self.payload))

    def as_envelope(self) -> TriggerEnvelope:
        """Create the canonical envelope for this external event delivery."""
        idempotency_key = f"event:{self.source}:{self.event_id}"
        payload = {
            **self.payload,
            "objective": self.objective,
            "task_mode": self.task_mode,
            "max_steps": self.max_steps,
        }
        return TriggerEnvelope(
            trigger_id=_stable_trigger_id(self.task_id, idempotency_key),
            idempotency_key=idempotency_key,
            task_id=self.task_id,
            kind=TriggerKind.EVENT,
            source=self.source,
            payload=payload,
            created_at=_utc(self.occurred_at, "occurred_at"),
            owner_id=self.owner_id,
        )


__all__ = ["EventTriggerSpec", "ScheduledTriggerSpec"]
