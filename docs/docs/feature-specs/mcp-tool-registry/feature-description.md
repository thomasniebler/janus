# MCP tool registry

Janus's Tools and Connections settings surface lets you inspect the built-in
tool groups and manage remote MCP servers that belong to your account.

## What you can do

- Review built-in tools, their capabilities, risk, and confirmation policy.
- Register an HTTP MCP endpoint with a provider name.
- Discover the server's tools and refresh their descriptions and input schemas.
- Enable or disable a server and each discovered tool independently.
- Complete OAuth authorization in the provider's browser flow when discovery
  requires it, without entering or viewing tokens in Janus.
- Or, for a server that hands out a fixed access token instead of OAuth (common
  for self-hosted and local MCP servers), paste that **bearer token** when
  adding the server or later in the server's details. You can replace or remove
  it at any time; Janus never shows it again (JAN-342).
- Disconnect a server, which removes its stored connection credentials and
  discovered metadata.

Remote servers must use HTTPS except for loopback development endpoints.
Janus rejects endpoints that resolve to private or special-use networks and
uses bounded connection and response timeouts. A server's own tool metadata
cannot grant it additional Janus permissions.

Janus can use the enabled tools of an enabled server whose discovery
succeeded, in chat only (JAN-343). Each tool's arguments come from the input
schema the server published. A tool the server marks read-only runs without
asking. Every other call waits for your approval of its exact arguments.
Enabling a server or tool therefore does not approve a write, send, delete, or
other consequential action. Everything a remote tool returns is treated as
untrusted content. Triggers, automations, and delegated tasks do not use
remote tools yet (JAN-344).

## Security and privacy

OAuth access and refresh tokens are encrypted at rest, scoped to the account
that connected the server, refreshed when possible, and deleted on disconnect.
Tokens never appear in normal API responses, the settings UI, logs, events, or
model context. OAuth setup uses a short-lived authorization state and PKCE.

A bearer token is stored the same way (encrypted, owner-scoped, deleted on
disconnect) and is never refreshed. A server uses either OAuth or a bearer
token, not both. Changing a server's endpoint drops its saved token unless you
enter a new one with the change, so a token is only ever sent to the endpoint
it was given for. When the server rejects the token, the server shows *Token
rejected* and asks for a new token instead of an OAuth sign-in.

This first remote slice supports authenticated HTTP MCP only. Stdio and
local-process MCP servers are not supported through the settings UI.

See the [technical specification](tech-spec.md) for the API, persistence,
transport, OAuth, and policy boundaries.
