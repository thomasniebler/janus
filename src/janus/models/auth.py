"""Auth state: revoked sessions and OAuth flow state."""

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)

from janus import database
from janus.time_utils import utcnow


class RevokedSession(database.Base):
    """Server-side record of an invalidated JWT session."""

    __tablename__ = "revoked_sessions"

    id = Column(Integer, primary_key=True)
    session_id = Column(String, unique=True, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    reason = Column(String, nullable=False)
    revoked_at = Column(DateTime, default=utcnow, server_default=func.now(), nullable=False)
    expires_at = Column(DateTime, nullable=True)


class OAuthState(database.Base):
    """Short-lived CSRF state token for OAuth2 authorization flows."""

    __tablename__ = "oauth_states"

    id = Column(Integer, primary_key=True, index=True)
    state = Column(String, unique=True, index=True, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    code_verifier = Column(String, nullable=True)
    # Provider-specific flow state that must survive the redirect (Microsoft's MSAL auth-code flow).
    payload = Column(Text, nullable=True)
    mcp_server_id = Column(Integer, ForeignKey("mcp_servers.id", ondelete="CASCADE"), nullable=True, index=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)


class OAuthConnection(database.Base):
    """One user's connected Google or Microsoft account (the "Connect ..." buttons in Settings).

    ``credentials`` is Fernet-encrypted: a Google authorized-user JSON blob, or a serialized MSAL
    token cache for Microsoft. Calendar sources and mailboxes reference the connection instead of
    holding their own copies of the sign-in.
    """

    __tablename__ = "oauth_connections"
    __table_args__ = (UniqueConstraint("user_id", "provider", name="uq_oauth_connections_user_provider"),)

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    provider = Column(String, nullable=False)  # "google" | "microsoft"
    account_email = Column(String, nullable=True)
    credentials = Column(Text, nullable=False)  # Encrypted at rest
    scopes = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class DeviceToken(database.Base):
    """A scoped, revocable token for a script or device that may use only the action inbox (see the spec).

    The plaintext is shown once at creation; only its SHA-256 and a short display prefix are stored.
    """

    __tablename__ = "device_tokens"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String(80), nullable=False)
    token_hash = Column(String(64), unique=True, nullable=False)
    token_prefix = Column(String(12), nullable=False)
    created_at = Column(DateTime, default=utcnow, server_default=func.now(), nullable=False)
    last_used_at = Column(DateTime, nullable=True)
    revoked_at = Column(DateTime, nullable=True)
