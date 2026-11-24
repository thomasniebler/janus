"""Weekly quality report: checks, fixed schema, delta, ratcheted baselines, history (JAN-270).

Covers scripts/quality_report.py with a fake tool runner; no test runs pytest, jscpd, mypy,
Semgrep or an audit for real. The history tests push to a local bare repository.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
from datetime import UTC, datetime
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
qr = _load("quality_report")

NOW = datetime(2026, 10, 12, 6, 37, tzinfo=UTC)
JUNIT = """<?xml version="1.0"?><testsuites><testsuite name="pytest" tests="10" failures="1" errors="0"
skipped="2" time="12.5"></testsuite></testsuites>"""


def _done(command, code=0, out="", err=""):
    return subprocess.CompletedProcess(command, code, out, err)


@pytest.fixture(autouse=True)
def no_semgrep_exceptions(monkeypatch):
    """The fake Semgrep run finds nothing, so the real exception list would all be stale."""
    monkeypatch.setattr(qr._load_sibling("semgrep_scan"), "load_exceptions", lambda: [])


class FakeRunner:
    """Answers tool commands from a table keyed by a command fragment; writes the files tools would."""

    def __init__(self, work: Path, **overrides):
        """Write tool outputs under ``work``; ``overrides`` maps a command fragment (``_`` = space) to a handler."""
        self.work = work
        self.calls: list[list[str]] = []
        self.overrides = overrides

    def __call__(self, command):
        """Answer one tool command."""
        self.calls.append(command)
        joined = " ".join(command)
        for fragment, handler in self.overrides.items():
            if fragment in joined or fragment.replace("_", " ") in joined:
                return handler(command)
        if "pytest -q" in joined:
            (self.work / "junit.xml").write_text(JUNIT)
            (self.work / "coverage.json").write_text(
                json.dumps({"totals": {"percent_statements_covered": 90.123, "percent_branches_covered": 75.5}})
            )
            return _done(command, 1)
        if "ruff check" in joined:
            findings = [
                {
                    "message": "`big` is too complex (31 > 0)",
                    "filename": str(qr.ROOT / "src/janus/a.py"),
                    "location": {"row": 10},
                },
                {
                    "message": "`small` is too complex (2 > 0)",
                    "filename": str(qr.ROOT / "src/janus/b.py"),
                    "location": {"row": 3},
                },
            ]
            return _done(command, 0, json.dumps(findings))
        if "jscpd" in joined:
            out = Path(command[command.index("--output") + 1])
            out.mkdir(parents=True, exist_ok=True)
            percentage = 3.1 if "tests" in joined else 0.7
            report = {
                "statistics": {"total": {"percentage": percentage, "clones": 5}},
                "duplicates": [
                    {"lines": 12, "firstFile": {"name": "src/janus/a.py"}, "secondFile": {"name": "src/janus/b.py"}}
                ],
            }
            (out / "jscpd-report.json").write_text(json.dumps(report))
            return _done(command)
        if command[:3] == ["uv", "run", "mypy"] and len(command) == 3:
            return _done(command, 1, "x.py:1: error: bad\nFound 3 errors in 2 files (checked 40 source files)\n")
        if "semgrep_scan.py" in joined:
            Path(command[command.index("--json") + 1]).write_text(json.dumps({"results": [], "errors": []}))
            return _done(command)
        if "dependency_audit.py" in joined:
            Path(command[command.index("--json") + 1]).write_text(
                json.dumps([{"excepted": False, "blocking": False}, {"excepted": True, "blocking": True}])
            )
            return _done(command)
        if "--version" in joined:
            return _done(command, 0, "tool 1.2.3\n")
        if command[:2] == ["git", "rev-parse"]:
            return _done(command, 0, "abc123def456\n")
        raise AssertionError(f"unexpected command {command}")


def _collect(tmp_path, only=qr.CHECKS, **overrides):
    runner = FakeRunner(tmp_path, **overrides)
    report = qr.collect(
        only=set(only) - {"deployment"},
        work=tmp_path,
        runner=runner,
        now=NOW,
        env={"GITHUB_REF": qr.MAIN_REF, "GITHUB_SHA": "f" * 40},
    )
    return report, runner


# --- collect --------------------------------------------------------------------------------------


def test_collect_measures_every_check_and_follows_the_schema(tmp_path):
    """A full run fills every check, the tool versions, the scope and the hotspots."""
    report, _ = _collect(tmp_path)
    qr.validate(report)
    checks = report["checks"]
    assert checks["tests"]["status"] == "fail"
    assert checks["tests"]["measurements"] == {
        "tests.total": 10,
        "tests.failed": 1,
        "tests.skipped": 2,
        "tests.duration_seconds": 12.5,
    }
    assert checks["coverage"]["measurements"]["coverage.statements_percent"] == 90.12
    assert checks["complexity"]["measurements"]["complexity.over_limit"] == 1
    assert checks["duplication"]["status"] == "pass"
    assert checks["duplication"]["measurements"]["duplication.tests_percent"] == 3.1
    assert checks["types"] == {
        "status": "fail",
        "detail": "Found 3 errors in 2 files (checked 40 source files)",
        "measurements": {"types.errors": 3},
    }
    assert checks["dependencies"]["measurements"] == {"dependencies.blocking": 0, "dependencies.reported": 1}
    assert checks["deployment"]["status"] == "unavailable"
    assert report["tools"]["jscpd"] == "5.4.0" and report["tools"]["ruff"] == "1.2.3"
    assert set(report["scope"]) == set(qr.CHECKS)
    assert len(report["hotspots"]) == 10
    scores = [spot["score"] for spot in report["hotspots"]]
    assert scores == sorted(scores, reverse=True)


def test_hotspots_combine_complexity_size_and_duplication():
    """Each term is 1.0 at its limit; files are ranked by the sum and cut at ten."""
    functions = [
        {"path": "src/janus/a.py", "line": 10, "name": "big", "complexity": 30},
        {"path": "src/janus/a.py", "line": 50, "name": "small", "complexity": 3},
        {"path": "src/janus/b.py", "line": 1, "name": "f", "complexity": 15},
    ]
    modules = [
        {"path": "src/janus/a.py", "lines": 250, "budget": 500},
        {"path": "src/janus/b.py", "lines": 500, "budget": 500},
        *[{"path": f"src/janus/m{i}.py", "lines": 10, "budget": 500} for i in range(12)],
    ]
    spots = qr.hotspots(functions, modules, {"src/janus/b.py": 50})
    assert len(spots) == 10
    assert spots[0] == {
        "path": "src/janus/b.py",
        "lines": 500,
        "max_complexity": 15,
        "worst_function": "f:1",
        "duplicated_lines": 50,
        "score": 3.0,
    }
    assert spots[1]["path"] == "src/janus/a.py" and spots[1]["score"] == 2.5
    assert spots[1]["worst_function"] == "big:10"


def test_missing_tool_is_unavailable_and_crash_is_error(tmp_path):
    """A tool that is not installed is unavailable; one that gives no result is an error."""

    def missing(command):
        raise FileNotFoundError(command[0])

    report, _ = _collect(tmp_path, npx=missing, uv_run_mypy=lambda c: _done(c, 2, "", "mypy: crashed"))
    assert report["checks"]["duplication"]["status"] == "unavailable"
    assert report["checks"]["types"]["status"] == "error"
    assert "crashed" in report["checks"]["types"]["detail"]
    assert qr.gate(report) == [
        "tests: fail (10 tests, 1 failed, 2 skipped)",
        "types: error (mypy exit 2: mypy: crashed)",
    ]


def test_pytest_without_report_is_an_error_for_tests_and_coverage(tmp_path):
    """No JUnit file means the test run itself broke."""
    report, _ = _collect(tmp_path, pytest_=lambda c: _done(c, 4, "", "ImportError"))
    assert report["checks"]["tests"]["status"] == "error"
    assert report["checks"]["coverage"]["status"] == "error"
    assert qr.measurements(report).get("tests.total") is None


@pytest.mark.parametrize("code", [2, 3, 4, 5, 124])
def test_abnormal_test_run_exit_is_an_error_even_with_a_junit_file(tmp_path, code):
    """Interrupted, internal, usage, no-tests or timed-out runs are not measurements."""

    def partial_run(command):
        (tmp_path / "junit.xml").write_text(JUNIT)
        (tmp_path / "coverage.json").write_text(
            json.dumps({"totals": {"percent_statements_covered": 99.0, "percent_branches_covered": 99.0}})
        )
        return _done(command, code, "", "Interrupted: 1 error during collection")

    report, _ = _collect(tmp_path, pytest_=partial_run)
    assert report["checks"]["tests"]["status"] == "error"
    assert f"pytest exit {code}" in report["checks"]["tests"]["detail"]
    assert report["checks"]["coverage"]["status"] == "error"
    assert qr.measurements(report).get("tests.total") is None
    assert qr.measurements(report).get("coverage.statements_percent") is None
    assert [line.split(":")[0] for line in qr.gate(report)] == ["tests", "coverage", "types"]


def test_semgrep_follows_the_ci_verdict(tmp_path):
    """Blocking findings and scan errors (e.g. rule timeouts) fail the check, like the CI gate."""

    def scan_with(results, errors):
        def handler(command):
            Path(command[command.index("--json") + 1]).write_text(json.dumps({"results": results, "errors": errors}))
            return _done(command, 1)

        return handler

    finding = {
        "check_id": "x.rule",
        "path": "README.md",
        "start": {"line": 1},
        "extra": {"severity": "ERROR", "message": "m"},
    }
    report, _ = _collect(tmp_path, only=("semgrep",), semgrep_scan=scan_with([finding], []))
    assert report["checks"]["semgrep"]["status"] == "fail"
    assert report["checks"]["semgrep"]["measurements"] == {"semgrep.blocking": 1, "semgrep.reported": 0}
    timeout = [{"type": "Timeout", "message": "Timeout when running rule on x.py"}]
    report, _ = _collect(tmp_path, only=("semgrep",), semgrep_scan=scan_with([], timeout))
    assert report["checks"]["semgrep"]["status"] == "fail"
    assert "Timeout" in report["checks"]["semgrep"]["detail"]
    crashed, _ = _collect(tmp_path / "x", only=("semgrep",), semgrep_scan=lambda c: _done(c, 2, "", "no uvx"))
    assert crashed["checks"]["semgrep"]["status"] == "error"


def test_only_runs_the_selected_checks(tmp_path):
    """Unselected checks are marked unavailable and their tools never run."""
    report, runner = _collect(tmp_path, only=("complexity", "size"))
    assert report["checks"]["tests"]["status"] == "unavailable"
    assert report["checks"]["coverage"]["detail"] == "not selected for this run"
    assert not any("pytest -q" in " ".join(c) for c in runner.calls)
    assert report["checks"]["size"]["measurements"]["size.backend_modules"] > 0


def test_complexity_parser_ignores_other_messages():
    """Only C901 messages count, and paths become repository-relative."""
    findings = [
        {"message": "`f` is too complex (16 > 0)", "filename": str(qr.ROOT / "src/janus/x.py"), "location": {"row": 4}},
        {"message": "something else", "filename": "y.py", "location": {"row": 1}},
    ]
    assert qr.parse_ruff_complexity(findings) == [{"path": "src/janus/x.py", "line": 4, "name": "f", "complexity": 16}]


def test_module_sizes_skip_tests_stories_and_generated(tmp_path):
    """Frontend tests, stories, fixtures and generated code are not production modules."""
    for name in ("a.tsx", "a.test.tsx", "a.stories.tsx", "aFixtures.ts", "generated/api.ts", "test/x.ts"):
        path = tmp_path / "frontend" / "src" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x\n\ny\n")
    (tmp_path / "src" / "janus").mkdir(parents=True)
    (tmp_path / "src" / "janus" / "m.py").write_text("a\n" * 501)
    modules = qr.module_sizes(tmp_path)
    assert [m["path"] for m in modules] == ["frontend/src/a.tsx", "src/janus/m.py"]
    result, _ = qr.check_size(tmp_path)
    assert result.measurements["size.backend_over_budget"] == 1
    assert result.measurements["size.frontend_modules"] == 1


def test_deployment_measurements_from_ops_history():
    """The deployment check summarizes the last 30 days of the JAN-269 records."""
    deploys = [
        {"recorded_at": "2026-10-10T00:00:00Z", "outcome": "success", "timing": {"duration_seconds": 300}},
        {"recorded_at": "2026-10-11T00:00:00Z", "outcome": "failure", "timing": {"duration_seconds": None}},
        {"recorded_at": "2026-08-01T00:00:00Z", "outcome": "success", "timing": {"duration_seconds": 900}},
    ]
    availability = [{"recorded_at": "2026-10-11T04:20:00Z", "ok_samples": 4, "total_samples": 5}]
    assert qr.deployment_measurements(deploys, availability, NOW) == {
        "deployment.deploys_30d": 2,
        "deployment.success_rate_30d": 0.5,
        "deployment.median_duration_seconds_30d": 300,
        "deployment.availability_30d": 0.8,
    }


# --- delta, baselines, schema ---------------------------------------------------------------------


def _report_with(**values):
    """A minimal valid report whose checks carry ``values`` (metric -> value)."""
    checks = {name: {"status": "unavailable", "detail": "", "measurements": {}} for name in qr.CHECKS}
    for metric, value in values.items():
        check = checks[metric.split(".")[0]]
        check["status"] = "measured"
        check["measurements"][metric] = value
    return {
        "schema": qr.SCHEMA,
        "generated_at": "2026-10-12T06:37:00Z",
        "commit": "c" * 40,
        "ref": qr.MAIN_REF,
        "run": {"id": None, "url": None, "event": None},
        "tools": {},
        "scope": dict(qr.SCOPE),
        "checks": checks,
        "hotspots": [],
        "previous": None,
        "delta": {},
        "baselines": {},
        "regressions": [],
    }


def test_finalize_reports_change_against_previous_main_only():
    """The delta compares with the newest main report; branch reports are ignored."""
    older = qr.finalize(_report_with(**{"coverage.statements_percent": 88.0, "complexity.max": 30}), [])
    branch = dict(_report_with(**{"coverage.statements_percent": 10.0}), ref="refs/heads/feature")
    current = _report_with(**{"coverage.statements_percent": 89.0, "complexity.max": 32})
    final = qr.finalize(current, [older, branch])
    assert final["delta"]["coverage.statements_percent"] == {
        "previous": 88.0,
        "current": 89.0,
        "change": 1.0,
        "direction": "better",
    }
    assert final["delta"]["complexity.max"]["direction"] == "worse"
    assert final["previous"]["commit"] == "c" * 40


def test_baselines_ratchet_and_are_never_lowered():
    """A worse value is flagged as a regression, and the baseline stays where it was."""
    first = qr.finalize(_report_with(**{"coverage.branches_percent": 75.0, "types.errors": 3}), [])
    second = qr.finalize(_report_with(**{"coverage.branches_percent": 70.0, "types.errors": 1}), [first])
    assert second["baselines"] == {"coverage.branches_percent": 75.0, "types.errors": 1}
    assert second["regressions"] == ["coverage.branches_percent"]
    third = qr.finalize(_report_with(**{"coverage.branches_percent": 76.0}), [first, second])
    # A check that was unavailable keeps its baseline unchanged.
    assert third["baselines"] == {"coverage.branches_percent": 76.0, "types.errors": 1}
    assert third["regressions"] == []


def test_unavailable_or_errored_checks_are_never_compared():
    """Measurements of error/unavailable checks are ignored even if present."""
    previous = qr.finalize(_report_with(**{"types.errors": 0}), [])
    current = _report_with(**{"types.errors": 50})
    current["checks"]["types"]["status"] = "error"
    final = qr.finalize(current, [previous])
    assert final["delta"] == {}
    assert final["baselines"] == {"types.errors": 0}


@pytest.mark.parametrize(
    "mutate",
    [
        lambda r: r.pop("hotspots"),
        lambda r: r.update(extra=1),
        lambda r: r.update(schema="janus.quality-report/2"),
        lambda r: r["checks"].pop("types"),
        lambda r: r["checks"]["types"].update(status="skipped"),
        lambda r: r["checks"]["types"]["measurements"].update({"types.made_up": 1}),
        lambda r: r["checks"]["types"]["measurements"].update({"coverage.branches_percent": 1}),
        lambda r: r["baselines"].update({"nope": 1}),
        lambda r: r.update(hotspots=[{}] * 11),
        # Nested objects are validated strictly too (exact keys and value types).
        lambda r: r["run"].pop("event"),
        lambda r: r["run"].update(id=7),
        lambda r: r["tools"].update(ruff=1),
        lambda r: r["scope"].pop("types"),
        lambda r: r["scope"].update(types=None),
        lambda r: r["checks"].update(types="pass"),
        lambda r: r["checks"]["types"].update(extra=1),
        lambda r: r["checks"]["types"].pop("detail"),
        lambda r: r["checks"]["types"].update(detail=None),
        lambda r: r["checks"]["types"].update(measurements=[]),
        lambda r: r["checks"]["types"]["measurements"].update({"types.errors": "3"}),
        lambda r: r["checks"]["types"]["measurements"].update({"types.errors": True}),
        lambda r: r.update(hotspots=["src/janus/a.py"]),
        lambda r: r.update(hotspots=[{"path": "a.py"}]),
        lambda r: r.update(
            hotspots=[
                {
                    "path": "a.py",
                    "lines": "10",
                    "max_complexity": 1,
                    "worst_function": None,
                    "duplicated_lines": 0,
                    "score": 1.0,
                }
            ]
        ),
        lambda r: r.update(previous={"commit": "c"}),
        lambda r: r.update(previous={"commit": 1, "generated_at": None}),
        lambda r: r["delta"].update({"types.errors": 1}),
        lambda r: r["delta"].update({"types.errors": {"previous": 1, "current": 0, "change": -1}}),
        lambda r: r["delta"].update(
            {"types.errors": {"previous": 1, "current": 0, "change": -1, "direction": "sideways"}}
        ),
        lambda r: r["delta"].update(
            {"types.errors": {"previous": "1", "current": 0, "change": -1, "direction": "better"}}
        ),
        lambda r: r["baselines"].update({"types.errors": None}),
        lambda r: r.update(regressions=["nope"]),
        lambda r: r.update(regressions=[{"types.errors": 1}]),
    ],
)
def test_schema_violations_are_rejected(mutate):
    """The fixed schema rejects missing, extra or unknown fields."""
    report = copy.deepcopy(_report_with(**{"types.errors": 0}))
    mutate(report)
    with pytest.raises(ValueError, match="schema"):
        qr.validate(report)


def test_markdown_lists_commit_tools_scope_measurements_hotspots_and_delta(tmp_path):
    """The job summary carries every element the issue asks for."""
    report, _ = _collect(tmp_path)
    previous = qr.finalize(copy.deepcopy(report), [])
    report["checks"]["coverage"]["measurements"]["coverage.statements_percent"] = 91.0
    markdown = qr.render_markdown(qr.finalize(report, [previous]))
    assert "## Quality report `ffffffffffff`" in markdown
    assert "**FAIL**" in markdown and "| deployment | **UNAVAILABLE** |" in markdown
    assert "`coverage.statements_percent` (%) | 91 | 90.12 | +0.88 (better)" in markdown
    assert "### Top 10 hotspots" in markdown and "Score = max complexity" in markdown
    assert "jscpd 5.4.0" in markdown
    assert "- **complexity:**" in markdown


def test_collector_never_edits_gate_floors(tmp_path):
    """Gate floors in pyproject.toml and .jscpd.json are read, never written."""
    before = [(qr.ROOT / name).read_bytes() for name in ("pyproject.toml", ".jscpd.json")]
    report, _ = _collect(tmp_path)
    qr.finalize(report, [])
    assert [(qr.ROOT / name).read_bytes() for name in ("pyproject.toml", ".jscpd.json")] == before


# --- CLI and history --------------------------------------------------------------------------------


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def clone(tmp_path):
    """A clone of a bare remote with one commit on main."""
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "--quiet", "--bare", "-b", "main", str(remote))
    work = tmp_path / "work"
    _git(tmp_path, "clone", "--quiet", str(remote), str(work))
    (work / "README").write_text("x\n")
    _git(work, "add", "README")
    _git(work, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "--quiet", "-m", "init")
    _git(work, "push", "--quiet", "origin", "HEAD:main")
    return work


def test_publish_appends_main_reports_only(clone, tmp_path, capsys):
    """Main reports enter the quality-history branch; other refs are skipped; finalize reads it back."""
    main_report = tmp_path / "main.json"
    main_report.write_text(json.dumps(qr.finalize(_report_with(**{"types.errors": 2}), [])))
    branch_report = tmp_path / "branch.json"
    branch_report.write_text(json.dumps(dict(_report_with(**{"types.errors": 9}), ref="refs/heads/x")))
    assert qr.main(["publish", str(branch_report), "--repo", str(clone)]) == 0
    assert "not publishing" in capsys.readouterr().out
    assert qr.main(["publish", str(main_report), "--repo", str(clone)]) == 0
    records = history.read_history(clone, "origin", qr.HISTORY_BRANCH, qr.HISTORY_PATH)
    assert [r["checks"]["types"]["measurements"] for r in records] == [{"types.errors": 2}]

    raw = tmp_path / "raw.json"
    raw.write_text(json.dumps(_report_with(**{"types.errors": 1})))
    out, md = tmp_path / "final.json", tmp_path / "summary.md"
    assert qr.main(["finalize", str(raw), "--out", str(out), "--markdown", str(md), "--repo", str(clone)]) == 0
    final = json.loads(out.read_text())
    assert final["delta"]["types.errors"]["direction"] == "better"
    assert final["baselines"] == {"types.errors": 1}


def test_gate_cli_exit_codes(tmp_path):
    """The gate step fails only on fail/error checks."""
    good = tmp_path / "good.json"
    good.write_text(json.dumps(_report_with(**{"types.errors": 0})))
    assert qr.main(["gate", str(good)]) == 0
    bad_report = _report_with(**{"types.errors": 4})
    bad_report["checks"]["types"]["status"] = "fail"
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(bad_report))
    assert qr.main(["gate", str(bad)]) == 1


def test_collect_cli_rejects_unknown_checks(tmp_path):
    """--only names must be known checks."""
    with pytest.raises(SystemExit):
        qr.main(["collect", "--out", str(tmp_path / "r.json"), "--only", "tests,bogus"])


def test_deployment_check_reads_the_ops_metrics_branch(clone):
    """Unavailable without the JAN-269 branch; measured once deploys and samples are on it."""
    ops = qr._load_sibling("ops_metrics")
    missing = qr.check_deployment(clone, "origin", NOW)
    assert missing.status == "unavailable" and "ops-metrics" in missing.detail
    deploy = {"recorded_at": "2026-10-10T00:00:00Z", "outcome": "success", "timing": {"duration_seconds": 240}}
    sample = {"recorded_at": "2026-10-11T04:17:00Z", "ok_samples": 5, "total_samples": 5}
    history.append_and_push(clone, "origin", qr.OPS_BRANCH, ops.DATASETS["deploys"], [deploy], "deploy")
    history.append_and_push(clone, "origin", qr.OPS_BRANCH, ops.DATASETS["availability"], [sample], "probe")
    measured = qr.check_deployment(clone, "origin", NOW)
    assert measured.status == "measured"
    assert measured.measurements == {
        "deployment.deploys_30d": 1,
        "deployment.success_rate_30d": 1.0,
        "deployment.median_duration_seconds_30d": 240,
        "deployment.availability_30d": 1.0,
    }


def test_deployment_check_marks_unreadable_history_as_error(tmp_path):
    """A git failure while reading the history is an error, not a silent zero."""

    def broken_git(repo, *args, check=True, **kwargs):
        if args[0] == "ls-remote":
            return _done(list(args), 0, "0123abcd\trefs/heads/ops-metrics\n")
        raise subprocess.CalledProcessError(128, ["git", *args])

    result = qr.check_deployment(tmp_path, "origin", NOW, git=broken_git)
    assert result.status == "error"
    assert result.measurements == {}


def test_gated_checks_fail_below_their_floors(tmp_path, monkeypatch):
    """Coverage below its floor and duplication above its threshold fail; the floors come from the repo."""
    monkeypatch.setattr(qr, "coverage_floors", lambda: {"statements": 95.0, "branches": 70.0})
    monkeypatch.setattr(qr, "jscpd_threshold", lambda: 0.5)
    report, _ = _collect(tmp_path, only=("tests", "coverage", "duplication"))
    assert report["checks"]["coverage"]["status"] == "fail"
    assert "floor 95.0%" in report["checks"]["coverage"]["detail"]
    assert report["checks"]["duplication"]["status"] == "fail"
    assert [line.split(":")[0] for line in qr.gate(report)] == ["tests", "coverage", "duplication"]


def test_latest_prints_the_newest_main_reports_for_consumers(tmp_path, capsys):
    """`latest` gives the monthly review (JAN-271) the newest main reports, oldest first."""
    records = [dict(_report_with(**{"types.errors": n}), generated_at=f"2026-10-0{n}T06:37:00Z") for n in range(1, 4)]
    records.insert(1, dict(_report_with(**{"types.errors": 99}), ref="refs/heads/feature"))
    path = tmp_path / "history.jsonl"
    path.write_text(history.dump_jsonl(records))
    assert qr.main(["latest", "--history", str(path), "--count", "2"]) == 0
    printed = history.read_jsonl(capsys.readouterr().out)
    assert [r["checks"]["types"]["measurements"]["types.errors"] for r in printed] == [2, 3]
    assert all(qr.validate(r) for r in printed)
    assert qr.main(["latest", "--history", str(tmp_path / "none.jsonl")]) == 0
    assert capsys.readouterr().out == ""
    with pytest.raises(SystemExit):
        qr.main(["latest", "--history", str(path), "--count", "0"])


def test_publish_recomputes_against_the_history_tip(clone, tmp_path, capsys):
    """Overlapping main runs: the later publish is re-based on the tip it lands on.

    Both reports were finalized against an empty history; the second must neither restart the
    baselines nor drop its regression.
    """
    first = qr.finalize(dict(_report_with(**{"types.errors": 1}), commit="a" * 40), [])
    second = qr.finalize(dict(_report_with(**{"types.errors": 5}), commit="b" * 40), [])
    assert second["baselines"] == {"types.errors": 5} and second["previous"] is None  # stale
    for name, report in (("first", first), ("second", second)):
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(report))
        assert qr.main(["publish", str(path), "--repo", str(clone)]) == 0
    assert "recomputed delta and baselines" in capsys.readouterr().out
    records = history.read_history(clone, "origin", qr.HISTORY_BRANCH, qr.HISTORY_PATH)
    assert [r["commit"][0] for r in records] == ["a", "b"]
    published = records[-1]
    assert published["previous"]["commit"] == "a" * 40
    assert published["baselines"] == {"types.errors": 1}
    assert published["regressions"] == ["types.errors"]
    assert published["delta"]["types.errors"]["direction"] == "worse"
    assert qr.validate(published)


def test_publish_rejects_a_report_with_a_malformed_nested_object(clone, tmp_path):
    """Strict validation runs before anything is pushed."""
    report = qr.finalize(_report_with(**{"types.errors": 1}), [])
    report["checks"]["types"]["measurements"]["types.errors"] = "1"
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(report))
    with pytest.raises(ValueError, match="non-numeric"):
        qr.main(["publish", str(path), "--repo", str(clone)])
    assert history.read_history(clone, "origin", qr.HISTORY_BRANCH, qr.HISTORY_PATH) == []


def _ls_remote_git(code: int, stdout: str = "", stderr: str = ""):
    calls = []

    def git(repo, *args, check=True, **kwargs):
        calls.append(args)
        if args[0] == "ls-remote":
            return _done(list(args), code, stdout, stderr)
        raise AssertionError(f"unexpected git {args}")

    return git, calls


@pytest.mark.parametrize(("code", "stdout"), [(2, ""), (0, "")])
def test_missing_history_branch_reads_as_empty(tmp_path, code, stdout):
    """Only a definite "no such ref" (exit 2, or nothing listed) means the history is empty."""
    git, calls = _ls_remote_git(code, stdout)
    assert history.fetch_branch(tmp_path, "origin", qr.HISTORY_BRANCH, git=git) is None
    assert history.read_history(tmp_path, "origin", qr.HISTORY_BRANCH, qr.HISTORY_PATH, git=git) == []
    assert calls[0][-1] == f"refs/heads/{qr.HISTORY_BRANCH}"


@pytest.mark.parametrize("code", [1, 128])
def test_ls_remote_failure_aborts_instead_of_resetting_the_history(tmp_path, code):
    """A network or auth error must not look like a missing branch.

    That would reset the baselines on finalize and start a fresh orphan history on publish.
    """
    git, _ = _ls_remote_git(code, "", "fatal: unable to access remote")
    with pytest.raises(subprocess.CalledProcessError):
        history.read_history(tmp_path, "origin", qr.HISTORY_BRANCH, qr.HISTORY_PATH, git=git)
    with pytest.raises(subprocess.CalledProcessError):
        history.append_and_push(tmp_path, "origin", qr.HISTORY_BRANCH, qr.HISTORY_PATH, [{"n": 1}], "m", git=git)
    # The deployment check reports it as an error, not as "no ops-metrics branch yet".
    assert qr.check_deployment(tmp_path, "origin", NOW, git=git).status == "error"


def test_finalize_cli_aborts_when_the_history_cannot_be_read(tmp_path, monkeypatch):
    """The finalize command never compares against an empty history it could not actually read."""
    git, _ = _ls_remote_git(128, "", "fatal: could not read from remote")
    monkeypatch.setattr(history, "run_git", git)
    monkeypatch.setattr(history.fetch_branch, "__defaults__", (git,))
    monkeypatch.setattr(history.read_history, "__defaults__", (git,))
    raw = tmp_path / "raw.json"
    raw.write_text(json.dumps(_report_with(**{"types.errors": 1})))
    out = tmp_path / "final.json"
    with pytest.raises(subprocess.CalledProcessError):
        qr.main(["finalize", str(raw), "--out", str(out), "--repo", str(tmp_path)])
    assert not out.exists()
