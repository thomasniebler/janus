# Quickstart

Get Janus running on your machine: install it, set a secret key, create the first admin, connect a model and add a mailbox.

## Prerequisites

| Tool | Version | Needed for |
|---|---|---|
| Python | 3.12 or newer | Running Janus |
| uv | latest | Installing and running Janus |
| pipx | latest | Optional, instead of `uv` for a tool install |
| Node.js | 18 or newer | Only for building the web app from a git checkout |
| Ollama | latest | Optional, for a local model |

Install `uv` with `curl -LsSf https://astral.sh/uv/install.sh | sh`. If your system Python is older than 3.12, let `uv` provide one with `uv python install 3.12`.

## 1. Install

### As a command (recommended)

The `janus-assistant` package contains the web app and the database migrations, so you need neither Node.js nor the source tree. It installs the `janus` command:

```bash
uv tool install janus-assistant
# or: pipx install janus-assistant
```

With optional extras: `uv tool install "janus-assistant[whatsapp,desktop]"`. See [Optional features](#optional-features).

With a tool install, run `janus …` wherever this page says `uv run janus …`.

### From a git checkout

```bash
git clone https://github.com/thomasniebler/janus.git
cd janus
uv sync
cd frontend && npm install && npm run build && cd ..
```

`uv sync` creates `.venv` with the dependencies Janus always needs. The `npm` step builds the web app into `frontend/dist`. Without it the API works but the dashboard does not load. `task run` builds the web app and starts Janus in one step.

## 2. Set a secret key

Janus signs logins with a secret key and refuses to start without one. Create the settings file:

```bash
uv run janus config init
```

This writes `~/.config/janus/settings.toml` with every setting commented out. Generate a key:

```bash
openssl rand -hex 32
```

Then set it in the file:

```toml
# ~/.config/janus/settings.toml
secret_key = "paste-your-generated-key-here"
```

The chat connection checks the browser's origin. Janus always allows the origin of `base_url` (default `http://localhost:8080`), so opening that address works without further settings.

Check what Janus will use, and look for common mistakes:

```bash
uv run janus config show     # effective settings, secrets masked
uv run janus config doctor   # errors and warnings
```

You can also set `JANUS_SECRET_KEY` in the environment or in a `.env` file instead. See [Configuration](configuration.md) for every setting and how the sources combine.

## 3. Start Janus

=== "Local desktop run"
    ```bash
    uv run janus start
    ```

=== "Headless server"
    ```bash
    uv run janus start --server-mode
    ```

On start, Janus:

1. Creates its data directories and the encryption key file (`~/.local/share/janus/.janus_key`) if they are missing.
2. Creates or migrates the database (`~/.local/share/janus/janus.db` by default).
3. Starts its background workers and listens on `http://127.0.0.1:8080`. Open `http://localhost:8080`.

`--server-mode` turns off the tray icon and desktop notifications. Use it on any machine without a desktop.

!!! note "Single-instance runtime"
    Janus runs one server process per set of runtime paths. If a second copy starts against the same paths, it refuses to start and names the lock file (`janus.lock`, next to the log file).

To keep Janus running in the background and start it at login, see [Operations](operations.md#run-janus-as-a-service).

## 4. Create the first admin

Pick one of two ways.

### From the command line (default)

```bash
uv run janus user bootstrap-admin admin
```

Janus asks for the password twice, then creates the admin directly in the database. The server does not need to be running. Open `http://localhost:8080` and log in.

### In the browser

The browser setup screen is off by default. To use it, set `public_setup_enabled = true` in `settings.toml` and restart Janus. While no user exists, Janus then prints a one-time setup token in its log:

```
Janus first-run setup is required.
Use this one-time token within 15 minutes: <YOUR-TOKEN>
```

Open `http://localhost:8080/setup`, paste the token, choose a username and password, and submit. If the token expires, restart Janus for a new one. Turn `public_setup_enabled` off again afterwards.

Passwords can be at most 72 bytes.

## 5. Connect a model

Open **Settings → AI & Models → Models**. In the **AI Configuration** card, pick a provider and fill in its fields:

| Provider | What you need |
|---|---|
| Ollama | A running Ollama (default `http://localhost:11434`) and a pulled model, for example `ollama pull llama3`. Everything stays on your machine. |
| OpenAI, OpenRouter, Gemini, Anthropic | An API key. |
| GitHub Models | A GitHub token with the `models:read` permission. |
| Azure OpenAI | The resource endpoint (root only, such as `https://my-resource.openai.azure.com`) and a deployment, plus an API key or Entra ID. Entra ID uses `az login` or a managed identity with the **Cognitive Services OpenAI User** role, and works only on Azure OpenAI hosts. |

Click **Test Selected Backend**, then **Save Backend Settings**. Only an admin can change the model, because it is shared by the whole instance. See [Configuration](configuration.md#settings-in-the-app) for per-surface models, embeddings and image generation.

!!! warning "Cloud models see your data"
    With a cloud provider, the mail, chat and context Janus works on are sent to that provider. Ollama keeps it local.

## 6. Add a mail account

Open **Settings → Connections → Mail** (or the **Email Preview** page). In **Connected Accounts**:

- **Custom IMAP**: enter the server, port, username and password. For Gmail, use an [app password](https://support.google.com/accounts/answer/185833).
- **Add Google mailbox** or **Add Microsoft mailbox**: available once the operator configured Google or Microsoft. See [Integrations](integrations.md).

Common IMAP servers:

| Provider | IMAP server | Port |
|---|---|---|
| Gmail | `imap.gmail.com` | 993 |
| iCloud | `imap.mail.me.com` | 993 |
| Outlook / Hotmail | `imap-mail.outlook.com` | 993 |

Janus starts reading a mailbox as soon as you add it: it syncs recent mail and then waits for new mail with IMAP IDLE. Your rules and the model sort each message; see [Inbox](inbox.md).

## 7. Look around

- **Dashboard**: today's morning briefing. The built-in triggers (Morning briefing at 06:30, Afternoon digest at 16:00, Mail processing for every new mail) are under **Settings → Triggers**. See [Runtime triggers](runtime-triggers.md).
- **Chat**: talk to Janus on any page. See [Chat](chat.md).
- **Settings → Tools & Safety**: what Janus may do on its own. See [Tools and permissions](tools.md).
- **Settings → Character**: how Janus speaks.

From the command line, `uv run janus brief` and `uv run janus digest` run the briefing and digest now and print them.

## Optional features

Some features are optional extras, so a server install stays small:

| Extra | Adds | Without it |
|---|---|---|
| `whatsapp` | The WhatsApp connector (Neonize). Needs glibc 2.34 or newer. | WhatsApp accounts report that WhatsApp support is not installed. |
| `desktop` | The tray icon (`janus start --tray`; pystray and Pillow). | Janus logs a warning and runs without the icon. |
| `documents` | PDF, Word, Excel and PowerPoint parsing for the knowledge store. Needs glibc 2.27 or newer. | Only text, Markdown and CSV files are read. |
| `postgres` | The PostgreSQL driver, for a `postgresql://` database URL. | Only SQLite works. `janus config doctor` reports the missing driver. |

Install them with your install method:

```bash
uv sync --extra whatsapp --extra desktop                 # git checkout
uv tool install "janus-assistant[whatsapp,desktop]"      # uv tool
pipx install "janus-assistant[whatsapp,desktop]"         # pipx
```

In a git checkout, a plain `uv sync` removes extras that you do not name. Repeat the `--extra` flags after pulling, or use `task up`, which keeps the installed ones. Automatic updates keep installed extras on their own.

Other optional pieces:

- **Ollama** for local chat models and local embeddings. Install it from [ollama.com](https://ollama.com), pull a model, and select it in **Settings → AI & Models → Models**. For local embeddings also run `ollama pull nomic-embed-text`.
- **Google and Microsoft** sign-in, mail and calendars need OAuth client ids in `settings.toml`. See [Integrations](integrations.md).
- **The tray icon on Wayland** needs AppIndicator libraries. See [Desktop](desktop.md).
- **Shell and coding-agent tools** are off unless the operator turns them on. See [Configuration](configuration.md#all-settings).

## Troubleshooting

**`janus start` exits with a secret key error.** `secret_key` is still the placeholder. Set it as in step 2 and check with `uv run janus config show`.

**The dashboard shows a blank page from a git checkout.** Build the web app (`cd frontend && npm run build`).

**Chat does not connect.** Open Janus at its `base_url` (default `http://localhost:8080`). Any other address, such as `http://127.0.0.1:8080`, must be listed in `allowed_origins`; see [Configuration](configuration.md#all-settings).

**`/setup` returns "Initial setup is not available".** Public setup is off. Use `janus user bootstrap-admin`, or turn `public_setup_enabled` on.
