# GitHub Metadata Code Map

Scope: GitHub-specific repository metadata under `.github/`.

## Responsibilities

- define GitHub Actions workflows
- keep GitHub-only repository metadata separate from Codex instructions, agents, and skills

## File and submodule map

| Path | Responsibility |
| --- | --- |
| `workflows/ci.yml` | CI workflow for tests, linting, and related repository checks (including `uv lock --check`, the strict mypy scope, backend statement/branch coverage floors, diff-cover on changed lines, frontend coverage floors, the coverage report artifacts, and the `openapi-check` drift check of the committed OpenAPI schema and generated frontend API types, JAN-139). On pull requests a `changes` job (`scripts/ci_paths.py`) selects the jobs the changed paths can affect, and it calls `semgrep.yml` and `dependency-audit.yml` the same way. A draft PR runs only the cheap checks (`full=false` skips the test steps and defers migration tests, e2e and Semgrep); the suites run once the PR is ready for review (`ready_for_review` re-runs CI). Merges to `main` are not re-run per push: a nightly scheduled run (or a manual dispatch) runs every CI job, and the nightly run skips itself when `main` has not moved. The Playwright suite runs as 4 `e2e-tests` shards whose blob reports `e2e-report` merges into one HTML report (JAN-345). Newer pushes cancel a PR's older run; every job has a timeout. The gates and the selection are described in `docs/docs/tech-spec/architecture-overview.md#quality-gates` |
| `workflows/pr-title.yml` | Conventional-commit PR title check for squash merges; also runs when a title is edited, without re-running CI (JAN-314) |
| `workflows/deploy-uberspace.yml` | Deployment workflow for the Uberspace target: pre-release database snapshot (`scripts/pre_release_snapshot.py`), migrate, restart, then the `validate` and 15-minute `soak` post-release checks (`scripts/release_validation.py`, `scripts/release_host_checks.sh` running `scripts/release_host_checks.py` on the host); the deploy job waits for the health endpoint to report the expected release version and the `metrics` job appends deploy duration and outcome to the `ops-metrics` branch (`scripts/ops_metrics.py`, JAN-269) |
| `workflows/deployment-capabilities.yml` | Manually dispatched, read-only run of `scripts/deployment_capabilities.py` on the Uberspace host over the deploy SSH connection |
| `workflows/dependency-audit.yml` | Known-vulnerability scan of `uv.lock` (pip-audit) and the npm lockfiles (npm audit) via `task audit:deps`: daily and on dispatch over all of `main`, and called by `ci.yml` on pull requests that change dependencies or the audit (JAN-268, JAN-313) |
| `workflows/semgrep.yml` | Semgrep security scan (pinned Semgrep and community rules plus `.semgrep/`) via `task security:semgrep`: weekly and on dispatch over all of `main`, and called by `ci.yml` on pull requests that change scanned source or the scan configuration, with SARIF/JSON artifacts (JAN-267, JAN-313) |
| `workflows/quality-report.yml` | Weekly (and dispatched) quality report (JAN-265/266/270): `scripts/quality_report.py` collects tests, coverage, complexity, module size, duplication (production gated at 1%), mypy, Semgrep, dependency-audit and deployment metrics into one fixed-schema JSON report, compares it with the previous main report, lists the top-10 hotspots in the job summary, and the `history` job appends main reports to the `quality-history` branch; plus the knip exports/types and low-confidence Vulture findings the CI gates skip, and the last seven days of GitHub Actions cost and timing against the frozen baseline (`scripts/actions_cost_report.py`, JAN-308); uploads the `quality-report` artifact (90 days) and fails last when a gated check failed or errored |
| `workflows/structural-review.yml` | Monthly (2nd, 07:41 UTC) and dispatched input for the agent-assisted structural review (JAN-271): `scripts/structural_review.py bundle` writes the window of main since the last recorded review, the newest main quality reports from `quality-history`, the main diff, the CODEMAPs and router metrics to the `structural-review` artifact (90 days). `contents: read`, no secrets, no LLM, no writes; the job summary reports the agent review itself as blocked in CI (it runs as `code-quality-auditor` Mode 3 in a Claude Code or Codex session) |
| `workflows/nightly-health-check.yml` | Scheduled nightly availability sample of the hosted Janus health endpoint (5 samples, 30 s apart, `scripts/ops_metrics.py probe`); the `record` job appends them to the `ops-metrics` branch (JAN-269); the probe job fails when no sample is healthy |
| `workflows/release-please.yml` | Release Please automation workflow, in manifest mode, with the `RELEASE_PLEASE_TOKEN` secret so its release PR runs the required checks: it reads `../release-please-config.json` and `../.release-please-manifest.json`; it builds the frontend, then the `janus_assistant` wheel and sdist (smoke-tested with `scripts/wheel_smoke.sh`), uploads them with `janus-frontend-dist.tar.gz` (the prebuilt frontend a git checkout's auto-update installs), calls `publish-pypi.yml` when `PYPI_ENABLED` is `true`, and deploys |
| `workflows/publish-pypi.yml` | Uploads a release's wheel and sdist to PyPI as `janus-assistant` with trusted publishing from the `pypi` environment; called by `release-please.yml` or run by hand with a `tag` input |
| `ci-cost/` | Frozen GitHub Actions baseline (JAN-308): `workload.json` (the latest 20 pull request updates with their changed-path categories and weights), the reference for every later cost comparison |
| `dependabot.yml` | Dependabot config for uv, npm (root and `frontend/`), and GitHub Actions dependency updates: one combined `weekly-dependencies` multi-ecosystem PR per week for uv and npm, plus one weekly `github-actions` group PR kept apart because the self-evolution loop fences `.github/` (security updates still arrive individually) |

## Read-next guidance

- GitHub Actions CI -> `workflows/ci.yml` (job selection in `scripts/ci_paths.py`)
- GitHub Actions minutes and timing -> `scripts/actions_cost_report.py`, workload model in `ci-cost/`
- Deployment automation -> `workflows/deploy-uberspace.yml`
- Dependency vulnerability audit -> `workflows/dependency-audit.yml` (logic in `scripts/dependency_audit.py`, policy in `docs/docs/tech-spec/auth-security.md`)
- Semgrep security scan -> `workflows/semgrep.yml` (logic in `scripts/semgrep_scan.py`, Janus rules in `.semgrep/`, policy in `docs/docs/tech-spec/auth-security.md`)
- Checking what the deployment host supports -> `workflows/deployment-capabilities.yml` (probes live in `scripts/deployment_capabilities.py`)
- Hosted-instance uptime check and sampled availability -> `workflows/nightly-health-check.yml`, then `../docs/docs/tech-spec/deployment-operations.md#deploy-and-availability-metrics`
- Post-release validation -> `workflows/deploy-uberspace.yml`, then `../docs/docs/tech-spec/deployment-operations.md#post-release-validation`
- Release automation -> `workflows/release-please.yml`, then `../release-please-config.json`; PyPI -> `workflows/publish-pypi.yml`
- Dependency update policy -> `dependabot.yml`
- Coding-agent repository instructions -> `../AGENTS.md`
- Agent skills -> `../.agents/CODEMAP.md`
