"""Align the migrated schema with the ORM models and close the alembic-check drift (JAN-338).

``alembic check`` on a fresh database upgraded to ``b4d7e2a9c6f1`` reported five differences.
Four of them were the models being looser than the migrated schema, fixed in the models with no
schema change:

* ``email_accounts.user_id``, ``planner_accounts.user_id``, ``rules.user_id``: ``37b30d7c85e1``
  backfilled them to the first admin and made them ``NOT NULL``; the models still said
  ``nullable=True``. Every row has an owner, so the models are now ``nullable=False``.
* ``ix_error_groups_fingerprint``: ``5e735b5364f2`` enforces uniqueness through the
  ``uq_error_groups_fingerprint`` constraint and keeps the index plain; the model also declared
  ``unique=True`` on the column. The model now matches (constraint unique, index plain).

The fifth is real and is this migration: ``tracked_topics.status`` was created as a
``VARCHAR(8)`` enum (``ACTIVE``/``SNOOZED``/``RESOLVED``/``IGNORED``), and ``3c7d9e1a4b62``
added ``CANDIDATE`` (nine characters) only to PostgreSQL's native enum type. On SQLite the
column is widened to the model's ``Enum(TopicStatus)`` (``VARCHAR(9)``, no CHECK constraint,
like every other enum column here). SQLite never enforced the length, so existing ``CANDIDATE``
rows are already intact; the batch copy keeps every row, the ``ACTIVE`` server default, the
``ix_tracked_topics_status`` index, and the ``(user_id, slug)`` unique constraint. Before the
copy, status values stored in lower case (the enum's values rather than its names, which the
ORM cannot read back) are normalised to the names; any other value is left untouched.

PostgreSQL already has the native ``topicstatus`` type with ``CANDIDATE``, so nothing changes there.

Revision ID: c3e9a1f7d2b8
Revises: b4d7e2a9c6f1
Create Date: 2026-10-12 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3e9a1f7d2b8"
down_revision: str | Sequence[str] | None = "b4d7e2a9c6f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TOPIC_STATUSES = ("CANDIDATE", "ACTIVE", "SNOOZED", "RESOLVED", "IGNORED")
_PRE_CANDIDATE_STATUSES = ("ACTIVE", "SNOOZED", "RESOLVED", "IGNORED")


def _uses_native_enum() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    """Normalise lower-case topic statuses and widen ``tracked_topics.status`` to the model's enum."""
    if _uses_native_enum():
        return

    names = ", ".join(f"'{name}'" for name in _TOPIC_STATUSES)
    op.execute(
        sa.text(
            "UPDATE tracked_topics SET status = UPPER(status) "
            f"WHERE status <> UPPER(status) AND UPPER(status) IN ({names})"
        )
    )

    with op.batch_alter_table("tracked_topics") as batch_op:
        batch_op.alter_column(
            "status",
            existing_type=sa.Enum(*_PRE_CANDIDATE_STATUSES, name="topicstatus"),
            type_=sa.Enum(*_TOPIC_STATUSES, name="topicstatus"),
            existing_nullable=False,
            existing_server_default=sa.text("'ACTIVE'"),
        )


def downgrade() -> None:
    """Restore the ``VARCHAR(8)`` column; SQLite does not enforce the length, so no row changes."""
    if _uses_native_enum():
        return

    with op.batch_alter_table("tracked_topics") as batch_op:
        batch_op.alter_column(
            "status",
            existing_type=sa.Enum(*_TOPIC_STATUSES, name="topicstatus"),
            type_=sa.Enum(*_PRE_CANDIDATE_STATUSES, name="topicstatus"),
            existing_nullable=False,
            existing_server_default=sa.text("'ACTIVE'"),
        )
