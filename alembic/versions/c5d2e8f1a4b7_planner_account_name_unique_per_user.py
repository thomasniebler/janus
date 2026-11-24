"""Make planner account names unique per owner instead of globally (JAN-318).

``planner_accounts.name`` carried a global unique index, but every user's
"Connect Google" creates accounts named "Google Calendar" and "Google Tasks", so
a second user could never connect Google. The name becomes unique per
``user_id`` (``uq_planner_accounts_user_name``) and ``ix_planner_accounts_name``
stays as a plain lookup index. Names were globally unique until now, so no
existing rows can violate the new constraint.

Downgrading restores the global unique index and fails if two users now hold
an account with the same name.

Revision ID: c5d2e8f1a4b7
Revises: b7d1e4a9c2f6
Create Date: 2026-10-11 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c5d2e8f1a4b7"
down_revision: str | Sequence[str] | None = "b7d1e4a9c2f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX = "ix_planner_accounts_name"
_CONSTRAINT = "uq_planner_accounts_user_name"


def _name_indexes() -> list[str]:
    """Returns the names of the existing indexes that cover only ``name``."""
    inspector = sa.inspect(op.get_bind())
    return [
        index["name"]
        for index in inspector.get_indexes("planner_accounts")
        if index.get("column_names") == ["name"] and index.get("name")
    ]


def upgrade() -> None:
    """Replace the global unique name index with a per-owner unique constraint."""
    existing = _name_indexes()
    with op.batch_alter_table("planner_accounts") as batch_op:
        for index_name in existing:
            batch_op.drop_index(index_name)
        batch_op.create_index(_INDEX, ["name"], unique=False)
        batch_op.create_unique_constraint(_CONSTRAINT, ["user_id", "name"])


def downgrade() -> None:
    """Restore the global unique name index."""
    existing = _name_indexes()
    with op.batch_alter_table("planner_accounts") as batch_op:
        batch_op.drop_constraint(_CONSTRAINT, type_="unique")
        for index_name in existing:
            batch_op.drop_index(index_name)
        batch_op.create_index(_INDEX, ["name"], unique=True)
