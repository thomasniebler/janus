"""Record who started a conversation and whether Janus's new conversation is unread (JAN-319).

Additive only: ``conversations.origin`` (``user`` for every existing row),
``conversations.origin_trigger_id`` (the run that started a Janus
conversation), and ``conversations.unread`` (``false`` for every existing row).

Revision ID: d7f9b1c3e5a7
Revises: d4f7b2c9e1a6
Create Date: 2026-10-11 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d7f9b1c3e5a7"
down_revision: str | Sequence[str] | None = "d4f7b2c9e1a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the three columns and the trigger-id index."""
    with op.batch_alter_table("conversations") as batch_op:
        batch_op.add_column(sa.Column("origin", sa.String(length=16), nullable=False, server_default="user"))
        batch_op.add_column(sa.Column("origin_trigger_id", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("unread", sa.Boolean(), nullable=False, server_default="0"))
        batch_op.create_index("ix_conversations_origin_trigger_id", ["origin_trigger_id"], unique=False)


def downgrade() -> None:
    """Drop the index and the three columns."""
    with op.batch_alter_table("conversations") as batch_op:
        batch_op.drop_index("ix_conversations_origin_trigger_id")
        batch_op.drop_column("unread")
        batch_op.drop_column("origin_trigger_id")
        batch_op.drop_column("origin")
