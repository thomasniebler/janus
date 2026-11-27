# Integrations

Each integration below says what it gives Janus, what you need to set, and where. Instance settings (`JANUS_*`) go in the environment or in `settings.toml`; see [Configuration](configuration.md). Everything else is set in the web UI.

Some integrations are **instance-wide**: one credential, set by an admin, that every user's Janus shares. The rest are **per user**.

| Integration | Scope | Where |
|---|---|---|
| Google, Microsoft (Connect) | Per user | Settings → Connections |
| Microsoft 365 for the agent | Instance-wide | `janus microsoft login` |
| GitHub, Linear | Instance-wide, admin | Settings → GitHub, Settings → Linear |
| Jira | Per user | `janus planner add` |
| WhatsApp | Per user | Settings → WhatsApp |
| Telegram | Per user | Settings → Connections |
| Inbound webhooks | Per user | Settings → Triggers |
| Remote MCP servers, A2A agents | Per user | Settings → Tools & Connections |
| Web search | Instance-wide, admin | Settings → Tools & Connections |
| Image generation | Instance-wide, admin | Settings → AI & System |
| Azure resources | Instance-wide, admin | `JANUS_AZURE_ENABLED` |
| Shell, coding agents | Instance-wide, admin | `JANUS_SHELL_TOOL_ENABLED`, `JANUS_CODING_AGENTS_ENABLED` |
| Self-evolution loop | Admin | Settings → Tools & Connections |

What a connected integration lets Janus *do* is governed by its toolboxes and their switches; see [Tools](tools.md#how-permissions-work).

## Google

**Connect Google** gives Janus your Google Calendar, Google Tasks and Gmail. Calendars and tasks appear on the Planner page, Gmail on the Mail page. The same OAuth client also enables **Sign in with Google** and linking a Google account to your Janus login.

An operator sets it up once:

1. In Google Cloud, create an OAuth client of type **Web application**.
2. In the same project, enable the **Google Calendar API** and the **Google Tasks API** (APIs & Services → Library). Gmail is read over IMAP, so the Gmail API is not needed.
3. Add `<base_url>/api/connections/google/callback` as its redirect URI. `<base_url>` is `JANUS_BASE_URL`.
4. Set `JANUS_GOOGLE_CLIENT_ID` and `JANUS_GOOGLE_CLIENT_SECRET`.

Then each user clicks **Connect** on the Google card in Settings → Connections. Janus asks for Calendar, Tasks and full Gmail access (IMAP over XOAUTH2 has no narrower scope). After connecting, add the Gmail mailbox under Settings → Email Accounts; Janus starts reading it right away.

## Microsoft

Janus talks to Microsoft in two separate ways. Both use the same Entra ID app registration: set `JANUS_MICROSOFT_CLIENT_ID` and `JANUS_MICROSOFT_TENANT_ID` (a tenant id, or `common`).

### Connect Microsoft (per user)

Gives Janus your Outlook calendars and your Outlook / Microsoft 365 mail. Calendars appear on the Planner page; mail is polled through Microsoft Graph and appears on the Mail page.

1. In the app registration, add a **Mobile and desktop** (public client) redirect URI of `<base_url>/api/connections/microsoft/callback`.
2. Grant the delegated permissions `Calendars.Read.Shared` and `Mail.Read`.
3. Each user clicks **Connect** on the Microsoft card in Settings → Connections. Then add the Outlook mailbox under Settings → Email Accounts; Janus starts reading it right away.

### Microsoft 365 for the agent (instance-wide)

The `microsoft.*` toolboxes read and send Outlook mail, read the calendar, and read and post Teams chats. They use one instance-wide sign-in, made on the Janus host. It needs more from the app registration than the per-user connection:

1. Add a second **Mobile and desktop** redirect URI: `http://localhost` (the sign-in uses a local loopback).
2. Grant the delegated permissions `User.Read`, `Mail.Read`, `Calendars.Read` and `Chat.Read`, plus `Mail.Send` and `Chat.ReadWrite` for the send tools. Get tenant admin consent if your organization requires it.
3. Sign in on the Janus host:

```bash
janus microsoft login    # opens a browser once
janus microsoft status   # who is signed in, and which Graph permissions the token has
janus microsoft logout
```

The sign-in requests `.default`, so the token carries exactly what the registration has consented: a missing permission shows up as an error on that tool, not as a failed sign-in. An `az login` token does not work here, because it lacks the mail and Teams permissions.

The token cache is stored in `microsoft_token_cache.enc` in the data directory (mode `0600`), encrypted with Janus's Fernet key. These toolboxes are offered to admins only. Sending mail or Teams messages asks for approval in chat and is Off for unattended runs by default.

## GitHub

The `github` toolbox lets Janus inspect and manage pull requests and issues. An admin sets it up in Settings → GitHub:

1. Create a **fine-grained token** on the target repository with **Pull requests: Read and write** and **Contents: Read and write** (needed to merge), plus **Issues: Read and write** to file issues from chat. The self-evolution loop also needs **Checks: Read** and **Commit statuses: Read**. A classic token with the `repo` scope covers all of these.
2. Paste it as **Personal Access Token**.
3. Optionally set a **Default Repository** (`owner/name`).

The token is stored encrypted and shared by every user. The toolbox asks for approval in chat.

## Linear

The `linear` toolbox lets Janus read, create and update Linear issues. An admin sets it up in Settings → Linear:

1. In Linear, open **Settings → API** and create a personal API key.
2. Paste it as **Personal API Key**.
3. Optionally set a **Default Team**: the team key from issue ids (`ENG` for `ENG-123`) or the team UUID.

## Jira

Jira issues are imported into the planner. Add a Jira source from the CLI:

```bash
janus planner add "Work Jira" jira --api-url https://your-site.atlassian.net
```

Janus prompts for the token: either `email:api_token` for Jira Cloud, or a personal access token. See [Planner](planner.md) for how sources sync.

## WhatsApp

Janus reads your WhatsApp chats through Neonize, a client for the WhatsApp multi-device protocol. Each account runs in its own supervised thread and reacts to events as they arrive.

### Install

WhatsApp needs the optional `whatsapp` extra:

```bash
uv sync --extra whatsapp                      # git checkout
uv tool install 'janus-assistant[whatsapp]'   # tool install
```

!!! warning "glibc 2.34 or newer"
    Neonize's native library needs glibc 2.34 or newer. On an older host it cannot load, and a failed load can crash the whole process. Janus probes the import once per process and reports the account as unavailable instead of retrying. The production host does not install the extra.

### Pair an account

In Settings → WhatsApp, open **WhatsApp Setup**:

1. Enter a **Display Name**.
2. Choose **Pair in Janus** and scan the QR code with WhatsApp on your phone (**Linked devices**).
3. Click **Finish**.

Past messages arrive once, shortly after pairing, as many as WhatsApp chooses to send. After that Janus sees only messages that arrive while it is connected; restarting the account does not fetch missed ones.

If you already have a Neonize session database, **Fallback import** takes its path instead.

### Events and toolboxes

New incoming messages emit trigger events:

| Event | When |
|---|---|
| `whatsapp.direct_message_received` | A direct message arrives |
| `whatsapp.group_message_received` | A group message arrives |
| `whatsapp.group_mention` | Someone mentions you in a group |

Messages you send, including those Janus sends for you, emit nothing. The `whatsapp` toolbox reads chats, `whatsapp.send` sends a message (it asks for approval in chat), and `whatsapp.notify_me` lets a trigger run message you.

## Telegram

You link your own Telegram bot to your own private chat. Messages you send there start Telegram triggers, and trigger runs can message you back.

1. In Telegram, create a bot with **@BotFather** and copy its token.
2. In Settings → Connections, paste it as **Bot token** and click **Connect**. Janus checks the token and shows a pairing code.
3. Send `/start <code>` to your bot from a **private** chat. That chat becomes the only one Janus listens and writes to; everyone else is ignored.
4. Click **Send test message** to check the link.

Janus long-polls the bot in a background worker. Then:

- A text message in the linked chat emits `telegram.message_received`.
- The `telegram.notify` toolbox lets trigger and automation runs send you a short text message, at most 20 a day. It is not offered in chat.

You cannot chat with Janus over Telegram; messages only start triggers.

## Inbound webhooks

A webhook is a URL that any service can POST to. Each delivery emits `webhook.received`, which your trigger definitions can react to.

### Create an endpoint

In Settings → Triggers, enter a **Webhook name** and click **Create webhook**. Janus shows two values once:

- the delivery path, `/api/webhooks/in/<key>`; prepend your public base URL;
- the HMAC secret.

Copy both right away. **New secret** issues a fresh secret; **Switch off** pauses the endpoint. You can have up to 20 webhooks.

### Sign deliveries

The body must be JSON, at most 64 KB. Janus accepts one of two signatures:

=== "Janus style (replay-protected)"

    ```text
    X-Janus-Timestamp: <unix seconds>
    X-Janus-Signature: sha256=<hex HMAC-SHA256 of "<timestamp>." + body>
    ```

    The timestamp must be within 5 minutes of the server's clock.

=== "GitHub style"

    ```text
    X-Hub-Signature-256: sha256=<hex HMAC-SHA256 of body>
    ```

    GitHub's own webhooks work unchanged.

For example:

```bash
ts=$(date +%s)
body='{"status":"deployed"}'
sig=$(printf '%s.%s' "$ts" "$body" | openssl dgst -sha256 -hmac "$SECRET" -hex | sed 's/^.* //')
curl -X POST "https://janus.example.com/api/webhooks/in/$KEY" \
  -H "Content-Type: application/json" \
  -H "X-Janus-Timestamp: $ts" -H "X-Janus-Signature: sha256=$sig" \
  -d "$body"
```

A valid delivery answers `202`. The run sees the webhook name, the sender's event header (`X-GitHub-Event`, `X-GitLab-Event`, `X-Event-Type` or `X-Janus-Event`), the time and the body. The same delivery sent twice starts at most one run per definition, and each endpoint accepts at most 60 deliveries a minute. The body is untrusted: narrow it with a payload filter on the trigger definition. See [Runtime triggers](runtime-triggers.md).

## Remote MCP servers

Janus is an MCP **client**: it can call tools on remote MCP servers over HTTP. It is not an MCP server itself.

In Settings → Tools & Connections, click **Add MCP server**:

1. Enter a **Provider name** and the **HTTP MCP endpoint**. The endpoint must use HTTPS and resolve to a public address; plain HTTP is accepted only on loopback.
2. Choose how to authenticate:
    - **OAuth (PKCE):** fill in the authorization endpoint, token endpoint, client id and scopes, save, then click **Authorize**. Register `<base_url>/api/tools/oauth/callback` as the redirect URI with the provider.
    - **Bearer token:** paste a fixed token. Saving a token replaces the server's OAuth connection.
3. Click **Discover** to list the server's tools. You can switch each tool off on its own.

If a server shows **Token rejected**, save a new token and click **Discover** again. Changing the endpoint drops the saved token unless you enter a new one at the same time.

Each discovered tool becomes a toolbox named `mcp.<server>.<tool>`. Remote tools are offered **in chat only**. A tool the server marks read-only runs without asking; every other tool asks for approval of its exact arguments. Trigger runs get remote tools only when you set unattended runs to **Allow everything**.

## A2A remote agents

Janus can hand a question to another agent that speaks the Agent2Agent (A2A) protocol over JSON-RPC.

In Settings → Tools & Connections, under remote agents, click **Add remote agent**:

1. Enter a **Name** and the **Agent URL** (the base address that serves `/.well-known/agent-card.json`), plus an optional **Bearer token**.
2. Click **Add and fetch card**. Janus fetches the agent card and shows the skills the agent advertises.
3. Click **Switch on**. New agents start switched off.

Each agent becomes a toolbox `a2a.<id>`, offered in chat only, and each call asks for approval of the exact prompt. The token is sent only to the agent URL's own origin. **Refresh card** fetches the card again. Trigger runs get remote agents only under **Allow everything**.

## Web search and web fetch

**Web search** lets Janus search the public web. An admin picks a **Provider** in Settings → Tools & Connections:

| Provider | Setting |
|---|---|
| SearXNG (self-hosted) | **SearXNG base URL**; the instance must have JSON output enabled |
| Brave Search API | **Brave Search API key** |

Only the query Janus writes leaves the machine, never your mail or chat. Results are treated as untrusted content. The `web_search` toolbox is on for trigger runs unless you switch it Off.

**Web fetch** (`web_fetch`) reads one public web page by URL and needs no setup. It refuses private and local addresses. In chat it asks for approval; for unattended runs it is Off by default.

## Azure resources

The `azure.resources` toolbox lists Azure subscriptions, resource groups and resources. It is read-only and offered to admins only.

1. Set `JANUS_AZURE_ENABLED=true`.
2. Give the host an Azure identity: a service principal in `AZURE_CLIENT_ID`, `AZURE_TENANT_ID` and `AZURE_CLIENT_SECRET`, a managed identity, or an `az login` session.

Janus uses the standard Azure credential chain and stores nothing itself.

## Shell and coding agents

Both are meant for a local, personal install. An operator turns them on with environment settings; then only admins get them.

### Shell

The `shell` toolbox runs one command on the Janus host.

| Setting | Default | Meaning |
|---|---|---|
| `JANUS_SHELL_TOOL_ENABLED` | `false` | Offer the tool at all |
| `JANUS_SHELL_TOOL_WORKING_DIR` | home | Working directory |
| `JANUS_SHELL_TOOL_TIMEOUT_SECONDS` | `30` | Wall-clock limit |
| `JANUS_SHELL_TOOL_OUTPUT_MAX_BYTES` | `16000` | Output cap |
| `JANUS_SHELL_TOOL_ALLOW_SHELL_SYNTAX` | `false` | Run through `/bin/sh -c` (pipes, redirection) |

By default the command is split into arguments and run without a shell, with a scrubbed environment and no stdin.

### Coding agents

The `coding_agents` toolbox hands a focused task to a coding-agent CLI you already installed and logged in to: Claude Code (`claude`) or the OpenAI Codex CLI (`codex`). Only agents found on `PATH` are offered.

| Setting | Default | Meaning |
|---|---|---|
| `JANUS_CODING_AGENTS_ENABLED` | `false` | Offer the tool at all |
| `JANUS_CODING_AGENTS_WORKING_DIR` | home | Working directory |
| `JANUS_CODING_AGENTS_TIMEOUT_SECONDS` | `300` | Wall-clock limit |
| `JANUS_CODING_AGENTS_OUTPUT_MAX_BYTES` | `50000` | Output cap |
| `JANUS_CODING_AGENTS_ALLOW_EDITS` | `false` | Use each agent's edit mode instead of read-only |
| `JANUS_CODING_AGENTS_ENV_PASSTHROUGH` | `[]` | Extra environment variables to pass, such as an API key |
| `JANUS_CODING_AGENTS_COMMANDS` | `{}` | Adjust a preset's command; it needs exactly one `{prompt}` argument |

Both toolboxes ask for approval of the exact command or prompt in chat, and are Off for unattended runs until you switch them on.

## Image generation

Image generation stays off until an admin picks an **Image provider** and an **Image model** in Settings → AI & System. The providers are OpenAI, Gemini and OpenRouter. Each reuses the API key stored for the chat provider of the same name, so there is no second credential.

The `images` toolbox is chat-only. Trigger runs get it only under **Allow everything**.

## Self-evolution loop

The self-evolution loop lets Janus work on its own code without a human approving each step:

1. It files a Linear issue for a gap it noticed.
2. It starts an implementation job in Claude Code through a Routine API trigger.
3. It merges the resulting pull request, then the release PR.
4. It runs a production self-check after the release, and opens a revert PR if the check fails.

It is opt-in and admin-only. It needs the GitHub integration and the Linear integration with a **Default Team**. Write the Routine's prompt so it carries out the fire payload Janus sends (for example "Implement the Linear issue described in the routine fire payload, following its rules"), and keep the payload's instruction to name the session and issue in the PR description: Janus merges only PRs that name a session it started. In Settings → Tools & Connections, the admin sets:

- **Claude Code Routine trigger id** and **Routine API token**;
- **GitHub repository** (empty uses the GitHub default);
- **Production base URL for self-checks**;
- the switch itself, off by default.

Gates enforced in code, whatever the prompt says:

- **Daily caps** over a rolling day: 5 issues, and 3 each of jobs, feature merges, release merges, self-checks and revert PRs.
- **Merge gates:** the PR is open, not a draft, from a branch of the same repository, mergeable, and every check passed. It must come from a job the loop started, a minor or patch Dependabot bump, or a revert the loop opened.
- **Scope fence:** a self-merged PR may not touch authentication, the tool policy, the prompt-injection guard, CI and release automation, migrations, or the loop's own code.
- **Audit trail:** every attempt, refusal and outcome is recorded and shown under **Recent actions**.

The `self_evolution` toolbox follows this switch rather than an Unattended switch.
