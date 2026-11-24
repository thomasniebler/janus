"""Concurrent reader for the ``/ws/chat`` socket (JAN-254).

The chat loop used to read exactly one frame per turn. A paused
confirmation-required tool call needs the client's answer *while* the reply
is still streaming, so one reader task now owns ``receive_text()`` for the
whole connection: ``tool_approval`` frames go straight to the connection's
:class:`~janus.services.tool_approval.ToolApprovalBroker`, ``ui_action_result``
frames to its :class:`~janus.services.ui_actions.UIActionBroker` (JAN-251),
``ui_introspection_result`` frames to its
:class:`~janus.services.ui_introspection.UIIntrospectionBroker` (JAN-284), every other frame
is queued as the next user message (a message typed mid-turn still runs after
the turn, as before).

When the socket closes, the reader refuses every waiting approval (and page
action and introspection request) as ``disconnected`` and :meth:`ChatInbox.send` turns into a no-op, so the turn in
flight still finishes and is stored; the next :meth:`ChatInbox.receive` then
raises ``WebSocketDisconnect`` and messages still queued are dropped (nobody
is there to read their replies).

The queue is bounded (:data:`MAX_QUEUED_MESSAGES`): a client that sends more
messages than that while a turn is still running is closed with 1008 instead
of growing server memory and the backlog of LLM turns without limit. The
reader never blocks on a full queue, so approval answers always get through.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Protocol

from fastapi import WebSocket, WebSocketDisconnect, status

from janus.services.tool_approval import ToolApprovalBroker

logger = logging.getLogger("janus")

_CLOSED = object()

#: User messages a client may queue while a turn is running (the web UI sends
#: one per turn); one more closes the connection.
MAX_QUEUED_MESSAGES = 8


class FrameHandler(Protocol):
    """A per-connection broker that consumes its own client frames (approvals, page-action/introspection results)."""

    def handle_frame(self, raw: str) -> bool:
        """Consume ``raw`` if it is this broker's frame; return whether it was."""
        ...

    def close(self) -> None:
        """Answer everything still waiting as disconnected."""
        ...


class _InboxOverflow(Exception):
    """The client queued more messages than :data:`MAX_QUEUED_MESSAGES`."""


class ChatInbox:
    """Reads one chat socket in the background and sends to it safely."""

    def __init__(self, websocket: WebSocket) -> None:
        """Wrap ``websocket``; call :meth:`start` to begin reading."""
        self._websocket = websocket
        self._queue: asyncio.Queue[object] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None
        self._handlers: tuple[FrameHandler, ...] = ()
        self.closed = False

    def start(self, broker: ToolApprovalBroker, *handlers: FrameHandler) -> None:
        """Start the reader task, routing approval answers to ``broker`` and other broker frames to ``handlers``."""
        self._handlers = (broker, *handlers)
        self._task = asyncio.create_task(self._read())

    def _close_handlers(self) -> None:
        for handler in self._handlers:
            handler.close()

    async def _read(self) -> None:
        try:
            while True:
                raw = await self._websocket.receive_text()
                if any(handler.handle_frame(raw) for handler in self._handlers):
                    continue
                if self._queue.qsize() >= MAX_QUEUED_MESSAGES:
                    raise _InboxOverflow()
                self._queue.put_nowait(raw)
        except WebSocketDisconnect:
            pass
        except _InboxOverflow:
            logger.warning("Closing chat socket: more than %d messages queued during one turn", MAX_QUEUED_MESSAGES)
            self.closed = True
            with contextlib.suppress(Exception):
                await self._websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        except RuntimeError as error:
            # Starlette's answer to a read after the socket was already closed
            # (for example by a failed send): the normal end of the connection.
            logger.info("Chat socket closed: %s", error)
        except Exception as error:  # noqa: BLE001 - any read failure ends the connection
            logger.warning("Chat socket read failed: %s", error)
        finally:
            self.closed = True
            self._close_handlers()
            self._queue.put_nowait(_CLOSED)

    async def receive(self) -> str:
        """Return the next user message frame; raise ``WebSocketDisconnect`` once the socket closed."""
        if self.closed:
            raise WebSocketDisconnect()
        item = await self._queue.get()
        if item is _CLOSED:
            self._queue.put_nowait(_CLOSED)
            raise WebSocketDisconnect()
        return str(item)

    async def send(self, text: str) -> None:
        """Send a frame; after the socket closed, drop it instead of failing the turn."""
        if self.closed:
            return
        try:
            await self._websocket.send_text(text)
        except Exception as error:  # noqa: BLE001 - a gone client must not abort the turn's persistence
            logger.info("Chat socket send failed, treating the client as gone: %s", error)
            self.closed = True
            self._close_handlers()

    async def stop(self) -> None:
        """Stop the reader task (refusing any waiting approval)."""
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
