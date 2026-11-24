"""Split the owner-scoped trigger history out of the ``events`` tool (JAN-195).

The ``events`` tool-registry entry used to build both ``list_recent_events``
(the system-wide, not owner-scoped event log) and ``list_my_trigger_history``
(the caller's own trigger outcomes). The latter now lives in its own
``trigger-history`` entry, and ``events`` is withheld from every unattended
task mode. Data-only migration so existing saved state keeps its meaning:

* ``trigger_definitions.tool_ids``: ``"events"`` is rewritten to
  ``"trigger-history"`` -- an automation could only ever have used the event
  log's owner-scoped half safely, and that is what it keeps.
* ``tool_policies``: a user's ``events`` override is copied to
  ``trigger-history`` (unless one already exists), so a user who blocked the
  old toolbox for unattended runs keeps trigger history blocked too.

Downgrade reverses the rewrite (``"trigger-history"`` -> ``"events"``) and
drops the copied ``trigger-history`` policy rows.

Revision ID: c4e1a7b9d2f3
Revises: a9afd7fc84dd
Create Date: 2026-09-28 22:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4e1a7b9d2f3"
down_revision: str | Sequence[str] | None = "a9afd7fc84dd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_ID = "events"
_NEW_ID = "trigger-history"

_definitions = sa.table("trigger_definitions", sa.column("id", sa.Integer), sa.column("tool_ids", sa.JSON))
_policies = sa.table(
    "tool_policies",
    sa.column("id", sa.Integer),
    sa.column("user_id", sa.Integer),
    sa.column("tool_id", sa.String),
    sa.column("block_without_confirmation", sa.Boolean),
    sa.column("updated_at", sa.DateTime),
)


def _rewrite_tool_ids(source: str, target: str) -> None:
    """Replace ``source`` with ``target`` in every definition's tool list, de-duplicated."""
    bind = op.get_bind()
    for definition_id, tool_ids in bind.execute(sa.select(_definitions.c.id, _definitions.c.tool_ids)).all():
        if not isinstance(tool_ids, list) or source not in tool_ids:
            continue
        rewritten = list(dict.fromkeys(target if tool_id == source else tool_id for tool_id in tool_ids))
        bind.execute(_definitions.update().where(_definitions.c.id == definition_id).values(tool_ids=rewritten))


def upgrade() -> None:
    """Move saved ``events`` selections and policy overrides to ``trigger-history``."""
    _rewrite_tool_ids(_OLD_ID, _NEW_ID)

    bind = op.get_bind()
    existing = {
        user_id
        for (user_id,) in bind.execute(sa.select(_policies.c.user_id).where(_policies.c.tool_id == _NEW_ID)).all()
    }
    rows = bind.execute(
        sa.select(_policies.c.user_id, _policies.c.block_without_confirmation, _policies.c.updated_at).where(
            _policies.c.tool_id == _OLD_ID
        )
    ).all()
    for user_id, block, updated_at in rows:
        if user_id in existing:
            continue
        bind.execute(
            _policies.insert().values(
                user_id=user_id, tool_id=_NEW_ID, block_without_confirmation=block, updated_at=updated_at
            )
        )


def downgrade() -> None:
    """Fold ``trigger-history`` back into the combined ``events`` toolbox."""
    _rewrite_tool_ids(_NEW_ID, _OLD_ID)
    op.get_bind().execute(_policies.delete().where(_policies.c.tool_id == _NEW_ID))
