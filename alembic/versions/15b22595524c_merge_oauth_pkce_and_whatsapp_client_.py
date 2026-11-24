"""Merge OAuth PKCE and WhatsApp client UUID heads.

Revision ID: 15b22595524c
Revises: 18dfcb8aff1b, 31b66872e197
Create Date: 2026-09-16 11:22:37.185368

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "15b22595524c"
down_revision: str | Sequence[str] | None = ("18dfcb8aff1b", "31b66872e197")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
