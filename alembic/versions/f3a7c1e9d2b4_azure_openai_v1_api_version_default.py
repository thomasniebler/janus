"""Move the Azure OpenAI api-version default to the v1 endpoint (JAN-356).

Janus now reaches Azure OpenAI through its v1 endpoint (``{endpoint}/openai/v1``,
``api-version=preview``) by default, where gpt-6-family deployments can use the
Responses API with tools. A dated api-version now selects the legacy deployments route.
Rows that still hold the old default ``2024-10-21`` are switched to ``preview``. Other
dated versions are kept, along with their legacy route. The column default lives in the
ORM, so no server default changes.

Lossy in both directions (JAN-357): a stored value does not record whether the admin saved
it deliberately, so the upgrade also moves a deliberately chosen ``2024-10-21`` to
``preview``. The downgrade also maps a deliberately chosen ``preview`` (and the other v1
values ``latest`` and ``v1``) back to ``2024-10-21``, which the older code needs for its
deployments route.

Revision ID: f3a7c1e9d2b4
Revises: e6b2d8f4a1c3
Create Date: 2026-11-10 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f3a7c1e9d2b4"
down_revision: str | Sequence[str] | None = "e6b2d8f4a1c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_DEFAULT = "2024-10-21"
_NEW_DEFAULT = "preview"
#: Every non-empty v1 value; the older code would send them verbatim to the deployments route,
#: which rejects them, so the downgrade maps them all back to the old default (JAN-357).
_V1_VALUES = ("preview", "latest", "v1")
_settings = sa.table("settings", sa.column("azure_openai_api_version", sa.String()))


def upgrade() -> None:
    """Upgrade schema."""
    op.get_bind().execute(
        sa.update(_settings)
        .where(_settings.c.azure_openai_api_version == _OLD_DEFAULT)
        .values(azure_openai_api_version=_NEW_DEFAULT)
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.get_bind().execute(
        sa.update(_settings)
        .where(_settings.c.azure_openai_api_version.in_(_V1_VALUES))
        .values(azure_openai_api_version=_OLD_DEFAULT)
    )
