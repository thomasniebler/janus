"""OAuth connections for Google and Microsoft; Google client credentials move to settings.toml.

Adds ``oauth_connections``, ``oauth_states.payload``, ``email_accounts.auth_type`` and
``connection_id``, and drops ``settings.google_client_id`` / ``google_client_secret``: the
Google OAuth client is now operator configuration (``google_client_id`` /
``google_client_secret`` in settings.toml or ``JANUS_GOOGLE_CLIENT_*``), not database state.

Google calendars connected before this revision get a connection row (the planner token is the same
encrypted blob). It also adds ``MICROSOFT_CALENDAR`` to the ``provider`` enum of ``planner_accounts``.

Revision ID: a1c4e7f9b3d5
Revises: c3f8a1d6e9b2
Create Date: 2026-11-12 12:00:00.000000
"""

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1c4e7f9b3d5"
down_revision: str | Sequence[str] | None = "c3f8a1d6e9b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_LEGACY_GOOGLE_SCOPES = (
    "openid",
    "email",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/tasks.readonly",
)
_OLD_PROVIDERS = ("GOOGLE_CALENDAR", "GOOGLE_TASKS", "O365", "JIRA", "ADO")
_NEW_PROVIDERS = ("GOOGLE_CALENDAR", "GOOGLE_TASKS", "MICROSOFT_CALENDAR", "O365", "JIRA", "ADO")


def _provider_enum(values: tuple[str, ...]) -> sa.Enum:
    return sa.Enum(*values, name="provider", native_enum=False, create_constraint=True)


def _set_provider_values(values: tuple[str, ...], previous: tuple[str, ...]) -> None:
    """Widen or narrow ``planner_accounts.provider`` on SQLite (PostgreSQL's enum is widened in place)."""
    with op.batch_alter_table("planner_accounts") as batch_op:
        batch_op.alter_column(
            "provider",
            existing_type=_provider_enum(previous),
            type_=_provider_enum(values),
            existing_nullable=True,
        )


def _backfill_google_connections() -> None:
    """Gives every user whose Google calendar was connected before connections a connection row.

    The planner account's token is the same encrypted credential blob a connection stores, so it is
    copied as is. The address is unknown and the mail scope was never granted: reconnecting adds both.
    """
    bind = op.get_bind()
    accounts = sa.table(
        "planner_accounts", sa.column("user_id", sa.Integer), sa.column("provider"), sa.column("token", sa.String)
    )
    connections = sa.table(
        "oauth_connections",
        sa.column("user_id", sa.Integer),
        sa.column("provider", sa.String),
        sa.column("credentials", sa.Text),
        sa.column("scopes", sa.JSON),
        sa.column("created_at", sa.DateTime),
        sa.column("updated_at", sa.DateTime),
    )
    rows = bind.execute(
        sa.select(accounts.c.user_id, accounts.c.token).where(
            accounts.c.provider == "GOOGLE_CALENDAR", accounts.c.token.isnot(None), accounts.c.token != ""
        )
    ).fetchall()
    now = datetime.now(UTC).replace(tzinfo=None)
    seen: set[int] = set()
    for user_id, token in rows:
        if user_id in seen:
            continue
        seen.add(user_id)
        bind.execute(
            connections.insert().values(
                user_id=user_id,
                provider="google",
                credentials=token,
                scopes=list(_LEGACY_GOOGLE_SCOPES),
                created_at=now,
                updated_at=now,
            )
        )


def upgrade() -> None:
    """Create the connection table, link mailboxes to it, and drop the DB-held Google client."""
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TYPE provider ADD VALUE IF NOT EXISTS 'MICROSOFT_CALENDAR'")
    else:
        _set_provider_values(_NEW_PROVIDERS, _OLD_PROVIDERS)
    op.create_table(
        "oauth_connections",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("account_email", sa.String(), nullable=True),
        sa.Column("credentials", sa.Text(), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("user_id", "provider", name="uq_oauth_connections_user_provider"),
    )
    op.create_index("ix_oauth_connections_id", "oauth_connections", ["id"])
    op.create_index("ix_oauth_connections_user_id", "oauth_connections", ["user_id"])
    _backfill_google_connections()
    with op.batch_alter_table("oauth_states") as batch_op:
        batch_op.add_column(sa.Column("payload", sa.Text(), nullable=True))
    with op.batch_alter_table("email_accounts") as batch_op:
        batch_op.add_column(sa.Column("auth_type", sa.String(), nullable=False, server_default="password"))
        batch_op.add_column(sa.Column("connection_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key("fk_email_accounts_connection_id", "oauth_connections", ["connection_id"], ["id"])
    with op.batch_alter_table("settings") as batch_op:
        batch_op.drop_column("google_client_secret")
        batch_op.drop_column("google_client_id")


def downgrade() -> None:
    """Restore the Google client columns (empty) and drop the connection table."""
    if op.get_bind().dialect.name != "postgresql":
        op.execute("DELETE FROM planner_accounts WHERE provider = 'MICROSOFT_CALENDAR'")
        _set_provider_values(_OLD_PROVIDERS, _NEW_PROVIDERS)
    with op.batch_alter_table("settings") as batch_op:
        batch_op.add_column(sa.Column("google_client_id", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("google_client_secret", sa.String(), nullable=True))
    with op.batch_alter_table("email_accounts") as batch_op:
        batch_op.drop_constraint("fk_email_accounts_connection_id", type_="foreignkey")
        batch_op.drop_column("connection_id")
        batch_op.drop_column("auth_type")
    with op.batch_alter_table("oauth_states") as batch_op:
        batch_op.drop_column("payload")
    op.drop_index("ix_oauth_connections_user_id", table_name="oauth_connections")
    op.drop_index("ix_oauth_connections_id", table_name="oauth_connections")
    op.drop_table("oauth_connections")
