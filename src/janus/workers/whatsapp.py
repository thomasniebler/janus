"""Per-account WhatsApp connector worker: a persistent Neonize connection."""

import threading
import uuid
from datetime import UTC, datetime

from janus import database, models, time_utils
from janus.whatsapp_neonize import NeonizeConnector, NeonizeUnavailableError, _session_dir, neonize_import_error
from janus.worker_manager import PermanentWorkerError, worker_manager

_live_connectors_lock = threading.Lock()
_live_connectors: dict[int, NeonizeConnector] = {}


def whatsapp_connector_worker(account_id: int) -> None:
    """Blocking worker body: open and run a persistent Neonize connection for one account."""
    db = database.SessionLocal()
    try:
        account = db.query(models.WhatsAppAccount).filter(models.WhatsAppAccount.id == account_id).first()
        if account is None or not account.is_enabled:
            return
        if not (account.session_data or account.session_database_path):
            return
        session_database_path = account.session_database_path or str(_session_dir() / f"account-{account_id}.db")
        # Neonize resumes a paired session by reusing the same sqlite path *and*
        # client UUID it was paired under; fall back to the legacy account-scoped
        # UUID for accounts created before client_uuid was persisted.
        client_uuid = account.client_uuid or f"account-{account_id}"
    finally:
        db.close()

    # A native-library load failure is permanent for this process (JAN-172): stop the
    # supervisor from retrying it, since re-touching the broken library can segfault
    # the whole Janus process. The crash handler marks the account DEGRADED with this message.
    import_error = neonize_import_error()
    if import_error is not None:
        raise PermanentWorkerError(import_error)

    connector = NeonizeConnector(
        session_database_path=session_database_path,
        client_uuid=client_uuid,
        account_id=account_id,
    )
    with _live_connectors_lock:
        _live_connectors[account_id] = connector
    try:
        connector.connect_and_run()
    except NeonizeUnavailableError as exc:
        raise PermanentWorkerError(str(exc)) from exc
    finally:
        with _live_connectors_lock:
            # Only remove this worker's own connector: if a restart raced with this
            # cleanup, a newer connector may already be registered under the same
            # account_id, and popping unconditionally would orphan it from
            # stop_account_connector/restart_connector forever (see _PairingRegistry
            # for the equivalent, already-correct pattern for setup-mode sessions).
            if _live_connectors.get(account_id) is connector:
                _live_connectors.pop(account_id, None)


def start_account_connector(account: models.WhatsAppAccount) -> None:
    """Register and launch the persistent Neonize connector thread for one account.

    Skips accounts with no paired session material: starting a connector doomed to
    fail its first connect would just make worker_manager restart it forever with
    backoff, repeatedly flipping the account to DEGRADED for no reason.
    """
    if not (account.session_data or account.session_database_path):
        return
    worker_manager.start(f"whatsapp:{account.id}", whatsapp_connector_worker, args=(account.id,))


def stop_account_connector(account_id: int) -> None:
    """Disconnect and stop the persistent Neonize connector thread for one account."""
    with _live_connectors_lock:
        connector = _live_connectors.pop(account_id, None)
    if connector is not None:
        connector.stop()
    worker_manager.stop(f"whatsapp:{account_id}")


class WhatsAppNotConnectedError(RuntimeError):
    """The account has no live connection in this process, so nothing can be sent (JAN-360)."""


def send_text_message(account_id: int, chat_jid: str, text: str) -> tuple[str, datetime]:
    """Send ``text`` to ``chat_jid`` over the account's live connector (JAN-360).

    Returns the provider message id and the send time (naive UTC). Raises
    :class:`WhatsAppNotConnectedError` when the account has no live connection.
    """
    with _live_connectors_lock:
        connector = _live_connectors.get(account_id)
    client = getattr(connector, "client", None)
    if client is None:
        raise WhatsAppNotConnectedError(
            "this WhatsApp account is not connected right now; check Settings -> Connections -> Messaging and try again"
        )
    response = client.send_message(chat_jid, text)
    message_id = str(getattr(response, "ID", "") or "") or f"janus-{uuid.uuid4().hex}"
    raw_timestamp = getattr(response, "Timestamp", None)
    sent_at = time_utils.utcnow()
    if isinstance(raw_timestamp, int | float) and raw_timestamp > 0:
        # Seconds, or milliseconds as in MessageInfo (JAN-346).
        seconds = raw_timestamp / 1000 if raw_timestamp > 10**11 else raw_timestamp
        sent_at = datetime.fromtimestamp(seconds, tz=UTC).replace(tzinfo=None)
    return message_id, sent_at
