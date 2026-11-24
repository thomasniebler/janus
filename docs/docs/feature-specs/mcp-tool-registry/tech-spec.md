# MCP tool registry and remote HTTP connections

## Status

**Status:** Shipped
**Linear:** JAN-158, static bearer tokens JAN-342, chat runtime JAN-343
**Depends on:** unattended use of remote tools is open as JAN-344

Implemented in JAN-158; static bearer tokens added in JAN-342; offering
discovered tools to Janus in chat added in JAN-343 (see "Chat runtime"
below). This document is the implementation contract for the managed tool
registry plane and deliberately does not include the playground UI from
JAN-159. Unattended runs (triggers, automations, delegated children) do not
get remote tools yet (JAN-344).

## Scope and compatibility

Janus exposes one management registry containing code-owned built-in tool
groups and user-owned remote MCP connections. The managed registry is an
additive boundary that the task-scoped runtime consumes. (When this spec was
written it deliberately left the then-existing `AgentDefinition` access flags
and `build_tool_set()` path untouched; both were removed or reduced to a test
helper in JAN-227.) Built-in groups also carry the per-user "Unattended: Allowed / Off"
switch (JAN-229, JAN-255; `unattended_allowed`, `locked_reason`, and related
fields), shown on the same Tools & Connections panel.

Remote execution is limited to authenticated HTTP MCP discovery and tool
invocation. Stdio, local-process servers, arbitrary proxying, and provider-
specific OAuth assumptions are out of scope.

## Registry contract

Built-ins expose stable IDs, display metadata, operation, risk, confirmation,
required capabilities, and a documentation reference through the existing
`ToolRegistry`. Remote tools use stable IDs in the form
`mcp.<server-id>.<tool-name>` and retain the upstream tool name separately.
Remote descriptions and input schemas are advisory metadata; they never grant
Janus permissions or action approval.

The effective remote tool set is the intersection of the authenticated
owner's connection, source enabled state, discovered tool enabled state, and
the caller's task/runtime policy. Registry selection is not consequential
action confirmation.

## Persistence and ownership

The database stores three owner-scoped records:

1. an MCP server connection with provider name, endpoint, HTTP transport,
   enabled state, discovery/health status, OAuth configuration, and redacted
   error state;
2. discovered tools with upstream name, namespaced ID, description, input
   schema, and enabled state; and
3. a credential connection with an encrypted access token and, for OAuth,
   an encrypted refresh token, token expiry, token type, and scopes. A static
   bearer token (JAN-342) uses the same row with `token_type = "static"` and
   no refresh token, expiry, or scopes; that marker is what tells it apart
   from an OAuth row (whose `token_type` is the provider's, usually `Bearer`).

Every read, mutation, discovery, credential operation, and remote invocation
resolves the server through the current user's `user_id`. Secrets are omitted
from ORM serialization, response schemas, UI text, events, and logs.

## HTTP MCP boundary

Discovery and invocation use bounded `httpx` timeouts, no automatic redirects,
bounded response bodies, and JSON-RPC MCP `initialize`/`notifications/initialized`
then `tools/list` or `tools/call` messages. The client preserves an MCP session
identifier when the server supplies one. JSON responses are supported for the
first slice; malformed, unsupported, unauthorized, timeout, and upstream
errors become safe connection status/error values rather than exceptions that
leak response content.

User-configured endpoints must be absolute HTTP(S) URLs without credentials,
fragments, or query strings. HTTPS is required for non-loopback hosts; plain
HTTP is allowed only for loopback development endpoints. DNS results are
checked and private, link-local, multicast, reserved, and unspecified
addresses are rejected, while loopback is permitted for local prototypes.

## OAuth PKCE lifecycle

OAuth setup is an authorization-code flow with S256 PKCE. The server's
authorization endpoint, token endpoint, client ID, and scopes are entered as
connection metadata; Janus constructs one exact callback URI from the
configured public base URL. A short-lived, owner-scoped state row stores the
server, user, and verifier. The callback consumes state once, validates the
owner relationship, exchanges the code with a bounded timeout, and stores
tokens encrypted with the existing Fernet key. Access tokens are refreshed
before discovery/invocation when an encrypted refresh token and expiry are
available. Disconnect deletes credentials and the pending OAuth state.

OAuth callback errors redirect to the Settings tool-registry surface with a
safe error code. Token values and provider response bodies are never returned
to the browser.

## Static bearer tokens (JAN-342)

The rules live in `services/mcp_static_token.py`; `mcp_service.py` calls
them. `MCPServerCreate.access_token` and `MCPServerUpdate.access_token` (a
`SecretStr`, at most 8,192 characters) accept an owner-supplied bearer token;
`MCPServerUpdate.clear_access_token` removes it. Both in one request is a
`422`, and so is clearing on a server whose credential is OAuth (disconnect it
instead). The service trims the value, drops a pasted `Bearer ` prefix, treats a
blank value as "no token", and refuses anything but one line of printable
ASCII without spaces, so the value cannot break the `Authorization` header. It
is Fernet-encrypted into the server's credential row (replacing an OAuth row
if there was one) and never returned; events record only "saved"/"removed".

- **Exclusive with OAuth.** A token cannot be saved while the server is
  OAuth-configured (authorization endpoint, token endpoint, or client ID set,
  the fields behind `oauth_configured`; scopes alone do not count; clear them
  in the same update to switch), those fields cannot be set while a token is
  saved, and OAuth start/callback refuse a server with a token. A token may
  replace an OAuth credential row whose OAuth settings were already removed.
- **Bound to the endpoint.** An update that changes the endpoint drops the
  saved token unless the same update brings a new one.
- **Use.** Discovery, refresh, and `tools/call` send it exactly like an OAuth
  access token (`Authorization: Bearer <token>`); it is never refreshed.
- **Rejection.** An upstream `401` or `403` to a static token becomes "Remote
  MCP server rejected the bearer token. Save a new token for this server."
  (`mcp_static_token.translate`, applied by `MCPService._remote` to every call
  that reaches the server). Discovery and a tool call both record it through
  `mcp_status.mark_failed`: `discovery_status=auth_required`, unhealthy, that
  `last_error`; discovery answers `401` with that detail. `oauth_required`
  stays `false` for such a server, so the UI asks for a new token, not an
  OAuth sign-in.
- **Status reset.** `mcp_status.reset_discovery` forgets the last outcome:
  always after an endpoint change, and after a token is saved or removed when
  the last contact failed (`auth_required` or `error`), so a good token clears
  a stale error. A healthy status survives a token rotation.
- **Response.** `MCPServerResponse.credential_status` is `"none"`,
  `"oauth"`, or `"static"`; `credential_configured` is kept.
- **Validation errors.** The app's `RequestValidationError` handler omits each
  error's `input`, so a `422` never echoes a token (or any other submitted
  secret) back.

## Chat runtime (JAN-343)

- **Registry.** `agent_tools.build_managed_registry(db, user)` returns
  `DEFAULT_TOOL_REGISTRY` plus one entry per tool
  `agent_toolboxes/remote_mcp.offered_tools` returns: an enabled, unflagged
  (or owner-accepted) tool of the user's enabled server whose
  `discovery_status` is `ready`. Chat
  (`websocket/chat.py`) rebuilds it per message, so Settings changes apply
  from the next message. It also widens the chat allowlist by those `mcp.*`
  ids (`remote_mcp.chat_allowed_tool_ids`). Every other path keeps
  `DEFAULT_TOOL_REGISTRY`, which has no remote entries.
- **Tool.** The tool is named `mcp_<server id>_<upstream name>` (provider-safe,
  at most 64 characters, deduplicated) and carries the upstream description,
  prefixed with the provider name. Its args schema comes from
  `remote_mcp.args_model_from_schema`:
  - Object properties map to pydantic fields: scalars, scalar enums
    (`Literal`), arrays, `X | null`, and nested objects as `dict`, with
    `required`, scalar defaults, and descriptions.
  - `$ref`, `oneOf`/`allOf`, multi-type unions, unusable property names (not identifiers, pydantic attributes, or names LangChain reserves on a tool call such as `callbacks`, `run_manager`, `config`, `tool_call_id`),
    objects without declared properties, and schemas with more than 64
    properties fall back to one free-form `arguments: dict`.
  - Models are cached per schema. Extra keys are allowed unless the schema
    sets `additionalProperties: false` (JSON Schema's default).
  - The tool advertises the converted model's JSON schema
    (`model_json_schema()`), not the raw upstream dict. That keeps only
    keywords Janus understands, with property descriptions cut at 500
    characters. Being a JSON-schema dict rather than the pydantic model,
    arguments reach the approval card and the upstream call exactly as the
    model supplied them: omitted optional arguments are not sent, explicit
    `null`s are forwarded, and no defaults are filled in.
  - Arguments are validated against the model when the tool runs, that is
    after any approval. A call missing a required argument still shows an
    approval card; the validation error appears only once it is approved, and
    nothing is sent upstream. Validating before the card is JAN-351.
- **Untrusted metadata (tool poisoning).** `services/mcp_metadata.py` keeps
  server-written metadata bounded and scanned at discovery:
  - Descriptions are cut at 2,000 characters, and input schemas over 20,000
    characters of JSON are stored as `{}`, which means the free-form tool.
  - `apply_discovered` scans the description plus every schema
    description/title with the prompt-injection rules. A match stores
    `mcp_discovered_tools.description_scan` (rules, text hash,
    `acknowledged: false`) and disables the tool.
  - `offerable` keeps flagged, unacknowledged tools out of chat. Switching
    the tool on (`set_tool_enabled`) acknowledges that exact text, and a
    rediscovery with a different text flags it again.
  - The registry API reports `injection_suspected` and
    `injection_acknowledged`, which Settings shows.
  - The approval card's reason is `ToolRegistryEntry.approval_reason`,
    "Remote MCP tool <provider>: <name>.", with the description only as
    quoted untrusted text.
- **Annotations.** Discovery stores the server's boolean hint annotations
  (`readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`) in
  `mcp_discovered_tools.annotations`, a nullable JSON column (migration
  `e6b2d8f4a1c3`, which also adds `description_scan`). Anything else is
  dropped (`services/mcp_metadata.py`).
  - An exact `readOnlyHint: true` makes the entry `operation="read"`,
    `confirmation="never"`.
  - Every other tool is `confirmation="required"`: a per-call approval card
    with the full arguments, refused after a suspected prompt injection, and
    with an *In chat* switch (`PUT /api/tools/policies/{id}` accepts remote
    ids).
  - The hint is the server's claim: an owner who enabled the server trusts it
    that far, and can switch any tool off.
- **Untrusted.** Every remote entry is `reads_untrusted_content`, so its
  output is spotlighted and scanned like `web_fetch`. After a suspected
  injection, `runtime_adapter._escalates_on_injection` refuses every remote
  tool for the rest of the step, read-only ones included, because each call
  sends model-chosen arguments to a third party.
- **Chat only.** Remote entries are `enabled_when` only in a `chat` step
  (`disabled_by_context` elsewhere). `tool_policy.locked_reason` locks every
  `mcp.*` id for unattended runs (`REMOTE_MCP_LOCK_REASON`; never offered,
  never delegable, the switch answers `422`), and trigger definitions only
  accept built-in ids.
- **API.** `GET`/`PUT /api/tools/policies` use
  `agent_tools.build_policy_registry`, which covers every discovered tool
  whatever its server's state. A switch on a tool whose server is disabled
  or failed discovery can therefore still be listed and reset. Remote tools
  are locked for unattended runs. `MCPService.disconnect` and rediscovery
  purge the `tool_policies` rows of removed tools
  (`mcp_metadata.purge_tool_policies`), because SQLite may reuse a server
  id. Each tool in `/api/tools/registry` carries `read_only`,
  `confirmation`, and the scan state.

## API and UI

Authenticated `/api/tools` management routes expose the built-in registry,
owner's remote servers, server creation/update/discovery/disconnect, OAuth
start/callback, and per-tool enable/disable. Mutations retain Janus's existing
cookie-session CSRF protection. The Settings page adds a Tools and Connections
surface with built-in metadata, remote connection lifecycle controls,
discovery health, per-tool enablement, and explicit messaging that enablement
does not approve actions.

## Validation and auditability

Discovery refresh replaces the server's discovered tool snapshot in one
transaction while preserving enablement for unchanged namespaced tools.
Failures retain the last successful tool snapshot and set a redacted error;
discovery health is tracked separately from the user-controlled server and
tool enablement state. Events record connect, disconnect, discovery,
enable/disable, OAuth failure, and policy-denial actions with server/tool
identifiers only.

Tests cover owner isolation, stable namespacing, encrypted credential storage
and redaction, static bearer tokens (`tests/test_mcp_static_token.py`: storage,
replace/clear, OAuth exclusivity, endpoint binding, headers, 401 mapping, no
echo), PKCE state/callback behavior, token refresh, endpoint/SSRF
validation, timeout/error handling, discovery persistence, API contracts,
migrations, and frontend registry lifecycle states.
