"""Regression tests for outgoing email service behavior."""

import smtplib

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from janus import models, security
from janus.services.email_service import EmailService


def _make_account(
    db,
    *,
    email: str = "sender@example.com",
    username: str = "sender-user",
    imap_server: str = "imap.example.com",
) -> models.EmailAccount:
    """Create and persist an active email account, owned by a fresh user, for SMTP tests."""
    owner = models.User(username=f"owner-{email}", hashed_password="x")
    db.add(owner)
    db.flush()
    account = models.EmailAccount(
        user_id=owner.id,
        name=f"Mail {email}",
        email=email,
        username=username,
        imap_server=imap_server,
        imap_port=993,
        password=security.security_service.encrypt("smtp-secret"),
        is_active=True,
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


def test_resolve_smtp_host_prefers_explicit_override(db):
    """Explicit SMTP server overrides should take precedence over inference."""
    account = _make_account(db, imap_server="outlook.office365.com")

    host = EmailService(db)._resolve_smtp_host(account, "smtp.custom.example")

    assert host == "smtp.custom.example"


def test_resolve_smtp_host_maps_outlook_office365_accounts(db):
    """Office365/Outlook IMAP hosts should map to the correct SMTP server."""
    account = _make_account(
        db,
        email="sender@contoso.com",
        username="sender@contoso.com",
        imap_server="outlook.office365.com",
    )

    host = EmailService(db)._resolve_smtp_host(account, None)

    assert host == "smtp.office365.com"


def test_resolve_smtp_host_uses_safe_imap_prefix_fallback(db):
    """Unknown IMAP hosts should keep the conservative imap->smtp prefix fallback."""
    account = _make_account(db, imap_server="imap.example.com")

    host = EmailService(db)._resolve_smtp_host(account, None)

    assert host == "smtp.example.com"


@pytest.mark.parametrize(
    ("smtp_attr", "failing_stage", "error"),
    [
        ("SMTP", "connect", OSError("connection timed out")),
        ("SMTP", "login", smtplib.SMTPAuthenticationError(535, b"bad credentials")),
    ],
)
def test_send_email_wraps_transport_errors_and_logs_warning(db, monkeypatch, smtp_attr, failing_stage, error):
    """SMTP and network failures should be logged once and surfaced as a stable ValueError."""
    account = _make_account(db)
    logged: list[tuple[object, str, str, dict[str, object] | None]] = []
    owners: list[int | None] = []

    class FailingSMTP:
        def __init__(self, host, port, timeout):
            if failing_stage == "connect":
                raise error

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def ehlo(self):
            return None

        def starttls(self):
            return None

        def login(self, username, password):
            if failing_stage == "login":
                raise error

        def send_message(self, message, from_addr, to_addrs):
            raise AssertionError("send_message should not be reached when the transport already failed")

    monkeypatch.setattr(f"janus.services.email_service.smtplib.{smtp_attr}", FailingSMTP)
    monkeypatch.setattr(
        "janus.services.email_service.event_logger.log",
        lambda *args, user_id=None: (logged.append(args), owners.append(user_id)),
    )

    with pytest.raises(ValueError, match=EmailService._EMAIL_SEND_FAILURE_MESSAGE):
        EmailService(db).send_email(
            user_id=account.user_id,
            to=["recipient@example.com"],
            subject="Hello from Janus",
            body="This is a test email.",
            account_id=account.id,
        )

    assert len(logged) == 1
    assert owners == [account.user_id], "the warning belongs to the sending account's owner (JAN-272)"
    event_type, source, message, details = logged[0]
    assert event_type == models.EventType.WARNING
    assert source == "EmailService"
    assert message == "Failed to send outgoing email."
    assert details == {
        "account_id": account.id,
        "smtp_server": "smtp.example.com",
        "smtp_port": 587,
        "use_ssl": False,
        "error_type": type(error).__name__,
        "error": str(error),
    }


def test_serialize_emails_skips_topic_badges_when_topic_tables_are_unavailable():
    """Email payloads should still serialize on pre-topic-tracking schemas."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    session_local = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    try:
        models.User.__table__.create(bind=engine)
        models.EmailAccount.__table__.create(bind=engine)
        models.EmailRecord.__table__.create(bind=engine)

        db = session_local()
        user = models.User(username="legacy", hashed_password="hash")
        db.add(user)
        db.flush()
        account = models.EmailAccount(name="Legacy", email="legacy@test.com", password="hash", user_id=user.id)
        db.add(account)
        db.flush()

        email = models.EmailRecord(
            account_id=account.id,
            message_id="legacy-preview",
            subject="Legacy preview",
            sender="legacy@test.com",
            snippet="Preview text",
            priority=models.Priority.DIGEST,
        )
        db.add(email)
        db.commit()

        payload = EmailService(db).serialize_emails([email], user=user)

        assert payload == [
            {
                "id": email.id,
                "account_id": account.id,
                "message_id": "legacy-preview",
                "imap_uid": None,
                "graph_message_id": None,
                "subject": "Legacy preview",
                "sender": "legacy@test.com",
                "received_at": email.received_at,
                "snippet": "Preview text",
                "body": None,
                "priority": models.Priority.DIGEST,
                "reasoning": None,
                "classification_confidence": None,
                "is_read": False,
                "notified": False,
                "topics": [],
            }
        ]
    finally:
        db.close()
        engine.dispose()


def test_oauth_mailbox_cannot_send_and_is_skipped_for_the_implicit_sender(db):
    """Gmail/Outlook mailboxes have no SMTP password, so they are never picked to send mail."""
    account = _make_account(db, email="oauth@example.com")
    account.auth_type = "google"
    account.password = None
    db.commit()
    service = EmailService(db)

    with pytest.raises(ValueError, match="cannot send mail"):
        service._resolve_outgoing_account_for_user(account.id, account.user_id)
    with pytest.raises(ValueError, match="no active email accounts"):
        service._resolve_outgoing_account_for_user(None, account.user_id)


def test_update_ignores_server_and_identity_fields_on_oauth_mailboxes(db):
    """A PATCH cannot repoint an OAuth mailbox's server or identity (its token would follow)."""
    account = _make_account(db, email="oauth2@example.com")
    account.auth_type = "microsoft"
    db.commit()

    updated = EmailService(db).update_account_for_user(
        account.id, account.user_id, {"imap_server": "evil.example.com", "username": "x", "name": "Renamed"}
    )

    assert updated.imap_server == "imap.example.com"
    assert updated.username == "sender-user"
    assert updated.name == "Renamed"
