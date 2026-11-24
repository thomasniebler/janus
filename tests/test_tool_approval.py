"""Unit tests for the chat approval gate (JAN-254): adapter wiring and the broker itself."""

import asyncio
import json

import pytest
from fastapi import WebSocketDisconnect

from janus import models
from janus.runtime_adapter import RuntimeAdapter
from janus.services.tool_approval import (
    APPROVED,
    DENIED,
    DISCONNECTED,
    TIMEOUT,
    TOOL_APPROVAL_PREFIX,
    UNAVAILABLE,
    ToolApprovalBroker,
    ToolApprovalRequest,
    refusal_message,
)
from janus.services.tool_call_events import classify_result
from janus.websocket.chat_inbox import MAX_QUEUED_MESSAGES, ChatInbox


def _user(db) -> models.User:
    user = models.User(username="gate-user", hashed_password="x", is_admin=False)
    db.add(user)
    db.commit()
    return user


def _request(call_id: str = "call-1") -> ToolApprovalRequest:
    return ToolApprovalRequest(
        call_id=call_id,
        tool_id="planner.local.write",
        tool_name="create_local_task",
        display_name="Change local planner",
        risk_level="medium",
        reason="Create tasks.",
        args={"title": "x"},
        args_truncated=False,
        timeout_seconds=1,
    )


def test_adapter_builds_gated_entries_and_keeps_them_out_of_the_grant(db):
    """With a gate, a required entry is offered (wrapped outermost) instead of withheld."""
    user = _user(db)
    wrapped: list[tuple[str, str]] = []

    def gate(tool, entry, log, trace):
        wrapped.append((entry.id, tool.name))
        return tool

    prepared = RuntimeAdapter(db, user).prepare_step(
        task_id="chat-gate",
        objective="add a task",
        requested_tool_ids=("planner.local", "planner.local.write", "delegation"),
        task_mode="chat",
        granted_capabilities=frozenset({"planner.local"}),
        confirmed_tool_ids=frozenset({"planner.local", "delegation"}),
        approval_gate=gate,
        guard_tool_errors=True,
    )
    assert [entry.id for entry in prepared.selection.entries] == ["planner.local", "planner.local.write", "delegation"]
    assert prepared.denials == ()
    assert prepared.awaiting_confirmation_tool_ids == ()
    assert {entry_id for entry_id, _ in wrapped} == {"planner.local.write"}
    assert "create_local_task" in {name for _, name in wrapped}
    assert "list_local_tasks" not in {name for _, name in wrapped}
    assert prepared.tool_ids_by_name["create_local_task"] == "planner.local.write"


def test_adapter_without_gate_still_withholds_required_entries(db):
    """Unattended and other callers keep the old confirmation gate."""
    prepared = RuntimeAdapter(db, _user(db)).prepare_step(
        task_id="chat-nogate",
        objective="add a task",
        requested_tool_ids=("planner.local.write",),
        task_mode="chat",
        granted_capabilities=frozenset({"planner.local"}),
    )
    assert prepared.tools == ()
    assert [(d.tool_id, d.reason) for d in prepared.denials] == [("planner.local.write", "confirmation_required")]


def test_refusals_use_the_blocked_shape_for_every_reason():
    """Every refusal is recorded as a blocked call by the JAN-249 classifier."""
    for decision in (DENIED, TIMEOUT, DISCONNECTED, UNAVAILABLE):
        message = refusal_message("send_email", decision)
        assert classify_result("send_email", message) == "blocked"
    assert refusal_message("send_email", DENIED).startswith(
        "BLOCKED: send_email was not run because the user declined."
    )


def test_broker_resolves_only_the_waiting_call():
    """Answers resolve one pending call; unrelated frames are not consumed."""

    async def scenario():
        sent: list[str] = []

        async def send(frame):
            sent.append(frame)

        broker = ToolApprovalBroker(send, user_id=1, timeout_seconds=5)
        broker.note_started("call-1")
        pending = asyncio.create_task(broker.request(_request("call-1")))
        while not sent:
            await asyncio.sleep(0)
        assert json.loads(sent[0][len(TOOL_APPROVAL_PREFIX) :])["call_id"] == "call-1"
        assert broker.handle_frame("hello") is False
        assert broker.handle_frame(json.dumps({"text": "hi"})) is False
        assert broker.handle_frame(json.dumps({"type": "tool_approval", "call_id": "other", "decision": "approve"}))
        assert not pending.done()
        assert broker.handle_frame(json.dumps({"type": "tool_approval", "call_id": "call-1", "decision": "approve"}))
        assert await pending == APPROVED
        assert broker.decisions == {"call-1": APPROVED}

    asyncio.run(scenario())


def test_broker_close_and_missing_client():
    """A close refuses waiting and later calls; a client without the frame is refused at once."""

    async def scenario():
        async def send(_frame):
            return None

        broker = ToolApprovalBroker(send, user_id=1, timeout_seconds=5)
        broker.note_started("call-1")
        pending = asyncio.create_task(broker.request(_request("call-1")))
        await asyncio.sleep(0.01)
        broker.close()
        assert await pending == DISCONNECTED
        assert await broker.request(_request("call-2")) == DISCONNECTED

        silent = ToolApprovalBroker(None, user_id=1, timeout_seconds=5)
        assert await silent.request(_request("call-3")) == UNAVAILABLE

    asyncio.run(scenario())


def test_request_after_close_is_refused_without_sending_or_waiting():
    """A broker closed before a request answers DISCONNECTED at once: no frame, no start-event wait."""
    sent = []

    async def scenario():
        async def send(frame):
            sent.append(frame)

        broker = ToolApprovalBroker(send, user_id=1, timeout_seconds=5)
        broker.close()
        loop = asyncio.get_running_loop()
        started = loop.time()
        decision = await broker.request(_request("call-1"))
        return decision, loop.time() - started

    decision, elapsed = asyncio.run(scenario())

    assert decision == DISCONNECTED
    assert sent == []
    assert elapsed < 0.5  # START_WAIT_SECONDS is 2


def test_gate_on_the_event_loop_thread_refuses_instead_of_deadlocking():
    """A tool invoked synchronously on the connection's own loop cannot wait for an answer."""

    async def scenario():
        broker = ToolApprovalBroker(lambda _frame: asyncio.sleep(0), user_id=1, timeout_seconds=5)
        return broker._await_decision(_request())

    assert asyncio.run(scenario()) == UNAVAILABLE


class _FakeSocket:
    """Feeds frames to ``ChatInbox`` and records how it was closed."""

    def __init__(self, frames):
        self._frames = list(frames)
        self.closed_with = None

    async def receive_text(self):
        if self._frames:
            return self._frames.pop(0)
        await asyncio.sleep(3600)

    async def send_text(self, _text):
        return None

    async def close(self, code=1000):
        self.closed_with = code


def test_inbox_closes_a_client_that_floods_messages_during_a_turn():
    """The queue is bounded; overflowing it closes the socket (1008) and refuses waiting approvals."""

    async def scenario():
        socket = _FakeSocket([f"message {index}" for index in range(MAX_QUEUED_MESSAGES + 1)])
        inbox = ChatInbox(socket)
        broker = ToolApprovalBroker(inbox.send, user_id=1, timeout_seconds=5)
        broker.note_started("call-1")
        pending = asyncio.create_task(broker.request(_request("call-1")))
        await asyncio.sleep(0)
        inbox.start(broker)
        assert await pending == DISCONNECTED
        assert socket.closed_with == 1008
        # Messages still queued never run once the socket is gone.
        with pytest.raises(WebSocketDisconnect):
            await inbox.receive()
        await inbox.stop()

    asyncio.run(scenario())


def test_inbox_queues_messages_and_routes_approval_answers():
    """Ordinary frames are queued in order; approval answers never reach the queue."""

    async def scenario():
        answer = json.dumps({"type": "tool_approval", "call_id": "call-1", "decision": "deny"})
        socket = _FakeSocket(["first", answer, "second"])
        inbox = ChatInbox(socket)
        broker = ToolApprovalBroker(inbox.send, user_id=1, timeout_seconds=5)
        broker.note_started("call-1")
        pending = asyncio.create_task(broker.request(_request("call-1")))
        await asyncio.sleep(0)
        inbox.start(broker)
        assert await inbox.receive() == "first"
        assert await inbox.receive() == "second"
        assert await pending == DENIED
        await inbox.stop()

    asyncio.run(scenario())
