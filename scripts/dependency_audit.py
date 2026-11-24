"""Dependency vulnerability audit for Python (pip-audit) and npm (npm audit), JAN-268.

Audits exactly what the lockfiles pin, never what happens to be installed:

* Python: ``uv export --frozen`` writes hashed requirements for five scopes (production,
  dev group, and the optional ``documents``, ``whatsapp`` and ``desktop`` extras), which
  ``pip-audit`` checks without resolving or installing anything. pip-audit reports no
  severity, so every Python finding blocks.
* npm: ``npm audit --package-lock-only`` over each lockfile in :data:`NPM_PROJECTS`. A
  finding blocks at :data:`NPM_BLOCKING_SEVERITIES`; lower severities are reported only.
  A second ``--omit=dev`` pass tells production findings from dev-only ones.

Accepted findings live in ``[[tool.janus.dependency_audit.exceptions]]`` in pyproject.toml,
each with the advisory ``id``, the ``package``, a ``reason``, and an ``expires`` date. An
expired exception fails the run, and so does one that no longer matches any finding, so the
list cannot outlive its purpose. Nothing here upgrades a dependency: fixes go through
``uv lock --upgrade-package`` or ``npm install`` in a reviewed change.

Run with ``task audit:deps``; CI runs it on pull requests and daily (dependency-audit.yml).
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import subprocess
import sys
import tempfile
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PIP_AUDIT = "pip-audit==2.10.1"

#: Python scopes: name -> extra ``uv export`` arguments.
PYTHON_SCOPES: dict[str, list[str]] = {
    "production": ["--no-dev"],
    "dev": ["--only-dev"],
    "documents": ["--no-dev", "--extra", "documents"],
    "whatsapp": ["--no-dev", "--extra", "whatsapp"],
    "desktop": ["--no-dev", "--extra", "desktop"],
}

#: npm lockfiles in scope: directory (relative to the repo root) -> what it builds.
NPM_PROJECTS: dict[str, str] = {
    ".": "Tailwind CSS for the server-rendered pages",
    "frontend": "React web dashboard",
    "modules/todo-chat/frontend": "todo-chat desktop module",
}
NPM_BLOCKING_SEVERITIES = frozenset({"high", "critical"})


@dataclass
class Finding:
    """One advisory affecting one package version, merged across the scopes it appears in."""

    ecosystem: str
    package: str
    version: str
    advisory: str
    aliases: list[str]
    fix: str
    severity: str
    blocking: bool
    scopes: list[str] = field(default_factory=list)
    excepted: bool = False

    @property
    def ids(self) -> set[str]:
        """The advisory id and its aliases, as an exception may name any of them."""
        return {self.advisory, *self.aliases}


@dataclass(frozen=True)
class AuditException:
    """An accepted finding: ``id`` and ``package`` must match, and it lapses on ``expires``."""

    id: str
    package: str
    reason: str
    expires: datetime.date


class AuditError(RuntimeError):
    """A scanner could not produce a result (network, lockfile, tool failure)."""


def load_exceptions(pyproject: Path = ROOT / "pyproject.toml") -> list[AuditException]:
    """Read the accepted findings from pyproject.toml."""
    config = tomllib.loads(pyproject.read_text())
    entries = config.get("tool", {}).get("janus", {}).get("dependency_audit", {}).get("exceptions", [])
    exceptions = []
    for entry in entries:
        missing = {"id", "package", "reason", "expires"} - entry.keys()
        if missing:
            raise ValueError(f"dependency audit exception {entry} is missing {sorted(missing)}")
        if not isinstance(entry["expires"], datetime.date):
            raise ValueError(f"dependency audit exception {entry['id']}: expires must be a TOML date")
        exceptions.append(AuditException(entry["id"], entry["package"], entry["reason"], entry["expires"]))
    return exceptions


def _run(command: list[str], cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False)


def parse_pip_audit(report: dict, scope: str) -> list[Finding]:
    """Turn a ``pip-audit -f json`` report into findings."""
    findings = []
    for dependency in report.get("dependencies", []):
        for vuln in dependency.get("vulns", []):
            findings.append(
                Finding(
                    ecosystem="python",
                    package=dependency["name"],
                    version=dependency["version"],
                    advisory=vuln["id"],
                    aliases=list(vuln.get("aliases", [])),
                    fix=", ".join(vuln.get("fix_versions", [])) or "none available",
                    severity="unrated",
                    blocking=True,
                    scopes=[scope],
                )
            )
    return findings


def audit_python() -> list[Finding]:
    """Audit every Python scope exported from uv.lock."""
    findings = []
    with tempfile.TemporaryDirectory() as tmp:
        for scope, args in PYTHON_SCOPES.items():
            requirements = Path(tmp) / f"{scope}.txt"
            export = _run(["uv", "export", "--frozen", "--no-emit-project", "--quiet", "-o", str(requirements), *args])
            if export.returncode != 0:
                raise AuditError(f"uv export ({scope}) failed: {export.stderr.strip()}")
            output = Path(tmp) / f"{scope}.json"
            audit = _run(
                [
                    "uvx",
                    "--from",
                    PIP_AUDIT,
                    "pip-audit",
                    "--requirement",
                    str(requirements),
                    "--require-hashes",
                    "--disable-pip",
                    "--progress-spinner",
                    "off",
                    "--format",
                    "json",
                    "--output",
                    str(output),
                ]
            )
            # pip-audit exits 1 when it finds something; anything else without a report is a failure.
            if audit.returncode not in (0, 1) or not output.exists():
                raise AuditError(f"pip-audit ({scope}) failed: {audit.stderr.strip()}")
            findings += parse_pip_audit(json.loads(output.read_text()), scope)
    return findings


def _npm_advisories(report: dict) -> dict[tuple[str, str], dict]:
    """Map (package, advisory) to the advisory details in an ``npm audit --json`` report."""
    advisories = {}
    for name, vulnerability in report.get("vulnerabilities", {}).items():
        for via in vulnerability.get("via", []):
            # Strings in "via" point at another vulnerable package; that package lists the advisory itself.
            if isinstance(via, dict):
                advisory = via.get("url", "").rsplit("/", 1)[-1] or str(via.get("source"))
                advisories[(name, advisory)] = {"via": via, "vulnerability": vulnerability}
    return advisories


def parse_npm_audit(full: dict, production: dict, project: str) -> list[Finding]:
    """Turn ``npm audit --json`` reports (all deps, and ``--omit=dev``) into findings."""
    production_keys = set(_npm_advisories(production))
    findings = []
    for (name, advisory), details in _npm_advisories(full).items():
        via, vulnerability = details["via"], details["vulnerability"]
        fix_available = vulnerability.get("fixAvailable")
        if isinstance(fix_available, dict):
            fix = f"{fix_available['name']}@{fix_available['version']}"
            if fix_available.get("isSemVerMajor"):
                fix += " (semver-major)"
        else:
            fix = "npm audit fix" if fix_available else "none available"
        severity = via.get("severity", vulnerability.get("severity", "unknown"))
        scope = "production" if (name, advisory) in production_keys else "dev"
        findings.append(
            Finding(
                ecosystem="npm",
                package=name,
                version=via.get("range", vulnerability.get("range", "?")),
                advisory=advisory,
                aliases=[str(via["source"])] if via.get("source") else [],
                fix=fix,
                severity=severity,
                blocking=severity in NPM_BLOCKING_SEVERITIES,
                scopes=[f"{project} ({scope})"],
            )
        )
    return findings


def audit_npm() -> list[Finding]:
    """Audit every npm lockfile in scope."""
    findings = []
    for project in NPM_PROJECTS:
        reports = []
        for extra in ([], ["--omit=dev"]):
            result = _run(["npm", "audit", "--json", "--package-lock-only", *extra], cwd=ROOT / project)
            try:
                report = json.loads(result.stdout)
            except json.JSONDecodeError as error:
                raise AuditError(f"npm audit ({project}) failed: {result.stderr.strip()}") from error
            if "error" in report:
                raise AuditError(f"npm audit ({project}) failed: {report['error']}")
            reports.append(report)
        findings += parse_npm_audit(reports[0], reports[1], project)
    return findings


def merge(findings: list[Finding]) -> list[Finding]:
    """Collapse the same advisory on the same package version across scopes."""
    merged: dict[tuple[str, str, str, str], Finding] = {}
    for finding in findings:
        key = (finding.ecosystem, finding.package, finding.version, finding.advisory)
        if key in merged:
            merged[key].scopes += [scope for scope in finding.scopes if scope not in merged[key].scopes]
        else:
            merged[key] = finding
    return sorted(merged.values(), key=lambda f: (f.ecosystem, f.package, f.advisory))


def apply_exceptions(findings: list[Finding], exceptions: list[AuditException], today: datetime.date) -> list[str]:
    """Mark excepted findings and return problems with the exception list itself."""
    problems = []
    for exception in exceptions:
        matches = [f for f in findings if f.package == exception.package and exception.id in f.ids]
        if exception.expires < today:
            problems.append(f"exception {exception.id} ({exception.package}) expired on {exception.expires}")
            continue
        if not matches:
            problems.append(f"exception {exception.id} ({exception.package}) matches no finding; remove it")
        for finding in matches:
            finding.excepted = True
    return problems


def render_markdown(findings: list[Finding], problems: list[str]) -> str:
    """A summary table for the job summary and the terminal."""
    lines = ["## Dependency audit", ""]
    if findings:
        lines += [
            "| Ecosystem | Package | Version | Advisory | Severity | Fix | Scopes | Status |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for f in findings:
            status = "excepted" if f.excepted else ("**blocking**" if f.blocking else "reported")
            advisory = " / ".join([f.advisory, *[a for a in f.aliases if a != f.advisory]])
            row = [f.ecosystem, f.package, f.version, advisory, f.severity, f.fix, ", ".join(f.scopes), status]
            lines.append("| " + " | ".join(row) + " |")
    else:
        lines.append("No known vulnerabilities in the audited lockfiles.")
    if problems:
        lines += ["", "### Exception list problems", "", *[f"- {problem}" for problem in problems]]
    return "\n".join(lines) + "\n"


def evaluate(
    findings: list[Finding], exceptions: list[AuditException], today: datetime.date
) -> tuple[int, str, list[Finding]]:
    """Return the exit code, the rendered report, and the merged findings."""
    findings = merge(findings)
    problems = apply_exceptions(findings, exceptions, today)
    blocking = [f for f in findings if f.blocking and not f.excepted]
    return (1 if blocking or problems else 0), render_markdown(findings, problems), findings


def main(argv: list[str] | None = None) -> int:
    """Run the audit; exit 1 on blocking findings or exception problems, 2 when a scanner fails."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", type=Path, help="also write the findings as JSON to this path")
    args = parser.parse_args(argv)
    try:
        findings = audit_python() + audit_npm()
    except AuditError as error:
        print(f"dependency audit could not run: {error}", file=sys.stderr)
        return 2
    exit_code, report, merged = evaluate(findings, load_exceptions(), datetime.date.today())
    print(report)
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a") as handle:
            handle.write(report)
    if args.json:
        args.json.write_text(json.dumps([vars(f) for f in merged], indent=2))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
