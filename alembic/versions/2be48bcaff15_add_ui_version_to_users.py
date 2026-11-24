"""Add the per-user UI shell preference (UIv1 / UIv2).

Revision ID: 2be48bcaff15
Revises: 1a2b3c4d5e6f
Create Date: 2026-09-21 13:40:07.608099
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2be48bcaff15"
down_revision: str | Sequence[str] | None = "1a2b3c4d5e6f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _users_has_ui_version() -> bool:
    """Returns True when the users table already carries the ui_version column."""
    inspector = sa.inspect(op.get_bind())
    return any(column["name"] == "ui_version" for column in inspector.get_columns("users"))


def upgrade() -> None:
    """Adds users.ui_version defaulting every existing account to the classic UI."""
    if _users_has_ui_version():
        return

    op.add_column("users", sa.Column("ui_version", sa.String(), nullable=False, server_default="v1"))
    op.get_bind().execute(sa.text("UPDATE users SET ui_version = 'v1' WHERE ui_version IS NULL"))


def downgrade() -> None:
    """Removes users.ui_version."""
    if not _users_has_ui_version():
        return

    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("ui_version")
