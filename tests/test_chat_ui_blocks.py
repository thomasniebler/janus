"""/ws/chat UI blocks end to end (JAN-252).

The fake chat model runs the *real* prepared ``render_ui`` tool through
LangChain (``astream_events``), interleaved with reply text, so validation,
the ``__UI_BLOCK__`` frame after the call's tool event, persistence before
``__END__``, and the reload through ``GET /api/conversations/{id}`` are all
exercised.
"""

import json

import pytest

from janus import database as db_module
from janus import llm_service as llm_mod
from janus import models, security
from janus.services.tool_call_events import TOOL_EVENT_HANDLERS, TOOL_EVENT_PREFIX, ToolEventTracker
from janus.services.ui_blocks import UI_BLOCK_PREFIX

# db, client and testing_session_local fixtures are provided by conftest.py

CARD = {
    "type": "card",
    "title": "Invoice overdue",
    "body": "<b>120 EUR</b> due Friday",
    "links": [{"label": "Open", "href": "/emails"}],
}
TABLE = {"type": "table", "columns": ["Task", "Due"], "rows": [["Pay invoice", "Fri"], ["Call Anna", None]]}


def _sign_in(client, name: str):
    client.cookies.clear()
    response = client.post("/api/auth/login", json={"username": name, "password": "pw"})
    client.headers[security.CSRF_HEADER_NAME] = response.json()["csrf_token"]
    return client


def _login(client, db, name: str):
    hashed_pw = security.security_service.get_password_hash("pw")
    db.add(models.User(username=name, hashed_password=hashed_pw, is_admin=False))
    db.commit()
    return _sign_in(client, name)


@pytest.fixture
def auth_client(client, db):
    """A client with an authenticated, non-admin session."""
    return _login(client, db, "block-chat-user")


@pytest.fixture
def script(monkeypatch, testing_session_local):
    """What the fake model does, in order: ``("text", str)`` or ``(tool_name, args)``."""
    steps: list[tuple[str, object]] = []
    monkeypatch.setattr(db_module, "SessionLocal", testing_session_local)
    monkeypatch.setattr(llm_mod.LLMService, "__init__", lambda self, **_kw: None)

    async def fake_stream_agent_chat(self, system_prompt, tools, history, user_msg):
        by_name = {tool.name: tool for tool in tools}
        tracker = ToolEventTracker()
        while steps:
            name, args = steps.pop(0)
            if name == "text":
                yield args
                continue
            async for event in by_name[name].astream_events(args, version="v2"):
                handler = TOOL_EVENT_HANDLERS.get(event.get("event"))
                if handler:
                    frame = getattr(tracker, handler)(event)
                    if frame is not None:
                        yield frame

    async def fake_title(self, msg):
        return "UI blocks"

    monkeypatch.setattr(llm_mod.LLMService, "stream_agent_chat", fake_stream_agent_chat)
    monkeypatch.setattr(llm_mod.LLMService, "generate_conversation_title", fake_title)
    monkeypatch.setattr(llm_mod.LLMService, "suggest_memory_fact", lambda self, *a, **k: None)
    return steps


def _until_end(ws) -> list[str]:
    frames = []
    while True:
        frame = ws.receive_text()
        frames.append(frame)
        if frame == "__END__" or frame.startswith("__ERROR__"):
            return frames


def _blocks(frames: list[str]) -> list[dict]:
    return [json.loads(frame[len(UI_BLOCK_PREFIX) :]) for frame in frames if frame.startswith(UI_BLOCK_PREFIX)]


def _events(frames: list[str]) -> list[dict]:
    return [json.loads(frame[len(TOOL_EVENT_PREFIX) :]) for frame in frames if frame.startswith(TOOL_EVENT_PREFIX)]


def _conversation_id(frames: list[str]) -> int:
    return int(next(frame for frame in frames if frame.startswith("__CONVERSATION_ID__:")).split(":")[1])


def _turn(auth_client, text: str, url: str = "/ws/chat?tool_events=1") -> list[str]:
    with auth_client.websocket_connect(url) as ws:
        ws.send_text(json.dumps({"type": "message", "text": text}))
        return _until_end(ws)


def test_blocks_stream_after_their_call_and_reload_identically(auth_client, script, db):
    """A card and a table stream in order between the reply text and come back from the API unchanged."""
    script.extend(
        [
            ("text", "Here is the invoice: "),
            ("render_ui", {"block": CARD}),
            ("text", "and your tasks: "),
            ("render_ui", {"block": TABLE}),
            ("text", "Done."),
        ]
    )
    frames = _turn(auth_client, "show my invoice and tasks")

    streamed = _blocks(frames)
    assert [block["type"] for block in streamed] == ["card", "table"]
    assert all(block["version"] == 1 and block["id"].startswith("blk_") for block in streamed)
    assert streamed[0]["body"] == "<b>120 EUR</b> due Friday"
    assert streamed[1]["rows"] == [["Pay invoice", "Fri"], ["Call Anna", None]]

    # Each block follows its own call's terminal tool event, between the text before and after it.
    order = [frame for frame in frames if not frame.startswith(("__TOOL_SELECTION__", "__CONVERSATION_ID__"))]
    card_at = order.index(UI_BLOCK_PREFIX + json.dumps(streamed[0], ensure_ascii=False))
    assert order[card_at - 1].startswith(TOOL_EVENT_PREFIX) and '"phase": "end"' in order[card_at - 1]
    assert order.index("Here is the invoice: ") < card_at < order.index("and your tasks: ")
    ends = [event for event in _events(frames) if event["phase"] == "end"]
    assert [event["tool_id"] for event in ends] == ["ui_blocks", "ui_blocks"]
    assert ends[0]["result_preview"].startswith(f"Shown to the user: card block {streamed[0]['id']}")

    # Committed before __END__: the reload right after the turn sees the same blocks.
    detail = auth_client.get(f"/api/conversations/{_conversation_id(frames)}").json()
    user_message, assistant = detail["messages"]
    assert user_message["ui_blocks"] == []
    assert assistant["content"] == "Here is the invoice: and your tasks: Done."
    assert [row["payload"] for row in assistant["ui_blocks"]] == streamed
    assert [(row["block_id"], row["block_type"], row["schema_version"]) for row in assistant["ui_blocks"]] == [
        (streamed[0]["id"], "card", 1),
        (streamed[1]["id"], "table", 1),
    ]


def test_an_invalid_block_is_refused_with_the_reason_and_the_retry_is_shown(auth_client, script, db):
    """The model sees the validation error, nothing is streamed or stored for it, and its fixed block works."""
    script.extend(
        [
            ("render_ui", {"block": {**TABLE, "rows": [["only one cell"]]}}),
            ("render_ui", {"block": TABLE}),
            ("text", "Fixed."),
        ]
    )
    frames = _turn(auth_client, "tasks as a table")

    first, second = [event for event in _events(frames) if event["phase"] != "start"]
    assert first["phase"] == "error"
    assert first["result_preview"].startswith("ERROR: render_ui failed: invalid UI block:")
    assert "row 0 has 1 cells but there are 2 columns" in first["result_preview"]
    assert second["phase"] == "end"
    assert [block["type"] for block in _blocks(frames)] == ["table"]
    db.expire_all()
    assert db.query(models.ConversationUIBlock).count() == 1


def test_client_without_tool_events_gets_a_markdown_fallback_error(auth_client, script, db):
    """A client that did not opt in never gets a block frame, and nothing is stored."""
    script.extend([("render_ui", {"block": CARD}), ("text", "**Invoice overdue**")])
    frames = _turn(auth_client, "show my invoice", url="/ws/chat")

    assert not [frame for frame in frames if frame.startswith((UI_BLOCK_PREFIX, TOOL_EVENT_PREFIX))]
    db.expire_all()
    assert db.query(models.ConversationUIBlock).count() == 0
    call = db.query(models.ConversationToolCall).one()
    assert (call.tool_name, call.phase) == ("render_ui", "error")
    assert call.result_preview == (
        "ERROR: render_ui failed: this client cannot render UI blocks; answer in Markdown instead"
    )


def test_action_buttons_may_only_name_tools_of_the_turn(auth_client, script, db):
    """A button for an offered tool is shown; one for a tool the turn does not have is refused."""
    offered = {
        "type": "actions",
        "buttons": [{"label": "Remember", "tool": "add_memory_fact", "args": {"summary": "x"}}],
    }
    unknown = {"type": "actions", "buttons": [{"label": "Wipe", "tool": "delete_everything"}]}
    script.extend([("render_ui", {"block": offered}), ("render_ui", {"block": unknown}), ("text", "ok")])
    frames = _turn(auth_client, "offer buttons")

    (shown,) = _blocks(frames)
    assert shown["buttons"] == [
        {"label": "Remember", "tool": "add_memory_fact", "args": {"summary": "x"}, "style": "secondary"}
    ]
    refused = [event for event in _events(frames) if event["phase"] == "error"]
    assert "not available in this chat: delete_everything" in refused[0]["result_preview"]


def test_blocks_are_owner_scoped_and_deleted_with_the_conversation(auth_client, script, db):
    """Another user cannot read them; deleting the conversation removes them."""
    script.extend([("render_ui", {"block": CARD}), ("text", "ok")])
    conversation_id = _conversation_id(_turn(auth_client, "card please"))
    assert len(auth_client.get(f"/api/conversations/{conversation_id}").json()["messages"][1]["ui_blocks"]) == 1

    other = _login(auth_client, db, "someone-else")
    assert other.get(f"/api/conversations/{conversation_id}").status_code == 404
    assert other.delete(f"/api/conversations/{conversation_id}").status_code == 404
    db.expire_all()
    assert db.query(models.ConversationUIBlock).count() == 1

    owner = _sign_in(auth_client, "block-chat-user")
    assert owner.delete(f"/api/conversations/{conversation_id}").status_code == 204
    db.expire_all()
    assert db.query(models.ConversationUIBlock).count() == 0
