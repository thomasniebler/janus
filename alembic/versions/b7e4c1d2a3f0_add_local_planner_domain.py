"""Add Janus-owned local planner domain.

Revision ID: b7e4c1d2a3f0
Revises: e7a9c2d1f4b8
Create Date: 2026-06-28 13:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7e4c1d2a3f0"
down_revision: str | None = "e7a9c2d1f4b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    confidence_level = postgresql.ENUM("HIGH", "MEDIUM", "LOW", name="confidencelevel", create_type=False)
    task_status = postgresql.ENUM("TODO", "IN_PROGRESS", "DONE", name="taskstatus", create_type=False)
    planner_item_source = postgresql.ENUM(
        "MANUAL",
        "EMAIL_AGENT",
        "DIGEST_AGENT",
        "IMPORTED",
        name="planneritemsource",
        create_type=False,
    )
    planner_suggestion_type = postgresql.ENUM(
        "TASK",
        "EVENT",
        "TASK_UPDATE",
        "EVENT_UPDATE",
        name="plannersuggestiontype",
        create_type=False,
    )
    planner_suggestion_status = postgresql.ENUM(
        "PENDING",
        "APPROVED",
        "DISMISSED",
        "APPLIED",
        name="plannersuggestionstatus",
        create_type=False,
    )
    planner_link_source = postgresql.ENUM(
        "EMAIL",
        "TOPIC",
        "REMINDER",
        "IMPORTED_TASK",
        "IMPORTED_EVENT",
        name="plannerlinksource",
        create_type=False,
    )

    task_status.create(bind, checkfirst=True)
    planner_item_source.create(bind, checkfirst=True)
    planner_suggestion_type.create(bind, checkfirst=True)
    planner_suggestion_status.create(bind, checkfirst=True)
    planner_link_source.create(bind, checkfirst=True)

    if not inspector.has_table("planner_tasks"):
        op.create_table(
            "planner_tasks",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("title", sa.String(), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("status", task_status, nullable=False, server_default="TODO"),
            sa.Column("priority", sa.String(), nullable=True),
            sa.Column("due_at", sa.DateTime(), nullable=True),
            sa.Column("scheduled_for", sa.DateTime(), nullable=True),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.Column("source", planner_item_source, nullable=False, server_default="MANUAL"),
            sa.Column("confidence", confidence_level, nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_planner_tasks_id"), "planner_tasks", ["id"], unique=False)
        op.create_index(op.f("ix_planner_tasks_user_id"), "planner_tasks", ["user_id"], unique=False)
        op.create_index(op.f("ix_planner_tasks_status"), "planner_tasks", ["status"], unique=False)

    if not inspector.has_table("planner_events"):
        op.create_table(
            "planner_events",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("title", sa.String(), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("start_time", sa.DateTime(), nullable=False),
            sa.Column("end_time", sa.DateTime(), nullable=False),
            sa.Column("location", sa.String(), nullable=True),
            sa.Column("all_day", sa.Boolean(), nullable=False, server_default="0"),
            sa.Column("source", planner_item_source, nullable=False, server_default="MANUAL"),
            sa.Column("confidence", confidence_level, nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_planner_events_id"), "planner_events", ["id"], unique=False)
        op.create_index(op.f("ix_planner_events_user_id"), "planner_events", ["user_id"], unique=False)
        op.create_index(op.f("ix_planner_events_start_time"), "planner_events", ["start_time"], unique=False)

    if not inspector.has_table("planner_suggestions"):
        op.create_table(
            "planner_suggestions",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("suggestion_type", planner_suggestion_type, nullable=False),
            sa.Column("status", planner_suggestion_status, nullable=False, server_default="PENDING"),
            sa.Column("source", planner_item_source, nullable=False, server_default="EMAIL_AGENT"),
            sa.Column("confidence", confidence_level, nullable=True),
            sa.Column("reason", sa.Text(), nullable=True),
            sa.Column("dedupe_key", sa.String(), nullable=True),
            sa.Column("title", sa.String(), nullable=True),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("task_status", task_status, nullable=True),
            sa.Column("priority", sa.String(), nullable=True),
            sa.Column("due_at", sa.DateTime(), nullable=True),
            sa.Column("scheduled_for", sa.DateTime(), nullable=True),
            sa.Column("start_time", sa.DateTime(), nullable=True),
            sa.Column("end_time", sa.DateTime(), nullable=True),
            sa.Column("location", sa.String(), nullable=True),
            sa.Column("all_day", sa.Boolean(), nullable=False, server_default="0"),
            sa.Column("source_email_id", sa.Integer(), nullable=True),
            sa.Column("source_topic_id", sa.Integer(), nullable=True),
            sa.Column("target_task_id", sa.Integer(), nullable=True),
            sa.Column("target_event_id", sa.Integer(), nullable=True),
            sa.Column("applied_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["source_email_id"], ["emails.id"]),
            sa.ForeignKeyConstraint(["source_topic_id"], ["tracked_topics.id"]),
            sa.ForeignKeyConstraint(["target_event_id"], ["planner_events.id"]),
            sa.ForeignKeyConstraint(["target_task_id"], ["planner_tasks.id"]),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_planner_suggestions_id"), "planner_suggestions", ["id"], unique=False)
        op.create_index(op.f("ix_planner_suggestions_user_id"), "planner_suggestions", ["user_id"], unique=False)
        op.create_index(op.f("ix_planner_suggestions_status"), "planner_suggestions", ["status"], unique=False)
        op.create_index(
            op.f("ix_planner_suggestions_suggestion_type"),
            "planner_suggestions",
            ["suggestion_type"],
            unique=False,
        )
        op.create_index(op.f("ix_planner_suggestions_dedupe_key"), "planner_suggestions", ["dedupe_key"], unique=False)
        op.create_index(
            op.f("ix_planner_suggestions_source_email_id"),
            "planner_suggestions",
            ["source_email_id"],
            unique=False,
        )
        op.create_index(
            op.f("ix_planner_suggestions_source_topic_id"),
            "planner_suggestions",
            ["source_topic_id"],
            unique=False,
        )
        op.create_index(
            op.f("ix_planner_suggestions_target_task_id"),
            "planner_suggestions",
            ["target_task_id"],
            unique=False,
        )
        op.create_index(
            op.f("ix_planner_suggestions_target_event_id"),
            "planner_suggestions",
            ["target_event_id"],
            unique=False,
        )

    if not inspector.has_table("planner_links"):
        op.create_table(
            "planner_links",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("planner_task_id", sa.Integer(), nullable=True),
            sa.Column("planner_event_id", sa.Integer(), nullable=True),
            sa.Column("source_type", planner_link_source, nullable=False),
            sa.Column("source_id", sa.Integer(), nullable=True),
            sa.Column("source_detail", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["planner_event_id"], ["planner_events.id"]),
            sa.ForeignKeyConstraint(["planner_task_id"], ["planner_tasks.id"]),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_planner_links_id"), "planner_links", ["id"], unique=False)
        op.create_index(op.f("ix_planner_links_user_id"), "planner_links", ["user_id"], unique=False)
        op.create_index(
            op.f("ix_planner_links_planner_task_id"),
            "planner_links",
            ["planner_task_id"],
            unique=False,
        )
        op.create_index(
            op.f("ix_planner_links_planner_event_id"),
            "planner_links",
            ["planner_event_id"],
            unique=False,
        )
        op.create_index(op.f("ix_planner_links_source_type"), "planner_links", ["source_type"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    planner_link_source = postgresql.ENUM(
        "EMAIL",
        "TOPIC",
        "REMINDER",
        "IMPORTED_TASK",
        "IMPORTED_EVENT",
        name="plannerlinksource",
        create_type=False,
    )
    planner_suggestion_status = postgresql.ENUM(
        "PENDING",
        "APPROVED",
        "DISMISSED",
        "APPLIED",
        name="plannersuggestionstatus",
        create_type=False,
    )
    planner_suggestion_type = postgresql.ENUM(
        "TASK",
        "EVENT",
        "TASK_UPDATE",
        "EVENT_UPDATE",
        name="plannersuggestiontype",
        create_type=False,
    )
    planner_item_source = postgresql.ENUM(
        "MANUAL",
        "EMAIL_AGENT",
        "DIGEST_AGENT",
        "IMPORTED",
        name="planneritemsource",
        create_type=False,
    )

    op.drop_index(op.f("ix_planner_links_source_type"), table_name="planner_links")
    op.drop_index(op.f("ix_planner_links_planner_event_id"), table_name="planner_links")
    op.drop_index(op.f("ix_planner_links_planner_task_id"), table_name="planner_links")
    op.drop_index(op.f("ix_planner_links_user_id"), table_name="planner_links")
    op.drop_index(op.f("ix_planner_links_id"), table_name="planner_links")
    op.drop_table("planner_links")

    op.drop_index(op.f("ix_planner_suggestions_target_event_id"), table_name="planner_suggestions")
    op.drop_index(op.f("ix_planner_suggestions_target_task_id"), table_name="planner_suggestions")
    op.drop_index(op.f("ix_planner_suggestions_source_topic_id"), table_name="planner_suggestions")
    op.drop_index(op.f("ix_planner_suggestions_source_email_id"), table_name="planner_suggestions")
    op.drop_index(op.f("ix_planner_suggestions_dedupe_key"), table_name="planner_suggestions")
    op.drop_index(op.f("ix_planner_suggestions_suggestion_type"), table_name="planner_suggestions")
    op.drop_index(op.f("ix_planner_suggestions_status"), table_name="planner_suggestions")
    op.drop_index(op.f("ix_planner_suggestions_user_id"), table_name="planner_suggestions")
    op.drop_index(op.f("ix_planner_suggestions_id"), table_name="planner_suggestions")
    op.drop_table("planner_suggestions")

    op.drop_index(op.f("ix_planner_events_start_time"), table_name="planner_events")
    op.drop_index(op.f("ix_planner_events_user_id"), table_name="planner_events")
    op.drop_index(op.f("ix_planner_events_id"), table_name="planner_events")
    op.drop_table("planner_events")

    op.drop_index(op.f("ix_planner_tasks_status"), table_name="planner_tasks")
    op.drop_index(op.f("ix_planner_tasks_user_id"), table_name="planner_tasks")
    op.drop_index(op.f("ix_planner_tasks_id"), table_name="planner_tasks")
    op.drop_table("planner_tasks")

    planner_link_source.drop(bind, checkfirst=True)
    planner_suggestion_status.drop(bind, checkfirst=True)
    planner_suggestion_type.drop(bind, checkfirst=True)
    planner_item_source.drop(bind, checkfirst=True)
