"""Add payload filters and rate limits to trigger definitions (JAN-363).

``event_filter`` (JSON conditions on the event's payload fields) and
``max_runs_per_hour`` gate event runs; ``rate_limited_count`` and
``last_rate_limited_at`` record events the limit dropped. Existing definitions
get no filter and no limit, so they behave as before.

Revision ID: b7e1d4c9a2f6
Revises: a9c4e2f7b1d3
Create Date: 2026-11-10 21:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7e1d4c9a2f6"
down_revision: str | Sequence[str] | None = "a9c4e2f7b1d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("trigger_definitions") as batch:
        batch.add_column(sa.Column("event_filter", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("max_runs_per_hour", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("rate_limited_count", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("last_rate_limited_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("trigger_definitions") as batch:
        batch.drop_column("last_rate_limited_at")
        batch.drop_column("rate_limited_count")
        batch.drop_column("max_runs_per_hour")
        batch.drop_column("event_filter")
