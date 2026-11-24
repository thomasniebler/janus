"""Add linear and agent management access flags.

Revision ID: 1c2d3e4f5a6b
Revises: 9a6b3d4c5e7f
Create Date: 2026-05-12 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1c2d3e4f5a6b"
down_revision: str | None = "9a6b3d4c5e7f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add new agent-definition access flags."""
    with op.batch_alter_table("agent_definitions") as batch_op:
        batch_op.add_column(sa.Column("linear_access", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(
            sa.Column("agent_management_access", sa.Boolean(), nullable=False, server_default=sa.false())
        )


def downgrade() -> None:
    """Remove the agent-definition access flags."""
    with op.batch_alter_table("agent_definitions") as batch_op:
        batch_op.drop_column("agent_management_access")
        batch_op.drop_column("linear_access")
