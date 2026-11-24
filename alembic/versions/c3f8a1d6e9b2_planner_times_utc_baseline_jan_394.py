"""Make the next planner sync a baseline, so it can rewrite stored times as UTC (JAN-394).

Before JAN-394 the planner sync stored an event's or task's aware datetime in a
naive ``DateTime`` column, and SQLite kept its wall time and dropped the offset:
a 10:00+01:00 event was stored as 10:00 although every reader treats stored
values as UTC. The sync now converts to UTC before storing, and its next run
rewrites every synced field anyway. Clearing ``planner_accounts.last_sync_at``
makes that run a baseline: it fixes the rows without firing a
``planner.*_changed`` trigger event for every corrected time. The original
offsets are not stored, so the rows cannot be corrected here directly.

Data-only; the status page shows "Waiting for the first successful sync" until
that sync, which the planner worker runs right after startup. The downgrade is
a no-op.

Revision ID: c3f8a1d6e9b2
Revises: b7e1d4c9a2f6
Create Date: 2026-11-10 22:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3f8a1d6e9b2"
down_revision: str | Sequence[str] | None = "b7e1d4c9a2f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(sa.text("UPDATE planner_accounts SET last_sync_at = NULL"))


def downgrade() -> None:
    """Downgrade schema (nothing to undo: the next sync sets last_sync_at again)."""
