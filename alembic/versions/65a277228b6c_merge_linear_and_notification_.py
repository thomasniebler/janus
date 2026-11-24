"""Merge linear and notification preferences heads.

Revision ID: 65a277228b6c
Revises: 7d9c4b2a1e6f, a6d4c2b1e9f0
Create Date: 2026-04-20 20:58:50.846655

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "65a277228b6c"
down_revision: str | Sequence[str] | None = ("7d9c4b2a1e6f", "a6d4c2b1e9f0")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
