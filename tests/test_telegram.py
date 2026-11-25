"""Tests for the Telegram bot connector (JAN-405). A fake Bot API; no network."""

from __future__ import annotations

import httpx
import pytest

from janus import models, security
from janus import trigger_definition_service as definitions
from janus.agent_toolboxes import telegram as telegram_toolbox
from janus.agent_tools import DEFAULT_TOOL_REGISTRY
from janus.capability_profile import user_connected_capabilities
from janus.services import telegram_service
from janus.trigger_events import TriggerEvent

TOKEN = "123456:ABCdef_ghi-JKL"


class FakeBotApi:
    """Answers Bot API methods; records every call."""

    def __init__(self):
        """Start with no queued updates."""
        self.calls: list[tuple[str, dict]] = []
        self.updates: list[dict] = []

    def __call__(self, token, method, payload=None, *, timeout=15.0):
        """Dispatch one method call."""
        self.calls.append((method, payload or {}))
        if token != TOKEN:
            raise telegram_service.TelegramError("Telegram rejected the bot token")
        if method == "getMe":
            return {"id": 1, "is_bot": True, "username": "janus_test_bot"}
        if method == "getUpdates":
            offset = (payload or {}).get("offset", 0)
            return [u for u in self.updates if u["update_id"] >= offset]
        if method == "sendMessage":
            return {"message_id": 99}
        raise AssertionError(method)

    def sent(self):
        """Texts sent with sendMessage."""
        return [(p["chat_id"], p["text"]) for m, p in self.calls if m == "sendMessage"]


@pytest.fixture
def fake(monkeypatch):
    """Route the service's Bot API calls to a fake."""
    api = FakeBotApi()
    monkeypatch.setattr(telegram_service, "_api", api)
    return api


def _user(db, name="tg-user") -> models.User:
    user = models.User(username=name, hashed_password=security.security_service.get_password_hash("secret"))
    db.add(user)
    db.commit()
    return user


def _message(update_id, chat_id, text, chat_type="private"):
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id * 10,
            "date": 1764100000,
            "chat": {"id": chat_id, "type": chat_type},
            "from": {"first_name": "Thomas"},
            "text": text,
        },
    }


def _linked_bot(db, fake, user, chat_id=4242):
    bot = telegram_service.connect(db, user, TOKEN)
    fake.updates = [_message(1, chat_id, f"/start {bot.pairing_code}")]
    telegram_service.poll_once(db, bot, timeout=0)
    return bot


def test_connect_checks_the_token_and_stores_it_encrypted(db, fake):
    """The token is verified with getMe, stored encrypted, and a pairing code is issued."""
    bot = telegram_service.connect(db, _user(db), f"  {TOKEN} ")
    assert bot.bot_username == "janus_test_bot"
    assert TOKEN not in bot.encrypted_token and telegram_service.token_of(bot) == TOKEN
    assert bot.pairing_code and len(bot.pairing_code) == 8 and bot.chat_id is None


@pytest.mark.parametrize("token", ["not-a-token", "123:bad token!", "x" * 120])
def test_malformed_tokens_are_refused_before_calling_telegram(db, fake, token):
    """Obviously wrong tokens never reach Telegram."""
    with pytest.raises(telegram_service.TelegramError):
        telegram_service.connect(db, _user(db), token)
    assert fake.calls == []


def test_pairing_links_only_the_private_chat_with_the_code(db, fake):
    """Strangers and wrong codes are ignored; the right /start links the chat and confirms."""
    bot = telegram_service.connect(db, _user(db), TOKEN)
    code = bot.pairing_code
    fake.updates = [
        _message(1, 111, "/start 00000000"),
        _message(2, 222, f"/start {code}", chat_type="group"),
        _message(3, 333, f"/start {code}"),
    ]
    assert telegram_service.poll_once(db, bot, timeout=0) == 3
    assert bot.chat_id == "333" and bot.pairing_code is None and bot.last_update_id == 3
    assert fake.sent() == [("333", "Linked to Janus. Messages you send here can start your Telegram triggers.")]


def test_messages_from_the_linked_chat_start_telegram_triggers(db, fake):
    """A text from the linked chat emits telegram.message_received; others are ignored."""
    user = _user(db)
    definitions.create_definition(
        db,
        user.id,
        name="On Telegram",
        objective="Reply.",
        tool_ids=["core"],
        kind="event",
        event_type=TriggerEvent.TELEGRAM_MESSAGE_RECEIVED.value,
    )
    bot = _linked_bot(db, fake, user)
    fake.updates = [_message(2, 999, "hello from a stranger"), _message(3, 4242, "remind me about the dentist")]
    telegram_service.poll_once(db, bot, timeout=0)
    (run,) = db.query(models.RuntimeTrigger).filter(models.RuntimeTrigger.user_id == user.id).all()
    event = run.payload["event"]
    assert event["type"] == "telegram.message_received"
    assert event["body"] == "remind me about the dentist" and event["sender"] == "Thomas"
    # The offset moves on, so the same update is not handled twice.
    assert telegram_service.poll_once(db, bot, timeout=0) == 0


def test_send_to_owner_writes_only_to_the_linked_chat_within_the_cap(db, fake, monkeypatch):
    """Messages go to the linked chat only, and the daily cap stops them."""
    monkeypatch.setattr(telegram_service, "MAX_SENT_PER_DAY", 2)
    user = _user(db)
    _linked_bot(db, fake, user)
    telegram_service.send_to_owner(db, user.id, "one")
    telegram_service.send_to_owner(db, user.id, "two")
    with pytest.raises(telegram_service.TelegramError, match="daily limit"):
        telegram_service.send_to_owner(db, user.id, "three")
    assert [text for chat, text in fake.sent() if chat == "4242"][-2:] == ["one", "two"]


def test_unlinked_bot_cannot_send(db, fake):
    """Without a linked chat nothing is sent."""
    user = _user(db)
    telegram_service.connect(db, user, TOKEN)
    with pytest.raises(telegram_service.TelegramError, match="no linked"):
        telegram_service.send_to_owner(db, user.id, "hi")


def test_api_maps_errors(monkeypatch):
    """Bot API failures become TelegramError with a safe message."""

    def reply(status, body):
        return lambda *args, **kwargs: httpx.Response(status, json=body)

    monkeypatch.setattr(telegram_service.httpx, "post", reply(401, {"ok": False, "description": "Unauthorized"}))
    with pytest.raises(telegram_service.TelegramError, match="rejected the bot token"):
        telegram_service._api(TOKEN, "getMe")
    monkeypatch.setattr(telegram_service.httpx, "post", reply(200, {"ok": True, "result": {"id": 1}}))
    assert telegram_service._api(TOKEN, "getMe") == {"id": 1}


# --- capability and tool ---------------------------------------------------------------------


def test_capability_and_notify_tool_need_a_linked_chat_and_an_unattended_run(db, fake):
    """'telegram' is granted only once the chat is linked; the alert tool is unattended-only."""
    user = _user(db)
    telegram_service.connect(db, user, TOKEN)
    db.commit()
    assert "telegram" not in user_connected_capabilities(db, user)
    _linked_bot(db, fake, user)
    db.commit()
    assert "telegram" in user_connected_capabilities(db, user)
    entry = {e.id: e for e in DEFAULT_TOOL_REGISTRY.entries()}["telegram.notify"]
    assert (entry.operation, entry.confirmation, entry.required_capabilities) == ("write", "recommended", ("telegram",))


def test_notify_tool_refuses_links_and_sends_plain_text(db, fake):
    """The tool refuses URLs and long messages, and sends plain text to the owner."""
    from types import SimpleNamespace

    user = _user(db)
    _linked_bot(db, fake, user)
    context = SimpleNamespace(db=db, user=user, turn_trace=None)
    (tool,) = telegram_toolbox.build_telegram_notify_tools(context)
    assert tool.invoke({"message": "See https://evil.example"}).startswith("ERROR:")
    assert tool.invoke({"message": "x" * 700}).startswith("ERROR:")
    assert tool.invoke({"message": "The build failed twice."}) == "Sent to the owner's Telegram."
    assert fake.sent()[-1] == ("4242", "The build failed twice.")


def test_notify_tool_is_blocked_after_a_suspected_injection(db, fake):
    """After a suspected prompt injection in the step, nothing is sent."""
    from types import SimpleNamespace

    user = _user(db)
    _linked_bot(db, fake, user)
    trace = SimpleNamespace(injection_suspected=lambda: True)
    (tool,) = telegram_toolbox.build_telegram_notify_tools(SimpleNamespace(db=db, user=user, turn_trace=trace))
    before = len(fake.sent())
    assert tool.invoke({"message": "hi"}).startswith("BLOCKED:")
    assert len(fake.sent()) == before


# --- API -------------------------------------------------------------------------------------


@pytest.fixture
def auth_client(client, db, monkeypatch):
    """A cookie-authenticated client; worker start/stop are no-ops."""
    from janus.api import telegram as telegram_api

    monkeypatch.setattr(telegram_api, "start_bot_worker", lambda user_id: None)
    monkeypatch.setattr(telegram_api, "stop_bot_worker", lambda user_id: None)
    _user(db, "tg-api-user")
    response = client.post("/api/auth/login", json={"username": "tg-api-user", "password": "secret"})
    client.headers[security.CSRF_HEADER_NAME] = response.json()["csrf_token"]
    return client


def test_api_connect_status_and_disconnect_never_return_the_token(auth_client, fake):
    """Connect shows the pairing code; the token is never returned; disconnect forgets it."""
    assert auth_client.get("/api/telegram").json()["connected"] is False
    connected = auth_client.post("/api/telegram/connect", json={"token": TOKEN})
    assert connected.status_code == 200, connected.text
    body = connected.json()
    assert body["bot_username"] == "janus_test_bot" and body["linked"] is False and body["pairing_code"]
    assert TOKEN not in connected.text and TOKEN not in auth_client.get("/api/telegram").text
    assert auth_client.post("/api/telegram/test").status_code == 422  # not linked yet
    assert auth_client.delete("/api/telegram").status_code == 204
    assert auth_client.get("/api/telegram").json()["connected"] is False


def test_api_rejects_a_bad_token(auth_client, fake):
    """A token Telegram rejects is a 422 and nothing is stored."""
    response = auth_client.post("/api/telegram/connect", json={"token": "999:WRONG"})
    assert response.status_code == 422
    assert auth_client.get("/api/telegram").json()["connected"] is False
