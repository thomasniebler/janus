"""Merge agent_definitions and conversations migration branches."""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "3324b96c4dd2"
down_revision: str | Sequence[str] | None = ("a1b2c3d4e5f6", "da2c5442a1ad")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
