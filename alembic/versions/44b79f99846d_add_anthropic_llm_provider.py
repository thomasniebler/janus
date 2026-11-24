"""Add Anthropic (Claude) LLM provider settings (JAN-214).

Widens the ``llmprovider`` enum with ``ANTHROPIC`` and adds the Anthropic model,
encrypted API key, and optional base URL columns. Existing installs keep their
current provider; the new columns are nullable (the model gets a default).

Revision ID: 44b79f99846d
Revises: c4e7a9d2f1b3
Create Date: 2026-09-26 07:16:51.731144
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "44b79f99846d"
down_revision: str | Sequence[str] | None = "c4e7a9d2f1b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _is_postgresql() -> bool:
    """PostgreSQL stores this column as a native enum type, widened via ALTER TYPE instead."""
    return op.get_bind().dialect.name == "postgresql"


_DEFAULT_ANTHROPIC_MODEL = "claude-opus-5"
_PREVIOUS_PROVIDERS = ("OLLAMA", "OPENAI", "OPENROUTER", "GEMINI")
_PROVIDERS = (*_PREVIOUS_PROVIDERS, "ANTHROPIC")


def upgrade() -> None:
    """Upgrade schema."""
    # PostgreSQL cannot remove enum values, so downgrade leaves the extra value in place.
    if _is_postgresql():
        op.execute("ALTER TYPE llmprovider ADD VALUE IF NOT EXISTS 'ANTHROPIC'")
    with op.batch_alter_table("settings") as batch_op:
        if not _is_postgresql():
            batch_op.alter_column(
                "llm_provider",
                existing_type=sa.Enum(*_PREVIOUS_PROVIDERS, name="llmprovider"),
                type_=sa.Enum(*_PROVIDERS, name="llmprovider"),
            )
        batch_op.add_column(
            sa.Column("anthropic_model", sa.String(), nullable=True, server_default=_DEFAULT_ANTHROPIC_MODEL)
        )
        batch_op.add_column(sa.Column("anthropic_api_key", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("anthropic_base_url", sa.String(), nullable=True))

    bind = op.get_bind()
    bind.execute(
        sa.text("UPDATE settings SET anthropic_model = COALESCE(anthropic_model, :model)"),
        {"model": _DEFAULT_ANTHROPIC_MODEL},
    )


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    bind.execute(sa.text("UPDATE settings SET llm_provider = 'OLLAMA' WHERE llm_provider = 'ANTHROPIC'"))

    with op.batch_alter_table("settings") as batch_op:
        batch_op.drop_column("anthropic_base_url")
        batch_op.drop_column("anthropic_api_key")
        batch_op.drop_column("anthropic_model")
        if not _is_postgresql():
            batch_op.alter_column(
                "llm_provider",
                existing_type=sa.Enum(*_PROVIDERS, name="llmprovider"),
                type_=sa.Enum(*_PREVIOUS_PROVIDERS, name="llmprovider"),
            )
