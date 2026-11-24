"""Proactive secretary: waking hours and WhatsApp alert settings, alert provenance on messages.

Adds ``notification_preferences.secretary_active_start_hour`` / ``secretary_active_end_hour`` /
``secretary_whatsapp_enabled`` / ``secretary_whatsapp_number`` / ``secretary_planner_fingerprint``
and ``whatsapp_messages.origin_trigger_id``.

Revision ID: c9e3a5b7d1f2
Revises: b7d2e4a6c8f1
Create Date: 2026-11-14 09:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c9e3a5b7d1f2"
down_revision: str | Sequence[str] | None = "b7d2e4a6c8f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the secretary settings and the alert provenance column."""
    with op.batch_alter_table("notification_preferences") as batch_op:
        batch_op.add_column(sa.Column("secretary_active_start_hour", sa.Integer(), nullable=False, server_default="8"))
        batch_op.add_column(sa.Column("secretary_active_end_hour", sa.Integer(), nullable=False, server_default="21"))
        batch_op.add_column(sa.Column("secretary_whatsapp_enabled", sa.Boolean(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("secretary_whatsapp_number", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("secretary_planner_fingerprint", sa.String(length=64), nullable=True))
    with op.batch_alter_table("whatsapp_messages") as batch_op:
        batch_op.add_column(sa.Column("origin_trigger_id", sa.String(), nullable=True))
        batch_op.create_index("ix_whatsapp_messages_origin_trigger_id", ["origin_trigger_id"])


def downgrade() -> None:
    """Drop the secretary settings and the alert provenance column."""
    with op.batch_alter_table("whatsapp_messages") as batch_op:
        batch_op.drop_index("ix_whatsapp_messages_origin_trigger_id")
        batch_op.drop_column("origin_trigger_id")
    with op.batch_alter_table("notification_preferences") as batch_op:
        batch_op.drop_column("secretary_planner_fingerprint")
        batch_op.drop_column("secretary_whatsapp_number")
        batch_op.drop_column("secretary_whatsapp_enabled")
        batch_op.drop_column("secretary_active_end_hour")
        batch_op.drop_column("secretary_active_start_hour")
