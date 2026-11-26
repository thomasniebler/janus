# Deployment and Production Operations

## Runtime requirements

- **One process per database.** Janus runs as one long-lived process. The SQLite file, the in-process workers and the domain-change bus cannot be shared between processes; `janus start` takes a single-instance lock and refuses a second start.
- **Python 3.12+.** A host with an older system Python needs an app-managed runtime (uv-managed Python on Uberspace).
- **Persistent paths.** Database, Fernet key, logs, images and the lock live outside the install directory. Defaults: database `$XDG_DATA_HOME/janus/janus.db`, key `<data dir>/.janus_key`, images `<data dir>/images`, log `$XDG_STATE_HOME/janus/janus.log`. All are configurable.
- **Server mode.** `JANUS_SERVER_MODE` (or `--server-mode`) turns off desktop integrations: no tray icon, no desktop notifications.
- **Behind a proxy.** Set `JANUS_TRUSTED_PROXY_HOPS`, `JANUS_ALLOWED_HOSTS`, `ALLOWED_ORIGINS` and `JANUS_BASE_URL` to the public origin, so cookies, redirects and OAuth callbacks use the HTTPS URL. The web app uses same-origin API and WebSocket URLs.
- **Optional extras.** `whatsapp` (Neonize, needs glibc ≥ 2.34), `desktop` (pystray, Pillow), `documents` (markitdown; its onnxruntime needs glibc ≥ 2.27), `postgres` (psycopg2). Production on Uberspace (glibc 2.17) installs none: no WhatsApp, no tray, and only TXT, Markdown and CSV documents are ingested. Without `whatsapp`, the WhatsApp service reports "not installed" instead of failing.
- **Hardening defaults.** API docs and browser setup stay closed (`JANUS_PUBLIC_API_DOCS_ENABLED`, `JANUS_PUBLIC_SETUP_ENABLED` false); the first admin is created over SSH with `janus user bootstrap-admin`.

## Configuration

`config.py` (pydantic-settings) reads, highest priority first: init values, environment, `.env` in the working directory, then `$XDG_CONFIG_HOME/janus/settings.toml` (created by `janus config init`). Most keys use the `JANUS_` prefix; `DATABASE_URL`, `HOST`, `PORT`, `ALLOWED_ORIGINS`, `ALGORITHM` and `ACCESS_TOKEN_EXPIRE_MINUTES` do not. `janus config show` prints the effective values; `janus config doctor` checks them. Per-instance options edited in the UI (LLM providers, web search, images) live in the database `settings` row instead. The user-facing list is in [Configuration](../configuration.md).

## Packaging

`pyproject.toml` builds the `janus-assistant` distribution with hatchling; the import package and the command stay `janus`. `hatch_build.py` copies `alembic.ini`, `alembic/`, `frontend/dist/` and the in-app docs (`docs/docs`, `docs/zensical.toml`, `TECH_SPEC.md`, `CHANGELOG.md`) into the wheel under `janus/_data`. `src/janus/resources.py` resolves these paths in the wheel first and falls back to the repository in a checkout.

`scripts/wheel_smoke.sh` installs a wheel as a uv tool in a scratch location, migrates a fresh database and checks the install kind. CI runs it without a frontend (`task package:smoke`); the release runs it with `--require-frontend`.

## Install modes

`update_service.install_kind()` tells three supported installs apart:

| Kind | Detected by | How it runs |
| --- | --- | --- |
| Checkout | `.git` at the install root | `uv sync`, `uv run janus start` |
| uv tool | `uv-receipt.toml` in the environment | `uv tool install janus-assistant`, `janus start` |
| pipx | `pipx_metadata.json` | `pipx install janus-assistant`, `janus start` |

`janus service install` (`services/systemd_unit.py`, Linux) writes a systemd user unit that runs `<janus> start` with the installing shell's working directory, `PATH` and XDG variables (no other environment, so no secrets in the unit), `Restart=on-failure`, then enables it. `status`, `logs` and `uninstall` wrap systemctl and journalctl. The production host uses supervisord instead.

## Release workflow

Release Please (`.github/workflows/release-please.yml`, manifest mode) maintains a release PR that bumps `pyproject.toml`, `CHANGELOG.md`, `.release-please-manifest.json` and Janus's own entry in `uv.lock`. Tags are `vX.Y.Z`. When the release PR merges:

1. `build-and-release-artifacts` builds the frontend, the wheel and the sdist, smoke-tests the wheel, and uploads the wheel, the sdist and `janus-frontend-dist.tar.gz` to the GitHub release;
2. `publish-pypi` calls `publish-pypi.yml` once the `PYPI_ENABLED` variable is `true`;
3. `deploy-release` calls `deploy-uberspace.yml` for the tag.

### Publishing to PyPI

`.github/workflows/publish-pypi.yml` downloads the release's wheel and sdist, checks that the wheel contains `janus/_data/frontend/index.html` and `janus/_data/alembic.ini`, and uploads both with PyPI trusted publishing (OIDC, no token) from the `pypi` environment. A manual dispatch with `tag` publishes an existing release. The trusted publisher on PyPI lists both `publish-pypi.yml` and `release-please.yml` (the caller for releases).

## Uberspace deploy

`deploy-uberspace.yml` deploys a ref to the production host over SSH. The `deploy` job:

1. records the start time and resolves the ref, commit and expected version (`pyproject.toml` at the ref; a tag that disagrees stops the deploy);
2. checks out the deploy tooling separately (`.deploy-tools/`), so a rollback to an older tag still deploys with current scripts;
3. builds the frontend and Storybook on the runner and verifies the backend installs and imports for the glibc 2.17 target;
4. rsyncs the tree to the host (no `.git`, no `.env`, key or database);
5. runs `uv sync --frozen --no-dev` on the host, takes the [pre-release snapshot](#pre-release-snapshot), runs `alembic upgrade head` and `janus config doctor` (with `uv run --no-sync`, so nothing is re-synced);
6. restarts the supervisord program and waits for the health endpoint to report the expected version.

The production deploy has no `.git`, so it never updates itself. All deploy jobs share the non-cancelling `deploy-uberspace-production` concurrency group.

### Deploy target configuration

Nothing host-specific is committed. The deploy, capability-check and nightly workflows read the target from repository secrets and variables:

| Name | Kind | Required | Default | Meaning |
| --- | --- | --- | --- | --- |
| `DEPLOY_ENABLED` | variable | yes | | `true` turns on the deploy, capability-check and nightly-probe jobs |
| `DEPLOY_SSH_TARGET` | secret | yes | | `user@host` |
| `JANUS_PUBLIC_URL` | secret | yes | | public base URL for version check, validation and probes |
| `DEPLOY_PATH` | variable | no | `janus` | checkout directory on the host |
| `DEPLOY_SERVICE` | variable | no | `janus` | supervisord program name |

The SSH key and known-hosts entry are the secrets `UBERSPACE_SSH_PRIVATE_KEY` and `UBERSPACE_KNOWN_HOSTS`. Host and URL are secrets so logs mask them; jobs also mask the SSH user. Without `DEPLOY_ENABLED=true` every job skips itself, so forks never deploy. The host needs SSH, rsync, `uv` in `~/.local/bin` and supervisord.

## Pre-release snapshot

Before migrating, `scripts/pre_release_snapshot.py` (streamed over SSH into the production environment) copies the live SQLite database with SQLite's online backup API to `janus.pre-release.db` next to `janus.db`. There is one snapshot; each release replaces it. If the copy fails or free space would drop below 256 MiB, the partial file is removed and the deploy stops before `alembic upgrade`. A PostgreSQL `DATABASE_URL` is skipped. The key file is not copied.

Manual rollback on the host:

1. `supervisorctl stop janus`;
2. move the failed `janus.db` aside together with its `-wal` and `-shm` files, then copy `janus.pre-release.db` to `janus.db`;
3. run the deploy workflow with `ref` set to the previous tag (its migrations are a no-op).

Everything written after the snapshot is lost.

## Post-release validation

After every deploy, `deploy-uberspace.yml` runs `validate` right after the restart and `soak` 15 minutes later (the job waits with `sleep 900`). Each runs `scripts/release_validation.py` on the runner against the public URL and `scripts/release_host_checks.py` on the host (through `scripts/release_host_checks.sh`, retrying a dropped SSH connection up to three times). Reports go to the job summary and a `release-check-*` artifact.

| Check | Where | Required | Proves |
| --- | --- | --- | --- |
| `health-version` | public | yes | `/api/status/health` answers 200 with the deployed version |
| `frontend-assets` | public | yes | `index.html` and its assets are served |
| `api-docs-closed` | public | yes | no OpenAPI schema or Swagger UI |
| `setup-closed` | public | yes | `/api/auth/setup` answers 404 |
| `chat-ws-anonymous` | public | yes | `/ws/chat` refuses a client without a session |
| `tls-certificate` | public | yes | valid for at least 14 more days |
| `trigger-worker` | signed in | yes | the trigger worker is enabled and running |
| `status-overview` | signed in | yes | no service `degraded` or `outage` |
| `smoke-user` | public | no | warns when the smoke-test secrets are missing |
| `installed-version` | host | yes | the installed distribution is the deployed version |
| `alembic-at-head` | host | yes | the database is at every head |
| `supervisor-restarted` | host | yes | `RUNNING` and younger than the restart |
| `log-tracebacks` | host | yes | no traceback or `CRITICAL` since the restart |
| `log-errors` | host | no | counts `ERROR` records |
| `trigger-queue` | host | yes | no run due and still queued for more than 15 minutes |
| `disk-space` | host | no | free space |

Signed-in checks use a dedicated non-admin user (`JANUS_SMOKE_USERNAME`, `JANUS_SMOKE_PASSWORD` in the `production` environment). Host checks are read-only. A failure fails the workflow run; nothing is filed automatically.

## Deploy and availability metrics

`scripts/ops_metrics.py` (standard library only; `task ops:*` runs it locally) holds the logic.

- **Version check.** A deploy is finished only when `/api/status/health` answers 200 with the expected version: up to 12 attempts, 10 s apart. 200 with another version fails; so does 500 with the right one.
- **Deploy duration.** From the deploy job's first step to the first successful version check. The record also carries restart-to-healthy time. A deploy that never verifies is recorded as `failure` without a duration. The `metrics` job runs for failed deploys too.
- **Sampled availability.** `nightly-health-check.yml` (daily, 04:17 UTC) takes 5 samples 30 s apart; a sample is healthy on 200 with `status: ok`. Availability is the share of healthy samples over 7 and 30 days, a sample rate rather than continuous uptime. The probe fails only when no sample is healthy.
- **History.** Records are appended as JSON Lines to the orphan branch `ops-metrics` (`deploys/deploys.jsonl`, `availability/samples.jsonl`) through `scripts/history_branch.py`: append-only, never forced, retried on races. `task ops:export DATASET=deploys|availability FORMAT=csv|jsonl` exports them.
- **Schemas.** `janus.version-check/1`, `janus.deploy-metrics/1`, `janus.availability/1`. Timestamps are UTC ISO 8601. `ensure_exportable` refuses records with secret-looking keys or values; error details are stored as exception types only.

## Self-hosted auto-update

A checkout or tool install updates itself; the hosted instance does not. `services/update_service.py`, run by the `auto-update` worker (`workers/update.py`) every `JANUS_AUTO_UPDATE_INTERVAL_MINUTES` (default 60, minimum 15) or by `janus update`.

- **Check.** `GET https://api.github.com/repos/<JANUS_AUTO_UPDATE_REPOSITORY>/releases/latest`. The tag must be `vX.Y.Z` and newer than the installed version.
- **Eligibility.** `JANUS_AUTO_UPDATE_ENABLED` (default on). A checkout also needs `git` and `uv`, its own `.venv`, no tracked changes, and `HEAD` on `main` or detached at a release tag. A tool install needs its installer on `PATH`. Ineligible installs are logged once and never updated.
- **Install (checkout).** Download `janus-frontend-dist.tar.gz` and verify its SHA-256 against the digest GitHub records; take the pre-release snapshot; fetch the tag from the configured repository; fast-forward `main` or check out the tag; `uv sync --frozen --inexact` with every installed extra the new `pyproject.toml` defines; import `janus.main` and run the migration in child processes on the new code; swap in the new frontend.
- **Install (tool).** `services/update_tool_install.py`: download the release wheel and verify its digest; keep the running release's wheel for rollback; snapshot; reinstall with `uv tool install --force` or `pipx install --force`, keeping detected extras, `--with` packages from the uv receipt and pipx-injected packages; then the same import and migration checks.
- **Lock.** The whole install holds an exclusive lock (`janus-update.lock`, in `.git` or the XDG state directory), so the worker and `janus update` never overlap.
- **Rollback.** A failure after the install started restores the previous commit or wheel, re-syncs, and restores the snapshot once migrations had begun. Failures are `ERROR` events (source `AutoUpdate`); the running process keeps serving.
- **Restart and boot recovery.** The worker writes a pending record, stops uvicorn, and `janus start` re-executes itself (`os.execv`, same PID). The new process confirms the release once uvicorn starts. If it fails before that, `update_boot.recover_on_failed_boot()` rolls back code, environment and database, marks the tag to skip, and re-executes the previous release.
- `janus update --check` reports the latest release and eligibility; `janus update` installs without restarting a running Janus.

## Deployment capability checks

`scripts/deployment_capabilities.py` records, as runnable probes, what Janus needs from the production host. The `Deployment capability checks` workflow (`.github/workflows/deployment-capabilities.yml`, manual dispatch) streams it over SSH into the production environment (`uv run --no-sync`) and fails when a required check fails. It shares the deploy concurrency group.

| Check | Required | Why |
| --- | --- | --- |
| `python-version` | yes | Janus needs Python 3.12+ |
| `sqlite-fts5` | no | ranked keyword search; without it, substring matching |
| `sqlite-load-extension` | yes | `sqlite3` must support `enable_load_extension` for sqlite-vec |
| `sqlite-vec` | yes | sqlite-vec loads and answers a nearest-neighbour query |
| `disk-space` | no | free space and user quota |

Rules for adding checks:

- Add a check in the same change as the feature that needs it, with its row here.
- Checks are read-only: they never install, write or restart anything.
- Put probes in the script, never free-form commands in a workflow.
