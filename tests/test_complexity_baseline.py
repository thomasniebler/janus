"""Ratchet for the functions baselined with `# noqa: C901` (JAN-259).

A ``noqa`` silences Ruff's complexity check whatever the number, so this test
re-runs C901 with ``--ignore-noqa`` and holds every baselined function at its
measured complexity: it may get simpler, not more complex. Lower an entry when a
function improves and delete it (and its ``noqa``) once it is at most 15. A new
function over the limit fails Ruff itself.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: Measured on main when the rule was introduced (2026-10-04, after JAN-249).
COMPLEXITY_BASELINE = {
    "src/janus/agent_toolboxes/local_tasks.py:build_local_task_tools": 17,
    "src/janus/agent_toolboxes/mail.py:build_mail_tools": 20,
    "src/janus/agent_toolboxes/skills.py:build_skill_tools": 24,
    "src/janus/agent_toolboxes/triggers.py:build_trigger_tools": 21,
    "src/janus/api/planner.py:update_settings": 18,
    "src/janus/cli.py:config_doctor": 22,
    "src/janus/planner_service.py:get_briefing_payload": 21,
    "src/janus/processor.py:_process_email": 16,
    "src/janus/runtime_adapter.py:prepare_step": 19,
    "src/janus/services/topic_service.py:update_topic": 21,
    "src/janus/websocket/chat.py:ws_chat": 30,
}


def _measured() -> dict[str, int]:
    result = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--select", "C901", "--ignore-noqa", "--exit-zero"]
        + ["--output-format", "json", "src"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    measured = {}
    for finding in json.loads(result.stdout):
        name, value = re.search(r"`(.+)` is too complex \((\d+)", finding["message"]).groups()
        path = Path(finding["filename"]).resolve().relative_to(ROOT).as_posix()
        measured[f"{path}:{name}"] = int(value)
    return measured


def test_baselined_functions_do_not_get_more_complex():
    """Every function over 15 is baselined, at or below its recorded complexity, and no entry is stale."""
    measured = _measured()
    grown = {key: (value, COMPLEXITY_BASELINE[key]) for key, value in measured.items() if key in COMPLEXITY_BASELINE}
    assert {key: pair for key, pair in grown.items() if pair[0] > pair[1]} == {}, "baselined function got more complex"
    assert set(measured) - set(COMPLEXITY_BASELINE) == set(), "new function over 15: simplify it, do not add a noqa"
    assert set(COMPLEXITY_BASELINE) - set(measured) == set(), "function is at most 15 now: drop its entry and noqa"
