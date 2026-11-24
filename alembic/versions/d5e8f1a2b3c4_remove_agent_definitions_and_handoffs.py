"""Remove the legacy agent-definition runtime: definitions, skill links, handoffs (JAN-169, JAN-227).

Janus runs a single agent whose identity comes from the user's character
constitution and whose skills are one flat library, so the per-conversation
``agent_definitions`` model, its ``agent_definition_skills`` join table, the
``agent_handoff_runs`` history, and every foreign key pointing at
``agent_definitions`` are dropped. ``agent_skills`` stays: it is the skill
library.

Downgrade restores the empty schema only; dropped rows are not recoverable.

Revision ID: d5e8f1a2b3c4
Revises: 44b79f99846d
Create Date: 2026-09-26 22:10:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d5e8f1a2b3c4"
down_revision: str | Sequence[str] | None = "44b79f99846d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: (table, column) pairs that reference ``agent_definitions.id``.
_REFERENCING_COLUMNS = (
    ("dashboard_widget_preferences", "briefing_agent_definition_id"),
    ("settings", "mail_ingestion_agent_definition_id"),
    ("agent_memory_facts", "source_agent_definition_id"),
)


def _columns(inspector: sa.Inspector, table: str) -> set[str]:
    if not inspector.has_table(table):
        return set()
    return {column["name"] for column in inspector.get_columns(table)}


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    for table in ("agent_handoff_runs", "agent_definition_skills"):
        if inspector.has_table(table):
            op.drop_table(table)

    for table, column in _REFERENCING_COLUMNS:
        if column not in _columns(inspector, table):
            continue
        index_name = f"ix_{table}_{column}"
        existing_indexes = {index["name"] for index in inspector.get_indexes(table)}
        # Recreating the table drops the column together with its (possibly
        # unnamed, on SQLite) foreign-key constraint. PostgreSQL drops the
        # column's constraints natively and must not rebuild native enum types.
        recreate = "always" if bind.dialect.name == "sqlite" else "auto"
        with op.batch_alter_table(table, recreate=recreate) as batch_op:
            if index_name in existing_indexes:
                batch_op.drop_index(index_name)
            batch_op.drop_column(column)

    if inspector.has_table("agent_definitions"):
        op.drop_table("agent_definitions")


def downgrade() -> None:
    """Downgrade schema (empty tables; dropped rows are gone)."""
    op.create_table(
        "agent_definitions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("system_prompt", sa.String(), nullable=False),
        sa.Column("mail_access", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("calendar_access", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("jira_access", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("planner_task_read_access", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("planner_task_write_access", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("github_access", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("linear_access", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("memory_access", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("topic_access", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("trigger_access", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("agent_management_access", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("default_model", sa.String(), nullable=True),
        sa.Column("color", sa.String(length=7), nullable=False, server_default="#6366F1"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_agent_definitions_id"), "agent_definitions", ["id"], unique=False)

    op.create_table(
        "agent_definition_skills",
        sa.Column("agent_definition_id", sa.Integer(), nullable=False),
        sa.Column("agent_skill_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["agent_definition_id"], ["agent_definitions.id"]),
        sa.ForeignKeyConstraint(["agent_skill_id"], ["agent_skills.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("agent_definition_id", "agent_skill_id"),
    )

    # PostgreSQL keeps the enum type after the table is dropped, so create it only when missing.
    handoff_status = postgresql.ENUM(
        "QUEUED", "RUNNING", "COMPLETED", "FAILED", name="agenthandoffstatus", create_type=False
    )
    handoff_status.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "agent_handoff_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("source_agent_definition_id", sa.Integer(), nullable=False),
        sa.Column("target_agent_definition_id", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            handoff_status,
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
    for column in ("id", "conversation_id", "user_id", "source_agent_definition_id", "target_agent_definition_id"):
        op.create_index(f"ix_agent_handoff_runs_{column}", "agent_handoff_runs", [column], unique=False)
    op.create_index("ix_agent_handoff_runs_status", "agent_handoff_runs", ["status"], unique=False)

    with op.batch_alter_table("dashboard_widget_preferences") as batch_op:
        batch_op.add_column(sa.Column("briefing_agent_definition_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_dwp_briefing_agent_definition_id",
            "agent_definitions",
            ["briefing_agent_definition_id"],
            ["id"],
        )
    with op.batch_alter_table("settings") as batch_op:
        batch_op.add_column(sa.Column("mail_ingestion_agent_definition_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            # PostgreSQL caps identifiers at 63 characters (see 9d3c1b2a4f6e).
            "fk_settings_mail_ingestion_agent_definition_id"
            if op.get_bind().dialect.name == "postgresql"
            else "fk_settings_mail_ingestion_agent_definition_id_agent_definitions",
            "agent_definitions",
            ["mail_ingestion_agent_definition_id"],
            ["id"],
        )
    with op.batch_alter_table("agent_memory_facts") as batch_op:
        batch_op.add_column(sa.Column("source_agent_definition_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_agent_memory_facts_source_agent_definition_id",
            "agent_definitions",
            ["source_agent_definition_id"],
            ["id"],
        )
        batch_op.create_index(
            "ix_agent_memory_facts_source_agent_definition_id", ["source_agent_definition_id"], unique=False
        )
