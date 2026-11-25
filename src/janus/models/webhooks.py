"""Owner-scoped inbound webhook endpoints (JAN-406)."""

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text

from janus import database
from janus.time_utils import utcnow


class WebhookEndpoint(database.Base):
    """A URL an outside service can POST to, which emits ``webhook.received`` for its owner.

    The URL's key is stored only as a SHA-256 hash (it is shown once, at creation);
    the HMAC secret is Fernet-encrypted, since verifying a signature needs it, and is
    also shown only once (at creation and after a rotation).
    """

    __tablename__ = "webhook_endpoints"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(80), nullable=False)
    key_hash = Column(String(64), nullable=False, unique=True)
    #: The first characters of the key, so the user can tell endpoints apart.
    key_prefix = Column(String(12), nullable=False)
    encrypted_secret = Column(Text, nullable=False)
    enabled = Column(Boolean, default=True, server_default="1", nullable=False)
    received_count = Column(Integer, default=0, server_default="0", nullable=False)
    last_received_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


__all__ = ["WebhookEndpoint"]
