# Configuration

Janus has two kinds of settings:

- **Instance settings** such as the secret key, paths, the database URL and network options. They come from a file, `.env` or the environment, and take effect when Janus starts.
- **App settings** such as the AI model, mail accounts, triggers and tool permissions. They live in the database and you change them in the web app under **Settings**.

## Where instance settings come from

Janus reads each instance setting from the first source that has it, highest priority first:

| Priority | Source | Notes |
|---|---|---|
| 1 | Environment variables | For example `JANUS_SECRET_KEY` or `DATABASE_URL`. |
| 2 | `.env` in the current directory | Same names as the environment variables. Janus reads it from the directory you start it in. |
| 3 | `~/.config/janus/settings.toml` | Lowercase keys such as `secret_key`. Created by `janus config init`. |
| 4 | Built-in defaults | See [All settings](#all-settings). |

Each setting is named once in each form: the TOML key (`server_mode`) and the environment variable (`JANUS_SERVER_MODE`). Six settings have no `JANUS_` prefix in the environment: `DATABASE_URL`, `HOST`, `PORT`, `ALLOWED_ORIGINS`, `ALGORITHM` and `ACCESS_TOKEN_EXPIRE_MINUTES`. The comment at the top of the generated `settings.toml` says every key maps to `JANUS_*`; for these six it does not. `base_url` accepts both `JANUS_BASE_URL` and `BASE_URL`.

In the environment, `ALLOWED_ORIGINS` and `JANUS_ALLOWED_HOSTS` are comma-separated lists. The coding-agent list and table settings are JSON, for example `JANUS_CODING_AGENTS_ENV_PASSTHROUGH='["OPENAI_API_KEY"]'`.

## The settings file

```bash
janus config init     # write ~/.config/janus/settings.toml with every key commented out
janus config show     # print the effective settings, secrets masked
janus config doctor   # check for common mistakes
```

- `init` never overwrites an existing file. `task setup` runs it in a git checkout.
- `show` says which file it loaded, then prints the main settings. It masks the secret key and any password in the database URL.
- `doctor` reports errors and warnings and exits with code 1 when it finds an error.

`janus config doctor` checks:

| Check | Level |
|---|---|
| `secret_key` is still the placeholder | Error |
| `port` differs from the port in a localhost `base_url` | Error |
| The origin of `base_url` is missing from `allowed_origins` | Error |
| `DATABASE_URL` needs a driver that is not installed (for example PostgreSQL without the `postgres` extra) | Error |
| The bind address origin is missing from `allowed_origins` | Warning |
| The `base_url` host is missing from `allowed_hosts` | Warning |
| The Fernet key file does not exist yet (it is created on the first start) | Warning |
| `server_mode` with a plain-HTTP public `base_url`, or with a localhost `base_url` | Warning |

A minimal file for a local install:

```toml
secret_key = "output of: openssl rand -hex 32"
allowed_origins = ["http://localhost:8080", "http://127.0.0.1:8080"]
```

Keep the file private (`chmod 600`) once it holds secrets such as `google_client_secret`.

## Files and directories

Janus follows the XDG base directories. Each one honours its `XDG_*_HOME` variable.

| What | Default path | Setting |
|---|---|---|
| Settings file | `~/.config/janus/settings.toml` | `XDG_CONFIG_HOME` |
| CLI login token | `~/.config/janus/token.json` | `XDG_CONFIG_HOME` |
| Database (SQLite) | `~/.local/share/janus/janus.db` | `database_url` |
| Encryption key | `~/.local/share/janus/.janus_key` | `fernet_key_path` |
| Generated images | `~/.local/share/janus/images/` | `image_storage_dir` |
| Log file | `~/.local/state/janus/janus.log` | `log_file_path` |
| Instance lock | `janus.lock` next to the log file | follows `log_file_path` |
| Pre-update database snapshot | `janus.pre-release.db` next to `janus.db` | follows `database_url` |

`fernet_key_path`, `log_file_path` and `image_storage_dir` must be absolute paths. Keep the database and the key file on persistent storage, and back them up together: without the key, stored passwords and API keys cannot be decrypted.

## All settings

### Core and network

| TOML key | Environment variable | Default | Meaning |
|---|---|---|---|
| `secret_key` | `JANUS_SECRET_KEY` | placeholder | Signs login tokens. Required: Janus refuses to start with the placeholder. |
| `database_url` | `DATABASE_URL` | `sqlite:////home/you/.local/share/janus/janus.db` | SQLAlchemy URL. See [Database](#database). |
| `fernet_key_path` | `JANUS_FERNET_KEY_PATH` | `~/.local/share/janus/.janus_key` | Key that encrypts stored credentials. Created on first start with mode 0600. |
| `log_file_path` | `JANUS_LOG_FILE_PATH` | `~/.local/state/janus/janus.log` | Runtime log. |
| `server_mode` | `JANUS_SERVER_MODE` | `false` | Turns off the tray icon and desktop notifications. Same as `janus start --server-mode`. |
| `host` | `HOST` | `127.0.0.1` | Address the server binds to. |
| `port` | `PORT` | `8080` | Port the server listens on. |
| `base_url` | `JANUS_BASE_URL` or `BASE_URL` | `http://localhost:8080` | Public address of Janus, used for OAuth redirect URIs and links. |
| `allowed_origins` | `ALLOWED_ORIGINS` | `http://localhost:5173`, `http://127.0.0.1:5173` | Browser origins allowed for CORS and WebSocket connections. Add the address you open Janus at. |
| `allowed_hosts` | `JANUS_ALLOWED_HOSTS` | `localhost`, `127.0.0.1`, `[::1]` | Host headers Janus accepts. Other hosts get 400. |
| `trusted_proxy_hops` | `JANUS_TRUSTED_PROXY_HOPS` | `0` | Reverse proxies whose `Forwarded` / `X-Forwarded-*` headers Janus trusts. |
| `public_api_docs_enabled` | `JANUS_PUBLIC_API_DOCS_ENABLED` | `false` | Serves `/apidocs`, `/apiredoc` and `/openapi.json`. |
| `public_setup_enabled` | `JANUS_PUBLIC_SETUP_ENABLED` | `false` | Enables the browser setup screen and its one-time token. |
| `algorithm` | `ALGORITHM` | `HS256` | Login token signature algorithm. |
| `access_token_expire_minutes` | `ACCESS_TOKEN_EXPIRE_MINUTES` | `10080` (7 days) | Login token lifetime. |

### Workers, chat and tools

| TOML key | Environment variable | Default | Meaning |
|---|---|---|---|
| `runtime_trigger_worker_enabled` | `JANUS_RUNTIME_TRIGGER_WORKER_ENABLED` | `true` | Runs queued, scheduled and event triggers. When off, triggers stay queued. See [Runtime triggers](runtime-triggers.md). |
| `runtime_trigger_max_attempts` | `JANUS_RUNTIME_TRIGGER_MAX_ATTEMPTS` | `3` | Attempts per trigger run before it is marked exhausted. |
| `runtime_trigger_retry_backoff_seconds` | `JANUS_RUNTIME_TRIGGER_RETRY_BACKOFF_SECONDS` | `0` | Base retry delay, doubled each retry. `0` retries on the next poll. |
| `knowledge_index_worker_enabled` | `JANUS_KNOWLEDGE_INDEX_WORKER_ENABLED` | `true` | Ingests sources and embeds memory facts and topics. See [Knowledge](knowledge.md). |
| `chat_tool_approval_timeout_seconds` | `JANUS_CHAT_TOOL_APPROVAL_TIMEOUT_SECONDS` | `300` | How long an approval card waits before the call counts as declined. |
| `chat_ui_action_timeout_seconds` | `JANUS_CHAT_UI_ACTION_TIMEOUT_SECONDS` | `10` | How long Janus waits for the page to confirm a page action or answer a page question. |
| `tool_selection_enabled` | `JANUS_TOOL_SELECTION_ENABLED` | `true` | Lets the utility model narrow the offered tools to what a task needs. See [Tools and permissions](tools.md). |
| `tool_selection_timeout_seconds` | `JANUS_TOOL_SELECTION_TIMEOUT_SECONDS` | `10` | After this, every allowed tool is offered. |
| `image_storage_dir` | `JANUS_IMAGE_STORAGE_DIR` | `~/.local/share/janus/images` | Generated images, one folder per user. |
| `image_storage_limit_mb` | `JANUS_IMAGE_STORAGE_LIMIT_MB` | `200` | Per-user image storage cap. |

### Shell and coding-agent tools

Both are off and meant only for a personal, local install. Only admins get them, and in chat every call waits for approval. See [Tools and permissions](tools.md).

| TOML key | Default | Meaning |
|---|---|---|
| `shell_tool_enabled` | `false` | Lets Janus run commands on this machine. |
| `shell_tool_working_dir` | your home directory | Where commands run. |
| `shell_tool_timeout_seconds` | `30` | Time limit per command (at most 600). |
| `shell_tool_output_max_bytes` | `16000` | Output above this stops the command. |
| `shell_tool_allow_shell_syntax` | `false` | Runs commands through `/bin/sh -c`, so pipes and redirection work. |
| `coding_agents_enabled` | `false` | Lets Janus ask an installed `claude` or `codex` CLI for help. |
| `coding_agents_working_dir` | your home directory | Where the agents run. |
| `coding_agents_timeout_seconds` | `300` | Time limit per question (at most 3600). |
| `coding_agents_output_max_bytes` | `50000` | Answer size cap. |
| `coding_agents_allow_edits` | `false` | Lets the agents edit files in their working directory. |
| `coding_agents_env_passthrough` | `[]` | Extra environment variables passed to the agents. `JANUS_*` names are never passed. |
| `coding_agents_commands` | `{}` | Replaces the `claude` or `codex` command line. Exactly one argument must be `{prompt}`. |

The environment variable is the TOML key in upper case with a `JANUS_` prefix, for example `JANUS_SHELL_TOOL_ENABLED`.

### Accounts and cloud services

| TOML key | Default | Meaning |
|---|---|---|
| `google_client_id`, `google_client_secret` | empty | OAuth client for Google sign-in and **Connect Google**. Empty keeps both off. |
| `microsoft_client_id`, `microsoft_tenant_id` | empty | Entra ID app for **Connect Microsoft** and Microsoft 365. Empty keeps it off. |
| `azure_enabled` | `false` | Turns on the read-only Azure resource tools. |

See [Integrations](integrations.md) for how to register the OAuth apps. The environment variables use the `JANUS_` prefix.

### Automatic updates

| TOML key | Default | Meaning |
|---|---|---|
| `auto_update_enabled` | `true` | Installs new GitHub releases and restarts. See [Operations](operations.md#updates). |
| `auto_update_repository` | `thomasniebler/janus` | Repository (`owner/name`) whose releases are installed. |
| `auto_update_interval_minutes` | `60` | Minutes between checks (at least 15). |

### Test-only settings

`JANUS_GOOGLE_API_BASE_URL`, `JANUS_TELEGRAM_API_BASE_URL` (default `https://api.telegram.org`) and `JANUS_AZURE_OPENAI_ALLOW_LOOPBACK_HTTP` point Janus at local fakes for the end-to-end tests. Leave them unset on a real install.

## Database

### SQLite (default)

Janus stores everything in one SQLite file, `~/.local/share/janus/janus.db`, in WAL mode. It creates the file on first start.

For a SQLite URL, an absolute path needs four slashes: `sqlite:////home/you/janus.db`. A relative path uses three: `sqlite:///./janus.db`. Janus refuses `sqlite:///home/you/janus.db`, which SQLAlchemy would read as a relative path.

Semantic search loads the `sqlite-vec` extension, and keyword search uses SQLite FTS5. Both depend on your Python's `sqlite3` build:

```bash
uv run python -c "import sqlite3; print(hasattr(sqlite3.connect(':memory:'), 'enable_load_extension'))"
```

If extension loading is missing, search falls back to keywords. Without FTS5, keyword search matches substrings. **Settings → System Status** shows the state under **Knowledge search**.

### Migrations

`janus start` brings the schema up to date before it serves requests, so you never run migrations by hand. `janus user bootstrap-admin`, `janus brief` and `janus digest` do the same. In a git checkout, `uv run alembic upgrade head` also works.

### PostgreSQL

1. Install the driver: the `postgres` extra (see [Optional features](quickstart.md#optional-features)).
2. Create an empty database.
3. Stop Janus and copy your data into it:

    ```bash
    janus db copy --target postgresql://user:password@host:5432/janus
    ```

    `--source` defaults to the configured `DATABASE_URL`. The command migrates the target first, refuses a target that already holds Janus data, and copies in one transaction. The source must be at the current schema (start Janus on it once if it is not).

4. Set `DATABASE_URL` to the PostgreSQL URL, keep the same key file, and start Janus.

Stored credentials are copied still encrypted, so the key file must move with the data. WhatsApp pairing sessions live in their own files and stay where they are.

!!! note "Semantic search on PostgreSQL"
    PostgreSQL has no vector index yet. Memory and topic search match substrings there, and no embeddings are computed.

## Settings in the app

Everything below is stored in the database. Instance-wide cards are editable by admins only; other users see them read-only.

| Tab | What you set there | Scope |
|---|---|---|
| Email Accounts | IMAP, Google and Microsoft mailboxes | Per user |
| WhatsApp | WhatsApp accounts and pairing | Per user |
| Security | Change your password (signs out your other sessions) | Per user |
| Appearance | Classic, Terminal or Rusty Console look | Per user |
| Daily Briefing | Briefing options, the secretary and device access | Per user |
| Character | Character profile and character state | Per user |
| Triggers | Trigger definitions, built-in triggers, suggested automations | Per user |
| Skills | The skill library | Per user |
| Mail Ingestion | How many emails the first sync of a mailbox fetches | Admin |
| AI & System | **AI Configuration** (provider, model, keys), **Models per surface**, **Semantic search** (embeddings), **Image generation** | Admin |
| Connections | Connect Google and Microsoft accounts | Per user |
| GitHub, Linear | Integration credentials | Admin |
| Tools & Connections | Web search, tool permissions, MCP servers, remote agents, prompt-injection check, self-evolution | Mixed |
| System Status, Stats, System Events, Error Groups | Monitoring; see [Operations](operations.md#monitoring) | Error Groups is admin only |

### AI & System

- **AI Configuration** picks the default model. Providers: Ollama, OpenAI, OpenRouter, Gemini, Anthropic, GitHub Models and Azure OpenAI. The model field lists the models the provider offers and also accepts free text. API keys are stored encrypted and never returned by the API. Use **Test Selected Backend** before **Save Backend Settings**.
- **Models per surface** lets **Chat** and **Utility** (small helper calls such as titles, memory suggestions and tool selection) use another provider or model. Each trigger picks its own model in its editor under **Settings → Triggers**. A model that cannot be used fails loudly; Janus never falls back to the default model silently.
- **Semantic search** adds an embedding model (Ollama, OpenAI or Gemini) so memory and knowledge search also match by meaning. `janus knowledge reembed` embeds everything at once.
- **Image generation** is off until an admin picks an image provider (OpenAI, Gemini or OpenRouter).

A cloud provider chosen for any of these receives the data that surface works on. Ollama keeps it on your machine.
