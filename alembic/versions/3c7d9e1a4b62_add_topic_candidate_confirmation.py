"""Add topic candidate state and confirmation threshold.

Revision ID: 3c7d9e1a4b62
Revises: 1a2b3c4d5e6f
Create Date: 2026-09-20 18:40:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3c7d9e1a4b62"
down_revision: str | None = "1a2b3c4d5e6f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # SQLite (the supported target) stores the enum as text without a CHECK constraint, so only
    # databases with a real enum type need the new value declared.
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TYPE topicstatus ADD VALUE IF NOT EXISTS 'CANDIDATE'")

    settings_columns = {column["name"] for column in inspector.get_columns("settings")}
    if "topic_tracking_default_min_confirmation_mentions" not in settings_columns:
        with op.batch_alter_table("settings") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "topic_tracking_default_min_confirmation_mentions",
                    sa.Integer(),
                    nullable=False,
                    server_default="2",
                )
            )

    preference_columns = {column["name"] for column in inspector.get_columns("topic_tracking_preferences")}
    if "min_confirmation_mentions" not in preference_columns:
        with op.batch_alter_table("topic_tracking_preferences") as batch_op:
            batch_op.add_column(
                sa.Column("min_confirmation_mentions", sa.Integer(), nullable=False, server_default="2")
            )


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # Candidate topics have no pre-candidate equivalent; treat them as active on downgrade.
    op.execute(sa.text("UPDATE tracked_topics SET status = 'ACTIVE' WHERE status = 'CANDIDATE'"))

    preference_columns = {column["name"] for column in inspector.get_columns("topic_tracking_preferences")}
    if "min_confirmation_mentions" in preference_columns:
        with op.batch_alter_table("topic_tracking_preferences") as batch_op:
            batch_op.drop_column("min_confirmation_mentions")

    settings_columns = {column["name"] for column in inspector.get_columns("settings")}
    if "topic_tracking_default_min_confirmation_mentions" in settings_columns:
        with op.batch_alter_table("settings") as batch_op:
            batch_op.drop_column("topic_tracking_default_min_confirmation_mentions")
