"""Add wiki pages and their revisions (JAN-244).

``wiki_pages`` holds one Markdown page per tracked topic or entity with its
citations and the hash of the inputs it was written from;
``wiki_page_revisions`` keeps every published version and pending proposals, so
a rewrite never destroys a user edit. Additive; the built-in Wiki synthesis
trigger (off by default) fills them.

Revision ID: d130c10835c8
Revises: be633b5aa1b6
Create Date: 2026-09-29 07:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d130c10835c8"
down_revision: str | Sequence[str] | None = "be633b5aa1b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "wiki_pages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(length=200), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("subject_type", sa.String(length=16), nullable=False),
        sa.Column("subject_id", sa.Integer(), nullable=True),
        sa.Column("body", sa.Text(), server_default="", nullable=False),
        sa.Column("citations", sa.JSON(), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=True),
        sa.Column("synthesized_at", sa.DateTime(), nullable=True),
        sa.Column("checked_at", sa.DateTime(), nullable=True),
        sa.Column("user_edited", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "slug", name="uq_wiki_pages_slug"),
    )
    op.create_index(op.f("ix_wiki_pages_id"), "wiki_pages", ["id"], unique=False)
    op.create_index("ix_wiki_pages_subject", "wiki_pages", ["user_id", "subject_type", "subject_id"], unique=False)
    op.create_index(op.f("ix_wiki_pages_user_id"), "wiki_pages", ["user_id"], unique=False)
    op.create_table(
        "wiki_page_revisions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("page_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("citations", sa.JSON(), nullable=False),
        sa.Column("author", sa.String(length=16), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=True),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column("input_hash", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["page_id"], ["wiki_pages.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_wiki_page_revisions_id"), "wiki_page_revisions", ["id"], unique=False)
    op.create_index("ix_wiki_page_revisions_page", "wiki_page_revisions", ["page_id", "kind", "status"], unique=False)
    op.create_index(op.f("ix_wiki_page_revisions_user_id"), "wiki_page_revisions", ["user_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_wiki_page_revisions_user_id"), table_name="wiki_page_revisions")
    op.drop_index("ix_wiki_page_revisions_page", table_name="wiki_page_revisions")
    op.drop_index(op.f("ix_wiki_page_revisions_id"), table_name="wiki_page_revisions")
    op.drop_table("wiki_page_revisions")
    op.drop_index(op.f("ix_wiki_pages_user_id"), table_name="wiki_pages")
    op.drop_index("ix_wiki_pages_subject", table_name="wiki_pages")
    op.drop_index(op.f("ix_wiki_pages_id"), table_name="wiki_pages")
    op.drop_table("wiki_pages")
