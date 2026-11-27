"""Azure (ARM) read-only integration: client shaping, credential wrapper, and admin/opt-in gating."""

from __future__ import annotations

import httpx
import pytest

from janus import config, models, security
from janus.agent_tools import build_tool_set
from janus.connectors.azure_arm import AzureArmClient, AzureError
from janus.services import azure_auth_service


def _client(handler) -> AzureArmClient:
    return AzureArmClient(lambda: "tok", transport=httpx.MockTransport(handler))


def test_lists_are_trimmed_and_use_bearer_token():
    """Subscriptions, groups and resources are slimmed down and sent with the bearer token."""
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/subscriptions":
            return httpx.Response(
                200, json={"value": [{"subscriptionId": "s1", "displayName": "Dev", "state": "Enabled", "x": 1}]}
            )
        if request.url.path.endswith("/resourcegroups"):
            return httpx.Response(200, json={"value": [{"name": "rg", "location": "westeurope"}]})
        return httpx.Response(
            200, json={"value": [{"id": "/i", "name": "vm", "type": "T", "location": "l", "tags": {"a": "b"}}]}
        )

    client = _client(handler)
    assert client.list_subscriptions() == [{"id": "s1", "name": "Dev", "state": "Enabled"}]
    assert client.list_resource_groups("s1") == [{"name": "rg", "location": "westeurope", "tags": {}}]
    resources = client.list_resources("s1", "my rg", top=10_000)

    assert resources[0]["name"] == "vm"
    assert seen[0].headers["Authorization"] == "Bearer tok"
    assert seen[2].url.raw_path.decode().startswith("/subscriptions/s1/resourceGroups/my%20rg/resources")
    assert seen[2].url.params["$top"] == "200"
    assert all(request.method == "GET" for request in seen)


def test_ids_are_quoted_and_blank_ids_rejected():
    """A hostile id stays one path segment, and a blank one never reaches the network."""
    paths = []

    def handler(request):
        paths.append(request.url.raw_path.decode().split("?")[0])
        return httpx.Response(200, json={"value": []})

    client = _client(handler)
    client.list_resource_groups("../x")
    assert paths == ["/subscriptions/..%2Fx/resourcegroups"]
    with pytest.raises(ValueError):
        client.list_resource_groups("  ")


@pytest.mark.parametrize("status", [401, 403])
def test_auth_errors_hint_at_az_login(status):
    """401/403 give an actionable one-liner."""
    client = _client(lambda r: httpx.Response(status, json={"error": {"code": "AuthorizationFailed", "message": "no"}}))
    with pytest.raises(AzureError) as excinfo:
        client.list_subscriptions()
    assert excinfo.value.status_code == status
    assert "az login" in str(excinfo.value)


def test_non_json_error_and_transport_error_are_not_echoed():
    """Raw bodies are not echoed, and transport failures become AzureError."""
    with pytest.raises(AzureError) as excinfo:
        _client(lambda r: httpx.Response(500, text="<html>secret</html>")).list_subscriptions()
    assert "secret" not in str(excinfo.value)

    def boom(request):
        raise httpx.ConnectError("x")

    with pytest.raises(AzureError) as excinfo:
        _client(boom).list_subscriptions()
    assert excinfo.value.status_code == 0


class _Token:
    token = "arm-token"


class _FakeCredential:
    def __init__(self, **kwargs):
        """Record constructor kwargs."""
        self.kwargs = kwargs

    def get_token(self, scope):
        """Return a fixed token."""
        assert scope == "https://management.azure.com/.default"
        return _Token()


def test_token_comes_from_default_azure_credential(monkeypatch):
    """The ARM token is requested from DefaultAzureCredential, without the browser credential."""
    created = []

    def factory(**kwargs):
        created.append(kwargs)
        return _FakeCredential(**kwargs)

    monkeypatch.setattr(azure_auth_service, "DefaultAzureCredential", factory)
    azure_auth_service.reset_credential()
    settings = config.Settings(JANUS_AZURE_ENABLED=True, JANUS_MICROSOFT_TENANT_ID="tenant-1")

    assert azure_auth_service.get_arm_token(settings) == "arm-token"
    assert azure_auth_service.get_arm_token(settings) == "arm-token"
    assert len(created) == 1
    assert created[0]["exclude_interactive_browser_credential"] is True
    assert created[0]["visual_studio_code_tenant_id"] == "tenant-1"
    azure_auth_service.reset_credential()


def test_disabled_and_missing_credential_raise_auth_error(monkeypatch):
    """Off by default; a failing credential chain becomes an actionable AzureAuthError."""
    with pytest.raises(azure_auth_service.AzureAuthError):
        azure_auth_service.get_arm_token(config.Settings(JANUS_AZURE_ENABLED=False))

    from azure.core.exceptions import ClientAuthenticationError

    class Failing:
        def get_token(self, scope):
            """Always fail."""
            raise ClientAuthenticationError("nope")

    monkeypatch.setattr(azure_auth_service, "DefaultAzureCredential", lambda **kw: Failing())
    azure_auth_service.reset_credential()
    with pytest.raises(azure_auth_service.AzureAuthError, match="az login"):
        azure_auth_service.get_arm_token(config.Settings(JANUS_AZURE_ENABLED=True))
    azure_auth_service.reset_credential()


def _user(db, name, *, admin):
    user = models.User(username=name, hashed_password=security.security_service.get_password_hash("s"), is_admin=admin)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def test_tools_require_admin_and_opt_in(db, monkeypatch):
    """Only an admin sees the Azure tools, and only when JANUS_AZURE_ENABLED is on."""
    names = {"azure_list_subscriptions", "azure_list_resource_groups", "azure_list_resources"}
    admin, other = _user(db, "owner", admin=True), _user(db, "colleague", admin=False)

    monkeypatch.setattr(azure_auth_service, "is_enabled", lambda settings=None: False)
    assert not ({t.name for t in build_tool_set(db, admin)} & names)

    monkeypatch.setattr(azure_auth_service, "is_enabled", lambda settings=None: True)
    assert {t.name for t in build_tool_set(db, admin)} >= names
    assert not ({t.name for t in build_tool_set(db, other)} & names)


def test_tool_maps_errors_and_closes_client(db, monkeypatch):
    """Azure errors become ValueError tool errors and the client is always closed."""
    monkeypatch.setattr(azure_auth_service, "is_enabled", lambda settings=None: True)
    closed = []

    class Fake:
        def list_subscriptions(self):
            """Fail like ARM would."""
            raise AzureError("denied", 403)

        def close(self):
            """Record the close."""
            closed.append(True)

    monkeypatch.setattr(azure_auth_service, "build_arm_client", lambda settings=None: Fake())
    tools = {t.name: t for t in build_tool_set(db, _user(db, "owner", admin=True))}

    with pytest.raises(ValueError, match="denied"):
        tools["azure_list_subscriptions"].invoke({})
    assert closed == [True]


def test_dot_segments_and_non_json_success_are_rejected():
    """Dot-segment ids never reach the network, and a non-JSON 200 becomes AzureError."""
    client = _client(lambda r: httpx.Response(200, text="<html>proxy</html>"))
    with pytest.raises(ValueError):
        client.list_resource_groups("..")
    with pytest.raises(AzureError):
        client.list_subscriptions()


def test_chat_offers_it_to_an_admin_only(db, monkeypatch):
    """Chat allowlists azure.resources; the admin-only gate still applies there."""
    from janus.capability_profile import user_connected_capabilities
    from janus.runtime_adapter import TASK_MODE_ALLOWED_TOOL_IDS, RuntimeAdapter

    monkeypatch.setattr(azure_auth_service, "is_enabled", lambda settings=None: True)
    assert "azure.resources" in TASK_MODE_ALLOWED_TOOL_IDS["chat"]

    def chat_tools(user):
        prepared = RuntimeAdapter(db, user).prepare_step(
            task_id="t",
            objective="list my Azure resources",
            requested_tool_ids=("azure.resources",),
            task_mode="chat",
            granted_capabilities=user_connected_capabilities(db, user),
        )
        return {tool.name for tool in prepared.tools}

    assert "azure_list_resources" in chat_tools(_user(db, "owner", admin=True))
    assert not chat_tools(_user(db, "colleague", admin=False))
