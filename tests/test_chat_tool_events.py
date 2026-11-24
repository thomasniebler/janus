"""/ws/chat streaming and persistence of structured tool-call events (JAN-249)."""

import json

import pytest

from janus import database as db_module
from janus import llm_service as llm_mod
from janus import models, security
from janus.services.tool_call_events import TOOL_EVENT_PREFIX, ToolEventTracker

# db, client and testing_session_local fixtures are provided by conftest.py


def _login(client, db, username: str):
    hashed_pw = security.security_service.get_password_hash("pw")
    db.add(models.User(username=username, hashed_password=hashed_pw, is_admin=False))
    db.commit()
    response = client.post("/api/auth/login", json={"username": username, "password": "pw"})
    client.headers[security.CSRF_HEADER_NAME] = response.json()["csrf_token"]
    return client


@pytest.fixture
def auth_client(client, db):
    """A client with an authenticated, non-admin session."""
    return _login(client, db, "tool-events-user")


@pytest.fixture
def fake_chat(monkeypatch, testing_session_local):
    """Stream a turn with one successful and one refused tool call, plus legacy markers."""
    monkeypatch.setattr(db_module, "SessionLocal", testing_session_local)
    monkeypatch.setattr(llm_mod.LLMService, "__init__", lambda self, **_kw: None)

    async def fake_stream_agent_chat(self, system_prompt, tools, history, user_msg):
        tracker = ToolEventTracker()
        yield tracker.start(
            {"name": "list_memory_facts", "run_id": "call-1", "data": {"input": {"query": "x", "api_key": "k"}}}
        )
        yield "__TOOL_CALL__:list_memory_facts"
        yield tracker.end({"name": "list_memory_facts", "run_id": "call-1", "data": {"output": "<b>fact</b>"}})
        yield "__TOOL_RESULT__:list_memory_facts"
        yield tracker.start({"name": "send_email", "run_id": "call-2", "data": {"input": {"to": "a@b.c"}}})
        yield tracker.end(
            {
                "name": "send_email",
                "run_id": "call-2",
                "data": {"output": "BLOCKED: send_email was not run because it is not available. Nothing was changed."},
            }
        )
        yield "Done."

    async def fake_title(self, msg):
        return "Tools"

    monkeypatch.setattr(llm_mod.LLMService, "stream_agent_chat", fake_stream_agent_chat)
    monkeypatch.setattr(llm_mod.LLMService, "generate_conversation_title", fake_title)
    monkeypatch.setattr(llm_mod.LLMService, "suggest_memory_fact", lambda self, *a, **k: None)


def _turn(client, path: str) -> tuple[int, list[str]]:
    with client.websocket_connect(path) as ws:
        ws.send_text("check my memory")
        frames = []
        while True:
            frame = ws.receive_text()
            frames.append(frame)
            if frame == "__END__" or frame.startswith("__ERROR__"):
                break
    conversation_id = int(next(f for f in frames if f.startswith("__CONVERSATION_ID__:")).split(":")[1])
    return conversation_id, frames


def _tool_events(frames: list[str]) -> list[dict]:
    return [json.loads(frame[len(TOOL_EVENT_PREFIX) :]) for frame in frames if frame.startswith(TOOL_EVENT_PREFIX)]


def test_opted_in_client_receives_tool_events_with_tool_ids(auth_client, fake_chat):
    """?tool_events=1 streams every phase, naming the registry capability; text stays clean."""
    conversation_id, frames = _turn(auth_client, "/ws/chat?tool_events=1")

    events = _tool_events(frames)
    assert [(event["call_id"], event["phase"]) for event in events] == [
        ("call-1", "start"),
        ("call-1", "end"),
        ("call-2", "start"),
        ("call-2", "blocked"),
    ]
    assert events[0]["tool_id"] == "memory"
    assert events[0]["args"] == {"query": "x", "api_key": "[redacted]"}
    # Legacy markers still stream for one release.
    assert "__TOOL_CALL__:list_memory_facts" in frames
    assert "__TOOL_RESULT__:list_memory_facts" in frames

    detail = auth_client.get(f"/api/conversations/{conversation_id}").json()
    assistant = [message for message in detail["messages"] if message["role"] == "assistant"][0]
    assert assistant["content"] == "Done."
    calls = assistant["tool_calls"]
    assert [(call["call_id"], call["tool_name"], call["phase"]) for call in calls] == [
        ("call-1", "list_memory_facts", "end"),
        ("call-2", "send_email", "blocked"),
    ]
    assert calls[0]["tool_id"] == "memory"
    assert calls[0]["args"] == {"query": "x", "api_key": "[redacted]"}
    # Untrusted result text is stored verbatim as text, never interpreted.
    assert calls[0]["result_preview"] == "<b>fact</b>"
    assert calls[0]["result_truncated"] is False
    user_message = [message for message in detail["messages"] if message["role"] == "user"][0]
    assert user_message["tool_calls"] == []


def test_legacy_client_never_receives_tool_events_but_calls_are_stored(auth_client, fake_chat, db):
    """Clients that did not opt in (the iOS scaffold) get no __TOOL_EVENT__ frame to mis-render."""
    conversation_id, frames = _turn(auth_client, "/ws/chat")

    assert _tool_events(frames) == []
    assert not any(frame.startswith(TOOL_EVENT_PREFIX) for frame in frames)
    assert "__TOOL_CALL__:list_memory_facts" in frames
    assert db.query(models.ConversationToolCall).count() == 2

    detail = auth_client.get(f"/api/conversations/{conversation_id}").json()
    assistant = [message for message in detail["messages"] if message["role"] == "assistant"][0]
    assert len(assistant["tool_calls"]) == 2


def test_tool_calls_are_owner_scoped_and_deleted_with_the_conversation(auth_client, fake_chat, db, client):
    """Another user cannot read a conversation's tool calls; deleting it removes them."""
    conversation_id, _ = _turn(auth_client, "/ws/chat?tool_events=1")
    assert db.query(models.ConversationToolCall).count() == 2

    owner_headers = dict(auth_client.headers)
    owner_cookies = dict(auth_client.cookies)
    _login(client, db, "someone-else")
    assert client.get(f"/api/conversations/{conversation_id}").status_code == 404

    client.cookies.clear()
    client.cookies.update(owner_cookies)
    client.headers.update(owner_headers)
    assert client.delete(f"/api/conversations/{conversation_id}").status_code == 204
    db.expire_all()
    assert db.query(models.ConversationToolCall).count() == 0
