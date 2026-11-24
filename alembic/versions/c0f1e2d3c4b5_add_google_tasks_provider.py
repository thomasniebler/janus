"""Add Google Tasks planner provider.

Revision ID: c0f1e2d3c4b5
Revises: 2bbf0d6f8b44
Create Date: 2026-05-18 18:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c0f1e2d3c4b5"
down_revision: str | None = "2bbf0d6f8b44"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_PROVIDER_ENUM = sa.Enum(
    "GOOGLE_CALENDAR",
    "O365",
    "JIRA",
    "ADO",
    name="provider",
    native_enum=False,
    create_constraint=True,
)
_NEW_PROVIDER_ENUM = sa.Enum(
    "GOOGLE_CALENDAR",
    "GOOGLE_TASKS",
    "O365",
    "JIRA",
    "ADO",
    name="provider",
    native_enum=False,
    create_constraint=True,
)


def upgrade() -> None:
    """Upgrade schema."""
    # The initial migration created `provider` as a native PostgreSQL enum; widen it in place.
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TYPE provider ADD VALUE IF NOT EXISTS 'GOOGLE_TASKS'")
        return
    with op.batch_alter_table("planner_accounts") as batch_op:
        batch_op.alter_column(
            "provider",
            existing_type=_OLD_PROVIDER_ENUM,
            type_=_NEW_PROVIDER_ENUM,
            existing_nullable=True,
        )


def downgrade() -> None:
    """Downgrade schema."""
    # PostgreSQL cannot drop a value from an enum type; the extra value is left in place.
    if op.get_bind().dialect.name == "postgresql":
        return
    with op.batch_alter_table("planner_accounts") as batch_op:
        batch_op.alter_column(
            "provider",
            existing_type=_NEW_PROVIDER_ENUM,
            type_=_OLD_PROVIDER_ENUM,
            existing_nullable=True,
        )
