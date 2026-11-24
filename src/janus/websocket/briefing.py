"""Morning briefing WebSocket endpoint.

The briefing is the built-in ``builtin.briefing`` trigger definition (JAN-231):
opening the dashboard shows the latest successful run from today, and
``?refresh=1`` (or no run yet today) runs the definition inline through the
durable trigger path. The objective, tools, and schedule are edited on
Settings -> Triggers; the run always speaks as the user's Janus character.
"""

import asyncio
import logging

from fastapi import Depends, WebSocket, WebSocketDisconnect, status

from janus import builtin_triggers, database, models, security
from janus import trigger_definition_service as definitions
from janus.trigger_runtime import run_definition_inline
from janus.websocket.closing import send_error_and_close

logger = logging.getLogger("janus")


def _load_or_run_briefing(user_id: int, refresh: bool, timezone: str | None = None) -> tuple[str, bool]:
    """Return ``(briefing_text, freshly_generated)`` for one user, in a worker thread."""
    db = database.SessionLocal()
    try:
        builtin_triggers.ensure_builtin_definitions(db, user_id)
        builtin_triggers.adopt_client_timezone(db, user_id, timezone)
        definition = definitions.get_builtin_definition(db, user_id, builtin_triggers.BUILTIN_BRIEFING)
        if definition is None:
            raise ValueError("the morning briefing trigger is missing")
        if not refresh:
            cached = builtin_triggers.todays_briefing(db, definition)
            if cached is not None:
                return cached.output or "", False
        definition_id = definition.id
    finally:
        db.close()

    record = run_definition_inline(database.SessionLocal, definition_id)
    if record.status != models.RuntimeTriggerStatus.COMPLETED.value or not record.output:
        raise RuntimeError(record.last_error or "the morning briefing run did not complete")
    return record.output, True


async def ws_briefing(
    websocket: WebSocket,
    refresh: bool = False,
    timezone: str | None = None,
    user: models.User | None = Depends(security.get_websocket_user_or_none),
):
    """Send today's briefing, generating it through the briefing trigger when needed.

    The run opens its own sessions; the socket holds none while it waits (JAN-336).
    """
    await websocket.accept()
    if not user:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    try:
        await websocket.send_text("__START__")
        text, _fresh = await asyncio.to_thread(_load_or_run_briefing, user.id, refresh, timezone)
        await websocket.send_text(text)
        await websocket.send_text("__END__")
    except WebSocketDisconnect:
        logger.info("WebSocket briefing disconnected")
    except Exception as e:
        logger.error(f"WebSocket briefing error: {e}")
        await send_error_and_close(websocket, e, "briefing")


def register_briefing_websocket(api) -> None:
    """Register the briefing WebSocket endpoint on an app."""
    api.websocket("/ws/briefing")(ws_briefing)
