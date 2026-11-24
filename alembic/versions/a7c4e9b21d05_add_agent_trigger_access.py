"""Add the agent-definition trigger_access flag (JAN-184).

Gates the self-service trigger toolbox that lets Janus schedule its own
follow-up work. The column is additive and defaults to false, so existing
agent definitions keep their current (no trigger access) behavior.

Revision ID: a7c4e9b21d05
Revises: 6d1f4a8c07be
Create Date: 2026-09-23 08:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7c4e9b21d05"
down_revision: str | Sequence[str] | None = "6d1f4a8c07be"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add trigger_access to agent_definitions when it is not already present."""
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("agent_definitions")}
    if "trigger_access" not in columns:
        with op.batch_alter_table("agent_definitions") as batch_op:
            batch_op.add_column(sa.Column("trigger_access", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    """Drop trigger_access from agent_definitions when it is present."""
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("agent_definitions")}
    if "trigger_access" in columns:
        with op.batch_alter_table("agent_definitions") as batch_op:
            batch_op.drop_column("trigger_access")
