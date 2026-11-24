"""Regression tests for the chat-turn SQLite write lock race (JAN-189, finding 2).

``ws_chat`` holds one long-lived session for the whole connection, while
tools such as ``add_memory_fact`` write through ``run_isolated_tool_mutation``
on a *second* session bound to the same engine. On an on-disk SQLite file the
first session's open write transaction (e.g. a flushed-but-uncommitted
``Conversation``) holds the RESERVED lock, so the tool's write can only wait
out the busy timeout and then fail with "database is locked".

The shared in-memory ``StaticPool`` engine used elsewhere cannot expose this,
because every session shares one connection. These tests use a file-backed
engine with a deliberately short busy timeout so a regression fails in well
under a second instead of hiding behind sqlite3's 5s default retry window.
"""

import asyncio
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker
from starlette.websockets import WebSocketDisconnect

from janus import database, models
from janus.agent_toolboxes.shared import ToolContext, run_isolated_tool_mutation
from janus.services.memory_service import MemoryService
from janus.websocket import chat as chat_module

_BUSY_TIMEOUT_SECONDS = 0.2


@pytest.fixture
def file_engine(tmp_path):
    """Yield an on-disk SQLite engine with a short busy timeout and the app schema."""
    engine = create_engine(
        f"sqlite:///{tmp_path / 'chat-lock.db'}",
        connect_args={"check_same_thread": False, "timeout": _BUSY_TIMEOUT_SECONDS},
    )
    database.Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def file_sessions(file_engine):
    """Return a session factory for the file-backed engine, configured like ``SessionLocal``."""
    return sessionmaker(bind=file_engine, autocommit=False, autoflush=False)


@pytest.fixture
def chat_user(file_sessions):
    """Persist a chat user in the file-backed database and return it detached."""
    with file_sessions() as session:
        user = models.User(username="lock-race", hashed_password="x", is_admin=False)
        session.add(user)
        session.commit()
        session.refresh(user)
        session.expunge(user)
    return user


def _add_fact(context: ToolContext, user_id: int, summary: str):
    return run_isolated_tool_mutation(
        context,
        MemoryService,
        lambda service: service.create_fact(user_id=user_id, summary=summary),
    )


def test_isolated_mutation_is_locked_out_by_an_open_chat_write_transaction(file_sessions, chat_user):
    """Documents the hazard: an uncommitted Conversation blocks the isolated tool write."""
    chat_db = file_sessions()
    try:
        chat_db.add(models.Conversation(user_id=chat_user.id))
        chat_db.flush()  # open write transaction, as ws_chat did before the fix
        context = ToolContext(db=chat_db, user=chat_user)

        with pytest.raises(OperationalError, match="database is locked"):
            _add_fact(context, chat_user.id, "blocked while the chat transaction is open")

        chat_db.commit()  # releasing the chat session's write lock unblocks the tool
        fact = _add_fact(context, chat_user.id, "written after the chat commit")
        assert fact.id is not None
    finally:
        chat_db.close()


class _FakeWebSocket:
    """Minimal WebSocket stand-in: one inbound message, then a disconnect."""

    def __init__(self, messages: list[str]):
        self._inbound = list(messages)
        self.sent: list[str] = []

    async def accept(self) -> None:
        return None

    async def receive_text(self) -> str:
        if self._inbound:
            return self._inbound.pop(0)
        raise WebSocketDisconnect()

    async def send_text(self, text: str) -> None:
        self.sent.append(text)

    async def close(self, code: int = 1000) -> None:
        return None


def test_first_chat_turn_tool_write_does_not_hit_database_lock(monkeypatch, file_sessions, chat_user):
    """A memory tool call on the first turn of a new conversation must commit, not lock."""
    monkeypatch.setattr(database, "SessionLocal", file_sessions)
    tool_results: list[object] = []

    async def fake_stream_agent_chat(_system_prompt, tools, _history, _user_msg):
        add_memory_fact = next(tool for tool in tools if tool.name == "add_memory_fact")
        # The tool runs synchronously inside the turn, while ws_chat's own
        # session is still open -- exactly where the lock race would bite.
        tool_results.append(add_memory_fact.invoke({"summary": "Prefers tea over coffee"}))
        yield "Noted."

    fake_llm = MagicMock()
    fake_llm.stream_agent_chat = fake_stream_agent_chat
    fake_llm.suggest_memory_fact = MagicMock(return_value=None)

    async def fake_title(_user_msg):
        return "Tea"

    fake_llm.generate_conversation_title = fake_title
    monkeypatch.setattr(chat_module.llm_service, "build_llm_service", lambda *_args, **_kwargs: fake_llm)

    websocket = _FakeWebSocket(["Remember that I prefer tea."])
    asyncio.run(chat_module.ws_chat(websocket, conversation_id=None, user=chat_user))

    assert not any(message.startswith("__ERROR__") for message in websocket.sent), websocket.sent
    assert len(tool_results) == 1
    assert "locked" not in str(tool_results[0]).lower(), tool_results[0]

    with file_sessions() as session:
        facts = session.query(models.AgentMemoryFact).filter_by(user_id=chat_user.id).all()
        assert [fact.summary for fact in facts] == ["Prefers tea over coffee"]
        conversation = session.query(models.Conversation).filter_by(user_id=chat_user.id).one()
        stored = (
            session.query(models.ConversationMessage)
            .filter_by(conversation_id=conversation.id)
            .order_by(models.ConversationMessage.id)
            .all()
        )
        assert [message.role for message in stored] == [models.MessageRole.USER, models.MessageRole.ASSISTANT]
