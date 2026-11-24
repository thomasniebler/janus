"""Add the knowledge graph (JAN-243).

Entities, mentions (chunk <-> entity), relations with their supporting chunks,
entity merge suggestions, per-user monthly LLM usage and budget, and
knowledge_chunks.extracted_hash to find chunks still awaiting extraction.
Additive; the built-in Knowledge extraction trigger (off by default) fills it.

Revision ID: be633b5aa1b6
Revises: c4e1a7b9d2f3
Create Date: 2026-09-28 20:35:02.924342
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "be633b5aa1b6"
down_revision: str | Sequence[str] | None = "c4e1a7b9d2f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "knowledge_entities",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("entity_type", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("normalized_name", sa.String(length=200), nullable=False),
        sa.Column("aliases", sa.JSON(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "entity_type", "normalized_name", name="uq_knowledge_entities_name"),
    )
    op.create_index(op.f("ix_knowledge_entities_id"), "knowledge_entities", ["id"], unique=False)
    op.create_index(op.f("ix_knowledge_entities_user_id"), "knowledge_entities", ["user_id"], unique=False)
    op.create_table(
        "knowledge_llm_usage",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("period", sa.String(length=7), nullable=False),
        sa.Column("input_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("output_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("calls", sa.Integer(), server_default="0", nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "period", name="uq_knowledge_llm_usage_period"),
    )
    op.create_index(op.f("ix_knowledge_llm_usage_id"), "knowledge_llm_usage", ["id"], unique=False)
    op.create_index(op.f("ix_knowledge_llm_usage_user_id"), "knowledge_llm_usage", ["user_id"], unique=False)
    op.create_table(
        "knowledge_preferences",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("monthly_token_budget", sa.Integer(), server_default="2000000", nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_index(op.f("ix_knowledge_preferences_id"), "knowledge_preferences", ["id"], unique=False)
    op.create_table(
        "knowledge_merge_suggestions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("duplicate_id", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["duplicate_id"], ["knowledge_entities.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["entity_id"], ["knowledge_entities.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("entity_id", "duplicate_id", name="uq_knowledge_merge_suggestions_pair"),
    )
    op.create_index(op.f("ix_knowledge_merge_suggestions_id"), "knowledge_merge_suggestions", ["id"], unique=False)
    op.create_index(
        op.f("ix_knowledge_merge_suggestions_user_id"), "knowledge_merge_suggestions", ["user_id"], unique=False
    )
    op.create_table(
        "knowledge_relations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("subject_id", sa.Integer(), nullable=False),
        sa.Column("predicate", sa.String(length=64), nullable=False),
        sa.Column("object_id", sa.Integer(), nullable=False),
        sa.Column("confidence", sa.String(length=8), nullable=False),
        sa.Column("valid_from", sa.DateTime(), nullable=True),
        sa.Column("valid_to", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["object_id"], ["knowledge_entities.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["subject_id"], ["knowledge_entities.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("subject_id", "predicate", "object_id", name="uq_knowledge_relations_triple"),
    )
    op.create_index(op.f("ix_knowledge_relations_id"), "knowledge_relations", ["id"], unique=False)
    op.create_index(op.f("ix_knowledge_relations_object_id"), "knowledge_relations", ["object_id"], unique=False)
    op.create_index(op.f("ix_knowledge_relations_subject_id"), "knowledge_relations", ["subject_id"], unique=False)
    op.create_index(op.f("ix_knowledge_relations_user_id"), "knowledge_relations", ["user_id"], unique=False)
    op.create_table(
        "knowledge_mentions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("chunk_id", sa.Integer(), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["chunk_id"], ["knowledge_chunks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["entity_id"], ["knowledge_entities.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("chunk_id", "entity_id", name="uq_knowledge_mentions_pair"),
    )
    op.create_index(op.f("ix_knowledge_mentions_chunk_id"), "knowledge_mentions", ["chunk_id"], unique=False)
    op.create_index(op.f("ix_knowledge_mentions_entity_id"), "knowledge_mentions", ["entity_id"], unique=False)
    op.create_index(op.f("ix_knowledge_mentions_id"), "knowledge_mentions", ["id"], unique=False)
    op.create_index(op.f("ix_knowledge_mentions_user_id"), "knowledge_mentions", ["user_id"], unique=False)
    op.create_table(
        "knowledge_relation_evidence",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("relation_id", sa.Integer(), nullable=False),
        sa.Column("chunk_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["chunk_id"], ["knowledge_chunks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["relation_id"], ["knowledge_relations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("relation_id", "chunk_id", name="uq_knowledge_relation_evidence_pair"),
    )
    op.create_index(
        op.f("ix_knowledge_relation_evidence_chunk_id"), "knowledge_relation_evidence", ["chunk_id"], unique=False
    )
    op.create_index(op.f("ix_knowledge_relation_evidence_id"), "knowledge_relation_evidence", ["id"], unique=False)
    op.create_index(
        op.f("ix_knowledge_relation_evidence_relation_id"), "knowledge_relation_evidence", ["relation_id"], unique=False
    )
    op.add_column("knowledge_chunks", sa.Column("extracted_hash", sa.String(length=64), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("knowledge_chunks") as batch_op:
        batch_op.drop_column("extracted_hash")
    op.drop_index(op.f("ix_knowledge_relation_evidence_relation_id"), table_name="knowledge_relation_evidence")
    op.drop_index(op.f("ix_knowledge_relation_evidence_id"), table_name="knowledge_relation_evidence")
    op.drop_index(op.f("ix_knowledge_relation_evidence_chunk_id"), table_name="knowledge_relation_evidence")
    op.drop_table("knowledge_relation_evidence")
    op.drop_index(op.f("ix_knowledge_mentions_user_id"), table_name="knowledge_mentions")
    op.drop_index(op.f("ix_knowledge_mentions_id"), table_name="knowledge_mentions")
    op.drop_index(op.f("ix_knowledge_mentions_entity_id"), table_name="knowledge_mentions")
    op.drop_index(op.f("ix_knowledge_mentions_chunk_id"), table_name="knowledge_mentions")
    op.drop_table("knowledge_mentions")
    op.drop_index(op.f("ix_knowledge_relations_user_id"), table_name="knowledge_relations")
    op.drop_index(op.f("ix_knowledge_relations_subject_id"), table_name="knowledge_relations")
    op.drop_index(op.f("ix_knowledge_relations_object_id"), table_name="knowledge_relations")
    op.drop_index(op.f("ix_knowledge_relations_id"), table_name="knowledge_relations")
    op.drop_table("knowledge_relations")
    op.drop_index(op.f("ix_knowledge_merge_suggestions_user_id"), table_name="knowledge_merge_suggestions")
    op.drop_index(op.f("ix_knowledge_merge_suggestions_id"), table_name="knowledge_merge_suggestions")
    op.drop_table("knowledge_merge_suggestions")
    op.drop_index(op.f("ix_knowledge_preferences_id"), table_name="knowledge_preferences")
    op.drop_table("knowledge_preferences")
    op.drop_index(op.f("ix_knowledge_llm_usage_user_id"), table_name="knowledge_llm_usage")
    op.drop_index(op.f("ix_knowledge_llm_usage_id"), table_name="knowledge_llm_usage")
    op.drop_table("knowledge_llm_usage")
    op.drop_index(op.f("ix_knowledge_entities_user_id"), table_name="knowledge_entities")
    op.drop_index(op.f("ix_knowledge_entities_id"), table_name="knowledge_entities")
    op.drop_table("knowledge_entities")
