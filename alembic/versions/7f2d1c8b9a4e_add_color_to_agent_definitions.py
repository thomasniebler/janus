"""Add color metadata to agent definitions.

Revision ID: 7f2d1c8b9a4e
Revises: 6ab4e530e335
Create Date: 2026-04-18 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7f2d1c8b9a4e"
down_revision: str | None = "6ab4e530e335"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("agent_definitions")}
    if "color" in columns:
        return

    with op.batch_alter_table("agent_definitions") as batch_op:
        batch_op.add_column(sa.Column("color", sa.String(length=7), nullable=False, server_default="#6366F1"))


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("agent_definitions")}
    if "color" not in columns:
        return

    with op.batch_alter_table("agent_definitions") as batch_op:
        batch_op.drop_column("color")
