"""Add runtime_tool_selections for brain-selected, task-scoped toolsets (JAN-300).

Additive only: one new owner-scoped table holding the toolset a plan step was
offered (``tool_ids``, ``NULL`` = every authorized toolbox), the selection's
source and reason, the selector's raw proposal, the ids trimmed because they
were not authorized, and the ids a chat widened to. A retried or resumed step
reuses its row. No existing table changes.

Revision ID: b8d4f2a6c1e9
Revises: d7f9b1c3e5a7
Create Date: 2026-10-12 09:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b8d4f2a6c1e9"
down_revision: str | Sequence[str] | None = "d7f9b1c3e5a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the table and its indexes."""
    op.create_table(
        "runtime_tool_selections",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.String(), nullable=False),
        sa.Column("step_id", sa.String(), nullable=False),
        sa.Column("task_mode", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("tool_ids", sa.JSON(), nullable=True),
        sa.Column("proposed_tool_ids", sa.JSON(), nullable=False),
        sa.Column("trimmed_tool_ids", sa.JSON(), nullable=False),
        sa.Column("widened_tool_ids", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "task_id", "step_id", name="uq_runtime_tool_selection_step"),
    )
    op.create_index("ix_runtime_tool_selections_id", "runtime_tool_selections", ["id"], unique=False)
    op.create_index("ix_runtime_tool_selections_user_id", "runtime_tool_selections", ["user_id"], unique=False)
    op.create_index("ix_runtime_tool_selections_task_id", "runtime_tool_selections", ["task_id"], unique=False)


def downgrade() -> None:
    """Drop the table."""
    op.drop_index("ix_runtime_tool_selections_task_id", table_name="runtime_tool_selections")
    op.drop_index("ix_runtime_tool_selections_user_id", table_name="runtime_tool_selections")
    op.drop_index("ix_runtime_tool_selections_id", table_name="runtime_tool_selections")
    op.drop_table("runtime_tool_selections")
