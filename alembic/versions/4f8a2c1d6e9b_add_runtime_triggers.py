"""Add durable plan-runtime trigger records and execution claims."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "4f8a2c1d6e9b"
down_revision: str | None = "3a6e9c1d4f8b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the additive trigger storage table."""
    op.create_table(
        "runtime_triggers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("trigger_id", sa.String(), nullable=False),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.Column("task_id", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("claimed_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id", "idempotency_key", name="uq_runtime_trigger_task_key"),
        sa.UniqueConstraint("trigger_id"),
    )
    op.create_index("ix_runtime_triggers_id", "runtime_triggers", ["id"], unique=False)
    op.create_index("ix_runtime_triggers_trigger_id", "runtime_triggers", ["trigger_id"], unique=True)
    op.create_index("ix_runtime_triggers_task_id", "runtime_triggers", ["task_id"], unique=False)
    op.create_index("ix_runtime_triggers_status", "runtime_triggers", ["status"], unique=False)


def downgrade() -> None:
    """Remove durable trigger storage."""
    op.drop_index("ix_runtime_triggers_status", table_name="runtime_triggers")
    op.drop_index("ix_runtime_triggers_task_id", table_name="runtime_triggers")
    op.drop_index("ix_runtime_triggers_trigger_id", table_name="runtime_triggers")
    op.drop_index("ix_runtime_triggers_id", table_name="runtime_triggers")
    op.drop_table("runtime_triggers")
