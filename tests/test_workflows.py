"""GitHub Actions workflow policy (JAN-309, JAN-310, JAN-313): selection wiring, cancellation, bounds."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
_spec = importlib.util.spec_from_file_location("ci_paths_for_workflows", ROOT / "scripts" / "ci_paths.py")
ci_paths = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = ci_paths
_spec.loader.exec_module(ci_paths)


def _load(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def _triggers(workflow: dict) -> dict:
    # PyYAML reads the bare key `on` as the boolean True.
    return workflow.get("on", workflow.get(True))


def test_every_runner_job_has_a_timeout():
    """A hung step ends at the job's bound instead of GitHub's six-hour default."""
    missing = [
        f"{path.name}:{name}"
        for path in sorted(WORKFLOWS.glob("*.yml"))
        for name, job in _load(path.name)["jobs"].items()
        if "runs-on" in job and "timeout-minutes" not in job
    ]
    assert missing == []


def test_ci_cancels_superseded_pull_request_runs_only():
    """A PR's newer push cancels its older run; other runs get a group per commit and never cancel."""
    concurrency = _load("ci.yml")["concurrency"]
    assert "github.event.pull_request.number" in concurrency["group"]
    assert "github.workflow" in concurrency["group"]
    assert "github.sha" in concurrency["group"]
    assert concurrency["cancel-in-progress"] == "${{ github.event_name == 'pull_request' }}"


def test_deploy_is_never_cancelled():
    """Production deploys stay serialized and are never cancelled by the PR policy."""
    concurrency = _load("deploy-uberspace.yml")["concurrency"]
    assert concurrency == {"group": "deploy-uberspace-production", "cancel-in-progress": False}


def test_ci_jobs_follow_the_selection():
    """Every CI job but `changes` waits for it and runs only when its own output is true."""
    workflow = _load("ci.yml")
    jobs = workflow["jobs"]
    outputs = jobs["changes"]["outputs"]
    assert set(outputs) == {*ci_paths.ALL_JOBS, ci_paths.FULL_OUTPUT}
    keys = {
        "backend-quality": "backend",
        "migration-tests": "migrations",
        "frontend-quality": "frontend",
        "e2e-tests": "e2e",
        "docs-build": "docs",
        "openapi-check": "openapi",
        "semgrep": "semgrep",
        "dependency-audit": "dependency_audit",
    }
    assert set(jobs) == {"changes", "e2e-report", "ci-result", *keys}
    for name, key in keys.items():
        assert jobs[name]["needs"] == "changes"
        assert jobs[name]["if"] == f"needs.changes.outputs.{key} == 'true'"
    # The report merge follows the e2e selection and also runs after failed shards (JAN-345).
    assert jobs["e2e-report"]["needs"] == ["changes", "e2e-tests"]
    assert jobs["e2e-report"]["if"] == "${{ !cancelled() && needs.changes.outputs.e2e == 'true' }}"


def test_ci_result_gates_on_every_job():
    """`ci-result`, the check the main ruleset requires, waits for every job and always runs."""
    jobs = _load("ci.yml")["jobs"]
    gate = jobs["ci-result"]
    assert set(gate["needs"]) == set(jobs) - {"ci-result"}
    assert gate["if"] == "${{ always() }}"
    script = gate["steps"][0]["run"]
    assert '"failure"' in script and '"cancelled"' in script
    assert gate["steps"][0]["env"]["FULL"] == "${{ needs.changes.outputs.full }}"


def test_e2e_runs_in_independent_shards():
    """Every shard runs to the end and passes its own `--shard` to Playwright (JAN-345)."""
    job = _load("ci.yml")["jobs"]["e2e-tests"]
    assert job["strategy"]["fail-fast"] is False
    shards = job["strategy"]["matrix"]["shard"]
    assert shards == list(range(1, len(shards) + 1)) and len(shards) > 1
    run = _step(job, "Run end-to-end tests")["run"]
    assert "--shard=${{ matrix.shard }}/${{ strategy.job-total }}" in run


def test_security_scans_run_on_schedule_and_through_ci():
    """The scans keep their scheduled full runs; pull requests reach them only through ci.yml."""
    for name in ("semgrep.yml", "dependency-audit.yml"):
        triggers = _triggers(_load(name))
        assert "schedule" in triggers
        assert "workflow_call" in triggers
        assert "pull_request" not in triggers
    assert _load("ci.yml")["jobs"]["semgrep"]["uses"] == "./.github/workflows/semgrep.yml"
    assert _load("ci.yml")["jobs"]["dependency-audit"]["uses"] == "./.github/workflows/dependency-audit.yml"


def test_pr_title_reruns_on_edit_without_ci():
    """Editing a title re-runs the title check, and CI does not listen to edits."""
    assert "edited" in _triggers(_load("pr-title.yml"))["pull_request"]["types"]
    assert "edited" not in _triggers(_load("ci.yml"))["pull_request"]["types"]


def test_release_prs_are_opened_with_a_token_that_starts_their_checks():
    """The release PR must run ci-result and pr-title like any PR, so release-please opens it with its own token."""
    workflow = _load("release-please.yml")
    step = next(s for s in workflow["jobs"]["release-please"]["steps"] if s.get("id") == "release")
    assert "secrets.RELEASE_PLEASE_TOKEN" in step["with"]["token"]
    assert "actions" not in workflow["permissions"]


def test_ci_runs_on_pull_requests_and_nightly_not_per_push_to_main():
    """Merges are validated nightly instead of per push; a PR re-runs when it becomes ready for review."""
    triggers = _triggers(_load("ci.yml"))
    assert "push" not in triggers
    assert triggers["schedule"]
    assert "workflow_dispatch" in triggers
    assert {"opened", "synchronize", "reopened", "ready_for_review"} <= set(triggers["pull_request"]["types"])


def test_nightly_run_skips_itself_when_main_has_not_moved():
    """The skip step runs only on schedule and, when it fires, the selection step does not run."""
    steps = _load("ci.yml")["jobs"]["changes"]["steps"]
    skip = _step({"steps": steps}, "Skip the nightly run when main has not moved")
    select = _step({"steps": steps}, "Select jobs from the changed paths")
    assert skip["if"] == "github.event_name == 'schedule'"
    assert "event=schedule" in skip["run"]
    assert select["if"] == "steps.nightly.outputs.skip != 'true'"
    assert '--draft "$PR_DRAFT"' in select["run"]


def test_draft_pull_requests_skip_the_test_steps():
    """The backend and frontend test steps follow the `full` output, so drafts skip them."""
    jobs = _load("ci.yml")["jobs"]
    gated = {
        "backend-quality": ("Run backend tests with coverage", "Check coverage of changed lines"),
        "frontend-quality": ("Run frontend tests with coverage",),
    }
    for job, names in gated.items():
        for name in names:
            assert "needs.changes.outputs.full == 'true'" in _step(jobs[job], name)["if"], (job, name)


def test_cancelled_runs_skip_report_uploads():
    """Superseded runs do not spend time uploading reports (`!cancelled()`, not `always()`)."""
    for name in ("ci.yml", "semgrep.yml", "dependency-audit.yml"):
        for job in _load(name)["jobs"].values():
            for step in job.get("steps", []):
                if str(step.get("uses", "")).startswith("actions/upload-artifact"):
                    assert step.get("if") == "${{ !cancelled() }}", (name, step["name"])
                    assert step["with"].get("retention-days") == 14, (name, step["name"])


def test_docs_job_runs_the_docs_reading_tests():
    """The docs job runs exactly the backend tests listed in ci_paths.DOCS_TESTS."""
    steps = _load("ci.yml")["jobs"]["docs-build"]["steps"]
    command = next(step["run"] for step in steps if step.get("name") == "Run the backend tests that read the docs")
    for test_file in ci_paths.DOCS_TESTS:
        assert test_file in command
    assert f'-k "{ci_paths.DOCS_TESTS_KEYWORD}"' in command


def _step(job: dict, name: str) -> dict:
    return next(step for step in job["steps"] if step.get("name") == name)


def test_deploy_verifies_the_release_version_and_records_metrics():
    """JAN-269: a 200 with the wrong version fails the deploy; duration and outcome are recorded."""
    workflow = _load("deploy-uberspace.yml")
    assert workflow["permissions"] == {"contents": "read"}
    deploy = workflow["jobs"]["deploy"]
    assert deploy["steps"][0]["id"] == "deploy-start"
    assert {"started_at", "version_check", "restarted_at", "version"} <= set(deploy["outputs"])
    verify = _step(deploy, "Verify release version")["run"]
    assert ".deploy-tools/scripts/ops_metrics.py verify-version" in verify
    assert "--attempts 12" in verify
    assert "curl" not in verify
    # Rollbacks to tags without the script still work; the tooling checkout is never synced to the host.
    assert _step(deploy, "Check out deploy tooling")["with"]["path"] == ".deploy-tools"
    assert "--exclude '.deploy-tools/'" in _step(deploy, "Sync release to the host")["run"]
    metrics = workflow["jobs"]["metrics"]
    assert metrics["needs"] == ["deploy", "validate"]
    assert metrics["permissions"] == {"contents": "write"}
    assert "report-failure" not in workflow["jobs"]
    # The release-please caller must grant at least what the called workflow's jobs ask for.
    assert _load("release-please.yml")["permissions"]["contents"] == "write"


def test_nightly_probe_samples_availability_with_least_privilege():
    """JAN-269: only the record job can push, and only a failed probe files a release failure."""
    workflow = _load("nightly-health-check.yml")
    jobs = workflow["jobs"]
    assert workflow["permissions"] == {"contents": "read"}
    assert "permissions" not in jobs["probe"]
    assert "ops_metrics.py probe" in _step(jobs["probe"], "Sample hosted Janus health")["run"]
    assert jobs["record"]["permissions"] == {"contents": "write"}
    assert "publish --dataset availability" in _step(jobs["record"], "Append to the ops-metrics history")["run"]
    assert "report-failure" not in jobs


def test_quality_report_runs_weekly_and_keeps_a_durable_main_history():
    """JAN-270: schedule + dispatch, the script does the work, only main reports are pushed, gate last."""
    workflow = _load("quality-report.yml")
    triggers = _triggers(workflow)
    assert "schedule" in triggers and "workflow_dispatch" in triggers
    assert workflow["permissions"] == {"contents": "read", "actions": "read"}
    # Overlapping main runs queue instead of racing on the history (and are never cancelled).
    assert workflow["concurrency"] == {"group": "quality-report-${{ github.ref }}", "cancel-in-progress": False}
    report = workflow["jobs"]["report"]
    runs = [step.get("run", "") for step in report["steps"]]
    assert any("quality_report.py collect" in run for run in runs)
    assert any("quality_report.py finalize" in run for run in runs)
    # The gate runs after the report is uploaded, so a failing check still leaves a report behind.
    assert "quality_report.py gate" in runs[-1]
    upload = next(step for step in report["steps"] if str(step.get("uses", "")).startswith("actions/upload-artifact"))
    assert upload["with"]["retention-days"] == 90
    assert upload["if"] == "${{ !cancelled() }}"
    history = workflow["jobs"]["history"]
    assert history["permissions"] == {"contents": "write"}
    assert "github.ref == 'refs/heads/main'" in history["if"]
    assert "!cancelled()" in history["if"]
    assert any("quality_report.py publish" in step.get("run", "") for step in history["steps"])


def test_task_quality_report_gates_last():
    """`task quality:report` fails like the workflow does, after the report and listings are written."""
    taskfile = yaml.safe_load((Path(__file__).resolve().parents[1] / "Taskfile.yml").read_text(encoding="utf-8"))
    cmds = taskfile["tasks"]["quality:report"]["cmds"]
    assert "quality_report.py gate quality-report/quality-report.json" in cmds[-1]
    assert "|| true" not in cmds[-1]


def test_structural_review_is_read_only_and_needs_no_secret():
    """The monthly review input (JAN-271) runs without secrets, an LLM, or write access."""
    text = (WORKFLOWS / "structural-review.yml").read_text(encoding="utf-8")
    workflow = _load("structural-review.yml")
    triggers = _triggers(workflow)
    assert triggers["schedule"] == [{"cron": "41 7 2 * *"}]
    assert "workflow_dispatch" in triggers
    assert workflow["permissions"] == {"contents": "read"}
    assert "secrets." not in text
    assert "environment:" not in text
    (job,) = workflow["jobs"].values()
    assert "permissions" not in job
    runs = "\n".join(step.get("run", "") for step in job["steps"])
    assert "structural_review.py" in runs and "bundle" in runs
    # It never records a review, pushes, or reaches an issue tracker: that is the reviewer's step.
    for forbidden in ("record", "git push", "linear", "curl", "ssh"):
        assert forbidden not in runs.lower()
    # The dispatch input reaches the script only through the environment.
    assert "${{ inputs.base }}" not in runs
    assert any(step.get("env", {}).get("BASE") == "${{ inputs.base }}" for step in job["steps"])
