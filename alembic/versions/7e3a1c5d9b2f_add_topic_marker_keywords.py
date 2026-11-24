"""Add topic marker keywords.

Revision ID: 7e3a1c5d9b2f
Revises: 9d3c1b2a4f6e
Create Date: 2026-05-20 15:45:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7e3a1c5d9b2f"
down_revision: str | None = "9d3c1b2a4f6e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tracked_topic_columns = {column["name"] for column in inspector.get_columns("tracked_topics")}

    if "marker_keywords" not in tracked_topic_columns:
        with op.batch_alter_table("tracked_topics") as batch_op:
            batch_op.add_column(sa.Column("marker_keywords", sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tracked_topic_columns = {column["name"] for column in inspector.get_columns("tracked_topics")}

    if "marker_keywords" in tracked_topic_columns:
        with op.batch_alter_table("tracked_topics") as batch_op:
            batch_op.drop_column("marker_keywords")
