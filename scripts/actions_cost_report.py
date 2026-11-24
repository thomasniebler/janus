"""GitHub Actions cost and timing report (JAN-308).

Read-only: it only lists workflow runs, their jobs (every attempt), and, for pull request runs,
the files the PR changed at that update. Nothing it prints is an invoice. Its minute figures are:

* **execution**: a job's ``completed_at - started_at``, summed over jobs (aggregate);
* **rounded estimate**: each job rounded up to a whole minute, the way GitHub bills hosted
  runners, then summed. It is an estimate, not the billed amount; the billing export is the
  only source for that;
* **wall time**: first job start to last job end of one run attempt, which is shorter than the
  aggregate whenever jobs overlap;
* **queue/wait**: ``started_at - created_at`` per job, which includes environment waits.

Skipped jobs and jobs that never got a runner are excluded and counted. Re-run attempts,
cancelled runs and failed runs are reported separately from complete successful runs.

Commands::

    collect   --repo owner/name --since 2026-09-20 [--until DATE] [--workflow ci.yml ...] \
              [--changed-files] --out snapshot.json
    report    snapshot.json [--workload workload.json] [--baseline report.json] \
              [--json report.json] [--markdown report.md]
    workload  snapshot.json --count 20 --out workload.json

``collect`` needs a token with ``actions: read`` (and ``contents: read`` for ``--changed-files``)
in ``GH_TOKEN`` or ``GITHUB_TOKEN``. ``workload`` freezes the latest pull request updates with
their changed-path categories (``scripts/ci_paths.py``); ``report --workload`` prices that frozen
workload with the measured per-job medians, under the old policy (every job on every update) and
the path-selected policy. Report schema: ``docs/docs/tech-spec/architecture-overview.md#ci-cost-report``.
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import math
import os
import re
import statistics
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path


def _load_ci_paths():
    """Load the sibling ``ci_paths.py`` (scripts/ is not a package) for the job selection rules."""
    spec = importlib.util.spec_from_file_location("ci_paths", Path(__file__).resolve().with_name("ci_paths.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(spec.name, module)
    spec.loader.exec_module(module)
    return sys.modules[spec.name]


ci_paths = _load_ci_paths()

GITHUB_API = "https://api.github.com"
PER_PAGE = 100
#: Filtered run listings stop at 1000 results; a longer period must be split.
RUN_LISTING_LIMIT = 1000
#: The compare endpoint lists at most 300 files.
COMPARE_FILES_LIMIT = 300
SCHEMA_VERSION = 1
#: Jobs whose slowest steps the report lists.
TOP_STEP_JOBS = ("backend-quality", "e2e-tests")
TOP_STEP_COUNT = 8

#: CI job name -> the selection key (``scripts/ci_paths.py``) that decides whether it runs.
#: Jobs not listed here (``pr-title``, ``changes``) run on every pull request update.
JOB_SELECTION_KEYS: dict[str, str] = {
    "backend-quality": ci_paths.BACKEND,
    "migration-tests": ci_paths.MIGRATIONS,
    "frontend-quality": ci_paths.FRONTEND,
    "e2e-tests": ci_paths.E2E,
    "docs-build": ci_paths.DOCS,
    "openapi-check": ci_paths.OPENAPI,
    "semgrep": ci_paths.SEMGREP,
    "dependency-audit": ci_paths.DEPENDENCY_AUDIT,
}
#: Jobs every pull request update ran before path selection (ci.yml plus the two security
#: workflows); the baseline policy prices each update with all of them.
BASELINE_PR_JOBS = ("pr-title", *JOB_SELECTION_KEYS)

GetJson = Callable[[str], object]
GetText = Callable[[str], "str | None"]


# --------------------------------------------------------------------------- collection


def _github_getters(token: str) -> tuple[GetJson, GetText]:
    def fetch(url: str, accept: str) -> bytes:
        request = urllib.request.Request(
            url,
            headers={"Accept": accept, "Authorization": f"Bearer {token}", "X-GitHub-Api-Version": "2022-11-28"},
        )
        with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310 (fixed https host)
            return response.read()

    def get_json(url: str) -> object:
        return json.loads(fetch(url, "application/vnd.github+json"))

    def get_text(url: str) -> str | None:
        try:
            return fetch(url, "application/vnd.github.raw+json").decode("utf-8")
        except (urllib.error.URLError, UnicodeDecodeError):
            return None

    return get_json, get_text


def paginate(get_json: GetJson, url: str, key: str | None) -> list[dict]:
    """Read every page of a listing; ``key`` names the list inside an object response."""
    items: list[dict] = []
    page = 1
    separator = "&" if "?" in url else "?"
    while True:
        payload = get_json(f"{url}{separator}per_page={PER_PAGE}&page={page}")
        batch = payload[key] if key else payload
        if not isinstance(batch, list):
            raise RuntimeError(f"{url} page {page}: expected a list")
        items.extend(batch)
        if len(batch) < PER_PAGE:
            return items
        page += 1


def _slim_job(job: dict) -> dict:
    """Keep only timing fields; no logs, no step output."""
    return {
        "id": job["id"],
        "name": job["name"],
        "run_attempt": job.get("run_attempt", 1),
        "status": job.get("status"),
        "conclusion": job.get("conclusion"),
        "created_at": job.get("created_at"),
        "started_at": job.get("started_at"),
        "completed_at": job.get("completed_at"),
        "labels": job.get("labels") or [],
        "runner_group": job.get("runner_group_name"),
        "runner_assigned": bool(job.get("runner_name")),
        "steps": [
            {
                "name": step.get("name"),
                "conclusion": step.get("conclusion"),
                "started_at": step.get("started_at"),
                "completed_at": step.get("completed_at"),
            }
            for step in job.get("steps") or []
        ],
    }


def _changed_paths(get_json: GetJson, repo: str, base: str, head_sha: str, get_text: GetText | None = None) -> dict:
    """The paths a PR changed at ``head_sha`` (merge-base diff against ``base``).

    With ``get_text``, ``pyproject.toml``/``uv.lock`` changes are also narrowed the way the CI
    ``changes`` job narrows them (``ci_paths.refine_toml``), into ``refined``.
    """
    try:
        payload = get_json(f"{GITHUB_API}/repos/{repo}/compare/{urllib.parse.quote(base)}...{head_sha}")
    except urllib.error.HTTPError as exc:  # e.g. a deleted fork commit
        return {"paths": None, "error": f"compare failed: HTTP {exc.code}"}
    files = payload.get("files") or []
    if len(files) >= COMPARE_FILES_LIMIT:
        return {"paths": None, "error": f"{len(files)} files reach the compare limit"}
    paths = sorted(set(ci_paths.paths_from_files(files)))
    refined: dict[str, list[str]] = {}
    merge_base = (payload.get("merge_base_commit") or {}).get("sha")
    if get_text is not None and merge_base:
        for path in ci_paths.REFINABLE:
            if path in paths:
                url = f"{GITHUB_API}/repos/{repo}/contents/{path}?ref="
                jobs = ci_paths.refine_toml(path, get_text(url + merge_base), get_text(url + head_sha))
                if jobs is not None:
                    refined[path] = sorted(jobs)
    return {"paths": paths, "error": None, **({"refined": refined} if refined else {})}


def collect(
    get_json: GetJson,
    repo: str,
    since: str,
    until: str | None = None,
    workflows: Sequence[str] = (),
    changed_files: bool = False,
    base: str = "main",
    get_text: GetText | None = None,
) -> dict:
    """List completed runs in the period with every job attempt; return a snapshot."""
    created = f"{since}..{until}" if until else f">={since}"
    query = urllib.parse.urlencode({"status": "completed", "created": created})
    sources = (
        [f"{GITHUB_API}/repos/{repo}/actions/workflows/{name}/runs?{query}" for name in workflows]
        if workflows
        else [f"{GITHUB_API}/repos/{repo}/actions/runs?{query}"]
    )
    runs: list[dict] = []
    for url in sources:
        listing = paginate(get_json, url, "workflow_runs")
        if len(listing) >= RUN_LISTING_LIMIT:
            raise RuntimeError(f"{url} reached the {RUN_LISTING_LIMIT}-run listing limit; use a shorter period")
        runs.extend(listing)
    snapshot_runs = []
    for index, run in enumerate(sorted(runs, key=lambda item: item["created_at"]), start=1):
        if index % 25 == 0:
            print(f"collect: {index}/{len(runs)} runs", file=sys.stderr, flush=True)
        jobs = paginate(get_json, f"{GITHUB_API}/repos/{repo}/actions/runs/{run['id']}/jobs?filter=all", "jobs")
        record = {
            "id": run["id"],
            "workflow": run.get("name"),
            "path": run.get("path"),
            "event": run.get("event"),
            "conclusion": run.get("conclusion"),
            "run_attempt": run.get("run_attempt", 1),
            "created_at": run.get("created_at"),
            "head_sha": run.get("head_sha"),
            "head_branch": run.get("head_branch"),
            "url": run.get("html_url"),
            "jobs": [_slim_job(job) for job in jobs],
        }
        if changed_files and record["event"] == "pull_request":
            record["changed"] = _changed_paths(get_json, repo, base, record["head_sha"], get_text)
        snapshot_runs.append(record)
    return {
        "schema": SCHEMA_VERSION,
        "repository": repo,
        "period": {"since": since, "until": until},
        "collected_at": dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "runs": snapshot_runs,
    }


# --------------------------------------------------------------------------- metrics


def _ts(value: str | None) -> dt.datetime | None:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def job_timing(job: dict) -> dict | None:
    """Execution, rounded estimate and wait for one job, or ``None`` when it never ran."""
    started, completed = _ts(job.get("started_at")), _ts(job.get("completed_at"))
    if job.get("conclusion") == "skipped" or started is None or completed is None:
        return None
    if not job.get("runner_assigned", True) and not job.get("steps"):
        return None  # cancelled while queued: no runner, nothing billed
    seconds = max(0.0, (completed - started).total_seconds())
    created = _ts(job.get("created_at"))
    return {
        "seconds": seconds,
        "rounded_minutes": max(1, math.ceil(seconds / 60)),
        "wait_seconds": max(0.0, (started - created).total_seconds()) if created else None,
        "started": started,
        "completed": completed,
    }


def step_seconds(step: dict) -> float | None:
    """Duration of one step, or ``None`` when it did not run."""
    started, completed = _ts(step.get("started_at")), _ts(step.get("completed_at"))
    if started is None or completed is None or step.get("conclusion") == "skipped":
        return None
    return max(0.0, (completed - started).total_seconds())


#: The ``(…)`` GitHub appends to a matrix leg's job name, e.g. ``e2e-tests (2/3)`` (JAN-345).
_MATRIX_SUFFIX = re.compile(r" \([^()]*\)$")


def base_job_name(name: str) -> str:
    """``semgrep / semgrep`` (a reusable workflow call) -> ``semgrep``; ``e2e-tests (2/3)`` -> ``e2e-tests``."""
    return _MATRIX_SUFFIX.sub("", name.rsplit(" / ", 1)[-1])


def attempt_records(run: dict) -> list[dict]:
    """One record per run attempt: its jobs' timings, aggregate, rounded total and wall time."""
    by_attempt: dict[int, list[dict]] = defaultdict(list)
    for job in run["jobs"]:
        by_attempt[job.get("run_attempt", 1)].append(job)
    records = []
    for attempt, jobs in sorted(by_attempt.items()):
        timed, excluded = [], 0
        for job in jobs:
            timing = job_timing(job)
            if timing is None:
                excluded += 1
                continue
            timed.append((job, timing))
        conclusions = {job.get("conclusion") for job in jobs}
        if attempt != max(by_attempt):
            outcome = "superseded_attempt"
        elif run.get("conclusion") == "success" and conclusions <= {"success", "skipped"}:
            outcome = "success"
        elif run.get("conclusion") == "success":
            outcome = "success_with_failed_jobs"
        else:
            outcome = run.get("conclusion") or "unknown"
        wall = (
            (max(t["completed"] for _, t in timed) - min(t["started"] for _, t in timed)).total_seconds()
            if timed
            else 0.0
        )
        records.append(
            {
                "run_id": run["id"],
                "attempt": attempt,
                "workflow": run.get("workflow"),
                "event": run.get("event"),
                "outcome": outcome,
                "created_at": run.get("created_at"),
                "url": run.get("url"),
                "head_sha": run.get("head_sha"),
                "runner_classes": sorted({",".join(job.get("labels") or []) or "unknown" for job, _ in timed}),
                "jobs": {
                    job["name"]: {
                        "seconds": t["seconds"],
                        "rounded_minutes": t["rounded_minutes"],
                        "wait_seconds": t["wait_seconds"],
                        "conclusion": job.get("conclusion"),
                    }
                    for job, t in timed
                },
                "excluded_jobs": excluded,
                "execution_minutes": round(sum(t["seconds"] for _, t in timed) / 60, 2),
                "rounded_minutes": sum(t["rounded_minutes"] for _, t in timed),
                "wall_minutes": round(wall / 60, 2),
                "max_wait_seconds": max((t["wait_seconds"] or 0.0 for _, t in timed), default=0.0),
            }
        )
    return records


def _median(values: Iterable[float]) -> float | None:
    data = list(values)
    return round(statistics.median(data), 2) if data else None


def _workflow_summary(records: list[dict]) -> dict:
    complete = [r for r in records if r["outcome"] == "success"]
    jobs: dict[str, list[dict]] = defaultdict(list)
    for record in complete:
        for name, job in record["jobs"].items():
            jobs[name].append(job)
    return {
        "attempts": len(records),
        "complete_success": len(complete),
        "failed": sum(r["outcome"] == "failure" for r in records),
        "cancelled": sum(r["outcome"] == "cancelled" for r in records),
        "rerun_attempts": sum(r["attempt"] > 1 for r in records),
        "superseded_attempts": sum(r["outcome"] == "superseded_attempt" for r in records),
        "rounded_minutes_all_attempts": sum(r["rounded_minutes"] for r in records),
        "rounded_minutes_not_complete": sum(r["rounded_minutes"] for r in records if r["outcome"] != "success"),
        "median_execution_minutes": _median(r["execution_minutes"] for r in complete),
        "median_rounded_minutes": _median(r["rounded_minutes"] for r in complete),
        "median_wall_minutes": _median(r["wall_minutes"] for r in complete),
        "jobs": {
            name: {
                "samples": len(values),
                "median_minutes": _median(v["seconds"] / 60 for v in values),
                "median_rounded_minutes": _median(v["rounded_minutes"] for v in values),
                "median_wait_seconds": _median(v["wait_seconds"] for v in values if v["wait_seconds"] is not None),
            }
            for name, values in sorted(jobs.items())
        },
    }


def top_steps(runs: list[dict], job_names: Sequence[str] = TOP_STEP_JOBS, count: int = TOP_STEP_COUNT) -> dict:
    """Median duration of the slowest steps of each named job, over successful jobs."""
    durations: dict[str, dict[str, list[float]]] = {name: defaultdict(list) for name in job_names}
    for run in runs:
        for job in run["jobs"]:
            name = base_job_name(job["name"])
            if name not in durations or job.get("conclusion") != "success":
                continue
            for step in job.get("steps") or []:
                seconds = step_seconds(step)
                if seconds is not None:
                    durations[name][step["name"]].append(seconds)
    result = {}
    for name, steps in durations.items():
        ranked = sorted(((statistics.median(v), step, len(v)) for step, v in steps.items()), reverse=True)
        result[name] = [{"step": step, "median_seconds": round(m, 1), "samples": n} for m, step, n in ranked[:count]]
    return result


# --------------------------------------------------------------------------- workload


def selection_category(jobs: Iterable[str]) -> str:
    """A stable label for a set of selected jobs, e.g. ``backend+e2e+semgrep`` or ``no-suite``."""
    ordered = [job for job in ci_paths.ALL_JOBS if job in set(jobs)]
    return "+".join(ordered) if ordered else "no-suite"


def freeze_workload(snapshot: dict, count: int = 20, workflow: str = "CI") -> dict:
    """The latest ``count`` completed PR updates (first attempts) with their selected jobs."""
    updates = []
    for run in sorted(snapshot["runs"], key=lambda item: item["created_at"], reverse=True):
        if run.get("workflow") != workflow or run.get("event") != "pull_request":
            continue
        if run.get("conclusion") not in {"success", "failure"}:
            continue  # a cancelled update says nothing about what a full update costs
        changed = run.get("changed") or {}
        if changed.get("paths") is None:
            selected, category, note = set(ci_paths.ALL_JOBS), "full (unknown paths)", changed.get("error")
        else:
            refined = {path: frozenset(jobs) for path, jobs in (changed.get("refined") or {}).items()}
            selection = ci_paths.select(changed["paths"], refined)
            selected, category, note = set(selection.jobs), selection_category(selection.jobs), None
        updates.append(
            {
                "run_id": run["id"],
                "url": run.get("url"),
                "created_at": run.get("created_at"),
                "head_branch": run.get("head_branch"),
                "head_sha": run.get("head_sha"),
                "changed_paths": None if changed.get("paths") is None else len(changed["paths"]),
                "selected_jobs": sorted(selected),
                "category": category,
                **({"note": note} if note else {}),
            }
        )
        if len(updates) == count:
            break
    weights: dict[str, int] = defaultdict(int)
    for update in updates:
        weights[update["category"]] += 1
    return {
        "schema": SCHEMA_VERSION,
        "frozen_at": snapshot.get("collected_at"),
        "repository": snapshot.get("repository"),
        "updates": updates,
        "categories": {
            category: {"updates": n, "weight": round(n / len(updates), 3)} for category, n in sorted(weights.items())
        },
    }


def price_workload(workload: dict, job_costs: dict[str, float], extra_jobs: Sequence[str] = ()) -> dict:
    """Estimated rounded minutes for the frozen workload, before and after path selection.

    ``job_costs`` maps a job name to its median rounded minutes. The baseline policy runs every
    job of :data:`BASELINE_PR_JOBS` on every update; the selected policy runs ``pr-title``, the
    ``extra_jobs`` (e.g. the ``changes`` detector), and the jobs the update's paths select.
    """
    missing = sorted(name for name in BASELINE_PR_JOBS if name not in job_costs)
    baseline = selected = 0.0
    for update in workload["updates"]:
        chosen = set(update["selected_jobs"])
        for name in BASELINE_PR_JOBS:
            cost = job_costs.get(name, 0.0)
            baseline += cost
            key = JOB_SELECTION_KEYS.get(name)
            if key is None or key in chosen:
                selected += cost
        selected += sum(job_costs.get(name, 1.0) for name in extra_jobs)
    updates = len(workload["updates"]) or 1
    return {
        "updates": len(workload["updates"]),
        "baseline_policy_rounded_minutes": round(baseline, 2),
        "selected_policy_rounded_minutes": round(selected, 2),
        "per_update_baseline": round(baseline / updates, 2),
        "per_update_selected": round(selected / updates, 2),
        "saving_percent": round(100 * (1 - selected / baseline), 1) if baseline else None,
        "jobs_without_cost": missing,
    }


# --------------------------------------------------------------------------- report


def build_report(snapshot: dict, workload: dict | None = None, baseline: dict | None = None) -> dict:
    """Aggregate a snapshot into the compact JSON report."""
    records = [record for run in snapshot["runs"] for record in attempt_records(run)]
    groups: dict[str, list[dict]] = defaultdict(list)
    for record in records:
        groups[f"{record['workflow']} ({record['event']})"].append(record)
    report: dict = {
        "schema": SCHEMA_VERSION,
        "repository": snapshot.get("repository"),
        "period": snapshot.get("period"),
        "collected_at": snapshot.get("collected_at"),
        "note": "Rounded minutes are per-job estimates (each job rounded up to a whole minute), not invoice usage.",
        "runner_classes": sorted({c for r in records for c in r["runner_classes"]}),
        "totals": {
            "attempts": len(records),
            "execution_minutes": round(sum(r["execution_minutes"] for r in records), 1),
            "rounded_minutes": sum(r["rounded_minutes"] for r in records),
        },
        "workflows": {name: _workflow_summary(group) for name, group in sorted(groups.items())},
        "complete_runs": [
            {
                k: r[k]
                for k in (
                    "url",
                    "created_at",
                    "workflow",
                    "event",
                    "execution_minutes",
                    "rounded_minutes",
                    "wall_minutes",
                )
            }
            for r in records
            if r["outcome"] == "success" and r["workflow"] == "CI"
        ][-20:],
        "top_steps": top_steps(snapshot["runs"]),
    }
    if workload is not None:
        costs = job_costs_from_report(report)
        report["workload"] = {
            "frozen_at": workload.get("frozen_at"),
            "categories": workload.get("categories"),
            # Before the `changes` job existed it has no measured cost; it is priced at one minute.
            "estimate": price_workload(workload, costs, extra_jobs=("changes",)),
        }
    if baseline is not None:
        report["comparison"] = compare(baseline, report)
    return report


def job_costs_from_report(report: dict) -> dict[str, float]:
    """Median rounded minutes per job name across the report's pull request workflows.

    The legs of a matrix job (the e2e shards, JAN-345) add up to one cost under the base name.
    """
    costs: dict[str, float] = {}
    for name, summary in report["workflows"].items():
        if not name.endswith("(pull_request)"):
            continue
        workflow_costs: dict[str, float] = defaultdict(float)
        for job, values in summary["jobs"].items():
            if values["median_rounded_minutes"] is not None:
                workflow_costs[base_job_name(job)] += values["median_rounded_minutes"]
        costs.update(workflow_costs)
    return costs


def compare(before: dict, after: dict) -> dict:
    """Per workflow: median rounded/execution/wall minutes before and after."""
    result = {}
    for name in sorted(set(before["workflows"]) | set(after["workflows"])):
        old, new = before["workflows"].get(name, {}), after["workflows"].get(name, {})
        result[name] = {
            metric: {"before": old.get(metric), "after": new.get(metric)}
            for metric in (
                "median_rounded_minutes",
                "median_execution_minutes",
                "median_wall_minutes",
                "complete_success",
            )
        }
    old_estimate = (before.get("workload") or {}).get("estimate")
    new_estimate = (after.get("workload") or {}).get("estimate")
    if old_estimate and new_estimate:
        result["frozen workload"] = {
            "rounded_minutes": {
                "before": old_estimate["baseline_policy_rounded_minutes"],
                "after": new_estimate["selected_policy_rounded_minutes"],
            }
        }
    return result


def render_markdown(report: dict) -> str:
    """A human-readable version of :func:`build_report`'s output."""
    period = report.get("period") or {}
    lines = [
        "## GitHub Actions cost and timing",
        "",
        f"Repository `{report.get('repository')}`, runs created {period.get('since')} "
        f"to {period.get('until') or 'now'}, "
        f"runner classes: {', '.join(report['runner_classes']) or 'none'}.",
        "",
        f"_{report['note']}_",
        "",
        "| Workflow (event) | Attempts | Complete | Failed | Cancelled | Re-runs | Median exec min | "
        "Median rounded min | Median wall min | Rounded min, all attempts |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for name, s in report["workflows"].items():
        lines.append(
            f"| {name} | {s['attempts']} | {s['complete_success']} | {s['failed']} | {s['cancelled']} | "
            f"{s['rerun_attempts']} | {s['median_execution_minutes']} | {s['median_rounded_minutes']} | "
            f"{s['median_wall_minutes']} | {s['rounded_minutes_all_attempts']} |"
        )
    for name, s in report["workflows"].items():
        if not s["jobs"]:
            continue
        lines += [
            "",
            f"### Jobs: {name}",
            "",
            "| Job | Samples | Median min | Median rounded min | Median wait s |",
            "| --- | --- | --- | --- | --- |",
        ]
        for job, j in s["jobs"].items():
            lines.append(
                f"| {job} | {j['samples']} | {j['median_minutes']} | "
                f"{j['median_rounded_minutes']} | {j['median_wait_seconds']} |"
            )
    for job, steps in report["top_steps"].items():
        if steps:
            lines += ["", f"### Slowest steps: {job}", "", "| Step | Median s | Samples |", "| --- | --- | --- |"]
            lines += [f"| {s['step']} | {s['median_seconds']} | {s['samples']} |" for s in steps]
    workload = report.get("workload")
    if workload:
        estimate = workload["estimate"]
        lines += ["", "### Frozen workload", "", "| Category | Updates | Weight |", "| --- | --- | --- |"]
        lines += [f"| {c} | {v['updates']} | {v['weight']} |" for c, v in (workload["categories"] or {}).items()]
        lines += [
            "",
            f"{estimate['updates']} updates: {estimate['baseline_policy_rounded_minutes']} rounded min with every job, "
            f"{estimate['selected_policy_rounded_minutes']} with path selection "
            f"({estimate['saving_percent']}% less).",
        ]
    comparison = report.get("comparison")
    if comparison:
        lines += ["", "### Before and after", "", "| Workflow | Metric | Before | After |", "| --- | --- | --- | --- |"]
        for name, metrics in comparison.items():
            for metric, values in metrics.items():
                lines.append(f"| {name} | {metric} | {values['before']} | {values['after']} |")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- CLI


def _load(path: str | None) -> dict | None:
    return json.loads(Path(path).read_text(encoding="utf-8")) if path else None


def _write(path: str | None, text: str) -> None:
    if path:
        Path(path).write_text(text, encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    commands = parser.add_subparsers(dest="command", required=True)
    c = commands.add_parser("collect", help="read run and job history into a snapshot")
    c.add_argument("--repo", required=True)
    c.add_argument("--since", required=True, help="first creation date, YYYY-MM-DD")
    c.add_argument("--until", help="last creation date, YYYY-MM-DD")
    c.add_argument("--workflow", action="append", default=[], help="workflow file name; repeatable")
    c.add_argument("--changed-files", action="store_true", help="also list each PR update's changed paths")
    c.add_argument("--base", default="main")
    c.add_argument("--out", required=True)
    r = commands.add_parser("report", help="summarize a snapshot")
    r.add_argument("snapshot")
    r.add_argument("--workload")
    r.add_argument("--baseline", help="an earlier report to compare against")
    r.add_argument("--json")
    r.add_argument("--markdown")
    w = commands.add_parser("workload", help="freeze the latest pull request updates")
    w.add_argument("snapshot")
    w.add_argument("--count", type=int, default=20)
    w.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    if args.command == "collect":
        token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
        if not token:
            print("collect needs GH_TOKEN or GITHUB_TOKEN with actions: read", file=sys.stderr)
            return 1
        get_json, get_text = _github_getters(token)
        snapshot = collect(
            get_json, args.repo, args.since, args.until, args.workflow, args.changed_files, args.base, get_text
        )
        _write(args.out, json.dumps(snapshot, indent=1) + "\n")
        print(f"{len(snapshot['runs'])} runs written to {args.out}")
    elif args.command == "workload":
        workload = freeze_workload(_load(args.snapshot), args.count)
        _write(args.out, json.dumps(workload, indent=1) + "\n")
        print(json.dumps(workload["categories"], indent=1))
    else:
        report = build_report(_load(args.snapshot), _load(args.workload), _load(args.baseline))
        _write(args.json, json.dumps(report, indent=1) + "\n")
        markdown = render_markdown(report)
        _write(args.markdown, markdown)
        print(markdown)
    return 0


if __name__ == "__main__":
    sys.exit(main())
