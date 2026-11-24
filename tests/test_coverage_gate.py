"""Tests for the backend coverage floors in scripts/coverage_gate.py (JAN-261)."""

import importlib.util
import json
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "coverage_gate.py"
_spec = importlib.util.spec_from_file_location("coverage_gate", _SCRIPT)
coverage_gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(coverage_gate)


def _report(statements: float, branches: float) -> dict:
    return {"totals": {"percent_statements_covered": statements, "percent_branches_covered": branches}}


def test_each_metric_is_gated_separately():
    """A drop in either metric fails, even when the other one is well above its floor."""
    floors = {"statements": 88.0, "branches": 72.0}
    assert coverage_gate.check(_report(90.0, 75.0), floors) == []
    assert coverage_gate.check(_report(88.0, 72.0), floors) == []
    (failure,) = coverage_gate.check(_report(95.0, 71.9), floors)
    assert failure.startswith("branches coverage 71.90%")
    (failure,) = coverage_gate.check(_report(87.99, 80.0), floors)
    assert failure.startswith("statements coverage 87.99%")


def test_main_reads_the_floors_from_pyproject(tmp_path, capsys):
    """The CLI exits non-zero below the configured floors and zero above them."""
    low, high = tmp_path / "low.json", tmp_path / "high.json"
    low.write_text(json.dumps(_report(10.0, 10.0)))
    high.write_text(json.dumps(_report(100.0, 100.0)))

    assert coverage_gate.main(["coverage_gate.py", str(high)]) == 0
    assert coverage_gate.main(["coverage_gate.py", str(low)]) == 1
    assert "below the floor" in capsys.readouterr().err
