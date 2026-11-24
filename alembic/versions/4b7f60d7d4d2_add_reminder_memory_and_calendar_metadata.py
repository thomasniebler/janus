"""Add reminder memory and Janus calendar metadata.

Revision ID: 4b7f60d7d4d2
Revises: 6baf2fdc37bf
Create Date: 2026-04-05 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4b7f60d7d4d2"
down_revision: str | Sequence[str] | None = "6baf2fdc37bf"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Create the enum type explicitly for PostgreSQL (no-op on SQLite).
    sa.Enum("PLANNER", "JANUS_REMINDER", name="calendareventsource").create(op.get_bind(), checkfirst=True)

    with op.batch_alter_table("calendar_events") as batch_op:
        batch_op.add_column(sa.Column("description", sa.String(), nullable=True))
        batch_op.add_column(
            sa.Column(
                "source_type",
                sa.Enum("PLANNER", "JANUS_REMINDER", name="calendareventsource"),
                nullable=False,
                server_default="PLANNER",
            )
        )

    op.create_table(
        "reminder_memory",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_email_id", sa.Integer(), nullable=True),
        sa.Column("synthetic_calendar_event_id", sa.Integer(), nullable=True),
        sa.Column("kind", sa.Enum("EVENT", "PREPARATION", name="reminderkind"), nullable=False),
        sa.Column(
            "status",
            sa.Enum("ACTIVE", "COMPLETED", "DISMISSED", "EXPIRED", name="reminderstatus"),
            nullable=False,
        ),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("notes", sa.String(), nullable=True),
        sa.Column("due_at", sa.DateTime(), nullable=False),
        sa.Column("prepare_for_at", sa.DateTime(), nullable=True),
        sa.Column("dedupe_key", sa.String(), nullable=False),
        sa.Column("confidence", sa.String(), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["source_email_id"], ["emails.id"]),
        sa.ForeignKeyConstraint(["synthetic_calendar_event_id"], ["calendar_events.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_reminder_memory_id"), "reminder_memory", ["id"], unique=False)
    op.create_index(op.f("ix_reminder_memory_dedupe_key"), "reminder_memory", ["dedupe_key"], unique=True)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_reminder_memory_dedupe_key"), table_name="reminder_memory")
    op.drop_index(op.f("ix_reminder_memory_id"), table_name="reminder_memory")
    op.drop_table("reminder_memory")

    with op.batch_alter_table("calendar_events") as batch_op:
        batch_op.drop_column("source_type")
        batch_op.drop_column("description")

    sa.Enum(name="reminderstatus").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="reminderkind").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="calendareventsource").drop(op.get_bind(), checkfirst=True)
