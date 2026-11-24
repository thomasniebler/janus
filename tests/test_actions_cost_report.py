"""GitHub Actions cost and timing report (scripts/actions_cost_report.py, JAN-308)."""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("actions_cost_report", ROOT / "scripts" / "actions_cost_report.py")
report = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = report
_spec.loader.exec_module(report)


def _job(name, start, end, *, attempt=1, conclusion="success", created=None, steps=None, runner=True):
    """A slim job whose times are seconds after 12:00:00."""

    def at(seconds):
        return None if seconds is None else f"2026-10-01T12:{seconds // 60:02d}:{seconds % 60:02d}Z"

    return {
        "id": hash((name, attempt)),
        "name": name,
        "run_attempt": attempt,
        "conclusion": conclusion,
        "created_at": at(start if created is None else created),
        "started_at": at(start),
        "completed_at": at(end),
        "labels": ["ubuntu-latest"],
        "runner_assigned": runner,
        "steps": steps
        if steps is not None
        else [{"name": "work", "conclusion": conclusion, "started_at": at(start), "completed_at": at(end)}],
    }


def _run(
    run_id,
    jobs,
    *,
    conclusion="success",
    event="pull_request",
    workflow="CI",
    created="2026-10-01T12:00:00Z",
    changed=None,
):
    run = {
        "id": run_id,
        "workflow": workflow,
        "event": event,
        "conclusion": conclusion,
        "created_at": created,
        "url": f"https://github.com/o/r/actions/runs/{run_id}",
        "head_sha": f"sha{run_id}",
        "head_branch": f"b{run_id}",
        "jobs": jobs,
    }
    if changed is not None:
        run["changed"] = {"paths": changed, "error": None}
    return run


def test_overlapping_jobs_wall_time_differs_from_execution_and_rounding():
    """Three parallel jobs: wall time is the longest job, execution the sum, rounding per job."""
    run = _run(1, [_job("a", 0, 70), _job("b", 0, 10), _job("c", 5, 125, created=0)])

    (record,) = report.attempt_records(run)

    assert record["wall_minutes"] == round(125 / 60, 2)
    assert record["execution_minutes"] == round((70 + 10 + 120) / 60, 2)
    assert record["rounded_minutes"] == 2 + 1 + 2  # 70 s -> 2, 10 s -> 1, 120 s -> 2
    assert record["jobs"]["c"]["wait_seconds"] == 5
    assert record["max_wait_seconds"] == 5
    assert record["outcome"] == "success"


def test_skipped_and_unstarted_jobs_are_excluded():
    """A skipped job and one cancelled before a runner picked it up cost nothing."""
    run = _run(
        2,
        [
            _job("ran", 0, 30),
            _job("skipped", 0, 0, conclusion="skipped"),
            _job("never-started", None, None, conclusion="cancelled"),
            _job("queued-cancel", 10, 10, conclusion="cancelled", runner=False, steps=[]),
        ],
        conclusion="cancelled",
    )

    (record,) = report.attempt_records(run)

    assert list(record["jobs"]) == ["ran"]
    assert record["excluded_jobs"] == 3
    assert record["rounded_minutes"] == 1
    assert record["outcome"] == "cancelled"


def test_attempts_are_separate_records_and_reruns_are_counted():
    """A re-run's first attempt is superseded; both attempts' minutes count in the totals."""
    run = _run(3, [_job("a", 0, 100, attempt=1, conclusion="failure"), _job("a", 200, 260, attempt=2)])

    records = report.attempt_records(run)
    summary = report._workflow_summary(records)

    assert [(r["attempt"], r["outcome"]) for r in records] == [(1, "superseded_attempt"), (2, "success")]
    assert summary["rerun_attempts"] == 1
    assert summary["superseded_attempts"] == 1
    assert summary["complete_success"] == 1
    assert summary["rounded_minutes_all_attempts"] == 2 + 1
    assert summary["rounded_minutes_not_complete"] == 2


def test_success_run_with_a_failed_job_is_not_complete():
    """A successful run with a failed (continue-on-error) job is not a complete run sample."""
    run = _run(4, [_job("a", 0, 10), _job("b", 0, 10, conclusion="failure")])
    (record,) = report.attempt_records(run)
    assert record["outcome"] == "success_with_failed_jobs"
    assert report._workflow_summary([record])["complete_success"] == 0


def test_rounding_boundaries():
    """Exactly 60 s is one minute, 61 s two, and a 0 s job that ran still bills one."""
    for seconds, minutes in [(0, 1), (1, 1), (60, 1), (61, 2), (600, 10)]:
        timing = report.job_timing(_job("x", 0, seconds))
        assert timing["rounded_minutes"] == minutes


def test_pagination_reads_until_a_short_page():
    """Every page is read; the last, shorter page ends the listing."""
    pages = {1: list(range(100)), 2: list(range(100)), 3: list(range(7))}
    seen = []

    def get_json(url):
        seen.append(url)
        page = int(re.search(r"[?&]page=(\d+)", url).group(1))
        return {"jobs": [{"n": n} for n in pages[page]]}

    items = report.paginate(get_json, "https://api/x?filter=all", "jobs")

    assert len(items) == 207
    assert len(seen) == 3
    assert seen[0] == "https://api/x?filter=all&per_page=100&page=1"


def test_collect_snapshot_keeps_timing_only(monkeypatch):
    """The snapshot keeps names and timestamps, never logs or extra job fields."""
    job = _job("a", 0, 10)
    job.update({"runner_name": "GitHub Actions 1", "html_url": "x", "check_run_url": "y"})
    job["steps"][0]["number"] = 1

    def get_json(url):
        if "/runs?" in url:
            return {
                "workflow_runs": [
                    {
                        "id": 9,
                        "name": "CI",
                        "event": "pull_request",
                        "conclusion": "success",
                        "created_at": "2026-10-01T12:00:00Z",
                        "head_sha": "abc",
                        "html_url": "u",
                    }
                ]
            }
        if "/jobs" in url:
            return {"jobs": [job]}
        if "/compare/" in url:
            return {
                "files": [
                    {"filename": "docs/docs/a.md"},
                    {"filename": "src/janus/n.py", "previous_filename": "src/janus/o.py"},
                ]
            }
        raise AssertionError(url)

    snapshot = report.collect(get_json, "o/r", "2026-10-01", workflows=["ci.yml"], changed_files=True)

    (run,) = snapshot["runs"]
    assert set(run["jobs"][0]) == {
        "id",
        "name",
        "run_attempt",
        "status",
        "conclusion",
        "created_at",
        "started_at",
        "completed_at",
        "labels",
        "runner_group",
        "runner_assigned",
        "steps",
    }
    assert set(run["jobs"][0]["steps"][0]) == {"name", "conclusion", "started_at", "completed_at"}
    assert run["changed"] == {"paths": ["docs/docs/a.md", "src/janus/n.py", "src/janus/o.py"], "error": None}


def test_compare_listing_at_the_limit_is_unknown():
    """A compare response at the 300-file cap is incomplete, so its paths are unknown."""
    result = report._changed_paths(
        lambda url: {"files": [{"filename": f"f{i}"} for i in range(300)]}, "o/r", "main", "s"
    )
    assert result["paths"] is None


def test_freeze_and_price_workload():
    """The frozen workload records categories and weights; pricing applies selected jobs only."""
    runs = [
        _run(10, [], changed=["docs/docs/a.md"], created="2026-10-01T10:00:00Z"),
        _run(11, [], changed=["src/janus/x.py"], created="2026-10-01T11:00:00Z"),
        _run(12, [], changed=["docs/docs/b.md"], created="2026-10-01T12:00:00Z", conclusion="cancelled"),
        _run(13, [], changed=None, created="2026-10-01T13:00:00Z", event="push"),
        _run(14, [], created="2026-10-01T14:00:00Z"),  # no changed paths known
    ]
    workload = report.freeze_workload({"runs": runs, "collected_at": "t"}, count=20)

    assert [u["run_id"] for u in workload["updates"]] == [14, 11, 10]
    assert workload["categories"] == {
        "backend+e2e+openapi+semgrep": {"updates": 1, "weight": 0.333},
        "docs": {"updates": 1, "weight": 0.333},
        "full (unknown paths)": {"updates": 1, "weight": 0.333},
    }

    costs = {
        "pr-title": 1,
        "backend-quality": 9,
        "migration-tests": 2,
        "frontend-quality": 2,
        "e2e-tests": 4,
        "docs-build": 1,
        "openapi-check": 1,
        "semgrep": 2,
        "dependency-audit": 1,
        "changes": 1,
    }
    estimate = report.price_workload(workload, costs, extra_jobs=("changes",))

    per_update_all = 23
    assert estimate["baseline_policy_rounded_minutes"] == 3 * per_update_all
    # full: 23 + changes; backend: title + backend + e2e + openapi + semgrep + changes; docs: title + docs + changes
    assert estimate["selected_policy_rounded_minutes"] == (23 + 1) + (1 + 9 + 4 + 1 + 2 + 1) + (1 + 1 + 1)
    assert estimate["jobs_without_cost"] == []


def test_report_markdown_and_comparison(tmp_path):
    """The CLI writes JSON and Markdown; a baseline report adds a before/after table."""
    snapshot = {
        "repository": "o/r",
        "period": {"since": "2026-10-01", "until": None},
        "collected_at": "t",
        "runs": [
            _run(
                1,
                [
                    _job(
                        "backend-quality",
                        0,
                        500,
                        steps=[
                            {
                                "name": "Run backend tests with coverage",
                                "conclusion": "success",
                                "started_at": "2026-10-01T12:00:10Z",
                                "completed_at": "2026-10-01T12:08:00Z",
                            },
                            {
                                "name": "Set up job",
                                "conclusion": "success",
                                "started_at": "2026-10-01T12:00:00Z",
                                "completed_at": "2026-10-01T12:00:02Z",
                            },
                        ],
                    ),
                    _job("pr-title", 0, 4),
                ],
            ),
        ],
    }
    snapshot_path = tmp_path / "s.json"
    snapshot_path.write_text(json.dumps(snapshot))
    baseline_path, markdown_path = tmp_path / "base.json", tmp_path / "r.md"

    assert report.main(["report", str(snapshot_path), "--json", str(baseline_path)]) == 0
    assert (
        report.main(["report", str(snapshot_path), "--baseline", str(baseline_path), "--markdown", str(markdown_path)])
        == 0
    )

    data = json.loads(baseline_path.read_text())
    assert data["top_steps"]["backend-quality"][0] == {
        "step": "Run backend tests with coverage",
        "median_seconds": 470.0,
        "samples": 1,
    }
    assert data["workflows"]["CI (pull_request)"]["median_rounded_minutes"] == 9 + 1
    assert "not invoice usage" in data["note"]
    text = markdown_path.read_text()
    assert "### Before and after" in text
    assert "### Slowest steps: backend-quality" in text


def test_reusable_workflow_job_names_map_to_their_base_name():
    """A job called through a reusable workflow is priced under its own name."""
    assert report.base_job_name("semgrep / semgrep") == "semgrep"
    assert report.base_job_name("backend-quality") == "backend-quality"


def test_matrix_legs_are_priced_together_under_their_base_name():
    """The e2e shards (JAN-345) count as one job: their names map back, their costs add up."""
    assert report.base_job_name("e2e-tests (2/3)") == "e2e-tests"
    summary = {
        "jobs": {
            "e2e-tests (1/3)": {"median_rounded_minutes": 4},
            "e2e-tests (2/3)": {"median_rounded_minutes": 5},
            "e2e-tests (3/3)": {"median_rounded_minutes": 4},
            "frontend-quality": {"median_rounded_minutes": 2},
        }
    }
    costs = report.job_costs_from_report({"workflows": {"CI (pull_request)": summary}})
    assert costs == {"e2e-tests": 13, "frontend-quality": 2}


def test_workload_applies_refined_pyproject_selection():
    """An update whose pyproject change was narrowed is priced with the narrowed selection."""
    run = _run(20, [], changed=["pyproject.toml", "src/janus/x.py"])
    run["changed"]["refined"] = {"pyproject.toml": ["backend"]}

    (update,) = report.freeze_workload({"runs": [run], "collected_at": "t"})["updates"]

    assert update["category"] == "backend+e2e+openapi+semgrep"


def test_collect_records_refined_pyproject_changes():
    """With a text getter, a tool-only pyproject change is narrowed at collection time."""
    base = '[project]\nname = "janus"\nversion = "1"\n[tool.mypy]\nfiles = ["a"]\n'
    head = base.replace('["a"]', '["a", "b"]')

    def get_json(url):
        return {"files": [{"filename": "pyproject.toml"}], "merge_base_commit": {"sha": "MB"}}

    def get_text(url):
        return {"MB": base, "HEAD": head}[url.rsplit("=", 1)[1]]

    changed = report._changed_paths(get_json, "o/r", "main", "HEAD", get_text)

    assert changed == {"paths": ["pyproject.toml"], "error": None, "refined": {"pyproject.toml": ["backend"]}}
