"""Add agent memory tables.

Revision ID: c3f9e1a6b7d2
Revises: 8d4f59c7a1b2
Create Date: 2026-04-10 21:55:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3f9e1a6b7d2"
down_revision: str | None = "8d4f59c7a1b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add durable agent memory tables and agent-definition access flag."""
    with op.batch_alter_table("agent_definitions") as batch_op:
        batch_op.add_column(sa.Column("memory_access", sa.Boolean(), nullable=False, server_default=sa.false()))

    op.create_table(
        "agent_memory_compaction_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("QUEUED", "RUNNING", "COMPLETED", "FAILED", name="agentmemorycompactionstatus"),
            nullable=False,
        ),
        sa.Column("result_summary", sa.String(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("facts_considered", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("facts_archived", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("facts_created", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_agent_memory_compaction_runs_id"),
        "agent_memory_compaction_runs",
        ["id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_agent_memory_compaction_runs_user_id"),
        "agent_memory_compaction_runs",
        ["user_id"],
        unique=False,
    )

    op.create_table(
        "agent_memory_facts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("summary", sa.String(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("source", sa.Enum("USER", "AGENT", "COMPACTION", name="agentmemorysource"), nullable=False),
        sa.Column("status", sa.Enum("ACTIVE", "ARCHIVED", name="agentmemorystatus"), nullable=False),
        sa.Column("source_conversation_id", sa.Integer(), nullable=True),
        sa.Column("source_agent_definition_id", sa.Integer(), nullable=True),
        sa.Column("compacted_into_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("last_accessed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["compacted_into_id"], ["agent_memory_facts.id"]),
        sa.ForeignKeyConstraint(["source_agent_definition_id"], ["agent_definitions.id"]),
        sa.ForeignKeyConstraint(["source_conversation_id"], ["conversations.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_agent_memory_facts_id"), "agent_memory_facts", ["id"], unique=False)
    op.create_index(op.f("ix_agent_memory_facts_user_id"), "agent_memory_facts", ["user_id"], unique=False)
    op.create_index(op.f("ix_agent_memory_facts_status"), "agent_memory_facts", ["status"], unique=False)
    op.create_index(
        op.f("ix_agent_memory_facts_source_conversation_id"),
        "agent_memory_facts",
        ["source_conversation_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_agent_memory_facts_source_agent_definition_id"),
        "agent_memory_facts",
        ["source_agent_definition_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_agent_memory_facts_compacted_into_id"),
        "agent_memory_facts",
        ["compacted_into_id"],
        unique=False,
    )


def downgrade() -> None:
    """Remove durable agent memory tables and agent-definition access flag."""
    op.drop_index(op.f("ix_agent_memory_facts_compacted_into_id"), table_name="agent_memory_facts")
    op.drop_index(op.f("ix_agent_memory_facts_source_agent_definition_id"), table_name="agent_memory_facts")
    op.drop_index(op.f("ix_agent_memory_facts_source_conversation_id"), table_name="agent_memory_facts")
    op.drop_index(op.f("ix_agent_memory_facts_status"), table_name="agent_memory_facts")
    op.drop_index(op.f("ix_agent_memory_facts_user_id"), table_name="agent_memory_facts")
    op.drop_index(op.f("ix_agent_memory_facts_id"), table_name="agent_memory_facts")
    op.drop_table("agent_memory_facts")

    op.drop_index(op.f("ix_agent_memory_compaction_runs_user_id"), table_name="agent_memory_compaction_runs")
    op.drop_index(op.f("ix_agent_memory_compaction_runs_id"), table_name="agent_memory_compaction_runs")
    op.drop_table("agent_memory_compaction_runs")

    with op.batch_alter_table("agent_definitions") as batch_op:
        batch_op.drop_column("memory_access")
