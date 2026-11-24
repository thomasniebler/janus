"""Add owner-scoped MCP servers, discovered tools, credentials, and OAuth state."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "1a2b3c4d5e6f"
down_revision: str = "15b22595524c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the additive managed MCP registry schema."""
    op.create_table(
        "mcp_servers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("provider_name", sa.String(), nullable=False),
        sa.Column("endpoint", sa.String(), nullable=False),
        sa.Column("transport", sa.String(), nullable=False, server_default="streamable_http"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("discovery_status", sa.String(), nullable=False, server_default="never"),
        sa.Column("last_health_status", sa.String(), nullable=True),
        sa.Column("last_health_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("oauth_authorization_endpoint", sa.String(), nullable=True),
        sa.Column("oauth_token_endpoint", sa.String(), nullable=True),
        sa.Column("oauth_client_id", sa.String(), nullable=True),
        sa.Column("oauth_scopes", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_mcp_servers_id", "mcp_servers", ["id"], unique=False)
    op.create_index("ix_mcp_servers_user_id", "mcp_servers", ["user_id"], unique=False)
    op.create_index("ix_mcp_servers_discovery_status", "mcp_servers", ["discovery_status"], unique=False)

    op.create_table(
        "mcp_discovered_tools",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("server_id", sa.Integer(), nullable=False),
        sa.Column("upstream_name", sa.String(), nullable=False),
        sa.Column("namespaced_id", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("input_schema", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["server_id"], ["mcp_servers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("server_id", "upstream_name", name="uq_mcp_tool_server_name"),
        sa.UniqueConstraint("namespaced_id"),
    )
    op.create_index("ix_mcp_discovered_tools_id", "mcp_discovered_tools", ["id"], unique=False)
    op.create_index("ix_mcp_discovered_tools_server_id", "mcp_discovered_tools", ["server_id"], unique=False)
    op.create_index("ix_mcp_discovered_tools_namespaced_id", "mcp_discovered_tools", ["namespaced_id"], unique=True)

    op.create_table(
        "mcp_credentials",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("server_id", sa.Integer(), nullable=False),
        sa.Column("encrypted_access_token", sa.Text(), nullable=False),
        sa.Column("encrypted_refresh_token", sa.Text(), nullable=True),
        sa.Column("token_type", sa.String(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("scopes", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["server_id"], ["mcp_servers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("server_id"),
    )
    op.create_index("ix_mcp_credentials_id", "mcp_credentials", ["id"], unique=False)
    op.create_index("ix_mcp_credentials_user_id", "mcp_credentials", ["user_id"], unique=False)
    op.create_index("ix_mcp_credentials_server_id", "mcp_credentials", ["server_id"], unique=True)

    with op.batch_alter_table("oauth_states") as batch_op:
        batch_op.add_column(sa.Column("mcp_server_id", sa.Integer(), nullable=True))
        batch_op.create_index("ix_oauth_states_mcp_server_id", ["mcp_server_id"], unique=False)
        batch_op.create_foreign_key(
            "fk_oauth_states_mcp_server_id", "mcp_servers", ["mcp_server_id"], ["id"], ondelete="CASCADE"
        )


def downgrade() -> None:
    """Remove the additive managed MCP registry schema."""
    with op.batch_alter_table("oauth_states") as batch_op:
        batch_op.drop_constraint("fk_oauth_states_mcp_server_id", type_="foreignkey")
        batch_op.drop_index("ix_oauth_states_mcp_server_id")
        batch_op.drop_column("mcp_server_id")
    op.drop_index("ix_mcp_credentials_server_id", table_name="mcp_credentials")
    op.drop_index("ix_mcp_credentials_user_id", table_name="mcp_credentials")
    op.drop_index("ix_mcp_credentials_id", table_name="mcp_credentials")
    op.drop_table("mcp_credentials")
    op.drop_index("ix_mcp_discovered_tools_namespaced_id", table_name="mcp_discovered_tools")
    op.drop_index("ix_mcp_discovered_tools_server_id", table_name="mcp_discovered_tools")
    op.drop_index("ix_mcp_discovered_tools_id", table_name="mcp_discovered_tools")
    op.drop_table("mcp_discovered_tools")
    op.drop_index("ix_mcp_servers_discovery_status", table_name="mcp_servers")
    op.drop_index("ix_mcp_servers_user_id", table_name="mcp_servers")
    op.drop_index("ix_mcp_servers_id", table_name="mcp_servers")
    op.drop_table("mcp_servers")
