"""Persist retry scheduling state for durable runtime triggers."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e5f6a7b8c9d0"
down_revision: str | None = "d4e5f6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add additive retry counters and delayed-retry timestamps."""
    op.add_column(
        "runtime_triggers",
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("runtime_triggers", sa.Column("next_attempt_at", sa.DateTime(), nullable=True))
    op.create_index("ix_runtime_triggers_next_attempt_at", "runtime_triggers", ["next_attempt_at"], unique=False)


def downgrade() -> None:
    """Remove durable retry scheduling state."""
    op.drop_index("ix_runtime_triggers_next_attempt_at", table_name="runtime_triggers")
    op.drop_column("runtime_triggers", "next_attempt_at")
    op.drop_column("runtime_triggers", "attempt_count")
