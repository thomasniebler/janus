"""LLM token usage per user, day, surface and model (JAN-408).

Revision ID: a4d6b8c0e2f5
Revises: f3c5a7e9b1d4
Create Date: 2026-11-25 20:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a4d6b8c0e2f5"
down_revision: str | Sequence[str] | None = "f3c5a7e9b1d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``llm_usage_days``."""
    op.create_table(
        "llm_usage_days",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("surface", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=200), nullable=False),
        sa.Column("calls", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unreported_calls", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("user_id", "day", "surface", "model", name="uq_llm_usage_days_key"),
    )
    op.create_index("ix_llm_usage_days_id", "llm_usage_days", ["id"])
    op.create_index("ix_llm_usage_days_user_id", "llm_usage_days", ["user_id"])
    op.create_index("ix_llm_usage_days_day", "llm_usage_days", ["day"])


def downgrade() -> None:
    """Drop ``llm_usage_days``."""
    op.drop_index("ix_llm_usage_days_day", table_name="llm_usage_days")
    op.drop_index("ix_llm_usage_days_user_id", table_name="llm_usage_days")
    op.drop_index("ix_llm_usage_days_id", table_name="llm_usage_days")
    op.drop_table("llm_usage_days")
