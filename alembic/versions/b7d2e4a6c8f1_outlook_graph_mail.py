"""Outlook mail over Microsoft Graph: a sync cursor per account and the Graph id per email.

Revision ID: b7d2e4a6c8f1
Revises: a1c4e7f9b3d5
Create Date: 2026-11-12 18:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b7d2e4a6c8f1"
down_revision: str | Sequence[str] | None = "a1c4e7f9b3d5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Adds ``email_accounts.sync_cursor`` and ``emails.graph_message_id``."""
    with op.batch_alter_table("email_accounts") as batch_op:
        batch_op.add_column(sa.Column("sync_cursor", sa.String(), nullable=True))
    with op.batch_alter_table("emails") as batch_op:
        batch_op.add_column(sa.Column("graph_message_id", sa.String(), nullable=True))


def downgrade() -> None:
    """Drops the two columns."""
    with op.batch_alter_table("emails") as batch_op:
        batch_op.drop_column("graph_message_id")
    with op.batch_alter_table("email_accounts") as batch_op:
        batch_op.drop_column("sync_cursor")
