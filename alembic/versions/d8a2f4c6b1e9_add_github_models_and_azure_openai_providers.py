"""Add GitHub Models and Azure OpenAI LLM provider settings (JAN-340).

Widens the ``llmprovider`` enum with ``GITHUB_MODELS`` and ``AZURE_OPENAI`` and adds
their settings columns: the GitHub Models model, encrypted token, and optional base
URL; the Azure OpenAI endpoint, deployment, API version, encrypted key, and auth mode
(``entra`` uses the host's Azure identity, ``api_key`` the stored key). Existing
installs keep their current provider; the new columns are nullable, and the ones with
a default are backfilled by an ``UPDATE`` (the ORM supplies those defaults for new rows,
so no server default is left in the schema).

On SQLite the enum column is a plain ``VARCHAR`` (no CHECK constraint); it is widened
from ``VARCHAR(10)`` to fit ``GITHUB_MODELS`` so the migrated schema matches the model.
PostgreSQL widens its native ``llmprovider`` type instead.

Revision ID: d8a2f4c6b1e9
Revises: c3e9a1f7d2b8
Create Date: 2026-11-09 19:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d8a2f4c6b1e9"
down_revision: str | Sequence[str] | None = "c3e9a1f7d2b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PREVIOUS_PROVIDERS = ("OLLAMA", "OPENAI", "OPENROUTER", "GEMINI", "ANTHROPIC")
_NEW_PROVIDERS = ("GITHUB_MODELS", "AZURE_OPENAI")
_PROVIDERS = (*_PREVIOUS_PROVIDERS, *_NEW_PROVIDERS)

#: Columns with a default, backfilled on existing rows.
_DEFAULTS = {
    "github_models_model": "openai/gpt-4.1-mini",
    "azure_openai_api_version": "2024-10-21",
    "azure_openai_auth_mode": "entra",
}
_NULLABLE_COLUMNS = (
    "github_models_api_key",
    "github_models_base_url",
    "azure_openai_endpoint",
    "azure_openai_deployment",
    "azure_openai_api_key",
)


def _is_postgresql() -> bool:
    """PostgreSQL stores this column as a native enum type, widened via ALTER TYPE instead."""
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    """Upgrade schema."""
    # PostgreSQL cannot remove enum values, so downgrade leaves the extra values in place.
    if _is_postgresql():
        for value in _NEW_PROVIDERS:
            op.execute(f"ALTER TYPE llmprovider ADD VALUE IF NOT EXISTS '{value}'")
    with op.batch_alter_table("settings") as batch_op:
        if not _is_postgresql():
            batch_op.alter_column(
                "llm_provider",
                existing_type=sa.Enum(*_PREVIOUS_PROVIDERS, name="llmprovider"),
                type_=sa.Enum(*_PROVIDERS, name="llmprovider"),
            )
        for column in (*_DEFAULTS, *_NULLABLE_COLUMNS):
            batch_op.add_column(sa.Column(column, sa.String(), nullable=True))

    settings = sa.table("settings", *(sa.column(column, sa.String()) for column in _DEFAULTS))
    op.get_bind().execute(sa.update(settings).values(**_DEFAULTS))


#: Free-text (provider, model) choices that may name a new provider; the older code rejects them.
_CHOICE_COLUMNS = (
    ("settings", "chat_llm_provider", "chat_llm_model"),
    ("settings", "utility_llm_provider", "utility_llm_model"),
    ("trigger_definitions", "llm_provider", "llm_model"),
    ("runtime_delegations", "llm_provider", "llm_model"),
)


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    bind.execute(
        sa.text("UPDATE settings SET llm_provider = 'OLLAMA' WHERE llm_provider IN ('GITHUB_MODELS', 'AZURE_OPENAI')")
    )
    # Reset choices naming a new provider to the default model; the model goes too, since
    # a model without a provider would run on the default provider (a deployment name on Ollama).
    for table_name, provider_column, model_column in _CHOICE_COLUMNS:
        table = sa.table(table_name, sa.column(provider_column, sa.String()), sa.column(model_column, sa.String()))
        bind.execute(
            sa.update(table)
            .where(table.c[provider_column].in_(("github_models", "azure_openai")))
            .values({provider_column: None, model_column: None})
        )
    with op.batch_alter_table("settings") as batch_op:
        for column in reversed(_NULLABLE_COLUMNS):
            batch_op.drop_column(column)
        for column in reversed(tuple(_DEFAULTS)):
            batch_op.drop_column(column)
        if not _is_postgresql():
            batch_op.alter_column(
                "llm_provider",
                existing_type=sa.Enum(*_PROVIDERS, name="llmprovider"),
                type_=sa.Enum(*_PREVIOUS_PROVIDERS, name="llmprovider"),
            )
