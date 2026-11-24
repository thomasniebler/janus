"""Qualify legacy Google Calendar external_ids with the primary calendar prefix.

Before multi-calendar sync support (68a37cc28773), GoogleCalendarConnector only ever synced the
"primary" calendar and stored the raw Google event id as external_id. Sync now qualifies
external_id as "<calendar_id>:<event_id>" for every calendar, so an existing row's unqualified
external_id would no longer match on the next sync and get duplicated instead of updated. Prefix
existing rows with "primary:" so PlannerService.sync_all()'s upsert-by-external_id keeps matching
them.

Revision ID: 8109db6b1275
Revises: 68a37cc28773
Create Date: 2026-09-27 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8109db6b1275"
down_revision: str | Sequence[str] | None = "68a37cc28773"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            UPDATE calendar_events
            SET external_id = 'primary:' || external_id
            WHERE external_id NOT LIKE 'primary:%'
            AND planner_account_id IN (
                SELECT id FROM planner_accounts WHERE provider = 'GOOGLE_CALENDAR'
            )
            """
        )
    )


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            UPDATE calendar_events
            SET external_id = substr(external_id, length('primary:') + 1)
            WHERE external_id LIKE 'primary:%'
            AND planner_account_id IN (
                SELECT id FROM planner_accounts WHERE provider = 'GOOGLE_CALENDAR'
            )
            """
        )
    )
