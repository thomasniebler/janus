"""Persist owner-scoped plan graph state snapshots."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d4e5f6a7b8c9"
down_revision: str | None = "9d3e5f7a1b2c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the additive owner-scoped plan state table."""
    op.create_table(
        "plan_runtime_states",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.String(), nullable=False),
        sa.Column("node", sa.String(), nullable=False),
        sa.Column("plan_status", sa.String(), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("state", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "task_id", name="uq_plan_runtime_state_owner_task"),
    )
    op.create_index("ix_plan_runtime_states_id", "plan_runtime_states", ["id"], unique=False)
    op.create_index("ix_plan_runtime_states_user_id", "plan_runtime_states", ["user_id"], unique=False)
    op.create_index("ix_plan_runtime_states_task_id", "plan_runtime_states", ["task_id"], unique=False)
    op.create_index("ix_plan_runtime_states_node", "plan_runtime_states", ["node"], unique=False)
    op.create_index("ix_plan_runtime_states_plan_status", "plan_runtime_states", ["plan_status"], unique=False)


def downgrade() -> None:
    """Remove durable plan state snapshots."""
    op.drop_index("ix_plan_runtime_states_plan_status", table_name="plan_runtime_states")
    op.drop_index("ix_plan_runtime_states_node", table_name="plan_runtime_states")
    op.drop_index("ix_plan_runtime_states_task_id", table_name="plan_runtime_states")
    op.drop_index("ix_plan_runtime_states_user_id", table_name="plan_runtime_states")
    op.drop_index("ix_plan_runtime_states_id", table_name="plan_runtime_states")
    op.drop_table("plan_runtime_states")
