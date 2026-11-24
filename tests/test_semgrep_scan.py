"""Tests for the Semgrep security scan wrapper in scripts/semgrep_scan.py (JAN-267)."""

import datetime
import importlib.util
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "semgrep_scan.py"
_spec = importlib.util.spec_from_file_location("semgrep_scan", _SCRIPT)
scan = importlib.util.module_from_spec(_spec)
# Registered before execution: dataclasses look their module up in sys.modules.
sys.modules[_spec.name] = scan
_spec.loader.exec_module(scan)

TODAY = datetime.date(2026, 10, 4)


def _result(rule: str, path: str, line: int, severity: str) -> dict:
    return {
        "check_id": f"python.example.security.{rule}",
        "path": path,
        "start": {"line": line},
        "extra": {"severity": severity, "message": f"{rule}\n  found"},
    }


REPORT = {
    "results": [
        _result("janus-route-without-auth-dependency", "src/janus/api/status.py", 26, "ERROR"),
        _result("use-timeout", "src/janus/cli_client.py", 142, "WARNING"),
    ],
    "errors": [{"type": ["PartialParsing", []], "message": "`& Tasks` was unexpected"}],
}


def test_errors_block_and_warnings_are_reported():
    """An ERROR finding fails the scan; a WARNING is listed but does not."""
    findings = scan.parse_results(REPORT)

    assert [(f.rule, f.path, f.line, f.blocking) for f in findings] == [
        ("janus-route-without-auth-dependency", "src/janus/api/status.py", 26, True),
        ("use-timeout", "src/janus/cli_client.py", 142, False),
    ]
    assert findings[0].code == '@router.get("/health")'
    exit_code, report = scan.evaluate(findings, [], TODAY, scan.scan_errors(REPORT))
    assert exit_code == 1
    assert "**blocking**" in report and "reported" in report
    assert scan.evaluate(findings[1:], [], TODAY)[0] == 0


def test_partial_parses_are_tolerated_but_other_errors_fail():
    """Semgrep still scans the rest of a partially parsed file; a rule or crash error is fatal."""
    assert scan.scan_errors(REPORT) == []
    broken = {"errors": [{"type": "InvalidRuleSchemaError", "message": "bad rule"}]}
    assert scan.scan_errors(broken) == ["InvalidRuleSchemaError: bad rule"]
    assert scan.evaluate([], [], TODAY, scan.scan_errors(broken))[0] == 1


def test_an_exception_needs_rule_path_and_line_text():
    """An exception matches its rule and path, and its line text when given."""
    finding = scan.parse_results(REPORT)[0]
    health = scan.ScanException(finding.rule, finding.path, "uptime probe", TODAY, '@router.get("/health")')
    other_line = scan.ScanException(finding.rule, finding.path, "x", TODAY, '@router.get("/status")')
    other_path = scan.ScanException(finding.rule, "src/janus/api/auth.py", "x", TODAY)

    assert health.matches(finding)
    assert not other_line.matches(finding)
    assert not other_path.matches(finding)
    exit_code, report = scan.evaluate([finding], [health], TODAY)
    assert exit_code == 0
    assert "excepted" in report


def test_expired_and_stale_exceptions_fail_the_scan():
    """An exception past its date, or one matching no finding, fails even without blocking findings."""
    finding = scan.parse_results(REPORT)[0]
    expired = scan.ScanException(finding.rule, finding.path, "old", TODAY - datetime.timedelta(days=1))
    stale = scan.ScanException("avoid-sqlalchemy-text", "src/janus/services/gone.py", "removed", TODAY)

    exit_code, report = scan.evaluate([finding], [expired, stale], TODAY)

    assert exit_code == 1
    assert not finding.excepted
    assert "expired on 2026-10-03" in report
    assert "matches no finding" in report


def test_load_exceptions_validates_entries(tmp_path):
    """Exceptions need rule, path, reason, and a TOML date; line is optional."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[[tool.janus.semgrep.exceptions]]\nrule = "r"\npath = "p"\nreason = "why"\nexpires = 2027-01-01\n'
    )
    assert scan.load_exceptions(pyproject) == [scan.ScanException("r", "p", "why", datetime.date(2027, 1, 1))]

    pyproject.write_text('[[tool.janus.semgrep.exceptions]]\nrule = "r"\npath = "p"\nexpires = 2027-01-01\n')
    try:
        scan.load_exceptions(pyproject)
    except ValueError as error:
        assert "reason" in str(error)
    else:
        raise AssertionError("an exception without a reason must be rejected")


def test_the_repository_exception_list_is_valid_and_pinned():
    """The checked-in exceptions parse, and the rules are pinned to a full commit."""
    exceptions = scan.load_exceptions()

    assert exceptions
    assert all(exception.reason for exception in exceptions)
    assert len(scan.RULES_COMMIT) == 40
    assert scan.SEMGREP.startswith("semgrep==")
    assert all(path.exists() for path in scan.LOCAL_RULE_TESTS)


def test_a_failed_semgrep_run_never_reuses_an_old_report(tmp_path, monkeypatch):
    """Earlier reports are removed first, and a failing exit code is an error even if a report exists."""
    json_path, sarif_path = tmp_path / "semgrep.json", tmp_path / "semgrep.sarif"
    json_path.write_text('{"results": [], "errors": []}')
    sarif_path.write_text("{}")

    def failing_semgrep(*args):
        assert not json_path.exists() and not sarif_path.exists()
        json_path.write_text('{"results": [], "errors": []}')
        return scan.subprocess.CompletedProcess(args, 2, "", "boom")

    monkeypatch.setattr(scan, "_semgrep", failing_semgrep)
    try:
        scan.run_semgrep(tmp_path, json_path, sarif_path)
    except scan.ScanError as error:
        assert "exit 2" in str(error)
    else:
        raise AssertionError("a failing Semgrep run must not be evaluated")
