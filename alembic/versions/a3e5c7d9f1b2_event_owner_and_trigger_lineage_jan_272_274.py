"""Record an owner on events and lineage on runtime triggers (JAN-272, JAN-274).

``events.user_id`` lets an unattended run read only its owner's event-log rows,
so the ``events`` tool no longer has to be locked out of unattended runs.
Every existing row stays ``NULL`` (a system row): the old log never recorded
who an event concerned, and guessing would be worse than hiding it.

``runtime_triggers.parent_trigger_id``/``chain_depth`` record which unattended
run scheduled a trigger and how deep that chain is, so the trigger tools can
refuse runaway self-scheduling instead of being locked out. Existing rows are
roots (depth 0, no parent).

Revision ID: a3e5c7d9f1b2
Revises: f2a7c3d9e1b4
Create Date: 2026-10-02 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a3e5c7d9f1b2"
down_revision: str | Sequence[str] | None = "f2a7c3d9e1b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_EVENT_USER_FK = "fk_events_user_id_users"


def upgrade() -> None:
    """Add the event owner column and the trigger lineage columns."""
    with op.batch_alter_table("events") as batch:
        batch.add_column(sa.Column("user_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(_EVENT_USER_FK, "users", ["user_id"], ["id"], ondelete="SET NULL")
        batch.create_index("ix_events_user_id", ["user_id"], unique=False)
    with op.batch_alter_table("runtime_triggers") as batch:
        batch.add_column(sa.Column("parent_trigger_id", sa.String(), nullable=True))
        batch.add_column(sa.Column("chain_depth", sa.Integer(), nullable=False, server_default="0"))
        batch.create_index("ix_runtime_triggers_parent_trigger_id", ["parent_trigger_id"], unique=False)


def downgrade() -> None:
    """Drop the trigger lineage columns and the event owner column."""
    with op.batch_alter_table("runtime_triggers") as batch:
        batch.drop_index("ix_runtime_triggers_parent_trigger_id")
        batch.drop_column("chain_depth")
        batch.drop_column("parent_trigger_id")
    with op.batch_alter_table("events") as batch:
        batch.drop_index("ix_events_user_id")
        batch.drop_constraint(_EVENT_USER_FK, type_="foreignkey")
        batch.drop_column("user_id")
