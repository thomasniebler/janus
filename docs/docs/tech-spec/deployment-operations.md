# Deployment and Production Operations

## Supported Topology

- Public deployments run Janus as one long-lived application process behind an HTTPS reverse proxy on a stable public base URL.
- Production builds default to same-origin API requests and same-origin WebSocket URLs.
- A separate absolute API URL override remains optional for development or split frontend/backend deployments only.

## Runtime Requirements

- **Managed runtime requirement:** Janus requires Python 3.12+. On hosts whose system Python is older, deployment must provision an app-managed Python runtime rather than relying on the host default interpreter.
- **Database backend:** SQLite by default; PostgreSQL via `DATABASE_URL` (migration chain verified in CI). `janus db copy` moves an existing installation between backends while the server is stopped; the Fernet key file must move with it.
- **Persistent state paths:** The SQLite database, Fernet key file, operational logs, and server lock file must live at explicit absolute paths in persistent application storage outside ephemeral release or checkout directories.
- **Default path pattern:** Production defaults use Janus XDG data/state directories, or equivalent configurable persistent directories for hosts that need different paths.
- **Headless server mode:** Server deployments must support running without desktop integrations such as the system tray icon and desktop notifications.
- **Optional extras for WhatsApp and the tray icon:** Neonize (the WhatsApp connector, plus segno for its pairing QR code) is the optional `whatsapp` extra, and pystray with Pillow (the tray icon) is the optional `desktop` extra. Neither runs on the glibc 2.17 Uberspace host: Neonize's Go library needs glibc 2.34, and the host is headless. Both pull in Pillow, whose newer releases drop the glibc 2.17 wheels, so as base dependencies they kept Pillow pinned there and blocked its security fixes. The deploy installs no extras, so Pillow, Neonize, and pystray are not installed in production, and Dependabot updates Pillow normally. Without `whatsapp`, `whatsapp_neonize.neonize_import_error()` reports "WhatsApp support is not installed" (a `ModuleNotFoundError`, told apart from a native-library load failure); without `desktop`, `janus start --tray` logs a warning and runs without the icon. Local installs add them with `uv sync --extra whatsapp --extra desktop`. `scripts/janus-up.sh` syncs with `--inexact` plus each installed extra, the same rule as the auto-update, so it keeps and upgrades them rather than removing them. The Playwright e2e backend starts with `uv run --extra whatsapp` for segno (the pairing QR code), and the fake `neonize` on `PYTHONPATH` shadows the real one. CI's backend-quality job installs every extra, so their tests run there; without them, the tests that need real Neonize objects or Pillow are skipped. Like `documents`, they stay out of the `dev` group.

## Proxy and Public Origin

- Production deployments behind TLS-terminating proxies must reconstruct the public request origin from trusted forwarded headers while leaving direct/no-proxy deployments unchanged by default.
- Trusted forwarded scheme/host headers are used so redirects, cookie flags, and generated callback URLs reflect the external HTTPS URL.

## Operational Constraints

- **Single-instance constraint:** Janus currently runs as a single server instance per runtime directory/database because SQLite-backed state and in-process background workers must not be shared across multiple concurrent server processes. The live UI refresh channel (`/ws/changes`, JAN-104) relies on this too: its domain-change bus is in-process, so a write served by one process would not reach sockets held by another.
- Production deployment must run exactly one active Janus application instance unless workers are split into a different architecture first.
- **Trigger claim scope:** Accepted triggers have durable, task-scoped idempotency records and atomic queued/failed/expired-lease claim helpers. Claim tokens bind terminal transitions to the worker that owns the lease. The durable worker (enabled by default) executes outside claim transactions, persists plan progress, and supports bounded retry/backoff; cross-process execution still requires a shared deployment architecture beyond the current single-instance constraint.
- **Migration rollout:** Revisions `4f8a2c1d6e9b`, `7b1c2d3e4f5a`, `9d3e5f7a1b2c`, `d4e5f6a7b8c9`, and `e5f6a7b8c9d0` add trigger storage, claim/owner columns, plan-state storage, and retry state additively. Release deploys must run the normal Alembic upgrade before starting code that consumes durable records; no manual data backfill is required.
- **Runtime trigger worker:** `runtime_trigger_worker_enabled` defaults to `true` (JAN-199), so queued manual, scheduled, and event triggers execute without extra configuration. Startup registers the built-in plan-runtime handler (`trigger_runtime.build_runtime_trigger_handler`) through `configure_runtime_trigger_handler()` whenever the flag is on, and still fails closed if the worker is enabled without a handler. Set `runtime_trigger_worker_enabled = false` (or `JANUS_RUNTIME_TRIGGER_WORKER_ENABLED=false`) to opt out; triggers are then accepted but stay `queued`. The authenticated `GET /api/runtime/triggers/worker` endpoint reports `{enabled, running}` so the Settings → Triggers panel shows the real worker state. Upgrading deployments that never set the flag will start executing any already-queued triggers on first start, using the owner's current per-tool unattended switch (JAN-255; before, a fixed trigger toolset).
- **SQLite extension loading (JAN-241):** vector search needs Python's `sqlite3` built with `enable_load_extension` so `sqlite-vec` can load. Check on the server with `uv run python -c "import sqlite3; print(hasattr(sqlite3.connect(':memory:'), 'enable_load_extension'))"`. When it is missing, Janus logs a startup warning, system status shows the Knowledge search service as degraded once an embedding model is configured, and search stays keyword-only. Revision `b3c4d5e6f7a8` is additive; after configuring a model, the knowledge-index worker backfills vectors, or run `janus knowledge reembed`. The production host passes `sqlite-load-extension` and `sqlite-vec` but has no FTS5, so its keyword side is substring matching; see the capability table below.
- **Document parsing (JAN-242):** `markitdown[pdf,docx,xlsx,pptx]` is the optional `documents` extra. CI's backend-quality job installs it; locally, `uv sync --extra documents` enables it, and the parsing tests that need it are skipped without it. It must stay out of the `dev` group, because a plain `uv run` on the host syncs that group. That is how v1.2.1's deploy still failed: `uv sync --no-dev` succeeded, then `uv run alembic upgrade head` tried to install onnxruntime. The deploy's `uv run` calls now pass `--no-sync`. It needs onnxruntime (via magika), and every onnxruntime build for Python 3.12 needs glibc 2.27. The Uberspace host is glibc 2.17 (`manylinux_2_17`), so the v1.2.0 deploy failed in `uv sync`. The deploy installs no extras, so in production only TXT, Markdown, and CSV are ingested. Other documents are recorded as `skipped` ("needs the optional 'documents' extra"), and uploads of them are rejected. A host with glibc 2.27 or newer can run `uv sync --frozen --no-dev --extra documents`, which adds about 220 MB to the virtualenv (measured 563 MB → 786 MB). CI's backend-quality job and the deploy's verify step both dry-run installing the default dependency set, dev group included, for `x86_64-manylinux_2_17`, so a dependency the host cannot install fails before anything is synced. That check catches packages that have neither a compatible wheel nor an sdist; an sdist-only package would still be built on the host. Running `uv cache prune` after `uv sync` keeps the uv cache from growing with every release (not yet part of `deploy-uberspace.yml`). Revision `a9afd7fc84dd` is additive; after upgrading, the knowledge-index worker ingests existing mail, chats, calendar events, and tasks at 50 sources per minute, or run `janus knowledge backfill` once. Staged attachment bytes are capped at 200 MB in the database.
- **Knowledge graph (JAN-243):** revision `be633b5aa1b6` is additive. Every user gets a disabled **Knowledge extraction** trigger on the next worker poll; nothing is sent to a model until a user turns it on. Extraction uses the utility model (Settings → AI & System), so its cost and rate limits land there; the per-user monthly token budget (default 2,000,000) caps it.
- **Wiki (JAN-244):** revision `d130c10835c8` is additive; every user gets a disabled **Wiki synthesis** trigger on the next worker poll. It draws on the same per-user monthly token budget as extraction.
- **In chat tool switch (JAN-306):** revision `c8e2f5a1d7b3` adds `tool_policies.chat_mode` and makes `unattended_allowed` nullable; existing overrides keep their meaning and every tool keeps asking per call in chat until a user switches it off. No manual step.
- **Unattended tool switch (JAN-255):** revision `f2a7c3d9e1b4` rewrites `tool_policies` in place (existing overrides keep their meaning) and needs no manual step. After upgrading, ad-hoc triggers and delegated tasks follow the same per-tool defaults as saved automations, so they can also use mail reading, local planner reading, rule reading, and web search (and ad-hoc triggers mail labelling and mail processing) unless a user switches those off on Settings → Tools & Connections.
- **Trigger-first upgrade (JAN-226):** migrations `d5e8f1a2b3c4` (destructive: drops agent definitions, skill assignments, and handoff history), `e6f9a3b4c5d7`, `f7a0b4c5d6e8`, and `a8b1c2d3e4f5` (JAN-233 model choices, additive) must be applied before starting the new release. On its first poll the worker seeds the built-in Morning briefing, Afternoon digest, and Mail processing triggers for every user. With the worker enabled, Mail processing turns every new email into one agent run, which matters for rate limits and cost on hosted model providers; disable that trigger per user, or run with the worker disabled, to keep the synchronous classifier. With the worker disabled, schedules and events do not run, but the dashboard briefing and `janus brief`/`janus digest` still run their triggers inline.
- **Bootstrap strategy:** Internet-facing deployments must support SSH/CLI-based first-admin bootstrap so public setup endpoints are not the only way to provision the initial admin account.
- **Public surface minimization:** Production mode should disable or strictly protect non-essential public surfaces such as interactive API docs and setup endpoints that are no longer needed after provisioning.

## Release Deploys

- GitHub releases must automatically redeploy the tagged Janus revision to the primary hosted environment over SSH.
- Hosted release deploys build the frontend on the runner, then remotely sync dependencies, take the [pre-release snapshot](#pre-release-snapshot), run Alembic migrations and `janus config doctor`, restart the supervised `janus` service, and wait until the public health endpoint reports the expected release version; deploy duration and outcome are recorded ([deploy and availability metrics](#deploy-and-availability-metrics)).
- Every deploy is followed by [post-release validation](#post-release-validation); a failure of any of them fails its workflow run.
- The deploy target is configured with repository secrets and variables, not in the repository ([deploy target configuration](#deploy-target-configuration)).
- **Release versioning (JAN-290):** Release Please runs in manifest mode (`release-please-config.json`, `.release-please-manifest.json`). Each release PR bumps the version in `pyproject.toml`, `CHANGELOG.md`, the manifest, and, through a `toml` extra-file updater with the jsonpath `$.package[?(@.name.value=='janus')].version`, the `janus` entry in `uv.lock`, so the tagged revision's lockfile matches its `pyproject.toml` and a plain `uv run` or `uv sync` does not rewrite it. Tags stay `vX.Y.Z` (`include-component-in-tag: false`) and the release branch stays `release-please--branches--main`. CI's backend-quality job runs `uv lock --check`, so a lockfile that drifts from `pyproject.toml` fails the build.

### Deploy target configuration

Nothing host-specific is committed. `deploy-uberspace.yml`, `deployment-capabilities.yml`,
`nightly-health-check.yml`, and `scripts/release_host_checks.sh` read the target from GitHub
repository secrets and variables (Settings → Secrets and variables → Actions). The host and the
public URL are secrets, so the public run logs mask them; each job that reaches the host also masks
the SSH user, which would otherwise show in host paths such as `/home/<user>/...`:

| Name | Kind | Required | Default | Meaning |
|---|---|---|---|---|
| `DEPLOY_ENABLED` | Variable | Yes | — | `true` turns on the deploy, capability-check, and nightly-probe jobs. |
| `DEPLOY_SSH_TARGET` | Secret | Yes | — | `user@host` the deploy SSH key logs in to. |
| `JANUS_PUBLIC_URL` | Secret | Yes | — | Public base URL of the hosted Janus, used for the version check, release validation, and the nightly probe. |
| `DEPLOY_PATH` | Variable | No | `janus` | Checkout directory on the host, relative to the SSH user's home or absolute, without spaces. |
| `DEPLOY_SERVICE` | Variable | No | `janus` | supervisord program name that `supervisorctl restart` targets. |

The SSH key and known-hosts entry are the repository secrets `UBERSPACE_SSH_PRIVATE_KEY` and
`UBERSPACE_KNOWN_HOSTS`. Without `DEPLOY_ENABLED=true` every one of these jobs skips itself, so a
fork or a fresh clone never tries to deploy.
Locally, `task ops:verify-version` and `task ops:probe` take `BASE_URL=` or `$JANUS_PUBLIC_URL`.
The workflow still assumes a host with SSH, rsync, `uv` in `~/.local/bin`, and supervisord.

## Self-Hosted Auto-Update

The hosted instance is updated by the push deploy above. A self-hosted install instead pulls
releases itself: `services/update_service.py`, run by the `auto-update` worker (`workers/update.py`)
every `auto_update_interval_minutes` (default 60, at least 15), or by hand with `janus update`.
`install_kind` tells the two supported installs apart: a git clone run with `janus start` (a `.git`
at the install root), and the release wheel installed with `uv tool install` (a `uv-receipt.toml`
in the environment) or `pipx install` (a `pipx_metadata.json`). The bullets below describe the
checkout; [Tool installs](#tool-installs) covers the wheel.

- **Check:** `GET https://api.github.com/repos/<auto_update_repository>/releases/latest`
  (unauthenticated; drafts and prereleases are never "latest"). The tag must be `vX.Y.Z` and newer
  than the checkout's `pyproject.toml` version.
- **Eligibility** (checked when `janus start` launches the worker and again before an install):
  `auto_update_enabled` is on (default), the install root has a `.git`, `git` and `uv` are
  installed, the running interpreter is the checkout's own `.venv`, there are no tracked changes,
  and `HEAD` is on `main` or detached at a release tag. Anything else is logged once at startup and
  never updated: the Uberspace deploy has no `.git`, and a development checkout is on a feature branch.
- **Install:** download the release asset `janus-frontend-dist.tar.gz` (built and uploaded by
  `release-please.yml`'s `build-and-release-artifacts` job) and check it against the SHA-256
  `digest` GitHub records for the asset (refused without one; a release whose asset is not uploaded
  yet waits for the next check); take the [pre-release snapshot](#pre-release-snapshot); fetch the
  tag from `https://github.com/<auto_update_repository>.git` (not `origin`); `git merge --ff-only`
  on `main` or `git checkout --detach` otherwise; `uv sync --frozen --inexact` plus `--extra <name>`
  for each optional extra that is installed and that the checked-out `pyproject.toml` defines
  (`desktop`, `documents`, `whatsapp`, detected by an installed marker distribution), so their
  packages follow the new lockfile instead of being left at their old versions; `import janus.main` and `database.ensure_schema_up_to_date()` in child
  processes on the new code; then swap the new build into `frontend/dist`.
  The whole install holds an exclusive lock on `.git/janus-update.lock`, so the worker and
  `janus update` never install at once. The snapshot, import and migration children run in the
  running process's working directory, so they load the same `.env` and resolve a relative
  `database_url` to the same file; only `uv sync` runs in the checkout.
- **Rollback:** a failure after the checkout moved returns it to the previous commit, re-runs
  `uv sync` (with the extras the old `pyproject.toml` defines), and, once migrations had started, restores the snapshot into the database. Failures are
  `ERROR` rows in the event log (source `AutoUpdate`); the running process keeps serving the old
  release.
- **Restart:** after an install the worker sets the uvicorn server's `should_exit`; once it has
  shut down, `janus start` re-executes itself (`os.execv`) with its original arguments, so no
  supervisor is needed. In-flight chat turns and trigger runs are interrupted, the same as with any
  restart. Between `uv sync` and the restart (usually under a minute) the old process runs from an
  editable install whose files are already the new release's.
- **Boot recovery** (`services/update_boot.py`): before that restart the worker writes
  `.git/janus-update-pending.json` (tag, replaced commit, branch, process id; `os.execv` keeps
  the id, so no other process acts on it). The re-executed `janus start` confirms the release once
  uvicorn reports it started, removing the record and the previous frontend build
  (`frontend/.dist-previous`). If `janus start` ends before that (an exception, a failed
  `ensure_runtime_initialized`, a server that never started), it rolls the checkout, the
  virtualenv and the database snapshot back, restores the previous frontend, writes the tag to
  `.git/janus-update-skip` so the worker does not install it again, and re-executes on the
  previous release. The recovery runs on the new release's code, so a release that breaks the
  updater itself cannot recover; the import and migration checks before the restart cover the
  common cases. `janus update` writes no record: whoever runs it restarts Janus and sees a failure.
- **Manual:** `janus update --check` reports the latest release and whether this install is
  eligible; `janus update` installs it without restarting a running Janus.


### Packaging

`pyproject.toml` builds with hatchling. `hatch_build.py` copies `alembic.ini`, `alembic/` and
`frontend/dist/` into the wheel under `janus/_data`, and `src/janus/resources.py` resolves the
migrations and the frontend there first, falling back to the repository paths in a checkout. The
sdist lists `frontend/dist` as an artifact, so the wheel `uv build` makes from it still carries the
UI. `release-please.yml` builds the frontend before `uv build`, uploads the wheel, the sdist and
`janus-frontend-dist.tar.gz`, and first runs `scripts/wheel_smoke.sh --require-frontend` on the
wheel (install as a uv tool in a scratch location, migrate a fresh database, check `install_kind`).
CI's backend job runs the same smoke test without a frontend (`task package:smoke`).

The distribution is `janus-assistant` (`janus` and `janus-agent` are taken on PyPI); the
import package and the command stay `janus`, and `update_service.DISTRIBUTION` names it for the
version lookup and the wheel file name. The `publish-pypi` job in `release-please.yml` uploads
the wheel and sdist with PyPI trusted publishing (OIDC, no token) from the `pypi` environment. It
only runs when the repository variable `PYPI_ENABLED` is `true`, which is set after pypi.org has
a trusted publisher for this repository, workflow `release-please.yml` and environment `pypi`.

### Tool installs

`services/update_tool_install.py`, called by `apply_update` under the same lock.

- **Eligibility:** `auto_update_enabled` is on and the installer (`uv` or `pipx`) is on the PATH.
  The installed version comes from the `janus` distribution metadata, not `pyproject.toml`.
- **Install:** download the release's `janus_assistant-X.Y.Z-py3-none-any.whl` and check its SHA-256
  `digest`; download the running release's wheel into `<state dir>/rollback/` (refused if that is
  not possible, so a rollback never needs the network); take the pre-release snapshot in-process
  with SQLite's backup API; `uv tool install --force --python X.Y "janus-assistant[extras] @ file://…"` or
  `pipx install --force "…whl[extras]"`, with the extras detected the same way as in a checkout
  (now including `postgres`); then the same import check and migration children.
- **Rollback and boot recovery:** a failure reinstalls the kept wheel and, once migrations started,
  restores the snapshot. Bookkeeping (`janus-update.lock`, `janus-update-pending.json`,
  `janus-update-skip`) lives in the XDG state directory (`update_service.state_dir`) instead of
  `.git`; the pending record carries `kind` and `previous_wheel`, so a release that does not start
  reinstalls the kept wheel. The restart is the same `os.execv` of the `janus` entry point, whose
  path does not change across a reinstall.

## Pre-Release Snapshot

Before a deploy migrates the schema, `scripts/pre_release_snapshot.py` (streamed over the deploy SSH
connection into the production uv environment, like the capability checks) copies the live SQLite
database with SQLite's online backup API to `janus.pre-release.db` next to `janus.db`
(`~/.local/share/janus/` with the default paths). There is exactly one snapshot: each release
replaces the previous one. The old snapshot is deleted first; if the copy then fails, or the free
space would drop below 256 MiB, the partial file is removed and the deploy stops before
`alembic upgrade`, so the live database stays untouched. A PostgreSQL `DATABASE_URL` is skipped.
The Fernet key file is not copied; it does not change between releases.

Rolling back a release (manual, on the host):

1. `supervisorctl stop janus`
2. Move the failed `janus.db` aside for diagnosis **together with its `janus.db-wal` and `janus.db-shm` files** (SQLite runs in WAL mode, JAN-382), then copy `janus.pre-release.db` to `janus.db`. A leftover `-wal` file next to the restored database would be replayed onto it and corrupt it.
3. Redeploy the previous tag: run the `Deploy to Uberspace` workflow with `ref` set to that tag.
   Its migrations are then a no-op, because the restored schema is already the previous head. That
   redeploy overwrites the snapshot with the restored database.

Everything written between the snapshot and the rollback is lost.

## Post-Release Validation

`deploy-uberspace.yml` runs two jobs after every deploy (release, manual release, or manual
dispatch), and the expected version is `pyproject.toml`'s at the deployed ref:

- **`validate`**, right after the restart: `scripts/release_validation.py` on the runner against
  the public URL, then `scripts/release_host_checks.py` streamed into the production uv environment
  over the deploy SSH connection, through `scripts/release_host_checks.sh`. The host sometimes
  closes a fresh SSH session right after the deploy's own connections (ssh exit 255), so that
  connection failure is retried up to three times, 20 s apart; a failed check (exit 1) or an
  unwritable report ends the step at once (`tests/test_release_host_checks.py`).
- **`soak`**, 15 minutes later: the same two scripts again. It keeps the shared
  `deploy-uberspace-production` concurrency group busy for that time.

Each check prints `PASS`, `FAIL` (required) or `WARN` (informational), and the reports land in the
job summary and in a `release-check-*` artifact.

### Soak scheduling (JAN-312, decision pending)

The `soak` job waits with `sleep 900` on an allocated runner. That is about 17 rounded runner
minutes per deploy, 15 of them idle, and it holds `deploy-uberspace-production` for the whole
interval. GitHub has no delayed-dispatch primitive. These are the candidates for running the soak
checks at least 15 minutes after the restart without a runner waiting through the interval:

| Option | Runner cost per soak | Lateness | What it needs | Main risk |
|---|---|---|---|---|
| Keep `sleep 900` (current) | about 17 min | none | nothing | idle runner; production group blocked for 15 minutes |
| Environment wait timer on a `production-soak` environment | about 1–2 min | none | GitHub Pro/Team: wait timers are not available for private repositories on GitHub Free (checked 2026-10-05: the rulesets and branch-protection APIs answer "Upgrade to GitHub Pro") | needs a plan change |
| Host crontab + `workflow_dispatch` | about 1–2 min | up to 1 minute plus runner queue | a crontab entry on the host (a read-only capability probe first), a fine-grained token limited to `Actions: write` on this repository stored on the host, and a `Release soak` workflow | a new credential on the host; a dispatch failure after the deploy run ended must still reach Linear |
| Self-disabling scheduled dispatcher | about 3–5 min (each `*/5` tick bills at least a minute) | 5–20 minutes, since GitHub delays scheduled runs under load | `actions: write` for the deploy job, to enable the dispatcher workflow; it disables itself after the check | late or dropped scheduled runs |

Whichever option replaces the sleep keeps the existing checks, secrets, reports and Linear
reporting, and adds the following:

- **Validation record:** a bounded record of the deployed ref and version, the restart time, the
  due time, and the deploy run link.
- **No early or stale runs:** the soak never runs before the due time, and it compares the
  deployed version with the record first. A newer deploy supersedes the record instead of
  validating the wrong release.
- **No duplicates:** dispatch is idempotent, so no duplicate checks or reports.
- **Release semantics:** the deploy run then ends after `validate`, and a soak failure is
  reported by a later run that links back to it.

Until a mechanism is chosen, the `sleep 900` stays.

| Check | Where | Required | What it proves |
|---|---|---|---|
| `health-version` | public | Yes | `/api/status/health` answers 200 with the deployed version (retried for about a minute). A stale IMAP monitor answers 500 and fails it. |
| `frontend-assets` | public | Yes | `index.html` and every `/assets/` file it references are served. |
| `api-docs-closed` | public | Yes | `/openapi.json` and `/apidocs` do not serve the schema or Swagger UI. |
| `setup-closed` | public | Yes | `/api/auth/setup` answers 404. |
| `chat-ws-anonymous` | public | Yes | `/ws/chat` turns away a client without a session (close 1008, or a 401/403 handshake). |
| `tls-certificate` | public | Yes | The certificate is valid for at least 14 more days. |
| `trigger-worker` | signed in | Yes | `/api/runtime/triggers/worker` reports `enabled` and `running`. |
| `status-overview` | signed in | Yes | `/api/status/summary` reports no `degraded` or `outage` service. |
| `smoke-user` | public | No | Warns, instead of running the two signed-in checks, when the smoke-test secrets are missing. |
| `installed-version` | host | Yes | The installed `janus` distribution is the deployed version. |
| `alembic-at-head` | host | Yes | The database is at every Alembic head of the deployed code. |
| `supervisor-restarted` | host | Yes | `janus` is `RUNNING` and its uptime is younger than the restart. |
| `log-tracebacks` | host | Yes | No traceback or `CRITICAL` record in the Janus log since the restart. |
| `log-errors` | host | No | Counts `ERROR` records since the restart. |
| `trigger-queue` | host | Yes | No trigger run has been due and still `queued` for more than 15 minutes. |
| `disk-space` | host | No | Reports free space. |

The signed-in checks log in as a dedicated non-admin smoke-test user, whose credentials are the
`production` environment secrets `JANUS_SMOKE_USERNAME` and `JANUS_SMOKE_PASSWORD`. Host checks
are read-only, like the capability checks.

**Failure reporting.** A failed release, deploy, validation, or nightly health check shows up as a
failed workflow run (GitHub notifies the repository's watchers); nothing is filed automatically.

## Deploy and Availability Metrics

`scripts/ops_metrics.py` (JAN-269, standard library only, `tests/test_ops_metrics.py`) holds the
logic; the workflows only pass arguments, and every command also runs locally (`task ops:*`).

**Release version check.** A deploy is finished only when `/api/status/health` answers HTTP 200
**with the expected version**. The expected version is `pyproject.toml`'s at the deployed ref
(`ops_metrics.py expected-version`); when that ref is a release tag (`vX.Y.Z`) and the two
disagree, the deploy stops before anything is synced. The `deploy` job's `Verify release version`
step (`verify-version`) replaces the former plain `curl` health probe: up to 12 attempts, 10 s
apart, so about two minutes while Janus restarts. HTTP 200 with another version fails, as does
500 (`degraded`) with the right one. Expected and actual version, the last HTTP status and the
attempt count are written to the job summary. The script runs from a second, sparse checkout of
the workflow's own revision (`.deploy-tools/`, excluded from the rsync), so redeploying an older
tag as a rollback still verifies the same way. The `validate` job's `health-version` check stays
as an independent second look.

**Deploy duration.** Defined from the first step of the `deploy` job on the runner (`Record deploy
start`; queue time and the wait for the `deploy-uberspace-production` concurrency group are
excluded) until the version check first sees HTTP 200 with the expected version. The record also
carries *restart to healthy*: from `supervisorctl restart` (the existing `restarted_at`) to the
same verification, an upper bound for the user-visible restart gap. A deploy that never verifies
has no duration and is recorded with outcome `failure`; the `deploy` and `validate` job results are
recorded next to it. The `metrics` job (`needs: [deploy, validate]`, runs for failed deploys too,
`contents: write` for that job only) builds the record (`deploy-record`), appends it to the history
and summarizes the last 90 days. It is not part of the release failure report: a metrics failure
turns the run red without filing a `Release vX.Y.Z issues` issue.

**Sampled availability.** `nightly-health-check.yml` (daily at 04:17 UTC) takes 5 samples, 30 s
apart, 10 s timeout each (`probe`). A sample is healthy when the endpoint answers HTTP 200 with
`status: ok`. Availability is the share of healthy samples over a window (7 and 30 days in the job
summary), always stated with that sampling interval. It is a sample, not continuous uptime: an
outage between two runs is not seen. The `probe` job (read-only) fails, and files a release
failure as before, only when no sample was healthy; a separate `record` job (`contents: write`)
appends the samples to the history. Each sample also records latency and version; the version on
`main` is compared for information only (`version_matches`).

**Durable history and export.** Artifacts expire, so records are appended as JSON Lines to the
orphan branch `ops-metrics` (`scripts/history_branch.py`): `deploys/deploys.jsonl` and
`availability/samples.jsonl`. The branch shares no history with `main`, is never merged, and only
grows; a publish builds the commit with git plumbing (the checkout is untouched), never forces,
and retries up to three times when another run pushed first. Only a definite missing branch
(`git ls-remote --exit-code` exit 2) reads as an empty history; any other `ls-remote` failure
(network, authentication) aborts the read or publish rather than starting a new orphan branch.
Export with `git fetch origin
ops-metrics && git show origin/ops-metrics:deploys/deploys.jsonl`, `task ops:export
DATASET=deploys|availability FORMAT=csv|jsonl`, or `task ops:metrics` for the summaries. The weekly quality report ([architecture overview](architecture-overview.md#weekly-quality-report), JAN-270) reads the last 30 days of both files for its `deployment` check, through the same summaries.

| Schema | Fields |
|---|---|
| `janus.version-check/1` | `url`, `expected_version`, `actual_version`, `ok`, `attempts`, `max_attempts`, `delay_seconds`, `last_http_status`, `health_status`, `started_at`, `verified_at`, `detail` |
| `janus.deploy-metrics/1` | `recorded_at`, `run` (`workflow`, `id`, `attempt`, `event`, `url`), `ref`, `commit`, `expected_version`, `actual_version`, `outcome`, `jobs` (`deploy`, `validate`), `timing` (`started_at`, `restarted_at`, `verified_at`, `duration_seconds`, `restart_to_healthy_seconds`), `verification` |
| `janus.availability/1` | `recorded_at`, `run`, `url`, `expected_version`, `version_matches`, `sampling` (`samples`, `interval_seconds`, `timeout_seconds`), `ok_samples`, `total_samples`, `availability`, `samples[]` (`at`, `ok`, `http_status`, `health_status`, `version`, `latency_ms`, `unhealthy_account_count`, `error`) |

Timestamps are UTC ISO 8601. **No secrets:** records are built from these fields only, and
`ensure_exportable` refuses to write or publish a record with a secret-looking key (password,
token, key, cookie, ...) or value (GitHub/Linear tokens, bearer headers, JWTs, private keys,
e-mail addresses). The health endpoint's `unhealthy_accounts` list is reduced to a count, and a
network error is stored as its exception type only.

Baseline measured from a workstation on 2026-10-11 against release 1.9.0: HTTP 200, `status: ok`,
version `1.9.0`, 3 of 3 samples healthy, about 1.1–1.3 s per request.

## Deployment Capability Checks

`scripts/deployment_capabilities.py` records, as runnable probes, what Janus needs from the
production host and its Python runtime. The `Deployment capability checks` workflow
(`.github/workflows/deployment-capabilities.yml`, manual dispatch only) streams the script over
the deploy SSH connection into the production uv environment (`uv run --no-sync`, so the live
`.venv` is never re-synced) and fails when a required check fails. It shares the deploy workflow's concurrency group, so it never
runs during a deploy.

| Check | Required | Why |
|---|---|---|
| `python-version` | Yes | Janus requires Python 3.12+ (`requires-python`). |
| `sqlite-fts5` | No | FTS5 ranks keyword matches (BM25) in memory and topic search when available. Without it, as on the Uberspace host (uv-managed Python 3.12.14, SQLite 3.53.4, checked 2026-09-28), keyword search uses substring (`ILIKE`) matching instead (JAN-245, [`knowledge-graph-and-wiki.md`](../feature-specs/knowledge-graph-and-wiki.md)). |
| `sqlite-load-extension` | Yes | `sqlite-vec` is a loadable SQLite extension, so Python's `sqlite3` must support `enable_load_extension`. |
| `sqlite-vec` | Yes | `sqlite-vec` loads and answers a nearest-neighbour query (JAN-241). The workflow adds the pinned package for the run only, until it becomes a Janus dependency. |
| `disk-space` | No | Reports free space and the user quota; a full quota has broken deploys before. |

Rules for adding checks:

- Add a check in the same change as the feature that needs the capability, and add its row here.
- Checks are read-only. They may inspect the host but never install, write, or restart anything.
- Put probes in the script, not free-form commands in the workflow, so each one is reviewed.
