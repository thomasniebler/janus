"""Add dashboard widget preferences.

Revision ID: 2d4e6f8a9b0c
Revises: 1c2d3e4f5a6b
Create Date: 2026-05-17 10:20:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2d4e6f8a9b0c"
down_revision: str | None = "1c2d3e4f5a6b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create per-user dashboard widget preference storage."""
    op.create_table(
        "dashboard_widget_preferences",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column(
            "widget_key",
            sa.Enum("BRIEFING", "IMPORTANT_EMAILS", "SCHEDULE", "TASKS", name="dashboardwidgetkey"),
            nullable=False,
        ),
        sa.Column("is_visible", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_collapsed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "widget_key", name="uq_dashboard_widget_preferences_user_widget"),
    )
    op.create_index(
        op.f("ix_dashboard_widget_preferences_id"),
        "dashboard_widget_preferences",
        ["id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_dashboard_widget_preferences_user_id"),
        "dashboard_widget_preferences",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_dashboard_widget_preferences_widget_key"),
        "dashboard_widget_preferences",
        ["widget_key"],
        unique=False,
    )


def downgrade() -> None:
    """Drop per-user dashboard widget preference storage."""
    op.drop_index(op.f("ix_dashboard_widget_preferences_widget_key"), table_name="dashboard_widget_preferences")
    op.drop_index(op.f("ix_dashboard_widget_preferences_user_id"), table_name="dashboard_widget_preferences")
    op.drop_index(op.f("ix_dashboard_widget_preferences_id"), table_name="dashboard_widget_preferences")
    op.drop_table("dashboard_widget_preferences")
