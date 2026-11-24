"""Fail when backend statement or branch coverage drops below its floor (JAN-261).

Reads the ``coverage json`` report and the floors in ``[tool.janus.coverage]`` of
``pyproject.toml``. pytest-cov's ``--cov-fail-under`` only knows the combined number, so
the two are gated here separately.

Usage: ``python scripts/coverage_gate.py coverage.json``
"""

from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def check(report: dict, floors: dict) -> list[str]:
    """Return one message per metric below its floor."""
    totals = report["totals"]
    measured = {
        "statements": totals["percent_statements_covered"],
        "branches": totals["percent_branches_covered"],
    }
    return [
        f"{name} coverage {measured[name]:.2f}% is below the floor of {floor:.2f}%"
        for name, floor in floors.items()
        if measured[name] < floor
    ]


def main(argv: list[str]) -> int:
    """Print the measured values and return non-zero when a floor is missed."""
    report = json.loads(Path(argv[1] if len(argv) > 1 else "coverage.json").read_text())
    floors = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["janus"]["coverage"]
    totals = report["totals"]
    print(
        f"Backend coverage: statements {totals['percent_statements_covered']:.2f}% "
        f"(floor {floors['statements']:.2f}%), branches {totals['percent_branches_covered']:.2f}% "
        f"(floor {floors['branches']:.2f}%)"
    )
    failures = check(report, floors)
    for failure in failures:
        print(f"FAIL: {failure}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
