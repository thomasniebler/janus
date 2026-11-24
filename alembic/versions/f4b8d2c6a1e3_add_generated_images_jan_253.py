"""Add chat-generated images and the image-generation settings (JAN-253).

Additive only: a new ``generated_images`` table with one metadata row per
image the chat tool ``generate_image`` stored (the bytes are files under
``image_storage_dir``, never blobs), and the nullable
``settings.image_provider`` / ``settings.image_model`` columns. Image
generation stays off (``image_provider`` ``NULL``) until an admin picks a
provider. Rows cascade with their user and conversation; ``message_id`` is
set to ``NULL`` when its message goes.

Revision ID: f4b8d2c6a1e3
Revises: e3a7c1b9d4f2
Create Date: 2026-10-05 16:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f4b8d2c6a1e3"
down_revision: str | Sequence[str] | None = "e3a7c1b9d4f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "generated_images"


def upgrade() -> None:
    """Create the image table and its indexes, and add the settings columns."""
    op.create_table(
        _TABLE,
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=True),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("path", sa.String(), nullable=False),
        sa.Column("mime_type", sa.String(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["message_id"], ["conversation_messages.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_generated_images_user_id", _TABLE, ["user_id"], unique=False)
    op.create_index("ix_generated_images_conversation_id", _TABLE, ["conversation_id"], unique=False)
    with op.batch_alter_table("settings") as batch_op:
        batch_op.add_column(sa.Column("image_provider", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("image_model", sa.String(), nullable=True))


def downgrade() -> None:
    """Drop the settings columns and the image table (files on disk are left alone)."""
    with op.batch_alter_table("settings") as batch_op:
        batch_op.drop_column("image_model")
        batch_op.drop_column("image_provider")
    op.drop_index("ix_generated_images_conversation_id", table_name=_TABLE)
    op.drop_index("ix_generated_images_user_id", table_name=_TABLE)
    op.drop_table(_TABLE)
