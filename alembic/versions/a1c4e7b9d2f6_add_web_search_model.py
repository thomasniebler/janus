"""Add the web search model column for provider-native web search.

Additive only: the nullable ``settings.web_search_model`` holds the model that
answers a search when ``web_search_provider`` is OpenAI, Gemini, Anthropic or
OpenRouter (those reuse the provider key stored for chat). ``NULL`` means the
provider's default model; Brave and SearXNG ignore it. Existing installs keep
their backend and behave exactly as before.

Revision ID: a1c4e7b9d2f6
Revises: f3c5e7a9b1d4
Create Date: 2026-11-28 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1c4e7b9d2f6"
down_revision: str | Sequence[str] | None = "f3c5e7a9b1d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the nullable model column."""
    with op.batch_alter_table("settings") as batch_op:
        batch_op.add_column(sa.Column("web_search_model", sa.String(), nullable=True))


def downgrade() -> None:
    """Drop the model column."""
    with op.batch_alter_table("settings") as batch_op:
        batch_op.drop_column("web_search_model")
