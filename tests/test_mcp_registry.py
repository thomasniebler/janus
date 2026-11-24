"""Regression coverage for the managed MCP registry and HTTP boundary."""

from __future__ import annotations

import base64
import json
from hashlib import sha256
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from janus import agent_tools, models, security
from janus.runtime_adapter import RuntimeAdapter
from janus.services import mcp_service, mcp_status


def _user(db, username: str, *, admin: bool = False) -> models.User:
    """Create one persisted owner for registry tests."""
    user = models.User(
        username=username,
        hashed_password=security.security_service.get_password_hash("secret"),
        is_admin=admin,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _allow_loopback(monkeypatch):
    """Keep endpoint tests independent from DNS availability."""
    monkeypatch.setattr(mcp_service, "_resolved_addresses", lambda *_args: ["127.0.0.1"])


def test_endpoint_validation_blocks_ssrf_and_plaintext_public_hosts(monkeypatch):
    """Private destinations and non-TLS public HTTP endpoints are rejected."""
    monkeypatch.setattr(mcp_service, "_resolved_addresses", lambda *_args: ["10.0.0.8"])
    with pytest.raises(mcp_service.MCPServiceError, match="blocked network"):
        mcp_service.validate_mcp_endpoint("https://mcp.example.com/mcp")

    monkeypatch.setattr(mcp_service, "_resolved_addresses", lambda *_args: ["93.184.216.34"])
    with pytest.raises(mcp_service.MCPServiceError, match="use HTTPS"):
        mcp_service.validate_mcp_endpoint("http://mcp.example.com/mcp")


def test_endpoint_validation_allows_loopback_only_for_local_http(monkeypatch):
    """Local prototypes can use loopback HTTP while URLs stay normalized."""
    _allow_loopback(monkeypatch)
    with pytest.raises(mcp_service.MCPServiceError, match="query"):
        mcp_service.validate_mcp_endpoint("http://localhost:8787/mcp?bad=1")
    assert mcp_service.validate_mcp_endpoint("http://localhost:8787/mcp") == "http://localhost:8787/mcp"


def test_oauth_pkce_is_owner_scoped_and_credentials_are_encrypted(db, monkeypatch):
    """PKCE state and token storage cannot cross owners or expose token text."""
    _allow_loopback(monkeypatch)
    owner = _user(db, "mcp-owner")
    service = mcp_service.MCPService(db)
    server = service.create_server(
        owner,
        {
            "provider_name": "Example MCP",
            "endpoint": "http://127.0.0.1:8787/mcp",
            "oauth_authorization_endpoint": "http://127.0.0.1:8787/oauth/authorize",
            "oauth_token_endpoint": "http://127.0.0.1:8787/oauth/token",
            "oauth_client_id": "janus-client",
            "oauth_scopes": "tools.read",
        },
    )
    authorization_url = service.start_oauth(owner.id, server.id)
    params = parse_qs(urlsplit(authorization_url).query)
    verifier = db.query(models.OAuthState).one().code_verifier
    assert params["code_challenge_method"] == ["S256"]
    assert "access_token" not in authorization_url

    expected_challenge = base64.urlsafe_b64encode(sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert params["code_challenge"] == [expected_challenge]
    monkeypatch.setattr(
        service,
        "_exchange_token",
        lambda *_args: {"access_token": "secret-access", "refresh_token": "secret-refresh", "expires_in": 3600},
    )
    service.complete_oauth(params["state"][0], "authorization-code")
    credential = db.query(models.MCPCredential).one()
    assert credential.encrypted_access_token != "secret-access"
    assert security.security_service.decrypt(credential.encrypted_access_token) == "secret-access"
    assert "secret-access" not in str(credential)
    credential.expires_at = mcp_service.time_utils.utcnow()
    db.commit()
    monkeypatch.setattr(
        service,
        "_exchange_token",
        lambda *_args: {"access_token": "refreshed-access", "refresh_token": "refreshed-refresh", "expires_in": 3600},
    )
    assert service._access_token(server) == "refreshed-access"


def test_discovery_persists_namespaced_tools_and_preserves_enablement(db, monkeypatch):
    """Refreshes keep unchanged tool controls while replacing stale metadata."""
    _allow_loopback(monkeypatch)
    owner = _user(db, "mcp-discovery")
    service = mcp_service.MCPService(db)
    server = service.create_server(owner, {"provider_name": "Example", "endpoint": "http://127.0.0.1:8787/mcp"})
    monkeypatch.setattr(
        mcp_service.MCPHTTPClient,
        "discover_tools",
        lambda _client: [
            {"upstream_name": "search", "description": "Search", "input_schema": {"type": "object"}},
            {"upstream_name": "create", "description": "Create", "input_schema": {}},
        ],
    )
    refreshed = service.discover(owner.id, server.id)
    assert {tool.namespaced_id for tool in refreshed.discovered_tools} == {"mcp.1.search", "mcp.1.create"}
    search = next(tool for tool in refreshed.discovered_tools if tool.upstream_name == "search")
    service.set_tool_enabled(owner.id, server.id, search.id, False)
    monkeypatch.setattr(
        mcp_service.MCPHTTPClient,
        "discover_tools",
        lambda _client: [{"upstream_name": "search", "description": "Updated", "input_schema": {}}],
    )
    refreshed = service.discover(owner.id, server.id)
    assert len(refreshed.discovered_tools) == 1
    assert refreshed.discovered_tools[0].enabled is False
    assert refreshed.discovered_tools[0].description == "Updated"


def test_mcp_json_response_rejects_oversized_and_malformed_payloads():
    """The HTTP boundary bounds response size and never accepts arbitrary JSON."""
    response = httpx.Response(200, content=b"not-json", headers={"content-type": "application/json"})
    with pytest.raises(mcp_service.MCPServiceError, match="invalid JSON"):
        mcp_service._json_response(response)

    oversized = httpx.Response(200, content=b"x" * (mcp_service.MCP_MAX_RESPONSE_BYTES + 1))
    with pytest.raises(mcp_service.MCPServiceError, match="too large"):
        mcp_service._json_response(oversized)


def test_mcp_timeout_status_is_safe():
    """Transport timeouts become stable redacted status text."""
    assert mcp_status.safe_error(httpx.ReadTimeout("provider timed out")) == "Remote MCP server timed out."


def test_http_mcp_discovery_uses_json_rpc_session_and_bounded_client(monkeypatch):
    """The HTTP adapter performs the MCP handshake and carries its session ID."""
    requests = []

    def handler(request):
        requests.append(request)
        payload = json.loads(request.content)
        if payload["method"] == "initialize":
            return httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": payload["id"], "result": {}},
                headers={"mcp-session-id": "session-1"},
            )
        if payload["method"] == "notifications/initialized":
            return httpx.Response(202)
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": payload["id"],
                "result": {"tools": [{"name": "search", "description": "Search", "inputSchema": {}}]},
            },
        )

    transport = httpx.MockTransport(handler)
    real_client = httpx.Client
    monkeypatch.setattr(
        mcp_service.httpx,
        "Client",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )
    monkeypatch.setattr(mcp_service, "_resolved_addresses", lambda *_args: ["127.0.0.1"])
    tools = mcp_service.MCPHTTPClient("http://127.0.0.1/mcp").discover_tools()
    assert tools[0]["upstream_name"] == "search"
    assert requests[2].headers["mcp-session-id"] == "session-1"


def test_managed_registry_extends_builtins_without_changing_default_registry(db, monkeypatch):
    """The managed registry (chat's, JAN-343) adds remote entries; the default registry never contains any."""
    _allow_loopback(monkeypatch)
    owner = _user(db, "managed-registry")
    server = mcp_service.MCPService(db).create_server(
        owner,
        {"provider_name": "Remote", "endpoint": "http://127.0.0.1:8787/mcp", "enabled": True},
    )
    server.discovery_status = models.MCPDiscoveryStatus.READY
    db.add(
        models.MCPDiscoveredTool(
            server_id=server.id,
            upstream_name="search",
            namespaced_id=f"mcp.{server.id}.search",
            description="Remote search",
            input_schema={},
        )
    )
    db.commit()
    registry = agent_tools.build_managed_registry(db, owner)
    assert any(entry.id == f"mcp.{server.id}.search" for entry in registry.entries())
    prepared = RuntimeAdapter(db, owner, registry=registry).prepare_step(
        task_id="task-1",
        objective="search",
        requested_tool_ids=(f"mcp.{server.id}.search",),
        task_mode="chat",
        allowed_tool_ids=frozenset({f"mcp.{server.id}.search"}),
        granted_capabilities=frozenset({"mcp"}),
        confirmed_tool_ids=frozenset({f"mcp.{server.id}.search"}),
    )
    assert [entry.id for entry in prepared.selection.entries] == [f"mcp.{server.id}.search"]
    assert prepared.tools[0].name == f"mcp_{server.id}_search"
    assert not any(entry.id.startswith("mcp.") for entry in agent_tools.DEFAULT_TOOL_REGISTRY.entries())
    assert not any(tool.name.startswith("mcp_") for tool in agent_tools.build_tool_set(db, owner))
