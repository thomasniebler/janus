"""Add WhatsApp foundation tables and dashboard widget key.

Revision ID: f1b2c3d4e5f6
Revises: 152cb34fcb7b
Create Date: 2026-07-06 14:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "152cb34fcb7b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _drop_widget_key_check() -> None:
    """Drop the existing widget-key CHECK so it can be recreated with the new value set.

    SQLite batch mode rebuilds the table (and its CHECK) anyway; PostgreSQL alters in place and
    would otherwise fail on the duplicate constraint name.
    """
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE dashboard_widget_preferences DROP CONSTRAINT IF EXISTS dashboardwidgetkey")


def upgrade() -> None:
    """Upgrade schema."""
    _drop_widget_key_check()
    with op.batch_alter_table("dashboard_widget_preferences") as batch_op:
        batch_op.alter_column(
            "widget_key",
            existing_type=sa.String(length=17),
            type_=sa.Enum(
                "BRIEFING",
                "TOPIC_FOCUS",
                "IMPORTANT_EMAILS",
                "WHATSAPP_MESSAGES",
                "SCHEDULE",
                "TASKS",
                name="dashboardwidgetkey",
                native_enum=False,
                create_constraint=True,
                validate_strings=True,
            ),
            existing_nullable=False,
        )

    op.create_table(
        "whatsapp_accounts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("session_data", sa.Text(), nullable=True),
        sa.Column("session_database_path", sa.String(), nullable=True),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column(
            "connection_status",
            sa.Enum("disconnected", "connected", "degraded", name="whatsappconnectionstatus", native_enum=False),
            nullable=False,
        ),
        sa.Column("initial_history_days", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("last_synced_at", sa.DateTime(), nullable=True),
        sa.Column("last_sync_error", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_whatsapp_accounts_id"), "whatsapp_accounts", ["id"], unique=False)
    op.create_index(op.f("ix_whatsapp_accounts_user_id"), "whatsapp_accounts", ["user_id"], unique=False)

    op.create_table(
        "whatsapp_conversations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("whatsapp_account_id", sa.Integer(), nullable=False),
        sa.Column("provider_chat_id", sa.String(), nullable=False),
        sa.Column(
            "conversation_type",
            sa.Enum("direct", "group", name="whatsappconversationtype", native_enum=False),
            nullable=False,
        ),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("participant_summary", sa.Text(), nullable=True),
        sa.Column("unread_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("latest_message_at", sa.DateTime(), nullable=True),
        sa.Column("latest_message_snippet", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["whatsapp_account_id"], ["whatsapp_accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "whatsapp_account_id",
            "provider_chat_id",
            name="uq_whatsapp_conversation_account_provider_chat",
        ),
    )
    op.create_index(op.f("ix_whatsapp_conversations_id"), "whatsapp_conversations", ["id"], unique=False)
    op.create_index(
        op.f("ix_whatsapp_conversations_provider_chat_id"),
        "whatsapp_conversations",
        ["provider_chat_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_whatsapp_conversations_whatsapp_account_id"),
        "whatsapp_conversations",
        ["whatsapp_account_id"],
        unique=False,
    )
    op.create_index(op.f("ix_whatsapp_conversations_user_id"), "whatsapp_conversations", ["user_id"], unique=False)
    op.create_index(
        op.f("ix_whatsapp_conversations_latest_message_at"),
        "whatsapp_conversations",
        ["latest_message_at"],
        unique=False,
    )

    op.create_table(
        "whatsapp_messages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("whatsapp_account_id", sa.Integer(), nullable=False),
        sa.Column("whatsapp_conversation_id", sa.Integer(), nullable=False),
        sa.Column("provider_message_id", sa.String(), nullable=False),
        sa.Column("provider_timestamp", sa.DateTime(), nullable=False),
        sa.Column(
            "direction",
            sa.Enum("incoming", "outgoing", name="whatsappmessagedirection", native_enum=False),
            nullable=False,
        ),
        sa.Column("sender_identifier", sa.String(), nullable=True),
        sa.Column("sender_display_name", sa.String(), nullable=True),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("snippet", sa.Text(), nullable=False),
        sa.Column("message_type", sa.String(), nullable=True),
        sa.Column("attachment_summary", sa.Text(), nullable=True),
        sa.Column("reply_to_message_id", sa.String(), nullable=True),
        sa.Column(
            "priority",
            postgresql.ENUM("IMPORTANT", "DIGEST", "IGNORE", "SPAM", name="priority", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "classification_confidence",
            postgresql.ENUM("HIGH", "MEDIUM", "LOW", name="confidencelevel", create_type=False),
            nullable=True,
        ),
        sa.Column("reasoning", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["whatsapp_account_id"], ["whatsapp_accounts.id"]),
        sa.ForeignKeyConstraint(["whatsapp_conversation_id"], ["whatsapp_conversations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "whatsapp_account_id",
            "provider_message_id",
            name="uq_whatsapp_message_account_provider_message",
        ),
    )
    op.create_index(op.f("ix_whatsapp_messages_id"), "whatsapp_messages", ["id"], unique=False)
    op.create_index(op.f("ix_whatsapp_messages_user_id"), "whatsapp_messages", ["user_id"], unique=False)
    op.create_index(
        op.f("ix_whatsapp_messages_whatsapp_account_id"),
        "whatsapp_messages",
        ["whatsapp_account_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_whatsapp_messages_whatsapp_conversation_id"),
        "whatsapp_messages",
        ["whatsapp_conversation_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_whatsapp_messages_provider_message_id"),
        "whatsapp_messages",
        ["provider_message_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_whatsapp_messages_provider_timestamp"),
        "whatsapp_messages",
        ["provider_timestamp"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_whatsapp_messages_provider_timestamp"), table_name="whatsapp_messages")
    op.drop_index(op.f("ix_whatsapp_messages_provider_message_id"), table_name="whatsapp_messages")
    op.drop_index(op.f("ix_whatsapp_messages_whatsapp_conversation_id"), table_name="whatsapp_messages")
    op.drop_index(op.f("ix_whatsapp_messages_whatsapp_account_id"), table_name="whatsapp_messages")
    op.drop_index(op.f("ix_whatsapp_messages_user_id"), table_name="whatsapp_messages")
    op.drop_index(op.f("ix_whatsapp_messages_id"), table_name="whatsapp_messages")
    op.drop_table("whatsapp_messages")

    op.drop_index(op.f("ix_whatsapp_conversations_latest_message_at"), table_name="whatsapp_conversations")
    op.drop_index(op.f("ix_whatsapp_conversations_user_id"), table_name="whatsapp_conversations")
    op.drop_index(op.f("ix_whatsapp_conversations_whatsapp_account_id"), table_name="whatsapp_conversations")
    op.drop_index(op.f("ix_whatsapp_conversations_provider_chat_id"), table_name="whatsapp_conversations")
    op.drop_index(op.f("ix_whatsapp_conversations_id"), table_name="whatsapp_conversations")
    op.drop_table("whatsapp_conversations")

    op.drop_index(op.f("ix_whatsapp_accounts_user_id"), table_name="whatsapp_accounts")
    op.drop_index(op.f("ix_whatsapp_accounts_id"), table_name="whatsapp_accounts")
    op.drop_table("whatsapp_accounts")

    _drop_widget_key_check()
    with op.batch_alter_table("dashboard_widget_preferences") as batch_op:
        batch_op.alter_column(
            "widget_key",
            existing_type=sa.String(length=17),
            type_=sa.Enum(
                "BRIEFING",
                "TOPIC_FOCUS",
                "IMPORTANT_EMAILS",
                "SCHEDULE",
                "TASKS",
                name="dashboardwidgetkey",
                native_enum=False,
                create_constraint=True,
                validate_strings=True,
            ),
            existing_nullable=False,
        )
