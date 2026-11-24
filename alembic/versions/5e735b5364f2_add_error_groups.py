"""Add error groups table.

Revision ID: 5e735b5364f2
Revises: 7f2d1c8b9a4e
Create Date: 2026-04-19 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5e735b5364f2"
down_revision: str | None = "7f2d1c8b9a4e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("error_groups"):
        return

    op.create_table(
        "error_groups",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("fingerprint", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("message_template", sa.String(), nullable=False),
        sa.Column("first_seen", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("last_seen", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("occurrence_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("sample_details", sa.String(), nullable=True),
        sa.Column("last_compacted_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("fingerprint", name="uq_error_groups_fingerprint"),
    )
    op.create_index(op.f("ix_error_groups_id"), "error_groups", ["id"], unique=False)
    op.create_index(op.f("ix_error_groups_fingerprint"), "error_groups", ["fingerprint"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("error_groups"):
        return

    op.drop_index(op.f("ix_error_groups_fingerprint"), table_name="error_groups")
    op.drop_index(op.f("ix_error_groups_id"), table_name="error_groups")
    op.drop_table("error_groups")
