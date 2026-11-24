"""Regression coverage for the MCP registry migration after the main-head merge."""

from pathlib import Path

import alembic.command
import alembic.config
import sqlalchemy


def test_mcp_registry_schema_upgrades_from_current_main_chain(tmp_path) -> None:
    """The MCP migration must follow the merged OAuth/WhatsApp head and produce one usable schema."""
    db_path = tmp_path / "mcp-registry.db"
    database_url = f"sqlite:///{db_path}"
    config = alembic.config.Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)

    alembic.command.upgrade(config, "head")

    engine = sqlalchemy.create_engine(database_url, connect_args={"check_same_thread": False})
    try:
        inspector = sqlalchemy.inspect(engine)
        assert {"mcp_servers", "mcp_discovered_tools", "mcp_credentials", "oauth_states"} <= set(
            inspector.get_table_names()
        )
        assert {"user_id", "provider_name", "endpoint", "transport", "enabled", "discovery_status"} <= {
            column["name"] for column in inspector.get_columns("mcp_servers")
        }
        assert {"server_id", "upstream_name", "namespaced_id", "input_schema", "enabled"} <= {
            column["name"] for column in inspector.get_columns("mcp_discovered_tools")
        }
        assert {"user_id", "server_id", "encrypted_access_token", "encrypted_refresh_token"} <= {
            column["name"] for column in inspector.get_columns("mcp_credentials")
        }
        assert "mcp_server_id" in {column["name"] for column in inspector.get_columns("oauth_states")}
    finally:
        engine.dispose()
