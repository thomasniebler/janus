"""Neonize integration: setup-time pairing and the live per-account connector."""

from __future__ import annotations

import base64
import io
import logging
import secrets
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

from janus import config, database, models, time_utils, whatsapp_trigger_events
from janus.security import security_service
from janus.whatsapp_translate import (
    NeonizeConversationPayload,
    NeonizeMessagePayload,
    translate_history_sync,
    translate_live_message,
)

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
        """Best-effort disconnect from any thread; pairing uses ``stop()``, as ``disconnect()`` leaves it blocking."""
        if self.client is not None:
            stop_client = getattr(self.client, "stop", None) if self.setup_session_id is not None else None
            try:  # noqa: SIM105
                (stop_client or self.client.disconnect)()
            except Exception:
                pass

    def _fail_pairing(self, message: str) -> None:
        """Mark the setup session FAILED and end this pairing connection."""
        if self.setup_session_id is None:
            return
        _update_setup_session_pairing(self.setup_session_id, state=models.WhatsAppPairingState.FAILED, message=message)
        threading.Thread(target=self.stop, daemon=True).start()

    def _register_handlers(self, client: Any) -> None:
        from neonize import events as ev

        client.event(ev.ConnectedEv)(self._on_connected)
        client.event(ev.DisconnectedEv)(self._on_disconnected)
        client.event(ev.LoggedOutEv)(self._on_logged_out)
        client.event(ev.ConnectFailureEv)(self._on_connect_failure)
        if self.account_id is not None:
            client.event(ev.MessageEv)(self._on_message)
            client.event(ev.HistorySyncEv)(self._on_history_sync)
        if self.setup_session_id is not None:
            client.qr(self._on_qr)
            client.event(ev.PairStatusEv)(self._on_pair_status)

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
        # During pairing: the store held an unlinked device, so WhatsApp will never offer a QR code.
        self._fail_pairing("WhatsApp rejected the stored session as logged out; no QR code was offered.")

    def _on_connect_failure(self, client: Any, event: Any) -> None:
        del client
        detail = getattr(event, "Message", "") or f"reason {getattr(event, 'Reason', 'unknown')}"
        message = f"WhatsApp refused the connection: {detail}."
        if self.account_id is not None:
            _update_account_status(self.account_id, models.WhatsAppConnectionStatus.DEGRADED, error=message)
        self._fail_pairing(message)

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
            translated = translate_live_message(event)
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
            for conversation_payload, message_payload in translate_history_sync(event):
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

        # Always a new file: Neonize resumes the first device stored in an existing one instead of
        # offering a QR code, e.g. a stale device left by an older database with colliding ids.
        session_database_path = str(_session_dir() / f"setup-{setup_session.id}-{secrets.token_hex(8)}.db")
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
