"""Mail: IMAP accounts, stored email records, classification rules and clarification requests."""

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from janus import database
from janus.models.enums import ClarificationStatus, ConfidenceLevel, MonitorStatus, Priority
from janus.time_utils import utcnow


class EmailAccount(database.Base):
    """Database model for an email account."""

    __tablename__ = "email_accounts"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String, unique=True, index=True)
    email = Column(String, unique=True, index=True)
    imap_server = Column(String)
    imap_port = Column(Integer, default=993)
    username = Column(String)
    password = Column(String)
    is_active = Column(Boolean, default=True)
    monitor_status = Column(Enum(MonitorStatus), nullable=True)
    last_heartbeat = Column(DateTime, nullable=True)
    last_error = Column(String, nullable=True)
    uid_validity = Column(BigInteger, nullable=True)
    last_uid = Column(BigInteger, default=0, nullable=False)
    # "password" (classic IMAP login) or "google"/"microsoft" (IMAP XOAUTH2 with a connected account).
    auth_type = Column(String, default="password", server_default="password", nullable=False)
    connection_id = Column(Integer, ForeignKey("oauth_connections.id"), nullable=True)
    # Outlook (Graph) mailboxes: the newest receivedDateTime seen, as an ISO timestamp.
    sync_cursor = Column(String, nullable=True)

    emails = relationship("EmailRecord", back_populates="account")


class EmailRecord(database.Base):
    """Database model for a processed email."""

    __tablename__ = "emails"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("email_accounts.id"))
    message_id = Column(String, index=True)
    imap_uid = Column(BigInteger, nullable=True)
    # Outlook (Graph) mail has no IMAP UID; the Graph message id is kept here.
    graph_message_id = Column(String, nullable=True)
    subject = Column(String)
    sender = Column(String)
    received_at = Column(DateTime, default=utcnow)
    snippet = Column(String)
    body = Column(Text, nullable=True)
    priority = Column(Enum(Priority), default=Priority.DIGEST)
    reasoning = Column(String, nullable=True)
    classification_confidence = Column(Enum(ConfidenceLevel), nullable=True)
    is_read = Column(Boolean, default=False)
    notified = Column(Boolean, default=False)

    account = relationship("EmailAccount", back_populates="emails")

    __table_args__ = (UniqueConstraint("account_id", "imap_uid", name="uq_email_account_uid"),)


class Rule(database.Base):
    """Database model for a prioritization rule."""

    __tablename__ = "rules"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String)
    pattern = Column(String)  # Regex or keyword
    field = Column(String)  # sender, subject, body
    priority = Column(Enum(Priority), default=Priority.IMPORTANT)
    is_active = Column(Boolean, default=True)
    order = Column(Integer, default=0, nullable=False)


class EmailClarificationRequest(database.Base):
    """A reviewable clarification item created from manual email relabeling."""

    __tablename__ = "email_clarification_requests"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    email_id = Column(Integer, ForeignKey("emails.id"), nullable=False, index=True)
    applied_rule_id = Column(Integer, ForeignKey("rules.id"), nullable=True)
    previous_priority = Column(Enum(Priority), nullable=False)
    requested_priority = Column(Enum(Priority), nullable=False)
    suggested_rule_name = Column(String, nullable=False)
    suggested_rule_pattern = Column(String, nullable=False)
    suggested_rule_field = Column(String, nullable=False)
    suggested_rule_priority = Column(Enum(Priority), nullable=False)
    suggestion_confidence = Column(Enum(ConfidenceLevel), nullable=False)
    suggestion_reason = Column(String, nullable=False)
    status = Column(Enum(ClarificationStatus), default=ClarificationStatus.PENDING, nullable=False, index=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    user = relationship("User")
    email = relationship("EmailRecord")
    applied_rule = relationship("Rule")
