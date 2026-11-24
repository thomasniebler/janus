"""Add OpenRouter LLM settings.

Revision ID: e7a9c2d1f4b8
Revises: 4d2e6f8a1b9c
Create Date: 2026-05-31 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e7a9c2d1f4b8"
down_revision: str | Sequence[str] | None = "4d2e6f8a1b9c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _is_postgresql() -> bool:
    """PostgreSQL stores this column as a native enum type, widened via ALTER TYPE instead."""
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    """Upgrade schema."""
    # PostgreSQL cannot remove enum values, so downgrade leaves the extra value in place.
    if _is_postgresql():
        op.execute("ALTER TYPE llmprovider ADD VALUE IF NOT EXISTS 'OPENROUTER'")
    with op.batch_alter_table("settings") as batch_op:
        if not _is_postgresql():
            batch_op.alter_column(
                "llm_provider",
                existing_type=sa.Enum("OLLAMA", "OPENAI", "GEMINI", name="llmprovider"),
                type_=sa.Enum("OLLAMA", "OPENAI", "OPENROUTER", "GEMINI", name="llmprovider"),
            )
        batch_op.add_column(sa.Column("openrouter_model", sa.String(), nullable=True, server_default="openrouter/auto"))
        batch_op.add_column(
            sa.Column("openrouter_base_url", sa.String(), nullable=True, server_default="https://openrouter.ai/api/v1")
        )
        batch_op.add_column(sa.Column("openrouter_api_key", sa.String(), nullable=True))

    bind = op.get_bind()
    bind.execute(sa.text("UPDATE settings SET openrouter_model = COALESCE(openrouter_model, 'openrouter/auto')"))
    bind.execute(
        sa.text(
            "UPDATE settings SET openrouter_base_url = COALESCE(openrouter_base_url, 'https://openrouter.ai/api/v1')"
        )
    )


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    bind.execute(sa.text("UPDATE settings SET llm_provider = 'OLLAMA' WHERE llm_provider = 'OPENROUTER'"))

    with op.batch_alter_table("settings") as batch_op:
        batch_op.drop_column("openrouter_api_key")
        batch_op.drop_column("openrouter_base_url")
        batch_op.drop_column("openrouter_model")
        if not _is_postgresql():
            batch_op.alter_column(
                "llm_provider",
                existing_type=sa.Enum("OLLAMA", "OPENAI", "OPENROUTER", "GEMINI", name="llmprovider"),
                type_=sa.Enum("OLLAMA", "OPENAI", "GEMINI", name="llmprovider"),
            )
