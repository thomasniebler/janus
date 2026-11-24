"""Weekly quality report with a durable history (JAN-270).

One JSON report with a fixed schema (:data:`SCHEMA`), consolidating tests, coverage, complexity,
module size, duplication, type findings, security findings and deployment metrics, plus a job
summary with the commit, the tool versions, the scope, the measurements, the top-10 hotspots and
the change against the previous ``main`` report. ``.github/workflows/quality-report.yml`` only
calls this script; ``task quality:report`` runs the same locally. Commands::

    collect  --out raw.json [--work-dir DIR] [--only tests,complexity,...]
        Run the checks and write the raw report. A check whose tool is missing is
        ``unavailable``; one whose tool crashed or produced no result is ``error``.
    finalize raw.json --out report.json [--markdown summary.md]
             [--history FILE | --repo . --remote origin --branch quality-history]
        Add the change against the previous main report and the ratcheted baselines.
    publish  report.json [--repo . --remote origin --branch quality-history]
        Append a main report to the durable history (scripts/history_branch.py).
    gate     report.json
        Exit 1 when a gated check failed or errored (after the report is saved and published).
    latest   [--count N] [--history FILE | --repo . --remote origin --branch quality-history]
        Print the newest N main reports (default 1) as JSON Lines, oldest first, for consumers
        such as the monthly structural review (JAN-271).

Check status: ``pass`` / ``fail`` (measured against a gate), ``measured`` (report only),
``error`` (the tool ran but gave no usable result) or ``unavailable`` (tool or input missing, or
the check was not selected). Only ``pass``, ``fail`` and ``measured`` checks contribute
measurements, deltas and baselines.

Baselines ratchet: a metric's baseline only ever moves in its better direction, starting from
the first main report that measured it. Nothing here lowers a baseline or edits a gate floor in
``pyproject.toml`` or ``.jscpd.json``; those change only through a reviewed commit. The policy
is in docs/docs/tech-spec/architecture-overview.md#weekly-quality-report.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import platform
import re
import statistics
import subprocess
import sys
import tempfile
import tomllib
import xml.etree.ElementTree as ET  # noqa: N817 - parses our own pytest JUnit file
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
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

SCHEMA = "janus.quality-report/1"
HISTORY_BRANCH = "quality-history"
HISTORY_PATH = "reports/quality-report.jsonl"
OPS_BRANCH = "ops-metrics"
DEPLOYMENT_WINDOW_DAYS = 30
MAIN_REF = "refs/heads/main"
JSCPD = "jscpd@5.4.0"
TOOL_TIMEOUT_SECONDS = 1800
TOP_HOTSPOTS = 10
COMPLEXITY_LIMIT = 15
BACKEND_MODULE_BUDGET = 500
FRONTEND_MODULE_BUDGET = 400
STATUSES = ("pass", "fail", "measured", "error", "unavailable")
#: pytest exit codes of a run that completed: 0 all passed, 1 some tests failed.
PYTEST_COMPLETED = (0, 1)
CHECKS = ("tests", "coverage", "complexity", "size", "duplication", "types", "semgrep", "dependencies", "deployment")

#: Every metric the report may carry: unit and whether a higher value is better. Fixed by the
#: schema; a metric outside this table is a schema violation.
METRICS: dict[str, tuple[str, bool]] = {
    "tests.total": ("count", True),
    "tests.failed": ("count", False),
    "tests.skipped": ("count", False),
    "tests.duration_seconds": ("s", False),
    "coverage.statements_percent": ("%", True),
    "coverage.branches_percent": ("%", True),
    "complexity.functions": ("count", True),
    "complexity.over_limit": ("count", False),
    "complexity.max": ("cc", False),
    "complexity.mean": ("cc", False),
    "size.backend_modules": ("count", True),
    "size.backend_over_budget": ("count", False),
    "size.frontend_modules": ("count", True),
    "size.frontend_over_budget": ("count", False),
    "size.largest_lines": ("lines", False),
    "duplication.production_percent": ("%", False),
    "duplication.production_clones": ("count", False),
    "duplication.tests_percent": ("%", False),
    "duplication.tests_clones": ("count", False),
    "types.errors": ("count", False),
    "semgrep.blocking": ("count", False),
    "semgrep.reported": ("count", False),
    "dependencies.blocking": ("count", False),
    "dependencies.reported": ("count", False),
    "deployment.deploys_30d": ("count", True),
    "deployment.success_rate_30d": ("ratio", True),
    "deployment.median_duration_seconds_30d": ("s", False),
    "deployment.availability_30d": ("ratio", True),
}

#: What each check looks at, recorded in every report so a change of scope is visible.
SCOPE = {
    "tests": "uv run pytest over tests/ (backend suite)",
    "coverage": "src/janus, statements and branches; floors in [tool.janus.coverage]",
    "complexity": f"src/janus, Ruff C901 with --ignore-noqa, every function; limit {COMPLEXITY_LIMIT}",
    "size": (
        f"src/janus/**/*.py (budget {BACKEND_MODULE_BUDGET} lines) and frontend/src/**/*.ts(x) without tests, "
        f"stories, fixtures and generated code (budget {FRONTEND_MODULE_BUDGET})"
    ),
    "duplication": f"{JSCPD}, .jscpd.json (production, gated by its threshold) and .jscpd.tests.json",
    "types": "mypy on the files in [tool.mypy]",
    "semgrep": "scripts/semgrep_scan.py (pinned Semgrep and rules), open findings after exceptions",
    "dependencies": "scripts/dependency_audit.py (pip-audit, npm audit), open findings after exceptions",
    "deployment": f"last 30 days of the {OPS_BRANCH} history branch (deploys and sampled availability)",
}


# --- data model ---------------------------------------------------------------------------------


@dataclass
class CheckResult:
    """The outcome of one check."""

    status: str
    detail: str = ""
    measurements: dict[str, float | int | None] = field(default_factory=dict)

    def as_dict(self) -> dict:
        """The schema form."""
        return {"status": self.status, "detail": self.detail, "measurements": self.measurements}


Runner = Callable[[list[str]], subprocess.CompletedProcess]


def run_command(command: list[str]) -> subprocess.CompletedProcess:
    """Run a fixed tool command in the repository root; never raises on a non-zero exit."""
    return subprocess.run(  # noqa: S603 - argument lists built in this module
        command, cwd=ROOT, capture_output=True, text=True, check=False, timeout=TOOL_TIMEOUT_SECONDS
    )


def _run(runner: Runner, command: list[str]) -> subprocess.CompletedProcess | None:
    """Run ``command``; ``None`` when the tool itself is not installed."""
    try:
        return runner(command)
    except FileNotFoundError:
        return None
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(command, 124, "", f"timed out after {exc.timeout} s")


def _tail(text: str, lines: int = 3) -> str:
    return " | ".join(line.strip() for line in text.strip().splitlines()[-lines:])


# --- checks -----------------------------------------------------------------------------------------


def parse_junit(text: str) -> dict:
    """Totals from a pytest JUnit XML report."""
    root = ET.fromstring(text)  # noqa: S314 - our own pytest output
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    total = sum(int(s.get("tests", 0)) for s in suites)
    failed = sum(int(s.get("failures", 0)) + int(s.get("errors", 0)) for s in suites)
    skipped = sum(int(s.get("skipped", 0)) for s in suites)
    duration = sum(float(s.get("time", 0)) for s in suites)
    return {
        "tests.total": total,
        "tests.failed": failed,
        "tests.skipped": skipped,
        "tests.duration_seconds": round(duration, 1),
    }


def coverage_floors(pyproject: Path = ROOT / "pyproject.toml") -> dict[str, float]:
    """The gated coverage floors (read only)."""
    return tomllib.loads(pyproject.read_text(encoding="utf-8"))["tool"]["janus"]["coverage"]


def check_tests_and_coverage(runner: Runner, work: Path) -> tuple[CheckResult, CheckResult]:
    """One pytest run with coverage gives both the test totals and the coverage numbers."""
    junit, cov = work / "junit.xml", work / "coverage.json"
    result = _run(
        runner,
        [
            "uv",
            "run",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "--cov=src/janus",
            "--cov-branch",
            f"--cov-report=json:{cov}",
            f"--junitxml={junit}",
        ],
    )
    if result is None:
        missing = CheckResult("unavailable", "uv is not installed")
        return missing, missing
    if not junit.is_file():
        failed = CheckResult(
            "error", f"pytest exit {result.returncode}, no JUnit report: {_tail(result.stdout + result.stderr)}"
        )
        return failed, CheckResult("error", "no coverage without a test run")
    if result.returncode not in PYTEST_COMPLETED:
        # Interrupted (2), internal error (3), usage error (4), nothing collected (5) or a timeout:
        # a JUnit file may exist but describes a partial run, so neither half is a measurement.
        failed = CheckResult(
            "error", f"pytest exit {result.returncode}, run did not complete: {_tail(result.stdout + result.stderr)}"
        )
        return failed, CheckResult("error", "no coverage without a complete test run")
    totals = parse_junit(junit.read_text(encoding="utf-8"))
    tests = CheckResult(
        "pass" if totals["tests.failed"] == 0 else "fail",
        f"{totals['tests.total']} tests, {totals['tests.failed']} failed, {totals['tests.skipped']} skipped",
        totals,
    )
    if not cov.is_file():
        return tests, CheckResult("error", "pytest wrote no coverage.json")
    report = json.loads(cov.read_text(encoding="utf-8"))["totals"]
    measured = {
        "coverage.statements_percent": round(report["percent_statements_covered"], 2),
        "coverage.branches_percent": round(report["percent_branches_covered"], 2),
    }
    floors = coverage_floors()
    below = [name for name in ("statements", "branches") if measured[f"coverage.{name}_percent"] < floors[name]]
    detail = (
        f"statements {measured['coverage.statements_percent']}% (floor {floors['statements']}%), "
        f"branches {measured['coverage.branches_percent']}% (floor {floors['branches']}%)"
    )
    return tests, CheckResult("fail" if below else "pass", detail, measured)


_COMPLEXITY = re.compile(r"^`(?P<name>.+)` is too complex \((?P<value>\d+) > \d+\)")


def parse_ruff_complexity(findings: list[dict], root: Path = ROOT) -> list[dict]:
    """``{path, line, name, complexity}`` for every function from Ruff's C901 JSON output."""
    functions = []
    for finding in findings:
        match = _COMPLEXITY.match(finding.get("message", ""))
        if not match:
            continue
        path = Path(finding["filename"])
        functions.append(
            {
                "path": str(path.relative_to(root)) if path.is_absolute() and path.is_relative_to(root) else str(path),
                "line": finding["location"]["row"],
                "name": match["name"],
                "complexity": int(match["value"]),
            }
        )
    return functions


def check_complexity(runner: Runner) -> tuple[CheckResult, list[dict]]:
    """Cyclomatic complexity of every backend function (``max-complexity = 0`` reports them all)."""
    result = _run(
        runner,
        [
            "uv",
            "run",
            "ruff",
            "check",
            "src/janus",
            "--select",
            "C901",
            "--ignore-noqa",
            "--config",
            "lint.mccabe.max-complexity = 0",
            "--output-format",
            "json",
            "--exit-zero",
        ],
    )
    if result is None:
        return CheckResult("unavailable", "uv is not installed"), []
    try:
        functions = parse_ruff_complexity(json.loads(result.stdout))
    except (json.JSONDecodeError, KeyError, TypeError):
        return CheckResult("error", f"ruff exit {result.returncode}: {_tail(result.stderr)}"), []
    if not functions:
        return CheckResult("error", "ruff reported no functions"), []
    values = [f["complexity"] for f in functions]
    over = sum(value > COMPLEXITY_LIMIT for value in values)
    measured = {
        "complexity.functions": len(values),
        "complexity.over_limit": over,
        "complexity.max": max(values),
        "complexity.mean": round(statistics.fmean(values), 2),
    }
    return CheckResult("measured", f"{over} of {len(values)} functions above {COMPLEXITY_LIMIT}", measured), functions


_FRONTEND_EXCLUDED = re.compile(r"(\.test\.tsx?|\.stories\.tsx|Fixtures\.ts|\.d\.ts)$|(^|/)(test|generated)/")


def module_sizes(root: Path = ROOT) -> list[dict]:
    """Non-blank line counts of backend and frontend production modules."""
    modules = []
    sources = [
        ("backend", root / "src" / "janus", ("*.py",), BACKEND_MODULE_BUDGET),
        ("frontend", root / "frontend" / "src", ("*.ts", "*.tsx"), FRONTEND_MODULE_BUDGET),
    ]
    for side, base, patterns, budget in sources:
        if not base.is_dir():
            continue
        for pattern in patterns:
            for path in base.rglob(pattern):
                relative = path.relative_to(root).as_posix()
                if "__pycache__" in relative or "node_modules" in relative:
                    continue
                if side == "frontend" and _FRONTEND_EXCLUDED.search(path.relative_to(base).as_posix()):
                    continue
                text = path.read_text(encoding="utf-8", errors="replace")
                lines = sum(1 for line in text.splitlines() if line.strip())
                modules.append({"path": relative, "side": side, "lines": lines, "budget": budget})
    return sorted(modules, key=lambda m: m["path"])


def check_size(root: Path = ROOT) -> tuple[CheckResult, list[dict]]:
    """Module counts and how many modules exceed the size budget, per side."""
    modules = module_sizes(root)
    if not modules:
        return CheckResult("unavailable", "no source modules found"), []
    measured: dict[str, float | int | None] = {}
    for side in ("backend", "frontend"):
        mine = [m for m in modules if m["side"] == side]
        measured[f"size.{side}_modules"] = len(mine)
        measured[f"size.{side}_over_budget"] = sum(m["lines"] > m["budget"] for m in mine)
    measured["size.largest_lines"] = max(m["lines"] for m in modules)
    detail = (
        f"{measured['size.backend_over_budget']} backend and {measured['size.frontend_over_budget']} "
        f"frontend modules over budget"
    )
    return CheckResult("measured", detail, measured), modules


def _jscpd(runner: Runner, config: str, out: Path) -> tuple[dict | None, str]:
    result = _run(
        runner,
        [
            "npx",
            "--yes",
            JSCPD,
            "--config",
            config,
            "--reporters",
            "json,html",
            "--output",
            str(out),
            "--silent",
            "--threshold",
            "100",
        ],
    )
    if result is None:
        return None, "npx is not installed"
    report = out / "jscpd-report.json"
    if not report.is_file():
        return None, f"jscpd exit {result.returncode}: {_tail(result.stdout + result.stderr)}"
    return json.loads(report.read_text(encoding="utf-8")), ""


def jscpd_threshold(config: Path = ROOT / ".jscpd.json") -> float:
    """The gated duplication threshold (read only)."""
    return float(json.loads(config.read_text(encoding="utf-8"))["threshold"])


def duplicated_lines_per_file(report: dict) -> dict[str, int]:
    """Lines inside a clone, per file, from a jscpd JSON report."""
    per_file: dict[str, int] = {}
    for duplicate in report.get("duplicates", []):
        for side in ("firstFile", "secondFile"):
            name = duplicate.get(side, {}).get("name")
            if name:
                per_file[name] = per_file.get(name, 0) + int(duplicate.get("lines", 0))
    return per_file


def check_duplication(runner: Runner, work: Path) -> tuple[CheckResult, dict[str, int]]:
    """Run jscpd over production code (gated) and test code (report only)."""
    production, error = _jscpd(runner, ".jscpd.json", work / "jscpd")
    if production is None:
        status = "unavailable" if "not installed" in error else "error"
        return CheckResult(status, error), {}
    tests, tests_error = _jscpd(runner, ".jscpd.tests.json", work / "jscpd" / "tests")
    total = production["statistics"]["total"]
    measured: dict[str, float | int | None] = {
        "duplication.production_percent": round(float(total["percentage"]), 2),
        "duplication.production_clones": int(total["clones"]),
        "duplication.tests_percent": round(float(tests["statistics"]["total"]["percentage"]), 2) if tests else None,
        "duplication.tests_clones": int(tests["statistics"]["total"]["clones"]) if tests else None,
    }
    threshold = jscpd_threshold()
    detail = f"production {measured['duplication.production_percent']}% (threshold {threshold}%)"
    if tests is None:
        detail += f"; test code not measured: {tests_error}"
    status = "fail" if measured["duplication.production_percent"] > threshold else "pass"
    return CheckResult(status, detail, measured), duplicated_lines_per_file(production)


_MYPY_FOUND = re.compile(r"Found (\d+) errors? in \d+ files?")


def check_types(runner: Runner) -> CheckResult:
    """Run mypy on its configured scope."""
    result = _run(runner, ["uv", "run", "mypy"])
    if result is None:
        return CheckResult("unavailable", "uv is not installed")
    output = result.stdout + "\n" + result.stderr
    success = re.search(r"^Success: no issues found.*$", output, re.MULTILINE)
    if success:
        return CheckResult("pass", success[0].strip(), {"types.errors": 0})
    found = _MYPY_FOUND.search(output)
    if found:
        line = output[found.start() :].splitlines()[0].strip()
        return CheckResult("fail", line, {"types.errors": int(found[1])})
    return CheckResult("error", f"mypy exit {result.returncode}: {_tail(output)}")


def _count_open(findings: list[dict]) -> dict[str, int]:
    open_findings = [f for f in findings if not f.get("excepted")]
    blocking = sum(bool(f.get("blocking")) for f in open_findings)
    return {"blocking": blocking, "reported": len(open_findings) - blocking}


def check_semgrep(runner: Runner, work: Path) -> CheckResult:
    """Open Semgrep findings (exceptions applied the same way as the CI gate)."""
    raw = work / "semgrep.json"
    result = _run(
        runner,
        [
            "uv",
            "run",
            "--no-project",
            "python",
            "scripts/semgrep_scan.py",
            "--json",
            str(raw),
            "--sarif",
            str(work / "semgrep.sarif"),
        ],
    )
    if result is None:
        return CheckResult("unavailable", "uv is not installed")
    if result.returncode == 2 or not raw.is_file():
        return CheckResult("error", f"semgrep could not run: {_tail(result.stdout + result.stderr)}")
    scan = _load_sibling("semgrep_scan")
    report = json.loads(raw.read_text(encoding="utf-8"))
    findings = scan.parse_results(report)
    # Same verdict as the CI gate: blocking findings, scan errors (e.g. rule timeouts) and stale or
    # expired exceptions all fail it.
    problems = scan.scan_errors(report) + scan.apply_exceptions(
        findings, scan.load_exceptions(), datetime.now(UTC).date()
    )
    counts = _count_open([{"excepted": f.excepted, "blocking": f.blocking} for f in findings])
    status = "fail" if counts["blocking"] or problems else "pass"
    detail = f"{counts['blocking']} blocking, {counts['reported']} reported" + (
        f"; {len(problems)} scan or exception problem(s): {problems[0][:120]}" if problems else ""
    )
    return CheckResult(status, detail, {"semgrep.blocking": counts["blocking"], "semgrep.reported": counts["reported"]})


def check_dependencies(runner: Runner, work: Path) -> CheckResult:
    """Open dependency-audit findings."""
    raw = work / "dependency-audit.json"
    result = _run(runner, ["uv", "run", "--no-project", "python", "scripts/dependency_audit.py", "--json", str(raw)])
    if result is None:
        return CheckResult("unavailable", "uv is not installed")
    if result.returncode == 2 or not raw.is_file():
        return CheckResult("error", f"audit could not run: {_tail(result.stdout + result.stderr)}")
    counts = _count_open(json.loads(raw.read_text(encoding="utf-8")))
    return CheckResult(
        "fail" if counts["blocking"] else "pass",
        f"{counts['blocking']} blocking, {counts['reported']} reported",
        {"dependencies.blocking": counts["blocking"], "dependencies.reported": counts["reported"]},
    )


def deployment_measurements(deploys: list[dict], availability: list[dict], now: datetime) -> dict:
    """30-day deploy and availability figures, computed by ``scripts/ops_metrics.py`` (JAN-269)."""
    ops = _load_sibling("ops_metrics")
    deployed = ops.deploy_summary(deploys, DEPLOYMENT_WINDOW_DAYS, now)
    sampled = ops.availability_summary(availability, DEPLOYMENT_WINDOW_DAYS, now)
    return {
        "deployment.deploys_30d": deployed["deploys"],
        "deployment.success_rate_30d": (
            round(deployed["successes"] / deployed["deploys"], 4) if deployed["deploys"] else None
        ),
        "deployment.median_duration_seconds_30d": deployed["median_duration_seconds"],
        "deployment.availability_30d": sampled["availability"],
    }


def check_deployment(repo: Path, remote: str, now: datetime, git=None) -> CheckResult:
    """Read the ops-metrics history branch; unavailable until it exists."""
    ops = _load_sibling("ops_metrics")
    kwargs = {"git": git} if git else {}
    try:
        if history.fetch_branch(repo, remote, OPS_BRANCH, **kwargs) is None:
            return CheckResult("unavailable", f"no {OPS_BRANCH} branch on {remote}")
        deploys = history.read_history(repo, remote, OPS_BRANCH, ops.DATASETS["deploys"], **kwargs)
        availability = history.read_history(repo, remote, OPS_BRANCH, ops.DATASETS["availability"], **kwargs)
        measured = deployment_measurements(deploys, availability, now)
    except (subprocess.CalledProcessError, ValueError, KeyError, TypeError, OSError) as exc:
        return CheckResult("error", f"could not read {OPS_BRANCH}: {type(exc).__name__}")
    detail = (
        f"{measured['deployment.deploys_30d']} deploys in the last {DEPLOYMENT_WINDOW_DAYS} days; "
        f"{len(deploys)} deploys and {len(availability)} probe runs on record"
    )
    return CheckResult("measured", detail, measured)


# --- hotspots -------------------------------------------------------------------------------------


def hotspots(
    functions: list[dict], modules: list[dict], duplicated: dict[str, int], top: int = TOP_HOTSPOTS
) -> list[dict]:
    """The ``top`` files by hotspot score.

    score = max function complexity / 15 + non-blank lines / module budget + duplicated lines / 50.
    Each term is 1.0 at its limit, so a file scores above 1 on any single exceeded limit.
    """
    per_file: dict[str, dict] = {}
    for module in modules:
        per_file[module["path"]] = {
            "path": module["path"],
            "lines": module["lines"],
            "budget": module["budget"],
            "max_complexity": 0,
            "worst_function": None,
            "duplicated_lines": 0,
        }
    for function in functions:
        entry = per_file.setdefault(
            function["path"],
            {
                "path": function["path"],
                "lines": 0,
                "budget": BACKEND_MODULE_BUDGET,
                "max_complexity": 0,
                "worst_function": None,
                "duplicated_lines": 0,
            },
        )
        if function["complexity"] > entry["max_complexity"]:
            entry["max_complexity"] = function["complexity"]
            entry["worst_function"] = f"{function['name']}:{function['line']}"
    for path, lines in duplicated.items():
        if path in per_file:
            per_file[path]["duplicated_lines"] = lines
    for entry in per_file.values():
        entry["score"] = round(
            entry["max_complexity"] / COMPLEXITY_LIMIT
            + entry["lines"] / entry["budget"]
            + entry["duplicated_lines"] / 50,
            2,
        )
        del entry["budget"]
    ranked = sorted(per_file.values(), key=lambda e: (-e["score"], e["path"]))
    return ranked[:top]


# --- collect --------------------------------------------------------------------------------------


def tool_versions(runner: Runner) -> dict[str, str]:
    """Versions of every tool the checks use; pinned ones come from their pins."""
    scan = _load_sibling("semgrep_scan")
    audit = _load_sibling("dependency_audit")
    versions = {
        "python": platform.python_version(),
        "jscpd": JSCPD.split("@", 1)[1],
        "semgrep": scan.SEMGREP.split("==", 1)[1],
        "pip-audit": audit.PIP_AUDIT.split("==", 1)[1],
    }
    commands = {
        "uv": ["uv", "--version"],
        "ruff": ["uv", "run", "ruff", "--version"],
        "mypy": ["uv", "run", "mypy", "--version"],
        "pytest": ["uv", "run", "pytest", "--version"],
        "coverage": ["uv", "run", "coverage", "--version"],
        "node": ["node", "--version"],
        "npm": ["npm", "--version"],
    }
    for name, command in commands.items():
        result = _run(runner, command)
        output = (result.stdout or result.stderr).strip() if result else ""
        match = re.search(r"\d+\.\d+(\.\d+)?", output) if result and result.returncode == 0 else None
        versions[name] = match[0] if match else "unavailable"
    return dict(sorted(versions.items()))


def git_commit(runner: Runner) -> str | None:
    """The checked-out commit."""
    result = _run(runner, ["git", "rev-parse", "HEAD"])
    return result.stdout.strip() if result and result.returncode == 0 else None


def collect(
    *,
    only: set[str],
    work: Path,
    runner: Runner = run_command,
    now: datetime | None = None,
    env: dict[str, str] | None = None,
    repo: Path = ROOT,
    remote: str = "origin",
) -> dict:
    """Run the selected checks; unselected ones are recorded as ``unavailable``."""
    env = os.environ if env is None else env
    now = now or datetime.now(UTC)
    work.mkdir(parents=True, exist_ok=True)
    checks: dict[str, CheckResult] = {}
    functions: list[dict] = []
    modules: list[dict] = []
    duplicated: dict[str, int] = {}
    if only & {"tests", "coverage"}:
        checks["tests"], checks["coverage"] = check_tests_and_coverage(runner, work)
    if "complexity" in only:
        checks["complexity"], functions = check_complexity(runner)
    if "size" in only:
        checks["size"], modules = check_size()
    if "duplication" in only:
        checks["duplication"], duplicated = check_duplication(runner, work)
    if "types" in only:
        checks["types"] = check_types(runner)
    if "semgrep" in only:
        checks["semgrep"] = check_semgrep(runner, work)
    if "dependencies" in only:
        checks["dependencies"] = check_dependencies(runner, work)
    if "deployment" in only:
        checks["deployment"] = check_deployment(repo, remote, now)
    for name in CHECKS:
        checks.setdefault(name, CheckResult("unavailable", "not selected for this run"))
        if name not in only and checks[name].status != "unavailable":
            # tests and coverage run together; keep the unselected half out of the report.
            checks[name] = CheckResult("unavailable", "not selected for this run")
    server, repository, run_id = env.get("GITHUB_SERVER_URL"), env.get("GITHUB_REPOSITORY"), env.get("GITHUB_RUN_ID")
    return {
        "schema": SCHEMA,
        "generated_at": now.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "commit": env.get("GITHUB_SHA") or git_commit(runner),
        "ref": env.get("GITHUB_REF") or None,
        "run": {
            "id": run_id,
            "url": f"{server}/{repository}/actions/runs/{run_id}" if server and repository and run_id else None,
            "event": env.get("GITHUB_EVENT_NAME") or None,
        },
        "tools": tool_versions(runner),
        "scope": dict(SCOPE),
        "checks": {name: checks[name].as_dict() for name in CHECKS},
        "hotspots": hotspots(functions, modules, duplicated),
        "previous": None,
        "delta": {},
        "baselines": {},
        "regressions": [],
    }


# --- delta, baselines, schema ---------------------------------------------------------------------


def measurements(report: dict) -> dict[str, float | int]:
    """The usable measurements of a report (from pass, fail and measured checks, non-null only)."""
    values = {}
    for check in report.get("checks", {}).values():
        if check.get("status") in ("pass", "fail", "measured"):
            values.update({k: v for k, v in check.get("measurements", {}).items() if v is not None})
    return values


def _better(metric: str, a: float, b: float) -> float:
    return max(a, b) if METRICS[metric][1] else min(a, b)


def previous_main_report(records: list[dict]) -> dict | None:
    """The newest main report of this schema in the history."""
    mains = latest_main_reports(records)
    return mains[-1] if mains else None


def finalize(report: dict, records: list[dict]) -> dict:
    """Add the change against the previous main report and the ratcheted baselines."""
    previous = previous_main_report(records)
    current = measurements(report)
    before = measurements(previous) if previous else {}
    delta = {}
    for metric, value in current.items():
        if metric not in before:
            continue
        change = round(value - before[metric], 4)
        better = _better(metric, value, before[metric])
        direction = "same" if change == 0 else ("better" if better == value else "worse")
        delta[metric] = {"previous": before[metric], "current": value, "change": change, "direction": direction}
    old_baselines = dict(previous.get("baselines", {})) if previous else {}
    baselines = dict(old_baselines)
    regressions = []
    for metric, value in current.items():
        if metric in old_baselines:
            if _better(metric, value, old_baselines[metric]) != value and value != old_baselines[metric]:
                regressions.append(metric)
            baselines[metric] = _better(metric, value, old_baselines[metric])
        else:
            baselines[metric] = value
    finalized = dict(report)
    finalized["previous"] = (
        {"commit": previous.get("commit"), "generated_at": previous.get("generated_at")} if previous else None
    )
    finalized["delta"] = dict(sorted(delta.items()))
    finalized["baselines"] = dict(sorted(baselines.items()))
    finalized["regressions"] = sorted(regressions)
    return validate(finalized)


_TOP_LEVEL = {
    "schema": str,
    "generated_at": str,
    "commit": (str, type(None)),
    "ref": (str, type(None)),
    "run": dict,
    "tools": dict,
    "scope": dict,
    "checks": dict,
    "hotspots": list,
    "previous": (dict, type(None)),
    "delta": dict,
    "baselines": dict,
    "regressions": list,
}


_NUMBER = (int, float)
_OPTIONAL_STR = (str, type(None))
_DIRECTIONS = ("better", "worse", "same")
_RUN_KEYS = {"id", "url", "event"}
_CHECK_KEYS = {"status", "detail", "measurements"}
_PREVIOUS_KEYS = {"commit", "generated_at"}
_DELTA_KEYS = {"previous", "current", "change", "direction"}
_HOTSPOT_FIELDS = {
    "path": str,
    "lines": int,
    "max_complexity": int,
    "worst_function": _OPTIONAL_STR,
    "duplicated_lines": int,
    "score": _NUMBER,
}


def _is(value, kind) -> bool:
    """``isinstance`` that does not let ``bool`` pass for a number."""
    return isinstance(value, kind) and not isinstance(value, bool)


def _exact_keys(where: str, value, keys: set[str], problems: list[str]) -> bool:
    if not isinstance(value, dict):
        problems.append(f"{where} is not an object")
        return False
    if set(value) != keys:
        problems.append(f"{where} keys {sorted(set(value) ^ keys)} differ from the schema")
        return False
    return True


def _typed(where: str, values: dict, kind, problems: list[str]) -> None:
    problems += [f"{where}.{k} has type {type(v).__name__}" for k, v in values.items() if not _is(v, kind)]


def _validate_check(name: str, check, problems: list[str]) -> None:
    if not _exact_keys(f"check {name}", check, _CHECK_KEYS, problems):
        return
    if check["status"] not in STATUSES:
        problems.append(f"check {name} has status {check['status']!r}")
    if not _is(check["detail"], str):
        problems.append(f"check {name} detail is not a string")
    if not isinstance(check["measurements"], dict):
        problems.append(f"check {name} measurements is not an object")
        return
    unknown = [m for m in check["measurements"] if m not in METRICS or not m.startswith(f"{name}.")]
    if unknown:
        problems.append(f"check {name} has unknown metrics {unknown}")
    bad = [m for m, v in check["measurements"].items() if v is not None and not _is(v, _NUMBER)]
    if bad:
        problems.append(f"check {name} has non-numeric measurements {bad}")


def _validate_delta(metric: str, change, problems: list[str]) -> None:
    if not _exact_keys(f"delta {metric}", change, _DELTA_KEYS, problems):
        return
    if any(not _is(change[k], _NUMBER) for k in ("previous", "current", "change")):
        problems.append(f"delta {metric} has non-numeric values")
    if change["direction"] not in _DIRECTIONS:
        problems.append(f"delta {metric} has direction {change['direction']!r}")


def _items(value) -> list:
    """``(key, value)`` pairs of a dict, ``(rank, item)`` pairs of a list, nothing otherwise."""
    if isinstance(value, dict):
        return list(value.items())
    if isinstance(value, list):
        return list(enumerate(value, start=1))
    return []


def _validate_nested(report: dict, problems: list[str]) -> None:
    """Every nested object of the schema: exact keys and value types."""
    if _exact_keys("run", report.get("run"), _RUN_KEYS, problems):
        _typed("run", report["run"], _OPTIONAL_STR, problems)
    if isinstance(report.get("tools"), dict):
        _typed("tools", report["tools"], str, problems)
    if _exact_keys("scope", report.get("scope"), set(CHECKS), problems):
        _typed("scope", report["scope"], str, problems)
    for name, check in _items(report.get("checks")):
        _validate_check(name, check, problems)
    for rank, spot in _items(report.get("hotspots")):
        if _exact_keys(f"hotspot {rank}", spot, set(_HOTSPOT_FIELDS), problems):
            problems += [
                f"hotspot {rank} {k} has a wrong type" for k, t in _HOTSPOT_FIELDS.items() if not _is(spot[k], t)
            ]
    previous = report.get("previous")
    if previous is not None and _exact_keys("previous", previous, _PREVIOUS_KEYS, problems):
        _typed("previous", previous, _OPTIONAL_STR, problems)
    for metric, change in _items(report.get("delta")):
        _validate_delta(metric, change, problems)
    problems += [f"baseline {m} is not a number" for m, v in _items(report.get("baselines")) if not _is(v, _NUMBER)]
    problems += [
        f"regression {m!r} is not a known metric"
        for _, m in _items(report.get("regressions"))
        if not isinstance(m, str) or m not in METRICS
    ]


def validate(report: dict) -> dict:
    """Return ``report`` if it follows :data:`SCHEMA` exactly; raise ``ValueError`` otherwise.

    Strict at every level: exact keys and value types for the top level and every nested object
    (run, scope, checks, hotspots, previous, delta, baselines, regressions), so a malformed report
    can never be appended to the durable history.
    """
    if not isinstance(report, dict):
        raise ValueError("quality report does not follow the schema: not an object")
    problems = []
    if set(report) != set(_TOP_LEVEL):
        problems.append(f"keys {sorted(set(report) ^ set(_TOP_LEVEL))} differ from the schema")
    for key, kind in _TOP_LEVEL.items():
        if key in report and not isinstance(report[key], kind):
            problems.append(f"{key} has type {type(report[key]).__name__}")
    if report.get("schema") != SCHEMA:
        problems.append(f"schema is {report.get('schema')!r}, expected {SCHEMA!r}")
    checks = report.get("checks")
    if isinstance(checks, dict) and set(checks) != set(CHECKS):
        problems.append(f"checks {sorted(set(checks) ^ set(CHECKS))} differ from the schema")
    for section in ("delta", "baselines"):
        values = report.get(section)
        unknown = [m for m in values if m not in METRICS] if isinstance(values, dict) else []
        if unknown:
            problems.append(f"{section} has unknown metrics {unknown}")
    hotspots = report.get("hotspots")
    if isinstance(hotspots, list) and len(hotspots) > TOP_HOTSPOTS:
        problems.append("more than 10 hotspots")
    _validate_nested(report, problems)
    if problems:
        raise ValueError("quality report does not follow the schema: " + "; ".join(problems))
    return report


# --- markdown -----------------------------------------------------------------------------------------


def _fmt(value) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def render_markdown(report: dict) -> str:
    """The job summary."""
    lines = [f"## Quality report `{(report['commit'] or 'unknown')[:12]}`", ""]
    lines.append(
        f"Generated {report['generated_at']} on `{report['ref'] or 'local'}`"
        + (f" ([run]({report['run']['url']}))" if report["run"].get("url") else "")
        + "."
    )
    previous = report.get("previous")
    lines.append(
        f"Compared with the main report of `{(previous['commit'] or '')[:12]}` ({previous['generated_at']})."
        if previous
        else "No previous main report to compare with."
    )
    lines += ["", "### Checks", "", "| Check | Status | Detail |", "| --- | --- | --- |"]
    for name, check in report["checks"].items():
        status = check["status"] if check["status"] in ("pass", "measured") else f"**{check['status'].upper()}**"
        detail = " ".join(check["detail"].split()).replace("|", "\\|")
        lines.append(f"| {name} | {status} | {detail} |")
    lines += [
        "",
        "### Measurements",
        "",
        "| Metric | Value | Previous main | Change | Baseline |",
        "| --- | --- | --- | --- | --- |",
    ]
    values = measurements(report)
    for metric in METRICS:
        if metric not in values:
            continue
        delta = report["delta"].get(metric)
        change = "n/a" if not delta else f"{delta['change']:+g} ({delta['direction']})"
        flag = " (below baseline)" if metric in report["regressions"] else ""
        lines.append(
            f"| `{metric}` ({METRICS[metric][0]}) | {_fmt(values[metric])}{flag} | "
            f"{_fmt(delta['previous']) if delta else 'n/a'} | {change} | {_fmt(report['baselines'].get(metric))} |"
        )
    lines += [
        "",
        "### Top 10 hotspots",
        "",
        "Score = max complexity / 15 + lines / module budget + duplicated lines / 50.",
        "",
        "| # | File | Score | Max complexity | Worst function | Lines | Duplicated lines |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for rank, spot in enumerate(report["hotspots"], start=1):
        lines.append(
            f"| {rank} | `{spot['path']}` | {spot['score']} | {spot['max_complexity']} | "
            f"{spot['worst_function'] or ''} | {spot['lines']} | {spot['duplicated_lines']} |"
        )
    lines += [
        "",
        "### Tool versions",
        "",
        ", ".join(f"{k} {v}" for k, v in report["tools"].items()),
        "",
        "### Scope",
        "",
    ]
    lines += [f"- **{name}:** {text}" for name, text in report["scope"].items()]
    return "\n".join(lines) + "\n"


def latest_main_reports(records: list[dict], count: int = 1) -> list[dict]:
    """The newest ``count`` main reports of this schema, oldest first."""
    mains = [r for r in records if r.get("schema") == SCHEMA and r.get("ref") == MAIN_REF]
    return mains[-count:] if count > 0 else []


def gate(report: dict) -> list[str]:
    """Checks that failed their gate or errored (unavailable checks are reported, not gated)."""
    return [
        f"{name}: {check['status']} ({check['detail']})"
        for name, check in report["checks"].items()
        if check["status"] in ("fail", "error")
    ]


# --- CLI ------------------------------------------------------------------------------------------------


def _records(args) -> list[dict]:
    if args.history:
        path = Path(args.history)
        return history.read_jsonl(path.read_text(encoding="utf-8")) if path.is_file() else []
    return history.read_history(Path(args.repo), args.remote, args.branch, HISTORY_PATH)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    cmd = commands.add_parser("collect")
    cmd.add_argument("--out", required=True)
    cmd.add_argument("--work-dir", default=None)
    cmd.add_argument("--only", default=",".join(CHECKS), help=f"comma-separated subset of {', '.join(CHECKS)}")
    for name in ("finalize", "publish", "latest"):
        cmd = commands.add_parser(name)
        if name != "latest":
            cmd.add_argument("report")
        cmd.add_argument("--repo", default=".")
        cmd.add_argument("--remote", default="origin")
        cmd.add_argument("--branch", default=HISTORY_BRANCH)
        if name == "finalize":
            cmd.add_argument("--out", required=True)
            cmd.add_argument("--markdown")
        if name == "latest":
            cmd.add_argument("--count", type=int, default=1, help="how many of the newest main reports")
        if name != "publish":
            cmd.add_argument("--history", help="local JSON Lines history instead of the history branch")
    cmd = commands.add_parser("gate")
    cmd.add_argument("report")
    return parser


def _read_report(args) -> dict:
    return json.loads(Path(args.report).read_text(encoding="utf-8"))


def _cmd_collect(args, parser) -> int:
    only = {name.strip() for name in args.only.split(",") if name.strip()}
    if only - set(CHECKS):
        parser.error(f"unknown checks: {', '.join(sorted(only - set(CHECKS)))}")
    work = Path(args.work_dir) if args.work_dir else Path(tempfile.mkdtemp(prefix="quality-report-"))
    report = collect(only=only, work=work.resolve())
    Path(args.out).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    for name, check in report["checks"].items():
        print(f"{check['status']:11} {name:13} {check['detail']}")
    return 0


def _cmd_latest(args, parser) -> int:
    if args.count < 1:
        parser.error("--count must be at least 1")
    print(history.dump_jsonl(latest_main_reports(_records(args), args.count)), end="")
    return 0


def _cmd_finalize(args, parser) -> int:
    final = finalize(_read_report(args), _records(args))
    Path(args.out).write_text(json.dumps(final, indent=2) + "\n", encoding="utf-8")
    markdown = render_markdown(final)
    if args.markdown:
        Path(args.markdown).write_text(markdown, encoding="utf-8")
    print(markdown)
    return 0


def _cmd_publish(args, parser) -> int:
    report = validate(_read_report(args))
    if report.get("ref") != MAIN_REF:
        print(f"not publishing: only {MAIN_REF} reports enter the history (ref {report.get('ref')!r})")
        return 0
    published: dict[str, dict] = {}

    def against_tip(records: list[dict]) -> list[dict]:
        # Recompute previous/delta/baselines/regressions against the history tip this append lands
        # on, not the one finalize read: another main run may have published in between.
        published["report"] = finalize(report, records)
        return [published["report"]]

    commit = history.append_and_push(
        Path(args.repo),
        args.remote,
        args.branch,
        HISTORY_PATH,
        against_tip,
        f"quality: {report['generated_at']} {(report['commit'] or '')[:12]}",
    )
    if published["report"]["baselines"] != report["baselines"]:
        print("recomputed delta and baselines against the newer history tip")
    print(f"appended to {args.branch}:{HISTORY_PATH} at {commit}")
    return 0


def _cmd_gate(args, parser) -> int:
    failures = gate(validate(_read_report(args)))
    for failure in failures:
        print(f"FAIL {failure}", file=sys.stderr)
    return 1 if failures else 0


_COMMANDS = {
    "collect": _cmd_collect,
    "finalize": _cmd_finalize,
    "publish": _cmd_publish,
    "latest": _cmd_latest,
    "gate": _cmd_gate,
}


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and run one command."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    return _COMMANDS[args.command](args, parser)


if __name__ == "__main__":
    sys.exit(main())
