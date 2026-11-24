"""Add Ollama API key column.

Revision ID: 8c4f1a2b3d4e
Revises: 623957070a5f
Create Date: 2026-05-11 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8c4f1a2b3d4e"
down_revision: str | None = "623957070a5f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    settings_columns = {column["name"] for column in inspector.get_columns("settings")}

    if "ollama_api_key" not in settings_columns:
        op.add_column("settings", sa.Column("ollama_api_key", sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    settings_columns = {column["name"] for column in inspector.get_columns("settings")}

    if "ollama_api_key" in settings_columns:
        with op.batch_alter_table("settings") as batch_op:
            batch_op.drop_column("ollama_api_key")
