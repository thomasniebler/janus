"""Merge the UI shell preference and topic-candidate confirmation heads.

Revision ID: 6d1f4a8c07be
Revises: 2be48bcaff15, 3c7d9e1a4b62
Create Date: 2026-09-21 14:45:00.000000
"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "6d1f4a8c07be"
down_revision: str | Sequence[str] | None = ("2be48bcaff15", "3c7d9e1a4b62")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
