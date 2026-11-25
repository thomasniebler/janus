"""Telegram bot connector (JAN-405): the user's own bot, linked to the user's own chat.

The user creates a bot with @BotFather and gives Janus its token. Janus checks it
(``getMe``), stores it Fernet-encrypted, and shows a one-time pairing code. The
user sends ``/start <code>`` to the bot from their own Telegram; that chat
becomes the only one Janus listens to and writes to. Everyone else is ignored.

* **Inbound:** a supervised worker (``workers/telegram.py``) long-polls
  ``getUpdates`` and calls :func:`handle_update`. A text message from the linked
  chat emits ``telegram.message_received`` for the bot's owner, as untrusted
  event data like every event.
* **Outbound:** :func:`send_to_owner` sends text to the linked chat only, at
  most :data:`MAX_SENT_PER_DAY` messages a day (the ``telegram.notify`` tool).

Telegram's Bot API base URL is a setting (``telegram_api_base_url``) so the e2e
backend can use a local fake.
"""

from __future__ import annotations

import logging
import re
import secrets
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy.orm import Session

from janus import config, models, security, time_utils, trigger_definition_service
from janus.trigger_events import TriggerEvent

logger = logging.getLogger(__name__)

POLL_TIMEOUT_SECONDS = 25
MAX_TEXT_CHARS = 4000
MAX_SENT_PER_DAY = 20
MAX_BODY_CHARS = 4000
_TOKEN_CHARS = set("0123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_-")


_TOKEN_IN_URL = re.compile(r"/bot[0-9]+:[A-Za-z0-9_-]+")


class _RedactBotTokens(logging.Filter):
    """Redact bot tokens from log records: the Bot API puts the token in the URL path, and httpx logs URLs at INFO."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if _TOKEN_IN_URL.search(message):
            record.msg, record.args = _TOKEN_IN_URL.sub("/bot<redacted>", message), ()
        return True


def _install_log_redaction() -> None:
    for name in ("httpx", "httpcore"):
        target = logging.getLogger(name)
        if not any(isinstance(existing, _RedactBotTokens) for existing in target.filters):
            target.addFilter(_RedactBotTokens())


_install_log_redaction()


class TelegramError(ValueError):
    """A Telegram request refused or failed."""


def _api(token: str, method: str, payload: dict[str, Any] | None = None, *, timeout: float = 15.0) -> Any:
    """Call one Bot API method and return its ``result``."""
    base = config.settings.telegram_api_base_url.rstrip("/")
    try:
        response = httpx.post(f"{base}/bot{token}/{method}", json=payload or {}, timeout=timeout)
    except httpx.TimeoutException as exc:
        raise TelegramError("Telegram did not answer in time") from exc
    except httpx.HTTPError as exc:
        raise TelegramError("Telegram could not be reached") from exc
    try:
        body = response.json()
    except ValueError as exc:
        raise TelegramError(f"Telegram returned HTTP {response.status_code}") from exc
    if not body.get("ok"):
        if response.status_code in (401, 404):
            raise TelegramError("Telegram rejected the bot token")
        raise TelegramError(str(body.get("description") or f"Telegram returned HTTP {response.status_code}")[:200])
    return body.get("result")


def _clean_token(token: str) -> str:
    value = (token or "").strip()
    if ":" not in value or len(value) > 100 or not set(value) <= _TOKEN_CHARS:
        raise TelegramError("that does not look like a bot token from @BotFather (digits, a colon, then letters)")
    return value


def get_bot(db: Session, user: models.User) -> models.TelegramBot | None:
    """The user's bot, if connected."""
    return db.query(models.TelegramBot).filter(models.TelegramBot.user_id == user.id).first()


def connect(db: Session, user: models.User, token: str) -> models.TelegramBot:
    """Check the token with Telegram and (re)connect the user's bot; a new pairing code is issued."""
    value = _clean_token(token)
    me = _api(value, "getMe")
    if not isinstance(me, dict) or not me.get("is_bot"):
        raise TelegramError("that token does not belong to a bot")
    bot = get_bot(db, user) or models.TelegramBot(user_id=user.id)
    bot.encrypted_token = security.security_service.encrypt(value)
    bot.bot_username = str(me.get("username") or "bot")[:64]
    bot.chat_id = None
    bot.pairing_code = f"{secrets.randbelow(10**8):08d}"
    bot.enabled = True
    bot.last_error = None
    bot.last_update_id = None
    db.add(bot)
    db.flush()
    return bot


def disconnect(db: Session, user: models.User) -> None:
    """Forget the user's bot (the worker stops at its next poll)."""
    db.query(models.TelegramBot).filter(models.TelegramBot.user_id == user.id).delete(synchronize_session=False)
    db.flush()


def delete_all_for_user(db: Session, user_id: int) -> None:
    """Delete a user's bot: SQLite does not cascade, and a reused id must not inherit the token."""
    db.query(models.TelegramBot).filter(models.TelegramBot.user_id == user_id).delete(synchronize_session=False)


def token_of(bot: models.TelegramBot) -> str:
    """The decrypted bot token (never returned by the API)."""
    return security.security_service.decrypt(bot.encrypted_token)


def handle_update(db: Session, bot: models.TelegramBot, update: dict[str, Any]) -> str:
    """Process one update; returns what happened (``paired``, ``event``, or ``ignored``)."""
    message = update.get("message") if isinstance(update, dict) else None
    if not isinstance(message, dict):
        return "ignored"
    chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
    chat_id = str(chat.get("id") or "")
    text = message.get("text") if isinstance(message.get("text"), str) else ""
    if bot.chat_id is None:
        if (
            chat.get("type") == "private"
            and bot.pairing_code
            and text.strip()
            in (
                f"/start {bot.pairing_code}",
                bot.pairing_code,
            )
        ):
            bot.chat_id = chat_id
            bot.pairing_code = None
            db.flush()
            _send(bot, "Linked to Janus. Messages you send here can start your Telegram triggers.")
            return "paired"
        return "ignored"
    if chat_id != bot.chat_id or not text:
        return "ignored"
    sender = message.get("from") if isinstance(message.get("from"), dict) else {}
    sent_at = message.get("date")
    received_at = datetime.fromtimestamp(int(sent_at), UTC).isoformat() if isinstance(sent_at, int | float) else ""
    data = {
        "message_id": message.get("message_id"),
        "bot": f"@{bot.bot_username}",
        "sender": " ".join(str(sender.get(k) or "") for k in ("first_name", "last_name")).strip()[:120],
        "received_at": received_at,
        "body": text[:MAX_BODY_CHARS],
    }
    trigger_definition_service.emit_event(
        db,
        bot.user_id,
        TriggerEvent.TELEGRAM_MESSAGE_RECEIVED,
        f"{bot.id}:{bot.chat_id}:{message.get('message_id')}",
        data,
    )
    return "event"


def poll_once(db: Session, bot: models.TelegramBot, *, timeout: int = POLL_TIMEOUT_SECONDS) -> int:
    """Fetch and handle pending updates once; returns how many were handled."""
    offset = (bot.last_update_id + 1) if bot.last_update_id is not None else None
    payload: dict[str, Any] = {"timeout": timeout, "allowed_updates": ["message"]}
    if offset is not None:
        payload["offset"] = offset
    token = token_of(bot)
    updates = _api(token, "getUpdates", payload, timeout=timeout + 10) or []
    # The long poll can take a while: if the user reconnected (a new token) or removed the bot
    # meanwhile, these updates belong to the old bot and its offset must not land on the new one.
    db.expire(bot)
    current = db.query(models.TelegramBot).filter(models.TelegramBot.id == bot.id).first()
    if current is None or token_of(current) != token:
        db.rollback()
        return 0
    for update in updates:
        try:
            handle_update(db, bot, update)
        except Exception as exc:  # noqa: BLE001 - one bad update must not stall the queue
            logger.warning("Telegram update %s for bot %s failed: %s", update.get("update_id"), bot.id, exc)
        update_id = update.get("update_id")
        if isinstance(update_id, int):
            bot.last_update_id = max(update_id, bot.last_update_id or 0)
    bot.last_error = None
    db.commit()
    return len(updates)


def _send(bot: models.TelegramBot, text: str) -> None:
    _api(token_of(bot), "sendMessage", {"chat_id": bot.chat_id, "text": text[:MAX_TEXT_CHARS]})


def send_to_owner(db: Session, user_id: int, text: str) -> None:
    """Send ``text`` to the user's linked chat, within the daily cap. Raises :class:`TelegramError`."""
    bot = db.query(models.TelegramBot).filter(models.TelegramBot.user_id == user_id).first()
    if bot is None or not bot.enabled or not bot.chat_id:
        raise TelegramError("no linked Telegram chat")
    message = (text or "").strip()
    if not message:
        raise TelegramError("the message is empty")
    today = time_utils.utcnow().date()
    if bot.sent_day != today:
        bot.sent_day, bot.sent_today = today, 0
    if bot.sent_today >= MAX_SENT_PER_DAY:
        raise TelegramError(f"the daily limit of {MAX_SENT_PER_DAY} Telegram messages is reached")
    _send(bot, message)
    bot.sent_today += 1
    db.flush()


__all__ = [
    "MAX_SENT_PER_DAY",
    "TelegramError",
    "connect",
    "delete_all_for_user",
    "disconnect",
    "get_bot",
    "handle_update",
    "poll_once",
    "send_to_owner",
    "token_of",
]
