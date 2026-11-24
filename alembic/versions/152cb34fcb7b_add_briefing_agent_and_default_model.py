"""Add briefing agent and default model.

Revision ID: 152cb34fcb7b
Revises: 37b30d7c85e1
Create Date: 2026-05-27 19:47:24.534398

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "152cb34fcb7b"
down_revision: str | Sequence[str] | None = "37b30d7c85e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("agent_definitions", sa.Column("default_model", sa.String(), nullable=True))
    with op.batch_alter_table("dashboard_widget_preferences") as batch_op:
        batch_op.add_column(sa.Column("briefing_agent_definition_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_dwp_briefing_agent_definition_id",
            "agent_definitions",
            ["briefing_agent_definition_id"],
            ["id"],
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("dashboard_widget_preferences") as batch_op:
        batch_op.drop_constraint("fk_dwp_briefing_agent_definition_id", type_="foreignkey")
        batch_op.drop_column("briefing_agent_definition_id")
    op.drop_column("agent_definitions", "default_model")
