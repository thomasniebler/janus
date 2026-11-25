"""Telegram bot connections (JAN-405).

Revision ID: c6f8d0e2a4b7
Revises: b5e7c9d1f3a6
Create Date: 2026-11-25 21:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c6f8d0e2a4b7"
down_revision: str | Sequence[str] | None = "b5e7c9d1f3a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``telegram_bots``."""
    op.create_table(
        "telegram_bots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("encrypted_token", sa.Text(), nullable=False),
        sa.Column("bot_username", sa.String(length=64), nullable=False),
        sa.Column("chat_id", sa.String(length=32), nullable=True),
        sa.Column("pairing_code", sa.String(length=16), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("last_update_id", sa.Integer(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("sent_day", sa.Date(), nullable=True),
        sa.Column("sent_today", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_telegram_bots_id", "telegram_bots", ["id"])
    op.create_index("ix_telegram_bots_user_id", "telegram_bots", ["user_id"])


def downgrade() -> None:
    """Drop ``telegram_bots``."""
    op.drop_index("ix_telegram_bots_user_id", table_name="telegram_bots")
    op.drop_index("ix_telegram_bots_id", table_name="telegram_bots")
    op.drop_table("telegram_bots")
