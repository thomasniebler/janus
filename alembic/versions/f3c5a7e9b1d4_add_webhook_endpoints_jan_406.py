"""Inbound webhook endpoints (JAN-406).

Adds ``webhook_endpoints`` (owner, name, hashed URL key and its display prefix,
Fernet-encrypted HMAC secret, enabled switch, delivery counter).

Revision ID: f3c5a7e9b1d4
Revises: e7a1b3c5d9f2
Create Date: 2026-11-25 19:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f3c5a7e9b1d4"
down_revision: str | Sequence[str] | None = "e7a1b3c5d9f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``webhook_endpoints``."""
    op.create_table(
        "webhook_endpoints",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("key_prefix", sa.String(length=12), nullable=False),
        sa.Column("encrypted_secret", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("received_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_received_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_webhook_endpoints_id", "webhook_endpoints", ["id"])
    op.create_index("ix_webhook_endpoints_user_id", "webhook_endpoints", ["user_id"])


def downgrade() -> None:
    """Drop ``webhook_endpoints``."""
    op.drop_index("ix_webhook_endpoints_user_id", table_name="webhook_endpoints")
    op.drop_index("ix_webhook_endpoints_id", table_name="webhook_endpoints")
    op.drop_table("webhook_endpoints")
