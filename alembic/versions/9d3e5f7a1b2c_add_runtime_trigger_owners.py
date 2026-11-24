"""Bind runtime triggers to authenticated users."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "9d3e5f7a1b2c"
down_revision: str | None = "8c2d4e6f7a1b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add a nullable owner column so legacy trigger rows remain readable."""
    op.add_column("runtime_triggers", sa.Column("user_id", sa.Integer(), nullable=True))
    op.create_index("ix_runtime_triggers_user_id", "runtime_triggers", ["user_id"], unique=False)


def downgrade() -> None:
    """Remove the optional runtime-trigger owner binding."""
    op.drop_index("ix_runtime_triggers_user_id", table_name="runtime_triggers")
    op.drop_column("runtime_triggers", "user_id")
