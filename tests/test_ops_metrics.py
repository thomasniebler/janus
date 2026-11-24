"""Deploy version verification, deploy/availability metrics and their history branch (JAN-269).

Covers scripts/ops_metrics.py and scripts/history_branch.py. No test reaches the network; the
history tests push to a local bare repository.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


history = _load("history_branch")
ops = _load("ops_metrics")

T0 = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
PYPROJECT = '[project]\nname = "janus"\nversion = "1.9.0"\n'


def _sample(status=200, health="ok", version="1.9.0", error=None, at=T0, accounts=0):
    return ops.HealthSample(at, status, health, version, 120 if status else None, accounts, error)


def _sequence(*samples):
    """A fake fetch answering with ``samples`` in order and recording the calls."""
    queue = list(samples)
    calls = []

    def fetch(url):
        calls.append(url)
        return queue.pop(0)

    fetch.calls = calls
    return fetch


# --- expected version ---------------------------------------------------------------------------


def test_expected_version_accepts_matching_tag_and_branches():
    """Expected version accepts matching tag and branches."""
    assert ops.expected_version("v1.9.0", PYPROJECT) == "1.9.0"
    assert ops.expected_version("refs/tags/v1.9.0", PYPROJECT) == "1.9.0"
    assert ops.expected_version("main", PYPROJECT) == "1.9.0"


def test_expected_version_rejects_a_tag_that_disagrees_with_pyproject():
    """Expected version rejects a tag that disagrees with pyproject."""
    with pytest.raises(ValueError, match="does not match"):
        ops.expected_version("v1.8.0", PYPROJECT)


def test_expected_version_cli_exit_codes(tmp_path, capsys):
    """Expected version cli exit codes."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(PYPROJECT)
    assert ops.main(["expected-version", "--ref", "v1.9.0", "--pyproject", str(pyproject)]) == 0
    assert capsys.readouterr().out.strip() == "1.9.0"
    assert ops.main(["expected-version", "--ref", "v2.0.0", "--pyproject", str(pyproject)]) == 1


# --- verify-version -----------------------------------------------------------------------------


def test_verify_version_passes_on_200_with_expected_version():
    """Verify version passes on 200 with expected version."""
    check = ops.verify_version("https://j.example", "v1.9.0", 3, 5, fetch=_sequence(_sample()), sleep=lambda s: None)
    assert check["ok"] is True
    assert check["expected_version"] == "1.9.0"
    assert check["actual_version"] == "1.9.0"
    assert check["attempts"] == 1
    assert check["verified_at"] == "2026-10-11T12:00:00Z"


def test_verify_version_fails_on_200_with_the_wrong_version():
    """Verify version fails on 200 with the wrong version."""
    sleeps = []
    fetch = _sequence(*[_sample(version="1.8.0")] * 3)
    check = ops.verify_version("https://j.example", "1.9.0", 3, 7, fetch=fetch, sleep=sleeps.append)
    assert check["ok"] is False
    assert check["last_http_status"] == 200
    assert check["actual_version"] == "1.8.0"
    assert "expected '1.9.0'" in check["detail"]
    assert check["verified_at"] is None
    # Bounded: exactly `attempts` requests and one sleep between each pair.
    assert len(fetch.calls) == 3
    assert sleeps == [7, 7]


def test_verify_version_retries_through_restart_then_passes():
    """Verify version retries through restart then passes."""
    fetch = _sequence(
        _sample(status=None, health=None, version=None, error="ConnectionRefusedError"),
        _sample(status=502, health=None, version=None),
        _sample(),
    )
    check = ops.verify_version("https://j.example", "1.9.0", 5, 1, fetch=fetch, sleep=lambda s: None)
    assert check["ok"] is True
    assert check["attempts"] == 3


def test_verify_version_fails_when_degraded_even_with_right_version():
    """Verify version fails when degraded even with right version."""
    fetch = _sequence(*[_sample(status=500, health="degraded")] * 2)
    check = ops.verify_version("https://j.example", "1.9.0", 2, 1, fetch=fetch, sleep=lambda s: None)
    assert check["ok"] is False
    assert "HTTP 500" in check["detail"]


def test_version_check_markdown_names_expected_and_actual():
    """Version check markdown names expected and actual."""
    fetch = _sequence(_sample(version="1.8.0"))
    markdown = ops.version_check_markdown(ops.verify_version("https://j.example", "1.9.0", 1, 1, fetch=fetch))
    assert "FAIL" in markdown
    assert "`1.9.0`" in markdown and "`1.8.0`" in markdown


def test_verify_version_cli_rejects_non_http_urls():
    """Verify version cli rejects non http urls."""
    with pytest.raises(SystemExit):
        ops.main(["verify-version", "--base-url", "file:///etc/passwd", "--expected", "1.9.0"])


# --- probe --------------------------------------------------------------------------------------


def test_probe_records_every_sample_and_the_interval():
    """Probe records every sample and the interval."""
    sleeps = []
    fetch = _sequence(_sample(), _sample(status=500, health="degraded", accounts=2), _sample())
    record = ops.probe("https://j.example", 3, 30, "v1.9.0", fetch=fetch, sleep=sleeps.append, now=lambda: T0, env={})
    assert sleeps == [30, 30]
    assert record["schema"] == ops.AVAILABILITY_SCHEMA
    assert record["sampling"] == {"samples": 3, "interval_seconds": 30, "timeout_seconds": ops.TIMEOUT_SECONDS}
    assert (record["ok_samples"], record["total_samples"], record["availability"]) == (2, 3, 0.6667)
    assert record["samples"][1]["unhealthy_account_count"] == 2
    assert record["version_matches"] is True


def test_probe_cli_fails_only_when_no_sample_was_healthy(monkeypatch, tmp_path):
    """Probe cli fails only when no sample was healthy."""
    monkeypatch.setattr(
        ops, "fetch_health", lambda url: _sample(status=None, health=None, version=None, error="TimeoutError")
    )
    monkeypatch.setattr(ops.time, "sleep", lambda s: None)
    out = tmp_path / "probe.json"
    argv = ["probe", "--base-url", "https://j.example", "--samples", "2", "--interval", "0", "--json", str(out)]
    assert ops.main(argv) == 1
    assert json.loads(out.read_text())["ok_samples"] == 0
    monkeypatch.setattr(ops, "fetch_health", lambda url: _sample())
    assert ops.main(argv) == 0


# --- secrets guard ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "record",
    [
        {"password": "x"},
        {"nested": {"api_key": "x"}},
        {"error": "Bearer abc.def"},
        {"samples": [{"error": "ghp_" + "a" * 30}]},
        {"accounts": ["someone@example.org"]},
        {"detail": "lin_api_123"},
    ],
)
def test_secret_looking_records_are_refused(record):
    """Secret looking records are refused."""
    with pytest.raises(ValueError, match="secret-looking"):
        ops.ensure_exportable(record)


def test_health_sample_drops_account_addresses():
    """Health sample drops account addresses."""
    record = _sample(status=500, health="degraded", accounts=3).as_record()
    assert record["unhealthy_account_count"] == 3
    assert ops.find_secrets(record) == []


# --- deploy record ------------------------------------------------------------------------------


def _check(ok=True, verified=T0 + timedelta(minutes=6)):
    return {
        "ok": ok,
        "actual_version": "1.9.0" if ok else "1.8.0",
        "verified_at": ops.iso(verified) if ok else None,
        "attempts": 2,
        "max_attempts": 12,
        "delay_seconds": 10,
        "last_http_status": 200,
    }


ENV = {
    "GITHUB_SERVER_URL": "https://github.com",
    "GITHUB_REPOSITORY": "o/janus",
    "GITHUB_RUN_ID": "42",
    "GITHUB_RUN_ATTEMPT": "1",
    "GITHUB_EVENT_NAME": "release",
    "GITHUB_WORKFLOW": "Deploy",
    "GITHUB_SHA": "abc",
}


def test_deploy_record_duration_runs_from_first_step_to_verified_version():
    """Deploy record duration runs from first step to verified version."""
    record = ops.deploy_record(
        started_at=str(int(T0.timestamp())),
        restarted_at=str(int((T0 + timedelta(minutes=5)).timestamp())),
        check=_check(),
        deploy_result="success",
        validate_result="success",
        ref="v1.9.0",
        version="1.9.0",
        now=lambda: T0 + timedelta(minutes=7),
        env=ENV,
    )
    assert record["outcome"] == "success"
    assert record["timing"]["duration_seconds"] == 360
    assert record["timing"]["restart_to_healthy_seconds"] == 60
    assert record["run"]["url"] == "https://github.com/o/janus/actions/runs/42"
    assert record["commit"] == "abc"


def test_failed_deploy_has_no_duration():
    """Failed deploy has no duration."""
    record = ops.deploy_record(
        started_at=str(int(T0.timestamp())),
        restarted_at=None,
        check=_check(ok=False),
        deploy_result="failure",
        validate_result="skipped",
        ref="v1.9.0",
        version="1.9.0",
        env={},
    )
    assert record["outcome"] == "failure"
    assert record["timing"]["duration_seconds"] is None
    assert record["actual_version"] == "1.8.0"


def test_deploy_record_without_a_verification_file(tmp_path):
    """Deploy record without a verification file."""
    out = tmp_path / "deploy.json"
    argv = [
        "deploy-record",
        "--started-at",
        "1760184000",
        "--deploy-result",
        "failure",
        "--ref",
        "main",
        "--version",
        "1.9.0",
        "--verify-json",
        str(tmp_path / "missing.json"),
        "--out",
        str(out),
    ]
    assert ops.main(argv) == 0
    assert json.loads(out.read_text())["outcome"] == "failure"


# --- summaries and export -----------------------------------------------------------------------


def test_availability_summary_states_window_and_interval():
    """Availability summary states window and interval."""
    records = [
        {
            "recorded_at": ops.iso(T0 - timedelta(days=d)),
            "ok_samples": ok,
            "total_samples": 5,
            "sampling": {"interval_seconds": 30},
        }
        for d, ok in ((1, 5), (2, 4), (40, 0))
    ]
    summary = ops.availability_summary(records, 30, T0)
    assert (summary["runs"], summary["samples"], summary["ok_samples"]) == (2, 10, 9)
    assert summary["availability"] == 0.9
    markdown = ops.summary_markdown("availability", summary)
    assert "90.00%" in markdown and "30 s apart" in markdown and "not continuous uptime" in markdown


def test_deploy_summary_and_csv_export():
    """Deploy summary and csv export."""
    records = [
        {"recorded_at": ops.iso(T0), "outcome": "success", "timing": {"duration_seconds": 300}},
        {"recorded_at": ops.iso(T0), "outcome": "failure", "timing": {"duration_seconds": None}},
    ]
    summary = ops.deploy_summary(records, 30, T0)
    assert (summary["deploys"], summary["successes"], summary["median_duration_seconds"]) == (2, 1, 300)
    csv_text = ops.export_csv(records)
    assert csv_text.splitlines()[0] == "outcome,recorded_at,timing.duration_seconds"


# --- history branch -----------------------------------------------------------------------------


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def clones(tmp_path):
    """A bare remote and two clones of it, each with one commit on main."""
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "--quiet", "--bare", "-b", "main", str(remote))
    paths = []
    for name in ("a", "b"):
        clone = tmp_path / name
        _git(tmp_path, "clone", "--quiet", str(remote), str(clone))
        paths.append(clone)
    seed = paths[0]
    (seed / "README").write_text("main\n")
    _git(seed, "add", "README")
    _git(seed, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "--quiet", "-m", "init")
    _git(seed, "push", "--quiet", "origin", "HEAD:main")
    _git(paths[1], "pull", "--quiet", "origin", "main")
    return remote, paths


def test_history_branch_is_an_append_only_orphan(clones):
    """History branch is an append only orphan."""
    remote, (a, b) = clones
    assert history.read_history(a, "origin", "ops-metrics", "x/data.jsonl") == []
    history.append_and_push(a, "origin", "ops-metrics", "x/data.jsonl", [{"n": 1}], "first")
    # b has never seen the branch; its publish must build on a's commit, not replace it.
    history.append_and_push(b, "origin", "ops-metrics", "x/data.jsonl", [{"n": 2}, {"n": 3}], "second")
    assert history.read_history(a, "origin", "ops-metrics", "x/data.jsonl") == [{"n": 1}, {"n": 2}, {"n": 3}]
    log = _git(remote, "log", "--format=%s", "ops-metrics").split()
    assert log == ["second", "first"]
    # Orphan: no shared history with main, and only the data file on the branch.
    assert _git(remote, "ls-tree", "-r", "--name-only", "ops-metrics").split() == ["x/data.jsonl"]
    assert subprocess.run(["git", "merge-base", "main", "ops-metrics"], cwd=remote, capture_output=True).returncode == 1
    # The clone's own checkout is untouched.
    assert _git(a, "status", "--porcelain") == ""
    assert _git(a, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"


def test_history_push_retries_after_losing_a_race(clones):
    """History push retries after losing a race."""
    _remote, (a, b) = clones
    history.append_and_push(a, "origin", "h", "d.jsonl", [{"n": 1}], "first")
    real = history.run_git
    raced = {"done": False}

    def racing_git(repo, *args, **kwargs):
        # Just before a's push, b publishes; a's first push is then rejected as non-fast-forward.
        if args[:1] == ("push",) and not raced["done"]:
            raced["done"] = True
            history.append_and_push(b, "origin", "h", "d.jsonl", [{"n": 2}], "racer")
        return real(repo, *args, **kwargs)

    history.append_and_push(a, "origin", "h", "d.jsonl", [{"n": 3}], "third", git=racing_git)
    assert history.read_history(a, "origin", "h", "d.jsonl") == [{"n": 1}, {"n": 2}, {"n": 3}]


def test_history_push_gives_up_after_bounded_attempts(clones):
    """History push gives up after bounded attempts."""
    _remote, (a, _b) = clones
    real = history.run_git

    def rejecting_git(repo, *args, **kwargs):
        if args[:1] == ("push",):
            return subprocess.CompletedProcess(args, 1, "", "rejected")
        return real(repo, *args, **kwargs)

    with pytest.raises(RuntimeError, match="after 2 attempts: rejected"):
        history.append_and_push(a, "origin", "h", "d.jsonl", [{"n": 1}], "m", git=rejecting_git, attempts=2)


def test_publish_cli_refuses_secret_records(clones, tmp_path):
    """Publish cli refuses secret records."""
    _remote, (a, _b) = clones
    record = tmp_path / "r.json"
    record.write_text(json.dumps({"recorded_at": "2026-10-11T00:00:00Z", "token": "x"}))
    with pytest.raises(ValueError, match="secret-looking"):
        ops.main(["publish", "--dataset", "deploys", "--record", str(record), "--repo", str(a)])
    assert history.read_history(a, "origin", ops.HISTORY_BRANCH, ops.DATASETS["deploys"]) == []


def test_read_jsonl_rejects_non_objects():
    """Read jsonl rejects non objects."""
    with pytest.raises(ValueError, match="line 2"):
        history.read_jsonl('{"a": 1}\n[1]\n')


def test_fetch_health_refuses_non_http_urls():
    """The health fetch never opens file:// or other non-HTTP URLs, even when called directly."""
    with pytest.raises(ValueError, match="http"):
        ops.fetch_health("file:///etc")


def test_deploy_record_names_the_deployed_commit_not_the_workflow_sha():
    """A rollback deploys an older ref, so the record keeps that ref's commit over GITHUB_SHA."""
    record = ops.deploy_record(
        started_at=str(int(T0.timestamp())),
        restarted_at=None,
        check=_check(),
        deploy_result="success",
        validate_result="success",
        ref="v1.8.0",
        version="1.9.0",
        commit="0ldc0mm1t",
        env=ENV,
    )
    assert record["commit"] == "0ldc0mm1t"


def test_fetch_health_stamps_the_sample_when_the_response_completed(monkeypatch):
    """The verified time is when the endpoint answered, so request latency counts toward the duration."""

    class _Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return json.dumps({"status": "ok", "version": "1.9.0"}).encode()

    answered = []

    def _urlopen(request, timeout):
        answered.append(True)
        return _Response()

    monkeypatch.setattr(ops.urllib.request, "urlopen", _urlopen)
    sample = ops.fetch_health("https://j.example", now=lambda: T0 + timedelta(seconds=8) if answered else T0)
    assert sample.ok
    assert sample.at == T0 + timedelta(seconds=8)
