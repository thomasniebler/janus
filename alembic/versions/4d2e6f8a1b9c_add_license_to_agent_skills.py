"""Add license column to agent_skills.

Revision ID: 4d2e6f8a1b9c
Revises: 152cb34fcb7b
Create Date: 2026-05-27 20:10:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4d2e6f8a1b9c"
down_revision: str | None = "152cb34fcb7b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("agent_skills", sa.Column("license", sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("agent_skills", "license")
