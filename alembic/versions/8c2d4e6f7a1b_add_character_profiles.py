"""Add user-owned character constitutions."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "8c2d4e6f7a1b"
down_revision: str | None = "7b1c2d3e4f5a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the per-user character profile table."""
    op.create_table(
        "character_profiles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("constitution_id", sa.String(), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("identity", sa.Text(), nullable=False),
        sa.Column("core_values", sa.JSON(), nullable=False),
        sa.Column("non_negotiables", sa.JSON(), nullable=False),
        sa.Column("traits", sa.JSON(), nullable=False),
        sa.Column("exploration_rate", sa.Float(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_index("ix_character_profiles_id", "character_profiles", ["id"], unique=False)
    op.create_index("ix_character_profiles_user_id", "character_profiles", ["user_id"], unique=True)


def downgrade() -> None:
    """Remove user-owned character profiles."""
    op.drop_index("ix_character_profiles_user_id", table_name="character_profiles")
    op.drop_index("ix_character_profiles_id", table_name="character_profiles")
    op.drop_table("character_profiles")
