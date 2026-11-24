"""/ws/chat per-call approval of confirmation="required" tools (JAN-254).

The fake chat model below runs the *real* prepared tools through LangChain
(``astream_events``), so the approval gate, its worker-thread pause, the
``call_id`` it reports, and the tool's real side effect are all exercised.
"""

import json
import threading
import time

import pytest

from janus import config, models, security, tool_policy
from janus import database as db_module
from janus import llm_service as llm_mod
from janus.agent_tools import DEFAULT_TOOL_REGISTRY
from janus.services.tool_approval import TOOL_APPROVAL_PREFIX
from janus.services.tool_call_events import TOOL_EVENT_HANDLERS, TOOL_EVENT_PREFIX, ToolEventTracker
from janus.websocket.chat_inbox import ChatInbox

# db, client and testing_session_local fixtures are provided by conftest.py


@pytest.fixture
def auth_client(client, db):
    """A client with an authenticated, non-admin session."""
    hashed_pw = security.security_service.get_password_hash("pw")
    db.add(models.User(username="approval-user", hashed_password=hashed_pw, is_admin=False))
    db.commit()
    response = client.post("/api/auth/login", json={"username": "approval-user", "password": "pw"})
    client.headers[security.CSRF_HEADER_NAME] = response.json()["csrf_token"]
    return client


@pytest.fixture
def script(monkeypatch, testing_session_local):
    """The tool calls (name, args) the fake model makes, in order; each is used up by the turn that makes it."""
    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(db_module, "SessionLocal", testing_session_local)
    monkeypatch.setattr(llm_mod.LLMService, "__init__", lambda self, **_kw: None)

    async def fake_stream_agent_chat(self, system_prompt, tools, history, user_msg):
        by_name = {tool.name: tool for tool in tools}
        tracker = ToolEventTracker()
        while calls:
            name, args = calls.pop(0)
            async for event in by_name[name].astream_events(args, version="v2"):
                handler = TOOL_EVENT_HANDLERS.get(event.get("event"))
                if handler:
                    frame = getattr(tracker, handler)(event)
                    if frame is not None:
                        yield frame
        yield "Done."

    async def fake_title(self, msg):
        return "Approval"

    monkeypatch.setattr(llm_mod.LLMService, "stream_agent_chat", fake_stream_agent_chat)
    monkeypatch.setattr(llm_mod.LLMService, "generate_conversation_title", fake_title)
    monkeypatch.setattr(llm_mod.LLMService, "suggest_memory_fact", lambda self, *a, **k: None)
    return calls


def _payload(frame: str, prefix: str) -> dict:
    return json.loads(frame[len(prefix) :])


def _receive_until(ws, predicate) -> list[str]:
    frames = []
    while True:
        frame = ws.receive_text()
        frames.append(frame)
        if predicate(frame) or frame.startswith("__ERROR__"):
            return frames


def _until_approval(ws) -> tuple[list[str], dict]:
    frames = _receive_until(ws, lambda frame: frame.startswith(TOOL_APPROVAL_PREFIX))
    return frames, _payload(frames[-1], TOOL_APPROVAL_PREFIX)


def _until_end(ws) -> list[str]:
    return _receive_until(ws, lambda frame: frame == "__END__")


def _events(frames: list[str]) -> list[dict]:
    return [_payload(frame, TOOL_EVENT_PREFIX) for frame in frames if frame.startswith(TOOL_EVENT_PREFIX)]


def _answer(ws, call_id: str, decision: str) -> None:
    ws.send_text(json.dumps({"type": "tool_approval", "call_id": call_id, "decision": decision}))


def _tasks(db) -> list[str]:
    db.expire_all()
    return [task.title for task in db.query(models.PlannerTask).all()]


def _stored_calls(db) -> list[tuple[str, str, str | None]]:
    db.expire_all()
    rows = db.query(models.ConversationToolCall).order_by(models.ConversationToolCall.id).all()
    return [(row.tool_name, row.phase, row.approval) for row in rows]


def _wait_for(read, expected, timeout: float = 15.0):
    """Poll ``read()`` until it returns ``expected``: the real test server finishes a closed turn on its own."""
    deadline = time.monotonic() + timeout
    value = read()
    while value != expected and time.monotonic() < deadline:
        time.sleep(0.05)
        value = read()
    return value


def test_required_tool_runs_only_after_approve(auth_client, script, db):
    """The call pauses on a redacted approval request and runs once approved."""
    script.append(("create_local_task", {"title": "Buy milk", "description": "api_key=1", "due_at": None}))
    with auth_client.websocket_connect("/ws/chat?tool_events=1") as ws:
        ws.send_text("add a task")
        frames, request = _until_approval(ws)

        selection = _payload(next(f for f in frames if f.startswith("__TOOL_SELECTION__:")), "__TOOL_SELECTION__:")
        assert {"id": "planner.local.write", "confirmation": "required"} in selection["tools"]
        assert all(denial["tool_id"] != "planner.local.write" for denial in selection["denied"])
        # The call's start event precedes its approval request and shares its id.
        start = _events(frames)[-1]
        assert (start["phase"], start["call_id"]) == ("start", request["call_id"])
        assert request["tool_id"] == "planner.local.write"
        assert request["tool_name"] == "create_local_task"
        assert request["display_name"] == "Change local planner"
        assert request["risk_level"] == "medium"
        assert request["reason"]
        assert request["args"]["title"] == "Buy milk"
        assert request["timeout_seconds"] == config.settings.chat_tool_approval_timeout_seconds
        assert _tasks(db) == []  # paused, nothing ran yet

        _answer(ws, request["call_id"], "approve")
        frames = _until_end(ws)

    end = _events(frames)[-1]
    assert (end["call_id"], end["phase"], end["approval"]) == (request["call_id"], "end", "approved")
    assert _tasks(db) == ["Buy milk"]
    assert _stored_calls(db) == [("create_local_task", "end", "approved")]


def test_deny_blocks_the_call_and_is_recorded(auth_client, script, db):
    """Deny never runs the tool; the model gets the tool-policy BLOCKED shape."""
    script.append(("create_local_task", {"title": "Nope"}))
    with auth_client.websocket_connect("/ws/chat?tool_events=1") as ws:
        ws.send_text("add a task")
        _, request = _until_approval(ws)
        _answer(ws, request["call_id"], "deny")
        frames = _until_end(ws)

    end = _events(frames)[-1]
    assert (end["phase"], end["approval"]) == ("blocked", "denied")
    assert end["result_preview"].startswith("BLOCKED: create_local_task was not run because the user declined.")
    assert _tasks(db) == []
    assert _stored_calls(db) == [("create_local_task", "blocked", "denied")]
    refused = db.query(models.Event).filter(models.Event.source == "ToolApproval").all()
    assert [event.message for event in refused] == ["Refused tool call create_local_task"]


def test_unanswered_request_times_out_as_declined(auth_client, script, db, monkeypatch):
    """No answer within the configured timeout counts as a refusal."""
    monkeypatch.setattr(config.settings, "chat_tool_approval_timeout_seconds", 0.3)
    script.append(("create_local_task", {"title": "Later"}))
    with auth_client.websocket_connect("/ws/chat?tool_events=1") as ws:
        ws.send_text("add a task")
        _, request = _until_approval(ws)
        assert request["timeout_seconds"] == 0.3
        frames = _until_end(ws)
        # A late answer for the expired call changes nothing.
        _answer(ws, request["call_id"], "approve")

    end = _events(frames)[-1]
    assert (end["phase"], end["approval"]) == ("blocked", "timeout")
    assert _tasks(db) == []
    assert _stored_calls(db) == [("create_local_task", "blocked", "timeout")]


def test_disconnect_mid_approval_refuses_and_reconnect_does_not_replay(auth_client, script, db):
    """Closing the socket refuses the waiting call; a new connection never sees or approves it."""
    script.append(("create_local_task", {"title": "Orphan"}))
    with auth_client.websocket_connect("/ws/chat?tool_events=1") as ws:
        ws.send_text("add a task")
        frames, request = _until_approval(ws)
    conversation_id = int(next(f for f in frames if f.startswith("__CONVERSATION_ID__:")).split(":")[1])

    # The turn still finishes server-side and is stored with the refusal.
    expected = [("create_local_task", "blocked", "disconnected")]
    assert _wait_for(lambda: _stored_calls(db), expected) == expected
    assert _tasks(db) == []

    with auth_client.websocket_connect(f"/ws/chat?tool_events=1&conversation_id={conversation_id}") as ws:
        assert ws.receive_text() == f"__CONVERSATION_ID__:{conversation_id}"
        # An answer for the old connection's call is ignored, not queued as a message.
        _answer(ws, request["call_id"], "approve")
        ws.send_text("hello again")
        frames = _until_end(ws)
    assert not any(frame.startswith(TOOL_APPROVAL_PREFIX) for frame in frames)
    assert _tasks(db) == []


def test_an_approval_never_covers_another_call(auth_client, script, db):
    """Approving one call (or replaying that answer) does not approve the next call to the same tool."""
    script.extend([("create_local_task", {"title": "First"}), ("create_local_task", {"title": "Second"})])
    with auth_client.websocket_connect("/ws/chat?tool_events=1") as ws:
        ws.send_text("add two tasks")
        _, first = _until_approval(ws)
        _answer(ws, first["call_id"], "approve")
        _, second = _until_approval(ws)
        assert second["call_id"] != first["call_id"]
        assert second["args"]["title"] == "Second"
        # Replaying the first approval, an unknown id, or a bogus decision are ignored.
        _answer(ws, first["call_id"], "approve")
        _answer(ws, "not-a-call", "approve")
        _answer(ws, second["call_id"], "yes please")
        _answer(ws, second["call_id"], "deny")
        _until_end(ws)

    assert _tasks(db) == ["First"]
    assert _stored_calls(db) == [
        ("create_local_task", "end", "approved"),
        ("create_local_task", "blocked", "denied"),
    ]


def test_recommended_tools_run_without_approval(auth_client, script, db):
    """``recommended``/``never`` tools are unaffected: no request, no decision recorded."""
    script.extend([("add_memory_fact", {"summary": "Likes tea"}), ("list_local_tasks", {})])
    with auth_client.websocket_connect("/ws/chat?tool_events=1") as ws:
        ws.send_text("remember")
        frames = _until_end(ws)

    assert not any(frame.startswith(TOOL_APPROVAL_PREFIX) for frame in frames)
    assert [(event["phase"], event["approval"]) for event in _events(frames) if event["phase"] != "start"] == [
        ("end", None),
        ("end", None),
    ]
    assert db.query(models.AgentMemoryFact).filter(models.AgentMemoryFact.summary == "Likes tea").count() == 1


def test_client_without_tool_events_gets_blocked_never_a_silent_run(auth_client, script, db):
    """A client that cannot show the card (iOS) is refused instead of the tool running unasked."""
    script.append(("create_local_task", {"title": "Silent"}))
    with auth_client.websocket_connect("/ws/chat") as ws:
        ws.send_text("add a task")
        frames = _until_end(ws)

    assert not any(frame.startswith((TOOL_APPROVAL_PREFIX, TOOL_EVENT_PREFIX)) for frame in frames)
    assert _tasks(db) == []
    assert _stored_calls(db) == [("create_local_task", "blocked", "unavailable")]
    detail = auth_client.get(f"/api/conversations/{db.query(models.Conversation).one().id}").json()
    call = [message for message in detail["messages"] if message["role"] == "assistant"][0]["tool_calls"][0]
    assert call["approval"] == "unavailable"
    assert call["result_preview"].startswith("BLOCKED: create_local_task was not run because this chat client")


def test_message_sent_while_a_call_waits_is_queued_for_the_next_turn(auth_client, script, db):
    """The reader keeps ordinary messages in order; only approval frames are consumed."""
    script.append(("create_local_task", {"title": "Queued"}))
    with auth_client.websocket_connect("/ws/chat?tool_events=1") as ws:
        ws.send_text("add a task")
        _, request = _until_approval(ws)
        ws.send_text("and another thing")
        _answer(ws, request["call_id"], "approve")
        _until_end(ws)
        frames = _until_end(ws)
    assert "Done." in frames
    db.expire_all()
    user_messages = (
        db.query(models.ConversationMessage)
        .filter(models.ConversationMessage.role == models.MessageRole.USER)
        .order_by(models.ConversationMessage.id)
        .all()
    )
    assert [message.content for message in user_messages] == ["add a task", "and another thing"]


# --- Per-tool "In chat" switch (JAN-306) ---------------------------------------------------------


def _entry(tool_id: str):
    return next(entry for entry in DEFAULT_TOOL_REGISTRY.entries() if entry.id == tool_id)


def _approval_user(db) -> models.User:
    return db.query(models.User).filter(models.User.username == "approval-user").one()


def _switch_chat(db, user_id: int, tool_id: str, mode: str) -> None:
    tool_policy.update_policy(db, user_id, tool_id, chat_mode=mode, entry=_entry(tool_id))


def _selection(frames: list[str]) -> dict:
    return _payload(next(f for f in frames if f.startswith("__TOOL_SELECTION__:")), "__TOOL_SELECTION__:")


@pytest.fixture
def offered_names(script, monkeypatch):
    """Tool names the chat model was offered on each turn (wraps the scripted fake model)."""
    seen: list[frozenset[str]] = []
    scripted = llm_mod.LLMService.stream_agent_chat

    async def recording(self, system_prompt, tools, history, user_msg):
        seen.append(frozenset(tool.name for tool in tools))
        async for token in scripted(self, system_prompt, tools, history, user_msg):
            yield token

    monkeypatch.setattr(llm_mod.LLMService, "stream_agent_chat", recording)
    return seen


class _ServerReads:
    """Counts the times /ws/chat asked its inbox for the next message.

    ``__END__`` is not the end of a turn on the server: the title and the memory
    suggestion run, and commit, after it. The test database is one shared
    in-memory SQLite connection (``StaticPool``), so a test that writes while
    that tail is still running interleaves two sessions' transactions on one
    connection: a rollback or commit on one side ends the other side's
    transaction too (the lost write, or ``UPDATE ... 0 were matched``). The
    server asking for the next message is the event that says the previous
    turn, tail included, is done and committed.
    """

    def __init__(self) -> None:
        self._count = 0
        self._changed = threading.Condition()

    def entered(self) -> None:
        with self._changed:
            self._count += 1
            self._changed.notify_all()

    def wait_for(self, count: int, timeout: float = 15.0) -> None:
        """Block until the server has asked for its ``count``-th message (the turns before it are done)."""
        with self._changed:
            if not self._changed.wait_for(lambda: self._count >= count, timeout=timeout):
                raise AssertionError(f"chat server asked for {self._count} messages, expected {count}")


@pytest.fixture
def server_reads(monkeypatch):
    """Lets a test wait until the chat server finished its turn and waits for the next message."""
    reads = _ServerReads()
    receive = ChatInbox.receive

    async def counting_receive(self):
        reads.entered()
        return await receive(self)

    monkeypatch.setattr(ChatInbox, "receive", counting_receive)
    return reads


def test_off_in_chat_tool_is_withheld_not_stubbed(auth_client, offered_names, db):
    """An "Off in chat" required tool is not requested: absent from the toolset, the selection, and the denials."""
    _switch_chat(db, _approval_user(db).id, "planner.local.write", tool_policy.CHAT_MODE_OFF)
    with auth_client.websocket_connect("/ws/chat?tool_events=1") as ws:
        ws.send_text("add a task")
        frames = _until_end(ws)

    selection = _selection(frames)
    assert all(tool["id"] != "planner.local.write" for tool in selection["tools"])
    # Withheld, not denied: no refusal stub or denial entry either.
    assert all(denial["tool_id"] != "planner.local.write" for denial in selection["denied"])
    assert "create_local_task" not in offered_names[-1]
    # The rest of chat is untouched: the read side of the same planner is still offered.
    assert any(tool["id"] == "planner.local" for tool in selection["tools"])
    assert not any(frame.startswith(TOOL_APPROVAL_PREFIX) for frame in frames)


def test_explicit_ask_every_time_still_asks_per_call(auth_client, script, db):
    """Ask every time (the default, here stored explicitly) keeps JAN-254's per-call approval."""
    _switch_chat(db, _approval_user(db).id, "planner.local.write", tool_policy.CHAT_MODE_ASK)
    script.append(("create_local_task", {"title": "Buy milk", "description": "", "due_at": None}))
    with auth_client.websocket_connect("/ws/chat?tool_events=1") as ws:
        ws.send_text("add a task")
        frames, request = _until_approval(ws)
        assert {"id": "planner.local.write", "confirmation": "required"} in _selection(frames)["tools"]
        assert _tasks(db) == []
        _answer(ws, request["call_id"], "deny")
        _until_end(ws)
    assert _tasks(db) == []


def test_another_users_off_in_chat_setting_never_applies(auth_client, script, db):
    """The switch is per user: someone else's "Off in chat" leaves this user's tool offered and asking."""
    other = models.User(username="someone-else", hashed_password="x", is_admin=False)
    db.add(other)
    db.commit()
    _switch_chat(db, other.id, "planner.local.write", tool_policy.CHAT_MODE_OFF)
    script.append(("create_local_task", {"title": "Buy milk", "description": "", "due_at": None}))
    with auth_client.websocket_connect("/ws/chat?tool_events=1") as ws:
        ws.send_text("add a task")
        _frames, request = _until_approval(ws)
        assert request["tool_id"] == "planner.local.write"
        _answer(ws, request["call_id"], "approve")
        _until_end(ws)
    assert _tasks(db) == ["Buy milk"]


def test_switching_off_in_chat_applies_from_the_next_message(auth_client, offered_names, server_reads, db):
    """The switch is read per message, not cached for the connection."""
    with auth_client.websocket_connect("/ws/chat?tool_events=1") as ws:
        ws.send_text("first")
        _until_end(ws)
        # The first turn's title and memory tail commit after __END__: change the
        # setting only once the server waits for the second message.
        server_reads.wait_for(2)
        assert "create_local_task" in offered_names[-1]
        _switch_chat(db, _approval_user(db).id, "planner.local.write", tool_policy.CHAT_MODE_OFF)
        ws.send_text("second")
        _until_end(ws)
        server_reads.wait_for(3)
        assert len(offered_names) == 2
        assert "create_local_task" not in offered_names[-1]
