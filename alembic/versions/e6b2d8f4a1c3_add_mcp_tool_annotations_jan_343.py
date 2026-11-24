"""Store the discovered MCP tool annotations and description scan (JAN-343).

Additive only: ``mcp_discovered_tools.annotations`` is a nullable JSON column
holding the server's hint annotations (``readOnlyHint`` and friends), and
``description_scan`` a nullable JSON column with the prompt-injection scan of
the server-written description. Existing rows keep ``NULL`` until their server
is discovered again, which treats them as not read-only (every call asks
first) and not flagged.

Revision ID: e6b2d8f4a1c3
Revises: d8a2f4c6b1e9
Create Date: 2026-11-09 21:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e6b2d8f4a1c3"
down_revision: str | Sequence[str] | None = "d8a2f4c6b1e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the nullable annotations and description-scan columns."""
    with op.batch_alter_table("mcp_discovered_tools") as batch_op:
        batch_op.add_column(sa.Column("annotations", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("description_scan", sa.JSON(), nullable=True))


def downgrade() -> None:
    """Drop both columns."""
    with op.batch_alter_table("mcp_discovered_tools") as batch_op:
        batch_op.drop_column("description_scan")
        batch_op.drop_column("annotations")
