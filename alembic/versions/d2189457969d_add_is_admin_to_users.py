"""add is_admin to users.

Revision ID: d2189457969d
Revises: 318b4a706a6e
Create Date: 2026-03-12 09:10:44.230938

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d2189457969d"
down_revision: str | Sequence[str] | None = "318b4a706a6e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("users", sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("users", "is_admin")
