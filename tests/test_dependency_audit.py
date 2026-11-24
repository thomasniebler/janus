"""Tests for the dependency vulnerability audit in scripts/dependency_audit.py (JAN-268)."""

import datetime
import importlib.util
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "dependency_audit.py"
_spec = importlib.util.spec_from_file_location("dependency_audit", _SCRIPT)
audit = importlib.util.module_from_spec(_spec)
# Registered before execution: dataclasses look their module up in sys.modules.
sys.modules[_spec.name] = audit
_spec.loader.exec_module(audit)

TODAY = datetime.date(2026, 10, 4)

PIP_REPORT = {
    "dependencies": [
        {"name": "fastapi", "version": "1.0.0", "vulns": []},
        {
            "name": "click",
            "version": "8.3.1",
            "vulns": [{"id": "PYSEC-2026-2132", "aliases": ["GHSA-47fr-3ffg-hgmw"], "fix_versions": ["8.3.3"]}],
        },
    ]
}


def _npm_report(severity: str) -> dict:
    return {
        "vulnerabilities": {
            "vite": {
                "severity": severity,
                "range": "<7.1.5",
                "fixAvailable": {"name": "vite", "version": "7.1.5", "isSemVerMajor": False},
                "via": [
                    {
                        "source": 1108,
                        "url": "https://github.com/advisories/GHSA-aaaa-bbbb-cccc",
                        "severity": severity,
                        "range": "<7.1.5",
                    }
                ],
            },
            # A package that is only vulnerable through another one names it as a string.
            "@vitejs/plugin-react": {"severity": severity, "range": "*", "fixAvailable": True, "via": ["vite"]},
        }
    }


def _click_findings():
    return audit.parse_pip_audit(PIP_REPORT, "production") + audit.parse_pip_audit(PIP_REPORT, "dev")


def test_python_findings_block_and_merge_across_scopes():
    """Every Python finding blocks; one advisory seen in two scopes is reported once with both."""
    exit_code, report, findings = audit.evaluate(_click_findings(), [], TODAY)

    assert exit_code == 1
    assert [(f.package, f.advisory, f.fix, f.scopes) for f in findings] == [
        ("click", "PYSEC-2026-2132", "8.3.3", ["production", "dev"])
    ]
    assert "**blocking**" in report


def test_npm_blocks_only_from_high_severity():
    """Findings from npm audit block at high/critical, moderate is reported; --omit=dev separates production."""
    moderate = audit.parse_npm_audit(_npm_report("moderate"), {}, "frontend")
    high = audit.parse_npm_audit(_npm_report("high"), _npm_report("high"), "frontend")

    assert [(f.package, f.advisory, f.blocking, f.scopes) for f in moderate] == [
        ("vite", "GHSA-aaaa-bbbb-cccc", False, ["frontend (dev)"])
    ]
    assert audit.evaluate(moderate, [], TODAY)[0] == 0
    assert [(f.fix, f.scopes) for f in high] == [("vite@7.1.5", ["frontend (production)"])]
    assert audit.evaluate(high, [], TODAY)[0] == 1


def test_an_exception_can_name_an_alias_and_unblocks_its_finding():
    """An unexpired exception matching the package and any advisory alias lets the run pass."""
    exception = audit.AuditException("GHSA-47fr-3ffg-hgmw", "click", "click.edit() is unused", TODAY)

    exit_code, report, findings = audit.evaluate(_click_findings(), [exception], TODAY)

    assert exit_code == 0
    assert findings[0].excepted
    assert "excepted" in report


def test_expired_and_stale_exceptions_fail_the_run():
    """An exception past its date, or one that matches nothing, fails even with no blocking finding."""
    expired = audit.AuditException("PYSEC-2026-2132", "click", "accepted", TODAY - datetime.timedelta(days=1))
    stale = audit.AuditException("GHSA-zzzz-zzzz-zzzz", "requests", "fixed upstream", TODAY)

    exit_code, report, findings = audit.evaluate(_click_findings(), [expired, stale], TODAY)

    assert exit_code == 1
    assert not findings[0].excepted
    assert "expired on 2026-10-03" in report
    assert "matches no finding" in report
    assert audit.evaluate([], [stale], TODAY)[0] == 1


def test_no_findings_passes():
    """A clean audit exits 0 and says so."""
    exit_code, report, _ = audit.evaluate([], [], TODAY)

    assert exit_code == 0
    assert "No known vulnerabilities" in report


def test_load_exceptions_validates_entries(tmp_path):
    """Exceptions need id, package, reason, and a TOML date for expires."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[[tool.janus.dependency_audit.exceptions]]\nid = "X"\npackage = "p"\nreason = "r"\nexpires = 2026-12-31\n'
    )
    assert audit.load_exceptions(pyproject) == [audit.AuditException("X", "p", "r", datetime.date(2026, 12, 31))]

    pyproject.write_text('[[tool.janus.dependency_audit.exceptions]]\nid = "X"\npackage = "p"\nexpires = "soon"\n')
    try:
        audit.load_exceptions(pyproject)
    except ValueError as error:
        assert "reason" in str(error)
    else:
        raise AssertionError("an exception without a reason must be rejected")


def test_the_repository_exception_list_is_valid():
    """The checked-in exception list parses."""
    audit.load_exceptions()
