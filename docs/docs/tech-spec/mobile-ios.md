# Mobile iOS Architecture

`ios/JanusMobile` is a SwiftUI prototype for iOS 17+, generated with XcodeGen (`project.yml`). It is a thin client over the same REST API and `/ws/chat` the web app uses; it adds no backend endpoints.

## Modules

| File | Role |
| --- | --- |
| `JanusMobileApp.swift`, `ContentView.swift` | app entry, login screen or tabs (dashboard, chat), settings sheet |
| `AppSettings.swift` | the API base URL, persisted on device, and its validation |
| `APIClient.swift` | REST calls over `URLSession`: login, `me`, dashboard, logout |
| `SessionStore.swift` | signed-in state |
| `TokenStore.swift` | the JWT in the Keychain |
| `ChatStore.swift`, `ChatView.swift` | the chat socket and transcript |
| `Models.swift` | decoded response types |

## Configuration

The settings sheet holds only the API base URL (default `http://localhost:8080`). `AppSettings.isValidBaseURL` accepts `https://` for any host and `http://` only for `localhost`, `127.0.0.1` and `::1`, so credentials never travel in clear text to a remote host. A real device therefore needs an HTTPS endpoint in front of Janus. Changing the URL signs out and clears the stored token.

## REST session

`POST /api/auth/login` sets the HTTP-only session cookie, which `URLSession` stores and sends on later REST calls (`GET /api/auth/me`, `GET /api/dashboard/`, `POST /api/auth/logout`). The dashboard view is read-only. The login response body also carries the JWT; the client keeps it for the chat socket.

## Chat transport

- **Auth.** Cookie forwarding on a WebSocket handshake is unreliable, so `ChatStore` sends `Authorization: Bearer <jwt>`, which the backend accepts on any endpoint. The token is stored in the Keychain (`kSecAttrAccessibleAfterFirstUnlock`) and cleared on logout or a URL change.
- **Origin.** The backend refuses a WebSocket handshake whose `Origin` is neither the `base_url` origin nor in `ALLOWED_ORIGINS`, and `URLSessionWebSocketTask` sends none, so the client sets `Origin` to the scheme, host and port of its base URL. That origin is allowed when it matches the server's `base_url`; otherwise it must be listed in `ALLOWED_ORIGINS`.
- **Path.** `/ws/chat` is appended to the base URL's path, so a deployment under a prefix (`https://host/janus`) works.
- **Protocol.** The client does not request `?tool_events=1`. It reads plain reply text, `__CONVERSATION_ID__`, `__START__`, `__END__` and `__ERROR__`, and ignores the deprecated `__TOOL_CALL__`/`__TOOL_RESULT__` markers. It never receives tool events, turn steps, approval cards, page actions or UI blocks.
- **Consequence for approvals.** A tool that needs approval (`confirmation="required"`) is never run silently for this client: the backend answers the model `BLOCKED: ... this chat client cannot ask the user for approval` and stores `approval: "unavailable"`. Such actions need the web app.

## Not implemented

Conversation history and switching (one conversation at a time; compose starts a new one), tool-call display and approvals, page context, memory, tasks, planner, settings beyond the URL, and push notifications.
