# Mobile iOS Architecture

## Scope

Janus provides a native iOS app scaffold alongside the web dashboard.

## Technology

- **Core:** SwiftUI for iOS 17+.
- **Networking:** `URLSession` for REST, `URLSessionWebSocketTask` for chat.
- **Configuration:** Runtime-configurable Janus API base URL persisted on-device.

## Requirements

- **Configurable backend URL:** Users can edit the Janus API base URL in-app so the same binary can target local, LAN, or hosted Janus servers.
- **Session-based auth for REST:** The iOS client authenticates REST calls through the existing `/api/auth/login` cookie session flow; `URLSession` forwards the resulting httponly cookie automatically.
- **Authenticated retrieval:** The client uses authenticated API routes for dashboard data retrieval.
- **Deployment compatibility:** The client must work with same-origin hosted deployments and explicit remote base URLs configured by the user.

## Chat transport and credential lifecycle

The Chat tab talks to the plan-driven single-agent runtime over `/ws/chat`
(see `janus/websocket/chat.py` and the agent runtime migration tracked in
#333), reusing the same text-marker streaming protocol the web dashboard's
"Chat with Janus" panel parses.
The client does not open the socket with `?tool_events=1`, so it never
receives the structured `__TOOL_EVENT__` frames (JAN-249) or the
`__PLAN_EVENT__` turn-step frames (JAN-316) and keeps
ignoring the deprecated `__TOOL_CALL__`/`__TOOL_RESULT__` markers; showing
tool calls on iOS (and dropping those markers server-side) is follow-up
work.
For the same reason it never receives the `__TOOL_APPROVAL__` approval
request (JAN-254) and cannot approve a `confirmation="required"` tool (send
mail, planner writes, GitHub, Linear, ...). The backend never runs such a
tool silently for it: the call answers the model `BLOCKED: <tool> was not run
because this chat client cannot ask the user for approval`, so Janus says
it could not do it, and the refusal is stored on the tool call
(`approval: "unavailable"`). Approving on iOS (an approval card answering
`{"type": "tool_approval", ...}`) is follow-up work; until then such actions
need the web UI.

- **Auth:** cookie forwarding on a WebSocket handshake isn't as dependable as
  on plain HTTP requests, so the client authenticates the socket separately
  with the raw JWT returned in the `/api/auth/login` response body (the same
  token the login cookie carries) via `Authorization: Bearer <token>` — the
  backend already accepts this on any endpoint (`janus.security.get_token`).
  The token is persisted in the iOS Keychain (`kSecAttrAccessibleAfterFirstUnlock`,
  see `TokenStore.swift`) so it survives app relaunches the same way the
  session cookie does, and is cleared on logout or when the configured
  backend URL changes.
- **Origin allow-listing:** `TrustedProxyHeadersMiddleware` rejects any
  WebSocket handshake whose `Origin` header isn't in the backend's
  `allowed_origins` setting; a browser supplies this automatically, but
  `URLSessionWebSocketTask` does not. The client sets `Origin` explicitly to
  the scheme/host/port of its configured API base URL. This origin must be
  allow-listed on the backend — normally already true for a deployment that
  serves its own web dashboard from that origin, per the `allowed_origins`
  guidance in `settings.toml`.
- **Path handling:** the WebSocket URL is built by appending `/ws/chat` to the
  configured base URL's existing path (not replacing it), so a deployment
  mounted under a prefix (e.g. `https://host/janus`) keeps working.
- **Scope:** one conversation is shown at a time; conversation history and
  delegated-task tracking, both present on the web dashboard, are not yet
  implemented on iOS.
