# Operations

How to keep Janus running, reach it from other machines, update it, back it up and watch it. For settings and paths, see [Configuration](configuration.md).

## Desktop mode and server mode

`janus start` runs in one of two modes:

| Mode | Start with | Behavior |
|---|---|---|
| Desktop (default) | `janus start` or `janus start --desktop-mode` | Sends desktop notifications for important mail through `notify-send`. `--tray` adds a tray icon (needs the `desktop` extra). |
| Server | `janus start --server-mode` or `server_mode = true` | No notifications and no tray icon, even with `--tray`. Use it on any headless machine. |

Both modes run the same API, web app and background workers. `--host` and `--port` override `host` and `port` for one run.

Janus runs one server process per set of runtime paths. A second `janus start` against the same paths refuses to start, because the SQLite database and the in-process workers must not be shared.

## Run Janus as a service

On Linux with systemd, install Janus as a user service:

```bash
janus service install      # write the unit, enable it at login, start it
janus service status       # systemctl --user status janus.service
janus service logs -f      # follow the journal; -n sets the number of lines (default 100)
janus service uninstall    # stop, disable and remove the unit
```

`install` writes `~/.config/systemd/user/janus.service`. The unit runs the same `janus` executable you ran `install` with, as `janus start`, and keeps:

- the working directory you ran it from, so a `.env` there is still read;
- your shell's `PATH`, so automatic updates find `uv` or `pipx`;
- `XDG_CONFIG_HOME`, `XDG_DATA_HOME` and `XDG_STATE_HOME`, so it uses the same settings and database.

It copies no other environment variables, so no secrets end up in the unit. It restarts Janus on failure after 10 seconds. `install --no-start` enables the unit without starting it.

The service starts when you log in. To start it at boot without a login, run `loginctl enable-linger $USER`.

The unit runs `janus start` without `--tray`. To add the tray or other flags, use a drop-in such as `~/.config/systemd/user/janus.service.d/override.conf`:

```ini
[Service]
ExecStart=
ExecStart=/path/to/janus start --tray
```

Then run `systemctl --user daemon-reload && systemctl --user restart janus`.

Without systemd, run `janus start` under any process supervisor (supervisord, runit, a container). The hosted reference install runs under supervisord.

## Behind a reverse proxy

For access from other machines, keep Janus on `127.0.0.1` and put an HTTPS reverse proxy in front of it. Then set:

```toml
server_mode = true
base_url = "https://janus.example.com"
allowed_hosts = ["janus.example.com"]
allowed_origins = ["https://janus.example.com"]
trusted_proxy_hops = 1
public_setup_enabled = false
```

| Setting | Why |
|---|---|
| `base_url` | OAuth redirect URIs and links use it. It must be the public address. |
| `allowed_hosts` | Janus answers 400 to any other `Host` header. |
| `allowed_origins` | The browser's chat connection is refused from any other origin. |
| `trusted_proxy_hops` | With `1`, Janus trusts the scheme, host and client address from the last proxy's `Forwarded` or `X-Forwarded-*` headers. Login cookies are then marked secure and rate limits see the real client. Leave it at `0` without a proxy, or clients could spoof those headers. |
| `public_setup_enabled` | Keep the browser setup screen off on a public server. Create the first admin with `janus user bootstrap-admin` over SSH. |

The proxy must forward WebSocket upgrades for `/ws/chat`, `/ws/briefing` and `/ws/changes`. Leave `public_api_docs_enabled` off unless you need the API docs.

Run `janus config doctor` after changing these. It catches a missing origin or host and a plain-HTTP public `base_url`.

## Updates

Janus updates itself from its GitHub releases. Every `auto_update_interval_minutes` (default 60) it checks for a newer `vX.Y.Z` release. To turn this off, set `auto_update_enabled = false`.

```bash
janus update --check   # install kind, latest release, and whether auto-update applies
janus update           # install the latest release now
```

Two kinds of install can update:

| Install | Requirements |
|---|---|
| `uv tool install` or `pipx install` of the release wheel | `uv` or `pipx` on the `PATH` of the running Janus. |
| Git checkout | No uncommitted changes, on `main` or at a release tag, `git` and `uv` installed, and Janus run from the checkout's own `.venv` (as `uv run janus start` does). |

Anything else, such as a checkout on a feature branch, is never updated. Janus logs the reason once at startup.

An update:

1. Downloads the release (the wheel, or the prebuilt web app for a checkout) and checks its SHA-256 digest.
2. Takes a database snapshot (see [Backups](#backups)).
3. Installs the release, keeping your optional extras and any packages you added next to Janus with `uv tool install --with` or `pipx inject`.
4. Checks that the new code imports and migrates the database.
5. Restarts Janus in place.

If a step fails, Janus puts the previous release back, restores the snapshot once migrations had started, and keeps running. If the new release does not start after the restart, Janus rolls back, restarts on the previous release and skips that release from then on. A later release installs normally, and `janus update` can still install the skipped one. Failures appear in **System Events** with source `AutoUpdate`.

The automatic restart interrupts running chats and trigger runs, as any restart does.

`janus update` restarts the `janus` service when it runs. Otherwise restart Janus yourself.

## Backups

Everything Janus keeps lives in two places:

- `~/.local/share/janus/`: the database, the encryption key (`.janus_key`), generated images, WhatsApp sessions (`whatsapp_sessions/`) and the Microsoft token cache.
- `~/.config/janus/settings.toml`: your instance settings.

Back up both. The database is useless without the key: stored passwords and API keys are encrypted with it.

To copy a running SQLite database safely, use SQLite's backup command instead of copying the file:

```bash
sqlite3 ~/.local/share/janus/janus.db ".backup '/backups/janus-$(date +%F).db'"
```

Or stop Janus and copy the whole directory. For PostgreSQL, use `pg_dump`.

### Snapshot before an update

Before each update, Janus copies the SQLite database to `janus.pre-release.db` next to `janus.db`. There is only one snapshot; each update replaces it. PostgreSQL databases get no snapshot.

To restore it by hand:

1. Stop Janus.
2. Move `janus.db` aside together with its `janus.db-wal` and `janus.db-shm` files. A leftover `-wal` file would be replayed onto the restored database and corrupt it.
3. Copy `janus.pre-release.db` to `janus.db`.
4. Start the release that matches the snapshot. A newer release would migrate it again.

Everything written after the snapshot is lost.

## Monitoring

### In the app

| Page | Shows | Who |
|---|---|---|
| **Settings → System Status** | Each service (Mail Sync, Planner Sync, WhatsApp Sync, LLM, Knowledge search) with its state and an overall summary. | Every user |
| **Settings → Stats** | Your mail, trigger runs, memory, messaging and model token usage over 7, 30 or 90 days. | Every user, own data |
| **Settings → System Events** | The event log of background work, filterable by type, source and time. | Every user |
| **Settings → Error Groups** | Repeated errors grouped by cause. | Admins |
| **Agent Tasks** (`/tasks`) | Every trigger, automation and delegated run with its steps and tool calls. | Every user, own runs |

### Status API

| Endpoint | Auth | Returns |
|---|---|---|
| `GET /api/status/health` | None | `{"status": "ok", "version": …}` with 200, or `"degraded"` and the stale accounts with 500 when an IMAP monitor has not sent a heartbeat for 2 minutes. Use it for uptime checks. |
| `GET /api/status/workers` | Login | IMAP monitors and planner accounts with their last activity. |
| `GET /api/status/summary` | Login | The service overview shown on **System Status**. |
| `GET /api/status/version` | Login | The installed version and release notes. |

### Logs

Janus logs to the terminal and to `log_file_path` (default `~/.local/state/janus/janus.log`). As a service, `janus service logs` reads the journal. Janus does not rotate its log file; use `logrotate` with `copytruncate` if it grows too large.

## CLI reference

Every command takes `--help`. The global option `--url` (environment `JANUS_URL`, default `http://localhost:8080`) sets the server address for the commands that talk to a running Janus.

### Local commands

These work on the local install directly.

| Command | What it does |
|---|---|
| `janus start [--host H] [--port P] [--tray/--no-tray] [--server-mode/--desktop-mode]` | Start the server, the web app and the background workers. |
| `janus brief` | Run the Morning briefing trigger now and print it. |
| `janus digest` | Run the Afternoon digest trigger now and print it. |
| `janus update [--check]` | Install the latest release, or only report it. |
| `janus config init` | Create `settings.toml` from a commented template. |
| `janus config show` | Print the effective settings, secrets masked. |
| `janus config doctor` | Check the settings for common mistakes. |
| `janus user bootstrap-admin NAME [--password P]` | Create the first admin in the database. Prompts for the password. |
| `janus db copy --target URL [--source URL]` | Copy all data into an empty database, for example from SQLite to PostgreSQL. Stop Janus first. |
| `janus knowledge backfill` | Ingest all existing mail, chats, WhatsApp chats, tasks and calendar events as knowledge sources. |
| `janus knowledge reembed [--force]` | Embed missing or stale memory facts and topics. `--force` re-embeds everything and rebuilds the keyword index. |
| `janus microsoft login` | Sign in to Microsoft 365 in the browser for Outlook mail, calendar and Teams. |
| `janus microsoft status` | Show the signed-in Microsoft account and its permissions. |
| `janus microsoft logout` | Forget the Microsoft account and delete its token cache. |
| `janus service install [--no-start]` | Install and start the systemd user service. |
| `janus service status` | Show the service state. |
| `janus service logs [-f] [-n N]` | Show the service log. |
| `janus service uninstall` | Stop and remove the service. |

`brief` and `digest` run for the first user in the database. Run them with the server stopped or running; they do not need the API.

### API client commands

These call a running Janus at `--url`. Log in first; the token is cached in `~/.config/janus/token.json`.

| Command | What it does |
|---|---|
| `janus login USERNAME` | Log in and cache the token. Prompts for the password. |
| `janus me` | Show the logged-in user. |
| `janus desktop-summary [--no-unread-emails]` | Print open tasks and unread mail as JSON for Waybar. See [Desktop](desktop.md). |
| `janus account add NAME EMAIL USERNAME [--imap-server S] [--imap-port P]` | Add an IMAP account (default `imap.gmail.com:993`). Prompts for the password. |
| `janus account list` | List your mail accounts. |
| `janus rule add NAME PATTERN [--field F] [--priority P]` | Add a mail rule (default field `sender`, priority `IMPORTANT`). |
| `janus rule list` | List your mail rules. |
| `janus planner add NAME PROVIDER [--api-url URL]` | Add a planner connection with a token, for example Jira. Prompts for the token. |
| `janus planner list` | List planner connections. |
| `janus user create USERNAME` | Create a user (admin only). Prompts for the password. |
| `janus user list` | List users (admin only). |
