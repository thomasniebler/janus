"""A2A agents: the owner-scoped registry of remote Agent2Agent agents (JAN-404).

Adds ``a2a_agents`` (base URL, enabled switch, the bounded agent card, JSON-RPC URL,
and an optional Fernet-encrypted bearer token).

Revision ID: e7a1b3c5d9f2
Revises: d1a7c3e5f9b2
Create Date: 2026-11-25 15:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e7a1b3c5d9f2"
down_revision: str | Sequence[str] | None = "d1a7c3e5f9b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``a2a_agents``."""
    op.create_table(
        "a2a_agents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("base_url", sa.String(length=2048), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("card", sa.JSON(), nullable=True),
        sa.Column("rpc_url", sa.String(length=2048), nullable=True),
        sa.Column("encrypted_token", sa.Text(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_a2a_agents_id", "a2a_agents", ["id"])
    op.create_index("ix_a2a_agents_user_id", "a2a_agents", ["user_id"])


def downgrade() -> None:
    """Drop ``a2a_agents``."""
    op.drop_index("ix_a2a_agents_user_id", table_name="a2a_agents")
    op.drop_index("ix_a2a_agents_id", table_name="a2a_agents")
    op.drop_table("a2a_agents")
