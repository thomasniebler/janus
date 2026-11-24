"""Add per-surface model choices: trigger definitions, delegations, chat, and utility (JAN-233).

All columns are nullable; ``NULL`` keeps the default model.

Revision ID: a8b1c2d3e4f5
Revises: f7a0b4c5d6e8
Create Date: 2026-09-27 14:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a8b1c2d3e4f5"
down_revision: str | Sequence[str] | None = "f7a0b4c5d6e8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS = {
    "trigger_definitions": ("llm_provider", "llm_model"),
    "runtime_delegations": ("llm_provider", "llm_model"),
    "settings": ("chat_llm_provider", "chat_llm_model", "utility_llm_provider", "utility_llm_model"),
}


def upgrade() -> None:
    """Upgrade schema."""
    for table, columns in _COLUMNS.items():
        with op.batch_alter_table(table) as batch_op:
            for column in columns:
                batch_op.add_column(sa.Column(column, sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    for table, columns in _COLUMNS.items():
        with op.batch_alter_table(table) as batch_op:
            for column in reversed(columns):
                batch_op.drop_column(column)
