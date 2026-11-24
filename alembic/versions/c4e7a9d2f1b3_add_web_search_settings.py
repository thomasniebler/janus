"""Add optional agent web-search settings columns (JAN-211).

Additive and nullable only: existing installs keep web search disabled until an
admin picks a provider in Settings.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c4e7a9d2f1b3"
down_revision: str | None = "b8d2f4a6c1e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the web-search provider, encrypted API key, and base URL columns."""
    op.add_column("settings", sa.Column("web_search_provider", sa.String(), nullable=True))
    op.add_column("settings", sa.Column("web_search_api_key", sa.String(), nullable=True))
    op.add_column("settings", sa.Column("web_search_base_url", sa.String(), nullable=True))


def downgrade() -> None:
    """Drop the web-search settings columns."""
    with op.batch_alter_table("settings") as batch_op:
        batch_op.drop_column("web_search_base_url")
        batch_op.drop_column("web_search_api_key")
        batch_op.drop_column("web_search_provider")
