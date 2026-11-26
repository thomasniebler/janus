"""Let the Secretary propose planner items.

* ``planneritemsource`` gains ``JANUS_AGENT``: suggestions a Janus run made
  without a source email (the Secretary's ``propose_planner_item``). Only
  PostgreSQL has a native enum to widen; SQLite stores the name as text.
* Every built-in Secretary definition gets the ``planner.suggest`` tool, as new
  ones are seeded with it. A definition that already lists it is left alone.

Revision ID: e1b3d5f7a9c2
Revises: d8a0c2e4f6b8
Create Date: 2026-11-26 13:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e1b3d5f7a9c2"
down_revision: str | Sequence[str] | None = "d8a0c2e4f6b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TOOL_ID = "planner.suggest"
_SECRETARY = "builtin.secretary"
_definitions = sa.table(
    "trigger_definitions",
    sa.column("id", sa.Integer),
    sa.column("builtin_key", sa.String),
    sa.column("tool_ids", sa.JSON),
)


def _secretaries(bind) -> list[tuple[int, list]]:
    rows = bind.execute(
        sa.select(_definitions.c.id, _definitions.c.tool_ids).where(_definitions.c.builtin_key == _SECRETARY)
    ).all()
    return [(definition_id, tool_ids) for definition_id, tool_ids in rows if isinstance(tool_ids, list)]


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TYPE planneritemsource ADD VALUE IF NOT EXISTS 'JANUS_AGENT'")
    for definition_id, tool_ids in _secretaries(bind):
        if _TOOL_ID not in tool_ids:
            bind.execute(
                _definitions.update().where(_definitions.c.id == definition_id).values(tool_ids=[*tool_ids, _TOOL_ID])
            )


def downgrade() -> None:
    """Downgrade schema."""
    # PostgreSQL cannot drop a value from an enum type; the extra value is left in place.
    bind = op.get_bind()
    for definition_id, tool_ids in _secretaries(bind):
        if _TOOL_ID in tool_ids:
            bind.execute(
                _definitions.update()
                .where(_definitions.c.id == definition_id)
                .values(tool_ids=[tool_id for tool_id in tool_ids if tool_id != _TOOL_ID])
            )
