"""The catalog of events a trigger definition can subscribe to (JAN-210).

This module is the single source of truth for event-activated trigger
definitions. Every event Janus emits is a :class:`TriggerEvent` member that
belongs to exactly one :class:`TriggerSubsystem`, and carries a human label, a
description, and the payload fields its producer sends. The same catalog is:

* used by producers -- they emit a :class:`TriggerEvent` member, never a free
  string (``processor.py`` emits :attr:`TriggerEvent.MAIL_RECEIVED`);
* used to validate definitions -- the API schema types ``event_type`` as
  :class:`TriggerEvent`, so an unknown event is rejected with 422, and
  :func:`janus.trigger_definition_service.normalize_definition_fields`
  re-checks it for non-API callers;
* served to the UI by ``GET /api/runtime/definitions/event-catalog``, which
  populates the dependent subsystem -> event dropdowns.

Only events that are actually emitted belong here: a catalog entry nobody
emits would let users build definitions that never fire. Producers today:
``processor.py`` (mail), ``whatsapp_neonize.py`` (WhatsApp, JAN-361) and
``planner_service.py`` (planner, JAN-362). Add the member, its
:class:`EventSpec`, and the producer's ``emit_event`` call in the same change.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class TriggerSubsystem(StrEnum):
    """A part of Janus that publishes trigger events."""

    MAIL = "mail"
    WHATSAPP = "whatsapp"
    PLANNER = "planner"


class TriggerEvent(StrEnum):
    """Every event type Janus publishes. Values are ``<subsystem>.<event>``."""

    MAIL_RECEIVED = "mail.received"
    WHATSAPP_DIRECT_MESSAGE_RECEIVED = "whatsapp.direct_message_received"
    WHATSAPP_GROUP_MESSAGE_RECEIVED = "whatsapp.group_message_received"
    WHATSAPP_GROUP_MENTION = "whatsapp.group_mention"
    PLANNER_TASK_CREATED = "planner.task_created"
    PLANNER_TASK_CHANGED = "planner.task_changed"
    PLANNER_CALENDAR_EVENT_CREATED = "planner.calendar_event_created"
    PLANNER_CALENDAR_EVENT_CHANGED = "planner.calendar_event_changed"


@dataclass(frozen=True)
class EventPayloadField:
    """One field of an event's payload, as shown to the run and the user."""

    name: str
    description: str


@dataclass(frozen=True)
class SubsystemSpec:
    """Human-facing description of a subsystem."""

    subsystem: TriggerSubsystem
    label: str
    description: str


@dataclass(frozen=True)
class EventSpec:
    """Human-facing description of one event and the payload its producer sends."""

    event: TriggerEvent
    subsystem: TriggerSubsystem
    label: str
    description: str
    payload: tuple[EventPayloadField, ...]
    #: Rate limit a new definition on this event starts with (JAN-363); ``None`` = no limit.
    #: The user can change or remove it. Set for events that can fire in floods.
    default_max_runs_per_hour: int | None = None


SUBSYSTEMS: dict[TriggerSubsystem, SubsystemSpec] = {
    TriggerSubsystem.MAIL: SubsystemSpec(
        subsystem=TriggerSubsystem.MAIL,
        label="Mail",
        description="Email arriving through your connected IMAP accounts.",
    ),
    TriggerSubsystem.WHATSAPP: SubsystemSpec(
        subsystem=TriggerSubsystem.WHATSAPP,
        label="WhatsApp",
        description="Messages arriving live on your connected WhatsApp accounts (never your own, never history sync).",
    ),
    TriggerSubsystem.PLANNER: SubsystemSpec(
        subsystem=TriggerSubsystem.PLANNER,
        label="Planner",
        description=(
            "Changes the planner sync (Google Calendar, Google Tasks, Jira) finds. "
            "An account's first sync is a baseline and emits nothing."
        ),
    ),
}

_WHATSAPP_PAYLOAD = (
    EventPayloadField("message_id", "Janus's id of the stored WhatsApp message."),
    EventPayloadField("account", "The WhatsApp account's display name."),
    EventPayloadField("chat", "The chat's title."),
    EventPayloadField("chat_id", "The chat's WhatsApp id (a phone-number or group id)."),
    EventPayloadField("sender", "The sender's display name."),
    EventPayloadField("sender_id", "The sender's WhatsApp id."),
    EventPayloadField("received_at", "When the message was sent (ISO-8601)."),
    EventPayloadField("message_type", "text, voice_note, audio, image, ..."),
    EventPayloadField("body", "The first 4,000 characters of the text, if any."),
    EventPayloadField("attachment", "A short description of a non-text attachment, if any."),
)

_TASK_PAYLOAD = (
    EventPayloadField("task_id", "Janus's id of the synced task."),
    EventPayloadField("account", "The planner account's name."),
    EventPayloadField("provider", "google_tasks or jira."),
    EventPayloadField("external_id", "The task's id at the provider."),
    EventPayloadField("title", "The task's title."),
    EventPayloadField("status", "The task's status."),
    EventPayloadField("due_date", "The due date (ISO-8601), if any."),
)

_CALENDAR_EVENT_PAYLOAD = (
    EventPayloadField("calendar_event_id", "Janus's id of the synced calendar event."),
    EventPayloadField("account", "The planner account's name."),
    EventPayloadField("provider", "The calendar provider."),
    EventPayloadField("external_id", "The event's id at the provider."),
    EventPayloadField("summary", "The event's title."),
    EventPayloadField("start_time", "Start (ISO-8601)."),
    EventPayloadField("end_time", "End (ISO-8601)."),
    EventPayloadField("location", "The location, if any."),
)

_CHANGES_FIELD = EventPayloadField("changes", "What changed, as 'field: old -> new' lines.")

EVENTS: dict[TriggerEvent, EventSpec] = {
    TriggerEvent.MAIL_RECEIVED: EventSpec(
        event=TriggerEvent.MAIL_RECEIVED,
        subsystem=TriggerSubsystem.MAIL,
        label="New email received",
        description="A new email was stored and was not filtered out by a SPAM/IGNORE rule.",
        payload=(
            EventPayloadField("email_id", "Janus's id of the stored email."),
            EventPayloadField("account", "The mailbox address the email arrived in."),
            EventPayloadField("from", "The sender."),
            EventPayloadField("subject", "The subject line."),
            EventPayloadField("received_at", "When the email was received (ISO-8601)."),
            EventPayloadField("priority_from_rule", "The priority a rule assigned, or empty when no rule matched."),
            EventPayloadField("body", "The first 4,000 characters of the body."),
        ),
    ),
    TriggerEvent.WHATSAPP_DIRECT_MESSAGE_RECEIVED: EventSpec(
        event=TriggerEvent.WHATSAPP_DIRECT_MESSAGE_RECEIVED,
        subsystem=TriggerSubsystem.WHATSAPP,
        label="New direct message",
        description="Someone sent you a message in a one-to-one chat.",
        payload=_WHATSAPP_PAYLOAD,
    ),
    TriggerEvent.WHATSAPP_GROUP_MESSAGE_RECEIVED: EventSpec(
        event=TriggerEvent.WHATSAPP_GROUP_MESSAGE_RECEIVED,
        subsystem=TriggerSubsystem.WHATSAPP,
        label="New group message",
        description="Someone sent a message in a group you are in. Busy groups fire often.",
        payload=_WHATSAPP_PAYLOAD,
        default_max_runs_per_hour=10,
    ),
    TriggerEvent.WHATSAPP_GROUP_MENTION: EventSpec(
        event=TriggerEvent.WHATSAPP_GROUP_MENTION,
        subsystem=TriggerSubsystem.WHATSAPP,
        label="Mentioned in a group",
        description=(
            "Someone @-mentioned you, or replied to one of your messages, in a group. "
            "The message also fires 'New group message'."
        ),
        payload=_WHATSAPP_PAYLOAD,
        default_max_runs_per_hour=10,
    ),
    TriggerEvent.PLANNER_TASK_CREATED: EventSpec(
        event=TriggerEvent.PLANNER_TASK_CREATED,
        subsystem=TriggerSubsystem.PLANNER,
        label="New task",
        description="The planner sync saw a Google Tasks or Jira task for the first time.",
        payload=_TASK_PAYLOAD,
    ),
    TriggerEvent.PLANNER_TASK_CHANGED: EventSpec(
        event=TriggerEvent.PLANNER_TASK_CHANGED,
        subsystem=TriggerSubsystem.PLANNER,
        label="Task changed",
        description="A synced task's title, status, or due date changed.",
        payload=(*_TASK_PAYLOAD, _CHANGES_FIELD),
    ),
    TriggerEvent.PLANNER_CALENDAR_EVENT_CREATED: EventSpec(
        event=TriggerEvent.PLANNER_CALENDAR_EVENT_CREATED,
        subsystem=TriggerSubsystem.PLANNER,
        label="New calendar event",
        description=(
            "The planner sync saw a calendar event for the first time. The sync covers today and "
            "tomorrow only, so an event booked earlier fires when it enters that window."
        ),
        payload=_CALENDAR_EVENT_PAYLOAD,
    ),
    TriggerEvent.PLANNER_CALENDAR_EVENT_CHANGED: EventSpec(
        event=TriggerEvent.PLANNER_CALENDAR_EVENT_CHANGED,
        subsystem=TriggerSubsystem.PLANNER,
        label="Calendar event changed",
        description="A synced calendar event's title, start, end, or location changed.",
        payload=(*_CALENDAR_EVENT_PAYLOAD, _CHANGES_FIELD),
    ),
}


def parse_event(value: str | TriggerEvent | None) -> TriggerEvent:
    """Return the catalog member for ``value``, raising ``ValueError`` for anything unknown."""
    cleaned = (value or "").strip() if isinstance(value, str) else (value or "")
    try:
        return TriggerEvent(cleaned)
    except ValueError as error:
        known = ", ".join(event.value for event in TriggerEvent)
        raise ValueError(f"event_type must be one of: {known}") from error


def events_of(subsystem: TriggerSubsystem) -> list[EventSpec]:
    """The events one subsystem publishes, in catalog order."""
    return [spec for spec in EVENTS.values() if spec.subsystem == subsystem]


def catalog() -> list[dict[str, Any]]:
    """The full catalog, grouped by subsystem, as the API serves it."""
    return [
        {
            "subsystem": spec.subsystem.value,
            "label": spec.label,
            "description": spec.description,
            "events": [
                {
                    "event_type": event.event.value,
                    "label": event.label,
                    "description": event.description,
                    "payload": [{"name": field.name, "description": field.description} for field in event.payload],
                    "default_max_runs_per_hour": event.default_max_runs_per_hour,
                }
                for event in events_of(spec.subsystem)
            ],
        }
        for spec in SUBSYSTEMS.values()
    ]


__all__ = [
    "EVENTS",
    "SUBSYSTEMS",
    "EventPayloadField",
    "EventSpec",
    "SubsystemSpec",
    "TriggerEvent",
    "TriggerSubsystem",
    "catalog",
    "events_of",
    "parse_event",
]
