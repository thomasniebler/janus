# Configuration

Janus reads configuration from three sources, listed here from **highest to lowest** priority:

1. **Environment variables** — always win.
2. **`.env` file** — loaded from the current working directory.
3. **`~/.config/janus/settings.toml`** — the user configuration file described on this page.

Todo Chat and other desktop companions can also read shared desktop settings
from `~/.config/janus/config.toml`. That file is separate from
`settings.toml` and is only used by companion apps, not by the Janus server
itself.

## User configuration file

The file is located at `~/.config/janus/settings.toml` (XDG-compliant: if
`$XDG_CONFIG_HOME` is set, Janus uses `$XDG_CONFIG_HOME/janus/settings.toml`).

The CLI also stores its cached login token in the same directory as
`~/.config/janus/token.json` (or `$XDG_CONFIG_HOME/janus/token.json`). On the
first CLI use after upgrading, Janus moves the legacy `~/.janus_token` cache to
this location, so existing CLI sessions continue to work. The cache is written
with owner-only file permissions; do not copy it between users or hosts.

The file is **optional**. If it does not exist, Janus starts normally using
environment variables and defaults.

## Desktop companion configuration

The optional desktop-companion config lives at `~/.config/janus/config.toml`
(or `$XDG_CONFIG_HOME/janus/config.toml` when XDG overrides are in use). The
Todo Chat installer creates this file when needed.

Use it for shared companion settings such as the Janus base URL:

```toml
base_url = "https://janus.example.com"
```

`base_url` is global for Janus desktop companions. Chat in Todo Chat always
speaks as your Janus character; an old `[todo-chat] agent_id` entry from the
removed agent definitions is ignored.

For production/shared-hosting deployments, prefer explicit absolute paths that
live on persistent storage rather than relying on the current working directory.

### Initialise the file

```bash
janus config init
```

This creates `~/.config/janus/settings.toml` with a commented-out template.
If the file already exists the command prints a message and exits without
overwriting anything.

You can also run it via the convenience task:

```bash
task setup
```

### Supported keys

| Key | Type | Default | Description |
|---|---|---|---|
| `database_url` | string | `sqlite:////home/you/.local/share/janus/janus.db` | SQLAlchemy connection string |
| `fernet_key_path` | string | `/home/you/.local/share/janus/.janus_key` | Absolute path of the Fernet key file used for encrypted credentials |
| `log_file_path` | string | `/home/you/.local/state/janus/janus.log` | Absolute path of the runtime log file |
| `secret_key` | string | *(required)* | JWT signing key — maps to `JANUS_SECRET_KEY` |
| `server_mode` | bool | `false` | Disables desktop notifications and tray behavior for headless deployments |
| `host` | string | `127.0.0.1` | Host interface the API server binds to |
| `port` | int | `8080` | Port the API server listens on |
| `base_url` | string | `http://localhost:8080` | Public base URL used for generated redirect URLs (OAuth, links) — must match `host` and `port` |
| `allowed_origins` | list[string] | `["http://localhost:5173", "http://127.0.0.1:5173"]` | Browser origins allowed for CORS and WebSocket connections. Must include the URL where the dashboard is served. |
| `allowed_hosts` | list[string] | `["localhost", "127.0.0.1", "[::1]"]` | Explicit Host header allowlist. Include every hostname or `host:port` that Janus should accept. |
| `trusted_proxy_hops` | int | `0` | Number of trusted reverse-proxy hops in front of Janus. Set to `1` when a single TLS-terminating proxy forwards `X-Forwarded-*` headers. |
| `public_api_docs_enabled` | bool | `false` | Expose FastAPI interactive API docs (`/docs`, `/redoc`) publicly. Leave disabled unless needed. |
| `public_setup_enabled` | bool | `false` | Enables the public first-run setup endpoints and browser setup page |
| `runtime_trigger_worker_enabled` | bool | `true` | Runs the background trigger worker: scheduled and event triggers, including the built-in morning briefing, afternoon digest, and per-email Mail processing. When `false`, triggers stay queued and new mail uses the older built-in classifier. See [Runtime triggers](runtime-triggers.md). |
| `runtime_trigger_max_attempts` | int | `3` | Attempts per trigger run before it becomes `exhausted` |
| `runtime_trigger_retry_backoff_seconds` | float | `0` | Base delay for exponential retry backoff between failed attempts (`0` = retry on the next poll) |
| `chat_tool_approval_timeout_seconds` | float | `300` | How long a chat approval card waits for Approve / Deny before the call counts as declined. See [Tools and permissions](tools.md#how-permissions-work). |
| `tool_selection_enabled` | bool | `true` | Before each chat message, ad-hoc trigger run, or delegated task, let the utility model pick which of the authorized toolboxes the task needs, so the model is offered fewer tools. It can only narrow what your settings allow; when it fails, every authorized tool is offered. See [Tools and permissions](tools.md#how-janus-picks-tools). |
| `tool_selection_timeout_seconds` | float | `10` | How long the tool selection may take before every authorized tool is offered instead; also the utility model's request timeout for that call (no retries). |
| `chat_ui_action_timeout_seconds` | float | `10` | How long Janus waits for the web page to confirm a page action (navigate, filter, open, highlight, ...) or to answer a page-introspection request (outline, element styles) before telling the model it failed. See [Page actions](tools.md#page-actions) and [Page introspection](tools.md#page-introspection). |
| `image_storage_dir` | string | `/home/you/.local/share/janus/images` | Absolute directory for images Janus generates in chat, one folder per user. See [Image generation](#image-generation). |
| `image_storage_limit_mb` | int | `200` | Per-user total size of generated images, in MB; at the limit Janus refuses new images until conversations with images are deleted |
| `shell_tool_enabled` | bool | `false` | Lets administrators' Janus run shell commands on this machine (each approved in chat). Local installs only. See [Shell tool](#shell-tool). |
| `shell_tool_working_dir` | path | home directory | Directory shell commands run in |
| `shell_tool_timeout_seconds` | float | `30` | Seconds a shell command may run before it and everything it started are stopped (max 600) |
| `shell_tool_output_max_bytes` | int | `16000` | Output (stdout + stderr) above this many bytes stops the command |
| `shell_tool_allow_shell_syntax` | bool | `false` | Run commands through `/bin/sh -c`, so pipes, redirection, `&&`, globs, and `$VARIABLES` work |
| `auto_update_enabled` | bool | `true` | Install each new GitHub release automatically and restart. Applies to a git checkout that is clean, on `main` or a release tag, and run from its own `.venv`, and to a `uv tool` or `pipx` install of the release wheel. See [Automatic updates](#automatic-updates). |
| `auto_update_repository` | string | `thomasniebler/janus` | GitHub repository (`owner/name`) whose releases are installed |
| `auto_update_interval_minutes` | int | `60` | Minutes between release checks (at least 15) |

## Appearance: choosing an app shell

Janus ships three interchangeable looks, selectable per user under
**Settings → Appearance**:

| Option | Look |
| ------ | ---- |
| **Classic (UIv1)** | The original dark shell: blurred wallpaper backdrop, translucent glass panels, and a left sidebar for navigation. This is the default. |
| **Terminal (UIv2)** | A high-contrast console alternative: a near-black CRT backdrop with a faint grid and scanlines, hard-edged panels, monospaced uppercase chrome, acid-lime phosphor accents, a horizontal command bar, and a chat drawer you can hide. |
| **Rusty Console (UIv3)** | A weathered hacker terminal: a warm almost-black-red backdrop stained with corrosion, film grain and a burned-in vignette, rust-red rules around every panel and navigation key, typewriter lettering, a slow roll band drifting down the screen, a horizontal command bar, and a chat drawer you can hide. |

Every option shows exactly the same pages, widgets, and data — only the chrome
around them changes. The choice is stored on your account (not in
`settings.toml`), so it follows you across browsers and devices, and switching
takes effect immediately without a reload.

### Getting back to the classic UI

If an alternate shell ever misbehaves, there are three ways back:

1. **Settings → Appearance → Classic (UIv1)** — the normal way.
2. **Add `?ui=v1` to the URL** of any page (for example
   `http://localhost:8080/settings?ui=v1`). This forces the classic shell for
   that page load only and ignores both the saved preference and the local
   cache.
3. **The crash screen.** If the dashboard fails to render at all, the error
   screen offers a **Reset to classic UI (v1)** button that clears the cached
   preference and reloads into the classic shell.

## System Events

**Settings → System Events** shows a real-time log of background activity (mail
sync, planner sync, LLM probes, and other internal processing) as it happens.

Use the filter controls to narrow the log:

- **Type** — `Info`, `Success`, `Warning`, or `Error`.
- **Source** — an exact match against the component that logged the event; the
  dropdown lists only sources that currently have events.
- **From** / **To** — an inclusive start and exclusive end timestamp, in your
  browser's local time zone. Picking a "From" at or after "To" is rejected.
- **Clear filters** resets all four controls at once.

Results are paginated 50 events per page, newest first, with a "*X–Y of N*"
count and **Previous**/**Next** controls. Changing any filter jumps back to
the first page.

The page live-updates only while you're on page 1 (polling every 10 seconds).
Once you page forward to older events, auto-refresh stops so newly arriving
events don't shift rows out from under you; browse back to page 1 to resume
live updates.

## Stats

**Settings → Stats** shows what Janus has been doing over the last **7**,
**30**, or **90** days, counted per day in your browser's time zone. Only your
own data is counted.

- **Mail** — new mail per day, split into important, digest, ignore, and spam.
- **Triggers** — runs per day by outcome (completed, failed, cancelled,
  queued/running), the overall success rate (completed out of finished runs),
  and a table per trigger definition with its runs, success rate, failures,
  average run time, and last run. Runs without a definition are grouped as
  *Ad-hoc runs*.
- **Memory & knowledge** — active and archived memory facts, new facts per
  day, tracked topics, wiki pages, and knowledge sources.
- **Messaging** — WhatsApp messages received and sent, and your chat
  messages and Janus's replies, per day.

Hover a day to see its numbers, or use **Show table** under any chart for the
full figures. The page refreshes once a minute.

## Dashboard LLM provider settings

Janus LLM provider credentials and model settings are configured in the dashboard under
**Settings → AI & System → AI Configuration** (not in `settings.toml`).

Supported providers:

- **Ollama / Llama** — local runtime or Ollama-hosted endpoint
- **OpenAI**
- **OpenRouter** — OpenAI-compatible routing endpoint
- **Gemini**
- **Anthropic** — Claude models through an Anthropic API key
- **GitHub Models** — the models in [GitHub Models](https://github.com/marketplace/models),
  authenticated with a GitHub token
- **Azure OpenAI** — a model deployment in Azure OpenAI or Azure AI Foundry,
  signed in with your Azure identity (Entra ID) or an API key

Some models only work with OpenAI's Responses API: the *pro* models (for example
`gpt-5-pro`), `gpt-5.6-sol`, and any model whose name contains `codex`. Janus
reaches **GitHub Models** through the Chat Completions API only, so these models
are not offered there and fail with a message saying so. With **OpenAI** and a
custom base URL (a proxy), Janus uses Chat Completions for every other model and
leaves these to the proxy. If the proxy does not support them, its error is shown.
OpenAI itself (no base URL, or `https://api.openai.com/v1`) and **OpenRouter**
are not restricted.

When **OpenRouter** is selected, Janus supports:

- **Model** (default: `openrouter/auto`)
- **API key**
- **Base URL** (default: `https://openrouter.ai/api/v1`)

If you clear the OpenRouter base URL in the UI, Janus safely falls back to
`https://openrouter.ai/api/v1` at runtime.

When **Anthropic** is selected, Janus supports:

- **Model** (default: `claude-opus-5`)
- **API key** — stored encrypted at rest and never returned by the API
- **Base URL** (optional) — leave empty to use `https://api.anthropic.com`; set
  it only for a proxy or gateway that speaks the Anthropic Messages API

When **GitHub Models** is selected, Janus supports:

- **Model** (default: `openai/gpt-4.1-mini`) — model ids have the form
  `publisher/model`, for example `openai/gpt-4.1` or `meta/llama-4-scout-17b-16e-instruct`
- **GitHub token** — a fine-grained personal access token with the
  **Models: read** permission (`models:read`), or the output of `gh auth token`
  if you use the GitHub CLI. It is stored encrypted at rest and never returned
  by the API.
- **Base URL** (optional) — leave empty to use `https://models.github.ai/inference`

GitHub Models has rate limits that depend on your GitHub plan; it suits
personal use and trying models more than heavy background work.

When **Azure OpenAI** is selected, Janus supports:

- **Endpoint** — your resource endpoint, for example
  `https://my-resource.openai.azure.com` or
  `https://my-foundry.cognitiveservices.azure.com`. It must start with `https://`
  and be only the resource root: Janus adds the `/openai/v1` (or, with a dated
  API version, `/openai/deployments/...`) part itself, so a URL copied from the portal that ends in `/openai/v1/` or a full
  *Target URI* is refused with the root to use instead.
- **Deployment name** — the name of the model deployment, for example
  `gpt-4.1-mini` or `Kimi-K2.6`. Azure calls models by deployment, so this is
  also the model name Janus shows and the value to use as the model for the
  chat and utility model overrides. Once the endpoint and credential are
  filled in, the field lists the resource's deployments as
  *deployment (model)*. If they cannot be listed, it turns into a plain text
  field with the reason (see [Choosing a model](#choosing-a-model)).
- **API version** (default: `preview`). With `preview` (or `latest`, `v1`, or an
  empty field) Janus uses the resource's v1 endpoint (`<endpoint>/openai/v1`), which
  offers both the Chat Completions and the Responses API. Janus picks the right
  one per model, using the model behind each deployment (so a gpt-6 model
  deployed under any name counts): gpt-6 deployments such as `gpt-6-luna` use the Responses API
  when chat uses tools, because Azure only accepts tools together with reasoning
  there. Models that only offer the Responses API (the *pro* models,
  `gpt-5.6-sol`, anything containing `codex`) work too. Other deployments, for
  example `Kimi-K2.6`, use Chat Completions.
  Reading the model behind a deployment needs the same access that lists
  deployments. If Janus cannot read it (for example a missing permission), it
  routes by the deployment name instead and retries after a few minutes. A
  gpt-6 model deployed under a custom name then runs on Chat Completions and
  fails when chat uses tools; give the identity or key read access to the
  resource's deployments, or name the deployment after the model.
  A dated API version (for example `2024-10-21` or `2025-04-01-preview`)
  switches to the older per-deployment route, where Janus always uses Chat
  Completions. That route cannot run gpt-6 chat with tools, and it accepts
  Responses-only models only with a `-preview` date. Use it only if your
  resource has no v1 endpoint. Installs that still had the old default
  `2024-10-21` were moved to `preview` on upgrade.
- **Auth mode**:
    - **Entra ID** (default) — Janus signs in as the Azure identity of the
      machine it runs on, with no secret stored in Janus. On your own computer,
      run `az login` first; on a server, give the host a managed identity. The
      identity needs the **Cognitive Services OpenAI User** role (or a role with
      the same data access) on the resource. Janus gets and renews tokens
      automatically. If no identity is available, saving still works, but
      **Test Selected Backend** and every AI call fail with a message that tells
      you to run `az login` or switch to API key auth. Janus only sends the
      identity's token to Azure OpenAI hosts (`*.openai.azure.com`,
      `*.cognitiveservices.azure.com`, `*.services.ai.azure.com`); for any other
      endpoint, use **API key** auth.
    - **API key** — Janus sends a key from the resource's **Keys and Endpoint**
      page. The key is stored encrypted at rest and never returned by the API.

### Choosing a model

For every provider, the model field is a searchable dropdown filled with the
models that provider actually offers:

- **OpenAI** — chat models from your account (embedding, audio, image, and
  moderation models are hidden)
- **OpenRouter** — the public OpenRouter catalog
- **Gemini** — models that support text generation
- **Anthropic** — Claude models available to your API key
- **Ollama** — the models pulled on the configured Ollama runtime
- **GitHub Models** — the GitHub Models catalog (needs your GitHub token)

- **Azure OpenAI** — the resource's chat deployments that are ready to use
  (embedding, audio, and image deployments are hidden), listed with the selected
  auth mode: the Azure identity of the Janus host in **Entra ID** mode, the API
  key in **API key** mode. The list falls back to typing the deployment name when
  the identity or key may not read the resource's deployments (*HTTP 403,
  PermissionDenied*; for example, a role with access to only the model
  endpoints), when no Azure sign-in is available on the host (run
  `az login`, or give the host a managed identity; after a failed sign-in Janus
  waits about a minute before trying again), or, in Entra ID mode, while the
  endpoint is not yet a complete Azure OpenAI host name.

The list uses the values currently typed into the form, so you can paste a new
API key or base URL and pick a model before saving. Type to filter the list, or
type any model id that is not listed — the field always accepts free text.

If the list cannot be loaded (missing or rejected key, unreachable endpoint,
or you are not an administrator), the field turns back into a plain text input
with the note *"Could not load model list — enter a model name manually."*
Janus caches each list for about five minutes.

For safety, a stored API key is only sent to the base URL it was saved with. If
you change the base URL, enter the API key again to load models from, or test,
the new endpoint. For Azure OpenAI the endpoint plays the role of the base URL;
in Entra ID mode no key is involved, so the deployments of a newly typed Azure
endpoint are listed right away.

For OpenAI, OpenRouter, GitHub Models, and Azure OpenAI, Janus does not set a
sampling temperature, so these models run at their provider default. Reasoning
models (OpenAI's o-series and gpt-5 or later, and Azure deployments of them)
only accept that default and reject any other value. Anthropic never receives a
temperature either.

Use **Test Selected Backend** to verify credentials before you click **Save Backend Settings**.

The AI backend is shared by the whole Janus instance, so only an administrator
can test or save it. Other users see the current choice read-only. The same
holds for **Settings → GitHub**, **Settings → Linear**, the **Web Search** card,
and **Settings → Mail Ingestion**.

### Different models for chat, helpers, and triggers

The backend above is Janus's **default model**. Below it, the **Models per
surface** card lets two surfaces use something else:

- **Chat** — the model that answers you in chat.
- **Utility** — the model for Janus's small helper calls: conversation titles,
  deciding which chat facts to remember, and memory compaction. A small, cheap
  model is usually enough.

Each trigger picks its own model in its editor on **Settings → Triggers**
(for example a cheap local model for mail processing and a stronger one for
the morning briefing). Work that a chat or trigger run delegates uses the
same model as that run.

For each surface, choose **Default model** to follow the backend above, or a
provider and optionally a model (leave the model empty to use the model
configured for that provider). A choice reuses that provider's API key and
base URL from the AI Configuration form, so configure the provider there
first; the choices above do not need it to be the selected backend.

!!! warning "No silent fallback"
    If a chosen model cannot be used — for example no API key is stored for
    that provider — Janus does not quietly use the default model instead.
    A trigger run fails with `exhausted` and an error naming the model, chat
    shows the error and disconnects, and a broken utility model is recorded
    in the event log while titles and memory suggestions are skipped.

!!! note "Cloud models see your data"
    Choosing a cloud provider for a surface sends that surface's data to the
    provider even if your default backend is local Ollama: your chat, the
    trigger's context (for Mail processing, your emails), or your memory
    facts. The form shows a cloud hint next to every such choice. With the
    optional [model-based prompt-injection check](tools.md#how-permissions-work)
    on, the utility model also sees outside text that looks borderline.

### Semantic search for memory and topics

By default, memory and topic search match keywords: a search for "tea" finds
"green tea" but not "beverage". The **Semantic search** card on
**Settings → AI & System** adds an embedding model, so search also finds
entries by meaning. It combines both: an entry that matches by keyword and by
meaning ranks first.

1. Pick an **Embedding provider**: Ollama (local), OpenAI, or Google Gemini.
   It uses the key and base URL you entered for that provider in the AI
   Configuration form.
2. Leave **Embedding model** empty for the suggested model
   (`nomic-embed-text` for Ollama, run `ollama pull nomic-embed-text` first;
   `text-embedding-3-small` for OpenAI; `gemini-embedding-001` for Gemini),
   or enter another embedding model.
3. **Advanced → Minimum similarity** (default `0.5`) is an expert setting.
   Janus uses meaning to *order* matches, not to decide whether something
   matches. Filters such as status (and, for the agent, the fact's origin)
   are applied first, and at most five results per search come from meaning
   alone. This floor only drops meaning-only matches below it. Similarity
   scores differ between models and small differences mean little, so leave
   it alone unless you have measured your model.

Janus embeds your existing facts and topics in the background (a batch every
minute) and each new or edited one right away. Changing the model re-embeds
everything; until that finishes, search still finds entries by keyword. Run
`uv run janus knowledge reembed` to do it all at once. **Off** goes back to
keyword search.

!!! note "Cloud embeddings see your memory"
    With OpenAI or Gemini, the text of your memory facts, your topics, every
    memory or topic search, and the text of every ingested source (your mail,
    attachments, chats, WhatsApp chats, calendar, tasks, and uploads; see
    below) is sent to that provider. Ollama keeps it on your machine.

Semantic search needs a Python whose `sqlite3` module can load extensions.
If yours cannot, Janus keeps working with keyword search, logs a warning at
startup, and **Status** shows why under **Knowledge search**. On PostgreSQL,
search matches substrings for now.

### Image generation

Janus can draw pictures in chat when you ask for one (see
[Image generation](tools.md#image-generation)). It uses a cloud image model;
there is no local image backend, and Ollama and Anthropic cannot generate
images. It is **off** until an administrator turns it on in the
**Image generation** card on **Settings → AI & System**:

1. Pick an **Image provider**: OpenAI, Google Gemini, or OpenRouter. It uses
   the API key (and, for OpenAI and OpenRouter, the base URL) you entered for
   that provider in the AI Configuration form; the card warns when no key is
   stored yet. The provider does not need to be your chat backend.
2. Leave **Image model** empty for the default, or enter another image model:

    | Provider | Default model | Other examples |
    | --- | --- | --- |
    | OpenAI | `gpt-image-1` | `dall-e-3` |
    | Google Gemini | `gemini-2.5-flash-image` | another Gemini model that can output images |
    | OpenRouter | `google/gemini-2.5-flash-image` | any OpenRouter model whose output includes images |

3. Click **Save image generation**. Janus offers the tool in the next chat
   message you send.

Janus asks for a square (default), landscape, or portrait image; OpenAI gets
the matching size (`1024x1024`, `1536x1024`, `1024x1536` for `gpt-image-1`),
Gemini and OpenRouter the matching aspect ratio (`1:1`, `16:9`, `9:16`).

Images are stored as files under `image_storage_dir` (default
`~/.local/share/janus/images`, one folder per user) and deleted with their
conversation. `image_storage_limit_mb` (default `200`) caps each user's
total; back up the directory together with the database if you want to keep
the images.

!!! note "Image prompts leave your machine"
    The prompt Janus writes for each image is sent to the chosen provider,
    and each image is a paid request on that provider's account. The event
    log records the provider, model, and prompt length of every image, never
    the prompt.

### Documents and ingested sources

Janus reads your mail (except mail filed as spam or ignored), document
attachments, chats, WhatsApp chats, calendar events, and tasks into its
knowledge store, a few dozen items per minute in the background. Open
**Memory & Knowledge → Documents** to see everything it has read, why an item
was skipped or failed, and to upload a file yourself: PDF, Word, Excel,
PowerPoint, text, Markdown, CSV, HTML, or a saved mail, up to 10 MB. Only the
extracted text is kept, not the file. Every format except text, Markdown, and
CSV needs the optional `documents` extra (`uv sync --extra documents`); without
it, those files are listed as skipped. The extra needs glibc 2.27 or newer, so
hosts with an older glibc, such as the Uberspace host, run without it.
Attachments of mail Janus fetched before this feature existed cannot be
recovered.

After upgrading, `uv run janus knowledge backfill` reads everything at once
instead of waiting for the background worker. With an embedding model
configured, the text is also embedded. Searching it from chat comes in a later
release.

### Knowledge extraction

Under **Memory & Knowledge → Knowledge**, you can let Janus read what it has
ingested and record the people, organizations, projects, places, products,
and concepts in it, and how they relate (for example, *Dana works at
Northwind*). It is **off** until you turn it on. It runs every 30 minutes as
the built-in **Knowledge extraction** trigger, using the utility model from
**Settings → AI & System** (or a model you pick on the trigger).

!!! warning "A cloud model reads your mail"
    With a cloud utility model (OpenAI, Gemini, Anthropic, OpenRouter, GitHub
    Models, Azure OpenAI, or Ollama at an internet address such as hosted
    Ollama at `https://ollama.com/api`), turning extraction on sends the text of
    everything Janus has ingested to that provider. Janus asks before turning
    it on and names the model. Pick an Ollama model running on your machine or
    local network to keep it there.

Each month extraction may use up to your **monthly token budget** (2,000,000
tokens unless you change it); the Knowledge tab shows how much is used. When
the budget is reached, extraction pauses until the next month.

Janus only merges two entities on its own when their names match exactly.
Names that merely look alike (*Northwind* and *Northwind Traders GmbH*) show
up under **Possible duplicates**, where you merge them or keep them apart.
When a source changes or is deleted, what was extracted from it goes with it.

The Knowledge tab also has a **search** across everything ingested. It finds
passages by exact words and, with an embedding model, by meaning, and it
follows the graph. Asking about *Dana* also turns up passages about the
project Dana leads, marked *graph via Apollo migration*. Every hit names its
source and date, and clicking a name opens that entity's relations and the
sources that mention it. In chat and in triggers, Janus uses the same search
through its `search_knowledge` and `get_entity` tools. Nothing extra is sent
to a model for a search; only the query is embedded.

### Wiki

Under **Memory & Knowledge → Wiki**, Janus keeps a page for every active or
pinned tracked topic and for every person, organization, or project that comes
up in at least five passages. Every statement cites its sources as `[1]`,
`[2]`, …, and the markers link to the numbered source list under the page.
Pages are written by the built-in **Wiki synthesis** trigger, hourly, once you
turn it on. It uses the utility model and the same monthly token budget as
knowledge extraction, and it asks before you enable it with a cloud model.

You can edit any page. From then on Janus never overwrites it: its updates
show up as **proposals** next to your text, to accept or dismiss. Every
version is kept under **History** and can be restored. Each topic on the
Topics tab links to its page. In chat, Janus can read the wiki and propose
edits; a proposal never changes the page until you accept it.

Janus refers to other pages as `[[Page Title]]` (or `[[Page Title|shown
text]]`). In wiki pages, chat replies, and the briefing these show as links:
following one opens the Wiki tab on the page with that title (case does not
matter) or slug. If there is no such page yet, the Wiki tab says so and puts
the name in the search box. Brackets inside code, numbers such as `[[3]]`, and
targets longer than 200 characters stay as written. You can write the same
links when you edit a page.

When you delete the last source behind a page, the next run removes what Janus
wrote from it, history and open proposals included. Your own edits stay.

Besides the passages, Janus writes a page from the memory facts about its
subject: a topic's linked facts and facts that name the topic, or facts that
name the person, organization, or project (up to five). A statement taken from
a fact cites it like a passage.

Each page has a **Sources** tab. It lists every `[n]` with the passage or
memory fact it cites; click a `[n]` in the page text to jump straight to it.
The tab also shows the people, organizations, and projects mentioned in those
passages (with a button to open their own page) and the memory facts about the
subject, including ones no statement cites yet. A source deleted since the page
was written is marked **gone**.

The **Graph** button above the page list shows your active and pinned topics,
the people, organizations, and projects their mail and events mention, and the
relations extracted between them. Select a topic or entity (click, or Tab and
Enter) to see its links and open its wiki page; **Show table** lists every link
as text.

## Janus character, triggers, and tool permissions

These are configured per user in the dashboard, not in `settings.toml`:

- **Settings → Daily Briefing → Desktop and device access** — create and
  revoke the device tokens a desktop popup or phone uses to read your
  [action inbox](inbox.md). A token opens only the inbox and is shown once.
- **Settings → Daily Briefing → Secretary** — the built-in **Secretary**
  trigger thinks ahead for you every hour (edit or disable it under
  **Settings → Triggers**). Here you set its **waking hours** (default 08:00 to
  21:00; it never runs a model outside them) and optional **WhatsApp alerts**:
  with the switch on and your own number entered (international format, for
  example `+4915112345678`), it may message that number, and only that number,
  for something time-critical, at most three times a day. It needs a connected
  WhatsApp account; without alerts everything arrives as a conversation in
  Janus. With a cloud model, every run can send excerpts of your planner, mail,
  and memory to that provider; choose a local model for the Secretary on the
  Triggers page if that matters to you. See
  [Runtime triggers](runtime-triggers.md) and
  [Notify me on WhatsApp](tools.md#notify-me-on-whatsapp).
- **Settings → Character** — Janus's name, identity, values, boundaries, and
  two free-text fields: **Additional instructions** (appended to Janus's
  system prompt in chat and every trigger run) and **Memory instructions**
  (replace the built-in rules Janus uses after each chat turn to decide
  whether something is worth remembering). **Prompt preview** shows the exact
  system prompt before you save.
  Below the constitution, **Adaptive state** holds Janus's current tone,
  verbosity, proactivity, and focus. You can change them directly, approve or
  reject the changes Janus proposes in chat (**Proposed by Janus**), restore
  any earlier state from **History**, or **Reset to defaults**. **Bounds and
  defaults** set which values are allowed and what a reset restores; Janus
  can never go outside them, and only chat can propose changes (never a
  trigger or background task). The focus is shown to Janus as data, not as
  instructions.
- **Settings → Skills** — one shared skill library; every skill applies to
  Janus everywhere. There are no agent definitions to assign skills to.
- **Settings → Triggers** — the Morning briefing, Afternoon digest, and Mail
  processing built-ins and your own automations (objective, tools, schedule or
  event). The digest's time is set here, on its trigger; changing the
  notification digest settings through the API updates that trigger too.
- **Settings → Tools & Connections** — under **Built-in Janus tools**, each
  tool has an **Unattended** switch, *Allowed* or *Off*. It decides whether
  Janus may use the tool with nobody watching: in triggers (including the
  built-in briefing, digest, and mail processing) and in background tasks.
  In chat the switch does not apply: Janus can use every tool there, but asks
  you on an approval card before each call of a tool that is *Off* by default
  (see [Tools and permissions](tools.md#how-permissions-work)).
    - Each of those asking tools also has an **In chat** switch: *Ask every
      time* (the default), *Always allow* (runs without asking; the approval
      card's **Always allow** button sets it too), or *Off in chat*, which
      takes the tool out of chat completely so Janus never sees or offers it
      there. The switch does not affect unattended runs.
    - Above the list, two modes override the per-tool switches (Settings
      asks you to confirm before turning either on):
      **Unattended: Allow everything** lets every tool run in triggers,
      automations, and background tasks whatever its switch says, and also
      opens image generation and remote MCP tools to them (page actions,
      page introspection, UI blocks, and the propose tools stay chat only,
      the self-evolution tools keep their own switch, and background tasks
      still only read). **In chat: Allow all without asking** skips every
      approval card; tools set to *Off in chat* stay off.
    - By default the tools with the biggest effects are *Off*: sending mail,
      sending WhatsApp messages, changing the local planner or mail rules,
      proposing wiki edits, GitHub, Linear, and the skill library. Remote MCP
      tools are chat only (unless you chose *Allow everything*), so their
      switch is locked. Everything else, including web search, is *Allowed*.
    - A tool you changed is marked **changed**; **Reset** puts it back to its
      default.
    - Switching a tool that changes something (not just reads) to *Allowed*
      asks you to confirm first.
    - *Trigger tools* and *System introspection tools* are *Allowed* by
      default. Trigger runs may then schedule a few one-time follow-ups within
      fixed limits (see [Runtime triggers](runtime-triggers.md#follow-ups-scheduled-by-a-trigger-run-jan-274)),
      and read only your own event-log entries.
    - A tool whose integration is not set up says **Not connected yet**.
    - A few tools are never given to triggers Janus schedules itself (deleting
      memory facts, changing dashboard widget prompts); the row lists them.
    - On a trigger you wrote yourself, picking a tool allows it for that
      trigger even when it is *Off*. The built-in triggers do not get this.
    - *Janus documentation* lets Janus read these docs (read-only) to answer
      questions about itself. It is *Allowed* by default.

[Tools and permissions](tools.md) lists every tool, its default, and what a
`BLOCKED` answer means.

See [Runtime triggers](runtime-triggers.md) for how runs, refusals, and mail
processing work, including the cost of one agent run per new email.

## Agent web search

Janus can search the public web from chat through an optional `web_search`
tool. It is off by default and is configured by an administrator in the
dashboard under **Settings → Tools & Connections → Web Search** (not in
`settings.toml`).

Supported providers:

- **SearXNG (self-hosted)** — the local-first option. Enter the base URL of a
  SearXNG instance you run (for example `http://localhost:8888`). The instance
  must have the `json` output format enabled (`search.formats` in SearXNG's
  `settings.yml`). No API key is needed.
- **Brave Search API** — enter a Brave Search API key. The key is stored
  encrypted at rest and is never returned by the API.

What leaves your machine: only the short search query the assistant writes,
capped at 400 characters. Janus never sends the conversation, email content,
or any other context to the search provider. Each search returns at most 10
results (5 by default), with titles and snippets trimmed.

Behavior:

- **Chat and unattended runs.** Chat can always use web search once it is
  configured. Its **Unattended** switch is *Allowed* by default, so follow-ups
  Janus schedules for itself, background tasks, and triggers that select *Web
  search* among their tools can search too; none of the built-in triggers
  select it. Switch it *Off* under **Built-in Janus tools** to keep web search
  to chat.
- **No confirmation prompt.** Searching is read-only for Janus, so the tool
  runs without asking first.
- **Not configured → not offered.** Until a provider is set up, chat does not
  offer the tool to the assistant at all; if it is somehow called anyway, it
  replies that web search is not configured instead of failing.
- **Results are treated as untrusted.** Search results are passed to the model
  inside clearly labelled `<web_search_result>` blocks with an instruction
  never to follow directions found in them, the same way email content is
  handled.

Only administrators can change these settings; other users can see whether web
search is enabled.

### Reading a web page

Separately from search, Janus can open one web page you point it to (or a
search result) with the *Web page reader* (`web_fetch`). It needs no
configuration and no API key: Janus fetches the page itself from the machine
it runs on. Because the address is sent to that website, Janus asks you to
approve every fetch in chat, and the tool is *Off* for unattended runs until
you switch it on under **Built-in Janus tools**. It refuses local and
private-network addresses, sends no cookies, reads only text formats (HTML,
plain text, Markdown, CSV, JSON, XML) up to 2 MB, and hands Janus at most
20,000 characters, marked as untrusted. Environment proxy settings
(`HTTP_PROXY`/`HTTPS_PROXY`) are not used for these requests. See
[Web page reader](tools.md#web-page-reader).

## Shell tool

On a local, personal install you can let Janus run commands on your own
computer with the *Shell* tool (`run_shell_command`). It is **off by
default**; turn it on in `settings.toml`:

```toml
shell_tool_enabled = true
# Optional:
# shell_tool_working_dir = "/home/you/projects"
# shell_tool_timeout_seconds = 30
# shell_tool_output_max_bytes = 16000
# shell_tool_allow_shell_syntax = false
```

or with `JANUS_SHELL_TOOL_ENABLED=true`, then restart Janus. Only
administrators get the tool, and in chat every command waits for your
approval. For unattended runs it is *Off* until you switch it on under
**Built-in Janus tools**.

!!! warning "Not for shared or hosted servers"
    Janus reads mail, messages, and web pages written by other people. A
    message crafted to trick Janus could try to get a command run. Approving
    each command is the main protection, so read every command before you
    approve it. Commands run as the same operating-system user as Janus and
    can read whatever that user can, including the Janus database and key
    file. Leave `shell_tool_enabled` off on any machine you share.

See [Shell](tools.md#shell) for exactly what the tool does.

### `allowed_origins` and WebSocket connections

`allowed_origins` controls both CORS and WebSocket origin validation. The most
common source of "WebSocket connection failed" errors is a mismatch between the
URL where the dashboard is served and the values in `allowed_origins`.

**Key rule:** The origin composed from `{scheme}://{host}:{port}` (where `port`
is the value of `port` in your config) **must** appear in `allowed_origins`.

For example, if Janus listens on port `8000`, add `http://localhost:8000` to
`allowed_origins`. The Vite development-server entries (`http://localhost:5173`)
can stay alongside it.

```toml
allowed_origins = ["http://localhost:8000", "http://localhost:5173"]
```

`port` and the port embedded in `base_url` should always be identical. A
mismatch breaks OAuth redirect URIs and generated links — use `janus config doctor`
to detect this automatically.

### Example

```toml
# ~/.config/janus/settings.toml

# Persistent absolute runtime paths
database_url = "sqlite:////home/you/.local/share/janus/janus.db"
fernet_key_path = "/home/you/.local/share/janus/.janus_key"
log_file_path = "/home/you/.local/state/janus/janus.log"

# JWT signing secret — generate with: openssl rand -hex 32
secret_key = "change-me-to-a-real-random-string"

# Recommended for webspace/shared-hosting deployments
server_mode = true
public_setup_enabled = false

# Bind to all interfaces
host = "0.0.0.0"

# Listen on a non-default port
port = 8080
```

## Self-evolution loop

Janus can run its own development loop -- file a Linear issue about a gap,
start a Claude Code implementation job, merge the feature and release pull
requests, and check production afterwards -- **with no human approval step**.
It is off by default and only an administrator can turn it on, for their own
Janus, under **Settings → Tools & Connections → Self-evolution** (not in
`settings.toml`). It needs the GitHub token and default repository (**Settings → GitHub**) and
the [Linear](#linear-integration) API key with a default team.

Fields:

- **Run the self-evolution loop autonomously** -- the switch.
- **Claude Code Routine trigger id** (`trig_...`) and **Routine API token**
  (`sk-ant-oat01-...`) -- the API trigger of a Claude Code Routine that
  implements an issue and opens a pull request. Janus builds the fire URL
  (`https://api.anthropic.com/v1/claude_code/routines/<trig_id>/fire`) itself
  and sends the job's instructions as `{"text": ...}` (at most 65,536
  characters). The token is stored encrypted and never shown again.
- **GitHub repository** -- `owner/name`; empty uses the GitHub default
  repository.
- **Production base URL** -- where the post-release self-checks send their
  requests (https only).

Setting up the routine on claude.ai (needs a Pro, Max, Team or Enterprise
account with Claude Code on the web):

1. Create a routine for the repository Janus works on and add an **API
   trigger**. Copy its id (`trig_...`) and generate its token; the token is
   shown only once and works only for that routine.
2. Write the routine's prompt so it acts on the fire payload. Janus's text
   arrives in the session inside a `<routine-fire-payload>` block marked as
   untrusted, so a prompt such as "Implement the Linear issue described in the
   routine fire payload, following its rules" is needed; a routine whose prompt
   ignores the payload does nothing useful.
3. The text tells the session to open one pull request whose description
   contains the Linear issue id and the session URL. Janus only merges pull
   requests that name a session it started, so keep that instruction intact.

Limits: 30 fires per hour per routine and 100 API fires per hour per account;
the API trigger is experimental. Each call starts a new session and is never
retried automatically: after a `429` or `5xx` answer, Janus reports "try again
later" and records the error.

What Janus merges: only pull requests opened by a session it started, minor or
patch Dependabot bumps (one at a time -- unlike the repository's usual
"combine Dependabot PRs" rule, at the owner's request), revert pull requests it
opened, and the release-please pull request after its feature.

What stops it: green CI on every merged pull request, a fixed list of paths it
may never merge changes to, release ordering, and daily caps (5 issues, 3
jobs, 3 feature merges, 3 release merges, 3 self-checks, 3 revert PRs). The
card shows today's usage and every recent action; they are also in the event
log. See the [feature spec](feature-specs/autonomous-self-evolution-loop.md)
and [Self-evolution tools](tools.md#self-evolution-tools).

## Connecting Google and Microsoft

Janus reads the OAuth client of each provider from `settings.toml` (or the
`JANUS_*` variables above); the web UI no longer stores or edits them. Each
user then presses **Connect Google** / **Connect Microsoft** under
**Settings → Connections**.

```toml
google_client_id = "….apps.googleusercontent.com"
google_client_secret = "…"
microsoft_client_id = "…"   # Entra ID app registration
microsoft_tenant_id = "…"
```

Both providers use the same pattern, `<base_url>/api/connections/<provider>/callback`. Register these redirect URIs (the Connections tab shows the exact values for your
`base_url`):

| Provider | Redirect URIs | Notes |
| --- | --- | --- |
| Google | `<base_url>/api/connections/google/callback` (one URI for Connect Google and Sign in with Google) | Web application client; enable the Calendar and Tasks APIs. Gmail uses the `https://mail.google.com/` scope |
| Microsoft | `<base_url>/api/connections/microsoft/callback` | Platform "Mobile and desktop" (public client); delegated permissions `Calendars.Read.Shared` and `Mail.Read` (plus `openid`, `profile`, `offline_access`); Janus asks for nothing else |

Upgrading from a version that stored the Google client in the database: copy
it into `settings.toml`; the migration drops the old columns.

Once connected, the **Planner** page lists the account's calendars to tick and
syncs on demand, and the **Email** page adds a Gmail or Outlook mailbox in one
click (no password stored). Gmail is read over IMAP with OAuth. Outlook is read
over Microsoft Graph with `Mail.Read` and checked every minute; because
`Mail.ReadWrite` is not requested, Janus never marks Outlook mail read on the
server, mail moved into the inbox later (with an older received time) is not picked up, and Outlook mailboxes cannot send mail yet. The app registration must have `Mail.Read` granted, or Connect Microsoft fails. Mailboxes start with Janus,
so restart it after adding one.

## Linear integration

Janus can read, create, update, and comment on Linear issues from dashboard
chat once an API key is saved (see [Linear tools](tools.md#linear-tools)). Configure it under
**Settings → Linear**, not in `settings.toml`:

1. Create a Personal API key at <https://linear.app/settings/api>.
2. Paste it into the **Personal API Key** field and click **Save Settings**.
3. Optionally set a **Default Team** — the team key shown in issue IDs
   (e.g. `ENG` for `ENG-123`), the exact team name, or the team UUID. Janus
   resolves a key or name to the underlying team UUID automatically. Without
   a default, Janus's [Linear tools](tools.md#linear-tools) use the
   workspace's team when there is only one and otherwise need a team on each
   call; the chat's "Submit idea as Linear issue" button and the self-evolution loop
   always need a default team.
4. Click **Test Connection** (enabled once a key is saved) to confirm Janus
   can reach Linear. It reports either "Connected as `<name>`" or the actual
   error Linear returned (for example, an invalid key).

Only an administrator can change these settings; other users see them
read-only. The API key is stored Fernet-encrypted at rest and never returned by
the API. Pasted keys and team values
are whitespace-trimmed automatically, so a stray trailing newline from
copy/paste won't break the connection. An unknown team is reported when you
create an issue or an agent lists issues, with the team keys the API key can
see.

## Microsoft 365 (Outlook and Teams)

Janus can read and send Outlook mail, read your calendar, and read and send
Teams chats through Microsoft Graph, acting **as you** (delegated permissions).
It uses its own Entra ID app registration, not `az login`: an Azure CLI token
carries only the permissions Microsoft pre-consented for the CLI, which exclude
the Teams and mail scopes, so `DefaultAzureCredential` would return a token that
looks valid and then fails with 403.

1. In Entra ID, register a single-tenant app. Under **Authentication**, add the
   **Mobile and desktop applications** platform with the redirect URI
   `http://localhost`.
2. Under **API permissions**, add these delegated Microsoft Graph permissions
   and get tenant admin consent if your organization requires it: `User.Read`,
   `Mail.Read`, `Calendars.Read`, `Chat.Read`, and for the send tools
   `Mail.Send` and `Chat.ReadWrite`. Janus asks for whatever is consented
   (`.default`), so a missing permission shows up as a clear error on that tool,
   not as a failed sign-in.
3. Set the ids (they are public identifiers, not secrets):

    ```bash
    export JANUS_MICROSOFT_CLIENT_ID=<application (client) id>
    export JANUS_MICROSOFT_TENANT_ID=<directory (tenant) id>
    ```

4. Run `uv run janus microsoft login` on the machine running Janus. A browser
   opens once. `uv run janus microsoft status` shows the account and the
   permissions the token carries; `uv run janus microsoft logout` forgets it.

The sign-in is stored in `~/.local/share/janus/microsoft_token_cache.enc`
(mode `0600`), encrypted with Janus's Fernet key (`.janus_key`), the same
protection as the GitHub and Linear tokens. It needs no OS keyring. If you lose
or change the key file, the cache becomes unreadable and tools ask you to run
`janus microsoft login` again; `janus microsoft logout` deletes the file.

The sign-in is **one identity for the whole instance**, like the GitHub and
Linear tokens, and the Microsoft tools are offered to administrators only.
Sign-in is interactive and local, so Janus background runs never open a browser;
when the sign-in expires, tools answer with an error telling you to run
`janus microsoft login` again. Mail and chat content you let Janus read is sent
to the configured LLM backend: check that your employer allows that before
connecting a work account.

## Azure resources

Janus can list Azure subscriptions, resource groups and resources (read-only)
through Azure Resource Manager. It authenticates with `DefaultAzureCredential`
(azure-identity): a service principal from the environment
(`AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_CLIENT_SECRET`), a managed
identity, or the `az login` session of the machine Janus runs on. The browser
credential is excluded, so Janus never opens a window. Janus stores nothing.

Set `JANUS_AZURE_ENABLED=true` to turn it on. The tool is offered to
administrators only, and what it can see is whatever Azure RBAC grants that
identity. This is separate from the Microsoft 365 sign-in above: an `az login`
token cannot read mail or Teams chats.

## Automatic updates

A self-hosted Janus installed with `git clone` keeps itself up to date. Once an hour it checks
the project's GitHub releases. When a newer release is published, it:

1. downloads the release's prebuilt web dashboard (Node.js is not needed) and checks its checksum,
2. backs up the database to `janus.pre-release.db` next to `janus.db`,
3. moves your checkout to the release tag and runs `uv sync`,
4. applies the database migrations, and
5. restarts itself.

If any step fails, or the new release does not start after the restart, Janus goes back to
the version it was running, restarts on it, and logs the error under **System Events** (source
`AutoUpdate`). A release that failed to start is not installed again automatically; the next
release is, and `janus update` can still install it by hand.

### Installed with uv tool or pipx

A Janus installed from the release wheel with `uv tool install` or `pipx install` updates the
same way, through the same installer: it downloads the new release's wheel and checks its
checksum, keeps the running release's wheel in `~/.local/state/janus/rollback/`, backs up the
database, reinstalls the tool from the new wheel (keeping the optional extras you installed),
applies the migrations, and restarts. If a step fails, or the new release does not start, it
reinstalls the kept wheel and restores the database backup. `uv` or `pipx` must be on the
`PATH` of the running Janus.

### Git checkouts

Janus only updates a checkout when it is safe to move it. Your checkout must have no
uncommitted changes and be on `main` or at a release tag, and Janus must be running from the
checkout's own `.venv` (as `uv run janus start` does). On a feature branch or with local edits,
it logs why it skipped the update when it starts, and never touches the checkout.

```bash
janus update --check   # the install kind, and is a newer release available?
janus update           # install it now (then restart Janus yourself)
```

To turn automatic updates off, set `auto_update_enabled = false` in the configuration file, or
set `JANUS_AUTO_UPDATE_ENABLED=false`.

## Inspect effective settings

To see the fully merged, resolved configuration (with sensitive values masked):

```bash
janus config show
```

## Diagnose misconfigurations

Run the built-in doctor to detect common problems automatically:

```bash
janus config doctor
```

The doctor checks for:

- `secret_key` is still the default placeholder
- `port` does not match the port in `base_url` (breaks OAuth redirects and generated links)
- The server's own origin (`scheme://host:port`) is missing from `allowed_origins` (causes CORS / WebSocket failures)
- The `base_url` hostname is absent from `allowed_hosts` (causes 400 Bad Request)
- The Fernet key file does not yet exist on disk
- Plain HTTP `base_url` for a public hostname in `server_mode`

The command exits with code 1 when errors are found and code 0 when only warnings are present (or everything is clean).

Example output when `port = 8000` but `base_url = "http://localhost:8080"`:

```
❌ 2 error(s) found:
   • port (8000) does not match the port in base_url (http://localhost:8080). ...
   • The server origin (http://localhost:8000) is not in allowed_origins. ...
```

Example output:

```
# Loaded from: /home/user/.config/janus/settings.toml
database_url = "postgresql://user:***@localhost:5432/janus"
fernet_key_path = "/home/user/.local/share/janus/.janus_key"
log_file_path = "/home/user/.local/state/janus/janus.log"
secret_key = "***"
server_mode = true
host = "0.0.0.0"
port = 8080
```

## Shared-hosting / webspace deployments

When the host's system Python is older than 3.12, provision an application-managed
runtime first and then run Janus on that interpreter:

```bash
uv python install 3.12
uv run --python 3.12 janus start --server-mode
```

`server_mode` is the recommended deployment profile for headless servers. It keeps
desktop-only integrations off while preserving the normal API and worker behavior.

### SSH-only first-admin bootstrap

For hosted deployments, you can avoid exposing the public setup screen entirely:

```bash
uv run --python 3.12 janus user bootstrap-admin admin
uv run --python 3.12 janus start --server-mode
```

Set `public_setup_enabled = false` first. With that setting, Janus will not print
or accept public setup tokens, and `/api/auth/setup` stays unavailable while you
bootstrap the first admin over SSH/CLI.

## Single-instance deployment constraint

Janus currently supports **one running server process per runtime directory/database**.
The runtime acquires a lock file beside the configured log path and refuses to start
another copy against the same storage. This protects the SQLite database and the
in-process IMAP/planner workers from conflicting concurrent execution.

## Environment variable reference

| Environment variable | Settings key   | Description                   |
|----------------------|----------------|-------------------------------|
| `JANUS_SECRET_KEY`   | `secret_key`   | JWT signing key               |
| `DATABASE_URL`       | `database_url` | SQLAlchemy connection string  |
| `JANUS_FERNET_KEY_PATH` | `fernet_key_path` | Absolute Fernet key path |
| `JANUS_LOG_FILE_PATH` | `log_file_path` | Absolute runtime log path |
| `JANUS_SERVER_MODE` | `server_mode` | Headless/server deployment mode |
| `HOST`               | `host`         | Bind host                     |
| `PORT`               | `port`         | Listen port                   |
| `JANUS_BASE_URL` / `BASE_URL` | `base_url` | Public application base URL |
| `ALLOWED_ORIGINS` | `allowed_origins` | Comma-separated CORS/WebSocket origin allowlist. Unlike the other settings below, this key has no `JANUS_`-prefixed alias in `config.py`; only the bare `ALLOWED_ORIGINS` (or the TOML key) is read. |
| `JANUS_ALLOWED_HOSTS` | `allowed_hosts` | Comma-separated Host header allowlist |
| `JANUS_TRUSTED_PROXY_HOPS` | `trusted_proxy_hops` | Number of trusted proxy hops |
| `JANUS_PUBLIC_API_DOCS_ENABLED` | `public_api_docs_enabled` | Expose API docs publicly |
| `JANUS_PUBLIC_SETUP_ENABLED` | `public_setup_enabled` | Enable/disable public first-run setup |
| `JANUS_RUNTIME_TRIGGER_WORKER_ENABLED` | `runtime_trigger_worker_enabled` | Enable/disable the background trigger worker |
| `JANUS_GOOGLE_API_BASE_URL` | `google_api_base_url` | Test seam only: sends the backend's Google token, userinfo, Calendar, and Tasks calls to a fake Google at this base URL (the Playwright e2e suite's). Leave unset on a real install |
| `JANUS_KNOWLEDGE_INDEX_WORKER_ENABLED` | `knowledge_index_worker_enabled` | Enable/disable the background knowledge worker (source ingestion and the embedding sweep for semantic search; entity extraction is the Knowledge extraction trigger, not this worker) |
| `JANUS_RUNTIME_TRIGGER_MAX_ATTEMPTS` | `runtime_trigger_max_attempts` | Attempts per trigger run |
| `JANUS_RUNTIME_TRIGGER_RETRY_BACKOFF_SECONDS` | `runtime_trigger_retry_backoff_seconds` | Retry backoff base in seconds |
| `JANUS_CHAT_TOOL_APPROVAL_TIMEOUT_SECONDS` | `chat_tool_approval_timeout_seconds` | Seconds a chat approval card waits before the call counts as declined |
| `JANUS_MICROSOFT_CLIENT_ID` | `microsoft_client_id` | Entra ID application (client) id for Microsoft 365 access and "Connect Microsoft"; empty keeps it off |
| `JANUS_MICROSOFT_TENANT_ID` | `microsoft_tenant_id` | Entra ID directory (tenant) id for Microsoft 365 access and "Connect Microsoft" |
| `JANUS_GOOGLE_CLIENT_ID` | `google_client_id` | Google OAuth client id shared by Sign in with Google and "Connect Google"; empty keeps both off |
| `JANUS_GOOGLE_CLIENT_SECRET` | `google_client_secret` | Google OAuth client secret; never stored in the database or shown by the API |
| `JANUS_AZURE_ENABLED` | `azure_enabled` | Turns on the read-only Azure resource tools (`DefaultAzureCredential`); default off |
| `JANUS_TOOL_SELECTION_ENABLED` | `tool_selection_enabled` | Let the utility model narrow each task's tools to what it needs |
| `JANUS_TOOL_SELECTION_TIMEOUT_SECONDS` | `tool_selection_timeout_seconds` | Seconds the tool selection may take before every authorized tool is offered |
| `JANUS_CHAT_UI_ACTION_TIMEOUT_SECONDS` | `chat_ui_action_timeout_seconds` | Seconds Janus waits for the web page to confirm a page action or answer a page-introspection request |
| `JANUS_IMAGE_STORAGE_DIR` | `image_storage_dir` | Directory for chat-generated images |
| `JANUS_IMAGE_STORAGE_LIMIT_MB` | `image_storage_limit_mb` | Per-user image storage cap in MB |
| `JANUS_SHELL_TOOL_ENABLED` | `shell_tool_enabled` | Turn on the admin-only *Shell* tool (local installs) |
| `JANUS_SHELL_TOOL_WORKING_DIR` | `shell_tool_working_dir` | Directory shell commands run in |
| `JANUS_SHELL_TOOL_TIMEOUT_SECONDS` | `shell_tool_timeout_seconds` | Time limit per shell command |
| `JANUS_SHELL_TOOL_OUTPUT_MAX_BYTES` | `shell_tool_output_max_bytes` | Output cap per shell command |
| `JANUS_SHELL_TOOL_ALLOW_SHELL_SYNTAX` | `shell_tool_allow_shell_syntax` | Run shell commands through `/bin/sh -c` |
