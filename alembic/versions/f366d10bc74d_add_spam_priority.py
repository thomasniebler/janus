"""add spam priority.

Revision ID: f366d10bc74d
Revises: d2189457969d
Create Date: 2026-03-14 19:54:50.631412

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f366d10bc74d"
down_revision: str | Sequence[str] | None = "d2189457969d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _is_postgresql() -> bool:
    """PostgreSQL stores this column as a native enum type, widened via ALTER TYPE instead."""
    return op.get_bind().dialect.name == "postgresql"


_OLD_PRIORITY_ENUM = sa.Enum("IMPORTANT", "DIGEST", "IGNORE", name="priority")
_NEW_PRIORITY_ENUM = sa.Enum("IMPORTANT", "DIGEST", "IGNORE", "SPAM", name="priority")


def upgrade() -> None:
    """Upgrade schema."""
    # PostgreSQL cannot remove enum values, so downgrade leaves the extra value in place.
    if _is_postgresql():
        op.execute("ALTER TYPE priority ADD VALUE IF NOT EXISTS 'SPAM'")
    with op.batch_alter_table("emails") as batch_op:
        if not _is_postgresql():
            batch_op.alter_column(
                "priority",
                existing_type=_OLD_PRIORITY_ENUM,
                type_=_NEW_PRIORITY_ENUM,
                existing_nullable=True,
            )
    with op.batch_alter_table("rules") as batch_op:
        if not _is_postgresql():
            batch_op.alter_column(
                "priority",
                existing_type=_OLD_PRIORITY_ENUM,
                type_=_NEW_PRIORITY_ENUM,
                existing_nullable=True,
            )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("rules") as batch_op:
        if not _is_postgresql():
            batch_op.alter_column(
                "priority",
                existing_type=_NEW_PRIORITY_ENUM,
                type_=_OLD_PRIORITY_ENUM,
                existing_nullable=True,
            )
    with op.batch_alter_table("emails") as batch_op:
        if not _is_postgresql():
            batch_op.alter_column(
                "priority",
                existing_type=_NEW_PRIORITY_ENUM,
                type_=_OLD_PRIORITY_ENUM,
                existing_nullable=True,
            )
