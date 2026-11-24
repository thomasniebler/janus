"""Add per-message chat tool calls (JAN-249).

Additive only: a new ``conversation_tool_calls`` table holding the final
``__TOOL_EVENT__`` state (redacted args, truncated result preview, phase,
duration) of each tool call an assistant chat message made. Existing messages
simply have no rows. Rows cascade with their message.

Revision ID: c4e8a1f6b2d9
Revises: b7d2e9f4a1c3
Create Date: 2026-10-03 22:45:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4e8a1f6b2d9"
down_revision: str | Sequence[str] | None = "b7d2e9f4a1c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "conversation_tool_calls"


def upgrade() -> None:
    """Create the tool-call table and its indexes."""
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("call_id", sa.String(), nullable=False),
        sa.Column("tool_id", sa.String(), nullable=True),
        sa.Column("tool_name", sa.String(), nullable=False),
        sa.Column("phase", sa.String(), nullable=False),
        sa.Column("args", sa.JSON(), nullable=True),
        sa.Column("args_truncated", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("result_preview", sa.Text(), nullable=True),
        sa.Column("result_truncated", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["message_id"], ["conversation_messages.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_conversation_tool_calls_id", _TABLE, ["id"], unique=False)
    op.create_index("ix_conversation_tool_calls_message_id", _TABLE, ["message_id"], unique=False)


def downgrade() -> None:
    """Drop the tool-call table; the messages themselves are untouched."""
    op.drop_index("ix_conversation_tool_calls_message_id", table_name=_TABLE)
    op.drop_index("ix_conversation_tool_calls_id", table_name=_TABLE)
    op.drop_table(_TABLE)
