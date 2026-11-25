"""A user's Telegram bot connection (JAN-405)."""

from sqlalchemy import Boolean, Column, Date, DateTime, ForeignKey, Integer, String, Text

from janus import database
from janus.time_utils import utcnow


class TelegramBot(database.Base):
    """The bot a user connected (one per user) and the private chat it is linked to.

    The bot token is Fernet-encrypted and never returned. Until ``chat_id`` is
    set, the bot answers only the ``/start <pairing_code>`` message that links
    the user's own chat; it ignores everyone else.
    """

    __tablename__ = "telegram_bots"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    encrypted_token = Column(Text, nullable=False)
    bot_username = Column(String(64), nullable=False)
    #: The linked private chat (the user's own), or ``None`` while waiting for pairing.
    chat_id = Column(String(32), nullable=True)
    pairing_code = Column(String(16), nullable=True)
    enabled = Column(Boolean, default=True, server_default="1", nullable=False)
    #: Telegram's update offset: the last update already handled.
    last_update_id = Column(Integer, nullable=True)
    last_error = Column(Text, nullable=True)
    #: The notify tool's daily cap: messages sent on ``sent_day``.
    sent_day = Column(Date, nullable=True)
    sent_today = Column(Integer, default=0, server_default="0", nullable=False)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


__all__ = ["TelegramBot"]
