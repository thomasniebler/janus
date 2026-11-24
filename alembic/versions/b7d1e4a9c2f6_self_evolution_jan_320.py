"""Add the self-evolution loop's settings and action tables (JAN-320).

Additive only: ``self_evolution_settings`` holds one row per user (the opt-in
switch, off by default, the Claude Code Routine trigger id with its encrypted
token, the target repository, and the production base URL), and
``self_evolution_actions`` records every action the loop attempts -- the audit
trail the daily caps are counted from. Both cascade with their user.

Revision ID: b7d1e4a9c2f6
Revises: f4b8d2c6a1e3
Create Date: 2026-10-08 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7d1e4a9c2f6"
down_revision: str | Sequence[str] | None = "f4b8d2c6a1e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create both tables and their indexes."""
    op.create_table(
        "self_evolution_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("routine_trigger_id", sa.String(), nullable=True),
        sa.Column("routine_token", sa.String(), nullable=True),
        sa.Column("github_repo", sa.String(), nullable=True),
        sa.Column("production_base_url", sa.String(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_self_evolution_settings_id", "self_evolution_settings", ["id"], unique=False)
    op.create_index("ix_self_evolution_settings_user_id", "self_evolution_settings", ["user_id"], unique=True)
    op.create_table(
        "self_evolution_actions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("subject", sa.String(), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("data", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("id", "user_id", "action", "subject", "created_at"):
        op.create_index(f"ix_self_evolution_actions_{column}", "self_evolution_actions", [column], unique=False)


def downgrade() -> None:
    """Drop both tables."""
    for column in ("created_at", "subject", "action", "user_id", "id"):
        op.drop_index(f"ix_self_evolution_actions_{column}", table_name="self_evolution_actions")
    op.drop_table("self_evolution_actions")
    op.drop_index("ix_self_evolution_settings_user_id", table_name="self_evolution_settings")
    op.drop_index("ix_self_evolution_settings_id", table_name="self_evolution_settings")
    op.drop_table("self_evolution_settings")
