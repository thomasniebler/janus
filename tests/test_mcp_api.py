"""API contract tests for authenticated MCP registry management."""

import pytest

from janus import models, security
from janus.services import mcp_service


@pytest.fixture
def auth_client(client, db):
    """Return a cookie-authenticated client with CSRF protection configured."""
    user = models.User(
        username="mcp-api-user",
        hashed_password=security.security_service.get_password_hash("secret"),
        is_admin=True,
    )
    db.add(user)
    db.commit()
    response = client.post("/api/auth/login", json={"username": user.username, "password": "secret"})
    assert response.status_code == 200
    client.headers[security.CSRF_HEADER_NAME] = response.json()["csrf_token"]
    return client


def test_registry_api_exposes_builtin_metadata_without_secrets(auth_client):
    """The stable management contract includes built-ins and no credential fields."""
    response = auth_client.get("/api/tools/registry")
    assert response.status_code == 200
    payload = response.json()
    assert payload["builtins"][0]["id"] == "core"
    assert payload["remote_servers"] == []
    assert "encrypted_access_token" not in response.text


def test_registry_api_locks_only_the_chat_only_toolboxes(auth_client):
    """Only the chat-only toolboxes lock (page and UI tools, images, automation and character proposals).

    ``self_evolution`` also shows a reason while its own switch (on its settings card) is off (JAN-320).
    """
    builtins = {entry["id"]: entry for entry in auth_client.get("/api/tools/registry").json()["builtins"]}
    locked = {tool_id for tool_id, entry in builtins.items() if entry["locked_reason"]}
    assert locked == {
        "triggers.propose",
        "ui_actions",
        "ui_blocks",
        "images",
        "ui_introspection",
        "character.propose",
        "self_evolution",
    }
    assert builtins["self_evolution"]["unattended_allowed"] is False
    assert builtins["events"]["unattended_allowed"] is True
    assert builtins["triggers"]["unattended_allowed"] is True
    assert builtins["core"]["unattended_allowed"] is True
    assert builtins["dashboard"]["withheld_unattended_tools"] == [
        "clear_dashboard_widget_prompt",
        "set_dashboard_widget_prompt",
    ]


def test_server_api_is_owner_scoped_and_discovery_is_safe(auth_client, db, monkeypatch):
    """CRUD, discovery, tool controls, and owner isolation use one contract."""
    monkeypatch.setattr(mcp_service, "_resolved_addresses", lambda *_args: ["127.0.0.1"])
    create = auth_client.post(
        "/api/tools/servers",
        json={"provider_name": "Local MCP", "endpoint": "http://127.0.0.1:8787/mcp"},
    )
    assert create.status_code == 201
    server = create.json()
    assert server["credential_configured"] is False
    assert "oauth_token_endpoint" not in create.text

    monkeypatch.setattr(
        mcp_service.MCPHTTPClient,
        "discover_tools",
        lambda _client: [{"upstream_name": "search", "description": "Find things", "input_schema": {}}],
    )
    discover = auth_client.post(f"/api/tools/servers/{server['id']}/discover")
    assert discover.status_code == 200
    tool = discover.json()["server"]["tools"][0]
    assert tool["namespaced_id"] == f"mcp.{server['id']}.search"

    toggle = auth_client.patch(
        f"/api/tools/servers/{server['id']}/tools/{tool['id']}",
        json={"enabled": False},
    )
    assert toggle.status_code == 200
    assert toggle.json()["enabled"] is False

    other = models.User(
        username="other-mcp-user",
        hashed_password=security.security_service.get_password_hash("secret"),
        is_admin=False,
    )
    db.add(other)
    db.commit()
    auth_client.post("/api/auth/logout")
    login = auth_client.post("/api/auth/login", json={"username": other.username, "password": "secret"})
    auth_client.headers[security.CSRF_HEADER_NAME] = login.json()["csrf_token"]
    assert auth_client.get(f"/api/tools/servers/{server['id']}").status_code == 404
