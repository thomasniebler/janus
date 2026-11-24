"""Add persisted LLM status probe fields.

Revision ID: 6baf2fdc37bf
Revises: 9f8d5b6f2c11
Create Date: 2026-04-04 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "6baf2fdc37bf"
down_revision: str | Sequence[str] | None = "9f8d5b6f2c11"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("settings") as batch_op:
        batch_op.add_column(sa.Column("llm_status", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("llm_status_checked_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("llm_status_message", sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("settings") as batch_op:
        batch_op.drop_column("llm_status_message")
        batch_op.drop_column("llm_status_checked_at")
        batch_op.drop_column("llm_status")
