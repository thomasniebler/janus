"""WebSocket handlers must not hold a pooled DB connection per socket for its lifetime (JAN-336).

The e2e backend logged SQLAlchemy ``QueuePool limit ... reached`` under load. The cause was
the WebSocket auth dependency: ``security.get_user_or_none`` took its session from
``Depends(database.get_db)``, and FastAPI tears a dependency down only when the endpoint
returns -- for a socket, when it closes. Every open ``/ws/chat`` socket (one per tab, open on
every page) therefore pinned the handshake's connection next to its handler's own session,
and ``/ws/briefing`` pinned one while the briefing ran. The chat handler's own session also kept
its connection while the socket sat idle after its setup reads; it now ends that transaction
before waiting for each message, so an idle socket holds no connection at all.

These tests run the real app on a loopback server against a file-backed SQLite database with
a real, small ``QueuePool`` (the in-memory ``StaticPool`` other tests use cannot show it).
"""

import asyncio
import json
import threading
import time
from unittest.mock import MagicMock, patch

import pytest
from asgi_client import SyncASGIClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import QueuePool

from janus import config, database, models, security
from janus.database import Base, get_db
from janus.main import api

POOL_SIZE = 3


@pytest.fixture
def pooled_engine(tmp_path, monkeypatch):
    """A file-backed SQLite engine with a small QueuePool, wired into the app."""
    engine = create_engine(
        f"sqlite:///{tmp_path / 'pool.db'}",
        connect_args={"check_same_thread": False},
        poolclass=QueuePool,
        pool_size=POOL_SIZE,
        max_overflow=0,
        pool_timeout=3,
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    monkeypatch.setitem(api.dependency_overrides, get_db, override_get_db)
    monkeypatch.setattr(database, "SessionLocal", session_factory)
    monkeypatch.setattr(database, "ensure_schema_up_to_date", lambda database_url=None: None)

    with session_factory() as db:
        db.add(
            models.User(
                username="pool-user",
                hashed_password=security.security_service.get_password_hash("pw"),
                is_admin=False,
            )
        )
        db.commit()
    yield engine
    engine.dispose()


@pytest.fixture
def pooled_client(pooled_engine, monkeypatch):
    """A logged-in loopback client backed by ``pooled_engine``."""
    monkeypatch.setattr(config.settings, "base_url", "http://localhost:8080")
    monkeypatch.setattr(config.settings, "allowed_hosts", ["testserver"])
    monkeypatch.setattr(config.settings, "allowed_origins", ["http://testserver"])
    with SyncASGIClient(api) as client:
        client.headers["origin"] = "http://testserver"
        response = client.post("/api/auth/login", json={"username": "pool-user", "password": "pw"})
        assert response.status_code == 200
        client.headers[security.CSRF_HEADER_NAME] = response.json()["csrf_token"]
        client.access_token = response.json()["access_token"]
        yield client


def _wait_for_checkedout(engine, expected: int, timeout: float = 10.0) -> int:
    """Poll until the pool's checked-out count reaches ``expected`` (server teardown is async)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if engine.pool.checkedout() == expected:
            return expected
        time.sleep(0.02)
    return engine.pool.checkedout()


def _collect_until_end(ws) -> list[str]:
    messages: list[str] = []
    while True:
        message = ws.receive_text(timeout=15)
        messages.append(message)
        if message == "__END__" or message.startswith("__ERROR__"):
            return messages


def test_websocket_auth_dependency_releases_its_connection(pooled_client, pooled_engine):
    """The WebSocket user dependency closes its session and hands back a detached user."""
    assert _wait_for_checkedout(pooled_engine, 0) == 0
    token = security.AuthToken(token=pooled_client.access_token, source="cookie")

    resolved = asyncio.run(security.get_websocket_user_or_none(token=token))

    assert resolved is not None
    assert resolved.username == "pool-user"
    assert pooled_engine.pool.checkedout() == 0


def test_briefing_socket_holds_no_connection_while_the_briefing_runs(pooled_client, pooled_engine, monkeypatch):
    """While ``/ws/briefing`` waits for the run, the socket itself holds no pooled connection."""
    seen: list[int] = []

    def fake_briefing(user_id, refresh, timezone=None):
        seen.append(pooled_engine.pool.checkedout())
        return "Briefing text", True

    monkeypatch.setattr("janus.websocket.briefing._load_or_run_briefing", fake_briefing)

    with pooled_client.websocket_connect("/ws/briefing") as ws:
        assert _collect_until_end(ws) == ["__START__", "Briefing text", "__END__"]

    assert seen == [0]
    assert _wait_for_checkedout(pooled_engine, 0) == 0


def test_open_chat_sockets_use_one_connection_each_and_release_them(pooled_client, pooled_engine):
    """As many chat sockets as the pool has connections can be open and answer at once.

    Before JAN-336 each open ``/ws/chat`` socket held two connections (the handshake's and the
    handler's), so the third of three sockets ran out of pool and its turn failed with a
    QueuePool timeout. Afterwards every connection goes back to the pool.
    """

    def make_llm(*_args, **_kwargs):
        async def stream(*_a, **_kw):
            yield "Hi there"

        llm = MagicMock()
        llm.stream_agent_chat = MagicMock(side_effect=lambda *a, **kw: stream())
        return llm

    with patch("janus.main.llm_service.LLMService", side_effect=make_llm):
        sockets = [pooled_client.websocket_connect("/ws/chat") for _ in range(POOL_SIZE)]
        try:
            replies: dict[int, list[str]] = {}

            def talk(index: int, ws) -> None:
                ws.send_text(f"Hello from tab {index}")
                replies[index] = _collect_until_end(ws)

            threads = [threading.Thread(target=talk, args=(index, ws)) for index, ws in enumerate(sockets)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=30)

            for index in range(POOL_SIZE):
                assert replies.get(index, [""])[-1] == "__END__", json.dumps(replies.get(index))
        finally:
            for ws in sockets:
                ws.close()

    assert _wait_for_checkedout(pooled_engine, 0) == 0


def _scripted_llm(*_args, **_kwargs):
    async def stream(*_a, **_kw):
        yield "Hi there"

    llm = MagicMock()
    llm.stream_agent_chat = MagicMock(side_effect=lambda *a, **kw: stream())
    return llm


def test_idle_chat_sockets_hold_no_connection_however_many_are_open(pooled_client, pooled_engine):
    """Twice as many idle chat sockets as the pool has connections stay open and all answer.

    The checked-out count stays bounded by the turns in flight, not by the open sockets: with
    every socket idle (before and after its turn) nothing is checked out.
    """
    count = 2 * POOL_SIZE
    with patch("janus.main.llm_service.LLMService", side_effect=_scripted_llm):
        sockets = []
        try:
            for _ in range(count):
                sockets.append(pooled_client.websocket_connect("/ws/chat"))
            # Each socket finished its setup reads (its first turn below proves the
            # handler is in its receive loop) and then holds nothing while idle.
            for index, ws in enumerate(sockets):
                ws.send_text(f"Hello from tab {index}")
                assert _collect_until_end(ws)[-1] == "__END__"
                assert _wait_for_checkedout(pooled_engine, 0) == 0, f"after turn {index}"
            # A second round on the oldest sockets still works with all of them open.
            for ws in sockets[:POOL_SIZE]:
                ws.send_text("And again")
                assert _collect_until_end(ws)[-1] == "__END__"
            assert _wait_for_checkedout(pooled_engine, 0) == 0
        finally:
            for ws in sockets:
                ws.close()

    assert _wait_for_checkedout(pooled_engine, 0) == 0


def test_open_changes_sockets_hold_no_connection(pooled_client, pooled_engine):
    """``/ws/changes`` sockets, open for a tab's whole life, hold no pooled connection."""
    count = 2 * POOL_SIZE
    sockets = []
    try:
        for _ in range(count):
            ws = pooled_client.websocket_connect("/ws/changes")
            sockets.append(ws)
            assert json.loads(ws.receive_text(timeout=10)) == {"type": "ready"}
        assert _wait_for_checkedout(pooled_engine, 0) == 0
        # The REST API still has the whole pool, and a write reaches every socket.
        assert pooled_client.post("/api/memory/", json={"summary": "Pool check"}).status_code == 201
        for ws in sockets:
            assert json.loads(ws.receive_text(timeout=10)) == {"type": "changed", "domains": ["memory"]}
    finally:
        for ws in sockets:
            ws.close()
    assert _wait_for_checkedout(pooled_engine, 0) == 0
