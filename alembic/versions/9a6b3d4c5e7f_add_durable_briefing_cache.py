"""Add durable briefing cache table.

Revision ID: 9a6b3d4c5e7f
Revises: 8c4f1a2b3d4e
Create Date: 2026-05-12 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9a6b3d4c5e7f"
down_revision: str | None = "8c4f1a2b3d4e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "briefing_cache_entries" not in inspector.get_table_names():
        op.create_table(
            "briefing_cache_entries",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("briefing_date", sa.String(), nullable=False),
            sa.Column("cache_identity", sa.String(), nullable=False),
            sa.Column("summary_hash", sa.String(), nullable=False),
            sa.Column("briefing", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "briefing_date",
                "cache_identity",
                "summary_hash",
                name="uq_briefing_cache_entry_key",
            ),
        )
        op.create_index(op.f("ix_briefing_cache_entries_id"), "briefing_cache_entries", ["id"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "briefing_cache_entries" in inspector.get_table_names():
        op.drop_index(op.f("ix_briefing_cache_entries_id"), table_name="briefing_cache_entries")
        op.drop_table("briefing_cache_entries")
