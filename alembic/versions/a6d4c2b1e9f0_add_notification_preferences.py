"""Add notification preferences.

Revision ID: a6d4c2b1e9f0
Revises: 2f1a6c3d4e5b
Create Date: 2026-04-20 13:40:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a6d4c2b1e9f0"
down_revision: str | None = "2f1a6c3d4e5b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("notification_preferences"):
        op.create_table(
            "notification_preferences",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("notify_important", sa.Boolean(), nullable=False, server_default="1"),
            sa.Column("notify_digest", sa.Boolean(), nullable=False, server_default="1"),
            sa.Column("daily_digest_enabled", sa.Boolean(), nullable=False, server_default="0"),
            sa.Column("daily_digest_hour", sa.Integer(), nullable=False, server_default="16"),
            sa.Column("daily_digest_minute", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("digest_lookback_hours", sa.Integer(), nullable=False, server_default="24"),
            sa.Column("sender_allowlist", sa.Text(), nullable=True),
            sa.Column("sender_blocklist", sa.Text(), nullable=True),
            sa.Column("keyword_allowlist", sa.Text(), nullable=True),
            sa.Column("keyword_blocklist", sa.Text(), nullable=True),
            sa.Column("last_daily_digest_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("user_id"),
        )
        op.create_index(
            op.f("ix_notification_preferences_id"),
            "notification_preferences",
            ["id"],
            unique=False,
        )
        op.create_index(
            op.f("ix_notification_preferences_user_id"),
            "notification_preferences",
            ["user_id"],
            unique=True,
        )


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("notification_preferences"):
        op.drop_index(op.f("ix_notification_preferences_user_id"), table_name="notification_preferences")
        op.drop_index(op.f("ix_notification_preferences_id"), table_name="notification_preferences")
        op.drop_table("notification_preferences")
