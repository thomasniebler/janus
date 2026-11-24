"""Add auth session revocation support.

Revision ID: 2f1a6c3d4e5b
Revises: c9a8b7e6d5f4
Create Date: 2026-04-19 23:40:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2f1a6c3d4e5b"
down_revision: str | None = "c9a8b7e6d5f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    user_columns = {column["name"] for column in inspector.get_columns("users")}
    if "session_generation" not in user_columns:
        op.add_column(
            "users",
            sa.Column("session_generation", sa.Integer(), nullable=False, server_default="0"),
        )

    if not inspector.has_table("revoked_sessions"):
        op.create_table(
            "revoked_sessions",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("session_id", sa.String(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("reason", sa.String(), nullable=False),
            sa.Column("revoked_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("expires_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.UniqueConstraint("session_id"),
        )
        op.create_index(op.f("ix_revoked_sessions_user_id"), "revoked_sessions", ["user_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("revoked_sessions"):
        op.drop_index(op.f("ix_revoked_sessions_user_id"), table_name="revoked_sessions")
        op.drop_table("revoked_sessions")

    user_columns = {column["name"] for column in inspector.get_columns("users")}
    if "session_generation" in user_columns:
        op.drop_column("users", "session_generation")
