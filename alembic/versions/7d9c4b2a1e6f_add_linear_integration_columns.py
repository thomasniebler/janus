"""Add Linear integration columns.

Revision ID: 7d9c4b2a1e6f
Revises: 2f1a6c3d4e5b
Create Date: 2026-04-20 13:10:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7d9c4b2a1e6f"
down_revision: str | None = "2f1a6c3d4e5b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    settings_columns = {column["name"] for column in inspector.get_columns("settings")}

    if "linear_api_key" not in settings_columns:
        op.add_column("settings", sa.Column("linear_api_key", sa.String(), nullable=True))
    if "linear_default_team_id" not in settings_columns:
        op.add_column("settings", sa.Column("linear_default_team_id", sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    settings_columns = {column["name"] for column in inspector.get_columns("settings")}

    if "linear_default_team_id" in settings_columns:
        op.drop_column("settings", "linear_default_team_id")
    if "linear_api_key" in settings_columns:
        op.drop_column("settings", "linear_api_key")
