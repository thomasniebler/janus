"""Memory review: verification, verbatim evidence, access counts, and the memory index.

Adds to ``agent_memory_facts``:

- ``access_count``: times Janus's memory tools returned the fact.
- ``verification`` / ``verified_at`` / ``evidence_quote``: the memory review's
  check of the fact against its source conversation, with the exact passage.
- ``review_due_at``: when the review checks the fact again (``NULL`` = due).
- ``promoted`` / ``pin``: membership of the small memory index injected into
  every prompt, chosen by the review, overridable by the user.

Every existing fact starts ``unverified`` and due, so the first memory review
checks the ones that still have a source conversation.

Revision ID: f3c5e7a9b1d4
Revises: d7a9c1e3f5b8
Create Date: 2026-11-27 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f3c5e7a9b1d4"
down_revision: str | Sequence[str] | None = "d7a9c1e3f5b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the memory review columns."""
    with op.batch_alter_table("agent_memory_facts") as batch:
        batch.add_column(sa.Column("access_count", sa.Integer(), server_default="0", nullable=False))
        batch.add_column(sa.Column("verification", sa.String(length=20), server_default="unverified", nullable=False))
        batch.add_column(sa.Column("verified_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("evidence_quote", sa.Text(), nullable=True))
        batch.add_column(sa.Column("review_due_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("promoted", sa.Boolean(), server_default="0", nullable=False))
        batch.add_column(sa.Column("pin", sa.String(length=10), nullable=True))


def downgrade() -> None:
    """Drop the memory review columns."""
    with op.batch_alter_table("agent_memory_facts") as batch:
        for column in (
            "pin",
            "promoted",
            "review_due_at",
            "evidence_quote",
            "verified_at",
            "verification",
            "access_count",
        ):
            batch.drop_column(column)
