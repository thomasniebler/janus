"""/ws/chat streaming of the turn's step timeline (``__PLAN_EVENT__``, JAN-316)."""

import json

import pytest
from langchain_core.messages import AIMessage

from janus import database as db_module
from janus import llm_service as llm_mod
from janus import models, security
from janus.services.plan_step_events import PLAN_EVENT_PREFIX, TurnEventStream
from janus.services.tool_call_events import TOOL_EVENT_PREFIX

# db, client and testing_session_local fixtures are provided by conftest.py


@pytest.fixture
def auth_client(client, db):
    """A client with an authenticated, non-admin session."""
    hashed_pw = security.security_service.get_password_hash("pw")
    db.add(models.User(username="plan-events-user", hashed_password=hashed_pw, is_admin=False))
    db.commit()
    response = client.post("/api/auth/login", json={"username": "plan-events-user", "password": "pw"})
    client.headers[security.CSRF_HEADER_NAME] = response.json()["csrf_token"]
    return client


def _round(*tool_names: str) -> list[dict]:
    calls = [{"name": name, "args": {}, "id": f"tc-{name}"} for name in tool_names]
    return [
        {"event": "on_chat_model_start", "name": "ChatModel", "run_id": "m", "parent_ids": []},
        {
            "event": "on_chat_model_end",
            "name": "ChatModel",
            "run_id": "m",
            "parent_ids": [],
            "data": {"output": AIMessage(content="", tool_calls=calls)},
        },
    ]


def _call(name: str, run_id: str) -> list[dict]:
    return [
        {"event": "on_tool_start", "name": name, "run_id": run_id, "parent_ids": [], "data": {"input": {}}},
        {"event": "on_tool_end", "name": name, "run_id": run_id, "parent_ids": [], "data": {"output": "ok"}},
    ]


@pytest.fixture
def fake_chat(monkeypatch, testing_session_local):
    """Stream a turn with two tool calls in two model rounds, then the reply."""
    monkeypatch.setattr(db_module, "SessionLocal", testing_session_local)
    monkeypatch.setattr(llm_mod.LLMService, "__init__", lambda self, **_kw: None)

    async def fake_stream_agent_chat(self, system_prompt, tools, history, user_msg):
        turn = TurnEventStream()
        events = [*_round("list_memory_facts"), *_call("list_memory_facts", "call-1")]
        events += [*_round("list_topics"), *_call("list_topics", "call-2"), *_round()]
        for event in events:
            for frame in turn.observe(event):
                yield frame
        for frame in turn.finish():
            yield frame
        yield "Done."

    async def fake_title(self, msg):
        return "Steps"

    monkeypatch.setattr(llm_mod.LLMService, "stream_agent_chat", fake_stream_agent_chat)
    monkeypatch.setattr(llm_mod.LLMService, "generate_conversation_title", fake_title)
    monkeypatch.setattr(llm_mod.LLMService, "suggest_memory_fact", lambda self, *a, **k: None)


def _turn(client, path: str) -> tuple[int, list[str]]:
    with client.websocket_connect(path) as ws:
        ws.send_text("what do you know?")
        frames = []
        while True:
            frame = ws.receive_text()
            frames.append(frame)
            if frame == "__END__" or frame.startswith("__ERROR__"):
                break
    conversation_id = int(next(f for f in frames if f.startswith("__CONVERSATION_ID__:")).split(":")[1])
    return conversation_id, frames


def test_opted_in_client_sees_both_steps_live(auth_client, fake_chat):
    """Two tool calls in two rounds stream as two steps, each call naming its step."""
    conversation_id, frames = _turn(auth_client, "/ws/chat?tool_events=1")

    timeline = []
    for frame in frames:
        if frame.startswith(PLAN_EVENT_PREFIX):
            payload = json.loads(frame[len(PLAN_EVENT_PREFIX) :])
            timeline.append(("plan", payload["step_id"], payload["status"]))
        elif frame.startswith(TOOL_EVENT_PREFIX):
            payload = json.loads(frame[len(TOOL_EVENT_PREFIX) :])
            timeline.append(("tool", payload["call_id"], payload["phase"], payload["step_id"], payload["tool_id"]))
    assert timeline == [
        ("plan", "step-1", "pending"),
        ("plan", "step-1", "running"),
        ("tool", "call-1", "start", "step-1", "memory"),
        ("tool", "call-1", "end", "step-1", "memory"),
        ("plan", "step-1", "completed"),
        ("plan", "step-2", "pending"),
        ("plan", "step-2", "running"),
        ("tool", "call-2", "start", "step-2", "topics"),
        ("tool", "call-2", "end", "step-2", "topics"),
        ("plan", "step-2", "completed"),
    ]
    # Step frames come between __START__ and __END__ and are never reply text.
    assert frames.index("__START__") < frames.index(next(f for f in frames if f.startswith(PLAN_EVENT_PREFIX)))
    detail = auth_client.get(f"/api/conversations/{conversation_id}").json()
    assistant = [message for message in detail["messages"] if message["role"] == "assistant"][0]
    assert assistant["content"] == "Done."
    # Live only: the stored tool-call rows keep the JAN-249 shape.
    assert [call["call_id"] for call in assistant["tool_calls"]] == ["call-1", "call-2"]
    assert all("step_id" not in call for call in assistant["tool_calls"])


def test_legacy_client_never_receives_plan_events(auth_client, fake_chat):
    """Clients that did not opt in (the iOS scaffold) get no __PLAN_EVENT__ frame to mis-render."""
    conversation_id, frames = _turn(auth_client, "/ws/chat")

    assert not any(frame.startswith(PLAN_EVENT_PREFIX) for frame in frames)
    detail = auth_client.get(f"/api/conversations/{conversation_id}").json()
    assistant = [message for message in detail["messages"] if message["role"] == "assistant"][0]
    assert assistant["content"] == "Done."
