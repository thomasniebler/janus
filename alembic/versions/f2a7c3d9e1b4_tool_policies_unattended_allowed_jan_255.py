"""Replace "block without confirmation" with an "allowed in unattended runs" switch (JAN-255).

``tool_policies.block_without_confirmation`` becomes
``tool_policies.unattended_allowed``, its inverse: a blocked tool is a tool
that is not allowed in unattended runs. A row is still an override of the
registry default; no row means the default. Downgrade restores the old column
from the inverse.

Revision ID: f2a7c3d9e1b4
Revises: d130c10835c8
Create Date: 2026-09-29 20:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f2a7c3d9e1b4"
down_revision: str | Sequence[str] | None = "d130c10835c8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _swap(old: str, new: str) -> None:
    """Add ``new`` as the inverse of ``old``, then drop ``old``."""
    with op.batch_alter_table("tool_policies") as batch:
        batch.add_column(sa.Column(new, sa.Boolean(), nullable=True))
    policies = sa.table("tool_policies", sa.column(old, sa.Boolean), sa.column(new, sa.Boolean))
    op.execute(policies.update().values({new: sa.not_(policies.c[old])}))
    with op.batch_alter_table("tool_policies") as batch:
        batch.alter_column(new, existing_type=sa.Boolean(), nullable=False)
        batch.drop_column(old)


def upgrade() -> None:
    """Store whether each overridden tool is allowed in unattended runs."""
    _swap("block_without_confirmation", "unattended_allowed")


def downgrade() -> None:
    """Restore the "block without confirmation" column."""
    _swap("unattended_allowed", "block_without_confirmation")
