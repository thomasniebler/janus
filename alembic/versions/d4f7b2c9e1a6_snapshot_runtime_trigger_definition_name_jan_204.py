"""Snapshot a trigger run's definition name so its provenance survives deletion (JAN-204).

Deleting a ``TriggerDefinition`` nulls ``runtime_triggers.definition_id``
(``ON DELETE SET NULL``), which used to turn its past runs into ad-hoc runs on
the Agent Tasks page. ``runtime_triggers.definition_name`` keeps the name the
definition had when the run was enqueued.

Backfill: runs still linked copy their definition's current name. Runs whose
definition is already gone are recognised by the ``definition_id`` that
``build_definition_envelope`` writes into their immutable ``payload``; their
name is no longer recoverable, so they get ``"Deleted trigger definition"``.

Revision ID: d4f7b2c9e1a6
Revises: f2a7c4e9b1d3
Create Date: 2026-10-11 22:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4f7b2c9e1a6"
down_revision: str | Sequence[str] | None = "f2a7c4e9b1d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The name backfilled onto runs whose definition was deleted before this revision.
DELETED_DEFINITION_NAME = "Deleted trigger definition"

_TRIGGERS = sa.table(
    "runtime_triggers",
    sa.column("id", sa.Integer()),
    sa.column("definition_id", sa.Integer()),
    sa.column("definition_name", sa.String()),
    sa.column("payload", sa.JSON()),
)
_DEFINITIONS = sa.table("trigger_definitions", sa.column("id", sa.Integer()), sa.column("name", sa.String()))


def upgrade() -> None:
    """Add the nullable snapshot column and backfill it from current data."""
    with op.batch_alter_table("runtime_triggers") as batch_op:
        batch_op.add_column(sa.Column("definition_name", sa.String(), nullable=True))

    bind = op.get_bind()
    bind.execute(
        _TRIGGERS.update()
        .where(_TRIGGERS.c.definition_id.is_not(None))
        .values(
            definition_name=sa.select(_DEFINITIONS.c.name)
            .where(_DEFINITIONS.c.id == _TRIGGERS.c.definition_id)
            .scalar_subquery()
        )
    )
    orphaned = [
        row.id
        for row in bind.execute(
            sa.select(_TRIGGERS.c.id, _TRIGGERS.c.payload).where(
                _TRIGGERS.c.definition_id.is_(None), _TRIGGERS.c.definition_name.is_(None)
            )
        )
        if isinstance(row.payload, dict) and row.payload.get("definition_id") is not None
    ]
    if orphaned:
        bind.execute(
            _TRIGGERS.update().where(_TRIGGERS.c.id.in_(orphaned)).values(definition_name=DELETED_DEFINITION_NAME)
        )


def downgrade() -> None:
    """Drop the snapshot column."""
    with op.batch_alter_table("runtime_triggers") as batch_op:
        batch_op.drop_column("definition_name")
