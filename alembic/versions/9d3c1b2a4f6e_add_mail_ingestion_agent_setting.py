"""Add mail ingestion agent setting.

Revision ID: 9d3c1b2a4f6e
Revises: c0f1e2d3c4b5
Create Date: 2026-05-20 01:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9d3c1b2a4f6e"
down_revision: str | None = "c0f1e2d3c4b5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FK_NAME = "fk_settings_mail_ingestion_agent_definition_id_agent_definitions"
# PostgreSQL caps identifiers at 63 characters; existing SQLite databases keep the original name.
_PG_FK_NAME = "fk_settings_mail_ingestion_agent_definition_id"


def _fk_name() -> str:
    """Return the foreign-key name valid for the current dialect."""
    return _PG_FK_NAME if op.get_bind().dialect.name == "postgresql" else _FK_NAME


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("settings") as batch_op:
        batch_op.add_column(sa.Column("mail_ingestion_agent_definition_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            _fk_name(),
            "agent_definitions",
            ["mail_ingestion_agent_definition_id"],
            ["id"],
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("settings") as batch_op:
        batch_op.drop_constraint(_fk_name(), type_="foreignkey")
        batch_op.drop_column("mail_ingestion_agent_definition_id")
