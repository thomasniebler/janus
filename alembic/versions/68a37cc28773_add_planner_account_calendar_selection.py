"""Add selected_calendar_ids to planner_accounts.

Revision ID: 68a37cc28773
Revises: 44b79f99846d
Create Date: 2026-09-26 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "68a37cc28773"
down_revision: str | Sequence[str] | None = "44b79f99846d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("planner_accounts") as batch_op:
        batch_op.add_column(sa.Column("selected_calendar_ids", sa.JSON(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("planner_accounts") as batch_op:
        batch_op.drop_column("selected_calendar_ids")
