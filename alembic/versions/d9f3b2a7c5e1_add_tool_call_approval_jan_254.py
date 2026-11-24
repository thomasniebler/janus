"""Add the chat approval decision to tool calls (JAN-254).

Additive only: a nullable ``conversation_tool_calls.approval`` column holding
the user's decision (``approved``/``denied``/``timeout``/``disconnected``/
``unavailable``) for a ``confirmation="required"`` tool call in chat. Existing
rows keep ``NULL``: no approval was asked for them.

Revision ID: d9f3b2a7c5e1
Revises: c4e8a1f6b2d9
Create Date: 2026-10-04 13:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d9f3b2a7c5e1"
down_revision: str | Sequence[str] | None = "c4e8a1f6b2d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "conversation_tool_calls"


def upgrade() -> None:
    """Add the nullable approval column."""
    with op.batch_alter_table(_TABLE) as batch_op:
        batch_op.add_column(sa.Column("approval", sa.String(), nullable=True))


def downgrade() -> None:
    """Drop the approval column; the tool-call rows themselves are kept."""
    with op.batch_alter_table(_TABLE) as batch_op:
        batch_op.drop_column("approval")
