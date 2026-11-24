"""Pydantic contracts for the managed MCP registry API."""

from datetime import datetime
from typing import Any, Literal

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, SecretStr, field_validator

#: Longest static bearer token accepted for a remote MCP server (JAN-342).
MAX_ACCESS_TOKEN_CHARS = 8192

#: How a server authenticates: no credential, OAuth tokens from the PKCE flow, or a static bearer token.
CredentialStatus = Literal["none", "oauth", "static"]


class MCPServerCreate(BaseModel):
    """Request to register one user-owned HTTP MCP server."""

    provider_name: str = Field(min_length=1, max_length=120)
    endpoint: str = Field(min_length=1, max_length=2048)
    transport: Literal["streamable_http"] = "streamable_http"
    enabled: bool = False
    oauth_authorization_endpoint: str | None = None
    oauth_token_endpoint: str | None = None
    oauth_client_id: str | None = None
    oauth_scopes: str | None = None
    #: Static bearer token (JAN-342): write-only, stored encrypted, never returned. Not combinable with OAuth.
    access_token: SecretStr | None = Field(default=None, max_length=MAX_ACCESS_TOKEN_CHARS)

    @field_validator("provider_name")
    @classmethod
    def normalize_provider_name(cls, value: str) -> str:
        """Reject blank provider names after trimming user input."""
        normalized = value.strip()
        if not normalized:
            raise ValueError("provider_name must not be blank")
        return normalized


class MCPServerUpdate(BaseModel):
    """Mutable settings for one MCP server; the bearer token is write-only."""

    provider_name: str | None = Field(default=None, min_length=1, max_length=120)
    endpoint: str | None = Field(default=None, min_length=1, max_length=2048)
    enabled: bool | None = None
    oauth_authorization_endpoint: str | None = None
    oauth_token_endpoint: str | None = None
    oauth_client_id: str | None = None
    oauth_scopes: str | None = None
    #: Replaces the static bearer token (JAN-342); write-only, never returned.
    access_token: SecretStr | None = Field(default=None, max_length=MAX_ACCESS_TOKEN_CHARS)
    #: Removes the static bearer token. Cannot be combined with ``access_token``.
    clear_access_token: bool = False


class MCPToolResponse(BaseModel):
    """Safe discovered-tool metadata; input schemas are untrusted advisory data."""

    id: int
    upstream_name: str
    namespaced_id: str
    description: str
    input_schema: dict[str, Any]
    enabled: bool
    #: The server marked the tool ``readOnlyHint: true``: chat runs it without asking (JAN-343).
    read_only: bool = False
    #: ``"never"`` for a read-only tool, else ``"required"`` (each call waits for approval in chat).
    confirmation: Literal["never", "required"] = "required"
    #: The server-written description or schema text matched a prompt-injection rule at discovery (JAN-343);
    #: chat offers the tool only after the owner switches it on again (``injection_acknowledged``).
    injection_suspected: bool = False
    injection_acknowledged: bool = False
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MCPServerResponse(BaseModel):
    """Safe remote-server response without credentials or token material."""

    id: int
    provider_name: str
    endpoint: str
    transport: str
    enabled: bool
    discovery_status: str
    last_health_status: str | None
    last_health_at: datetime | None
    last_error: str | None
    oauth_required: bool
    oauth_configured: bool
    credential_configured: bool
    #: ``"none"``, ``"oauth"`` (PKCE tokens), or ``"static"`` (an owner-supplied bearer token, JAN-342).
    credential_status: CredentialStatus
    tools: list[MCPToolResponse]
    created_at: datetime
    updated_at: datetime


#: Effective *In chat* value of a capability (JAN-306); ``"allowed"`` means it has no switch.
ChatModeValue = Literal["allowed", "ask", "always", "off"]


class MCPBuiltinResponse(BaseModel):
    """Stable built-in registry metadata for the management UI."""

    id: str
    display_name: str
    description: str
    required_capabilities: list[str]
    operation: str
    risk_level: str
    confirmation: str
    docs_ref: str | None
    source: Literal["builtin"] = "builtin"
    #: Whether the current user allows this capability in unattended runs
    #: (triggers, saved automations, delegated tasks), and the default (JAN-255).
    unattended_allowed: bool = False
    default_unattended_allowed: bool = False
    #: The *In chat* switch (JAN-306): ``"allowed"`` for an entry that is not
    #: ``confirmation="required"`` (no switch), else ``"ask"`` (per-call approval) or ``"off"`` (withheld from chat).
    chat_mode: ChatModeValue = "allowed"
    default_chat_mode: ChatModeValue = "allowed"
    #: Why the switch cannot be changed (an entry in ``tool_policy.LOCKED_UNATTENDED``), or ``None``.
    #: A locked capability is never available in unattended runs, so the
    #: trigger definition editor does not offer it.
    locked_reason: str | None = None
    #: What applies once the blanket modes (JAN-359) are taken into account: whether unattended runs may use it,
    #: and whether chat still shows an approval card for it.
    effective_unattended_allowed: bool = False
    effective_chat_asks: bool = False
    #: Tool names of this toolbox that triggers Janus schedules itself never get.
    withheld_unattended_tools: list[str] = []
    #: Whether the integrations this capability needs are connected for the user.
    connected: bool = True


class ToolPolicyUpdate(BaseModel):
    """Change one capability's switches; an omitted field is left unchanged, ``None`` resets it.

    ``unattended_allowed`` allows or turns off the capability in unattended
    runs. ``chat_mode`` (JAN-306) is the *In chat* switch of a
    ``confirmation="required"`` capability: ``"ask"`` (approve every call),
    ``"always"`` (run without asking, JAN-359), or ``"off"`` (withheld from chat).
    """

    unattended_allowed: bool | None = None
    chat_mode: Literal["ask", "always", "off"] | None = None


class ToolPolicyResponse(BaseModel):
    """Effective policy for one capability."""

    tool_id: str
    unattended_allowed: bool
    default_unattended_allowed: bool
    chat_mode: ChatModeValue
    default_chat_mode: ChatModeValue
    overridden: bool
    locked_reason: str | None = None
    effective_unattended_allowed: bool = False
    effective_chat_asks: bool = False
    withheld_unattended_tools: list[str] = []


ToolPolicyModeValue = Literal["per_tool", "allow_all"]


class ToolPolicyModes(BaseModel):
    """The user's blanket permission modes (JAN-359).

    ``unattended_mode = "allow_all"``: every switchable tool runs in unattended runs (plus image generation and
    remote MCP tools), whatever its own switch says. ``chat_mode = "allow_all"``: no approval card in chat;
    tools switched *Off in chat* stay withheld.
    """

    unattended_mode: ToolPolicyModeValue
    chat_mode: ToolPolicyModeValue


class ToolPolicyModesUpdate(BaseModel):
    """Change one or both blanket modes; an omitted field is left unchanged."""

    unattended_mode: ToolPolicyModeValue | None = None
    chat_mode: ToolPolicyModeValue | None = None


class InjectionClassifierStatus(BaseModel):
    """The optional model-based prompt-injection classifier's settings and this month's usage (JAN-289)."""

    enabled: bool
    model: str
    is_cloud: bool
    monthly_token_budget: int
    period: str
    tokens_used: int
    calls: int
    cached_verdicts: int


class InjectionClassifierUpdate(BaseModel):
    """Turns the classifier on or off and sets its monthly token budget."""

    enabled: bool | None = None
    monthly_token_budget: int | None = Field(default=None, ge=0, le=1_000_000_000)


class MCPRegistryResponse(BaseModel):
    """Combined built-in and owner-scoped remote registry response."""

    builtins: list[MCPBuiltinResponse]
    remote_servers: list[MCPServerResponse]


class MCPOAuthStartResponse(BaseModel):
    """Browser redirect target for a PKCE authorization flow."""

    authorization_url: AnyHttpUrl


class MCPDiscoveryResponse(BaseModel):
    """Safe result of a discovery attempt."""

    server: MCPServerResponse
    discovered_count: int


class MCPToolUpdate(BaseModel):
    """Request to change only one discovered tool's enabled state."""

    enabled: bool
