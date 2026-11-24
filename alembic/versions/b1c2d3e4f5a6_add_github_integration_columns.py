"""Add GitHub integration columns.

Revision ID: b1c2d3e4f5a6
Revises: 34bbd1c436f8
Create Date: 2026-04-10 22:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b1c2d3e4f5a6"
down_revision: str | Sequence[str] | None = "c3f9e1a6b7d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Add github_access to agent_definitions
    op.add_column(
        "agent_definitions",
        sa.Column("github_access", sa.Boolean(), nullable=False, server_default="0"),
    )
    # Add github_token and github_default_repo to settings
    op.add_column("settings", sa.Column("github_token", sa.String(), nullable=True))
    op.add_column("settings", sa.Column("github_default_repo", sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("settings", "github_default_repo")
    op.drop_column("settings", "github_token")
    op.drop_column("agent_definitions", "github_access")
