"""Monthly agent-assisted structural review (JAN-271), fed by the weekly quality report (JAN-270).

The review itself is a read-only run of a coding agent (for example Claude Code or Codex). This
script prepares its input and checks its output; it never calls an LLM, Linear, or GitHub's API,
and it never changes code. ``.github/workflows/structural-review.yml`` runs ``bundle`` monthly;
``task quality:review-*`` runs the same locally. Commands::

    bundle --out-dir DIR [--base SHA] [--head REV] [--days 31] [--reports 4]
           [--history FILE] [--ledger FILE] [--repo . --remote origin --branch quality-history]
        Write the review input: ``bundle.json`` (window and automatic metrics), the newest main
        quality reports, ``diff.patch`` (main since the last recorded review), ``prompt.md`` and
        ``summary.md``.
    check  review.json --bundle DIR/bundle.json [--markdown review.md] [--repo .]
        Validate the agent's findings (``janus.structural-review/1``) against the bundle and the
        repository at the window's head commit, and render them next to the automatic metrics.
    record review.json --bundle DIR/bundle.json [--repo . --remote origin --branch quality-history]
        Append a checked review of a ``main`` window to ``reports/structural-review.jsonl`` on the
        ``quality-history`` branch, so the next window starts at its head commit.

Window: from the head commit of the newest recorded review (``ledger``) to ``--head``; without a
recorded review, from the last first-parent commit at least ``--days`` before the head (``days``),
or the root commit (``root``); ``--base`` overrides both (``explicit``). Automatic metrics stay in
the bundle; the review holds only qualitative findings and refers to the bundle by its digest.
Policy: docs/docs/tech-spec/architecture-overview.md#monthly-structural-review.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import json
import re
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_sibling(name: str):
    """Load a sibling script (scripts/ is not a package)."""
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().with_name(f"{name}.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


history = _load_sibling("history_branch")
quality = _load_sibling("quality_report")
ops = _load_sibling("ops_metrics")

BUNDLE_SCHEMA = "janus.structural-review-bundle/1"
REVIEW_SCHEMA = "janus.structural-review/1"
LEDGER_PATH = "reports/structural-review.jsonl"
MAIN_BRANCH = "main"
DEFAULT_DAYS = 31
DEFAULT_REPORTS = 4
#: The patch handed to the reviewer is cut at this size; the bundle says when it was.
PATCH_LIMIT_BYTES = 1_500_000
#: Only these paths enter the patch; the numstat covers every changed file.
PATCH_PATHS = ("src", "frontend/src", "scripts", "alembic", "Taskfile.yml", "pyproject.toml")
API_DIR = "src/janus/api"
ROUTE_METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "websocket", "api_route"}
#: Database calls inside a route handler: the automatic proxy for business logic in a router.
DATA_ACCESS_ATTRS = {"query", "execute", "scalars", "scalar", "add", "add_all", "delete", "commit", "flush", "rollback"}
DATA_ACCESS_NAMES = {"db", "session"}

CATEGORIES = ("responsibilities", "router-logic", "api-contract", "domain-coupling")
PRIORITIES = ("urgent", "high", "medium", "low")
ACTIONS = ("cleanup", "structural-check", "follow-up")
RECONCILIATION = ("existing", "proposed", "unchecked")
REVIEWERS = ("claude-code", "codex", "human")
_SHA = re.compile(r"^[0-9a-f]{40}$")
_ISSUE = re.compile(r"^[A-Z][A-Z0-9]*-\d+$")
_FINDING_ID = re.compile(r"^SR-\d+$")
WINDOW_KEYS = ("base", "head", "base_time", "head_time")


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    """``git -C repo args`` with a fixed argument list (no shell)."""
    return history.run_git(repo, *args, check=check)


def iso(value: str | datetime) -> str:
    """UTC ISO 8601 with a ``Z`` suffix, second precision."""
    moment = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    return moment.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def digest(bundle: dict) -> str:
    """Stable SHA-256 of a bundle (sorted keys, compact), which a review refers to."""
    return hashlib.sha256(json.dumps(bundle, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


# --- window ------------------------------------------------------------------------------------


def _commit(repo: Path, rev: str) -> tuple[str, str]:
    shown = git(repo, "show", "-s", "--format=%H%x09%cI", f"{rev}^{{commit}}").stdout.strip()
    sha, when = shown.split("\t")
    return sha, iso(when)


def _is_ancestor(repo: Path, older: str, newer: str) -> bool:
    return git(repo, "merge-base", "--is-ancestor", older, newer, check=False).returncode == 0


def furthest_descendant(repo: Path, shas: list[str]) -> str:
    """The commit among ``shas`` that descends from the others (ledger order does not matter)."""
    best = shas[0]
    for sha in shas[1:]:
        if _is_ancestor(repo, best, sha):
            best = sha
    return best


def recorded_heads(ledger: list[dict]) -> list[str]:
    """Window heads of the recorded reviews."""
    return [r["window"]["head"] for r in ledger if r.get("schema") == REVIEW_SCHEMA and r.get("window")]


def review_window(repo: Path, head: str, ledger: list[dict], base: str | None = None, days: int = DEFAULT_DAYS) -> dict:
    """The commit range to review: explicit base, else the last recorded review, else ``days``."""
    head_sha, head_time = _commit(repo, head)
    source = "explicit"
    if base is None:
        recorded = recorded_heads(ledger)
        recorded = [sha for sha in recorded if sha != head_sha and _is_ancestor(repo, sha, head_sha)]
        if recorded:
            base, source = furthest_descendant(repo, recorded), "ledger"
        else:
            cutoff = iso(datetime.fromisoformat(head_time.replace("Z", "+00:00")) - timedelta(days=days))
            older = git(repo, "rev-list", "--first-parent", "-1", f"--before={cutoff}", head_sha).stdout.strip()
            if older:
                base, source = older, "days"
            else:
                roots = git(repo, "rev-list", "--max-parents=0", head_sha).stdout.split()
                base, source = roots[-1], "root"
    elif not re.fullmatch(r"[0-9a-f]{7,40}", base):
        raise ValueError(f"--base must be a commit sha, got {base!r}")
    base_sha, base_time = _commit(repo, base)
    if not _is_ancestor(repo, base_sha, head_sha):
        raise ValueError(f"base {base_sha[:12]} is not an ancestor of head {head_sha[:12]}")
    return {
        "base": base_sha,
        "head": head_sha,
        "base_time": base_time,
        "head_time": head_time,
        "base_source": source,
    }


# --- automatic metrics -------------------------------------------------------------------------


def codemaps(repo: Path, window: dict) -> list[dict]:
    """Every tracked CODEMAP.md at the head commit, and whether the window changed it."""
    listed = git(repo, "ls-tree", "-r", window["head"]).stdout.splitlines()
    changed = set(
        git(repo, "diff", "--name-only", f"{window['base']}..{window['head']}", "--", "*CODEMAP.md").stdout.split()
    )
    maps = []
    for line in listed:
        meta, path = line.split("\t", 1)
        if path == "CODEMAP.md" or path.endswith("/CODEMAP.md"):
            maps.append({"path": path, "blob": meta.split()[2], "changed_in_window": path in changed})
    return sorted(maps, key=lambda item: item["path"])


def nearest_codemap(path: str, maps: list[str]) -> str:
    """The CODEMAP.md of the deepest directory that contains ``path`` (the root map otherwise)."""
    best = "CODEMAP.md"
    for candidate in maps:
        folder = candidate[: -len("CODEMAP.md")]
        if folder and path.startswith(folder) and len(candidate) > len(best):
            best = candidate
    return best


def parse_numstat(text: str, maps: list[str]) -> list[dict]:
    """``git diff --numstat`` lines as file records; binary files count as 0/0 and are flagged."""
    files = []
    for line in text.splitlines():
        if not line.strip():
            continue
        added, deleted, path = line.split("\t", 2)
        if " => " in path:  # a rename: keep the new path
            path = re.sub(r"\{[^{}]* => ([^{}]*)\}", r"\1", path).replace("//", "/")
            path = path.split(" => ")[-1]
        binary = added == "-"
        files.append(
            {
                "path": path,
                "insertions": 0 if binary else int(added),
                "deletions": 0 if binary else int(deleted),
                "binary": binary,
                "codemap": nearest_codemap(path, maps),
            }
        )
    return files


def diff_summary(repo: Path, window: dict, maps: list[str]) -> dict:
    """Commits and changed files of the window, grouped by their nearest CODEMAP."""
    span = f"{window['base']}..{window['head']}"
    log = git(repo, "log", "--first-parent", "--format=%H%x09%cI%x09%s", span).stdout.splitlines()
    commits = [dict(zip(("sha", "time", "subject"), line.split("\t", 2), strict=True)) for line in log if line]
    for commit in commits:
        commit["time"] = iso(commit["time"])
    files = parse_numstat(git(repo, "diff", "--numstat", "-M", span).stdout, maps)
    areas: dict[str, dict] = {}
    for item in files:
        area = areas.setdefault(item["codemap"], {"files": 0, "insertions": 0, "deletions": 0})
        area["files"] += 1
        area["insertions"] += item["insertions"]
        area["deletions"] += item["deletions"]
    return {
        "commits": list(reversed(commits)),
        "files_changed": len(files),
        "insertions": sum(item["insertions"] for item in files),
        "deletions": sum(item["deletions"] for item in files),
        "by_area": dict(sorted(areas.items())),
        "files": files,
    }


def write_patch(repo: Path, window: dict, out: Path, limit: int = PATCH_LIMIT_BYTES) -> dict:
    """Write the window's patch for the review paths, cut at ``limit`` bytes."""
    patch = git(repo, "diff", "-M", f"{window['base']}..{window['head']}", "--", *PATCH_PATHS).stdout.encode()
    truncated = len(patch) > limit
    # Cut at a line end, so the reviewer never sees half a line (or half a UTF-8 character).
    out.write_bytes(patch[: patch.rfind(b"\n", 0, limit) + 1] if truncated else patch)
    return {"path": out.name, "paths": list(PATCH_PATHS), "bytes": len(patch), "truncated": truncated}


def _is_route(node: ast.AST) -> bool:
    for decorator in getattr(node, "decorator_list", []):
        call = decorator.func if isinstance(decorator, ast.Call) else decorator
        if isinstance(call, ast.Attribute) and call.attr in ROUTE_METHODS:
            return True
    return False


def _data_access_calls(node: ast.AST) -> int:
    return sum(
        1
        for call in ast.walk(node)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr in DATA_ACCESS_ATTRS
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id in DATA_ACCESS_NAMES
    )


def router_metrics(source: str) -> dict:
    """Routes, top-level helpers, and database calls inside route handlers of one router module."""
    tree = ast.parse(source)
    functions = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    routes = [n for n in functions if _is_route(n)]
    return {
        "lines": sum(1 for line in source.splitlines() if line.strip()),
        "routes": len(routes),
        "helpers": len(functions) - len(routes),
        "data_access_calls": sum(_data_access_calls(n) for n in routes),
    }


def routers(repo: Path, window: dict, changed: set[str]) -> dict:
    """Router metrics for every ``src/janus/api/*.py`` at the head commit."""
    listed = git(repo, "ls-tree", "--name-only", f"{window['head']}:{API_DIR}", check=False)
    names = listed.stdout.split() if listed.returncode == 0 else []
    modules = []
    for name in sorted(names):
        if not name.endswith(".py") or name == "__init__.py":
            continue
        path = f"{API_DIR}/{name}"
        source = git(repo, "show", f"{window['head']}:{path}").stdout
        modules.append({"path": path, "changed_in_window": path in changed, **router_metrics(source)})
    keys = ("lines", "routes", "helpers", "data_access_calls")
    totals = {key: sum(module[key] for module in modules) for key in keys}
    totals["modules"] = len(modules)
    totals["modules_with_data_access"] = sum(1 for module in modules if module["data_access_calls"])
    return {"totals": totals, "modules": modules}


def quality_input(records: list[dict] | None, error: str | None, count: int) -> tuple[dict, list[dict]]:
    """Summary of the newest main quality reports; ``unavailable``/``error`` instead of zeros."""
    if error is not None:
        return {"status": "error", "detail": error, "reports": []}, []
    reports = quality.latest_main_reports(records or [], count)
    if not reports:
        detail = "no main report on the quality-history branch yet"
        return {"status": "unavailable", "detail": detail, "reports": []}, []
    summaries = [
        {
            "generated_at": r.get("generated_at"),
            "commit": r.get("commit"),
            "checks": {name: check.get("status") for name, check in (r.get("checks") or {}).items()},
            "regressions": r.get("regressions") or [],
            "hotspots": r.get("hotspots") or [],
        }
        for r in reports
    ]
    return {"status": "available", "detail": f"{len(reports)} main report(s)", "reports": summaries}, reports


def build_bundle(
    repo: Path,
    window: dict,
    quality_records: list[dict] | None,
    quality_error: str | None,
    out_dir: Path,
    count: int = DEFAULT_REPORTS,
    now: datetime | None = None,
) -> dict:
    """Write every review input to ``out_dir`` and return the bundle."""
    out_dir.mkdir(parents=True, exist_ok=True)
    maps = codemaps(repo, window)
    diff = diff_summary(repo, window, [m["path"] for m in maps])
    changed = {item["path"] for item in diff["files"]}
    summary, reports = quality_input(quality_records, quality_error, count)
    (out_dir / "quality-reports.jsonl").write_text(history.dump_jsonl(reports), encoding="utf-8")
    bundle = {
        "schema": BUNDLE_SCHEMA,
        "generated_at": iso(now or datetime.now(UTC)),
        "window": window,
        "quality": {**summary, "path": "quality-reports.jsonl"},
        "diff": {**diff, "patch": write_patch(repo, window, out_dir / "diff.patch")},
        "codemaps": maps,
        "routers": routers(repo, window, changed),
    }
    (out_dir / "bundle.json").write_text(json.dumps(bundle, indent=2) + "\n", encoding="utf-8")
    (out_dir / "prompt.md").write_text(render_prompt(bundle), encoding="utf-8")
    (out_dir / "summary.md").write_text(render_metrics(bundle) + render_agent_status(), encoding="utf-8")
    return bundle


# --- review ------------------------------------------------------------------------------------


def _lines_at(repo: Path, commit: str, path: str) -> int | None:
    """Line count of the file ``path`` at ``commit``; ``None`` when it is missing or not a file."""
    kind = git(repo, "cat-file", "-t", f"{commit}:{path}", check=False)
    if kind.returncode != 0 or kind.stdout.strip() != "blob":
        return None
    return len(git(repo, "show", f"{commit}:{path}").stdout.splitlines())


def _check_finding(index: int, finding, repo: Path, head: str, problems: list[str]) -> None:
    where = f"findings[{index}]"
    keys = {"id", "title", "category", "priority", "path", "line", "evidence", "action", "reconciliation"}
    if not isinstance(finding, dict) or set(finding) != keys:
        problems.append(f"{where}: keys must be exactly {sorted(keys)}")
        return
    if not isinstance(finding["id"], str) or not _FINDING_ID.match(finding["id"]):
        problems.append(f"{where}.id: expected SR-<n>")
    for key in ("title", "evidence"):
        if not isinstance(finding[key], str) or not finding[key].strip():
            problems.append(f"{where}.{key}: must be a non-empty string")
    for key, allowed in (("category", CATEGORIES), ("priority", PRIORITIES), ("action", ACTIONS)):
        if finding[key] not in allowed:
            problems.append(f"{where}.{key}: must be one of {', '.join(allowed)}")
    line = finding["line"]
    if not isinstance(line, int) or isinstance(line, bool) or line < 1:
        problems.append(f"{where}.line: must be a positive integer")
    path = finding["path"]
    if (
        not isinstance(path, str)
        or not path.strip()
        or path.startswith("/")
        or ".." in Path(path).parts
        or Path(path).parts in ((), (".",))
    ):
        problems.append(f"{where}.path: must be a repository-relative file path")
    else:
        length = _lines_at(repo, head, path)
        if length is None:
            problems.append(f"{where}.path: {path} is not a file at {head[:12]}")
        elif isinstance(line, int) and line > length:
            problems.append(f"{where}.line: {path} has {length} lines at {head[:12]}")
    rec = finding["reconciliation"]
    if not isinstance(rec, dict) or set(rec) != {"status", "issue"} or rec["status"] not in RECONCILIATION:
        problems.append(f"{where}.reconciliation: expected status ({', '.join(RECONCILIATION)}) and issue")
    elif rec["status"] == "existing" and not (isinstance(rec["issue"], str) and _ISSUE.match(rec["issue"])):
        problems.append(f"{where}.reconciliation.issue: an existing issue needs its identifier (JAN-123)")
    elif rec["status"] != "existing" and rec["issue"] is not None:
        problems.append(f"{where}.reconciliation.issue: only an existing issue carries an identifier")


def _unchecked(finding) -> bool:
    rec = finding.get("reconciliation") if isinstance(finding, dict) else None
    return isinstance(rec, dict) and rec.get("status") == "unchecked"


def check_review(review: dict, bundle: dict, repo: Path) -> list[str]:
    """Every problem with ``review``; empty when it is a valid review of ``bundle``."""
    problems: list[str] = []
    keys = {"schema", "reviewed_at", "reviewer", "bundle_sha256", "window", "findings", "blockers", "notes"}
    if not isinstance(review, dict) or set(review) != keys:
        return [f"top level: keys must be exactly {sorted(keys)}"]
    if review["schema"] != REVIEW_SCHEMA:
        problems.append(f"schema: expected {REVIEW_SCHEMA}")
    if bundle.get("schema") != BUNDLE_SCHEMA:
        problems.append(f"bundle: expected {BUNDLE_SCHEMA}")
    try:
        iso(review["reviewed_at"])
    except (AttributeError, TypeError, ValueError):
        problems.append("reviewed_at: expected an ISO 8601 time")
    if review["reviewer"] not in REVIEWERS:
        problems.append(f"reviewer: must be one of {', '.join(REVIEWERS)}")
    if review["bundle_sha256"] != digest(bundle):
        problems.append("bundle_sha256: does not match the bundle (reviewed a different input)")
    expected = {key: bundle.get("window", {}).get(key) for key in WINDOW_KEYS}
    if review["window"] != expected:
        problems.append(f"window: must state the bundle's commit range and time range exactly: {expected}")
    if not all(isinstance(expected[key], str) and _SHA.match(expected[key]) for key in ("base", "head")):
        problems.append("window: base and head must be full commit shas")
        return problems
    findings = review["findings"] if isinstance(review["findings"], list) else []
    if not isinstance(review["findings"], list):
        problems.append("findings: must be a list")
    else:
        for index, finding in enumerate(review["findings"]):
            _check_finding(index, finding, repo, expected["head"], problems)
        ids = [f.get("id") for f in review["findings"] if isinstance(f, dict)]
        if len(ids) != len(set(ids)):
            problems.append("findings: ids must be unique")
    blockers = review["blockers"]
    if not isinstance(blockers, list) or not all(isinstance(b, str) and b.strip() for b in blockers):
        problems.append("blockers: must be a list of non-empty strings")
    elif not blockers and any(_unchecked(f) for f in findings):
        problems.append("blockers: an unchecked reconciliation must be reported as a blocker (no issue tracker access)")
    if not isinstance(review["notes"], str):
        problems.append("notes: must be a string")
    problems += [f"{hit}: looks like a secret or an e-mail address" for hit in ops.find_secrets(review)]
    return problems


# --- rendering ---------------------------------------------------------------------------------


def _short(sha: str | None) -> str:
    return (sha or "?")[:12]


def render_metrics(bundle: dict) -> str:
    """The automatic part: window, quality report, diff and router metrics. No judgments."""
    w, q, d, r = bundle["window"], bundle["quality"], bundle["diff"], bundle["routers"]
    lines = [
        "## Monthly structural review: automatic metrics",
        "",
        f"- **Window:** `{_short(w['base'])}..{_short(w['head'])}`, {w['base_time']} to {w['head_time']}"
        f" (base from `{w['base_source']}`)",
        f"- **Main diff:** {len(d['commits'])} first-parent commits, {d['files_changed']} files,"
        f" +{d['insertions']}/-{d['deletions']}; patch {d['patch']['bytes']} bytes"
        + (" (truncated)" if d["patch"]["truncated"] else ""),
        f"- **Quality reports:** {q['status']} ({q['detail']})",
        f"- **CODEMAPs:** {len(bundle['codemaps'])}, changed in the window:"
        f" {sum(1 for m in bundle['codemaps'] if m['changed_in_window'])}",
        f"- **Routers:** {r['totals']['modules']} modules, {r['totals']['routes']} routes,"
        f" {r['totals']['helpers']} top-level helpers, {r['totals']['data_access_calls']} database calls in route"
        f" handlers across {r['totals']['modules_with_data_access']} modules",
        "",
    ]
    if q["reports"]:
        newest = q["reports"][-1]
        not_measured = [f"{n} ({s})" for n, s in newest["checks"].items() if s in ("error", "unavailable")]
        lines += [
            f"Newest quality report: `{_short(newest['commit'])}` at {newest['generated_at']}; regressions:"
            f" {', '.join(newest['regressions']) or 'none'}; no data: {', '.join(not_measured) or 'none'}.",
            "",
            "| Hotspot | Score | Worst function |",
            "| --- | --- | --- |",
        ]
        lines += [f"| `{h['path']}` | {h['score']} | `{h['worst_function']}` |" for h in newest["hotspots"]]
        lines.append("")
    lines += ["| Area (CODEMAP) | Files | + | - |", "| --- | --- | --- | --- |"]
    lines += [f"| `{a}` | {v['files']} | {v['insertions']} | {v['deletions']} |" for a, v in d["by_area"].items()]
    top = sorted(r["modules"], key=lambda m: (-m["data_access_calls"], -m["lines"]))[:10]
    lines += [
        "",
        "| Router | Lines | Routes | Helpers | DB calls in handlers | Changed |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    lines += [
        f"| `{m['path']}` | {m['lines']} | {m['routes']} | {m['helpers']} | {m['data_access_calls']} |"
        f" {'yes' if m['changed_in_window'] else ''} |"
        for m in top
    ]
    return "\n".join(lines) + "\n"


def render_agent_status() -> str:
    """The CI run's honest statement about the qualitative part (missing capability = blocker)."""
    return (
        "\n## Agent review: blocked in CI\n\n"
        "GitHub Actions has no agent integration for this repository (no LLM credentials, no issue"
        " tracker access), so this run only prepares the input. Run the monthly structural review"
        " on this bundle in a coding-agent session (for example Claude Code or Codex), then"
        " `task quality:review-check` and `task quality:review-record`. See"
        " docs/docs/tech-spec/architecture-overview.md#monthly-structural-review.\n"
    )


def render_prompt(bundle: dict) -> str:
    """The instruction handed to the reviewer, with the exact window and input files."""
    w = bundle["window"]
    return (
        "# Monthly structural review\n\n"
        "Review the code structure for this window, read-only.\n\n"
        f"- Window: `{w['base']}..{w['head']}` ({w['base_time']} to {w['head_time']}).\n"
        f"- Bundle digest (`bundle_sha256`): `{digest(bundle)}`.\n"
        "- Inputs next to this file: `bundle.json` (automatic metrics), `quality-reports.jsonl`"
        " (newest main quality reports), `diff.patch` (main diff of the window), and the CODEMAPs"
        " listed in `bundle.json` at the head commit.\n"
        "- Focus: responsibilities, business logic in routers, API contracts, domain coupling.\n"
        f"- Quality reports: {bundle['quality']['status']} ({bundle['quality']['detail']}); treat a missing"
        " report or an `error`/`unavailable` check as no data, never as zero.\n"
        + (
            f"- `diff.patch` is truncated; read the rest with `git diff -M {w['base']}..{w['head']}` in a clone.\n"
            if bundle["diff"]["patch"]["truncated"]
            else ""
        )
        + f"- Write `review.json` with schema `{REVIEW_SCHEMA}`, then run"
        " `task quality:review-check FILE=review.json BUNDLE=<this directory>/bundle.json`.\n\n"
        "```json\n"
        + json.dumps(
            {
                "schema": REVIEW_SCHEMA,
                "reviewed_at": "<ISO 8601 UTC>",
                "reviewer": "|".join(REVIEWERS),
                "bundle_sha256": digest(bundle),
                "window": {key: w[key] for key in WINDOW_KEYS},
                "findings": [
                    {
                        "id": "SR-1",
                        "title": "<one line>",
                        "category": "|".join(CATEGORIES),
                        "priority": "|".join(PRIORITIES),
                        "path": "<repository-relative path at the head commit>",
                        "line": 1,
                        "evidence": "<what the code shows, with the file and line>",
                        "action": "|".join(ACTIONS),
                        "reconciliation": {"status": "|".join(RECONCILIATION), "issue": "JAN-123 or null"},
                    }
                ],
                "blockers": ["<a capability the review lacked, e.g. no issue tracker access>"],
                "notes": "",
            },
            indent=2,
        )
        + "\n```\n"
    )


def render_review(review: dict, bundle: dict) -> str:
    """Automatic metrics first, then the qualitative findings, then blockers."""
    order = {name: index for index, name in enumerate(PRIORITIES)}
    findings = sorted(review["findings"], key=lambda f: (order[f["priority"]], f["id"]))
    lines = [
        render_metrics(bundle),
        f"## Findings (qualitative, {review['reviewer']}, {review['reviewed_at']})",
        "",
        "| ID | Priority | Category | Location | Finding | Action | Issue |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for f in findings:
        rec = f["reconciliation"]
        issue = rec["issue"] if rec["status"] == "existing" else rec["status"]
        title = f["title"].replace("|", "\\|")
        lines.append(
            f"| {f['id']} | {f['priority']} | {f['category']} | `{f['path']}:{f['line']}` | {title} |"
            f" {f['action']} | {issue} |"
        )
    if not findings:
        lines.append("| - | - | - | - | no findings | - | - |")
    lines += ["", "### Evidence", ""]
    lines += [f"- **{f['id']}:** {f['evidence']}" for f in findings]
    lines += ["", "### Blockers", ""]
    lines += [f"- {b}" for b in review["blockers"]] or ["- none"]
    if review["notes"].strip():
        lines += ["", "### Notes", "", review["notes"].strip()]
    return "\n".join(lines) + "\n"


# --- CLI ---------------------------------------------------------------------------------------


def _ledger(args) -> list[dict]:
    if args.ledger:
        path = Path(args.ledger)
        return history.read_jsonl(path.read_text(encoding="utf-8")) if path.is_file() else []
    return history.read_history(Path(args.repo), args.remote, args.branch, LEDGER_PATH)


def _quality_records(args) -> tuple[list[dict] | None, str | None]:
    """The quality history, or an error text: a missing report never stops the bundle."""
    try:
        if args.history:
            path = Path(args.history)
            return (history.read_jsonl(path.read_text(encoding="utf-8")) if path.is_file() else []), None
        return history.read_history(Path(args.repo), args.remote, args.branch, quality.HISTORY_PATH), None
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        return None, f"could not read the quality history: {type(error).__name__}"


def _cmd_bundle(args) -> int:
    repo = Path(args.repo)
    # The ledger decides the window; a read failure aborts unless the base is given explicitly,
    # so a transient error never silently widens or narrows the reviewed range.
    ledger = [] if args.base else _ledger(args)
    window = review_window(repo, args.head, ledger, args.base, args.days)
    records, error = _quality_records(args)
    bundle = build_bundle(repo, window, records, error, Path(args.out_dir), args.reports)
    print(render_metrics(bundle))
    print(f"bundle written to {args.out_dir}/bundle.json (sha256 {digest(bundle)})")
    return 0


def _load_pair(args) -> tuple[dict, dict]:
    review = json.loads(Path(args.review).read_text(encoding="utf-8"))
    bundle = json.loads(Path(args.bundle).read_text(encoding="utf-8"))
    return review, bundle


def _cmd_check(args) -> int:
    review, bundle = _load_pair(args)
    problems = check_review(review, bundle, Path(args.repo))
    for problem in problems:
        print(f"INVALID {problem}", file=sys.stderr)
    if problems:
        return 1
    markdown = render_review(review, bundle)
    if args.markdown:
        Path(args.markdown).write_text(markdown, encoding="utf-8")
    print(markdown)
    return 0


def _cmd_record(args) -> int:
    review, bundle = _load_pair(args)
    repo = Path(args.repo)
    problems = check_review(review, bundle, repo)
    if problems:
        for problem in problems:
            print(f"INVALID {problem}", file=sys.stderr)
        return 1
    main_ref = f"refs/remotes/{args.remote}/{MAIN_BRANCH}"
    git(repo, "fetch", "--quiet", "--no-tags", args.remote, f"+refs/heads/{MAIN_BRANCH}:{main_ref}")
    if not _is_ancestor(repo, review["window"]["head"], main_ref):
        print(f"not recording: {_short(review['window']['head'])} is not on {MAIN_BRANCH}", file=sys.stderr)
        return 1

    head = review["window"]["head"]

    def once(records: list[dict]) -> list[dict]:
        # Monotonic: every recorded head must be a strict ancestor of the new one, so a review
        # completed out of order can never move the next window's start backwards.
        for recorded in recorded_heads(records):
            if recorded == head:
                raise FileExistsError(f"a review ending at {_short(head)} is already recorded")
            if not _is_ancestor(repo, recorded, head):
                raise FileExistsError(
                    f"out of order: {_short(head)} does not descend from the recorded review head {_short(recorded)}"
                )
        return [review]

    try:
        commit = history.append_and_push(
            repo,
            args.remote,
            args.branch,
            LEDGER_PATH,
            once,
            f"structural review: {_short(review['window']['base'])}..{_short(review['window']['head'])}",
        )
    except FileExistsError as error:
        print(f"not recording: {error}", file=sys.stderr)
        return 1
    print(f"appended to {args.branch}:{LEDGER_PATH} at {commit}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("bundle", "check", "record"):
        cmd = commands.add_parser(name)
        cmd.add_argument("--repo", default=".")
        if name == "bundle":
            cmd.add_argument("--out-dir", required=True)
            cmd.add_argument("--base", default=None, help="review from this commit instead of the last review")
            cmd.add_argument("--head", default="HEAD")
            cmd.add_argument("--days", type=int, default=DEFAULT_DAYS)
            cmd.add_argument("--reports", type=int, default=DEFAULT_REPORTS, help="newest main quality reports")
            cmd.add_argument("--history", help="local quality-report JSON Lines instead of the history branch")
            cmd.add_argument("--ledger", help="local review JSON Lines instead of the history branch")
        else:
            cmd.add_argument("review")
            cmd.add_argument("--bundle", required=True)
        if name == "check":
            cmd.add_argument("--markdown")
        if name != "check":
            cmd.add_argument("--remote", default="origin")
            cmd.add_argument("--branch", default=quality.HISTORY_BRANCH)
    return parser


_COMMANDS = {"bundle": _cmd_bundle, "check": _cmd_check, "record": _cmd_record}


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and run one command."""
    args = _build_parser().parse_args(argv)
    if args.command == "bundle" and (args.days < 1 or args.reports < 1):
        raise SystemExit("--days and --reports must be at least 1")
    try:
        return _COMMANDS[args.command](args)
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    except subprocess.CalledProcessError as error:
        # Only the git subcommand, never its output: a remote URL could carry credentials.
        parts = [str(part) for part in error.cmd]
        command = parts[3] if len(parts) > 3 and parts[1] == "-C" else parts[1]
        print(f"error: git {command} failed (exit {error.returncode})", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
