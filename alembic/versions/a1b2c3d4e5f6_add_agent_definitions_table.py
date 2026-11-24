"""Add agent_definitions table.

Revision ID: a1b2c3d4e5f6
Revises: 4b7f60d7d4d2
Create Date: 2026-04-06 20:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "4b7f60d7d4d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "agent_definitions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("system_prompt", sa.String(), nullable=False),
        sa.Column("mail_access", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("calendar_access", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("jira_access", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index(op.f("ix_agent_definitions_id"), "agent_definitions", ["id"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_agent_definitions_id"), table_name="agent_definitions")
    op.drop_table("agent_definitions")
