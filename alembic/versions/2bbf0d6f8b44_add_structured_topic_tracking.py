"""Add structured topic tracking.

Revision ID: 2bbf0d6f8b44
Revises: 5c8d7e6f4a3b
Create Date: 2026-05-18 09:10:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2bbf0d6f8b44"
down_revision: str | None = "5c8d7e6f4a3b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    confidence_level = postgresql.ENUM("HIGH", "MEDIUM", "LOW", name="confidencelevel", create_type=False)
    topic_status = postgresql.ENUM("ACTIVE", "SNOOZED", "RESOLVED", "IGNORED", name="topicstatus", create_type=False)
    topic_origin = postgresql.ENUM("AUTO", "MANUAL", "RULE_SUGGESTION", name="topicorigin", create_type=False)
    topic_evidence_source = postgresql.ENUM(
        "EMAIL",
        "REMINDER",
        "CALENDAR_EVENT",
        "TASK",
        "MEMORY_FACT",
        name="topicevidencesource",
        create_type=False,
    )
    topic_evidence_role = postgresql.ENUM(
        "TRIGGER", "SUPPORTING", "DERIVED_LINK", name="topicevidencerole", create_type=False
    )
    topic_suggestion_type = postgresql.ENUM(
        "RENAME",
        "MERGE",
        "PIN",
        "IGNORE_PATTERN",
        "PROMOTE_PATTERN",
        name="topicsuggestiontype",
        create_type=False,
    )
    topic_suggestion_status = postgresql.ENUM(
        "PENDING", "APPROVED", "DISMISSED", name="topicsuggestionstatus", create_type=False
    )
    topic_extraction_scope = postgresql.ENUM(
        "IMPORTANT_ONLY",
        "IMPORTANT_AND_DIGEST",
        "ALL_NON_SPAM",
        name="topicextractionscope",
        create_type=False,
    )

    topic_status.create(bind, checkfirst=True)
    topic_origin.create(bind, checkfirst=True)
    topic_evidence_source.create(bind, checkfirst=True)
    topic_evidence_role.create(bind, checkfirst=True)
    topic_suggestion_type.create(bind, checkfirst=True)
    topic_suggestion_status.create(bind, checkfirst=True)
    topic_extraction_scope.create(bind, checkfirst=True)

    agent_definition_columns = {column["name"] for column in inspector.get_columns("agent_definitions")}
    if "topic_access" not in agent_definition_columns:
        with op.batch_alter_table("agent_definitions") as batch_op:
            batch_op.add_column(sa.Column("topic_access", sa.Boolean(), nullable=False, server_default=sa.false()))

    settings_columns = {column["name"] for column in inspector.get_columns("settings")}
    with op.batch_alter_table("settings") as batch_op:
        if "topic_tracking_available" not in settings_columns:
            batch_op.add_column(
                sa.Column("topic_tracking_available", sa.Boolean(), nullable=False, server_default=sa.true())
            )
        if "topic_tracking_default_enabled" not in settings_columns:
            batch_op.add_column(
                sa.Column("topic_tracking_default_enabled", sa.Boolean(), nullable=False, server_default=sa.true())
            )
        if "topic_tracking_default_scope" not in settings_columns:
            batch_op.add_column(
                sa.Column(
                    "topic_tracking_default_scope",
                    topic_extraction_scope,
                    nullable=False,
                    server_default="IMPORTANT_AND_DIGEST",
                )
            )
        if "topic_tracking_default_min_confidence" not in settings_columns:
            batch_op.add_column(
                sa.Column(
                    "topic_tracking_default_min_confidence",
                    confidence_level,
                    nullable=False,
                    server_default="MEDIUM",
                )
            )
        if "topic_tracking_max_active_topics" not in settings_columns:
            batch_op.add_column(
                sa.Column("topic_tracking_max_active_topics", sa.Integer(), nullable=False, server_default="20")
            )
        if "topic_tracking_max_context_topics" not in settings_columns:
            batch_op.add_column(
                sa.Column("topic_tracking_max_context_topics", sa.Integer(), nullable=False, server_default="5")
            )
        if "topic_tracking_review_required" not in settings_columns:
            batch_op.add_column(
                sa.Column("topic_tracking_review_required", sa.Boolean(), nullable=False, server_default=sa.true())
            )

    if not inspector.has_table("tracked_topics"):
        op.create_table(
            "tracked_topics",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("slug", sa.String(), nullable=False),
            sa.Column("title", sa.String(), nullable=False),
            sa.Column("summary", sa.Text(), nullable=True),
            sa.Column("status", topic_status, nullable=False, server_default="ACTIVE"),
            sa.Column("pinned", sa.Boolean(), nullable=False, server_default="0"),
            sa.Column("user_confirmed", sa.Boolean(), nullable=False, server_default="0"),
            sa.Column("origin", topic_origin, nullable=False, server_default="AUTO"),
            sa.Column("confidence", confidence_level, nullable=True),
            sa.Column("first_seen_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("last_seen_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("snoozed_until", sa.DateTime(), nullable=True),
            sa.Column("last_briefed_at", sa.DateTime(), nullable=True),
            sa.Column("last_digested_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("user_id", "slug", name="uq_tracked_topics_user_slug"),
        )
        op.create_index(op.f("ix_tracked_topics_id"), "tracked_topics", ["id"], unique=False)
        op.create_index(op.f("ix_tracked_topics_user_id"), "tracked_topics", ["user_id"], unique=False)
        op.create_index(op.f("ix_tracked_topics_slug"), "tracked_topics", ["slug"], unique=False)
        op.create_index(op.f("ix_tracked_topics_status"), "tracked_topics", ["status"], unique=False)

    if not inspector.has_table("topic_evidence"):
        op.create_table(
            "topic_evidence",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("topic_id", sa.Integer(), nullable=False),
            sa.Column("source_type", topic_evidence_source, nullable=False),
            sa.Column("source_id", sa.Integer(), nullable=True),
            sa.Column("excerpt", sa.Text(), nullable=True),
            sa.Column("evidence_role", topic_evidence_role, nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["topic_id"], ["tracked_topics.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_topic_evidence_id"), "topic_evidence", ["id"], unique=False)
        op.create_index(op.f("ix_topic_evidence_topic_id"), "topic_evidence", ["topic_id"], unique=False)

    if not inspector.has_table("topic_tracking_preferences"):
        op.create_table(
            "topic_tracking_preferences",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default="1"),
            sa.Column(
                "extraction_scope",
                topic_extraction_scope,
                nullable=False,
                server_default="IMPORTANT_AND_DIGEST",
            ),
            sa.Column("minimum_confidence", confidence_level, nullable=False, server_default="MEDIUM"),
            sa.Column("max_active_topics", sa.Integer(), nullable=False, server_default="10"),
            sa.Column("reminder_linking_enabled", sa.Boolean(), nullable=False, server_default="1"),
            sa.Column("digest_grouping_enabled", sa.Boolean(), nullable=False, server_default="1"),
            sa.Column("dashboard_widget_enabled", sa.Boolean(), nullable=False, server_default="1"),
            sa.Column("chat_context_enabled", sa.Boolean(), nullable=False, server_default="1"),
            sa.Column("suggestion_generation_enabled", sa.Boolean(), nullable=False, server_default="1"),
            sa.Column("allowlist_keywords", sa.Text(), nullable=True),
            sa.Column("blocklist_keywords", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("user_id"),
        )
        op.create_index(
            op.f("ix_topic_tracking_preferences_id"),
            "topic_tracking_preferences",
            ["id"],
            unique=False,
        )
        op.create_index(
            op.f("ix_topic_tracking_preferences_user_id"),
            "topic_tracking_preferences",
            ["user_id"],
            unique=True,
        )

    if not inspector.has_table("topic_suggestions"):
        op.create_table(
            "topic_suggestions",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("topic_id", sa.Integer(), nullable=True),
            sa.Column("suggestion_type", topic_suggestion_type, nullable=False),
            sa.Column("status", topic_suggestion_status, nullable=False, server_default="PENDING"),
            sa.Column("payload_json", sa.Text(), nullable=True),
            sa.Column("reason", sa.String(), nullable=False),
            sa.Column("confidence", confidence_level, nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["topic_id"], ["tracked_topics.id"]),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_topic_suggestions_id"), "topic_suggestions", ["id"], unique=False)
        op.create_index(op.f("ix_topic_suggestions_user_id"), "topic_suggestions", ["user_id"], unique=False)
        op.create_index(op.f("ix_topic_suggestions_topic_id"), "topic_suggestions", ["topic_id"], unique=False)
        op.create_index(op.f("ix_topic_suggestions_status"), "topic_suggestions", ["status"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    topic_extraction_scope = postgresql.ENUM(
        "IMPORTANT_ONLY",
        "IMPORTANT_AND_DIGEST",
        "ALL_NON_SPAM",
        name="topicextractionscope",
        create_type=False,
    )
    topic_suggestion_status = postgresql.ENUM(
        "PENDING", "APPROVED", "DISMISSED", name="topicsuggestionstatus", create_type=False
    )
    topic_suggestion_type = postgresql.ENUM(
        "RENAME",
        "MERGE",
        "PIN",
        "IGNORE_PATTERN",
        "PROMOTE_PATTERN",
        name="topicsuggestiontype",
        create_type=False,
    )
    topic_evidence_role = postgresql.ENUM(
        "TRIGGER", "SUPPORTING", "DERIVED_LINK", name="topicevidencerole", create_type=False
    )
    topic_evidence_source = postgresql.ENUM(
        "EMAIL",
        "REMINDER",
        "CALENDAR_EVENT",
        "TASK",
        "MEMORY_FACT",
        name="topicevidencesource",
        create_type=False,
    )
    topic_origin = postgresql.ENUM("AUTO", "MANUAL", "RULE_SUGGESTION", name="topicorigin", create_type=False)
    topic_status = postgresql.ENUM("ACTIVE", "SNOOZED", "RESOLVED", "IGNORED", name="topicstatus", create_type=False)

    op.drop_index(op.f("ix_topic_suggestions_status"), table_name="topic_suggestions")
    op.drop_index(op.f("ix_topic_suggestions_topic_id"), table_name="topic_suggestions")
    op.drop_index(op.f("ix_topic_suggestions_user_id"), table_name="topic_suggestions")
    op.drop_index(op.f("ix_topic_suggestions_id"), table_name="topic_suggestions")
    op.drop_table("topic_suggestions")

    op.drop_index(op.f("ix_topic_tracking_preferences_user_id"), table_name="topic_tracking_preferences")
    op.drop_index(op.f("ix_topic_tracking_preferences_id"), table_name="topic_tracking_preferences")
    op.drop_table("topic_tracking_preferences")

    op.drop_index(op.f("ix_topic_evidence_topic_id"), table_name="topic_evidence")
    op.drop_index(op.f("ix_topic_evidence_id"), table_name="topic_evidence")
    op.drop_table("topic_evidence")

    op.drop_index(op.f("ix_tracked_topics_status"), table_name="tracked_topics")
    op.drop_index(op.f("ix_tracked_topics_slug"), table_name="tracked_topics")
    op.drop_index(op.f("ix_tracked_topics_user_id"), table_name="tracked_topics")
    op.drop_index(op.f("ix_tracked_topics_id"), table_name="tracked_topics")
    op.drop_table("tracked_topics")

    with op.batch_alter_table("settings") as batch_op:
        batch_op.drop_column("topic_tracking_review_required")
        batch_op.drop_column("topic_tracking_max_context_topics")
        batch_op.drop_column("topic_tracking_max_active_topics")
        batch_op.drop_column("topic_tracking_default_min_confidence")
        batch_op.drop_column("topic_tracking_default_scope")
        batch_op.drop_column("topic_tracking_default_enabled")
        batch_op.drop_column("topic_tracking_available")

    with op.batch_alter_table("agent_definitions") as batch_op:
        batch_op.drop_column("topic_access")

    topic_suggestion_status.drop(bind, checkfirst=True)
    topic_suggestion_type.drop(bind, checkfirst=True)
    topic_evidence_role.drop(bind, checkfirst=True)
    topic_evidence_source.drop(bind, checkfirst=True)
    topic_origin.drop(bind, checkfirst=True)
    topic_status.drop(bind, checkfirst=True)
    topic_extraction_scope.drop(bind, checkfirst=True)
