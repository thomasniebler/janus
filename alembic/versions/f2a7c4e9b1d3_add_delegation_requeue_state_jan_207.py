"""Add requeue state to runtime_delegations for durable re-pickup (JAN-207).

Additive only: ``requeue_count`` (NOT NULL, default 0) records how often an
interrupted delegation was put back to ``queued`` (at most once), and
``requeued_at`` (nullable) is when, so a requeued row's lease runs from there.
Existing rows keep ``requeue_count = 0`` and ``requeued_at = NULL``, which is
exactly "never requeued". The trigger ``waiting`` status needs no schema
change: ``runtime_triggers.status`` is a plain string column.

Revision ID: f2a7c4e9b1d3
Revises: c3e5a7f9b1d2
Create Date: 2026-10-11 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f2a7c4e9b1d3"
down_revision: str | Sequence[str] | None = "c3e5a7f9b1d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add both columns."""
    with op.batch_alter_table("runtime_delegations") as batch_op:
        batch_op.add_column(sa.Column("requeue_count", sa.Integer(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("requeued_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    """Drop both columns."""
    with op.batch_alter_table("runtime_delegations") as batch_op:
        batch_op.drop_column("requeued_at")
        batch_op.drop_column("requeue_count")
