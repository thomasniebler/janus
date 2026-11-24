"""Neonize integration: setup-time pairing and the live per-account connector."""

from __future__ import annotations

import base64
import io
import logging
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from janus import config, database, models, time_utils, whatsapp_trigger_events
from janus.security import security_service

logger = logging.getLogger(__name__)

# One-shot, process-wide probe (JAN-172): a failed `import neonize` (e.g. host glibc too
# old for the Go .so) is dropped from sys.modules, so re-importing re-runs the native load,
# which can SIGSEGV the whole process. Every Neonize entry point uses this cached result.
_PROBE_UNSET: Any = object()
_neonize_probe_lock = threading.Lock()
_neonize_probe_result: Any = _PROBE_UNSET


class NeonizeUnavailableError(RuntimeError):
    """Permanent failure: the Neonize native library cannot be loaded in this process."""


def neonize_import_error() -> str | None:
    """Return a user-facing message if Neonize cannot be imported, probing only once per process."""
    global _neonize_probe_result
    with _neonize_probe_lock:
        if _neonize_probe_result is _PROBE_UNSET:
            try:
                __import__("neonize")
                _neonize_probe_result = None
            except Exception as exc:  # ModuleNotFoundError: the optional `whatsapp` extra is not installed
                logger.warning("Neonize is unavailable; WhatsApp is disabled: %s", exc)
                _neonize_probe_result = (
                    f"WhatsApp support is not installed: {exc}. Install the optional 'whatsapp' extra (`uv sync --extra whatsapp`) and restart Janus."  # noqa: E501
                    if isinstance(exc, ModuleNotFoundError)
                    else f"WhatsApp is not supported on this host: the Neonize native library could not be loaded ({exc}). Fix the host runtime and restart Janus."  # noqa: E501
                )
        return _neonize_probe_result


def _require_neonize() -> None:
    """Raise `NeonizeUnavailableError` instead of re-attempting a failed native load."""
    if (import_error := neonize_import_error()) is not None:
        raise NeonizeUnavailableError(import_error)


@dataclass(frozen=True)
class NeonizeConversationPayload:
    """Normalized conversation payload derived from a Neonize event."""

    provider_chat_id: str
    title: str
    conversation_type: models.WhatsAppConversationType
    participant_summary: str | None = None
    unread_count: int = 0


@dataclass(frozen=True)
class NeonizeMessagePayload:
    """Normalized message payload derived from a Neonize event."""

    provider_message_id: str
    provider_chat_id: str
    provider_timestamp: datetime
    direction: models.WhatsAppMessageDirection
    sender_identifier: str | None
    sender_display_name: str | None
    body: str | None
    snippet: str
    message_type: str | None = None
    attachment_summary: str | None = None
    reply_to_message_id: str | None = None
    # User parts of the JIDs this message @-mentions or quotes (replies to), for
    # the ``whatsapp.group_mention`` trigger event (JAN-361). Live messages only.
    referenced_users: tuple[str, ...] = ()
    # False for reactions, edits, deletions and other content-less messages, which never fire events.
    has_content: bool = True


@dataclass(frozen=True)
class NeonizePairingCapability:
    """Host/runtime capability report for live in-app pairing."""

    supported: bool
    message: str
    fallback_available: bool = True


@dataclass(frozen=True)
class NeonizePairingSnapshot:
    """Current pairing state snapshot for one setup session."""

    state: models.WhatsAppPairingState
    message: str
    pairing_code: str | None = None
    pairing_qr_payload: str | None = None
    session_data: str | None = None
    session_database_path: str | None = None
    show_fallback_import: bool = False


@dataclass(frozen=True)
class NeonizeSessionArtifact:
    """Session material that can later back a durable WhatsApp account."""

    session_data: str | None = None
    session_database_path: str | None = None

    @property
    def is_configured(self) -> bool:
        """Return whether any usable artifact is present."""
        return bool(self.session_data or self.session_database_path)


def _session_dir() -> Path:
    """Return (and create) the directory Neonize sqlite session stores live in."""
    path = config.get_data_dir() / "whatsapp_sessions"
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def _default_client_factory(session_database_path: str, client_uuid: str) -> Any:
    """Instantiate a real Neonize client bound to one sqlite session store."""
    _require_neonize()
    from neonize.client import NewClient

    return NewClient(session_database_path, uuid=client_uuid)


def _qr_bytes_to_data_uri(qr_bytes: bytes) -> str:
    """Render raw Neonize QR payload bytes as a scannable PNG data URI."""
    import segno

    buffer = io.BytesIO()
    segno.make_qr(qr_bytes).save(buffer, kind="png", scale=6, border=2)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _describe_message(message_proto: Any) -> tuple[str | None, str, str | None, str | None]:
    """Return (body, snippet, message_type, attachment_summary) for one WhatsApp message."""
    from neonize.utils.message import extract_text

    audio = message_proto.audioMessage
    if audio.ListFields():
        seconds = getattr(audio, "seconds", 0) or 0
        mimetype = getattr(audio, "mimetype", "") or "audio"
        if getattr(audio, "PTT", False):
            return (None, "🎤 Voice message", "voice_note", f"Voice message ({seconds}s, {mimetype})")
        return (None, "🎵 Audio message", "audio", f"Audio message ({seconds}s, {mimetype})")

    text = extract_text(message_proto).strip()
    if text:
        return text, text[:100], "text", None

    try:
        from neonize.utils.enum import MediaTypeToMMS

        media_kind = MediaTypeToMMS.from_message(message_proto).value
        return None, f"[{media_kind}]", media_kind, f"Unsupported {media_kind} attachment"
    except Exception:
        return None, "(unsupported message)", None, None


def _translate_live_message(event: Any) -> tuple[NeonizeConversationPayload, NeonizeMessagePayload] | None:
    """Translate a live MessageEv into normalized conversation/message payloads."""
    from neonize.utils.jid import Jid2String

    info = event.Info
    source = info.MessageSource
    if not (source.Chat.User or source.Chat.Server):
        return None

    provider_chat_id = Jid2String(source.Chat)
    is_group = bool(source.IsGroup) or provider_chat_id.endswith("@g.us")
    sender_identifier = Jid2String(source.Sender) if (source.Sender.User or source.Sender.Server) else provider_chat_id
    sender_display_name = info.Pushname or None
    body, snippet, message_type, attachment_summary = _describe_message(event.Message)
    provider_timestamp = time_utils.epoch_to_utc(getattr(info, "Timestamp", None))

    conversation_type = models.WhatsAppConversationType.GROUP if is_group else models.WhatsAppConversationType.DIRECT
    direction = (
        models.WhatsAppMessageDirection.OUTGOING if source.IsFromMe else models.WhatsAppMessageDirection.INCOMING
    )
    # Pushname identifies the message sender, not the chat. For a 1:1 incoming
    # message that's a reasonable chat title, but for a group (where it's just
    # one participant) or an outgoing message (where it's the local user) it
    # would mislabel the conversation, so fall back to the stable chat id instead.
    is_representative_of_chat = not is_group and not source.IsFromMe
    conversation_title = (
        sender_display_name if (is_representative_of_chat and sender_display_name) else None
    ) or provider_chat_id.split("@", 1)[0]
    conversation_payload = NeonizeConversationPayload(
        provider_chat_id=provider_chat_id,
        title=conversation_title,
        conversation_type=conversation_type,
    )
    message_payload = NeonizeMessagePayload(
        provider_message_id=info.ID,
        provider_chat_id=provider_chat_id,
        provider_timestamp=provider_timestamp,
        direction=direction,
        sender_identifier=sender_identifier,
        sender_display_name=sender_display_name,
        body=body,
        snippet=snippet,
        message_type=message_type,
        attachment_summary=attachment_summary,
        referenced_users=whatsapp_trigger_events.referenced_users(event.Message),
        has_content=whatsapp_trigger_events.has_content(event.Message),
    )
    return conversation_payload, message_payload


def _translate_history_sync(event: Any) -> Iterator[tuple[NeonizeConversationPayload, NeonizeMessagePayload]]:
    """Translate a one-time HistorySyncEv payload into normalized conversation/message payloads.

    WhatsApp decides how much backlog to push here; there is no way to request a specific
    date range from Neonize, so this is a best-effort seed for the "last 3 days" experience.
    """
    for conversation in event.Data.conversations:
        try:
            provider_chat_id = conversation.ID
            if not provider_chat_id:
                continue
            is_group = provider_chat_id.endswith("@g.us")
            title = conversation.name or conversation.displayName or provider_chat_id.split("@", 1)[0]
            conversation_type = (
                models.WhatsAppConversationType.GROUP if is_group else models.WhatsAppConversationType.DIRECT
            )
            conversation_payload = NeonizeConversationPayload(
                provider_chat_id=provider_chat_id,
                title=title,
                conversation_type=conversation_type,
                unread_count=conversation.unreadCount or 0,
            )
        except Exception:
            logger.warning("Skipping malformed WhatsApp history-sync conversation entry.")
            continue

        for history_message in conversation.messages:
            try:
                web_message = history_message.message
                key = web_message.key
                if not key.ID:
                    continue
                body, snippet, message_type, attachment_summary = _describe_message(web_message.message)
                provider_timestamp = time_utils.epoch_to_utc(web_message.messageTimestamp)
                message_payload = NeonizeMessagePayload(
                    provider_message_id=key.ID,
                    provider_chat_id=provider_chat_id,
                    provider_timestamp=provider_timestamp,
                    direction=(
                        models.WhatsAppMessageDirection.OUTGOING
                        if key.fromMe
                        else models.WhatsAppMessageDirection.INCOMING
                    ),
                    sender_identifier=key.participant or provider_chat_id,
                    sender_display_name=web_message.pushName or None,
                    body=body,
                    snippet=snippet,
                    message_type=message_type,
                    attachment_summary=attachment_summary,
                )
            except Exception:
                logger.warning("Skipping malformed WhatsApp history-sync message entry.")
                continue
            yield conversation_payload, message_payload


def _update_account_status(
    account_id: int,
    status: models.WhatsAppConnectionStatus,
    *,
    error: str | None = None,
) -> None:
    """Persist a connection-status transition for one WhatsApp account."""
    db = database.SessionLocal()
    try:
        account = db.query(models.WhatsAppAccount).filter(models.WhatsAppAccount.id == account_id).first()
        if account is None:
            return
        account.connection_status = status
        account.last_sync_error = error
        db.commit()
    finally:
        db.close()


def _update_setup_session_pairing(
    setup_session_id: int,
    *,
    state: models.WhatsAppPairingState,
    message: str,
    pairing_qr_payload: str | None = None,
    session_database_path: str | None = None,
) -> None:
    """Persist a pairing-state transition directly from the live pairing thread."""
    db = database.SessionLocal()
    try:
        session = (
            db.query(models.WhatsAppSetupSession).filter(models.WhatsAppSetupSession.id == setup_session_id).first()
        )
        if session is None:
            return
        session.pairing_state = state
        session.pairing_message = message
        if pairing_qr_payload is not None:
            session.pairing_qr_payload = pairing_qr_payload
        if session_database_path is not None:
            session.session_database_path = session_database_path
        session.expires_at = time_utils.utcnow() + _PairingRegistry.KEEPALIVE_EXTENSION
        db.commit()
    finally:
        db.close()


class NeonizeConnector:
    """Owns one Neonize client: either a durable per-account connection or a transient pairing session."""

    def __init__(
        self,
        *,
        session_database_path: str,
        client_uuid: str,
        account_id: int | None = None,
        setup_session_id: int | None = None,
        client_factory: Callable[[str, str], Any] | None = None,
    ) -> None:
        """Bind this connector to either a durable account or a transient setup session."""
        if account_id is None and setup_session_id is None:
            raise ValueError("NeonizeConnector requires an account_id or a setup_session_id.")
        self.session_database_path = session_database_path
        self.client_uuid = client_uuid
        self.account_id = account_id
        self.setup_session_id = setup_session_id
        self._client_factory = client_factory or _default_client_factory
        self.client: Any = None
        # The linked account's own user ids (phone-number JID and LID), for
        # recognising mentions of it (JAN-361); filled on connect.
        self.own_users: frozenset[str] = frozenset()

    def connect_and_run(self) -> None:
        """Blocking: open the Neonize connection and dispatch events until disconnected."""
        try:
            self.client = self._client_factory(self.session_database_path, self.client_uuid)
            self._register_handlers(self.client)
            self.client.connect()
        except Exception as exc:
            logger.warning("Neonize connector '%s' failed: %s", self.client_uuid, exc)
            if self.setup_session_id is not None:
                _update_setup_session_pairing(
                    self.setup_session_id,
                    state=models.WhatsAppPairingState.FAILED,
                    message=f"Live pairing failed: {exc}",
                )
            if self.account_id is not None:
                _update_account_status(self.account_id, models.WhatsAppConnectionStatus.DEGRADED, error=str(exc))
            raise

    def stop(self) -> None:
        """Best-effort disconnect, safe to call from another thread."""
        if self.client is not None:
            try:  # noqa: SIM105
                self.client.disconnect()
            except Exception:
                pass

    def _register_handlers(self, client: Any) -> None:
        from neonize.events import ConnectedEv, DisconnectedEv, HistorySyncEv, LoggedOutEv, MessageEv, PairStatusEv

        client.event(ConnectedEv)(self._on_connected)
        client.event(DisconnectedEv)(self._on_disconnected)
        client.event(LoggedOutEv)(self._on_logged_out)
        if self.account_id is not None:
            client.event(MessageEv)(self._on_message)
            client.event(HistorySyncEv)(self._on_history_sync)
        if self.setup_session_id is not None:
            client.qr(self._on_qr)
            client.event(PairStatusEv)(self._on_pair_status)

    def _on_connected(self, client: Any, event: Any) -> None:
        del event
        if self.account_id is not None:
            _update_account_status(self.account_id, models.WhatsAppConnectionStatus.CONNECTED)
            self.own_users = whatsapp_trigger_events.own_users(client)
        if self.setup_session_id is not None:
            _update_setup_session_pairing(
                self.setup_session_id,
                state=models.WhatsAppPairingState.PAIRED,
                message="Pairing complete.",
                session_database_path=self.session_database_path,
            )
            # Pairing's only job is to link the device; hand off to the durable
            # per-account connector started once setup is finished. Disconnect from
            # a separate thread since this callback runs on neonize's own
            # event-dispatch thread, which may not tolerate reentrant teardown.
            threading.Thread(target=self.stop, daemon=True).start()

    def _on_disconnected(self, client: Any, event: Any) -> None:
        del client, event
        if self.account_id is not None:
            _update_account_status(
                self.account_id, models.WhatsAppConnectionStatus.DEGRADED, error="Disconnected from WhatsApp."
            )

    def _on_logged_out(self, client: Any, event: Any) -> None:
        del client, event
        if self.account_id is not None:
            _update_account_status(
                self.account_id,
                models.WhatsAppConnectionStatus.DISCONNECTED,
                error="Logged out of WhatsApp on the linked device.",
            )

    def _on_qr(self, client: Any, qr_bytes: bytes) -> None:
        del client
        if self.setup_session_id is None:
            return
        try:
            payload = _qr_bytes_to_data_uri(qr_bytes)
        except Exception as exc:
            logger.warning("Failed to render Neonize QR payload: %s", exc)
            return
        _update_setup_session_pairing(
            self.setup_session_id,
            state=models.WhatsAppPairingState.CODE_READY,
            message="Scan the QR code with WhatsApp on your phone.",
            pairing_qr_payload=payload,
        )

    def _on_pair_status(self, client: Any, event: Any) -> None:
        del client
        if self.setup_session_id is None:
            return
        from neonize.events import PairStatusEv

        if getattr(event, "Status", None) == PairStatusEv.SUCCESS:
            _update_setup_session_pairing(
                self.setup_session_id,
                state=models.WhatsAppPairingState.WAITING,
                message="Pairing accepted. Finishing connection setup.",
            )
        else:
            error = getattr(event, "Error", "") or "unknown error"
            _update_setup_session_pairing(
                self.setup_session_id,
                state=models.WhatsAppPairingState.FAILED,
                message=f"Pairing failed: {error}.",
            )

    def _on_message(self, client: Any, event: Any) -> None:
        try:
            translated = _translate_live_message(event)
        except Exception as exc:
            logger.warning("Failed to translate WhatsApp message event: %s", exc)
            return
        if translated is None:
            return
        if not self.own_users:  # messages can arrive before ConnectedEv; mentions need the identity
            self.own_users = whatsapp_trigger_events.own_users(client, quiet=True)
        self._ingest(*translated, live=True)

    def _on_history_sync(self, client: Any, event: Any) -> None:
        del client
        try:
            for conversation_payload, message_payload in _translate_history_sync(event):
                self._ingest(conversation_payload, message_payload)
        except Exception as exc:
            logger.warning("Failed to process WhatsApp history-sync event: %s", exc)

    def _ingest(
        self,
        conversation_payload: NeonizeConversationPayload,
        message_payload: NeonizeMessagePayload,
        *,
        live: bool = False,
    ) -> None:
        """Persist one message; a new, live, incoming one is also published as trigger events (JAN-361)."""
        if self.account_id is None:
            return
        from janus.services.whatsapp_service import WhatsAppService

        db = database.SessionLocal()
        try:
            account = db.query(models.WhatsAppAccount).filter(models.WhatsAppAccount.id == self.account_id).first()
            if account is None or not account.is_enabled:
                return
            user = db.get(models.User, account.user_id)
            if user is None:
                return
            service = WhatsAppService(db)
            is_new = not service.message_exists(account, message_payload.provider_message_id)
            conversation = service.upsert_conversation(user, account, conversation_payload)
            message = service.upsert_message(user, account, conversation.id, message_payload)
            account.connection_status = models.WhatsAppConnectionStatus.CONNECTED
            account.last_sync_error = None
            account.last_synced_at = time_utils.utcnow()
            db.commit()
            incoming = message_payload.direction == models.WhatsAppMessageDirection.INCOMING
            if live and is_new and incoming and message_payload.has_content:
                try:  # a publish failure must never lose the stored message
                    own, referenced = self.own_users, message_payload.referenced_users
                    whatsapp_trigger_events.publish(db, account, conversation, message, own=own, referenced=referenced)
                except Exception as exc:
                    db.rollback()
                    logger.warning("Failed to publish WhatsApp trigger events for account %s: %s", self.account_id, exc)
        except Exception as exc:
            db.rollback()
            logger.warning("Failed to persist WhatsApp message for account %s: %s", self.account_id, exc)
        finally:
            db.close()


class _PairingRegistry:
    """Tracks live, transient pairing-mode connectors so they can be cancelled/replaced."""

    KEEPALIVE_EXTENSION = timedelta(minutes=15)

    def __init__(self) -> None:
        """Initialize an empty registry guarded by a lock."""
        self._lock = threading.Lock()
        self._connectors: dict[int, NeonizeConnector] = {}

    def start(self, setup_session_id: int, session_database_path: str) -> None:
        """Start (or restart) a pairing-mode connector for one setup session."""
        self.stop(setup_session_id)
        connector = NeonizeConnector(
            session_database_path=session_database_path,
            client_uuid=f"setup-{setup_session_id}",
            setup_session_id=setup_session_id,
        )
        with self._lock:
            self._connectors[setup_session_id] = connector

        def _run() -> None:
            try:
                connector.connect_and_run()
            except Exception:
                logger.warning(
                    "Pairing connector for setup session %s exited with an error.",
                    setup_session_id,
                    exc_info=True,
                )
            finally:
                with self._lock:
                    if self._connectors.get(setup_session_id) is connector:
                        self._connectors.pop(setup_session_id, None)

        thread = threading.Thread(
            target=_run,
            daemon=True,
            name=f"whatsapp-pairing-{setup_session_id}",
        )
        thread.start()

    def stop(self, setup_session_id: int) -> None:
        """Stop and forget a pairing-mode connector, if one is running."""
        with self._lock:
            connector = self._connectors.pop(setup_session_id, None)
        if connector is not None:
            connector.stop()


_pairing_registry = _PairingRegistry()


class NeonizeAdapter:
    """Setup-time Neonize surface: capability checks, pairing lifecycle, and session import/export."""

    def __init__(self, account: models.WhatsAppAccount | None = None) -> None:
        """Bind the adapter to an optional stored WhatsApp account configuration."""
        self.account = account

    def test_connection(self) -> tuple[bool, str]:
        """Return whether the account looks usable for a Neonize-backed connection."""
        if self.account is None:
            return False, "No WhatsApp account is configured."
        if not (self.account.session_data or self.account.session_database_path):
            return False, "No Neonize session data or session database path is configured."
        import_error = self._get_neonize_import_error()
        if import_error is not None:
            return False, import_error
        return True, "Neonize session configuration is present."

    def get_pairing_capability(self) -> NeonizePairingCapability:
        """Return whether the current runtime can attempt live in-app pairing."""
        import_error = self._get_neonize_import_error()
        if import_error is not None:
            return NeonizePairingCapability(
                supported=False,
                message=f"{import_error} Import an existing session instead.",
            )
        return NeonizePairingCapability(
            supported=True,
            message="Live in-app Neonize pairing is available. Scan the QR code with WhatsApp on your phone.",
        )

    def begin_pairing(self, setup_session: models.WhatsAppSetupSession) -> NeonizePairingSnapshot:
        """Start live QR pairing for a setup session when the runtime supports it."""
        capability = self.get_pairing_capability()
        if not capability.supported:
            return NeonizePairingSnapshot(
                state=models.WhatsAppPairingState.UNSUPPORTED,
                message=capability.message,
                show_fallback_import=True,
            )

        session_database_path = setup_session.session_database_path or str(
            _session_dir() / f"setup-{setup_session.id}.db"
        )
        try:
            _pairing_registry.start(setup_session.id, session_database_path)
        except Exception as exc:
            return NeonizePairingSnapshot(
                state=models.WhatsAppPairingState.FAILED,
                message=f"Failed to start live pairing: {exc}",
                show_fallback_import=True,
            )
        return NeonizePairingSnapshot(
            state=models.WhatsAppPairingState.STARTING,
            message="Waiting for a QR code from WhatsApp.",
            session_database_path=session_database_path,
            show_fallback_import=True,
        )

    def poll_pairing_state(self, setup_session: models.WhatsAppSetupSession) -> NeonizePairingSnapshot:
        """Return the current pairing snapshot, as last written by the live pairing thread."""
        return NeonizePairingSnapshot(
            state=setup_session.pairing_state,
            message=setup_session.pairing_message or "Waiting for live Neonize pairing to complete.",
            pairing_code=setup_session.pairing_code,
            pairing_qr_payload=setup_session.pairing_qr_payload,
            session_database_path=setup_session.session_database_path,
            show_fallback_import=setup_session.show_fallback_import,
        )

    def cancel_pairing(self, setup_session: models.WhatsAppSetupSession) -> None:
        """Cancel an in-flight pairing attempt."""
        _pairing_registry.stop(setup_session.id)

    def export_session_artifact(self, setup_session: models.WhatsAppSetupSession) -> NeonizeSessionArtifact:
        """Return session material captured during setup."""
        return NeonizeSessionArtifact(
            session_data=security_service.decrypt(setup_session.session_data) if setup_session.session_data else None,
            session_database_path=setup_session.session_database_path,
        )

    def import_session_artifact(
        self,
        *,
        session_data: str | None,
        session_database_path: str | None,
    ) -> NeonizeSessionArtifact:
        """Validate one fallback session artifact."""
        normalized_data = (session_data or "").strip() or None
        normalized_path = (session_database_path or "").strip() or None
        if not (normalized_data or normalized_path):
            raise ValueError("Provide session_data or session_database_path.")
        return NeonizeSessionArtifact(
            session_data=normalized_data,
            session_database_path=normalized_path,
        )

    def _get_neonize_import_error(self) -> str | None:
        """Return a user-facing import failure message when Neonize is unavailable."""
        return neonize_import_error()
