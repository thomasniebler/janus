"""Merge briefing and mail-ingestion migration heads.

Revision ID: f0a1b2c3d4e5
Revises: e4f5a6b7c8d9, 7e3a1c5d9b2f
Create Date: 2026-05-22 12:30:00.000000
"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "f0a1b2c3d4e5"
down_revision: str | Sequence[str] | None = ("e4f5a6b7c8d9", "7e3a1c5d9b2f")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
