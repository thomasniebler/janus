"""Add the bounded adaptive character state with audit and proposals (JAN-301).

Additive only: the nullable JSON ``character_profiles.state_envelope`` (the
owner's allowed values and defaults; ``NULL`` = the built-in envelope), and
three owner-scoped tables: ``character_states`` (one current state per user),
``character_state_proposals`` (changes Janus proposed in chat, inert until the
owner approves them), and ``character_state_changes`` (the audit trail used for
history and rollback). No backfill: a user without a ``character_states`` row
has the envelope defaults.

Revision ID: b4d7e2a9c6f1
Revises: b8d4f2a6c1e9
Create Date: 2026-10-12 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b4d7e2a9c6f1"
down_revision: str | Sequence[str] | None = "b8d4f2a6c1e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the envelope column and the three state tables."""
    with op.batch_alter_table("character_profiles") as batch_op:
        batch_op.add_column(sa.Column("state_envelope", sa.JSON(), nullable=True))

    op.create_table(
        "character_states",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("tone", sa.String(length=32), nullable=False),
        sa.Column("verbosity", sa.String(length=32), nullable=False),
        sa.Column("proactivity", sa.String(length=32), nullable=False),
        sa.Column("current_focus", sa.String(length=200), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_character_states_id", "character_states", ["id"], unique=False)
    op.create_index("ix_character_states_user_id", "character_states", ["user_id"], unique=True)

    op.create_table(
        "character_state_proposals",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="proposed"),
        sa.Column("changes", sa.JSON(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("chat_turn_id", sa.String(length=200), nullable=True),
        sa.Column("conversation_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_character_state_proposals_id", "character_state_proposals", ["id"], unique=False)
    op.create_index("ix_character_state_proposals_user_id", "character_state_proposals", ["user_id"], unique=False)
    op.create_index("ix_character_state_proposals_status", "character_state_proposals", ["status"], unique=False)

    op.create_table(
        "character_state_changes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("actor", sa.String(length=16), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("chat_turn_id", sa.String(length=200), nullable=True),
        sa.Column("proposal_id", sa.Integer(), nullable=True),
        sa.Column("rollback_of_id", sa.Integer(), nullable=True),
        sa.Column("previous_state", sa.JSON(), nullable=False),
        sa.Column("new_state", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["proposal_id"], ["character_state_proposals.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_character_state_changes_id", "character_state_changes", ["id"], unique=False)
    op.create_index("ix_character_state_changes_user_id", "character_state_changes", ["user_id"], unique=False)
    op.create_index("ix_character_state_changes_proposal_id", "character_state_changes", ["proposal_id"], unique=False)


def downgrade() -> None:
    """Drop the state tables and the envelope column (state, proposals and history are lost)."""
    op.drop_index("ix_character_state_changes_proposal_id", table_name="character_state_changes")
    op.drop_index("ix_character_state_changes_user_id", table_name="character_state_changes")
    op.drop_index("ix_character_state_changes_id", table_name="character_state_changes")
    op.drop_table("character_state_changes")
    op.drop_index("ix_character_state_proposals_status", table_name="character_state_proposals")
    op.drop_index("ix_character_state_proposals_user_id", table_name="character_state_proposals")
    op.drop_index("ix_character_state_proposals_id", table_name="character_state_proposals")
    op.drop_table("character_state_proposals")
    op.drop_index("ix_character_states_user_id", table_name="character_states")
    op.drop_index("ix_character_states_id", table_name="character_states")
    op.drop_table("character_states")
    with op.batch_alter_table("character_profiles") as batch_op:
        batch_op.drop_column("state_envelope")
