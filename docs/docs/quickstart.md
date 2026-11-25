# Quickstart

Get Janus running on your machine in five minutes.

## Prerequisites

| Tool | Version | Install |
|------|---------|---------|
| Python | 3.12 + | [python.org](https://www.python.org/downloads/) |
| uv | latest | `curl -LsSf https://astral.sh/uv/install.sh | sh` |
| Node.js | 18 + (optional, for dev frontend) | [nodejs.org](https://nodejs.org/) |

## 1. Install

### As a command (uv tool or pipx)

To run Janus without working on its code, install the `janus-assistant` package. It contains
the web dashboard and the database migrations, so neither Node.js nor the source tree is needed,
and it installs the `janus` command:

```bash
uv tool install janus-assistant
# or: pipx install janus-assistant
```

Add optional extras (see below) as `uv tool install "janus-assistant[whatsapp,desktop]"`. Each
[GitHub release](https://github.com/thomasniebler/janus/releases) also carries the same wheel
(`janus_assistant-<version>-py3-none-any.whl`), which you can install by URL instead. Then run
`janus` directly wherever the steps below say `uv run janus`. `janus update` upgrades the
install to each new release; see [Automatic updates](configuration.md#automatic-updates).

### From a git checkout

```bash
git clone https://github.com/thomasniebler/janus.git
cd janus
uv sync
```

This creates a `.venv` and installs the Python dependencies Janus always needs.

### Optional features

Some features are optional extras, so a server install stays small and does not
pull in libraries it cannot use:

| Extra | Adds | Without it |
|---|---|---|
| `whatsapp` | The WhatsApp connector (Neonize, which also installs Pillow) | WhatsApp accounts show "WhatsApp support is not installed" |
| `desktop` | The system tray icon (`janus start --tray`; pystray and Pillow) | Janus logs a warning and runs without the icon |
| `documents` | PDF, Word, Excel, and PowerPoint parsing for the knowledge store | Only TXT, Markdown, and CSV files are read |
| `postgres` | The PostgreSQL driver (psycopg2) for a `postgresql://` database URL | Only SQLite (the default) works; `janus config doctor` says so |

Install the ones you want, for example:

```bash
uv sync --extra whatsapp --extra desktop
```

Run the same command again after pulling a new version. The built-in
auto-update keeps whichever extras are installed. The `whatsapp` extra needs
glibc 2.34 or newer and `documents` needs glibc 2.27 or newer, so neither works
on older shared hosts such as Uberspace (glibc 2.17).

If your host only provides an older system Python (common on shared hosting),
install an app-managed Python 3.12 runtime with `uv` before continuing:

```bash
uv python install 3.12
```

## 2. Configure

Create the user config file:

```bash
uv run janus config init
```

This writes `~/.config/janus/settings.toml` with commented-out defaults. Open it and
set at least the `secret_key`:

```bash
# Generate a strong random key:
openssl rand -hex 32
```

Then paste the result into your config file:

```toml
# ~/.config/janus/settings.toml

# JWT signing key — required before starting Janus.
secret_key = "paste-your-generated-key-here"

# Persistent runtime paths (recommended for all deployments)
database_url = "sqlite:////home/you/.local/share/janus/janus.db"
fernet_key_path = "/home/you/.local/share/janus/.janus_key"
log_file_path = "/home/you/.local/state/janus/janus.log"

# Recommended for headless/shared-hosting deployments
server_mode = true
```

Verify your config was loaded correctly:

```bash
uv run janus config show
```

You should see `secret_key = "***"` (masked) and your other settings.

## 3. Build the frontend (optional)

The pre-built dashboard is included in releases. If you're running from source, build
it once with:

```bash
cd frontend && npm install && npm run build && cd ..
```

If you skip this step, the API still works but the web dashboard won't load.

## 4. Start the server

=== "Local desktop run"
    ```bash
    uv run janus start
    ```

=== "Shared hosting / headless server"
    ```bash
    uv run --python 3.12 janus start --server-mode
    ```

Janus will:

1. Run any pending database migrations automatically.
2. Print a **one-time setup token** in the log output if no admin user exists yet and public setup is enabled.
3. Begin listening at `http://127.0.0.1:8080`.

### Run in the background (Linux)

To keep Janus running without a terminal and start it when you log in, install it as a
systemd user service:

```bash
janus service install    # write ~/.config/systemd/user/janus.service, enable and start it
janus service status     # is it running?
janus service logs -f    # follow its log (journalctl)
janus service uninstall  # stop it and remove the unit
```

The service runs the same `janus` you ran `service install` with, in the directory you ran it
from (so a `.env` there is still read), with that shell's `PATH` (so automatic updates find
`uv` or `pipx`). To start it at boot without logging in, run `loginctl enable-linger $USER`.
`janus update` restarts the service after installing a release. The setup token below then
appears in `janus service logs`.

!!! note "Single-instance runtime"
    Janus currently supports one running server process per configured runtime
    directory/database. If a second copy starts against the same paths, Janus
    will refuse startup and point at the lock file near `log_file_path`.

The setup token looks like this in the log:

```
WARNING  janus.services.auth_service:auth_service.py:47 Janus first-run setup is required.
Use this one-time token within 15 minutes: <YOUR-TOKEN-HERE>
```

!!! warning "Token expiry"
    The setup token expires in **15 minutes**. If it expires before you complete setup,
    restart Janus to generate a new one.

## 5. Create your admin account

### Browser setup (default)

Open `http://127.0.0.1:8080` in your browser. You'll be redirected to the setup screen.

Enter the token from step 4, choose a username and password, then click **Complete Setup**.
Passwords can be at most 72 bytes — 72 plain ASCII characters, fewer if they contain accented or
non-Latin characters. The same limit applies to `janus user bootstrap-admin` and password changes.

You can now log in to the dashboard.

### SSH/CLI setup for hosted deployments

If you do not want to expose the public setup screen, set `public_setup_enabled = false`
in `~/.config/janus/settings.toml`, then create the first admin over SSH:

```bash
uv run --python 3.12 janus user bootstrap-admin admin
```

After that, start Janus normally and log in with the admin account you just created.

## 6. Add an email account

In the dashboard, go to **Accounts → Add Account** and fill in your IMAP details.

For **Gmail**, you must use an [App Password](https://support.google.com/accounts/answer/185833)
rather than your regular login password.

Common IMAP settings for other providers:

| Provider | IMAP server | Port |
|----------|-------------|------|
| Gmail | `imap.gmail.com` | 993 |
| iCloud | `imap.mail.me.com` | 993 |
| Outlook/Hotmail | `imap-mail.outlook.com` | 993 |

## 7. Configure rules (optional)

Rules tell Janus which emails are *Important* (immediate notification) and which go into
the *Digest* (batched summary). Your rules only apply to mail arriving on your own
accounts; other users' rules never touch it. You can manage them in the dashboard under
**Rules**, or via the CLI once the server is running:

```bash
# Flag emails from your manager as important
uv run janus rule add "Manager" "boss@company.com" --field sender

# Flag anything with "urgent" in the subject
uv run janus rule add "Urgent" "urgent|ASAP" --field subject
```

You can also ask Janus in chat to create, update, or delete rules for you, and
to list, approve, or dismiss learned rule suggestions from the relabel-learning
queue, once a mail account is connected. Chat is supervised by you, so it acts
right away; unattended trigger runs are refused rule changes unless you allow
them (see [Runtime triggers](runtime-triggers.md#what-a-trigger-may-do-the-unattended-switch-and-refusals)).

## 8. Check your triggers (optional)

Janus's briefing, digest, and per-email processing are **triggers** you can
review under **Settings → Triggers**:

- **Morning briefing** runs daily at 06:30 (in your browser's timezone once you
  open the dashboard); the dashboard shows it and its refresh button runs it
  again. `uv run janus brief` runs it from the CLI.
- **Afternoon digest** starts disabled unless you had digest notifications
  set up before; enable it and pick a time on its trigger. `uv run janus digest`
  runs it from the CLI.
- **Mail processing** runs once for every new email that no SPAM/IGNORE rule
  filtered out, including the initial sync of each mailbox. With a paid model
  provider that is one billed agent run per email; disable it to use the
  cheaper built-in classifier instead.

Most pages stay live: when Janus, a trigger run, a background worker, or
another browser tab changes memory, topics, rules, mail, planner items,
skills, dashboard widgets, triggers and suggested automations, knowledge,
the wiki, your character, tool settings, WhatsApp messages or a WhatsApp
account's connection status, the open pages
refresh on their own. Other settings, such as email accounts, still need a
reload to show changes made elsewhere.

Your Janus character (**Settings → Character**) shapes how Janus writes in
chat and in every trigger run. Its **Adaptive state** (tone, verbosity,
proactivity, focus) can change over time, but only when you approve one of
Janus's proposals or edit it yourself; **History** lets you restore any
earlier state.

## Next steps

- **Chat with Janus**: the chat panel sits on the right of every page (below the
  page on narrow screens), and **Chat** in the navigation opens the same
  conversation full width with your chat history alongside. Moving between
  pages, or between the panel and the Chat page, keeps the conversation and any
  unsent text; reloading the tab reopens the conversation you had open.
  Every tool Janus used for a reply shows as a chip under it (running, done,
  failed, or blocked, with how long it took); click a chip to see what Janus
  sent the tool and what came back. The chips stay when you reopen the chat.
  When Janus hands a question to a background task, a **Delegated tasks**
  line appears above the conversation; open it to see each task's status
  (queued, running, completed, or failed), what it was asked, which tools it
  could use, and its answer or error. See
  [Task delegation tools](tools.md#task-delegation-tools).
  **Janus knows which page you are on**: every message you send includes a
  short summary of what that page shows, so you can ask "what am I looking
  at?" or "summarize this email" without copying anything. The summary holds
  only what is on screen, as names and short labels: the page and its open
  tab, active filters and search, and the listed items (an email's subject and
  sender, topic titles, memory fact summaries, trigger names), never an
  email's body or a message's text. It covers the Dashboard, Emails, Memory
  (Agent Memory and Topics), and Settings (including Triggers); on other pages
  Janus is told the page name only. It is used for that one message and is not
  saved with the conversation. Janus treats it as data, not as instructions:
  an email subject written to steer Janus is flagged like any other untrusted
  content.
  **Janus can also act on that page**: ask it to "filter to important", "open
  the mail from Anna", "go to triggers and show the failed runs", or "fill in
  a topic about the garden", and it navigates, filters, opens, highlights, or
  fills in the form for you (it never submits anything; you do). Each action
  appears as a chip under the reply with an **Undo** button where possible.
  See [Page actions](tools.md#page-actions).
  **Janus can look at the page, too**: ask "why is the save button greyed
  out?" or "why does this look wrong?" and it reads an outline of the page
  (headings, buttons, form-field labels, short visible text) and, if needed,
  the styles of one element. It never sees what you typed into a form or any
  password field. See [Page introspection](tools.md#page-introspection).
  **Janus can answer with more than text**, too: ask it to "show my open
  tasks as a table" or "chart this week's mail by day", and the reply
  contains a table, a chart, a card, a checklist, or a few buttons. A
  button only sends Janus a chat message asking for that action, so
  anything that needs your OK still asks first. See
  [UI blocks](tools.md#ui-blocks).
  **Janus can draw**, once an administrator picks an image provider (OpenAI,
  Gemini, or OpenRouter) under **Settings → AI & System → Image generation**:
  ask it to "draw a lighthouse at dusk" and the picture appears in the reply
  with a **Download** link. The prompt is sent to that cloud provider and
  each image costs money; images are kept with the conversation and deleted
  with it. See [Image generation](tools.md#image-generation).
- **CLI reference**: run `uv run janus --help` to see all commands.
- **Configuration**: see [Configuration](configuration.md) for all settings including
  PostgreSQL, host/port binding, and environment variables.
- **Google and Microsoft accounts**: put `google_client_id` / `google_client_secret`
  and `microsoft_client_id` / `microsoft_tenant_id` in `~/.config/janus/settings.toml`
  (see [Connecting Google and Microsoft](configuration.md#connecting-google-and-microsoft)),
  restart, then press **Connect Google** / **Connect Microsoft** under
  **Settings → Connections**. Pick the calendars to sync on the **Planner** page
  and add Gmail, Outlook or custom IMAP mailboxes on the **Email** page.
  Jira is supported too, but only via
  `janus planner add` (CLI/API) — there is no dashboard UI for it yet. Azure
  DevOps and Office 365 are not implemented; see
  [Phase 3: The Planner](roadmap/planner.md) for that roadmap item.
- **Linear integration**: connect a Linear workspace under **Settings → Linear**
  so the chat agent can read and create issues — see
  [Configuration](configuration.md#linear-integration).

## Troubleshooting

### `janus start` exits immediately with a key error

You haven't set `secret_key` in your config yet. Re-read step 2 and run
`uv run janus config show` to confirm the value is being picked up.

### Connecting Google or Microsoft shows an error

After the provider sends you back, **Settings → Connections** names the cause:

- **The redirect URI is not registered**: add the redirect URI shown on
  the provider's card (`<base_url>/api/connections/<provider>/callback`) to your OAuth client (Google Cloud Console or the Entra
  app registration).
- **Google rejected the Client ID or Client Secret**: check `google_client_id`
  and `google_client_secret` in `settings.toml` and restart Janus.
- **Calendar and Tasks access are both required**: connect again and leave
  every permission ticked on Google's consent screen.
- **Google did not return a refresh token**: remove Janus at
  [myaccount.google.com/permissions](https://myaccount.google.com/permissions),
  then connect again.
- **Microsoft is not configured**: set `microsoft_client_id` and
  `microsoft_tenant_id` in `settings.toml` and restart Janus.
- **Failed to exchange authorization code for tokens**: anything else. The
  server log has the provider's own error.

Plain-http installs such as the default `http://localhost:8080` can connect
Google, and every user can connect their own Google and Microsoft accounts.

### System tray icon not visible on Wayland (Hyprland/Waybar)

The tray icon needs the optional `desktop` extra
(`uv sync --extra desktop`; see [Optional features](#optional-features)).
Then install the required platform libraries:

=== "Arch Linux"
    ```bash
    sudo pacman -S libayatana-appindicator python-gobject
    ```

=== "Ubuntu / Debian"
    ```bash
    sudo apt install libayatana-appindicator3-1 python3-gi
    ```

=== "Fedora"
    ```bash
    sudo dnf install libayatana-appindicator-gtk3 python3-gobject
    ```

Then ensure your `waybar` config includes the `tray` module.

You can also start without the tray:

```bash
uv run janus start --no-tray
```

On headless/shared-hosting deployments, prefer `--server-mode` instead. It keeps
both the tray and desktop notifications disabled.
