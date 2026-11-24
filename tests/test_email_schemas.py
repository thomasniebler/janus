"""Regression tests for email schema defaults."""

from datetime import datetime

from janus.models import Priority
from janus.schemas import email as email_schemas


def test_email_record_topics_default_to_a_fresh_list():
    """EmailRecord instances should not share topic badge lists."""
    first = email_schemas.EmailRecord(
        id=1,
        message_id="msg-1",
        sender="alice@example.com",
        subject="First",
        snippet="First email",
        priority=Priority.IMPORTANT,
        received_at=datetime(2026, 4, 10, 8, 0),
        account_id=1,
    )
    second = email_schemas.EmailRecord(
        id=2,
        message_id="msg-2",
        sender="bob@example.com",
        subject="Second",
        snippet="Second email",
        priority=Priority.DIGEST,
        received_at=datetime(2026, 4, 10, 9, 0),
        account_id=1,
    )

    first.topics.append(
        email_schemas.EmailTopicBadge(
            id=99,
            title="Launch",
            status="active",
            pinned=True,
        )
    )

    assert second.topics == []


def test_mail_ingestion_settings_no_longer_expose_agent_definition_selection():
    """Classifier guidance comes from the character constitution (JAN-201), not an agent picker."""
    settings = email_schemas.MailIngestionSettings(initial_sync_email_limit=100)

    assert set(settings.model_dump()) == {"initial_sync_email_limit"}
    assert "mail_ingestion_agent_definition_id" not in email_schemas.MailIngestionSettingsUpdate.model_fields
