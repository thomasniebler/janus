"""Add tracked agent handoff runs.

Revision ID: c9a8b7e6d5f4
Revises: 5e735b5364f2
Create Date: 2026-04-19 00:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c9a8b7e6d5f4"
down_revision: str | None = "5e735b5364f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("agent_handoff_runs"):
        return

    op.create_table(
        "agent_handoff_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("source_agent_definition_id", sa.Integer(), nullable=False),
        sa.Column("target_agent_definition_id", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("QUEUED", "RUNNING", "COMPLETED", "FAILED", name="agenthandoffstatus"),
            nullable=False,
            server_default="QUEUED",
        ),
        sa.Column("requested_task", sa.Text(), nullable=False),
        sa.Column("result_summary", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["source_agent_definition_id"], ["agent_definitions.id"]),
        sa.ForeignKeyConstraint(["target_agent_definition_id"], ["agent_definitions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_agent_handoff_runs_id"), "agent_handoff_runs", ["id"], unique=False)
    op.create_index(
        op.f("ix_agent_handoff_runs_conversation_id"),
        "agent_handoff_runs",
        ["conversation_id"],
        unique=False,
    )
    op.create_index(op.f("ix_agent_handoff_runs_user_id"), "agent_handoff_runs", ["user_id"], unique=False)
    op.create_index(
        op.f("ix_agent_handoff_runs_source_agent_definition_id"),
        "agent_handoff_runs",
        ["source_agent_definition_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_agent_handoff_runs_target_agent_definition_id"),
        "agent_handoff_runs",
        ["target_agent_definition_id"],
        unique=False,
    )
    op.create_index(op.f("ix_agent_handoff_runs_status"), "agent_handoff_runs", ["status"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("agent_handoff_runs"):
        return

    op.drop_index(op.f("ix_agent_handoff_runs_status"), table_name="agent_handoff_runs")
    op.drop_index(op.f("ix_agent_handoff_runs_target_agent_definition_id"), table_name="agent_handoff_runs")
    op.drop_index(op.f("ix_agent_handoff_runs_source_agent_definition_id"), table_name="agent_handoff_runs")
    op.drop_index(op.f("ix_agent_handoff_runs_user_id"), table_name="agent_handoff_runs")
    op.drop_index(op.f("ix_agent_handoff_runs_conversation_id"), table_name="agent_handoff_runs")
    op.drop_index(op.f("ix_agent_handoff_runs_id"), table_name="agent_handoff_runs")
    op.drop_table("agent_handoff_runs")
