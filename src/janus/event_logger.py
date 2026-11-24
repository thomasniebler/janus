"""Event logging module for Janus.

Every row records the user it concerns when the writer knows it (JAN-272), so
the event log can be read back per owner: an unattended run's
``list_recent_events`` sees only its owner's rows, and a non-admin sees only
their own. A caller passes ``user_id`` explicitly, or runs inside
:func:`owned_by` (a trigger run, a delegated task, a chat connection, one
mail account's IMAP thread, one planner account's sync), whose owner every
nested ``log`` call inherits. Anything logged with no known owner is a system
row (``user_id`` ``None``), visible to admins only.
"""

import json
import logging
import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from datetime import date, datetime

from janus import database, models

logger = logging.getLogger(__name__)

#: The owner of whatever is running in this context; see :func:`owned_by`.
_CURRENT_OWNER: ContextVar[int | None] = ContextVar("event_owner", default=None)


def bind_owner(user_id: int | None) -> Token[int | None]:
    """Attribute events logged in this context to ``user_id`` until :func:`release_owner`."""
    return _CURRENT_OWNER.set(user_id)


def release_owner(token: Token[int | None]) -> None:
    """Undo one :func:`bind_owner`."""
    _CURRENT_OWNER.reset(token)


@contextmanager
def owned_by(user_id: int | None) -> Iterator[None]:
    """Attribute every event logged inside this block to ``user_id`` (``None``: a system row)."""
    token = bind_owner(user_id)
    try:
        yield
    finally:
        release_owner(token)


def current_owner() -> int | None:
    """Return the owner events logged right now are attributed to, if any."""
    return _CURRENT_OWNER.get()


class _DatetimeEncoder(json.JSONEncoder):
    """JSON encoder that serialises datetime/date objects as ISO strings."""

    def default(self, obj):
        """Return a JSON-serializable value for date and datetime objects."""
        if isinstance(obj, (datetime, date)):
            return obj.isoformat()
        return super().default(obj)


def mask_pii(data):
    """Simple PII masker for logging."""
    if isinstance(data, str):
        # Mask email addresses

        return re.sub(r"[\w\.-]+@[\w\.-]+\.\w+", "[REDACTED_EMAIL]", data)
    elif isinstance(data, dict):
        return {k: mask_pii(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [mask_pii(v) for v in data]
    return data


class EventLogger:
    """Centralized event logger for Janus."""

    def log(
        self,
        event_type: models.EventType,
        source: str,
        message: str,
        details: dict | None = None,
        *,
        user_id: int | None = None,
    ):
        """Logs an event to the database and standard logger.

        Args:
            event_type (EventType): The type of the event (INFO, WARNING, ERROR).
            source (str): The source of the event (e.g., "EmailProcessor", "PlannerService").
            message (str): A descriptive message about the event.
            details (dict, optional): Additional details to log (will be masked for PII).
            user_id (int, optional): The user the event concerns; defaults to
                the enclosing :func:`owned_by` owner.
        """
        owner_id = user_id if user_id is not None else current_owner()
        db = database.SessionLocal()
        try:
            # Mask PII in details for both database and log file
            masked_details = mask_pii(details) if details else None
            masked_message = mask_pii(message)

            event = models.Event(
                event_type=event_type,
                source=source,
                message=masked_message,
                details=json.dumps(masked_details, cls=_DatetimeEncoder) if masked_details else None,
                user_id=owner_id,
            )
            db.add(event)
            db.commit()

            # Log to standard logger as well, but masked
            log_msg = f"[{source}] {masked_message}"
            if event_type == models.EventType.ERROR:
                logger.error(log_msg)
            elif event_type == models.EventType.WARNING:
                logger.warning(log_msg)
            else:
                logger.info(log_msg)

        except Exception as e:
            logger.error(f"Failed to log event from {source}: {e}")
        finally:
            db.close()


log_service = EventLogger()


def log(
    event_type: models.EventType,
    source: str,
    message: str,
    details: dict | None = None,
    *,
    user_id: int | None = None,
):
    """Module-level shortcut for event logging.

    Args:
        event_type (EventType): The type of the event.
        source (str): The source of the event.
        message (str): A descriptive message about the event.
        details (dict, optional): Additional details to log.
        user_id (int, optional): The user the event concerns (see :func:`owned_by`).
    """
    log_service.log(event_type, source, message, details, user_id=user_id)
