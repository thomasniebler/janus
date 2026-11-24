"""Sending WhatsApp messages from Janus (JAN-360)."""

from __future__ import annotations

from datetime import datetime

import pytest

from janus import agent_tools, models, security
from janus.agent_toolboxes.shared import ToolContext
from janus.agent_toolboxes.whatsapp import build_whatsapp_send_tools
from janus.runtime_adapter import TASK_MODE_ALLOWED_TOOL_IDS
from janus.services import whatsapp_service
from janus.services.whatsapp_service import WhatsAppService, phone_to_jid
from janus.workers import whatsapp as whatsapp_worker

SENT_AT = datetime(2026, 11, 10, 9, 30, 0)


def _user(db, username="sender") -> models.User:
    user = models.User(username=username, hashed_password=security.security_service.get_password_hash("pw"))
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _account(db, user, *, name="Phone", enabled=True) -> models.WhatsAppAccount:
    account = models.WhatsAppAccount(user_id=user.id, display_name=name, is_enabled=enabled)
    db.add(account)
    db.commit()
    return account


def _conversation(db, account, chat_id="4917000000@s.whatsapp.net", title="Anna") -> models.WhatsAppConversation:
    conversation = models.WhatsAppConversation(
        user_id=account.user_id, whatsapp_account_id=account.id, provider_chat_id=chat_id, title=title
    )
    db.add(conversation)
    db.commit()
    return conversation


@pytest.fixture
def sent(monkeypatch):
    """Capture sends instead of reaching a live Neonize connection."""
    calls: list[tuple[int, str, str]] = []

    def fake_send(account_id, chat_jid, text):
        calls.append((account_id, chat_jid, text))
        return f"MSG{len(calls)}", SENT_AT

    monkeypatch.setattr(whatsapp_worker, "send_text_message", fake_send)
    return calls


def test_registry_entry_asks_before_every_call():
    """``whatsapp.send`` is a required-confirmation write entry, offered in chat."""
    entry = next(entry for entry in agent_tools.DEFAULT_TOOL_REGISTRY.entries() if entry.id == "whatsapp.send")
    assert (entry.confirmation, entry.operation) == ("required", "write")
    assert "whatsapp.send" in TASK_MODE_ALLOWED_TOOL_IDS["chat"]


@pytest.mark.parametrize(
    ("raw", "jid"),
    [("+49 151 1234-5678", "4915112345678@s.whatsapp.net"), ("004915112345678", "4915112345678@s.whatsapp.net")],
)
def test_phone_to_jid_accepts_international_numbers(raw, jid):
    """International numbers become direct-chat JIDs."""
    assert phone_to_jid(raw) == jid


@pytest.mark.parametrize("raw", ["015112345678", "+49abc", "123", ""])
def test_phone_to_jid_rejects_national_or_malformed_numbers(raw):
    """National or malformed numbers are refused."""
    with pytest.raises(ValueError):
        phone_to_jid(raw)


def test_send_into_existing_conversation_stores_outgoing_message(db, sent):
    """A send into a stored conversation goes to its chat and is stored as outgoing."""
    user = _user(db)
    account = _account(db, user)
    conversation = _conversation(db, account)
    [tool] = build_whatsapp_send_tools(ToolContext(db=db, user=user))

    result = tool.invoke({"text": "On my way", "conversation_id": conversation.id})

    assert sent == [(account.id, "4917000000@s.whatsapp.net", "On my way")]
    assert result["sent"] is True and result["conversation_id"] == conversation.id
    message = db.query(models.WhatsAppMessage).one()
    assert (message.direction, message.body, message.provider_message_id) == (
        models.WhatsAppMessageDirection.OUTGOING,
        "On my way",
        "MSG1",
    )
    db.refresh(conversation)
    assert conversation.latest_message_snippet == "On my way"


def test_send_to_new_number_creates_a_direct_conversation(db, sent):
    """A phone number without a stored chat gets a new direct conversation."""
    user = _user(db)
    account = _account(db, user)
    conversation, _ = WhatsAppService(db).send_text(user, "Hello", phone_number="+4915112345678")
    assert sent == [(account.id, "4915112345678@s.whatsapp.net", "Hello")]
    assert (conversation.title, conversation.conversation_type) == (
        "+4915112345678",
        models.WhatsAppConversationType.DIRECT,
    )


def test_send_refuses_foreign_disabled_or_ambiguous_targets(db, sent):
    """Another user's conversation, a disabled account, and an ambiguous account are refused."""
    owner, other = _user(db, "owner"), _user(db, "other")
    foreign = _conversation(db, _account(db, other))
    service = WhatsAppService(db)
    with pytest.raises(ValueError, match="not found"):
        service.send_text(owner, "hi", conversation_id=foreign.id)
    disabled = _conversation(db, _account(db, owner, enabled=False), chat_id="1@s.whatsapp.net")
    with pytest.raises(ValueError, match="not found"):
        service.send_text(owner, "hi", conversation_id=disabled.id)
    with pytest.raises(ValueError, match="no enabled"):
        service.send_text(owner, "hi", phone_number="+4915112345678")
    _account(db, owner, name="A")
    second = _account(db, owner, name="B")
    with pytest.raises(ValueError, match="pass account_id"):
        service.send_text(owner, "hi", phone_number="+4915112345678")
    service.send_text(owner, "hi", phone_number="+4915112345678", account_id=second.id)
    with pytest.raises(ValueError, match="exactly one"):
        service.send_text(owner, "hi")
    with pytest.raises(ValueError, match="empty"):
        service.send_text(owner, "  ", phone_number="+4915112345678")
    assert len(sent) == 1


def test_send_without_live_connection_is_refused(db, monkeypatch):
    """No live connector for the account answers a clear error and stores nothing."""
    monkeypatch.setattr(whatsapp_worker, "_live_connectors", {})
    user = _user(db)
    conversation = _conversation(db, _account(db, user))
    with pytest.raises(whatsapp_worker.WhatsAppNotConnectedError):
        WhatsAppService(db).send_text(user, "hi", conversation_id=conversation.id)
    assert db.query(models.WhatsAppMessage).count() == 0
    assert whatsapp_service.MAX_SEND_CHARS == 4096
