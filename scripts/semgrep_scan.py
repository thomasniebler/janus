"""Semgrep security scan for the Python backend and the TypeScript frontend (JAN-267).

Reproducible by construction: Semgrep itself is pinned (:data:`SEMGREP`), and the
community rules come from the ``semgrep/semgrep-rules`` repository at a pinned commit
(:data:`RULES_COMMIT`), checked out into ``.cache/semgrep-rules`` on first use, never
from the registry, whose packs change underneath you. Only the security-relevant rule
directories in :data:`RULE_DIRS` are used, plus the Janus-specific rules in ``.semgrep/``
(routes without an auth dependency, unscoped lookups of user-owned rows).

INFO-severity rules are not run. An ERROR finding blocks; a WARNING is reported only.
Accepted findings are ``[[tool.janus.semgrep.exceptions]]`` entries in pyproject.toml with
the ``rule`` (the last part of its id), the ``path``, an optional ``line`` text to tell
several findings in one file apart, a ``reason``, and an ``expires`` date. An expired
entry, or one that no longer matches a finding, fails the scan.

Run with ``task security:semgrep``; CI runs it on pull requests and weekly (semgrep.yml),
uploading SARIF and JSON reports. It never probes a running Janus.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEMGREP = "semgrep==1.179.0"
RULES_REPO = "https://github.com/semgrep/semgrep-rules"
RULES_COMMIT = "a84ff9cc2453ca91d581380de4b8b3f272f6f4be"
RULES_CHECKOUT = ROOT / ".cache" / "semgrep-rules"
#: Community rule directories, chosen for auth, injection, secrets, and unsafe calls.
RULE_DIRS = [
    "python/lang/security",
    "python/fastapi",
    "python/jwt",
    "python/sqlalchemy",
    "python/requests",
    "python/cryptography",
    "python/jinja2",
    "javascript/browser",
    "javascript/lang/security",
    "javascript/jsonwebtoken",
    "javascript/react",
    "typescript/react",
    "typescript/lang",
    "generic/secrets",
]
LOCAL_RULES = ROOT / ".semgrep"
#: The Janus rules and their annotated fixture (``# ruleid:`` must match, ``# ok:`` must not).
LOCAL_RULE_TESTS = (LOCAL_RULES / "janus.yml", LOCAL_RULES / "janus.py")
TARGETS = ["src", "frontend/src", "scripts"]
BLOCKING_SEVERITY = "ERROR"


@dataclass
class Finding:
    """One Semgrep result."""

    rule: str
    path: str
    line: int
    code: str
    severity: str
    message: str
    excepted: bool = False

    @property
    def blocking(self) -> bool:
        """Only ERROR findings block."""
        return self.severity == BLOCKING_SEVERITY


@dataclass(frozen=True)
class ScanException:
    """An accepted finding: ``rule`` and ``path`` (and ``line`` text, if given) must match."""

    rule: str
    path: str
    reason: str
    expires: datetime.date
    line: str | None = None

    def matches(self, finding: Finding) -> bool:
        """Whether this exception covers ``finding``."""
        if finding.rule != self.rule or finding.path != self.path:
            return False
        return self.line is None or self.line in finding.code


class ScanError(RuntimeError):
    """Semgrep could not produce a usable result."""


def load_exceptions(pyproject: Path = ROOT / "pyproject.toml") -> list[ScanException]:
    """Read the accepted findings from pyproject.toml."""
    config = tomllib.loads(pyproject.read_text())
    entries = config.get("tool", {}).get("janus", {}).get("semgrep", {}).get("exceptions", [])
    exceptions = []
    for entry in entries:
        missing = {"rule", "path", "reason", "expires"} - entry.keys()
        if missing:
            raise ValueError(f"semgrep exception {entry} is missing {sorted(missing)}")
        if not isinstance(entry["expires"], datetime.date):
            raise ValueError(f"semgrep exception {entry['rule']} {entry['path']}: expires must be a TOML date")
        exceptions.append(
            ScanException(entry["rule"], entry["path"], entry["reason"], entry["expires"], entry.get("line"))
        )
    return exceptions


def _git(*args: str, cwd: Path) -> None:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise ScanError(f"git {' '.join(args)} failed: {result.stderr.strip()}")


def ensure_rules(checkout: Path = RULES_CHECKOUT) -> Path:
    """Check out the pinned community rules (once) and return the checkout."""
    head = checkout / ".git" / "HEAD"
    if head.exists():
        current = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=checkout, capture_output=True, text=True, check=False
        ).stdout.strip()
        if current == RULES_COMMIT:
            return checkout
    checkout.mkdir(parents=True, exist_ok=True)
    if not head.exists():
        _git("init", "--quiet", cwd=checkout)
    _git("fetch", "--quiet", "--depth", "1", RULES_REPO, RULES_COMMIT, cwd=checkout)
    _git("checkout", "--quiet", "--force", "FETCH_HEAD", cwd=checkout)
    return checkout


def parse_results(report: dict) -> list[Finding]:
    """Turn ``semgrep --json`` output into findings."""
    findings = []
    for result in report.get("results", []):
        extra = result.get("extra", {})
        findings.append(
            Finding(
                rule=result["check_id"].rsplit(".", 1)[-1],
                path=result["path"],
                line=result["start"]["line"],
                code=_source_line(result["path"], result["start"]["line"]),
                severity=extra.get("severity", "UNKNOWN"),
                message=" ".join(extra.get("message", "").split()),
            )
        )
    return findings


def _source_line(path: str, line: int) -> str:
    # Semgrep hides matched code ("requires login") when rules come from a checkout, so read it here.
    try:
        return (ROOT / path).read_text().splitlines()[line - 1].strip()
    except (OSError, IndexError):
        return ""


def scan_errors(report: dict) -> list[str]:
    """Errors that make the result untrustworthy. Partial parses still scan the rest of the file."""
    problems = []
    for error in report.get("errors", []):
        kind = error.get("type")
        kind = kind[0] if isinstance(kind, list) else kind
        if kind != "PartialParsing":
            problems.append(f"{kind}: {error.get('message', error)}")
    return problems


def apply_exceptions(findings: list[Finding], exceptions: list[ScanException], today: datetime.date) -> list[str]:
    """Mark excepted findings and return problems with the exception list itself."""
    problems = []
    for exception in exceptions:
        label = f"{exception.rule} in {exception.path}" + (f" ({exception.line!r})" if exception.line else "")
        if exception.expires < today:
            problems.append(f"exception for {label} expired on {exception.expires}")
            continue
        matches = [finding for finding in findings if exception.matches(finding)]
        if not matches:
            problems.append(f"exception for {label} matches no finding; remove it")
        for finding in matches:
            finding.excepted = True
    return problems


def render_markdown(findings: list[Finding], problems: list[str]) -> str:
    """A summary for the job summary and the terminal."""
    lines = ["## Semgrep scan", ""]
    if findings:
        lines += ["| Severity | Rule | Location | Status |", "| --- | --- | --- | --- |"]
        for f in sorted(findings, key=lambda f: (not f.blocking, f.path, f.line)):
            status = "excepted" if f.excepted else ("**blocking**" if f.blocking else "reported")
            lines.append(f"| {f.severity} | `{f.rule}` | `{f.path}:{f.line}` | {status} |")
        details = [f for f in findings if not f.excepted]
        if details:
            lines += ["", "### Open findings", ""]
            lines += [f"- `{f.path}:{f.line}` `{f.rule}`: {f.message}" for f in details]
    else:
        lines.append("No findings.")
    if problems:
        lines += ["", "### Problems", "", *[f"- {problem}" for problem in problems]]
    return "\n".join(lines) + "\n"


def evaluate(
    findings: list[Finding], exceptions: list[ScanException], today: datetime.date, errors: list[str] = ()
) -> tuple[int, str]:
    """Return the exit code and the rendered report."""
    problems = [*errors, *apply_exceptions(findings, exceptions, today)]
    blocking = [f for f in findings if f.blocking and not f.excepted]
    return (1 if blocking or problems else 0), render_markdown(findings, problems)


def _semgrep(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["uvx", "--from", SEMGREP, "semgrep", *args, "--metrics", "off", "--disable-version-check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "SEMGREP_SEND_METRICS": "off"},
    )


def check_local_rules() -> None:
    """Fail when a Janus rule no longer flags (or wrongly flags) its fixture."""
    rules, fixture = LOCAL_RULE_TESTS
    result = _semgrep("--test", "--config", str(rules), str(fixture))
    if result.returncode != 0:
        raise ScanError(f"the Janus Semgrep rules fail their fixture:\n{result.stdout}{result.stderr}")


def run_semgrep(rules: Path, json_path: Path, sarif_path: Path) -> dict:
    """Run the pinned Semgrep over :data:`TARGETS` and return its JSON report."""
    configs = [arg for directory in RULE_DIRS for arg in ("--config", str(rules / directory))]
    # A report left over from an earlier run must never stand in for this one.
    for path in (json_path, sarif_path):
        path.unlink(missing_ok=True)
    result = _semgrep(
        "scan",
        *configs,
        "--config",
        str(LOCAL_RULES / "janus.yml"),
        "--severity",
        "WARNING",
        "--severity",
        "ERROR",
        "--quiet",
        "--json-output",
        str(json_path),
        "--sarif-output",
        str(sarif_path),
        *TARGETS,
    )
    # Exit 0 is a clean scan and 1 a scan with findings; anything else means Semgrep failed.
    if result.returncode not in (0, 1) or not json_path.exists():
        raise ScanError(f"semgrep failed (exit {result.returncode}): {result.stderr.strip()}")
    return json.loads(json_path.read_text())


def main(argv: list[str] | None = None) -> int:
    """Run the scan; exit 1 on blocking findings or problems, 2 when Semgrep cannot run."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", type=Path, default=ROOT / "semgrep.json", help="Semgrep JSON report path")
    parser.add_argument("--sarif", type=Path, default=ROOT / "semgrep.sarif", help="SARIF report path")
    args = parser.parse_args(argv)
    try:
        check_local_rules()
        report = run_semgrep(ensure_rules(), args.json, args.sarif)
    except ScanError as error:
        print(f"semgrep scan could not run: {error}", file=sys.stderr)
        return 2
    exit_code, rendered = evaluate(parse_results(report), load_exceptions(), datetime.date.today(), scan_errors(report))
    print(rendered)
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a") as handle:
            handle.write(rendered)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
