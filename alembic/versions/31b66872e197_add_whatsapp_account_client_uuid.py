"""Persist the Neonize client UUID used to pair a WhatsApp account."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "31b66872e197"
down_revision: str | None = "e5f6a7b8c9d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the nullable client_uuid column to whatsapp_accounts."""
    op.add_column("whatsapp_accounts", sa.Column("client_uuid", sa.String(), nullable=True))


def downgrade() -> None:
    """Remove the client_uuid column from whatsapp_accounts."""
    op.drop_column("whatsapp_accounts", "client_uuid")
