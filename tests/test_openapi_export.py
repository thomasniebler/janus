"""Offline OpenAPI export (scripts/export_openapi.py, JAN-139)."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = ROOT / "scripts" / "export_openapi.py"
_spec = importlib.util.spec_from_file_location("export_openapi", _SCRIPT)
export_openapi = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = export_openapi
_spec.loader.exec_module(export_openapi)

#: One route per area the frontend talks to; the export must contain all of them.
REPRESENTATIVE_ROUTES = {
    "/api/auth/login": "post",
    "/api/emails/": "get",
    "/api/planner/events": "get",
    "/api/status/health": "get",
    "/api/topics/": "get",
    "/api/docs/nav": "get",
}


@pytest.fixture(scope="module")
def schema() -> dict:
    """The exported schema, built once for the module."""
    return export_openapi.build_schema()


def test_export_contains_representative_routes(schema):
    """Auth, emails, planner, status, topics and docs routes are all in the exported schema."""
    assert schema["openapi"].startswith("3.")
    assert schema["info"]["title"] == "Janus Dashboard"
    for path, method in REPRESENTATIVE_ROUTES.items():
        assert method in schema["paths"].get(path, {}), f"{method.upper()} {path} missing from the export"
    assert "LoginRequest" in schema["components"]["schemas"]


def test_export_works_with_public_api_docs_disabled(monkeypatch):
    """The schema is built from the routes, not served, so closed API docs change nothing."""
    from janus.config import settings

    monkeypatch.setattr(settings, "public_api_docs_enabled", False)
    closed = export_openapi.build_schema()
    monkeypatch.setattr(settings, "public_api_docs_enabled", True)
    opened = export_openapi.build_schema()

    assert closed["paths"]
    assert export_openapi.render(closed) == export_openapi.render(opened)
    assert "/openapi.json" not in closed["paths"]


def test_render_is_stable_and_ends_with_newline(schema):
    """Two renders are byte-identical and keep route order, so diffs stay reviewable."""
    first = export_openapi.render(schema)
    assert first == export_openapi.render(json.loads(first))
    assert first.endswith("}\n")
    assert list(json.loads(first)["paths"]) == list(schema["paths"])


def test_main_writes_and_checks(tmp_path, monkeypatch, capsys):
    """``--check`` passes on a fresh export and fails, with a diff, when the file is stale."""
    monkeypatch.setattr(export_openapi, "build_schema", lambda: {"openapi": "3.1.0", "paths": {"/a": {}}})
    target = tmp_path / "nested" / "openapi.json"

    assert export_openapi.main(["--check", "--output", str(target)]) == 1  # missing file
    assert not target.exists()
    assert export_openapi.main(["--output", str(target)]) == 0
    assert json.loads(target.read_text()) == {"openapi": "3.1.0", "paths": {"/a": {}}}
    assert export_openapi.main(["--check", "--output", str(target)]) == 0

    monkeypatch.setattr(export_openapi, "build_schema", lambda: {"openapi": "3.1.0", "paths": {"/b": {}}})
    capsys.readouterr()
    assert export_openapi.main(["--check", "--output", str(target)]) == 1
    captured = capsys.readouterr()
    assert '+    "/b": {}' in captured.out
    assert "task openapi:generate" in captured.err
    assert '"/a"' in target.read_text()  # --check never writes


def test_export_does_not_start_workers_or_touch_the_database(tmp_path):
    """A fresh interpreter exports the schema without startup hooks, workers or a database file."""
    driver = textwrap.dedent(
        f"""
        import importlib.util, json, sys, threading
        spec = importlib.util.spec_from_file_location("export_openapi", {str(_SCRIPT)!r})
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        code = module.main(["--output", {str(tmp_path / "openapi.json")!r}])

        from janus import startup
        from janus.worker_manager import worker_manager
        print(json.dumps({{
            "code": code,
            "startup_initialized": startup._startup_initialized,
            "trigger_worker_running": startup._runtime_trigger_stop_event is not None,
            "workers": sorted(worker_manager._workers),
            "threads": sorted(t.name for t in threading.enumerate()),
        }}))
        """
    )
    data_home = tmp_path / "xdg-data"
    env = {
        **os.environ,
        "XDG_DATA_HOME": str(data_home),
        "XDG_STATE_HOME": str(tmp_path / "xdg-state"),
        "XDG_CONFIG_HOME": str(tmp_path / "xdg-config"),
        "JANUS_SECRET_KEY": "test-secret-key-for-testing-only-32ch",
    }
    env.pop("DATABASE_URL", None)  # use the default SQLite file under XDG_DATA_HOME
    result = subprocess.run(
        [sys.executable, "-c", driver], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=300
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout.strip().splitlines()[-1])

    assert report["code"] == 0
    assert json.loads((tmp_path / "openapi.json").read_text())["paths"]
    assert report["startup_initialized"] is False
    assert report["trigger_worker_running"] is False
    assert report["workers"] == []
    # Only the idle supervisor that worker_manager creates on import; no IMAP, planner,
    # WhatsApp, trigger or knowledge-index threads.
    assert set(report["threads"]) <= {"MainThread", "worker-supervisor"}
    assert not (data_home / "janus" / "janus.db").exists()
