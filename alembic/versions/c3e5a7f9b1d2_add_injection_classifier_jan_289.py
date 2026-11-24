"""Add the optional prompt-injection classifier's settings and cache tables (JAN-289).

Additive only: ``injection_classifier_settings`` holds one row per user (the
opt-in switch, off by default, the monthly token budget, and this month's
usage), and ``injection_classifications`` caches verdicts by the SHA-256 of
the classified text (the text itself is never stored). Both cascade with their
user.

Revision ID: c3e5a7f9b1d2
Revises: c8e2f5a1d7b3
Create Date: 2026-10-11 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3e5a7f9b1d2"
down_revision: str | Sequence[str] | None = "c8e2f5a1d7b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create both tables and their indexes."""
    op.create_table(
        "injection_classifier_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("monthly_token_budget", sa.Integer(), nullable=False, server_default="200000"),
        sa.Column("usage_period", sa.String(length=7), nullable=True),
        sa.Column("tokens_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("calls", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_injection_classifier_settings_id", "injection_classifier_settings", ["id"], unique=False)
    op.create_index(
        "ix_injection_classifier_settings_user_id", "injection_classifier_settings", ["user_id"], unique=True
    )
    op.create_table(
        "injection_classifications",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("suspected", sa.Boolean(), nullable=False),
        sa.Column("labels", sa.JSON(), nullable=False),
        sa.Column("model", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "content_hash", name="uq_injection_classifications_user_hash"),
    )
    for column in ("id", "user_id", "created_at"):
        op.create_index(f"ix_injection_classifications_{column}", "injection_classifications", [column], unique=False)


def downgrade() -> None:
    """Drop both tables."""
    for column in ("created_at", "user_id", "id"):
        op.drop_index(f"ix_injection_classifications_{column}", table_name="injection_classifications")
    op.drop_table("injection_classifications")
    op.drop_index("ix_injection_classifier_settings_user_id", table_name="injection_classifier_settings")
    op.drop_index("ix_injection_classifier_settings_id", table_name="injection_classifier_settings")
    op.drop_table("injection_classifier_settings")
