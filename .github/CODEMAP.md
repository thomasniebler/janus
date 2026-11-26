# GitHub Metadata Code Map

Scope: GitHub-specific repository metadata under `.github/`.

## File and submodule map

| Path | Responsibility |
| --- | --- |
| `workflows/` | GitHub Actions: CI, PR-title check, deploy, release and PyPI publish, scheduled audits, quality report, structural review, health check, deployment capability probe |
| `ci-cost/` | Frozen GitHub Actions cost baseline used by later cost comparisons |
| `dependabot.yml` | Dependabot update configuration |

## Where to look

- CI gates and job selection -> `workflows/ci.yml`, `../scripts/ci_paths.py`, then `../docs/docs/tech-spec/architecture-overview.md#quality-gates`
- Deploy and post-release validation -> `workflows/deploy-uberspace.yml`, then `../docs/docs/tech-spec/deployment-operations.md`
- Release automation -> `workflows/release-please.yml`, `../release-please-config.json`
- Dependency audit and Semgrep policy -> `../docs/docs/tech-spec/auth-security.md`
- Logic behind most workflows -> `../scripts/` (named after the workflow's purpose)
