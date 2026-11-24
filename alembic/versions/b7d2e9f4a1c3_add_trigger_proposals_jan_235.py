"""Add the agent-proposed automation columns to trigger definitions (JAN-235).

Janus may draft a trigger definition from chat; it is stored inactive with
``proposal_status = 'proposed'`` and only runs once the user approves it. All
columns are nullable, so every existing row (user, system, and Janus's one-time
follow-ups) keeps ``proposal_status = NULL`` and needs no backfill.

Revision ID: b7d2e9f4a1c3
Revises: a3e5c7d9f1b2
Create Date: 2026-10-02 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7d2e9f4a1c3"
down_revision: str | Sequence[str] | None = "a3e5c7d9f1b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX = "ix_trigger_definitions_user_proposal_status"
_SUPERSEDES_FK = "fk_trigger_definitions_supersedes_id"
_APPROVER_FK = "fk_trigger_definitions_approved_by_user_id"


def upgrade() -> None:
    """Add the proposal lifecycle, provenance, and approval columns."""
    with op.batch_alter_table("trigger_definitions") as batch_op:
        batch_op.add_column(sa.Column("proposal_status", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("proposal_rationale", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("proposed_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("proposal_source", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("supersedes_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("approved_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("approved_by_user_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("approval_fingerprint", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("rejected_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("rejection_reason", sa.Text(), nullable=True))
        batch_op.create_foreign_key(
            _SUPERSEDES_FK, "trigger_definitions", ["supersedes_id"], ["id"], ondelete="SET NULL"
        )
        batch_op.create_foreign_key(_APPROVER_FK, "users", ["approved_by_user_id"], ["id"], ondelete="SET NULL")
        batch_op.create_index(_INDEX, ["user_id", "proposal_status"], unique=False)


def downgrade() -> None:
    """Drop the proposal columns; only Janus's own drafts ever used them."""
    with op.batch_alter_table("trigger_definitions") as batch_op:
        batch_op.drop_index(_INDEX)
        batch_op.drop_constraint(_APPROVER_FK, type_="foreignkey")
        batch_op.drop_constraint(_SUPERSEDES_FK, type_="foreignkey")
        for column in (
            "rejection_reason",
            "rejected_at",
            "approval_fingerprint",
            "approved_by_user_id",
            "approved_at",
            "supersedes_id",
            "proposal_source",
            "proposed_at",
            "proposal_rationale",
            "proposal_status",
        ):
            batch_op.drop_column(column)
