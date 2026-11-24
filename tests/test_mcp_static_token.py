"""Static bearer tokens for remote MCP servers (JAN-342). The upstream is always a mock transport."""

from __future__ import annotations

import json
from datetime import timedelta

import httpx
import pytest

from janus import models, security, time_utils
from janus.services import mcp_service, mcp_static_token

TOKEN = "s3cr3t-token-value"
ENDPOINT = "http://127.0.0.1:8787/mcp"


def _user(db, username: str = "token-owner") -> models.User:
    """Create one persisted owner."""
    user = models.User(username=username, hashed_password=security.security_service.get_password_hash("secret"))
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture(autouse=True)
def _loopback(monkeypatch):
    """Keep endpoint validation independent of DNS."""
    monkeypatch.setattr(mcp_service, "_resolved_addresses", lambda *_args: ["127.0.0.1"])


def _mock_upstream(monkeypatch, *, status_for_auth: dict[str, int] | None = None):
    """Route MCP HTTP calls to a fake server; return the recorded requests.

    ``status_for_auth`` maps an ``Authorization`` header value to the HTTP status to answer with.
    """
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        status = (status_for_auth or {}).get(request.headers.get("authorization", ""), 200)
        if status != 200:
            return httpx.Response(status)
        payload = json.loads(request.content)
        method = payload["method"]
        if method == "notifications/initialized":
            return httpx.Response(202)
        if method == "initialize":
            result: dict = {}
        elif method == "tools/list":
            result = {"tools": [{"name": "search", "description": "Search", "inputSchema": {}}]}
        else:
            result = {"content": [{"type": "text", "text": "found"}]}
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": payload["id"], "result": result})

    real_client = httpx.Client
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(mcp_service.httpx, "Client", lambda **kwargs: real_client(transport=transport, **kwargs))
    return requests


# --- token normalisation ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, None), ("", None), ("   ", None), (" abc ", "abc"), ("Bearer abc", "abc"), ("bearer  abc", "abc")],
)
def test_normalize_static_token(value, expected):
    """Blank means no token, surrounding space and a pasted ``Bearer`` prefix are dropped."""
    assert mcp_static_token.normalize_static_token(value) == expected


@pytest.mark.parametrize("value", ["two words", "line\nbreak", "tab\there", "ümlaut", "x" * 8193])
def test_normalize_static_token_rejects_header_breaking_values(value):
    """A token that could break or bloat the Authorization header is refused."""
    with pytest.raises(mcp_service.MCPServiceError):
        mcp_static_token.normalize_static_token(value)


# --- service --------------------------------------------------------------------------------------------------


def test_token_is_stored_encrypted_without_expiry_or_refresh(db):
    """The credential row holds only ciphertext and is marked static."""
    owner = _user(db)
    server = mcp_service.MCPService(db).create_server(
        owner, {"provider_name": "Local", "endpoint": ENDPOINT, "access_token": f"  {TOKEN} "}
    )
    credentials = server.credentials
    assert credentials.encrypted_access_token != TOKEN
    assert security.security_service.decrypt(credentials.encrypted_access_token) == TOKEN
    assert credentials.token_type == mcp_static_token.STATIC_TOKEN_TYPE
    assert credentials.encrypted_refresh_token is None
    assert credentials.expires_at is None
    assert mcp_static_token.credential_status(server) == "static"
    events = db.query(models.Event).filter(models.Event.source == "MCPRegistry").all()
    assert events and all(TOKEN not in (event.details or "") + event.message for event in events)


def test_token_and_oauth_are_mutually_exclusive(db):
    """OAuth settings and a static token cannot be combined on one server."""
    owner = _user(db)
    service = mcp_service.MCPService(db)
    oauth = {
        "oauth_authorization_endpoint": "http://127.0.0.1:9000/authorize",
        "oauth_token_endpoint": "http://127.0.0.1:9000/token",
        "oauth_client_id": "janus",
    }
    with pytest.raises(mcp_service.MCPServiceError, match="not both"):
        service.create_server(owner, {"provider_name": "X", "endpoint": ENDPOINT, "access_token": TOKEN, **oauth})

    static = service.create_server(owner, {"provider_name": "S", "endpoint": ENDPOINT, "access_token": TOKEN})
    with pytest.raises(mcp_service.MCPServiceError, match="not both"):
        service.update_server(owner.id, static.id, dict(oauth))
    with pytest.raises(mcp_service.MCPServiceError, match="bearer token"):
        service.start_oauth(owner.id, static.id)

    with_oauth = service.create_server(owner, {"provider_name": "O", "endpoint": ENDPOINT, **oauth})
    with pytest.raises(mcp_service.MCPServiceError, match="not both"):
        service.update_server(owner.id, with_oauth.id, {"access_token": TOKEN})
    # Switching to a token works when the OAuth settings are cleared in the same update.
    switched = service.update_server(owner.id, with_oauth.id, {"access_token": TOKEN, **dict.fromkeys(oauth)})
    assert mcp_static_token.credential_status(switched) == "static"


def test_replace_and_clear_token(db):
    """A new token replaces the old one; the clear flag removes it; both at once is refused."""
    owner = _user(db)
    service = mcp_service.MCPService(db)
    server = service.create_server(owner, {"provider_name": "S", "endpoint": ENDPOINT, "access_token": TOKEN})
    updated = service.update_server(owner.id, server.id, {"access_token": "new-token"})
    assert security.security_service.decrypt(updated.credentials.encrypted_access_token) == "new-token"
    assert db.query(models.MCPCredential).count() == 1

    with pytest.raises(mcp_service.MCPServiceError, match="not both"):
        service.update_server(owner.id, server.id, {"access_token": "x", "clear_access_token": True})

    cleared = service.update_server(owner.id, server.id, {"clear_access_token": True})
    assert cleared.credentials is None
    assert mcp_static_token.credential_status(cleared) == "none"
    assert db.query(models.MCPCredential).count() == 0


def test_replacing_an_oauth_credential_with_a_token(db):
    """An existing OAuth credential row is overwritten, not duplicated."""
    owner = _user(db)
    service = mcp_service.MCPService(db)
    server = service.create_server(owner, {"provider_name": "S", "endpoint": ENDPOINT})
    db.add(
        models.MCPCredential(
            user_id=owner.id,
            server_id=server.id,
            encrypted_access_token=security.security_service.encrypt("oauth-access"),
            encrypted_refresh_token=security.security_service.encrypt("oauth-refresh"),
            token_type="Bearer",
            expires_at=time_utils.utcnow() + timedelta(hours=1),
        )
    )
    db.commit()
    assert mcp_static_token.credential_status(service.get_server(owner.id, server.id)) == "oauth"
    updated = service.update_server(owner.id, server.id, {"access_token": TOKEN})
    assert mcp_static_token.credential_status(updated) == "static"
    assert updated.credentials.encrypted_refresh_token is None
    assert updated.credentials.expires_at is None
    assert db.query(models.MCPCredential).count() == 1


def test_endpoint_change_drops_the_token_unless_a_new_one_is_given(db):
    """A token is bound to its endpoint: moving the server elsewhere does not carry it along."""
    owner = _user(db)
    service = mcp_service.MCPService(db)
    server = service.create_server(owner, {"provider_name": "S", "endpoint": ENDPOINT, "access_token": TOKEN})
    kept = service.update_server(owner.id, server.id, {"provider_name": "Renamed", "endpoint": ENDPOINT})
    assert mcp_static_token.credential_status(kept) == "static"
    moved = service.update_server(owner.id, server.id, {"endpoint": "http://127.0.0.1:9999/mcp"})
    assert mcp_static_token.credential_status(moved) == "none"
    with_new = service.update_server(
        owner.id, server.id, {"endpoint": "http://127.0.0.1:8888/mcp", "access_token": "other"}
    )
    assert security.security_service.decrypt(with_new.credentials.encrypted_access_token) == "other"


def test_discovery_and_tool_calls_send_the_token(db, monkeypatch):
    """Every request of discovery and of a tool call carries ``Authorization: Bearer <token>``."""
    requests = _mock_upstream(monkeypatch)
    owner = _user(db)
    service = mcp_service.MCPService(db)
    server = service.create_server(
        owner, {"provider_name": "S", "endpoint": ENDPOINT, "access_token": TOKEN, "enabled": True}
    )
    discovered = service.discover(owner.id, server.id)
    assert [tool.upstream_name for tool in discovered.discovered_tools] == ["search"]
    result = service.call_tool(owner.id, server.id, f"mcp.{server.id}.search", {"q": "x"})
    assert result["content"][0]["text"] == "found"
    assert requests and all(request.headers["authorization"] == f"Bearer {TOKEN}" for request in requests)


def test_rejected_token_is_reported_without_an_oauth_prompt(db, monkeypatch):
    """A 401 to a static token says the token was rejected and does not point at OAuth."""
    _mock_upstream(monkeypatch, status_for_auth={f"Bearer {TOKEN}": 401})
    owner = _user(db)
    service = mcp_service.MCPService(db)
    server = service.create_server(
        owner, {"provider_name": "S", "endpoint": ENDPOINT, "access_token": TOKEN, "enabled": True}
    )
    with pytest.raises(mcp_service.MCPServiceError, match="rejected the bearer token") as raised:
        service.discover(owner.id, server.id)
    assert raised.value.auth_required
    failed = service.get_server(owner.id, server.id)
    assert failed.discovery_status == models.MCPDiscoveryStatus.AUTH_REQUIRED
    assert failed.last_error == mcp_static_token.STATIC_TOKEN_REJECTED

    db.add(
        models.MCPDiscoveredTool(server_id=server.id, upstream_name="search", namespaced_id=f"mcp.{server.id}.search")
    )
    db.commit()
    with pytest.raises(mcp_service.MCPServiceError, match="rejected the bearer token"):
        service.call_tool(owner.id, server.id, f"mcp.{server.id}.search", {})

    # A new token resets the failure so the owner can discover again.
    renewed = service.update_server(owner.id, server.id, {"access_token": "fresh"})
    assert renewed.discovery_status == models.MCPDiscoveryStatus.NEVER
    assert renewed.last_error is None


def test_a_401_without_a_token_keeps_the_generic_message(db, monkeypatch):
    """Without a static token the existing "requires authorization" status stays."""
    _mock_upstream(monkeypatch, status_for_auth={"": 401})
    owner = _user(db)
    service = mcp_service.MCPService(db)
    server = service.create_server(owner, {"provider_name": "S", "endpoint": ENDPOINT})
    with pytest.raises(mcp_service.MCPServiceError, match="requires authorization"):
        service.discover(owner.id, server.id)


# --- API ------------------------------------------------------------------------------------------------------


@pytest.fixture
def auth_client(client, db):
    """A cookie-authenticated client."""
    _user(db, "token-api")
    response = client.post("/api/auth/login", json={"username": "token-api", "password": "secret"})
    client.headers[security.CSRF_HEADER_NAME] = response.json()["csrf_token"]
    return client


def test_api_accepts_the_token_write_only(auth_client, db, monkeypatch):
    """Create, replace, and clear through the API; no response or listing ever contains the token."""
    _mock_upstream(monkeypatch, status_for_auth={f"Bearer {TOKEN}": 401})
    created = auth_client.post(
        "/api/tools/servers", json={"provider_name": "Local", "endpoint": ENDPOINT, "access_token": TOKEN}
    )
    assert created.status_code == 201
    body = created.json()
    assert body["credential_status"] == "static"
    assert body["credential_configured"] is True
    assert "access_token" not in body
    server_id = body["id"]

    rejected = auth_client.post(f"/api/tools/servers/{server_id}/discover")
    assert rejected.status_code == 401
    assert "rejected the bearer token" in rejected.json()["detail"]
    listed = auth_client.get("/api/tools/registry").json()["remote_servers"][0]
    assert listed["oauth_required"] is False  # a new token is needed, not an OAuth sign-in
    assert listed["discovery_status"] == "auth_required"

    replaced = auth_client.patch(f"/api/tools/servers/{server_id}", json={"access_token": "second-token"})
    assert replaced.status_code == 200
    assert replaced.json()["discovery_status"] == "never"
    cleared = auth_client.patch(f"/api/tools/servers/{server_id}", json={"clear_access_token": True})
    assert cleared.json()["credential_status"] == "none"

    for response in (created, rejected, replaced, cleared, auth_client.get("/api/tools/servers")):
        assert TOKEN not in response.text and "second-token" not in response.text


def test_api_validation_errors_never_echo_the_token(auth_client):
    """A 422 does not repeat the request body, so a token sent with a bad request is not echoed."""
    missing = auth_client.post("/api/tools/servers", json={"endpoint": ENDPOINT, "access_token": TOKEN})
    assert missing.status_code == 422
    assert TOKEN not in missing.text
    assert missing.json()["detail"][0]["loc"] == ["body", "provider_name"]
    too_long = auth_client.post(
        "/api/tools/servers", json={"provider_name": "X", "endpoint": ENDPOINT, "access_token": "y" * 9000}
    )
    assert too_long.status_code == 422
    assert "yyyy" not in too_long.text
    bad = auth_client.post(
        "/api/tools/servers", json={"provider_name": "X", "endpoint": ENDPOINT, "access_token": "has space"}
    )
    assert bad.status_code == 422
    assert "has space" not in bad.text


def test_api_refuses_token_with_oauth(auth_client):
    """The API answers 422 when OAuth settings and a token come together."""
    response = auth_client.post(
        "/api/tools/servers",
        json={
            "provider_name": "X",
            "endpoint": ENDPOINT,
            "access_token": TOKEN,
            "oauth_authorization_endpoint": "http://127.0.0.1:9000/authorize",
            "oauth_token_endpoint": "http://127.0.0.1:9000/token",
            "oauth_client_id": "janus",
        },
    )
    assert response.status_code == 422
    assert "not both" in response.json()["detail"]


# --- independent review fixes ---------------------------------------------------------------------------------


def test_scopes_alone_do_not_block_a_token(db):
    """Only the three fields behind ``oauth_configured`` make a server OAuth; scopes alone allow a token."""
    owner = _user(db)
    service = mcp_service.MCPService(db)
    server = service.create_server(
        owner, {"provider_name": "S", "endpoint": ENDPOINT, "oauth_scopes": "tools.read", "access_token": TOKEN}
    )
    assert mcp_static_token.credential_status(server) == "static"
    other = service.create_server(owner, {"provider_name": "T", "endpoint": ENDPOINT, "oauth_scopes": "tools.read"})
    updated = service.update_server(owner.id, other.id, {"access_token": TOKEN})
    assert mcp_static_token.credential_status(updated) == "static"


def test_clearing_an_oauth_credential_is_refused(db):
    """``clear_access_token`` on an OAuth credential is an error, not a silent no-op."""
    owner = _user(db)
    service = mcp_service.MCPService(db)
    server = service.create_server(owner, {"provider_name": "S", "endpoint": ENDPOINT})
    db.add(
        models.MCPCredential(
            user_id=owner.id,
            server_id=server.id,
            encrypted_access_token=security.security_service.encrypt("oauth-access"),
            token_type="Bearer",
        )
    )
    db.commit()
    with pytest.raises(mcp_service.MCPServiceError, match="Disconnect"):
        service.update_server(owner.id, server.id, {"clear_access_token": True})


def test_a_rejected_tool_call_is_persisted(db, monkeypatch):
    """A 401 on a tool call records the same failure state discovery does, so Settings stops saying Healthy."""
    _mock_upstream(monkeypatch, status_for_auth={f"Bearer {TOKEN}": 401})
    owner = _user(db)
    service = mcp_service.MCPService(db)
    server = service.create_server(
        owner, {"provider_name": "S", "endpoint": ENDPOINT, "access_token": TOKEN, "enabled": True}
    )
    server.discovery_status = models.MCPDiscoveryStatus.READY
    db.add(
        models.MCPDiscoveredTool(server_id=server.id, upstream_name="search", namespaced_id=f"mcp.{server.id}.search")
    )
    db.commit()
    with pytest.raises(mcp_service.MCPServiceError, match="rejected the bearer token"):
        service.call_tool(owner.id, server.id, f"mcp.{server.id}.search", {})
    db.expire_all()
    stored = service.get_server(owner.id, server.id)
    assert stored.discovery_status == models.MCPDiscoveryStatus.AUTH_REQUIRED
    assert stored.last_error == mcp_static_token.STATIC_TOKEN_REJECTED
    assert stored.last_health_status == "error"


def test_403_counts_as_a_rejected_token_and_a_new_token_clears_the_error(db, monkeypatch):
    """A 403 to a static token is "rejected"; saving a new token resets a failed status, including ``error``."""
    _mock_upstream(monkeypatch, status_for_auth={f"Bearer {TOKEN}": 403, "Bearer other": 500})
    owner = _user(db)
    service = mcp_service.MCPService(db)
    server = service.create_server(owner, {"provider_name": "S", "endpoint": ENDPOINT, "access_token": TOKEN})
    with pytest.raises(mcp_service.MCPServiceError, match="rejected the bearer token"):
        service.discover(owner.id, server.id)
    assert service.get_server(owner.id, server.id).discovery_status == models.MCPDiscoveryStatus.AUTH_REQUIRED

    service.update_server(owner.id, server.id, {"access_token": "other"})
    with pytest.raises(mcp_service.MCPServiceError, match="HTTP 500"):
        service.discover(owner.id, server.id)
    failed = service.get_server(owner.id, server.id)
    assert failed.discovery_status == models.MCPDiscoveryStatus.ERROR

    renewed = service.update_server(owner.id, server.id, {"access_token": "third"})
    assert renewed.discovery_status == models.MCPDiscoveryStatus.NEVER
    assert renewed.last_error is None


def test_a_credential_change_keeps_a_healthy_status(db, monkeypatch):
    """Replacing a working token does not throw away a successful discovery."""
    _mock_upstream(monkeypatch)
    owner = _user(db)
    service = mcp_service.MCPService(db)
    server = service.create_server(owner, {"provider_name": "S", "endpoint": ENDPOINT, "access_token": TOKEN})
    service.discover(owner.id, server.id)
    replaced = service.update_server(owner.id, server.id, {"access_token": "rotated"})
    assert replaced.discovery_status == models.MCPDiscoveryStatus.READY


def test_403_without_a_static_token_keeps_the_http_message(db, monkeypatch):
    """Only a static token turns a 403 into "token rejected"."""
    _mock_upstream(monkeypatch, status_for_auth={"": 403})
    owner = _user(db)
    service = mcp_service.MCPService(db)
    server = service.create_server(owner, {"provider_name": "S", "endpoint": ENDPOINT})
    with pytest.raises(mcp_service.MCPServiceError, match="HTTP 403"):
        service.discover(owner.id, server.id)
