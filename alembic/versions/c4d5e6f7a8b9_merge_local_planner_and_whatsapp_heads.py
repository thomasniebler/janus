"""Merge local planner and WhatsApp foundation heads.

Revision ID: c4d5e6f7a8b9
Revises: b7e4c1d2a3f0, f1b2c3d4e5f6
Create Date: 2026-07-06 20:20:00.000000
"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "c4d5e6f7a8b9"
down_revision: str | Sequence[str] | None = ("b7e4c1d2a3f0", "f1b2c3d4e5f6")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
