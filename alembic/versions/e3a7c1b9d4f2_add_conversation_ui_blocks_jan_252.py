"""Add per-message chat UI blocks (JAN-252).

Additive only: a new ``conversation_ui_blocks`` table holding each validated
generative-UI block (card, table, chart, ...) an assistant chat message
rendered through ``render_ui``, with its catalog version and JSON payload.
Existing messages simply have no rows. Rows cascade with their message.

Revision ID: e3a7c1b9d4f2
Revises: d9f3b2a7c5e1
Create Date: 2026-10-05 09:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e3a7c1b9d4f2"
down_revision: str | Sequence[str] | None = "d9f3b2a7c5e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "conversation_ui_blocks"


def upgrade() -> None:
    """Create the UI-block table and its indexes."""
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("block_id", sa.String(), nullable=False),
        sa.Column("block_type", sa.String(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["message_id"], ["conversation_messages.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_conversation_ui_blocks_id", _TABLE, ["id"], unique=False)
    op.create_index("ix_conversation_ui_blocks_message_id", _TABLE, ["message_id"], unique=False)


def downgrade() -> None:
    """Drop the UI-block table; the messages themselves are untouched."""
    op.drop_index("ix_conversation_ui_blocks_message_id", table_name=_TABLE)
    op.drop_index("ix_conversation_ui_blocks_id", table_name=_TABLE)
    op.drop_table(_TABLE)
