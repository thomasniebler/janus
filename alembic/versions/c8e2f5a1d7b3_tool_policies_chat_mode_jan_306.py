"""Add the per-tool "In chat" switch to tool policies (JAN-306).

``tool_policies.chat_mode`` (nullable string) is ``"off"`` when the owner
withholds a ``confirmation="required"`` tool from chat, ``"ask"`` for the
explicit default (per-call approval), and ``NULL`` for the default.
``tool_policies.unattended_allowed`` becomes nullable so a row can override
only the chat switch. Existing rows keep their meaning. Downgrade deletes rows
that override only the chat switch, drops the column, and restores NOT NULL.

Revision ID: c8e2f5a1d7b3
Revises: c5d2e8f1a4b7
Create Date: 2026-10-11 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c8e2f5a1d7b3"
down_revision: str | Sequence[str] | None = "c5d2e8f1a4b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add ``chat_mode`` and let ``unattended_allowed`` be ``NULL`` (the default)."""
    with op.batch_alter_table("tool_policies") as batch:
        batch.add_column(sa.Column("chat_mode", sa.String(), nullable=True))
        batch.alter_column("unattended_allowed", existing_type=sa.Boolean(), nullable=True)


def downgrade() -> None:
    """Drop ``chat_mode``; rows that only switched a tool off in chat go with it."""
    policies = sa.table("tool_policies", sa.column("unattended_allowed", sa.Boolean))
    op.execute(policies.delete().where(policies.c.unattended_allowed.is_(None)))
    with op.batch_alter_table("tool_policies") as batch:
        batch.alter_column("unattended_allowed", existing_type=sa.Boolean(), nullable=False)
        batch.drop_column("chat_mode")
