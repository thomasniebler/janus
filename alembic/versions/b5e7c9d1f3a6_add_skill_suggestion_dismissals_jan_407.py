"""Dismissed skill suggestions (JAN-407).

Revision ID: b5e7c9d1f3a6
Revises: a4d6b8c0e2f5
Create Date: 2026-11-25 20:45:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b5e7c9d1f3a6"
down_revision: str | Sequence[str] | None = "a4d6b8c0e2f5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``skill_suggestion_dismissals``."""
    op.create_table(
        "skill_suggestion_dismissals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("pattern_key", sa.String(length=32), nullable=False),
        sa.Column("dismissed_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "pattern_key", name="uq_skill_suggestion_dismissals_key"),
    )
    op.create_index("ix_skill_suggestion_dismissals_id", "skill_suggestion_dismissals", ["id"])
    op.create_index("ix_skill_suggestion_dismissals_user_id", "skill_suggestion_dismissals", ["user_id"])


def downgrade() -> None:
    """Drop ``skill_suggestion_dismissals``."""
    op.drop_index("ix_skill_suggestion_dismissals_user_id", table_name="skill_suggestion_dismissals")
    op.drop_index("ix_skill_suggestion_dismissals_id", table_name="skill_suggestion_dismissals")
    op.drop_table("skill_suggestion_dismissals")
