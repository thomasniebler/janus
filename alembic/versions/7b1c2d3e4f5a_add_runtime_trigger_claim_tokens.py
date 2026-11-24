"""Add ownership tokens to durable trigger claims."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "7b1c2d3e4f5a"
down_revision: str | None = "4f8a2c1d6e9b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the nullable claim ownership token for existing trigger rows."""
    op.add_column("runtime_triggers", sa.Column("claim_token", sa.String(), nullable=True))
    op.create_index("ix_runtime_triggers_claim_token", "runtime_triggers", ["claim_token"], unique=False)


def downgrade() -> None:
    """Remove durable trigger claim ownership tokens."""
    op.drop_index("ix_runtime_triggers_claim_token", table_name="runtime_triggers")
    op.drop_column("runtime_triggers", "claim_token")
