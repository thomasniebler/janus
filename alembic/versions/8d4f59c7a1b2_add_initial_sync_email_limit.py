"""Add initial sync email limit setting.

Revision ID: 8d4f59c7a1b2
Revises: 34bbd1c436f8
Create Date: 2026-04-10 20:55:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8d4f59c7a1b2"
down_revision: str | Sequence[str] | None = "34bbd1c436f8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("settings") as batch_op:
        batch_op.add_column(sa.Column("initial_sync_email_limit", sa.Integer(), nullable=False, server_default="100"))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("settings") as batch_op:
        batch_op.drop_column("initial_sync_email_limit")
