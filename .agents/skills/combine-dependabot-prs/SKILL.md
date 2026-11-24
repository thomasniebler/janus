---
name: combine-dependabot-prs
description: Combine all open Dependabot pull requests into one PR. Merge every bump not broken by its own change into a single branch, regenerate lockfiles with the package tooling, validate, open one combined PR, and close the originals with a link to it. Use when asked to "compact", "combine", "batch", or "merge" the dependency or Dependabot PRs.
---

# Combine Dependabot PRs

This is the standard procedure for Janus dependency bumps. Dependabot (`.github/dependabot.yml`) puts every version update for `uv` at `/` and `npm` at `/` and `/frontend` into one weekly `weekly-dependencies` multi-ecosystem PR, and every `github-actions` update into one weekly `github-actions` group PR. Security-update PRs are not batched and arrive as advisories land; they can also target `modules/todo-chat/frontend`. Use this procedure when more than one Dependabot PR is open (the weekly group PRs plus security PRs, or PRs left from before the grouping). Do not merge them one by one. Combine them into one reviewed PR. Earlier examples: #405 and #428.

## 1. Inventory

1. List the open PRs authored by `dependabot[bot]`. Skip release-please and human PRs.
2. For each one, record the package(s), the from → to versions, the update type (patch / minor / **major**), the directory it touches, and the CI result on its head commit. Get the CI result from the PR's check runs. Do not guess it from the title.
3. Fetch the branches: `git fetch origin main 'refs/heads/dependabot/*:refs/remotes/origin/dependabot/*'`.

## 2. Decide what goes in

- **In:** every bump whose own CI is green.
- **Red, but not the bump's fault:** classify these before you exclude anything. They can still go in, but only if local validation (step 4) passes:
  - The same check is red on the base commit the PR was built on, and the fix has since landed on `main`.
  - The job never started (0 steps, no log, a "job was not started" / billing / runner annotation). That bump is **untested**. Your local run is its only test, so say that in the PR body.
- **Out:** any bump that its own change breaks. Read the failing job log. Write down the root cause with the key error line, for example a new lint rule in a major ESLint config bump. A red CI run is never "a flake" without evidence. If the bump needs a code fix, the fix goes in a separate PR (see Rules). Do not fold it into the combined PR, unless the user asks.
- **Ask before including:** a bump that is green but touches a constraint documented in `.github/dependabot.yml` or `pyproject.toml`, for example the Uberspace glibc 2.17 wheel problem (JAN-191: google-re2, greenlet, lxml major; pillow is no longer installed on the host since it moved to the optional `whatsapp`/`desktop` extras) or the `bcrypt<4` pin for passlib. Green CI does not prove the bump installs on the production host.
- When a bump is left out for a structural reason that will come back every week, propose a `dependabot.yml` ignore rule. Write the rule with a comment in the existing style. Do not add the rule silently.

## 3. Build the combined branch

1. Start from the latest `origin/main`, on the branch the session was given. Otherwise use `chore/combine-dependabot-<date>`.
2. Merge each included branch with `git merge --no-edit origin/dependabot/...`. Use merge commits. Do not cherry-pick or squash locally. The combined PR is squash-merged.
3. Resolve conflicts:
   - `package.json` / `pyproject.toml`: most conflicts are bumps on lines next to each other. Keep both sides.
   - **Never hand-edit a lockfile.** Take one side, then regenerate the lockfile:
     - `frontend/package-lock.json`: `cd frontend && npm install --package-lock-only --ignore-scripts`
     - `package-lock.json` at the repository root, or `modules/todo-chat/frontend/package-lock.json`: run the same command in that directory.
     - `uv.lock`: `uv lock`
   - Regenerating a lockfile keeps every pin that is still valid. It does **not** re-apply a version bump that only lived in the side you dropped. A lock-only or transitive bump (for example a Dependabot security group, or `oauthlib`/`urllib3` in a uv group) can therefore silently revert. Recover it by naming each bump explicitly:
     - uv: `uv lock --upgrade-package <pkg>==<target>`
     - npm: `npm install --package-lock-only --ignore-scripts <pkg>@<target>`. Run this for a direct dependency only. For a transitive one, run `npm update <pkg> --package-lock-only`.
   - After you regenerate a lockfile, check that every included bump is present at its target version, for example with `git diff origin/main -- uv.lock '*package-lock.json'`. Do not move on until each one is.
4. Check the lockfiles: `uv lock --check`, then `npm ci` in every npm directory the batch touched (`frontend/`, the repository root, `modules/todo-chat/frontend`).

## 4. Validate locally (before pushing)

- Backend: `uv sync`, `uv run ruff check .`, and `uv run pytest`. Pass `--ignore=tests/test_processor.py` if it hangs while waiting for Ollama, and say so in the PR.
- Frontend: `npm run lint`, `npm run build`, and `npx vitest run` in `frontend/`.
- Root npm (Tailwind assets outside Vite): if the root `package.json` or `package-lock.json` changed, run `npm ci` and `npm run build:css` at the repository root. CI only installs `frontend/`, so nothing else exercises this.
- Todo Chat: if `modules/todo-chat/frontend` changed, run `npm ci` and `npm run build` there. CI does not build it either.
- Major bumps: read the upstream changelog or migration notes. List any breaking changes in the PR body, even when the tests pass.
- If a bump that was green on its own breaks only when combined, drop it from the combined branch. Leave its original PR open and explain why in the PR body.

## 5. Open the PR

Title: `chore(deps): combine N clean dependabot bumps`. Body sections:

- **Summary**: the included PRs, grouped by Backend (uv), Frontend (npm), Todo Chat (npm), Actions. One line each: `#NNN \`pkg\` a → b`. Mark *(major)* and *(dev)*.
- Any conflicts, and how each lockfile was regenerated.
- **Left out on purpose**: each excluded PR, with the failing check, its root cause, and what would unblock it (a code change or an ignore rule).
- **Test plan**: the commands you ran, with pass and fail counts.

Use the repo's PR template if one exists. Subscribe to the PR's activity and drive it to green.

## 6. Close the originals

Close the originals **after** the combined PR is green, or once the user approves the plan. Close each included Dependabot PR with a one-line comment: `Superseded by #<combined>`. Dependabot then stops rebasing the PR. Its next weekly run reopens nothing that the combined PR has already merged. Leave excluded PRs open, unless the user wants them closed or an ignore rule now covers them. If an ignore rule covers one, close it with a comment that references the rule.

## Rules

- Never push to a `dependabot/*` branch, and never force-push one.
- Never change a test, or loosen a lint rule, just to make a bump pass. If a bump needs code changes, it is a separate PR.
- Do not touch `CHANGELOG.md` or the release-please PR. The release flow handles both.
