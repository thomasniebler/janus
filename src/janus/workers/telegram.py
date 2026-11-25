"""Per-user Telegram bot worker: long-polls the Bot API for the user's own bot (JAN-405)."""

from __future__ import annotations

import time

from janus import database, event_logger, models
from janus.services import telegram_service
from janus.worker_manager import worker_manager

ERROR_BACKOFF_SECONDS = 15


def _label(user_id: int) -> str:
    return f"telegram:{user_id}"


def telegram_bot_worker(user_id: int) -> None:
    """Poll until the bot is gone or switched off; a failed poll is recorded and retried after a pause."""
    label = _label(user_id)
    while True:
        db = database.SessionLocal()
        try:
            bot = db.query(models.TelegramBot).filter(models.TelegramBot.user_id == user_id).first()
            if bot is None or not bot.enabled:
                return
            with event_logger.owned_by(user_id):
                try:
                    started = time.monotonic()
                    handled = telegram_service.poll_once(db, bot)
                    if not handled and time.monotonic() - started < 1:
                        # An endpoint that answers an empty long poll at once must not make this spin.
                        time.sleep(1)
                except telegram_service.TelegramError as exc:
                    db.rollback()
                    bot = db.query(models.TelegramBot).filter(models.TelegramBot.user_id == user_id).first()
                    if bot is None:
                        return
                    bot.last_error = str(exc)
                    db.commit()
                    time.sleep(ERROR_BACKOFF_SECONDS)
        finally:
            db.close()
        worker_manager.heartbeat(label)


def start_bot_worker(user_id: int) -> None:
    """Make sure a polling worker runs for one user's bot.

    A running worker is left alone: it reloads the bot row on every poll, so a new token or a
    reset pairing takes effect on its next request. Restarting it instead could fail while it
    is inside a long poll (``worker_manager.start`` refuses to run two at once).
    """
    if worker_manager.status().get(_label(user_id), {}).get("alive"):
        return
    worker_manager.start(_label(user_id), telegram_bot_worker, args=(user_id,))


def stop_bot_worker(user_id: int) -> None:
    """Stop the polling worker; it also ends by itself once the bot row is gone."""
    worker_manager.stop(_label(user_id))


def start_all_bot_workers() -> None:
    """Start a worker for every enabled bot (Janus startup)."""
    db = database.SessionLocal()
    try:
        user_ids = [
            row.user_id for row in db.query(models.TelegramBot.user_id).filter(models.TelegramBot.enabled.is_(True))
        ]
    finally:
        db.close()
    for user_id in user_ids:
        start_bot_worker(user_id)


__all__ = ["start_all_bot_workers", "start_bot_worker", "stop_bot_worker", "telegram_bot_worker"]
