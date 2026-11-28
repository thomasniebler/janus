"""Tests for the WebSocket briefing and chat endpoints.

These tests verify:
- Unauthenticated connections are rejected.
- The briefing socket frames the Morning briefing trigger's output with
  __START__/__END__, reuses today's run, and runs it on refresh.
- The chat stream emits __START__ before a response and __END__ after it.
- A failed briefing run ends with __ERROR__ instead of __END__.
"""

import threading
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.websockets import WebSocketDisconnect

from janus import builtin_triggers, config, database, models, security
from janus import trigger_definition_service as trigger_definitions
from janus.database import Base, get_db
from janus.main import api

# ---------------------------------------------------------------------------
# In-memory database wiring
# ---------------------------------------------------------------------------

SQLALCHEMY_DATABASE_URL = "sqlite://"
engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


async def override_get_db():
    """Override the database dependency to use the test in-memory engine."""
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def setup_db():
    """Create and tear down the schema around every test.

    Also redirects database.SessionLocal and the get_db FastAPI dependency
    override to the test engine for the duration of the test.

    - ws_chat creates sessions via database.SessionLocal directly (not via
      get_db), so we patch the module attribute directly.
    - Saving and restoring both overrides keeps this file's fixtures from
      contaminating test_api.py, which registers its own get_db override.
    """
    _original_dep_override = api.dependency_overrides.get(get_db)
    _original_session_local = database.SessionLocal
    _original_ensure_schema = database.ensure_schema_up_to_date

    api.dependency_overrides[get_db] = override_get_db
    database.SessionLocal = TestingSessionLocal
    database.ensure_schema_up_to_date = lambda database_url=None: None
    Base.metadata.create_all(bind=engine)
    try:
        yield
    finally:
        Base.metadata.drop_all(bind=engine)
        database.SessionLocal = _original_session_local
        database.ensure_schema_up_to_date = _original_ensure_schema
        if _original_dep_override is None:
            api.dependency_overrides.pop(get_db, None)
        else:
            api.dependency_overrides[get_db] = _original_dep_override


@pytest.fixture
def db():
    """Yield a database session backed by the test in-memory engine."""
    db = TestingSessionLocal()
    yield db
    db.close()


@pytest.fixture
def client(monkeypatch):
    """Yield a TestClient for the FastAPI app."""
    monkeypatch.setattr(config.settings, "allowed_hosts", ["testserver"])
    monkeypatch.setattr(config.settings, "allowed_origins", ["http://testserver"])
    with TestClient(api) as c:
        c.headers["origin"] = "http://testserver"
        yield c


@pytest.fixture
def auth_client(client, db):
    """Returns a TestClient whose HTTP session is authenticated."""
    hashed_pw = security.security_service.get_password_hash("password")
    user = models.User(username="testuser", hashed_password=hashed_pw, is_admin=False)
    db.add(user)
    db.commit()
    response = client.post("/api/auth/login", json={"username": "testuser", "password": "password"})
    client.headers[security.CSRF_HEADER_NAME] = response.json()["csrf_token"]
    return client


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _token_generator(*tokens):
    """Async generator that yields the provided string tokens."""
    for token in tokens:
        yield token


def _collect_messages(ws, stop_on: tuple[str, ...] = ("__END__", "__ERROR__:"), max_msgs: int = 50) -> list[str]:
    """Collect WebSocket messages until a stop signal or exception.

    Breaks out of the loop when any of *stop_on* signals are received. Each
    entry in *stop_on* is matched as a prefix, so ``"__ERROR__:"`` matches
    ``"__ERROR__:some message"`` as well as the bare string ``"__ERROR__:"``.
    The stop signal is kept in the returned list.
    """
    messages: list[str] = []
    for _ in range(max_msgs):
        try:
            msg = ws.receive_text()
            messages.append(msg)
            if any(msg == s or msg.startswith(s) for s in stop_on):
                break
        except Exception:
            break
    return messages


# ---------------------------------------------------------------------------
# Briefing WebSocket tests
# ---------------------------------------------------------------------------


def test_ws_briefing_rejects_unauthenticated(client):
    """An unauthenticated WebSocket connection to /ws/briefing should be closed."""
    with client.websocket_connect("/ws/briefing") as ws, pytest.raises(WebSocketDisconnect) as excinfo:
        ws.receive_text()

    assert excinfo.value.code == 1008


def test_ws_briefing_rejects_unexpected_origin(auth_client, monkeypatch):
    """Browser WebSocket handshakes from untrusted origins should be rejected."""
    monkeypatch.setattr(config.settings, "allowed_origins", ["https://janus.example.com"])

    with pytest.raises(WebSocketDisconnect) as excinfo:  # noqa: SIM117
        with auth_client.websocket_connect("/ws/briefing", headers={"origin": "https://evil.example.com"}):
            pass

    assert excinfo.value.code == 1008
    assert excinfo.value.reason == "Invalid Origin header"


def _briefing_definition_id(db, username: str = "testuser") -> int:
    """Seed (idempotently) and return the user's built-in Morning briefing definition id."""
    user = db.query(models.User).filter(models.User.username == username).one()
    builtin_triggers.ensure_builtin_definitions(db, user.id)
    definition = trigger_definitions.get_builtin_definition(db, user.id, builtin_triggers.BUILTIN_BRIEFING)
    assert definition is not None
    return definition.id


def _seed_completed_briefing_run(db, definition_id: int, output: str, completed_at: datetime) -> None:
    """Record a completed briefing run, as the durable trigger worker would."""
    definition = db.get(models.TriggerDefinition, definition_id)
    db.add(
        models.RuntimeTrigger(
            user_id=definition.user_id,
            trigger_id=f"briefing-{output}",
            idempotency_key=f"briefing-{output}",
            task_id=f"briefing-{output}",
            kind="schedule",
            source="test",
            payload={},
            status=models.RuntimeTriggerStatus.COMPLETED.value,
            completed_at=completed_at,
            definition_id=definition_id,
            output=output,
        )
    )
    db.commit()


class FakeInlineRun:
    """Stand-in for ``run_definition_inline``: records which definition ran and returns a canned record."""

    def __init__(self, output: str | None = "Hello world", status: str = "completed", last_error: str | None = None):
        """Configure the record the fake run returns."""
        self.output = output
        self.status = status
        self.last_error = last_error
        self.calls: list[int] = []

    def __call__(self, session_factory, definition_id, **_kwargs):
        """Record the run and return the configured record."""
        self.calls.append(definition_id)
        return SimpleNamespace(status=self.status, output=self.output, last_error=self.last_error)


def test_ws_briefing_emits_start_and_end(auth_client, db, monkeypatch):
    """With no briefing yet today, the socket runs the briefing trigger and frames its output."""
    fake_run = FakeInlineRun(output="Hello world")
    monkeypatch.setattr("janus.websocket.briefing.run_definition_inline", fake_run)

    with auth_client.websocket_connect("/ws/briefing") as ws:
        messages = _collect_messages(ws)

    assert messages == ["__START__", "Hello world", "__END__"]
    assert fake_run.calls == [_briefing_definition_id(db)]


def test_ws_briefing_emits_error_signal_on_llm_failure(auth_client, monkeypatch):
    """A failed briefing run sends __ERROR__:<message> and never __END__."""
    monkeypatch.setattr(
        "janus.websocket.briefing.run_definition_inline",
        FakeInlineRun(output=None, status="failed", last_error="LLM exploded"),
    )

    with auth_client.websocket_connect("/ws/briefing") as ws:
        messages = _collect_messages(ws)

    assert messages[0] == "__START__"
    assert messages[-1].startswith("__ERROR__:")
    assert "__END__" not in messages


def test_ws_briefing_emits_error_signal_when_the_run_raises(auth_client, monkeypatch):
    """An exception from the inline run is reported as a user-facing __ERROR__ frame."""

    def exploding_run(*_args, **_kwargs):
        raise RuntimeError("LLM exploded")

    monkeypatch.setattr("janus.websocket.briefing.run_definition_inline", exploding_run)

    with auth_client.websocket_connect("/ws/briefing") as ws:
        messages = _collect_messages(ws)

    assert messages[0] == "__START__"
    assert messages[-1].startswith("__ERROR__:")
    assert "__END__" not in messages


def test_ws_briefing_reuses_todays_briefing_without_a_new_run(auth_client, db, monkeypatch):
    """Today's completed briefing run is shown as-is; the trigger does not run again."""
    definition_id = _briefing_definition_id(db)
    now = datetime.now(UTC).replace(tzinfo=None)
    _seed_completed_briefing_run(db, definition_id, "Older briefing", now - timedelta(days=2))
    _seed_completed_briefing_run(db, definition_id, "Today's briefing", now)
    fake_run = FakeInlineRun(output="Should not run")
    monkeypatch.setattr("janus.websocket.briefing.run_definition_inline", fake_run)

    with auth_client.websocket_connect("/ws/briefing") as ws:
        messages = _collect_messages(ws)

    assert messages == ["__START__", "Today's briefing", "__END__"]
    assert fake_run.calls == []


def test_ws_briefing_runs_again_when_only_an_older_briefing_exists(auth_client, db, monkeypatch):
    """A briefing from an earlier day is not today's briefing, so the trigger runs."""
    definition_id = _briefing_definition_id(db)
    _seed_completed_briefing_run(
        db, definition_id, "Older briefing", datetime.now(UTC).replace(tzinfo=None) - timedelta(days=2)
    )
    fake_run = FakeInlineRun(output="Fresh briefing")
    monkeypatch.setattr("janus.websocket.briefing.run_definition_inline", fake_run)

    with auth_client.websocket_connect("/ws/briefing") as ws:
        messages = _collect_messages(ws)

    assert messages == ["__START__", "Fresh briefing", "__END__"]
    assert fake_run.calls == [definition_id]


def test_ws_briefing_refresh_forces_a_new_run(auth_client, db, monkeypatch):
    """``?refresh=1`` runs the briefing trigger even when today's briefing exists."""
    definition_id = _briefing_definition_id(db)
    _seed_completed_briefing_run(db, definition_id, "Today's briefing", datetime.now(UTC).replace(tzinfo=None))
    fake_run = FakeInlineRun(output="Fresh briefing")
    monkeypatch.setattr("janus.websocket.briefing.run_definition_inline", fake_run)

    with auth_client.websocket_connect("/ws/briefing?refresh=1") as ws:
        messages = _collect_messages(ws)

    assert messages == ["__START__", "Fresh briefing", "__END__"]
    assert fake_run.calls == [definition_id]


def test_ws_briefing_emits_start_before_the_run_finishes(auth_client, monkeypatch):
    """The briefing socket sends __START__ before the (slow) briefing run completes."""
    release_run = threading.Event()
    fake_run = FakeInlineRun(output="Hello world")

    def blocking_run(session_factory, definition_id, **kwargs):
        release_run.wait(timeout=2)
        return fake_run(session_factory, definition_id, **kwargs)

    monkeypatch.setattr("janus.websocket.briefing.run_definition_inline", blocking_run)

    with auth_client.websocket_connect("/ws/briefing") as ws:
        assert ws.receive_text() == "__START__"
        assert fake_run.calls == []
        release_run.set()
        messages = _collect_messages(ws)

    assert messages == ["Hello world", "__END__"]


def test_ws_briefing_offloads_the_briefing_run_to_a_thread(auth_client, monkeypatch):
    """Loading or running the briefing is offloaded instead of blocking the websocket event loop."""
    offloaded_calls: list[str] = []

    async def fake_to_thread(func, *args, **kwargs):
        offloaded_calls.append(getattr(func, "__name__", func.__class__.__name__))
        return func(*args, **kwargs)

    monkeypatch.setattr("janus.websocket.briefing.asyncio.to_thread", fake_to_thread)
    monkeypatch.setattr("janus.websocket.briefing.run_definition_inline", FakeInlineRun(output="Hello world"))

    with auth_client.websocket_connect("/ws/briefing") as ws:
        messages = _collect_messages(ws)

    assert messages == ["__START__", "Hello world", "__END__"]
    assert offloaded_calls == ["_load_or_run_briefing"]


# ---------------------------------------------------------------------------
# Chat WebSocket tests
# ---------------------------------------------------------------------------


def test_ws_chat_rejects_unauthenticated(client):
    """An unauthenticated WebSocket connection to /ws/chat should be closed."""
    with client.websocket_connect("/ws/chat") as ws, pytest.raises(WebSocketDisconnect) as excinfo:
        ws.receive_text()

    assert excinfo.value.code == 1008


def test_ws_chat_emits_start_and_end(auth_client, db):
    """A chat response stream starts with __START__ and ends with __END__."""
    mock_llm = MagicMock()
    mock_llm.stream_agent_chat = MagicMock(return_value=_token_generator("Hi ", "there"))

    with patch("janus.main.llm_service.LLMService", return_value=mock_llm):  # noqa: SIM117
        with auth_client.websocket_connect("/ws/chat") as ws:
            ws.send_text("Hello Janus")
            messages = _collect_messages(ws)
            # Explicitly close so the server's receive loop gets WebSocketDisconnect
            # and terminates cleanly rather than blocking the test.
            ws.close()

    assert messages[0].startswith("__CONVERSATION_ID__:"), "First chat message must identify the conversation"
    assert messages[1].startswith("__TOOL_SELECTION__:"), "Tool selection must stream before the response starts"
    assert messages[2] == "__START__", "Chat stream must start after the tool-selection event"
    assert messages[-1] == "__END__", "Last chat message must be __END__"
    body = "".join(messages[3:-1])
    assert "Hi " in body
    assert "there" in body


def test_ws_chat_emits_error_signal_on_llm_failure(auth_client, db):
    """When the LLM raises during chat, the backend sends __ERROR__:<message> and closes."""

    async def _failing_chat_generator(*args, **kwargs):
        yield "partial chat"
        raise RuntimeError("chat LLM exploded")

    mock_llm = MagicMock()
    mock_llm.stream_agent_chat = MagicMock(return_value=_failing_chat_generator())

    with patch("janus.main.llm_service.LLMService", return_value=mock_llm):  # noqa: SIM117
        with auth_client.websocket_connect("/ws/chat") as ws:
            ws.send_text("Hello Janus")
            messages = _collect_messages(ws)

    assert "__START__" in messages
    assert any(m.startswith("__ERROR__:") for m in messages), "Expected __ERROR__:<message> in messages"
    # __END__ must NOT appear on an error path
    assert "__END__" not in messages
    # Partial output before the error should still be present
    body = "".join(messages)
    assert "partial chat" in body


def test_ws_chat_injects_skills_into_system_prompt(auth_client, db):
    """Every stored skill is injected into the system prompt -- skills are one flat library (JAN-201)."""
    skill1 = models.AgentSkill(
        name="Skill Alpha",
        description="First test skill",
        content="This is the content for skill alpha.",
    )
    skill2 = models.AgentSkill(
        name="Skill Beta",
        description="Second test skill",
        content="This is the content for skill beta.",
    )
    db.add_all([skill1, skill2])
    db.commit()
    db.refresh(skill1)
    db.refresh(skill2)

    captured_prompt = None

    async def _capture_prompt(*args, **kwargs):
        nonlocal captured_prompt
        captured_prompt = kwargs.get("system_prompt")
        if captured_prompt is None and args:
            captured_prompt = args[0]
        yield "Response"

    mock_llm = MagicMock()
    mock_llm.stream_agent_chat = MagicMock(side_effect=_capture_prompt)

    with patch("janus.main.llm_service.LLMService", return_value=mock_llm):  # noqa: SIM117
        with auth_client.websocket_connect("/ws/chat") as ws:
            ws.send_text("Hello Janus")
            _collect_messages(ws)
            ws.close()

    # Verify that the system prompt is character-sourced and indexes the skills (full text via read_skill)
    assert captured_prompt is not None, "System prompt should have been captured"
    assert "# Character constitution: Janus" in captured_prompt, "Character constitution must be present"
    assert "--- Tool Availability ---" in captured_prompt, "Tool availability section must be present"
    assert "--- Memory ---" in captured_prompt, "Memory guidance must be present"
    assert "# Agent Skills" in captured_prompt, "Skills section header must be present"
    assert "- **Skill Alpha**: First test skill" in captured_prompt, "First skill must be indexed"
    assert "- **Skill Beta**: Second test skill" in captured_prompt, "Second skill must be indexed"
    assert "This is the content for skill alpha." not in captured_prompt, "Skill text loads on demand"


def test_ws_chat_omits_skills_section_when_no_skills(auth_client, db):
    """When the skill library is empty, the skills section is omitted entirely."""
    captured_prompt = None

    async def _capture_prompt(*args, **kwargs):
        nonlocal captured_prompt
        captured_prompt = kwargs.get("system_prompt")
        if captured_prompt is None and args:
            captured_prompt = args[0]
        yield "Response"

    mock_llm = MagicMock()
    mock_llm.stream_agent_chat = MagicMock(side_effect=_capture_prompt)

    with patch("janus.main.llm_service.LLMService", return_value=mock_llm):  # noqa: SIM117
        with auth_client.websocket_connect("/ws/chat") as ws:
            ws.send_text("Hello Janus")
            _collect_messages(ws)
            ws.close()

    # Verify that the system prompt is just the character prompt (no skills section)
    assert captured_prompt is not None, "System prompt should have been captured"
    assert "# Character constitution: Janus" in captured_prompt, "Character constitution must be present"
    assert "--- Tool Availability ---" in captured_prompt, "Tool availability section must be present"
    assert "# Agent Skills" not in captured_prompt, "Skills section must not be present when the library is empty"


def test_ws_chat_forwards_tool_tokens_but_persists_only_assistant_text(auth_client, db):
    """Tool protocol tokens should stream to clients but not be stored in assistant messages."""
    mock_llm = MagicMock()
    mock_llm.stream_agent_chat = MagicMock(
        return_value=_token_generator(
            "Hello ",
            "__TOOL_CALL__:search_emails",
            "__TOOL_RESULT__:search_emails",
            "world",
        )
    )

    with patch("janus.main.llm_service.LLMService", return_value=mock_llm):  # noqa: SIM117
        with auth_client.websocket_connect("/ws/chat") as ws:
            ws.send_text("Summarize my urgent emails")
            messages = _collect_messages(ws)
            ws.close()

    assert "__TOOL_CALL__:search_emails" in messages
    assert "__TOOL_RESULT__:search_emails" in messages

    conversation_id = int(messages[0].split(":", 1)[1])
    assistant_record = (
        db.query(models.ConversationMessage)
        .filter(
            models.ConversationMessage.conversation_id == conversation_id,
            models.ConversationMessage.role == models.MessageRole.ASSISTANT,
        )
        .first()
    )
    assert assistant_record is not None
    assert assistant_record.content == "Hello world"
