"""Add Topic Focus dashboard widget key.

Revision ID: 5c8d7e6f4a3b
Revises: 4b7f1c2d3e4a
Create Date: 2026-05-18 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5c8d7e6f4a3b"
down_revision: str | None = "4b7f1c2d3e4a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_DASHBOARD_WIDGET_KEY_ENUM = sa.Enum(
    "BRIEFING",
    "IMPORTANT_EMAILS",
    "SCHEDULE",
    "TASKS",
    name="dashboardwidgetkey",
    native_enum=False,
    create_constraint=True,
)
_NEW_DASHBOARD_WIDGET_KEY_ENUM = sa.Enum(
    "BRIEFING",
    "TOPIC_FOCUS",
    "IMPORTANT_EMAILS",
    "SCHEDULE",
    "TASKS",
    name="dashboardwidgetkey",
    native_enum=False,
    create_constraint=True,
)


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("dashboard_widget_preferences") as batch_op:
        batch_op.alter_column(
            "widget_key",
            existing_type=sa.String(length=16),
            type_=_NEW_DASHBOARD_WIDGET_KEY_ENUM,
            existing_nullable=False,
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("dashboard_widget_preferences") as batch_op:
        batch_op.alter_column(
            "widget_key",
            existing_type=_NEW_DASHBOARD_WIDGET_KEY_ENUM,
            type_=_OLD_DASHBOARD_WIDGET_KEY_ENUM,
            existing_nullable=False,
        )
