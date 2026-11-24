"""Add knowledge embeddings and embedding settings (JAN-241).

Adds ``knowledge_embeddings`` (one float32 vector per memory fact or tracked
topic) and the nullable ``settings.embedding_provider``/``embedding_model``
columns plus ``embedding_min_similarity`` (server default ``0.5``). Additive;
the knowledge-index worker backfills the vectors once a model is configured.
The SQLite FTS5 keyword tables are created at runtime, not here.

Revision ID: b3c4d5e6f7a8
Revises: 4c63f4a61e9b
Create Date: 2026-09-28 09:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b3c4d5e6f7a8"
down_revision: str | Sequence[str] | None = "4c63f4a61e9b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "knowledge_embeddings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("owner_type", sa.String(length=32), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("source_updated_at", sa.DateTime(), nullable=True),
        sa.Column("vector", sa.LargeBinary(), nullable=False),
        sa.Column("embedded_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_type", "owner_id", name="uq_knowledge_embeddings_owner"),
    )
    op.create_index(op.f("ix_knowledge_embeddings_id"), "knowledge_embeddings", ["id"], unique=False)
    op.create_index(op.f("ix_knowledge_embeddings_user_id"), "knowledge_embeddings", ["user_id"], unique=False)
    op.create_index(
        "ix_knowledge_embeddings_search", "knowledge_embeddings", ["user_id", "owner_type", "model"], unique=False
    )
    with op.batch_alter_table("settings") as batch_op:
        batch_op.add_column(sa.Column("embedding_provider", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("embedding_model", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("embedding_min_similarity", sa.Float(), server_default="0.5", nullable=False))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("settings") as batch_op:
        batch_op.drop_column("embedding_min_similarity")
        batch_op.drop_column("embedding_model")
        batch_op.drop_column("embedding_provider")
    op.drop_index("ix_knowledge_embeddings_search", table_name="knowledge_embeddings")
    op.drop_index(op.f("ix_knowledge_embeddings_user_id"), table_name="knowledge_embeddings")
    op.drop_index(op.f("ix_knowledge_embeddings_id"), table_name="knowledge_embeddings")
    op.drop_table("knowledge_embeddings")
