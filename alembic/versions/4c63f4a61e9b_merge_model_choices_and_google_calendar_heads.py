"""Merge the model-choice (JAN-233) and Google calendar selection heads.

Revision ID: 4c63f4a61e9b
Revises: 8109db6b1275, a8b1c2d3e4f5
Create Date: 2026-09-27 14:10:00.000000
"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "4c63f4a61e9b"
down_revision: str | Sequence[str] | None = ("8109db6b1275", "a8b1c2d3e4f5")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
