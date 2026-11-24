"""Ending a WebSocket on an error without writing to a socket that is gone (JAN-293).

A client can leave while a briefing run or chat turn is still working, for
example when the user presses Retry or navigates away. Starlette then turns
the next send's ``OSError`` into ``WebSocketDisconnect`` and marks the socket
closed, and a later ``close()`` raises ``RuntimeError('Cannot call "send" once
a close message has been sent.')``. These helpers check the socket state first
and treat a disconnect during the error report as the normal end it is.
"""

import html
import logging

from fastapi import WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from janus import llm_service

logger = logging.getLogger("janus")


def is_open(websocket: WebSocket) -> bool:
    """Whether both sides of the socket are still connected, so a send can go out."""
    return (
        websocket.client_state == WebSocketState.CONNECTED and websocket.application_state == WebSocketState.CONNECTED
    )


async def send_error_and_close(websocket: WebSocket, error: Exception, endpoint: str) -> None:
    """Send ``__ERROR__:<message>`` and close the socket, unless the client already left."""
    await send_message_and_close(websocket, llm_service.user_facing_llm_error(error), endpoint)


async def send_message_and_close(websocket: WebSocket, message: str, endpoint: str) -> None:
    """Send ``__ERROR__:<message>`` with a message of the caller's choosing and close, unless the client left."""
    if not is_open(websocket):
        logger.info("WebSocket %s: client left before the error could be sent", endpoint)
        return
    try:
        await websocket.send_text(f"__ERROR__:{html.escape(message)}")
    except WebSocketDisconnect:
        logger.info("WebSocket %s: client left while the error was being sent", endpoint)
    except Exception as exc:  # the report is best effort; closing below still runs
        logger.warning("WebSocket %s: could not send the error: %s", endpoint, exc)
    if websocket.application_state != WebSocketState.CONNECTED:
        return
    try:
        await websocket.close()
    except (WebSocketDisconnect, RuntimeError, OSError):
        logger.info("WebSocket %s: client left before the socket was closed", endpoint)
