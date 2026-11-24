"""Tests for the deployment capability probe in scripts/deployment_capabilities.py."""

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "deployment_capabilities.py"
_spec = importlib.util.spec_from_file_location("deployment_capabilities", _SCRIPT)
capabilities = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(capabilities)


def test_every_check_reports_a_result():
    """Each registered check runs in the test environment and returns a status and detail."""
    results = capabilities.run_checks()

    assert [result.name for result in results] == [name for name, _, _ in capabilities.CHECKS]
    assert all(result.detail for result in results)
    by_name = {result.name: result for result in results}
    assert by_name["python-version"].ok
    assert by_name["sqlite-fts5"].ok


def test_main_fails_only_on_required_checks(capsys):
    """A failing informational check warns; a failing required check fails the run."""

    def failing():
        return False, "missing"

    assert capabilities.main([("optional", False, failing)]) == 0
    assert "WARN  optional" in capsys.readouterr().out

    assert capabilities.main([("needed", True, failing)]) == 1
    assert "1 required check(s) failed: needed" in capsys.readouterr().out


def test_crashing_check_counts_as_failed():
    """An exception inside a probe is reported as a failure instead of aborting the run."""

    def crashing():
        raise RuntimeError("boom")

    (result,) = capabilities.run_checks([("crashing", True, crashing)])

    assert not result.ok
    assert result.detail == "RuntimeError: boom"
