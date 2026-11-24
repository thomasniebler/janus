"""Add briefing email window to dashboard widget preferences.

Revision ID: e4f5a6b7c8d9
Revises: c0f1e2d3c4b5
Create Date: 2026-05-21 22:15:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e4f5a6b7c8d9"
down_revision: str | None = "c0f1e2d3c4b5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BRIEFING_EMAIL_WINDOW_ENUM = sa.Enum(
    "TODAY",
    "YESTERDAY",
    "LAST_24_HOURS",
    name="briefingemailwindow",
    native_enum=False,
    create_constraint=True,
)


def upgrade() -> None:
    """Add briefing email window storage."""
    with op.batch_alter_table("dashboard_widget_preferences") as batch_op:
        batch_op.add_column(
            sa.Column(
                "briefing_email_window",
                _BRIEFING_EMAIL_WINDOW_ENUM,
                nullable=True,
            )
        )


def downgrade() -> None:
    """Drop briefing email window storage."""
    with op.batch_alter_table("dashboard_widget_preferences") as batch_op:
        batch_op.drop_column("briefing_email_window")
