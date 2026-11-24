"""Add per-user tool permission modes; let unattended runs store images (JAN-359).

``tool_policy_modes`` holds one row per user with the blanket *Unattended*
(``per_tool``/``allow_all``) and *In chat* (``per_tool``/``allow_all``) modes.
``generated_images.conversation_id`` becomes nullable: an image made by an
unattended run under "Allow everything" belongs to no chat conversation.
The downgrade deletes such conversation-less image rows (their files stay on
disk) before restoring ``NOT NULL``.

Revision ID: a9c4e2f7b1d3
Revises: f3a7c1e9d2b4
Create Date: 2026-11-10 13:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a9c4e2f7b1d3"
down_revision: str | Sequence[str] | None = "f3a7c1e9d2b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "tool_policy_modes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("unattended_mode", sa.String(), server_default="per_tool", nullable=False),
        sa.Column("chat_mode", sa.String(), server_default="per_tool", nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_tool_policy_modes_id"), "tool_policy_modes", ["id"], unique=False)
    op.create_index(op.f("ix_tool_policy_modes_user_id"), "tool_policy_modes", ["user_id"], unique=True)
    with op.batch_alter_table("generated_images") as batch_op:
        batch_op.alter_column("conversation_id", existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DELETE FROM generated_images WHERE conversation_id IS NULL")
    with op.batch_alter_table("generated_images") as batch_op:
        batch_op.alter_column("conversation_id", existing_type=sa.Integer(), nullable=False)
    op.drop_index(op.f("ix_tool_policy_modes_user_id"), table_name="tool_policy_modes")
    op.drop_index(op.f("ix_tool_policy_modes_id"), table_name="tool_policy_modes")
    op.drop_table("tool_policy_modes")
