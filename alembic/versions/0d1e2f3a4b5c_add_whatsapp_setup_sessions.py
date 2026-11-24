"""Add WhatsApp setup sessions.

Revision ID: 0d1e2f3a4b5c
Revises: c4d5e6f7a8b9
Create Date: 2026-07-07 14:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0d1e2f3a4b5c"
down_revision: str | None = "c4d5e6f7a8b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "whatsapp_setup_sessions" not in inspector.get_table_names():
        op.create_table(
            "whatsapp_setup_sessions",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("account_id", sa.Integer(), nullable=True),
            sa.Column("display_name", sa.String(), nullable=False),
            sa.Column("initial_history_days", sa.Integer(), nullable=False, server_default="3"),
            sa.Column(
                "setup_status",
                sa.Enum(
                    "created",
                    "pairing",
                    "awaiting_fallback",
                    "ready",
                    "completed",
                    "failed",
                    "cancelled",
                    "expired",
                    name="whatsappsetupstatus",
                    native_enum=False,
                ),
                nullable=False,
            ),
            sa.Column(
                "pairing_state",
                sa.Enum(
                    "idle",
                    "starting",
                    "waiting",
                    "code_ready",
                    "paired",
                    "imported",
                    "unsupported",
                    "failed",
                    "cancelled",
                    "expired",
                    name="whatsapppairingstate",
                    native_enum=False,
                ),
                nullable=False,
            ),
            sa.Column("pairing_supported", sa.Boolean(), nullable=False, server_default="0"),
            sa.Column("show_fallback_import", sa.Boolean(), nullable=False, server_default="0"),
            sa.Column("pairing_message", sa.Text(), nullable=True),
            sa.Column("pairing_code", sa.String(), nullable=True),
            sa.Column("pairing_qr_payload", sa.Text(), nullable=True),
            sa.Column("session_data", sa.Text(), nullable=True),
            sa.Column("session_database_path", sa.String(), nullable=True),
            sa.Column("last_error", sa.String(), nullable=True),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.Column("cancelled_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["account_id"], ["whatsapp_accounts.id"]),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_whatsapp_setup_sessions_id"), "whatsapp_setup_sessions", ["id"], unique=False)
        op.create_index(
            op.f("ix_whatsapp_setup_sessions_user_id"),
            "whatsapp_setup_sessions",
            ["user_id"],
            unique=False,
        )
        op.create_index(
            op.f("ix_whatsapp_setup_sessions_account_id"),
            "whatsapp_setup_sessions",
            ["account_id"],
            unique=False,
        )
        op.create_index(
            op.f("ix_whatsapp_setup_sessions_expires_at"),
            "whatsapp_setup_sessions",
            ["expires_at"],
            unique=False,
        )


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "whatsapp_setup_sessions" in inspector.get_table_names():
        op.drop_index(op.f("ix_whatsapp_setup_sessions_expires_at"), table_name="whatsapp_setup_sessions")
        op.drop_index(op.f("ix_whatsapp_setup_sessions_account_id"), table_name="whatsapp_setup_sessions")
        op.drop_index(op.f("ix_whatsapp_setup_sessions_user_id"), table_name="whatsapp_setup_sessions")
        op.drop_index(op.f("ix_whatsapp_setup_sessions_id"), table_name="whatsapp_setup_sessions")
        op.drop_table("whatsapp_setup_sessions")
