"""Add dashboard widget prompt overrides.

Revision ID: 4b7f1c2d3e4a
Revises: 2d4e6f8a9b0c
Create Date: 2026-05-17 10:35:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4b7f1c2d3e4a"
down_revision: str | None = "2d4e6f8a9b0c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add per-widget prompt override storage."""
    with op.batch_alter_table("dashboard_widget_preferences") as batch_op:
        batch_op.add_column(sa.Column("prompt_override", sa.Text(), nullable=True))


def downgrade() -> None:
    """Drop per-widget prompt override storage."""
    with op.batch_alter_table("dashboard_widget_preferences") as batch_op:
        batch_op.drop_column("prompt_override")
