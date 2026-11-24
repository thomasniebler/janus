"""Add parent-task -> child-task runtime delegations (JAN-200).

Additive only: the legacy ``agent_handoff_runs`` table is left untouched. It is
migrated and dropped separately (JAN-202) once this replacement is proven.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b8d2f4a6c1e3"
down_revision: str | None = "a7c4e9b21d05"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the owner-scoped delegation table."""
    op.create_table(
        "runtime_delegations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("delegation_id", sa.String(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("parent_task_id", sa.String(), nullable=False),
        sa.Column("parent_step_id", sa.String(), nullable=True),
        sa.Column("child_task_id", sa.String(), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=True),
        sa.Column("depth", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("requested_tool_ids", sa.JSON(), nullable=False),
        sa.Column("allowed_tool_ids", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="queued"),
        sa.Column("claim_token", sa.String(), nullable=True),
        sa.Column("claimed_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("result", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_runtime_delegations_id", "runtime_delegations", ["id"], unique=False)
    op.create_index("ix_runtime_delegations_delegation_id", "runtime_delegations", ["delegation_id"], unique=True)
    op.create_index("ix_runtime_delegations_user_id", "runtime_delegations", ["user_id"], unique=False)
    op.create_index("ix_runtime_delegations_parent_task_id", "runtime_delegations", ["parent_task_id"], unique=False)
    op.create_index("ix_runtime_delegations_child_task_id", "runtime_delegations", ["child_task_id"], unique=True)
    op.create_index("ix_runtime_delegations_conversation_id", "runtime_delegations", ["conversation_id"], unique=False)
    op.create_index("ix_runtime_delegations_status", "runtime_delegations", ["status"], unique=False)


def downgrade() -> None:
    """Remove runtime delegations."""
    op.drop_index("ix_runtime_delegations_status", table_name="runtime_delegations")
    op.drop_index("ix_runtime_delegations_conversation_id", table_name="runtime_delegations")
    op.drop_index("ix_runtime_delegations_child_task_id", table_name="runtime_delegations")
    op.drop_index("ix_runtime_delegations_parent_task_id", table_name="runtime_delegations")
    op.drop_index("ix_runtime_delegations_user_id", table_name="runtime_delegations")
    op.drop_index("ix_runtime_delegations_delegation_id", table_name="runtime_delegations")
    op.drop_index("ix_runtime_delegations_id", table_name="runtime_delegations")
    op.drop_table("runtime_delegations")
