"""Add local task hierarchy, urgency, ordering, and agent capabilities.

Revision ID: 3a6e9c1d4f8b
Revises: 0d1e2f3a4b5c
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "3a6e9c1d4f8b"
down_revision: str | None = "0d1e2f3a4b5c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add backward-compatible local planner and agent-definition fields."""
    with op.batch_alter_table("planner_tasks") as batch_op:
        batch_op.add_column(sa.Column("parent_task_id", sa.Integer(), nullable=True))
        batch_op.add_column(
            sa.Column(
                "urgency",
                sa.Enum("LOW", "NORMAL", "HIGH", "CRITICAL", name="taskurgency", native_enum=False),
                nullable=False,
                server_default="NORMAL",
            )
        )
        batch_op.add_column(sa.Column("sort_order", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_planner_tasks_parent_task_id",
            "planner_tasks",
            ["parent_task_id"],
            ["id"],
        )
    op.create_index("ix_planner_tasks_parent_task_id", "planner_tasks", ["parent_task_id"], unique=False)

    with op.batch_alter_table("agent_definitions") as batch_op:
        batch_op.add_column(
            sa.Column("planner_task_read_access", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch_op.add_column(
            sa.Column("planner_task_write_access", sa.Boolean(), nullable=False, server_default=sa.false())
        )


def downgrade() -> None:
    """Remove the local planner hierarchy and capability fields."""
    with op.batch_alter_table("agent_definitions") as batch_op:
        batch_op.drop_column("planner_task_write_access")
        batch_op.drop_column("planner_task_read_access")
    op.drop_index("ix_planner_tasks_parent_task_id", table_name="planner_tasks")
    with op.batch_alter_table("planner_tasks") as batch_op:
        batch_op.drop_constraint("fk_planner_tasks_parent_task_id", type_="foreignkey")
        batch_op.drop_column("sort_order")
        batch_op.drop_column("urgency")
        batch_op.drop_column("parent_task_id")
