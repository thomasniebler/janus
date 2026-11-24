"""Add trigger definitions, per-user tool policies, and character instruction fields (JAN-228/229/230).

* ``trigger_definitions``: saved objective + pre-selected tools + manual,
  recurring-schedule, or event activation.
* ``runtime_triggers.definition_id`` / ``runtime_triggers.output``: which
  definition a run came from, and the run's raw final answer.
* ``tool_policies``: per-user "block without confirmation" overrides.
* ``character_profiles.additional_instructions`` /
  ``character_profiles.memory_instructions``.

Revision ID: e6f9a3b4c5d7
Revises: d5e8f1a2b3c4
Create Date: 2026-09-26 23:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e6f9a3b4c5d7"
down_revision: str | Sequence[str] | None = "d5e8f1a2b3c4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "trigger_definitions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("builtin_key", sa.String(), nullable=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("tool_ids", sa.JSON(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("schedule", sa.JSON(), nullable=True),
        sa.Column("timezone", sa.String(), nullable=False, server_default="UTC"),
        sa.Column("event_type", sa.String(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("origin", sa.String(), nullable=False),
        sa.Column("next_run_at", sa.DateTime(), nullable=True),
        sa.Column("last_run_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "builtin_key", name="uq_trigger_definitions_user_builtin"),
    )
    op.create_index("ix_trigger_definitions_id", "trigger_definitions", ["id"], unique=False)
    op.create_index("ix_trigger_definitions_user_id", "trigger_definitions", ["user_id"], unique=False)
    op.create_index("ix_trigger_definitions_event_type", "trigger_definitions", ["event_type"], unique=False)
    op.create_index("ix_trigger_definitions_next_run_at", "trigger_definitions", ["next_run_at"], unique=False)

    with op.batch_alter_table("runtime_triggers") as batch_op:
        batch_op.add_column(sa.Column("definition_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("output", sa.Text(), nullable=True))
        batch_op.create_foreign_key(
            "fk_runtime_triggers_definition_id",
            "trigger_definitions",
            ["definition_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index("ix_runtime_triggers_definition_id", ["definition_id"], unique=False)

    op.create_table(
        "tool_policies",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("tool_id", sa.String(), nullable=False),
        sa.Column("block_without_confirmation", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "tool_id", name="uq_tool_policies_user_tool"),
    )
    op.create_index("ix_tool_policies_id", "tool_policies", ["id"], unique=False)
    op.create_index("ix_tool_policies_user_id", "tool_policies", ["user_id"], unique=False)

    with op.batch_alter_table("character_profiles") as batch_op:
        batch_op.add_column(sa.Column("additional_instructions", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("memory_instructions", sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("character_profiles") as batch_op:
        batch_op.drop_column("memory_instructions")
        batch_op.drop_column("additional_instructions")

    op.drop_index("ix_tool_policies_user_id", table_name="tool_policies")
    op.drop_index("ix_tool_policies_id", table_name="tool_policies")
    op.drop_table("tool_policies")

    with op.batch_alter_table("runtime_triggers") as batch_op:
        batch_op.drop_index("ix_runtime_triggers_definition_id")
        batch_op.drop_constraint("fk_runtime_triggers_definition_id", type_="foreignkey")
        batch_op.drop_column("output")
        batch_op.drop_column("definition_id")

    op.drop_index("ix_trigger_definitions_next_run_at", table_name="trigger_definitions")
    op.drop_index("ix_trigger_definitions_event_type", table_name="trigger_definitions")
    op.drop_index("ix_trigger_definitions_user_id", table_name="trigger_definitions")
    op.drop_index("ix_trigger_definitions_id", table_name="trigger_definitions")
    op.drop_table("trigger_definitions")
