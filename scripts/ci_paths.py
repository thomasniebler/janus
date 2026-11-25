"""Select the CI jobs a pull request needs from the paths it changes (JAN-309, JAN-313).

``ci.yml`` runs a small ``changes`` job first. It lists the PR's changed files through the
GitHub API (merge-base semantics, renames counted under both names, paginated) and maps each
path to the jobs it can affect with :data:`RULES`. Every downstream job runs only when it
was selected. Any other event (the nightly run on ``main``, a manual dispatch) selects every
CI job, so ``main`` keeps complete validation.

A draft pull request skips the expensive suites (:data:`DRAFT_SKIPPED`, and the backend and
frontend test steps through the ``full`` output); they run once the PR is marked ready for
review. Lint, type checks, builds, the docs build and the drift checks still run on drafts.

Rules, in order of precedence (the first matching rule wins):

* A path no rule matches selects every job (:data:`FULL`), and so does a change to the
  selection itself, ``ci.yml``, ``Taskfile.yml``, or the Python/npm dependency definitions.
* ``pyproject.toml`` and ``uv.lock`` are compared table by table between the merge base and
  the PR head (:func:`refine_toml`): a change confined to tool configuration or to Janus's own
  version selects only the jobs that read it; a dependency change, or anything the comparison
  cannot place, keeps the full selection.
* A path matched by an empty rule (agent definitions, plain Markdown outside ``docs/``,
  release metadata, other workflows) selects no suite. The PR title check still runs.
* Detection fails closed: an API error, a missing PR number, or a file list that does not
  match the PR's ``changed_files`` count exits non-zero, which fails the ``changes`` job and
  with it the run.

Policy and the full matrix: ``docs/docs/tech-spec/architecture-overview.md#ci-job-selection``.

Usage::

    python3 scripts/ci_paths.py select --event pull_request --pr 123 --repo owner/name --draft false \
        --github-output "$GITHUB_OUTPUT" --summary "$GITHUB_STEP_SUMMARY"
    python3 scripts/ci_paths.py classify path/one path/two   # local check, prints the jobs

Standard library only, so the ``changes`` job needs no dependency install.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

BACKEND = "backend"
MIGRATIONS = "migrations"
FRONTEND = "frontend"
E2E = "e2e"
DOCS = "docs"
OPENAPI = "openapi"
SEMGREP = "semgrep"
DEPENDENCY_AUDIT = "dependency_audit"

#: Jobs ``ci.yml`` runs on the nightly ``main`` run and on a manual dispatch.
CI_JOBS: tuple[str, ...] = (BACKEND, MIGRATIONS, FRONTEND, E2E, DOCS, OPENAPI)
#: Security scans that also run on a schedule; on pull requests they follow the selection.
SECURITY_JOBS: tuple[str, ...] = (SEMGREP, DEPENDENCY_AUDIT)
ALL_JOBS: tuple[str, ...] = CI_JOBS + SECURITY_JOBS
FULL = frozenset(ALL_JOBS)
NONE: frozenset[str] = frozenset()
#: Jobs a draft pull request skips; they run once the PR is ready for review.
DRAFT_SKIPPED: tuple[str, ...] = (MIGRATIONS, E2E, SEMGREP)
#: Output naming whether the test steps inside the backend and frontend jobs run
#: (false only on a draft pull request).
FULL_OUTPUT = "full"

#: Backend tests that read the real documentation tree. A docs-only change runs them in the
#: ``docs-build`` job instead of the whole backend suite (``tests/test_ci_paths.py`` checks
#: that every test file reading ``docs/`` is listed here).
DOCS_TESTS: tuple[str, ...] = (
    "tests/test_docs_links.py",
    "tests/test_docs_tools.py",
    "tests/test_api.py",
)
#: ``pytest -k`` expression that limits :data:`DOCS_TESTS` to the documentation tests.
DOCS_TESTS_KEYWORD = "docs or technical_spec"

GITHUB_API = "https://api.github.com"
PER_PAGE = 100
#: The pull request files endpoint returns at most this many files.
GITHUB_FILES_LIMIT = 3000


@dataclass(frozen=True)
class Rule:
    """A path pattern (``*`` within a segment, ``**`` across segments) and its jobs."""

    pattern: str
    jobs: frozenset[str]
    reason: str

    def matches(self, path: str) -> bool:
        """Return whether ``path`` matches this rule's pattern."""
        return _compile(self.pattern).fullmatch(path) is not None


def _rule(pattern: str, jobs: Iterable[str], reason: str) -> Rule:
    return Rule(pattern, frozenset(jobs), reason)


# Backend code can change the OpenAPI schema, so it also selects the drift check (JAN-139).
_BACKEND_CODE = (BACKEND, E2E, SEMGREP, OPENAPI)
_FRONTEND_CODE = (FRONTEND, E2E, SEMGREP)
_FRONTEND_TOOLING = (FRONTEND, E2E)

#: Ordered rules; the first match wins, so specific paths come before their directories.
RULES: tuple[Rule, ...] = (
    # The selection itself and what every job runs through select everything.
    _rule("scripts/ci_paths.py", FULL, "the job selection itself"),
    _rule(".github/workflows/ci.yml", FULL, "the CI workflow"),
    _rule("Taskfile.yml", FULL, "every job runs through task"),
    _rule("pyproject.toml", FULL, "Python dependencies and tool configuration"),
    _rule("uv.lock", FULL, "Python dependency lock"),
    _rule(".python-version", FULL, "Python version"),
    _rule("hatch_build.py", (BACKEND,), "wheel build hook, checked by scripts/wheel_smoke.sh"),
    _rule(".gitignore", FULL, "ignore rules read by knip, jscpd and the docs build"),
    # Security scan definitions.
    _rule(".github/workflows/semgrep.yml", (SEMGREP, BACKEND), "Semgrep workflow"),
    _rule(".github/workflows/dependency-audit.yml", (DEPENDENCY_AUDIT, BACKEND), "dependency audit workflow"),
    _rule(".semgrep/**", (SEMGREP, BACKEND), "Janus Semgrep rules and their tests"),
    _rule(".semgrepignore", (SEMGREP,), "Semgrep scan scope"),
    _rule("scripts/semgrep_scan.py", (SEMGREP, BACKEND), "Semgrep wrapper and its tests"),
    _rule("scripts/dependency_audit.py", (DEPENDENCY_AUDIT, BACKEND, SEMGREP), "dependency audit and its tests"),
    # The offline OpenAPI export and the generated client it checks (JAN-139).
    _rule("scripts/export_openapi.py", (BACKEND, SEMGREP, OPENAPI), "OpenAPI export and its tests"),
    _rule("frontend/src/generated/**", (*_FRONTEND_CODE, OPENAPI), "generated OpenAPI schema and API types"),
    # Backend, migrations and scripts.
    _rule("alembic/**/*.md", NONE, "migration documentation"),
    _rule("alembic.ini", (BACKEND, MIGRATIONS, E2E), "Alembic configuration"),
    _rule("alembic/**", (BACKEND, MIGRATIONS, E2E), "migrations"),
    _rule("src/**/*.md", NONE, "code maps"),
    # What alembic/env.py and the PostgreSQL migration tests import (models, database and their imports).
    _rule("src/janus/models/*.py", (*_BACKEND_CODE, MIGRATIONS), "ORM schema (the janus.models package)"),
    _rule("src/janus/models_knowledge.py", (*_BACKEND_CODE, MIGRATIONS), "ORM schema"),
    _rule("src/janus/database.py", (*_BACKEND_CODE, MIGRATIONS), "database engine and schema bootstrap"),
    _rule("src/janus/config.py", (*_BACKEND_CODE, MIGRATIONS), "settings, including the database URL"),
    _rule("src/janus/runtime.py", (*_BACKEND_CODE, MIGRATIONS), "imported by database.py"),
    _rule("src/janus/time_utils.py", (*_BACKEND_CODE, MIGRATIONS), "imported by the janus.models package"),
    _rule("src/janus/services/database_copy_service.py", (*_BACKEND_CODE, MIGRATIONS), "SQLite to PostgreSQL copy"),
    _rule("src/janus/**", _BACKEND_CODE, "backend code"),
    _rule("src/**", _BACKEND_CODE, "backend package"),
    _rule("tests/test_database_migrations.py", (BACKEND, MIGRATIONS), "migration tests"),
    _rule("tests/test_postgres_migrations.py", (BACKEND, MIGRATIONS), "PostgreSQL migration tests"),
    _rule("tests/conftest.py", (BACKEND, MIGRATIONS), "shared test fixtures"),
    _rule("tests/**/*.md", NONE, "test documentation"),
    _rule("tests/**", (BACKEND,), "backend tests"),
    _rule("scripts/**/*.md", NONE, "script documentation"),
    _rule("scripts/**", (BACKEND, SEMGREP), "scripts and their tests"),
    _rule(".env.example", (BACKEND,), "example environment"),
    # Frontend.
    _rule("frontend/src/lib/domainChanges.ts", (*_FRONTEND_CODE, BACKEND), "read by tests/test_domain_changes.py"),
    _rule("frontend/**/*.md", NONE, "frontend documentation"),
    _rule("frontend/src/**", _FRONTEND_CODE, "frontend code"),
    _rule("frontend/e2e/**", (E2E,), "end-to-end tests"),
    _rule("frontend/playwright.config.ts", (E2E,), "Playwright configuration"),
    # openapi-typescript and the generate:types scripts live here, so they select the drift check.
    _rule("frontend/package.json", (*_FRONTEND_TOOLING, DEPENDENCY_AUDIT, OPENAPI), "frontend dependencies"),
    _rule("frontend/package-lock.json", (*_FRONTEND_TOOLING, DEPENDENCY_AUDIT, OPENAPI), "frontend dependency lock"),
    _rule("frontend/**", _FRONTEND_TOOLING, "frontend tooling, assets and configuration"),
    # The companion module has its own toolchain; its installer is tested by the backend suite.
    _rule("modules/**/package.json", (DEPENDENCY_AUDIT, BACKEND), "companion module dependencies"),
    _rule("modules/**/package-lock.json", (DEPENDENCY_AUDIT, BACKEND), "companion module dependency lock"),
    _rule("modules/**/*.md", NONE, "companion module documentation"),
    _rule("modules/**", (BACKEND,), "companion module, read by tests/test_todo_chat_installer.py"),
    # Root npm project: only builds Tailwind CSS for the server-rendered pages, which no CI job does.
    _rule("package.json", (DEPENDENCY_AUDIT,), "root npm dependencies"),
    _rule("package-lock.json", (DEPENDENCY_AUDIT,), "root npm dependency lock"),
    _rule("tailwind.config.js", NONE, "Tailwind build for server-rendered pages, not built in CI"),
    # Documentation.
    _rule("docs/**", (DOCS,), "documentation site"),
    _rule("TECH_SPEC.md", (DOCS,), "root technical spec, part of the docs site"),
    # Metadata and tooling no CI job reads.
    _rule(".github/workflows/**", (BACKEND,), "workflow policy, checked by tests/test_workflows.py"),
    _rule(".github/**", NONE, "GitHub metadata"),
    _rule(".claude/**", NONE, "Claude agent definitions"),
    _rule(".codex/**", NONE, "Codex agent definitions"),
    _rule(".agents/**", NONE, "agent skills"),
    _rule(".gemini/**", NONE, "Gemini skills"),
    _rule("ios/**", NONE, "iOS client, not built in CI"),
    _rule(".jscpd*.json", NONE, "weekly duplication report"),
    _rule("prek.toml", NONE, "local pre-commit hooks"),
    _rule("release-please-config.json", NONE, "release automation"),
    _rule(".release-please-manifest.json", NONE, "release automation"),
    _rule("*.md", NONE, "repository Markdown"),
    _rule("LICENSE", NONE, "license text"),
)

_COMPILED: dict[str, re.Pattern[str]] = {}


def _compile(pattern: str) -> re.Pattern[str]:
    """Translate a glob into a regex: ``**/`` and ``**`` span directories, ``*`` does not."""
    if pattern not in _COMPILED:
        parts: list[str] = []
        index = 0
        while index < len(pattern):
            if pattern.startswith("**/", index):
                parts.append("(?:.*/)?")
                index += 3
            elif pattern.startswith("**", index):
                parts.append(".*")
                index += 2
            elif pattern[index] == "*":
                parts.append("[^/]*")
                index += 1
            else:
                parts.append(re.escape(pattern[index]))
                index += 1
        _COMPILED[pattern] = re.compile("".join(parts))
    return _COMPILED[pattern]


def rule_for(path: str) -> Rule | None:
    """Return the first rule matching ``path``, or ``None`` when no rule does."""
    return next((rule for rule in RULES if rule.matches(path)), None)


def jobs_for(path: str) -> frozenset[str]:
    """Return the jobs ``path`` selects; an unmapped path selects everything."""
    rule = rule_for(path)
    return FULL if rule is None else rule.jobs


#: ``pyproject.toml`` tables that only some jobs read; any other changed table selects every job.
PYPROJECT_TABLES: dict[str, frozenset[str]] = {
    "project.version": frozenset({BACKEND}),  # uv lock --check; releases bump only this
    "tool.janus.semgrep": frozenset({SEMGREP, BACKEND}),
    "tool.janus.dependency_audit": frozenset({DEPENDENCY_AUDIT, BACKEND}),
    "tool.janus.coverage": frozenset({BACKEND}),
    "tool.coverage": frozenset({BACKEND}),
    "tool.ruff": frozenset({BACKEND}),
    "tool.mypy": frozenset({BACKEND}),
    "tool.importlinter": frozenset({BACKEND}),
    "tool.vulture": frozenset({BACKEND}),
    "tool.deptry": frozenset({BACKEND}),
    "tool.pytest": frozenset({BACKEND, MIGRATIONS}),
    # Wheel contents; the backend job builds and smoke-tests the wheel.
    "tool.hatch": frozenset({BACKEND}),
}
#: The project's own package in ``uv.lock``, whose version follows ``pyproject.toml``.
PROJECT_NAME = "janus"
#: Paths :func:`refine_toml` can narrow.
REFINABLE = ("pyproject.toml", "uv.lock")


def _changed_keys(base: dict, head: dict, prefix: str = "") -> set[str]:
    """Dotted keys whose value differs, descending into ``project``, ``tool`` and ``tool.janus``."""
    changed: set[str] = set()
    for key in set(base) | set(head):
        name = f"{prefix}{key}"
        if base.get(key) == head.get(key):
            continue
        if (
            name in {"project", "tool", "tool.janus"}
            and isinstance(base.get(key), dict)
            and isinstance(head.get(key), dict)
        ):
            changed |= _changed_keys(base[key], head[key], f"{name}.")
        else:
            changed.add(name)
    return changed


def _without_project_version(lock: dict) -> dict:
    packages = [
        {k: v for k, v in package.items() if k != "version"} if package.get("name") == PROJECT_NAME else package
        for package in lock.get("package", [])
    ]
    return {**lock, "package": packages}


def refine_toml(path: str, base_text: str | None, head_text: str | None) -> frozenset[str] | None:
    """Narrow the jobs a ``pyproject.toml`` or ``uv.lock`` change selects, or ``None`` to keep the rule.

    ``None`` (keep the full selection) when either side is missing or unparsable, or when a
    changed table is not in :data:`PYPROJECT_TABLES`.
    """
    if path not in REFINABLE or base_text is None or head_text is None:
        return None
    try:
        base, head = tomllib.loads(base_text), tomllib.loads(head_text)
    except tomllib.TOMLDecodeError:
        return None
    if path == "uv.lock":
        if base == head:
            return NONE
        return frozenset({BACKEND}) if _without_project_version(base) == _without_project_version(head) else None
    jobs: set[str] = set()
    for key in _changed_keys(base, head):
        if key not in PYPROJECT_TABLES:
            return None
        jobs |= PYPROJECT_TABLES[key]
    return frozenset(jobs)


@dataclass(frozen=True)
class Selection:
    """The selected jobs and, per job, the paths that selected it."""

    jobs: frozenset[str]
    reasons: dict[str, list[str]]
    unmapped: list[str]
    #: Jobs the paths selected that a draft pull request defers until it is ready for review.
    deferred: frozenset[str] = frozenset()
    draft: bool = False


def select(paths: Iterable[str], refined: dict[str, frozenset[str]] | None = None) -> Selection:
    """Union the jobs every path selects; ``refined`` overrides the rule for some paths."""
    jobs: set[str] = set()
    reasons: dict[str, list[str]] = {}
    unmapped: list[str] = []
    refined = refined or {}
    for path in sorted(set(paths)):
        rule = rule_for(path)
        if rule is None and path not in refined:
            unmapped.append(path)
        selected = refined.get(path, FULL if rule is None else rule.jobs)
        jobs |= selected
        for job in selected:
            reasons.setdefault(job, []).append(path)
    return Selection(frozenset(jobs), reasons, unmapped)


def full_selection(reason: str) -> Selection:
    """Select every CI job (not the security scans), e.g. for the nightly run on ``main``."""
    return Selection(frozenset(CI_JOBS), {job: [reason] for job in CI_JOBS}, [])


def for_draft(selection: Selection) -> Selection:
    """Defer the :data:`DRAFT_SKIPPED` jobs of a draft pull request's selection."""
    deferred = selection.jobs & frozenset(DRAFT_SKIPPED)
    return Selection(selection.jobs - deferred, selection.reasons, selection.unmapped, deferred, draft=True)


class DetectionError(RuntimeError):
    """The changed files could not be listed completely; the caller must fail."""


def _request(url: str, token: str, accept: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"Accept": accept, "Authorization": f"Bearer {token}", "X-GitHub-Api-Version": "2022-11-28"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 (fixed https host)
            return response.read()
    except (urllib.error.URLError, TimeoutError) as exc:
        raise DetectionError(f"GET {url} failed: {exc}") from exc


def _get_json(url: str, token: str) -> object:
    try:
        return json.loads(_request(url, token, "application/vnd.github+json"))
    except json.JSONDecodeError as exc:
        raise DetectionError(f"GET {url} returned invalid JSON: {exc}") from exc


def _get_text(url: str, token: str) -> str | None:
    """A file's raw text, or ``None`` when it cannot be read (absent at that commit, say)."""
    try:
        return _request(url, token, "application/vnd.github.raw+json").decode("utf-8")
    except (DetectionError, UnicodeDecodeError):
        return None


def paths_from_files(files: Iterable[dict]) -> list[str]:
    """Return every path a files listing touches, the old name of a rename included."""
    paths: list[str] = []
    for item in files:
        paths.append(item["filename"])
        previous = item.get("previous_filename")
        if previous:
            paths.append(previous)
    return paths


def fetch_refinements(
    repo: str, pull: dict, paths: Iterable[str], token: str, get_json=_get_json, get_text=_get_text
) -> dict[str, frozenset[str]]:
    """Narrowed job sets for the refinable files a PR changes, compared at the merge base and head.

    Any lookup that fails leaves the file out, so its rule (every job) applies.
    """
    wanted = [path for path in REFINABLE if path in set(paths)]
    if not wanted:
        return {}
    try:
        head = pull["head"]["sha"]
        compare = get_json(f"{GITHUB_API}/repos/{repo}/compare/{pull['base']['sha']}...{head}", token)
        merge_base = compare["merge_base_commit"]["sha"]
    except (DetectionError, KeyError, TypeError):
        return {}
    refined = {}
    for path in wanted:
        quoted = urllib.parse.quote(path)
        jobs = refine_toml(
            path,
            get_text(f"{GITHUB_API}/repos/{repo}/contents/{quoted}?ref={merge_base}", token),
            get_text(f"{GITHUB_API}/repos/{repo}/contents/{quoted}?ref={head}", token),
        )
        if jobs is not None:
            refined[path] = jobs
    return refined


def fetch_pr(repo: str, pr_number: int, token: str, get_json=_get_json) -> tuple[dict, list[str]]:
    """The pull request and every path it changes, failing on an incomplete listing."""
    pull = get_json(f"{GITHUB_API}/repos/{repo}/pulls/{pr_number}", token)
    if not isinstance(pull, dict) or not isinstance(pull.get("changed_files"), int):
        raise DetectionError("the pull request did not report changed_files")
    expected = pull["changed_files"]
    if expected >= GITHUB_FILES_LIMIT:
        raise DetectionError(f"{expected} changed files reach the API limit of {GITHUB_FILES_LIMIT}")
    files: list[dict] = []
    page = 1
    while True:
        batch = get_json(f"{GITHUB_API}/repos/{repo}/pulls/{pr_number}/files?per_page={PER_PAGE}&page={page}", token)
        if not isinstance(batch, list):
            raise DetectionError(f"page {page} of the changed files is not a list")
        files.extend(batch)
        if len(batch) < PER_PAGE:
            break
        page += 1
    if len(files) != expected:
        raise DetectionError(f"listed {len(files)} changed files, the pull request reports {expected}")
    return pull, paths_from_files(files)


def output_lines(selection: Selection) -> list[str]:
    """``name=true|false`` lines for ``$GITHUB_OUTPUT``, one per job, then :data:`FULL_OUTPUT`."""
    lines = [f"{job}={'true' if job in selection.jobs else 'false'}" for job in ALL_JOBS]
    return lines + [f"{FULL_OUTPUT}={'false' if selection.draft else 'true'}"]


def summary_markdown(selection: Selection, changed: int | None) -> str:
    """A job-summary table: each job, whether it runs, and an example path that selected it."""
    lines = ["## CI job selection", ""]
    if changed is not None:
        lines += [f"{changed} changed paths (renames counted under both names).", ""]
    lines += ["| Job | Runs | Selected by |", "| --- | --- | --- |"]
    for job in ALL_JOBS:
        paths = selection.reasons.get(job, [])
        shown = ", ".join(f"`{path}`" for path in paths[:3]) + (f" and {len(paths) - 3} more" if len(paths) > 3 else "")
        if job in selection.jobs:
            runs = "yes"
        elif job in selection.deferred:
            runs = "skipped, draft (runs when ready for review)"
        else:
            runs = "skipped, not affected"
        lines.append(f"| {job} | {runs} | {shown} |")
    if selection.draft:
        lines += ["", "Draft pull request: the test suites run once it is marked ready for review."]
    if selection.unmapped:
        lines += ["", "Unmapped paths select every job: " + ", ".join(f"`{p}`" for p in selection.unmapped)]
    return "\n".join(lines) + "\n"


def _append(path: str | None, text: str) -> None:
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(text)


def _select_command(args: argparse.Namespace) -> int:
    if args.event != "pull_request":
        selection = full_selection(f"{args.event} event")
        changed = None
    else:
        token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
        if not args.pr or not args.repo or not token:
            print("::error::job selection needs --pr, --repo and GH_TOKEN on pull requests", file=sys.stderr)
            return 1
        try:
            pull, paths = fetch_pr(args.repo, args.pr, token)
        except DetectionError as exc:
            print(f"::error::job selection failed, so CI fails closed: {exc}", file=sys.stderr)
            return 1
        selection = select(paths, fetch_refinements(args.repo, pull, paths, token))
        if args.draft == "true":
            selection = for_draft(selection)
        changed = len(paths)
    _append(args.github_output, "\n".join(output_lines(selection)) + "\n")
    summary = summary_markdown(selection, changed)
    _append(args.summary, summary)
    print(summary)
    return 0


def _classify_command(args: argparse.Namespace) -> int:
    for path in args.paths:
        rule = rule_for(path)
        jobs = ", ".join(sorted(jobs_for(path))) or "(no suite)"
        print(f"{path}: {jobs}  [{rule.reason if rule else 'unmapped, selects everything'}]")
    print("selected:", ", ".join(sorted(select(args.paths).jobs)) or "(no suite)")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    commands = parser.add_subparsers(dest="command", required=True)
    select_parser = commands.add_parser("select", help="select the jobs for a workflow event")
    select_parser.add_argument("--event", required=True)
    select_parser.add_argument("--pr", type=int)
    select_parser.add_argument("--repo")
    select_parser.add_argument("--github-output")
    select_parser.add_argument("--summary")
    select_parser.add_argument("--draft", choices=("true", "false"), default="false")
    classify_parser = commands.add_parser("classify", help="print the jobs some paths select")
    classify_parser.add_argument("paths", nargs="+")
    args = parser.parse_args(argv)
    return _select_command(args) if args.command == "select" else _classify_command(args)


if __name__ == "__main__":
    sys.exit(main())
