# Getting Started with Janus

Janus is your personal AI assistant that monitors your communication channels and surfaces what's important.

## Feature highlights

- **Priority-aware inbox monitoring** keeps urgent mail separate from digest-only traffic.
- **Planner-aware briefings and chat** combine mail, tasks, and calendar context.
- **Triggers for everything outside chat**: the morning briefing, afternoon digest, per-email mail
  processing, and your own scheduled or event-driven automations all run as editable triggers under
  **Settings → Triggers**, speak as your Janus character, and record every tool they used or were
  refused. See [Runtime triggers](runtime-triggers.md).
- **Agent Tasks**: every trigger run, automation run, and delegated task with its plan steps and the
  tools it used or was refused, under **Agent Tasks** in the navigation. See
  [Runtime triggers](runtime-triggers.md#plan-steps-and-tool-trace-the-agent-tasks-page).
- **Agent memory**: durable facts Janus remembers about you, under **Memory & Knowledge -> Agent Memory**. Add,
  search, edit, or delete a fact there, tick *Show archived* to see facts that compaction merged away, and
  use **Compact Memory** to have the utility model merge overlapping facts.
- **Structured topic tracking** groups recurring email threads into durable topics that appear under
  **Memory & Knowledge -> Topics**. A newly spotted topic is only *watched* at first; it starts
  showing up in briefings and digests once a few emails mention it, or as soon as you confirm it.
- **Documents and ingested sources**: Janus reads your mail, attachments, chats, calendar, tasks, and
  uploaded files into its knowledge store; see and upload them under **Memory & Knowledge -> Documents**.
  See [Configuration](configuration.md#documents-and-ingested-sources).
- **Knowledge graph** (opt-in): Janus records the people, organizations, projects, and places in that
  text and how they relate, within a monthly token budget, under **Memory & Knowledge -> Knowledge**, and
  searches all of it (words, meaning, and the graph) with cited sources, in the tab and from chat.
  See [Configuration](configuration.md#knowledge-extraction).
- **Wiki** (opt-in): a cited, editable page for every tracked topic and frequently mentioned entity;
  Janus proposes updates to pages you edited instead of overwriting them. See
  [Configuration](configuration.md#wiki).
- **Self-evolution** (opt-in, admin only, off by default): Janus files Linear issues about its own
  gaps, starts Claude Code implementation jobs, merges the resulting pull requests and the release,
  and checks production afterwards -- with no human approval step, stopped only by gates in code
  (green CI, a scope fence, daily caps). See [Configuration](configuration.md#self-evolution-loop).
- **Ask Janus about Janus**: in chat, Janus reads this documentation to answer questions like "how do I
  connect my calendar?", "what does the Unattended switch do?", "why did my trigger say BLOCKED?", or
  "what can you do?", and tells you which page it used. It reads the user guides, feature specs, and
  technical specs, but not the archived review reports. See [Tools and permissions](tools.md).
- **Todo Chat companion sidebar** provides a resident Hyprland task workspace backed by Janus's local
  planner and chat capabilities. See the [Todo Chat companion guide](feature-specs/todo-chat-local-planner.md)
  for the Janus-side behavior and `modules/todo-chat/README.md` in the repository for packaging
  and desktop integration.

## Technical specifications

Need deployment, architecture, or security details beyond the operator guides on this page? Open
[Technical Spec Index](tech-spec/index.md) from the in-app documentation sidebar to browse the
focused Janus subsystem specs.

!!! tip "New to Janus?"
    Follow the [Quickstart](quickstart.md) for a step-by-step guide from installation through your first
    monitored email account.

## Installation

Ensure you have `uv` installed. Then, from the project root:

```bash
uv sync
```

WhatsApp, the system tray icon, and PDF/Office document parsing are optional
extras (`uv sync --extra whatsapp --extra desktop --extra documents`); see
[Optional features](quickstart.md#optional-features).

## Setup Process

### 1. Configure and start

Before adding accounts, you must configure a secret key and start the server. See [Quickstart](quickstart.md) for details.

```bash
uv run janus config init    # create ~/.config/janus/settings.toml
# edit the file and set secret_key
uv run janus start          # start the server, then complete first-run setup in the browser or via CLI
```

### 2. Add an Email Account

Once the server is running and you've created your admin account via the web UI, you can add accounts through the dashboard or CLI:

```bash
uv run janus account add "Personal" "your.email@gmail.com" "your.email@gmail.com"
```
*Note: For Gmail, use an [App Password](https://support.google.com/accounts/answer/185833).*

#### Configuring Other Providers

If you use a provider other than Gmail, you must specify the IMAP server and port:

- **iCloud**: `--imap-server imap.mail.me.com --imap-port 993`
- **Outlook/Hotmail**: `--imap-server imap-mail.outlook.com --imap-port 993`
- **Custom**: Check your provider's IMAP documentation.

Example for iCloud:
```bash
uv run janus account add "iCloud" "user@icloud.com" "user" --imap-server imap.mail.me.com --imap-port 993
```

### 3. Configure Rules

Janus uses rules to decide what is "Important" (notifies you immediately) and what goes into the "Digest". Each user's rules only apply to mail arriving on that user's own accounts.

```bash
# Notify me about emails from my boss
uv run janus rule add "Boss" "boss@company.com" --field sender

# Notify me about urgent subjects
uv run janus rule add "Urgent" "Urgent|ASAP|Important" --field subject
```

You can also manage rules by asking Janus in chat directly (for example,
"always send mail from newsletter@example.com to spam"), and it can surface and
act on learned rule suggestions from the relabel-learning queue. This needs a
connected mail account. Unattended trigger runs may not change rules unless
you allow it on Settings → Tools & Connections.

### Troubleshooting: System Tray on Wayland/Hyprland

The tray icon needs the optional `desktop` extra (`uv sync --extra desktop`). If you are using Wayland (e.g., Hyprland with Waybar) and the system tray icon is not visible, ensure you have the following installed:

1. **System Libraries**:
    - **Arch**: `libayatana-appindicator python-gobject`
    - **Ubuntu/Debian**: `libayatana-appindicator3-1 python3-gi`
    - **Fedora**: `libayatana-appindicator-gtk3 python3-gobject`

2. **Waybar Config**: Ensure your `waybar` config includes the `tray` module.

Janus will automatically attempt to use the `appindicator` backend when it detects a Wayland session.

## CLI Reference

- `janus account list`: View configured accounts.
- `janus rule list`: View current prioritization rules.
- `janus --help`: See all available commands.
