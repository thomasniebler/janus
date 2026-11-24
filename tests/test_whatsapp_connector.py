"""Tests for the live Neonize connector: event translation and per-account ingestion."""

import builtins
import importlib.util
import time

import pytest

from janus import database, models, time_utils, whatsapp_neonize
from janus import worker_manager as worker_manager_module
from janus.whatsapp_neonize import (
    NeonizeConnector,
    _describe_message,
    _qr_bytes_to_data_uri,
    _translate_history_sync,
    _translate_live_message,
)
from janus.workers import whatsapp as whatsapp_worker

# Uses real Neonize protobuf objects (or segno), which are the optional `whatsapp` extra.
requires_whatsapp_extra = pytest.mark.skipif(
    importlib.util.find_spec("neonize") is None, reason="needs the optional 'whatsapp' extra"
)


def _wa_message(**kwargs):
    from neonize.proto.waE2E.WAWebProtobufsE2E_pb2 import Message as WAMessage

    return WAMessage(**kwargs)


def _live_event(*, text=None, audio=None, is_from_me=False, is_group=False, chat_user="49123", pushname="Alice"):
    from neonize.proto.Neonize_pb2 import JID, MessageInfo, MessageSource
    from neonize.proto.Neonize_pb2 import Message as MessageEv

    chat_server = "g.us" if is_group else "s.whatsapp.net"
    chat_jid = JID(User=chat_user, Server=chat_server, Device=0, RawAgent=0, IsEmpty=False)
    sender_jid = JID(User="49999", Server="s.whatsapp.net", Device=0, RawAgent=0, IsEmpty=False)
    source = MessageSource(Chat=chat_jid, Sender=sender_jid, IsFromMe=is_from_me, IsGroup=is_group)
    info = MessageInfo(ID="MSG1", Timestamp=1732000000, Pushname=pushname, MessageSource=source)
    message_kwargs = {}
    if text is not None:
        message_kwargs["conversation"] = text
    if audio is not None:
        message_kwargs["audioMessage"] = audio
    return MessageEv(Info=info, Message=_wa_message(**message_kwargs))


class _FakeClient:
    """Records event/qr registrations the way the real Neonize client would."""

    def __init__(self):
        self.handlers: dict[str, object] = {}
        self.qr_handler = None
        self.connect_called = False
        self.disconnect_called = False

    def event(self, event_type):
        def register(func):
            self.handlers[event_type.__name__] = func
            return func

        return register

    def qr(self, func):
        self.qr_handler = func

    def connect(self, proxy_settings=None):
        del proxy_settings
        self.connect_called = True

    def disconnect(self):
        self.disconnect_called = True


@requires_whatsapp_extra
def test_describe_message_detects_text():
    """A plain-text message should surface its full text as body/snippet."""
    body, snippet, message_type, attachment_summary = _describe_message(_wa_message(conversation="Hello there"))
    assert body == "Hello there"
    assert snippet == "Hello there"
    assert message_type == "text"
    assert attachment_summary is None


@requires_whatsapp_extra
def test_describe_message_detects_voice_note():
    """A push-to-talk audio message should be classified as a voice note."""
    from neonize.proto.waE2E.WAWebProtobufsE2E_pb2 import AudioMessage

    audio = AudioMessage(PTT=True, seconds=7, mimetype="audio/ogg; codecs=opus")
    body, snippet, message_type, attachment_summary = _describe_message(_wa_message(audioMessage=audio))
    assert body is None
    assert message_type == "voice_note"
    assert "🎤" in snippet
    assert "7s" in attachment_summary


@requires_whatsapp_extra
def test_describe_message_detects_regular_audio_when_not_ptt():
    """A non-PTT audio message should be classified as regular audio, not a voice note."""
    from neonize.proto.waE2E.WAWebProtobufsE2E_pb2 import AudioMessage

    audio = AudioMessage(PTT=False, seconds=30, mimetype="audio/mp4")
    _, _, message_type, _ = _describe_message(_wa_message(audioMessage=audio))
    assert message_type == "audio"


@requires_whatsapp_extra
def test_translate_live_message_builds_conversation_and_message_payloads():
    """A live MessageEv should translate into matching conversation/message payloads."""
    event = _live_event(text="Hi from Alice", is_from_me=False)

    conversation_payload, message_payload = _translate_live_message(event)

    assert conversation_payload.provider_chat_id == "49123@s.whatsapp.net"
    assert conversation_payload.conversation_type == models.WhatsAppConversationType.DIRECT
    assert message_payload.provider_message_id == "MSG1"
    assert message_payload.body == "Hi from Alice"
    assert message_payload.direction == models.WhatsAppMessageDirection.INCOMING
    assert message_payload.sender_display_name == "Alice"


@requires_whatsapp_extra
def test_translate_live_message_marks_group_chats_and_outgoing_direction():
    """Group JIDs and self-authored messages should be classified accordingly."""
    event = _live_event(text="Hi group", is_from_me=True, is_group=True, chat_user="120363")

    conversation_payload, message_payload = _translate_live_message(event)

    assert conversation_payload.conversation_type == models.WhatsAppConversationType.GROUP
    assert message_payload.direction == models.WhatsAppMessageDirection.OUTGOING


@requires_whatsapp_extra
def test_translate_live_message_group_title_uses_chat_id_not_sender_pushname():
    """A group chat's title should not flip to whichever participant sent the last message."""
    event = _live_event(text="Hi group", is_group=True, chat_user="120363", pushname="Alice")

    conversation_payload, _ = _translate_live_message(event)

    assert conversation_payload.title == "120363"


@requires_whatsapp_extra
def test_translate_live_message_outgoing_direct_title_uses_chat_id_not_local_pushname():
    """An outgoing direct message's title should not become the local user's own name."""
    event = _live_event(text="On my way", is_from_me=True, pushname="Me")

    conversation_payload, _ = _translate_live_message(event)

    assert conversation_payload.title == "49123"


@requires_whatsapp_extra
def test_translate_history_sync_yields_payloads_and_skips_malformed_entries():
    """HistorySync should yield valid entries and silently skip malformed ones."""
    from neonize.proto.waCommon.WACommon_pb2 import MessageKey
    from neonize.proto.waHistorySync.WAWebProtobufsHistorySync_pb2 import Conversation, HistorySync, HistorySyncMsg
    from neonize.proto.waWeb.WAWebProtobufsWeb_pb2 import WebMessageInfo

    good_key = MessageKey(remoteJID="123@s.whatsapp.net", fromMe=False, ID="HID1", participant="")
    good_message = WebMessageInfo(
        key=good_key, message=_wa_message(conversation="hist text"), messageTimestamp=1700000000, pushName="Bob"
    )
    malformed_key_message = WebMessageInfo(key=MessageKey(remoteJID="123@s.whatsapp.net", ID=""))
    conversation = Conversation(
        ID="123@s.whatsapp.net",
        name="Bob Chat",
        unreadCount=2,
        messages=[HistorySyncMsg(message=good_message), HistorySyncMsg(message=malformed_key_message)],
    )
    malformed_conversation = Conversation(ID="", name="Ghost")
    history_sync = HistorySync(conversations=[conversation, malformed_conversation])

    class _Event:
        Data = history_sync

    results = list(_translate_history_sync(_Event()))

    assert len(results) == 1
    conversation_payload, message_payload = results[0]
    assert conversation_payload.provider_chat_id == "123@s.whatsapp.net"
    assert conversation_payload.title == "Bob Chat"
    assert conversation_payload.unread_count == 2
    assert message_payload.provider_message_id == "HID1"
    assert message_payload.body == "hist text"
    assert message_payload.sender_display_name == "Bob"


@requires_whatsapp_extra
def test_qr_bytes_to_data_uri_produces_a_scannable_png_data_uri():
    """Raw QR bytes should render as a base64 PNG data URI the frontend can display."""
    payload = _qr_bytes_to_data_uri(b"1@abc,def,ghi==,jkl=")
    assert payload.startswith("data:image/png;base64,")


@requires_whatsapp_extra
def test_register_handlers_account_mode_registers_message_and_history_only():
    """An account-bound connector should register message ingestion, not pairing, handlers."""
    connector = NeonizeConnector(session_database_path="/tmp/a.db", client_uuid="account-1", account_id=1)
    client = _FakeClient()

    connector._register_handlers(client)

    assert set(client.handlers.keys()) == {"Connected", "Disconnected", "LoggedOut", "Message", "HistorySync"}
    assert client.qr_handler is None


@requires_whatsapp_extra
def test_register_handlers_setup_mode_registers_qr_and_pairstatus_only():
    """A setup-session-bound connector should register pairing, not ingestion, handlers."""
    connector = NeonizeConnector(session_database_path="/tmp/s.db", client_uuid="setup-1", setup_session_id=1)
    client = _FakeClient()

    connector._register_handlers(client)

    assert set(client.handlers.keys()) == {"Connected", "Disconnected", "LoggedOut", "PairStatus"}
    assert client.qr_handler is not None


@requires_whatsapp_extra
def test_ingest_persists_conversation_and_message(db, monkeypatch, testing_session_local):
    """Ingesting a translated event should upsert both rows in a fresh DB session."""
    monkeypatch.setattr(database, "SessionLocal", testing_session_local)

    user = models.User(username="owner", hashed_password="hash", is_admin=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    account = models.WhatsAppAccount(
        user_id=user.id, display_name="Phone", provider="whatsapp_neonize", session_database_path="/tmp/a.db"
    )
    db.add(account)
    db.commit()
    db.refresh(account)

    connector = NeonizeConnector(session_database_path="/tmp/a.db", client_uuid="account-1", account_id=account.id)
    conversation_payload, message_payload = _translate_live_message(_live_event(text="hello"))

    connector._ingest(conversation_payload, message_payload)

    db.expire_all()
    assert db.query(models.WhatsAppConversation).count() == 1
    assert db.query(models.WhatsAppMessage).count() == 1
    refreshed_account = db.query(models.WhatsAppAccount).filter(models.WhatsAppAccount.id == account.id).one()
    assert refreshed_account.connection_status == models.WhatsAppConnectionStatus.CONNECTED


@requires_whatsapp_extra
def test_ingest_rolls_back_unrelated_pending_changes_on_error(db, monkeypatch, testing_session_local):
    """Ingestion failures should roll back and be swallowed rather than crash the connector."""
    monkeypatch.setattr(database, "SessionLocal", testing_session_local)
    monkeypatch.setattr(
        "janus.services.whatsapp_service.WhatsAppService.upsert_message",
        lambda self, *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    user = models.User(username="owner", hashed_password="hash", is_admin=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    account = models.WhatsAppAccount(
        user_id=user.id, display_name="Phone", provider="whatsapp_neonize", session_database_path="/tmp/a.db"
    )
    db.add(account)
    db.commit()
    db.refresh(account)

    connector = NeonizeConnector(session_database_path="/tmp/a.db", client_uuid="account-1", account_id=account.id)
    conversation_payload, message_payload = _translate_live_message(_live_event(text="hello"))

    # Should not raise: ingestion failures are logged and swallowed per event.
    connector._ingest(conversation_payload, message_payload)

    db.expire_all()
    assert db.query(models.WhatsAppMessage).count() == 0


def test_on_connected_in_setup_mode_marks_paired_and_disconnects(db, monkeypatch, testing_session_local):
    """A successful pairing connection should mark PAIRED and hand off by disconnecting."""
    monkeypatch.setattr(database, "SessionLocal", testing_session_local)
    session = models.WhatsAppSetupSession(
        user_id=1,
        display_name="Phone",
        initial_history_days=3,
        pairing_state=models.WhatsAppPairingState.STARTING,
        expires_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    )
    db.add(session)
    db.commit()
    db.refresh(session)

    connector = NeonizeConnector(session_database_path="/tmp/s.db", client_uuid="setup-1", setup_session_id=session.id)
    connector.client = _FakeClient()

    connector._on_connected(connector.client, object())

    db.expire_all()
    refreshed = db.query(models.WhatsAppSetupSession).filter(models.WhatsAppSetupSession.id == session.id).one()
    assert refreshed.pairing_state == models.WhatsAppPairingState.PAIRED
    assert refreshed.session_database_path == "/tmp/s.db"
    assert connector.client.disconnect_called is True


@requires_whatsapp_extra
def test_on_pair_status_failure_marks_setup_session_failed(db, monkeypatch, testing_session_local):
    """A PairStatus error should mark the setup session FAILED with the provider's error text."""
    from neonize.proto.Neonize_pb2 import PairStatus

    monkeypatch.setattr(database, "SessionLocal", testing_session_local)
    session = models.WhatsAppSetupSession(
        user_id=1,
        display_name="Phone",
        initial_history_days=3,
        pairing_state=models.WhatsAppPairingState.STARTING,
        expires_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    )
    db.add(session)
    db.commit()
    db.refresh(session)

    connector = NeonizeConnector(session_database_path="/tmp/s.db", client_uuid="setup-1", setup_session_id=session.id)
    connector._on_pair_status(_FakeClient(), PairStatus(Status=PairStatus.ERROR, Error="denied"))

    db.expire_all()
    refreshed = db.query(models.WhatsAppSetupSession).filter(models.WhatsAppSetupSession.id == session.id).one()
    assert refreshed.pairing_state == models.WhatsAppPairingState.FAILED
    assert "denied" in refreshed.pairing_message


@requires_whatsapp_extra
def test_on_qr_updates_pairing_payload(db, monkeypatch, testing_session_local):
    """A QR event should persist a rendered data-URI payload and CODE_READY state."""
    monkeypatch.setattr(database, "SessionLocal", testing_session_local)
    session = models.WhatsAppSetupSession(
        user_id=1,
        display_name="Phone",
        initial_history_days=3,
        pairing_state=models.WhatsAppPairingState.STARTING,
        expires_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    )
    db.add(session)
    db.commit()
    db.refresh(session)

    connector = NeonizeConnector(session_database_path="/tmp/s.db", client_uuid="setup-1", setup_session_id=session.id)
    connector._on_qr(_FakeClient(), b"raw-qr-bytes")

    db.expire_all()
    refreshed = db.query(models.WhatsAppSetupSession).filter(models.WhatsAppSetupSession.id == session.id).one()
    assert refreshed.pairing_state == models.WhatsAppPairingState.CODE_READY
    assert refreshed.pairing_qr_payload.startswith("data:image/png;base64,")


def test_whatsapp_connector_worker_returns_when_account_missing(db, monkeypatch, testing_session_local):
    """The worker body should return immediately if the account no longer exists."""
    monkeypatch.setattr(database, "SessionLocal", testing_session_local)

    called = []
    monkeypatch.setattr(whatsapp_worker, "NeonizeConnector", lambda **kwargs: called.append(kwargs) or _FakeConnector())

    whatsapp_worker.whatsapp_connector_worker(999)

    assert called == []


def test_whatsapp_connector_worker_returns_when_account_disabled(db, monkeypatch, testing_session_local):
    """The worker body should return immediately if the account has been disabled."""
    monkeypatch.setattr(database, "SessionLocal", testing_session_local)
    user = models.User(username="owner", hashed_password="hash", is_admin=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    account = models.WhatsAppAccount(
        user_id=user.id,
        display_name="Phone",
        provider="whatsapp_neonize",
        session_database_path="/tmp/a.db",
        is_enabled=False,
    )
    db.add(account)
    db.commit()
    db.refresh(account)

    called = []
    monkeypatch.setattr(whatsapp_worker, "NeonizeConnector", lambda **kwargs: called.append(kwargs) or _FakeConnector())

    whatsapp_worker.whatsapp_connector_worker(account.id)

    assert called == []


@requires_whatsapp_extra
def test_whatsapp_connector_worker_reuses_persisted_client_uuid(db, monkeypatch, testing_session_local):
    """The durable worker should reopen the client under the UUID it was paired with."""
    monkeypatch.setattr(database, "SessionLocal", testing_session_local)
    user = models.User(username="owner", hashed_password="hash", is_admin=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    account = models.WhatsAppAccount(
        user_id=user.id,
        display_name="Phone",
        provider="whatsapp_neonize",
        session_database_path="/tmp/a.db",
        client_uuid="setup-42",
        is_enabled=True,
    )
    db.add(account)
    db.commit()
    db.refresh(account)

    called = []
    monkeypatch.setattr(whatsapp_worker, "NeonizeConnector", lambda **kwargs: called.append(kwargs) or _FakeConnector())

    whatsapp_worker.whatsapp_connector_worker(account.id)

    assert called[0]["client_uuid"] == "setup-42"


@requires_whatsapp_extra
def test_whatsapp_connector_worker_falls_back_to_legacy_uuid_when_unset(db, monkeypatch, testing_session_local):
    """Accounts created before client_uuid was persisted should keep their old account-scoped UUID."""
    monkeypatch.setattr(database, "SessionLocal", testing_session_local)
    user = models.User(username="owner", hashed_password="hash", is_admin=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    account = models.WhatsAppAccount(
        user_id=user.id,
        display_name="Phone",
        provider="whatsapp_neonize",
        session_database_path="/tmp/a.db",
        is_enabled=True,
    )
    db.add(account)
    db.commit()
    db.refresh(account)

    called = []
    monkeypatch.setattr(whatsapp_worker, "NeonizeConnector", lambda **kwargs: called.append(kwargs) or _FakeConnector())

    whatsapp_worker.whatsapp_connector_worker(account.id)

    assert called[0]["client_uuid"] == f"account-{account.id}"


class _FakeConnector:
    def connect_and_run(self):
        pass

    def stop(self):
        pass


def test_start_account_connector_registers_a_per_account_worker(monkeypatch):
    """Starting a connector should register a supervised worker under a per-account label."""
    account = models.WhatsAppAccount(
        id=42,
        user_id=1,
        display_name="Phone",
        provider="whatsapp_neonize",
        session_database_path="/tmp/account-42.db",
    )

    calls = []
    monkeypatch.setattr(
        "janus.worker_manager.worker_manager.start",
        lambda label, target, args=(): calls.append((label, target, args)),
    )

    whatsapp_worker.start_account_connector(account)

    assert calls == [("whatsapp:42", whatsapp_worker.whatsapp_connector_worker, (42,))]


def test_start_account_connector_skips_accounts_without_a_session(monkeypatch):
    """Starting a connector for an account with no paired session should be a no-op."""
    account = models.WhatsAppAccount(id=43, user_id=1, display_name="Phone", provider="whatsapp_neonize")

    calls = []
    monkeypatch.setattr(
        "janus.worker_manager.worker_manager.start",
        lambda label, target, args=(): calls.append((label, target, args)),
    )

    whatsapp_worker.start_account_connector(account)

    assert calls == []


def test_stop_account_connector_stops_live_connector_and_worker(monkeypatch):
    """Stopping a connector should disconnect the live client and stop its worker registration."""
    fake_connector = _FakeConnector()
    stopped = {"connector": False}
    fake_connector.stop = lambda: stopped.__setitem__("connector", True)
    whatsapp_worker._live_connectors[7] = fake_connector

    worker_stop_calls = []
    monkeypatch.setattr("janus.worker_manager.worker_manager.stop", lambda label: worker_stop_calls.append(label))

    whatsapp_worker.stop_account_connector(7)

    assert stopped["connector"] is True
    assert worker_stop_calls == ["whatsapp:7"]
    assert 7 not in whatsapp_worker._live_connectors


@requires_whatsapp_extra
def test_whatsapp_connector_worker_cleanup_does_not_orphan_a_newer_connector(db, monkeypatch, testing_session_local):
    """A racing restart's newer connector must survive this worker's own teardown.

    If a restart lands a new connector in `_live_connectors` for the same account
    while an older, doomed worker is still exiting, the older worker's unconditional
    cleanup must not pop the newer entry out from under it (which would make the
    still-running connector unreachable to any future stop/restart call).
    """
    monkeypatch.setattr(database, "SessionLocal", testing_session_local)
    user = models.User(username="owner", hashed_password="hash", is_admin=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    account = models.WhatsAppAccount(
        user_id=user.id,
        display_name="Phone",
        provider="whatsapp_neonize",
        session_database_path="/tmp/a.db",
        is_enabled=True,
    )
    db.add(account)
    db.commit()
    db.refresh(account)

    newer_connector = _FakeConnector()

    class _RacingConnector(_FakeConnector):
        def connect_and_run(self):
            # Simulate a restart registering a newer connector for this account
            # while this (older, doomed) worker is still tearing down.
            whatsapp_worker._live_connectors[account.id] = newer_connector

    monkeypatch.setattr(whatsapp_worker, "NeonizeConnector", lambda **kwargs: _RacingConnector())

    whatsapp_worker.whatsapp_connector_worker(account.id)

    assert whatsapp_worker._live_connectors.get(account.id) is newer_connector


def _fail_neonize_import(monkeypatch):
    """Make `import neonize` fail like a native-library load error and count each attempt."""
    attempts = []
    real_import = builtins.__import__

    def failing_import(name, *args, **kwargs):
        if name == "neonize" or name.startswith("neonize."):
            attempts.append(name)
            raise OSError("/lib64/libc.so.6: version `GLIBC_2.28' not found")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", failing_import)
    return attempts


def test_neonize_native_import_failure_is_probed_once_per_process(monkeypatch):
    """A failed native load must never be re-attempted in-process (JAN-172): re-touching it can segfault."""
    attempts = _fail_neonize_import(monkeypatch)

    first = whatsapp_neonize.neonize_import_error()
    assert first is not None and "not supported on this host" in first
    assert whatsapp_neonize.neonize_import_error() == first
    assert whatsapp_neonize.NeonizeAdapter().get_pairing_capability().supported is False
    with pytest.raises(whatsapp_neonize.NeonizeUnavailableError):
        whatsapp_neonize._default_client_factory("/tmp/a.db", "account-1")

    assert attempts == ["neonize"]


def test_supervisor_does_not_retry_whatsapp_worker_after_native_import_failure(db, monkeypatch, testing_session_local):
    """A permanent native-import failure marks the account DEGRADED once and is never restarted."""
    monkeypatch.setattr(database, "SessionLocal", testing_session_local)
    monkeypatch.setattr(worker_manager_module, "_BACKOFF", [0])
    user = models.User(username="owner", hashed_password="hash", is_admin=True)
    db.add(user)
    db.commit()
    account = models.WhatsAppAccount(
        user_id=user.id,
        display_name="Phone",
        provider="whatsapp_neonize",
        session_database_path="/tmp/a.db",
        is_enabled=True,
    )
    db.add(account)
    db.commit()
    db.refresh(account)

    constructed = []
    monkeypatch.setattr(whatsapp_worker, "NeonizeConnector", lambda **kwargs: constructed.append(kwargs))
    attempts = _fail_neonize_import(monkeypatch)

    manager = worker_manager_module.WorkerManager(start_supervisor=False)
    label = f"whatsapp:{account.id}"
    manager.start(label, whatsapp_worker.whatsapp_connector_worker, args=(account.id,))
    state = manager._workers[label]
    for _ in range(5):
        state.thread.join(timeout=2)
        manager._handle_dead_worker(state)
        time.sleep(0.05)

    assert state.thread.is_alive() is False
    assert state.permanently_failed is True
    assert state.restart_count == 0
    assert manager.status()[label]["permanently_failed"] is True
    assert attempts == ["neonize"]
    assert constructed == []

    # An explicit restart (e.g. re-enabling the account) must not re-touch the native library either.
    manager.start(label, whatsapp_worker.whatsapp_connector_worker, args=(account.id,))
    manager._workers[label].thread.join(timeout=2)
    assert attempts == ["neonize"]

    db.expire_all()
    refreshed = db.get(models.WhatsAppAccount, account.id)
    assert refreshed.connection_status == models.WhatsAppConnectionStatus.DEGRADED
    assert "not supported on this host" in refreshed.last_sync_error


def test_epoch_to_utc_accepts_milliseconds_and_seconds():
    """Neonize 0.5 reports live MessageInfo.Timestamp in milliseconds; history sync stays in seconds (JAN-346)."""
    from datetime import UTC, datetime

    expected = datetime(2026, 11, 8, 20, 15, 50, tzinfo=UTC)
    assert time_utils.epoch_to_utc(1_794_168_950_000) == expected
    assert time_utils.epoch_to_utc(1_794_168_950) == expected
    assert time_utils.epoch_to_utc(1_794_168_950_123).microsecond == 123_000


def test_epoch_to_utc_falls_back_to_now_on_junk(monkeypatch, caplog):
    """Junk or out-of-range epoch values must not raise; they fall back to the current time."""
    from datetime import UTC, datetime

    now = datetime(2026, 1, 1, tzinfo=UTC)
    monkeypatch.setattr("janus.time_utils.utcnow", lambda: now)
    for raw in (None, 0, -5, "nope", True, 10**30, 4_200_000_000, 10**18):
        assert time_utils.epoch_to_utc(raw) == now
    assert "provider timestamp" in caplog.text


@requires_whatsapp_extra
def test_translate_live_message_accepts_millisecond_timestamps():
    """A millisecond timestamp used to raise 'year is out of range' and drop the message (JAN-346)."""
    from datetime import UTC, datetime

    event = _live_event(text="late message")
    event.Info.Timestamp = 1_794_168_950_000
    _, message_payload = _translate_live_message(event)
    assert message_payload.provider_timestamp == datetime(2026, 11, 8, 20, 15, 50, tzinfo=UTC)
