"""Drop the briefing cache (JAN-231).

The morning briefing is now the built-in ``builtin.briefing`` trigger
definition; its result is each run's ``runtime_triggers.output`` and the
dashboard shows today's run, so the separate generated-briefing cache is gone.

Revision ID: f7a0b4c5d6e8
Revises: e6f9a3b4c5d7
Create Date: 2026-09-27 13:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f7a0b4c5d6e8"
down_revision: str | Sequence[str] | None = "e6f9a3b4c5d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    inspector = sa.inspect(op.get_bind())
    if "briefing_cache_entries" in inspector.get_table_names():
        op.drop_index(op.f("ix_briefing_cache_entries_id"), table_name="briefing_cache_entries")
        op.drop_table("briefing_cache_entries")


def downgrade() -> None:
    """Downgrade schema (empty cache)."""
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
        sa.UniqueConstraint("briefing_date", "cache_identity", "summary_hash", name="uq_briefing_cache_entry_key"),
    )
    op.create_index(op.f("ix_briefing_cache_entries_id"), "briefing_cache_entries", ["id"], unique=False)
