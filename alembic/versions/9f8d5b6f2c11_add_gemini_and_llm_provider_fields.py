"""Add Gemini and provider-specific LLM settings fields."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9f8d5b6f2c11"
down_revision: str | Sequence[str] | None = "fa9753de9eed"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _is_postgresql() -> bool:
    """PostgreSQL stores this column as a native enum type, widened via ALTER TYPE instead."""
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    """Upgrade schema."""
    # PostgreSQL cannot remove enum values, so downgrade leaves the extra value in place.
    if _is_postgresql():
        op.execute("ALTER TYPE llmprovider ADD VALUE IF NOT EXISTS 'GEMINI'")
    with op.batch_alter_table("settings") as batch_op:
        if not _is_postgresql():
            batch_op.alter_column(
                "llm_provider",
                existing_type=sa.Enum("OLLAMA", "OPENAI", name="llmprovider"),
                type_=sa.Enum("OLLAMA", "OPENAI", "GEMINI", name="llmprovider"),
            )
        batch_op.add_column(sa.Column("ollama_model", sa.String(), nullable=True, server_default="llama3:latest"))
        batch_op.add_column(
            sa.Column("ollama_base_url", sa.String(), nullable=True, server_default="http://localhost:11434")
        )
        batch_op.add_column(sa.Column("openai_model", sa.String(), nullable=True, server_default="gpt-4o-mini"))
        batch_op.add_column(sa.Column("openai_base_url", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("gemini_model", sa.String(), nullable=True, server_default="gemini-2.5-flash"))
        batch_op.add_column(sa.Column("gemini_api_key", sa.String(), nullable=True))

    bind = op.get_bind()
    bind.execute(sa.text("UPDATE settings SET ollama_model = COALESCE(ollama_model, llm_model, 'llama3:latest')"))
    bind.execute(sa.text("UPDATE settings SET llm_model = COALESCE(llm_model, 'llama3:latest')"))
    bind.execute(sa.text("UPDATE settings SET openai_model = COALESCE(openai_model, 'gpt-4o-mini')"))
    bind.execute(sa.text("UPDATE settings SET gemini_model = COALESCE(gemini_model, 'gemini-2.5-flash')"))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("settings") as batch_op:
        batch_op.drop_column("gemini_api_key")
        batch_op.drop_column("gemini_model")
        batch_op.drop_column("openai_base_url")
        batch_op.drop_column("openai_model")
        batch_op.drop_column("ollama_base_url")
        batch_op.drop_column("ollama_model")
        if not _is_postgresql():
            batch_op.alter_column(
                "llm_provider",
                existing_type=sa.Enum("OLLAMA", "OPENAI", "GEMINI", name="llmprovider"),
                type_=sa.Enum("OLLAMA", "OPENAI", name="llmprovider"),
            )
