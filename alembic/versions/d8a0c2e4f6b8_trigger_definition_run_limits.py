"""Add per-definition run limits to trigger definitions.

``max_tool_calls`` and ``max_run_seconds`` bound one run of a definition:
``NULL`` keeps Janus's default (8 tool-calling rounds, 180 seconds) and ``0``
switches that limit off. Existing definitions get ``NULL`` and behave as before.

Revision ID: d8a0c2e4f6b8
Revises: c6f8d0e2a4b7
Create Date: 2026-11-26 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d8a0c2e4f6b8"
down_revision: str | Sequence[str] | None = "c6f8d0e2a4b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("trigger_definitions") as batch:
        batch.add_column(sa.Column("max_tool_calls", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("max_run_seconds", sa.Integer(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("trigger_definitions") as batch:
        batch.drop_column("max_run_seconds")
        batch.drop_column("max_tool_calls")
