"""Regression test for the loopback test client racing FastAPI's dependency teardown."""

import time

from conftest import _TestingSessionLocal

from janus import models, security
from janus.database import get_db
from janus.main import api


async def _slow_close_get_db():
    """``get_db`` whose teardown (after the response is sent) is slow enough to observe."""
    db = _TestingSessionLocal()
    try:
        yield db
    finally:
        time.sleep(0.2)
        db.close()


def test_requests_finish_before_the_test_continues(client, db, monkeypatch):
    """A request's late session close must not roll back the test thread's open transaction.

    FastAPI closes ``get_db``'s session after sending the response, and every test
    session shares one SQLite connection, so that ``ROLLBACK`` used to discard
    writes the test had flushed but not committed yet.
    """
    db.add(models.User(username="admin", hashed_password=security.security_service.get_password_hash("admin")))
    db.commit()
    monkeypatch.setitem(api.dependency_overrides, get_db, _slow_close_get_db)

    assert client.post("/api/auth/login", json={"username": "admin", "password": "admin"}).status_code == 200
    db.add(models.Rule(user_id=1, name="Probe", pattern="x", field="sender", priority=models.Priority.IMPORTANT))
    db.flush()
    time.sleep(0.4)  # the old race: the server's ROLLBACK landed here
    db.commit()

    db.expire_all()
    assert db.query(models.Rule).count() == 1


def test_closing_a_websocket_waits_for_its_handler(client, db, monkeypatch, testing_session_local):
    """Work a handler does after its last frame finishes before ``close()`` returns.

    ``/ws/chat`` commits the first message's title after ``__END__``; on the
    shared test connection that late commit used to interleave with the test
    thread's next write ("cannot commit - no transaction is active").
    """
    import asyncio

    from janus import database
    from janus import llm_service as llm_mod

    db.add(models.User(username="ws-user", hashed_password=security.security_service.get_password_hash("pw")))
    db.commit()
    response = client.post("/api/auth/login", json={"username": "ws-user", "password": "pw"})
    client.headers[security.CSRF_HEADER_NAME] = response.json()["csrf_token"]
    monkeypatch.setattr(database, "SessionLocal", testing_session_local)
    monkeypatch.setattr(llm_mod.LLMService, "__init__", lambda self, **_kw: None)
    monkeypatch.setattr(llm_mod.LLMService, "suggest_memory_fact", lambda self, *a, **k: None)

    async def reply(self, system_prompt, tools, history, user_msg):
        yield "Hi"

    async def slow_title(self, msg):
        await asyncio.sleep(0.4)
        return "Late title"

    monkeypatch.setattr(llm_mod.LLMService, "stream_agent_chat", reply)
    monkeypatch.setattr(llm_mod.LLMService, "generate_conversation_title", slow_title)

    with client.websocket_connect("/ws/chat") as ws:
        ws.send_text("Hello")
        while ws.receive_text() != "__END__":
            pass

    db.expire_all()
    assert [conversation.title for conversation in db.query(models.Conversation).all()] == ["Late title"]
