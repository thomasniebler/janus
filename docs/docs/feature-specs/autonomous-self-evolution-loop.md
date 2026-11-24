# Autonomous Self-Evolution Loop

**Status:** Shipped (opt-in, off by default; revert PRs are opened automatically but merged only through the normal merge gates, see [Follow-ups](#follow-ups))
**Linear:** JAN-320 (children JAN-324 detect & file, JAN-323 start job, JAN-322 merge & release, JAN-321 verify)
**Depends on:** GitHub and Linear integrations (instance-wide tokens), durable trigger machinery and one-time follow-up definitions (JAN-228, JAN-257), unattended tool policy (JAN-255), prompt-injection guard (JAN-127), a Claude Code Routine with an API trigger (claude.ai Pro/Max/Team/Enterprise with Claude Code on the web), release-please, the Uberspace deploy on release

## Goal

Janus (Aurelius) closes the loop on its own development:

1. **Detect & file** -- notice a capability gap or a system error and file a
   Linear issue with a clear title and description.
2. **Start a job** -- start an implementation job in Claude Code by firing a
   Claude Code Routine through its API trigger, with the Linear issue id and a
   detailed brief.
3. **Merge & release** -- merge the pull request that job opened, then the
   release-please PR.
4. **Verify** -- after the release merge, run a one-time production self-check;
   on failure, notify the owner, file an issue, and open a revert PR.

## Design decision: no human gate

The owner deliberately removed human review from this loop: every step runs
autonomously; humans observe but do not approve. This is a conscious departure
from "Janus proposes, the user approves" (JAN-235) **for this loop only** --
agent-proposed automations, the unattended tool switch, `LOCKED_UNATTENDED`,
the trigger chain guard and every other fence are unchanged.

Safety therefore comes from gates enforced **in code**, not in prompts. Each
gate below is checked by the service layer whatever the model says, and each
has a test in `tests/test_self_evolution.py`.

## Guardrails

| Guardrail | Enforcement |
| --- | --- |
| **Opt-in switch**, per user, default **off** | `self_evolution_settings.enabled`; only an admin's switch counts (the loop uses the instance-wide GitHub/Linear credentials). Every service entry point calls `require_enabled`. The registry entry needs the `self_evolution` capability, which `capability_profile` grants only while the switch is on and GitHub and Linear are configured. Its unattended allowance follows the same switch (`tool_policy.SWITCH_GOVERNED`); it cannot be flipped on the Tools page, and a trigger definition naming it never overrides the switch. |
| **Daily caps** per user, rolling 24 h | `self_evolution_service.DAILY_CAPS`: 5 issues filed, 3 implementation jobs, 3 feature merges (job, Dependabot and revert PRs together), 3 release merges, 3 self-checks, 3 revert PRs. Counted from `self_evolution_actions`; an attempt reserves its row *before* any outbound call and is refused if its rank exceeds the cap, so concurrent calls cannot overshoot. Refusals and duplicates do not count; failed calls do. |
| **Merge scope** (what may be merged at all) | `self_evolution_provenance.provenance`: only (a) a PR whose description names the Claude Code session (id or URL, word-bounded) of a job *this user's* loop recorded as started within the last 14 days for the same repository (`data["repo"]`; a session started for another repository refuses); (b) a Dependabot minor/patch bump -- opened by `dependabot[bot]` from a `dependabot/...` branch, a single commit authored by `dependabot[bot]`, and every entry of the commit message's `updated-dependencies:` block has `update-type: version-update:semver-patch` or `version-update:semver-minor` (missing or unparseable metadata, or any major bump, refuses); (c) a revert PR the loop opened itself. Release-please PRs go through their own tool. Everything else is refused with the reason. |
| **Scope fence** (refuses merge) | `self_evolution_service.SCOPE_FENCE`, the one list: `src/janus/security.py`, `src/janus/services/auth_service.py`, `src/janus/tool_policy.py`, `src/janus/prompt_injection.py`, `src/janus/services/injection_classifier.py`, `.github/`, `release-please-config.json`, the deploy/release scripts (`scripts/janus-up.sh`, `deployment_capabilities.py`, `pre_release_snapshot.py`, `release_host_checks.{py,sh}`, `release_validation.py`), `alembic/`, `alembic.ini`, and any path whose name mentions self-evolution (the loop's own modules, tests, UI and this page). Case-insensitive; renames are checked under the old and the new name; a PR whose files cannot all be listed is refused. |
| **CI gate** | Every check run on the PR head commit has completed with `success`/`skipped`/`neutral`, every commit status is `success` (nothing pending, failed or errored), and at least one check exists. |
| **Merge-state gate** | Open, not a draft, branch in the repository itself (no forks), base is the repository's default branch, `mergeable == true` and `mergeable_state` `clean`/`has_hooks`. The merge call pins the checked head SHA, so a push in between makes GitHub refuse it. |
| **Release ordering** | The release-please PR (branch `release-please--*`) is merged only after the loop itself recorded merging the named feature PR, the feature is merged on GitHub, and the release PR's head already contains the feature's merge commit (`compare` status `ahead`/`identical`). It may only change `CHANGELOG.md`, `.release-please-manifest.json`, `pyproject.toml`, `uv.lock`. Each feature is released once. |
| **Self-check after release** | Only for a release PR the loop merged, once per release. Runs in code (no model call), GETs only paths on the configured production base URL, does not follow redirects. |
| **Fixed targets** | Tools never take a repository, URL or token argument; they come from the owner's settings. The Routine fire URL is built in code from a constant base and the validated `trig_...` id; settings hold no URL for it. |
| **Injection escalation** | The entry is `confirmation="required"`, so after a suspected prompt injection in the same step every self-evolution tool answers `BLOCKED` (JAN-127). Not delegable (write entry). Not proposable as an automation (JAN-235 allowlist). In chat each call asks for approval. |
| **Observability** | Every attempt, refusal and outcome is a row in `self_evolution_actions` and an event-log entry (source `SelfEvolution`, owner-scoped), and is listed on the settings card. |

**Deviation from the Dependabot rule.** The repository's convention (CLAUDE.md,
`combine-dependabot-prs` skill) is to never merge Dependabot PRs one by one but
to combine them into one validated PR. The loop deliberately merges individual
minor/patch Dependabot PRs, at the owner's request; major bumps and anything
without usable Dependabot metadata are still left to that combined flow.
Dependabot's routine uv/npm version updates arrive as one weekly grouped PR
(`weekly-dependencies` in `.github/dependabot.yml`), so the loop merges that
PR only when every bump in it is minor/patch. GitHub Actions bumps get their
own weekly group PR, kept out of it because the scope fence covers
`.github/`; the loop never merges that one.

## Flow and tools

The `self_evolution` toolbox (`agent_toolboxes/self_evolution.py`) offers:

- `self_evolution_status` -- switch state, remaining caps, recent actions.
- `file_improvement_issue(kind, title, description)` (JAN-324) -- `kind` is
  `capability_gap` or `system_error`; the title is prefixed with the kind and
  the description gets a footer saying no human reviewed it. Before filing it
  compares the title with the team's 100 newest open issues (sequence ratio or
  word overlap ≥ 0.8) and returns the existing issue instead of a duplicate.
  Reuses `LinearService.list_team_issues`/`create_issue`.
- `start_implementation_job(issue_identifier, prompt)` (JAN-323) -- fires the
  Routine; one job per issue per day.
- `merge_feature_pr(pull_number)` and `merge_release_pr(release_pull_number,
  feature_pull_number)` (JAN-322) -- squash-merge behind the gates above.
- `schedule_production_self_check(release_pull_number, checks, delay_minutes)`
  (JAN-321) -- 1-5 checks of `{path, expect_status, expect_text}`, 10 min to
  24 h ahead.

A gate refusal answers `REFUSED: ... Nothing was done.`

### Claude Code Routine API trigger

Contract as documented by Anthropic
([Routines](https://code.claude.com/docs/en/routines),
[fire endpoint](https://platform.claude.com/docs/en/api/claude-code/routines-fire)),
implemented in `self_evolution_intake.start_job`:

```http
POST https://api.anthropic.com/v1/claude_code/routines/{trig_id}/fire
Authorization: Bearer sk-ant-oat01-...            # the routine's own token, stored Fernet-encrypted
anthropic-version: 2023-06-01                      # the only accepted value
anthropic-beta: experimental-cc-routine-2026-04-01 # optional per the reference; always sent
Content-Type: application/json

{"text": "<guardrail preamble + Linear issue id + Janus's brief>"}
```

- **Configuration** is the trigger id (validated `^trig_[A-Za-z0-9]+$`) and the
  token (prefix `sk-ant-oat01-`, generated once in the claude.ai web UI and
  scoped to that single routine). The URL is built in code.
- **Body:** `text` is free-form, at most 65,536 characters; Janus caps its
  brief at 20,000 and cuts anything that would exceed the limit with a marker.
  In the session the text arrives wrapped in a `<routine-fire-payload>` block
  labelled untrusted, so the routine's saved prompt must tell it to act on
  that payload.
- **Success** (`200`, once the session exists):
  `{"type": "routine_fire", "claude_code_session_id": "session_...",
  "claude_code_session_url": "https://claude.ai/code/session_..."}`. Both are
  recorded on the `job_started` row; they are what ties the resulting PR to the
  job (merge scope (a)). A `200` without a valid session id
  (`^session_[A-Za-z0-9]+$`) and URL (`https://claude.ai/code/session_...`) is
  recorded as a *failed* start (`started: false`): it cannot vouch for a PR,
  and it does not block another attempt for the same issue.
- **Errors** are `{"type": "error", "error": {"type": ..., "message": ...}}`:
  `400 invalid_request_error` (also a paused routine or text too long), `401`,
  `403`, `404`, `429 rate_limit_error` (with `Retry-After`), `500 api_error`,
  `503 overloaded_error`. The status, error type, message and `Retry-After`
  are recorded (never the token); 429/5xx answer "try again later".
- **No idempotency key:** every successful call starts a new session, so a fire
  is never retried automatically, and at most one job per issue per day is
  started.
- **Limits:** 30 fires per hour per routine, 100 API fires per hour per
  account. The endpoint is experimental. It needs a claude.ai Pro, Max, Team or
  Enterprise account with Claude Code on the web.

The `text` always starts with fixed rules: work on a branch of the repository
(not a fork) and open exactly one PR against the default branch whose
description contains the Linear issue id and the session URL, never merge, and
do not touch the fenced paths (listed).

### Verification

`schedule_self_check` stores the checks on a `self_check` action row and
queues a one-time run through the durable trigger machinery (an agent-origin
one-time definition plus `ScheduledTriggerSpec` + `enqueue_trigger`, as
`create_scheduled_trigger` does). The definition's `builtin_key` is
`selfevo.check:<row id>`; `builtin_triggers.builtin_for` maps that prefix to a
`prepare` hook that runs `self_evolution_checks.run_self_check` and returns
`skip_output`, so the run completes without a model call. A retried run never
re-runs a finished check. The definition appears on Settings -> Triggers;
switching it off cancels the run (it cannot be deleted, like built-ins).

On failure Janus:

1. sends a desktop alert (`NotificationService.notify_alert`; skipped in
   server mode) and records an `ERROR` event -- Janus's existing notification
   paths;
2. files a `system_error` issue through `file_issue` (dedupe and cap apply);
3. opens a revert PR of the feature PR (only one the loop merged) with GitHub's
   GraphQL `revertPullRequest` mutation. The revert PR is **not** merged
   automatically: it goes through `merge_feature_pr` and all its gates when
   Janus (or a person) merges it.

Steps 2 and 3 are skipped when the switch was turned off in the meantime.

## Configuration

**Settings → Tools & Connections → Self-evolution** (admin only; `GET/PATCH
/api/self-evolution/settings`): the switch, the routine's trigger id and API
token, the GitHub repository (defaults to the GitHub default repo), and the
production base URL. The card also shows usage against the caps, the scope
fence, and the recent actions. GitHub and Linear (with a default team) must be
configured. See [Configuration](../configuration.md#self-evolution-loop) for
setting up the routine itself.

## Data model

- `self_evolution_settings` -- one row per user (migration `b7d1e4a9c2f6`):
  `enabled`, `routine_trigger_id`, encrypted `routine_token`, `github_repo`,
  `production_base_url`.
- `self_evolution_actions` -- the audit trail and cap source:
  `action` (`issue_filed`, `job_started`, `feature_merged`, `release_merged`,
  `self_check`, `revert_opened`), `status` (`pending`, `ok`, `failed`,
  `refused`, `duplicate`, `passed`), `subject`, `detail`, `data` (for jobs the
  session id and URL; for merges the kind -- `job`, `dependabot`, `revert` --
  and what vouched for it).

## Follow-ups

- **Revert PRs are opened, not merged.** Merging one goes through the normal
  merge gates and caps; there is no automatic "merge the revert once green".
- **Self-checks are HTTP checks only.** Free-form, model-evaluated test cases
  would need a verdict the code can trust; not built.
- **Server-mode notifications.** In server mode the desktop alert is skipped;
  the `ERROR` event (and the Linear issue) are the notification. A push/mail
  channel would make failures harder to miss.
- **Session provenance is text in the PR description.** Anyone with write
  access to the repository could paste a recorded session URL into another
  PR's description; forks are refused. Matching on the head branch or commit
  trailers of the session would be stronger if Claude Code exposes them.
