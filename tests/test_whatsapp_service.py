"""Tests for WhatsApp account CRUD, upsert helpers, and setup-session lifecycle."""

from datetime import UTC, datetime, timedelta

import pytest

from janus import models
from janus.services.whatsapp_service import WhatsAppService
from janus.services.whatsapp_setup_service import SETUP_SESSION_TTL, WhatsAppSetupService
from janus.whatsapp_neonize import (
    NeonizeAdapter,
    NeonizeConversationPayload,
    NeonizeMessagePayload,
    NeonizePairingCapability,
    NeonizePairingSnapshot,
)


def _make_user(db) -> models.User:
    user = models.User(username="owner", hashed_password="hash", is_admin=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _make_account(db, user: models.User, **overrides) -> models.WhatsAppAccount:
    account = models.WhatsAppAccount(
        user_id=user.id,
        display_name=overrides.pop("display_name", "Personal WhatsApp"),
        provider="whatsapp_neonize",
        session_database_path=overrides.pop("session_database_path", "/tmp/neonize.db"),
        connection_status=overrides.pop("connection_status", models.WhatsAppConnectionStatus.DISCONNECTED),
        **overrides,
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


def test_upsert_conversation_creates_and_updates_by_provider_chat_id(db):
    """Upserting a conversation twice for the same provider chat id should update in place."""
    user = _make_user(db)
    account = _make_account(db, user)
    service = WhatsAppService(db)

    payload = NeonizeConversationPayload(
        provider_chat_id="chat-1",
        title="Alice",
        conversation_type=models.WhatsAppConversationType.DIRECT,
        unread_count=2,
    )
    conversation = service.upsert_conversation(user, account, payload)
    db.commit()

    assert db.query(models.WhatsAppConversation).count() == 1
    assert conversation.title == "Alice"
    assert conversation.unread_count == 2

    updated_payload = NeonizeConversationPayload(
        provider_chat_id="chat-1",
        title="Alice Updated",
        conversation_type=models.WhatsAppConversationType.DIRECT,
        unread_count=0,
    )
    same_conversation = service.upsert_conversation(user, account, updated_payload)
    db.commit()

    assert db.query(models.WhatsAppConversation).count() == 1
    assert same_conversation.id == conversation.id
    assert same_conversation.title == "Alice Updated"
    assert same_conversation.unread_count == 0


def test_upsert_message_dedupes_by_provider_message_id_and_updates_conversation_preview(db):
    """Upserting the same provider message id twice should update in place, not duplicate."""
    user = _make_user(db)
    account = _make_account(db, user)
    service = WhatsAppService(db)

    conversation = service.upsert_conversation(
        user,
        account,
        NeonizeConversationPayload(
            provider_chat_id="chat-1",
            title="Alice",
            conversation_type=models.WhatsAppConversationType.DIRECT,
        ),
    )
    db.commit()

    first_timestamp = datetime.now(UTC) - timedelta(minutes=5)
    service.upsert_message(
        user,
        account,
        conversation.id,
        NeonizeMessagePayload(
            provider_message_id="msg-1",
            provider_chat_id="chat-1",
            provider_timestamp=first_timestamp,
            direction=models.WhatsAppMessageDirection.INCOMING,
            sender_identifier="alice@s.whatsapp.net",
            sender_display_name="Alice",
            body="Hi",
            snippet="Hi",
        ),
    )
    db.commit()

    assert db.query(models.WhatsAppMessage).count() == 1
    db.refresh(conversation)
    assert conversation.latest_message_snippet == "Hi"

    second_timestamp = datetime.now(UTC)
    service.upsert_message(
        user,
        account,
        conversation.id,
        NeonizeMessagePayload(
            provider_message_id="msg-1",
            provider_chat_id="chat-1",
            provider_timestamp=second_timestamp,
            direction=models.WhatsAppMessageDirection.INCOMING,
            sender_identifier="alice@s.whatsapp.net",
            sender_display_name="Alice",
            body="Hi edited",
            snippet="Hi edited",
        ),
    )
    db.commit()

    assert db.query(models.WhatsAppMessage).count() == 1
    message = db.query(models.WhatsAppMessage).filter(models.WhatsAppMessage.provider_message_id == "msg-1").one()
    assert message.body == "Hi edited"
    db.refresh(conversation)
    assert conversation.latest_message_snippet == "Hi edited"


def test_create_account_starts_connector_when_enabled_with_session(db, monkeypatch):
    """Creating an account through the API directly should start its connector immediately."""
    user = _make_user(db)

    started: list[int] = []
    monkeypatch.setattr("janus.workers.whatsapp.start_account_connector", lambda acct: started.append(acct.id))

    account = WhatsAppService(db).create_account(
        user,
        {
            "display_name": "Personal WhatsApp",
            "session_database_path": "/tmp/neonize.db",
            "is_enabled": True,
        },
    )

    assert started == [account.id]


def test_create_account_does_not_start_connector_without_session(db, monkeypatch):
    """Creating an enabled account with no paired session yet should not start a connector."""
    user = _make_user(db)

    started: list[int] = []
    monkeypatch.setattr("janus.workers.whatsapp.start_account_connector", lambda acct: started.append(acct.id))

    WhatsAppService(db).create_account(user, {"display_name": "Personal WhatsApp", "is_enabled": True})

    assert started == []


def test_restart_connector_requires_configured_session(db):
    """Restarting the connector should refuse when no session material is configured."""
    user = _make_user(db)
    account = _make_account(db, user, session_database_path=None)

    with pytest.raises(RuntimeError, match="No Neonize session is configured"):
        WhatsAppService(db).restart_connector(account)


def test_restart_connector_stops_and_starts_the_worker(db, monkeypatch):
    """Restarting should stop then start the per-account connector worker."""
    user = _make_user(db)
    account = _make_account(db, user)

    calls: list[tuple[str, int]] = []
    monkeypatch.setattr(
        "janus.workers.whatsapp.stop_account_connector", lambda account_id: calls.append(("stop", account_id))
    )
    monkeypatch.setattr("janus.workers.whatsapp.start_account_connector", lambda acct: calls.append(("start", acct.id)))

    result = WhatsAppService(db).restart_connector(account)

    assert calls == [("stop", account.id), ("start", account.id)]
    assert result == {
        "account_id": account.id,
        "connector_started": True,
        "message": "WhatsApp connector restarted. New messages will sync automatically as they arrive.",
    }


def test_update_account_starts_connector_on_enable_transition(db, monkeypatch):
    """Flipping is_enabled from False to True should start the connector."""
    user = _make_user(db)
    account = _make_account(db, user, is_enabled=False)

    started: list[int] = []
    monkeypatch.setattr("janus.workers.whatsapp.start_account_connector", lambda acct: started.append(acct.id))

    WhatsAppService(db).update_account(account, {"is_enabled": True})

    assert started == [account.id]


def test_update_account_stops_connector_on_disable_transition(db, monkeypatch):
    """Flipping is_enabled from True to False should stop the connector."""
    user = _make_user(db)
    account = _make_account(db, user, is_enabled=True)

    stopped: list[int] = []
    monkeypatch.setattr("janus.workers.whatsapp.stop_account_connector", lambda account_id: stopped.append(account_id))

    WhatsAppService(db).update_account(account, {"is_enabled": False})

    assert stopped == [account.id]


def test_delete_account_stops_connector(db, monkeypatch):
    """Deleting an account should stop its live connector first."""
    user = _make_user(db)
    account = _make_account(db, user)
    account_id = account.id

    stopped: list[int] = []
    monkeypatch.setattr("janus.workers.whatsapp.stop_account_connector", lambda aid: stopped.append(aid))

    assert WhatsAppService(db).delete_account_for_user(account_id, user) is True

    assert stopped == [account_id]
    assert db.query(models.WhatsAppAccount).filter(models.WhatsAppAccount.id == account_id).first() is None


def test_setup_session_creation_exposes_fallback_when_pairing_is_unavailable(db, monkeypatch):
    """Unavailable pairing capability should route the setup session into fallback mode."""
    user = _make_user(db)

    monkeypatch.setattr(
        "janus.whatsapp_neonize.NeonizeAdapter.get_pairing_capability",
        lambda self: NeonizePairingCapability(
            supported=False,
            message="Live pairing is unavailable on this host.",
        ),
    )

    session = WhatsAppSetupService(db).create_setup_session(
        user,
        {"display_name": "Phone", "initial_history_days": 5},
    )

    assert session.setup_status == models.WhatsAppSetupStatus.AWAITING_FALLBACK
    assert session.pairing_state == models.WhatsAppPairingState.UNSUPPORTED
    assert session.show_fallback_import is True
    assert session.last_error == "Live pairing is unavailable on this host."


def test_neonize_test_connection_fails_when_package_cannot_be_imported(monkeypatch):
    """Connection checks should fail explicitly when Neonize cannot be imported."""
    account = models.WhatsAppAccount(
        user_id=1,
        display_name="Phone",
        provider="whatsapp_neonize",
        session_database_path="/tmp/neonize.db",
    )

    def raise_import_error(name, *args, **kwargs):
        if name == "neonize":
            raise ModuleNotFoundError("No module named 'neonize'")
        return __import__(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", raise_import_error)

    ok, message = NeonizeAdapter(account).test_connection()

    assert ok is False
    assert "WhatsApp support is not installed" in message
    assert "uv sync --extra whatsapp" in message
    assert "No module named 'neonize'" in message


def test_neonize_test_connection_reports_native_load_failure(monkeypatch):
    """An installed Neonize whose native library fails to load is a host problem, not a missing extra."""
    account = models.WhatsAppAccount(
        user_id=1,
        display_name="Phone",
        provider="whatsapp_neonize",
        session_database_path="/tmp/neonize.db",
    )
    original_import = __import__

    def raise_native_error(name, *args, **kwargs):
        if name == "neonize":
            raise OSError("/lib64/libc.so.6: version `GLIBC_2.28' not found")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", raise_native_error)

    ok, message = NeonizeAdapter(account).test_connection()

    assert ok is False
    assert "not supported on this host" in message
    assert "could not be loaded" in message


def test_neonize_test_connection_succeeds_when_package_is_importable(monkeypatch):
    """Connection checks should still pass when Neonize imports successfully."""
    account = models.WhatsAppAccount(
        user_id=1,
        display_name="Phone",
        provider="whatsapp_neonize",
        session_database_path="/tmp/neonize.db",
    )

    original_import = __import__

    def allow_neonize_import(name, *args, **kwargs):
        if name == "neonize":
            return object()
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", allow_neonize_import)

    ok, message = NeonizeAdapter(account).test_connection()

    assert ok is True
    assert message == "Neonize session configuration is present."


def test_neonize_get_pairing_capability_is_supported_when_package_imports(monkeypatch):
    """A real, importable Neonize package should report live pairing as supported."""
    original_import = __import__

    def allow_neonize_import(name, *args, **kwargs):
        if name == "neonize":
            return object()
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", allow_neonize_import)

    capability = NeonizeAdapter().get_pairing_capability()

    assert capability.supported is True


def test_neonize_poll_pairing_state_keeps_active_sessions_in_pairing(db):
    """Polling should forward whatever state the live pairing thread last persisted."""
    session = models.WhatsAppSetupSession(
        user_id=1,
        display_name="Phone",
        initial_history_days=3,
        setup_status=models.WhatsAppSetupStatus.PAIRING,
        pairing_state=models.WhatsAppPairingState.WAITING,
        pairing_message="Waiting for phone confirmation.",
        show_fallback_import=True,
        expires_at=datetime.now(UTC),
    )

    snapshot = NeonizeAdapter().poll_pairing_state(session)

    assert snapshot.state == models.WhatsAppPairingState.WAITING
    assert snapshot.message == "Waiting for phone confirmation."
    assert snapshot.show_fallback_import is True


def test_neonize_poll_pairing_state_reports_paired_sessions(db):
    """Polling should forward an explicit paired state unchanged."""
    session = models.WhatsAppSetupSession(
        user_id=1,
        display_name="Phone",
        initial_history_days=3,
        setup_status=models.WhatsAppSetupStatus.READY,
        pairing_state=models.WhatsAppPairingState.PAIRED,
        pairing_message="Pairing complete.",
        expires_at=datetime.now(UTC),
    )

    snapshot = NeonizeAdapter().poll_pairing_state(session)

    assert snapshot.state == models.WhatsAppPairingState.PAIRED
    assert snapshot.message == "Pairing complete."


def test_setup_session_complete_persists_account_and_starts_connector(db, monkeypatch):
    """Completing a setup session should create the account and start its live connector."""
    user = _make_user(db)

    monkeypatch.setattr(
        "janus.whatsapp_neonize.NeonizeAdapter.get_pairing_capability",
        lambda self: NeonizePairingCapability(supported=True, message="Pairing ready.", fallback_available=True),
    )
    monkeypatch.setattr(
        "janus.whatsapp_neonize.NeonizeAdapter.begin_pairing",
        lambda self, session: NeonizePairingSnapshot(
            state=models.WhatsAppPairingState.PAIRED,
            message="Pairing complete.",
            session_database_path="/tmp/paired.db",
        ),
    )

    started: list[int] = []
    monkeypatch.setattr("janus.workers.whatsapp.start_account_connector", lambda account: started.append(account.id))

    service = WhatsAppSetupService(db)
    session = service.create_setup_session(user, {"display_name": "Phone", "initial_history_days": 4})
    session = service.start_pairing(session)
    result = service.complete_setup_session(user, session)

    account = db.query(models.WhatsAppAccount).filter(models.WhatsAppAccount.id == result["account"]["id"]).one()
    assert account.display_name == "Phone"
    assert account.initial_history_days == 4
    assert account.session_database_path == "/tmp/paired.db"
    assert account.client_uuid == f"setup-{session.id}"
    assert started == [account.id]
    assert result["sync_result"] == {
        "account_id": account.id,
        "connector_started": True,
        "message": "WhatsApp connector started. Messages will sync automatically as they arrive.",
    }


def test_setup_session_reconnect_updates_existing_account(db, monkeypatch):
    """Reconnect flows should update the existing account instead of creating a duplicate."""
    user = _make_user(db)
    account = _make_account(
        db,
        user,
        display_name="Existing Phone",
        session_database_path="/tmp/old.db",
        connection_status=models.WhatsAppConnectionStatus.DEGRADED,
    )

    started: list[int] = []
    monkeypatch.setattr("janus.workers.whatsapp.start_account_connector", lambda acct: started.append(acct.id))

    service = WhatsAppSetupService(db)
    session = service.create_setup_session(
        user,
        {"display_name": "Existing Phone", "initial_history_days": 6, "account_id": account.id},
    )
    session = service.import_fallback_session(session, session_data=None, session_database_path="/tmp/new.db")
    service.complete_setup_session(user, session)

    db.refresh(account)
    assert db.query(models.WhatsAppAccount).count() == 1
    assert account.session_database_path == "/tmp/new.db"
    assert account.initial_history_days == 6
    assert started == [account.id]


def test_setup_session_cancel_and_expiry_behavior(db, monkeypatch):
    """Setup sessions should support cancellation and bounded expiry."""
    user = _make_user(db)

    monkeypatch.setattr(
        "janus.whatsapp_neonize.NeonizeAdapter.get_pairing_capability",
        lambda self: NeonizePairingCapability(supported=True, message="Pairing ready.", fallback_available=True),
    )
    monkeypatch.setattr(
        "janus.whatsapp_neonize.NeonizeAdapter.begin_pairing",
        lambda self, session: NeonizePairingSnapshot(
            state=models.WhatsAppPairingState.WAITING,
            message="Waiting for phone confirmation.",
            show_fallback_import=True,
        ),
    )

    service = WhatsAppSetupService(db)
    session = service.create_setup_session(user, {"display_name": "Phone", "initial_history_days": 3})
    session = service.start_pairing(session)
    cancelled = service.cancel_setup_session(session)

    assert cancelled.setup_status == models.WhatsAppSetupStatus.CANCELLED
    assert cancelled.pairing_state == models.WhatsAppPairingState.CANCELLED

    expired = service.create_setup_session(user, {"display_name": "Phone 2", "initial_history_days": 3})
    expired.expires_at = datetime.now(UTC) - SETUP_SESSION_TTL
    db.commit()

    service.expire_stale_sessions(user_id=user.id)
    db.refresh(expired)

    assert expired.setup_status == models.WhatsAppSetupStatus.EXPIRED
    assert expired.pairing_state == models.WhatsAppPairingState.EXPIRED


@pytest.mark.parametrize(
    ("thread_state", "thread_message", "expected_status", "expected_error"),
    [
        (models.WhatsAppPairingState.PAIRED, "Pairing complete.", models.WhatsAppSetupStatus.READY, None),
        (
            models.WhatsAppPairingState.FAILED,
            "Pairing failed: rejected.",
            models.WhatsAppSetupStatus.FAILED,
            "Pairing failed: rejected.",
        ),
    ],
)
def test_polling_derives_setup_status_from_the_pairing_threads_final_state(
    db, monkeypatch, thread_state, thread_message, expected_status, expected_error
):
    """A paired session must become READY so it can be finished (JAN-223 regression).

    `begin_pairing` only launches the pairing thread; the thread later writes the final
    `pairing_state` straight to the row. Polling used to skip any non-active pairing
    state, so the setup status stayed PAIRING and the wizard's Finish stayed disabled.
    """
    user = _make_user(db)
    monkeypatch.setattr(
        "janus.whatsapp_neonize.NeonizeAdapter.get_pairing_capability",
        lambda self: NeonizePairingCapability(supported=True, message="Pairing ready.", fallback_available=True),
    )
    monkeypatch.setattr(
        "janus.whatsapp_neonize.NeonizeAdapter.begin_pairing",
        lambda self, session: NeonizePairingSnapshot(
            state=models.WhatsAppPairingState.STARTING,
            message="Waiting for a QR code from WhatsApp.",
            session_database_path="/tmp/setup.db",
        ),
    )
    service = WhatsAppSetupService(db)
    session = service.start_pairing(
        service.create_setup_session(user, {"display_name": "Phone", "initial_history_days": 3})
    )
    assert session.setup_status == models.WhatsAppSetupStatus.PAIRING

    # What `_update_setup_session_pairing` does from the pairing thread.
    session.pairing_state = thread_state
    session.pairing_message = thread_message
    db.commit()

    polled = service.get_setup_session_for_user(session.id, user)

    assert polled.setup_status == expected_status
    assert polled.pairing_state == thread_state
    assert polled.pairing_message == thread_message
    assert polled.last_error == expected_error
    assert polled.session_database_path == "/tmp/setup.db"
