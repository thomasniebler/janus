"""Per-call user approval for ``confirmation="required"`` chat tools (JAN-254).

In chat, the runtime adapter builds the tools of ``confirmation="required"``
registry entries (send mail, GitHub, Linear, planner writes, mail-rule writes,
skill edits, wiki edit proposals) but wraps each one with
:meth:`ToolApprovalBroker.gate`. When the model calls such a tool, the call
pauses until the user answers an approval request:

1. LangChain runs the (sync) tool in a worker thread. The gate hands a
   :class:`ToolApprovalRequest` to the connection's event loop with
   ``asyncio.run_coroutine_threadsafe`` and blocks that thread only.
2. :meth:`ToolApprovalBroker.request` waits briefly until the call's JAN-249
   ``start`` event has been forwarded (:meth:`~ToolApprovalBroker.note_started`),
   sends one ``__TOOL_APPROVAL__:<json>`` frame, and awaits a per-call future.
3. The connection's reader (``websocket/chat_inbox.py``) passes every
   ``{"type": "tool_approval", "call_id", "decision"}`` frame to
   :meth:`ToolApprovalBroker.handle_frame`, which resolves that call's future.

Only ``approve`` for that exact ``call_id`` runs the tool, once, with the
arguments the request showed. Deny, the timeout, a closed connection, or a
client that cannot show the request answer the model with the tool-policy
refusal shape (``BLOCKED: <tool> was not run because ...``), so the JAN-249
recorder stores the call as ``blocked``; the decision itself is kept in
:attr:`ToolApprovalBroker.decisions` and persisted on the tool-call row.
A broker belongs to one WebSocket connection, so a reconnect can never answer
(or replay) a call of an earlier connection.

``always`` (JAN-359) approves the call like ``approve`` and also calls the
broker's ``on_always_allow`` hook with the call's registry entry, which sets
that capability's *In chat* switch to *Always allow*, so later messages run it
without asking. The decision is recorded as ``approved``.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool

from janus import event_logger, models
from janus.agent_toolboxes.shared import TurnTrace
from janus.runtime_adapter import ToolCallLog, ToolCallRecord
from janus.services.tool_call_events import preview_args
from janus.tool_registry import ToolRegistryEntry

TOOL_APPROVAL_PREFIX = "__TOOL_APPROVAL__:"
#: ``type`` of the client's answer frame.
TOOL_APPROVAL_FRAME_TYPE = "tool_approval"

APPROVED = "approved"
DENIED = "denied"
TIMEOUT = "timeout"
DISCONNECTED = "disconnected"
UNAVAILABLE = "unavailable"

_CLIENT_DECISIONS = {"approve": APPROVED, "deny": DENIED, "always": APPROVED}
#: Client decision that approves the call *and* switches the capability to *Always allow* (JAN-359).
ALWAYS_DECISION = "always"

#: How long a request waits for its call's ``start`` event to be forwarded, so
#: the client sees the call before the approval request for it.
START_WAIT_SECONDS = 2.0
#: Extra time the worker thread waits on top of the broker's own timeout.
_THREAD_GRACE_SECONDS = 30.0

_REFUSAL_REASONS = {
    DENIED: "the user declined",
    TIMEOUT: "the user declined (no answer to the approval request in time)",
    DISCONNECTED: "the user declined (the chat connection closed before they answered)",
    UNAVAILABLE: "this chat client cannot ask the user for approval",
}


def refusal_message(tool_name: str, decision: str) -> str:
    """Return what a refused call answers the model (the tool-policy ``BLOCKED`` shape)."""
    reason = _REFUSAL_REASONS.get(decision, "the user declined")
    return (
        f"BLOCKED: {tool_name} was not run because {reason}. Nothing was changed. "
        "Tell the user you did not do it; they can ask again if they want it."
    )


@dataclass
class ToolApprovalRequest:
    """One ``__TOOL_APPROVAL__`` frame: a paused call waiting for the user's answer."""

    call_id: str
    tool_id: str
    tool_name: str
    display_name: str
    risk_level: str
    reason: str
    args: Any
    args_truncated: bool
    timeout_seconds: float

    def to_marker(self) -> str:
        """Return the frame streamed to the chat client."""
        return TOOL_APPROVAL_PREFIX + json.dumps(asdict(self), ensure_ascii=False)


def _clone(tool: BaseTool, func: Callable[..., Any]) -> BaseTool:
    return StructuredTool.from_function(
        func=func,
        name=tool.name,
        description=tool.description,
        args_schema=tool.args_schema,
        infer_schema=False,
    )


class ToolApprovalBroker:
    """Asks the user to approve each confirmation-required call of one chat connection.

    ``send`` streams a frame to the client; ``None`` means the client cannot
    show approval requests (it did not open ``/ws/chat?tool_events=1``), so
    every gated call is refused as ``unavailable``. Construct it inside the
    connection's event loop.
    """

    def __init__(
        self,
        send: Callable[[str], Awaitable[None]] | None,
        *,
        user_id: int,
        timeout_seconds: float,
        on_always_allow: Callable[[ToolRegistryEntry], None] | None = None,
    ) -> None:
        """Bind the broker to the running loop, the client sender, and the connection's user."""
        self._send = send
        self._on_always_allow = on_always_allow
        #: ``call_id``s the user answered with *Always allow*.
        self._always: set[str] = set()
        self._user_id = user_id
        self.timeout_seconds = timeout_seconds
        self._loop = asyncio.get_running_loop()
        self._pending: dict[str, asyncio.Future[str]] = {}
        self._started: dict[str, asyncio.Event] = {}
        self._closed = False
        #: Decision by ``call_id`` for every call that needed one on this connection.
        self.decisions: dict[str, str] = {}

    # -- event-loop side -------------------------------------------------

    def note_started(self, call_id: str) -> None:
        """Mark that the ``start`` event of ``call_id`` has been forwarded to the client."""
        self._started.setdefault(call_id, asyncio.Event()).set()

    async def request(self, request: ToolApprovalRequest) -> str:
        """Ask the user about one call and return the decision (never raises)."""
        call_id = request.call_id
        if self._send is None:
            return self._decide(call_id, UNAVAILABLE)
        if self._closed:  # no point waiting for a start event on a closed connection
            return self._decide(call_id, DISCONNECTED)
        try:
            started = self._started.setdefault(call_id, asyncio.Event())
            try:  # noqa: SIM105
                await asyncio.wait_for(started.wait(), START_WAIT_SECONDS)
            except TimeoutError:
                pass
            if self._closed:
                return self._decide(call_id, DISCONNECTED)
            future: asyncio.Future[str] = self._loop.create_future()
            self._pending[call_id] = future
            try:
                await self._send(request.to_marker())
                decision = await asyncio.wait_for(future, self.timeout_seconds)
            except TimeoutError:
                decision = TIMEOUT
            finally:
                self._pending.pop(call_id, None)
        finally:
            self._started.pop(call_id, None)
        return self._decide(call_id, decision)

    def _decide(self, call_id: str, decision: str) -> str:
        self.decisions[call_id] = decision
        return decision

    def handle_frame(self, raw: str) -> bool:
        """Apply a client ``tool_approval`` frame; return whether ``raw`` was one (and so consumed).

        An answer for a call that is not waiting (unknown, already answered,
        or of another connection), or with an unknown ``decision``, is ignored.
        """
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            return False
        if not isinstance(payload, dict) or payload.get("type") != TOOL_APPROVAL_FRAME_TYPE:
            return False
        raw_decision = str(payload.get("decision"))
        decision = _CLIENT_DECISIONS.get(raw_decision)
        call_id = str(payload.get("call_id"))
        future = self._pending.get(call_id)
        if decision is not None and future is not None and not future.done():
            if raw_decision == ALWAYS_DECISION:
                self._always.add(call_id)
            future.set_result(decision)
        return True

    def _apply_always(self, call_id: str, entry: ToolRegistryEntry) -> None:
        if call_id not in self._always:
            return
        self._always.discard(call_id)
        if self._on_always_allow is None:
            return
        try:
            self._on_always_allow(entry)
        except Exception:  # noqa: BLE001 - the call was approved; failing to remember it must not undo that
            event_logger.log(
                models.EventType.WARNING,
                "ToolApproval",
                "Could not switch a tool to Always allow",
                {"user_id": self._user_id, "tool_id": entry.id},
                user_id=self._user_id,
            )

    def close(self) -> None:
        """Refuse every waiting call as ``disconnected``; later requests are refused at once."""
        self._closed = True
        for future in list(self._pending.values()):
            if not future.done():
                future.set_result(DISCONNECTED)

    # -- worker-thread side ----------------------------------------------

    def _await_decision(self, request: ToolApprovalRequest) -> str:
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is self._loop:
            # Called on the connection's own loop: blocking here would deadlock.
            return UNAVAILABLE
        try:
            future = asyncio.run_coroutine_threadsafe(self.request(request), self._loop)
        except RuntimeError:  # the connection's loop is gone
            return DISCONNECTED
        try:
            return future.result(timeout=self.timeout_seconds + START_WAIT_SECONDS + _THREAD_GRACE_SECONDS)
        except Exception:  # noqa: BLE001 - any failure to get an answer refuses the call
            future.cancel()
            return TIMEOUT

    def gate(self, tool: BaseTool, entry: ToolRegistryEntry, log: ToolCallLog, trace: TurnTrace) -> BaseTool:
        """Return ``tool`` wrapped so each call runs only after the user approves it.

        Signature of ``RuntimeAdapter.prepare_step(approval_gate=...)``; the
        wrapper is the outermost layer, so its LangChain ``run_id`` is the
        call's JAN-249 ``call_id``.
        """

        def run(callbacks=None, **kwargs):
            if trace.injection_suspected():
                # Pointless to ask: the inner wrapper refuses consequential
                # tools after a suspected prompt injection (JAN-127).
                return tool.invoke(kwargs)
            call_id = str(getattr(callbacks, "parent_run_id", None) or uuid.uuid4())
            args, args_truncated = preview_args(kwargs)
            decision = self._await_decision(
                ToolApprovalRequest(
                    call_id=call_id,
                    tool_id=entry.id,
                    tool_name=tool.name,
                    display_name=entry.display_name,
                    risk_level=str(entry.risk_level),
                    reason=entry.approval_reason or entry.description,
                    args=args,
                    args_truncated=args_truncated,
                    timeout_seconds=self.timeout_seconds,
                )
            )
            if decision == APPROVED:
                self._apply_always(call_id, entry)
                return tool.invoke(kwargs)
            log.records.append(ToolCallRecord(tool.name, entry.id, "refused", f"user_{decision}"))
            event_logger.log(
                models.EventType.WARNING,
                "ToolApproval",
                f"Refused tool call {tool.name}",
                {"user_id": self._user_id, "tool_id": entry.id, "reason": f"user_{decision}"},
                user_id=self._user_id,
            )
            return refusal_message(tool.name, decision)

        return _clone(tool, run)


__all__ = [
    "ALWAYS_DECISION",
    "APPROVED",
    "DENIED",
    "DISCONNECTED",
    "TIMEOUT",
    "TOOL_APPROVAL_FRAME_TYPE",
    "TOOL_APPROVAL_PREFIX",
    "UNAVAILABLE",
    "ToolApprovalBroker",
    "ToolApprovalRequest",
    "refusal_message",
]
