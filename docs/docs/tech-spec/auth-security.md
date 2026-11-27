# Authentication and Security Model

## Sessions and tokens

- **JWT.** `security.SecurityService` signs HS256 tokens with `JANUS_SECRET_KEY` (startup refuses the placeholder). Lifetime is `ACCESS_TOKEN_EXPIRE_MINUTES` (default 10080, seven days). Claims: `sub` (username), `sid` (session id), `sg` (session generation) and `csrf` (the session's CSRF token).
- **Transport.** The web app gets an HTTP-only `access_token` cookie; the CLI and the iOS chat socket send `Authorization: Bearer <jwt>`. `security.get_token` reads either, through plain FastAPI `Header`/`Cookie` parameters, so the same dependency works for HTTP and WebSocket handshakes.
- **Revocation.** Logout stores the token's `sid` in `revoked_sessions`. Password change and admin "revoke sessions" increment `users.session_generation`, which invalidates every older token, and revoke the user's device tokens. Expired `revoked_sessions` rows are pruned opportunistically.
- **WebSockets.** `get_websocket_user_or_none` authenticates on its own short-lived database session and closes it before the socket opens, so an open tab never pins a pooled connection. `/ws/changes` re-validates the session before every heartbeat.

## Password hashing

`bcrypt` directly (`hashpw`/`checkpw`, cost 12, `$2b$`). A new password longer than 72 UTF-8 bytes is rejected (`422` from the schemas, `ValueError` from `get_password_hash` for other callers) rather than pre-hashed. `verify_password` truncates to 72 bytes to match hashes written by the older passlib stack. An invalid stored hash verifies as `False`.

## First-run setup

No default credentials exist. The first admin is created in one of two ways:

- **CLI:** `janus user bootstrap-admin` writes the admin directly (the path for hosted installs).
- **Browser:** with `JANUS_PUBLIC_SETUP_ENABLED` on, startup prints a one-time setup token; `GET /api/auth/setup` reports whether setup is needed, and `POST /api/auth/setup` creates the admin and invalidates the token. With the setting off, the setup endpoints answer `404` and the web app treats that as "no setup required".

## Abuse resistance

Login and setup throttle repeated failures per client IP and per submitted username. Failed and throttled attempts are written to the event log (`log_auth_throttle`). Throttling uses the client IP after trusted-proxy reconstruction.

## CSRF

`security.enforce_csrf_for_cookie_auth` runs on every router. A mutating request (not `GET`/`HEAD`/`OPTIONS`) that authenticates with the cookie must send `X-CSRF-Token` equal to the token's `csrf` claim, compared in constant time; otherwise `403`. Requests without the cookie (bearer clients) are exempt, as are login, logout, setup and Google sign-in (`CSRF_EXEMPT_PATHS`) and inbound webhooks (`CSRF_EXEMPT_PREFIXES`, authenticated by signature). The web app reads the token from `GET /api/auth/csrf`.

## Device tokens

Scripts and small devices that cannot hold a browser session use a device token (`services/device_token_service.py`, `device_tokens`):

- `POST /api/inbox/tokens` returns the plaintext `jdt_...` token once; only its SHA-256, a display prefix, `last_used_at` and `revoked_at` are stored. At most ten active tokens per user.
- Only `api/inbox.get_inbox_user` accepts it, and only when no valid web session is present. Every other route parses bearers as JWTs, so a device token gets `401` there.
- It can read the inbox and run `mark_read` or `dismiss`. Approvals and token management need the web session.
- Password change and session revocation revoke all device tokens; deleting a user deletes them.

## Proxy, host, origin and cookie policy

`TrustedProxyHeadersMiddleware` (`request_origin.py`) runs before routing:

- **Forwarded headers.** Trusted only for `JANUS_TRUSTED_PROXY_HOPS` hops (default 0). uvicorn's own proxy-header handling is off, so the middleware is the only authority. The reconstructed scheme and host drive cookie flags, OAuth callback URLs and redirects.
- **Host allowlist.** The effective `Host` must be in `JANUS_ALLOWED_HOSTS`; anything else is rejected before routing.
- **Origin allowlist.** `request_origin.browser_origins` (`ALLOWED_ORIGINS` plus the origin of `base_url`) drives CORS and WebSocket `Origin` checks. A WebSocket handshake with a missing or unknown origin is refused, which is why the iOS client sets `Origin` itself.
- **Cookies.** `HttpOnly`, and `Secure` whenever the effective scheme is HTTPS.
- **Headers.** Every response gets a Content-Security-Policy (`script-src 'self'`, `frame-ancestors 'none'`), `X-Content-Type-Options: nosniff`, frame protection and `Referrer-Policy: strict-origin-when-cross-origin`.

## Public surface

Closed by default: `/openapi.json`, `/apidocs` and `/apiredoc` exist only with `JANUS_PUBLIC_API_DOCS_ENABLED`; browser setup only with `JANUS_PUBLIC_SETUP_ENABLED`; `/api/docs/*` requires a login. Intentionally public routes: login, setup (when enabled), OAuth callbacks, `GET /api/status/health`, and `POST /api/webhooks/in/{key}`. Release validation checks that the docs and setup surfaces stay closed in production.

## Inbound webhooks

`POST /api/webhooks/in/{key}` (`services/webhook_service.py`) is public and authenticated by HMAC-SHA256 with the endpoint's secret:

- either a timestamped `X-Janus-Signature` (5-minute window) or GitHub's `X-Hub-Signature-256`, compared in constant time;
- the URL key is stored only as its SHA-256 and the secret Fernet-encrypted; both are shown once;
- JSON bodies up to 64 KB, at most 60 deliveries a minute per endpoint;
- a valid delivery emits `webhook.received` for the endpoint's owner.

## Tenant isolation

- **Service-layer ownership.** User-owned rows are read and changed through `*_for_user` methods that filter on the owner in the same query; a foreign id answers like a missing one. See [Backend and API](backend-api.md#ownership-rule).
- **Mail through its account.** An email belongs to its account's owner. Mail reads, the briefing's mail, and the mail tools start from `EmailService.query_emails_for_user`. `send_email` sends only from the owner's own active account.
- **Rules.** Rules, mail accounts and planner accounts always have an owner (`user_id NOT NULL`). Rule evaluation and rule learning touch only the mail owner's rules.
- **Derived context.** Reminders (owned through their source mail), the planner outlook, calendar and task tools, topic capture and IMPORTANT-mail notifications all follow the mail or account owner.
- **Event log.** `events.user_id` records whom an event concerns. Interactive admins see every row; everyone else, and every unattended run, sees only the owner's rows (see [LLM and agent safety](llm-agent-safety.md#event-log-and-trigger-history)).
- **User deletion.** `DELETE /api/auth/users/{username}` deletes owned rows that SQLite would not cascade (device tokens, webhook endpoints, remote A2A agents, the Telegram bot, character state) and turns the user's events into system rows, so a reused id inherits nothing.

## Secrets at rest

- Mail passwords, API keys, OAuth tokens, bot tokens and similar fields are Fernet-encrypted with the key at `JANUS_FERNET_KEY_PATH` (default `<data dir>/.janus_key`). The key file must move with the database.
- Settings responses return only "configured" booleans for secrets, never the values.
- A stored LLM key goes only to its own base URL: a model listing or connection test with a draft base URL and no draft key never sends the stored key there.

## Microsoft 365 sign-in

Outlook and Teams use delegated Microsoft Graph permissions through Janus's own Entra ID app (`JANUS_MICROSOFT_CLIENT_ID`, `JANUS_MICROSOFT_TENANT_ID`) and MSAL's `PublicClientApplication` (`services/microsoft_auth_service.py`). `janus microsoft login` runs the one-time interactive browser flow; every other call is silent, so background code never opens a browser and an expired sign-in is an error naming the login command. MSAL's token cache is Fernet-encrypted at `<data dir>/microsoft_token_cache.enc` (mode `0600`). The sign-in is one identity per instance, so the Microsoft tools are admin-only. Mailboxes connected through Settings → Connections use per-user OAuth instead (`services/mailbox_oauth_service.py`).

## Regex safety

User rules are compiled and matched with `re2` (linear time), and patterns are validated on save (`tests/test_redos.py`). The prompt-injection detector uses `re2` too.

## Dependency vulnerability audit

`scripts/dependency_audit.py` (`task audit:deps`) checks pinned dependencies for known vulnerabilities. The `Dependency audit` workflow runs it daily on `main` and from CI when a PR changes a manifest, a lockfile, the script or its exceptions. It reports and blocks; it never upgrades.

- **Python:** `uv export --frozen` per scope (production, dev group, and the `documents`, `whatsapp` and `desktop` extras), checked by a pinned `pip-audit` through `uvx`. Every Python finding blocks (the data has no severity).
- **npm:** `npm audit --package-lock-only` over the root, `frontend/` and `modules/todo-chat/frontend/` lockfiles. High and critical findings block.
- **Exceptions:** `[[tool.janus.dependency_audit.exceptions]]` in `pyproject.toml` with advisory id, package, reason and `expires`. An expired or unmatched entry fails the audit.
- A scanner that cannot run exits 2 and fails the job.

## Static security scan (Semgrep)

`scripts/semgrep_scan.py` (`task security:semgrep`) runs a pinned Semgrep with community rules from a pinned commit of `semgrep/semgrep-rules` (security directories for Python, FastAPI, JWT, SQLAlchemy, JS/TS, React and secrets) plus the Janus rules in `.semgrep/janus.yml`:

| Rule | Flags |
| --- | --- |
| `janus-route-without-auth-dependency` | an API route with no `get_current_user`/`admin_required` dependency |
| `janus-websocket-without-auth-dependency` | a WebSocket handler with no auth dependency |
| `janus-unscoped-owned-row-lookup` | `db.get(models.X, id)` on an owned model in routers, toolboxes or WebSocket handlers |

The scan first runs `semgrep --test` against `.semgrep/janus.py`. ERROR findings block; WARNING findings are reported. Accepted findings are `[[tool.janus.semgrep.exceptions]]` entries (rule, path, optional line text, reason, `expires`); an expired or unmatched entry fails the scan. The weekly `Semgrep` workflow scans `main`; CI calls it when a PR touches scanned source or the scan config.
