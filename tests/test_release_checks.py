"""Tests for the post-release validation scripts (JAN-305).

Covers scripts/release_validation.py (public and signed-in checks), scripts/release_host_checks.py
(host checks) and scripts/pre_release_snapshot.py (the one pre-release database snapshot).
No check reaches the network.
"""

import importlib.util
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


validation = _load("release_validation")
host = _load("release_host_checks")
snapshot = _load("pre_release_snapshot")

BASE = "https://janus.example.org/"


def _routes(table: dict[str, tuple[int, str]]):
    """A fake ``http_get`` answering from ``{path: (status, body)}``; unknown paths are 404."""
    calls = []

    def get(url, token=None):
        calls.append((url, token))
        status, body = table.get(url.removeprefix(BASE.rstrip("/")), (404, ""))
        return validation.Response(status, body)

    get.calls = calls
    return get


# --- release_validation.py -------------------------------------------------------------------


def test_health_version_passes_once_the_new_version_answers():
    """Retries while the old process (or none) answers, and passes on the expected version."""
    answers = iter(
        [
            validation.Response(502, "Bad Gateway"),
            validation.Response(200, json.dumps({"status": "ok", "version": "1.4.0"})),
            validation.Response(200, json.dumps({"status": "ok", "version": "1.5.0"})),
        ]
    )
    sleeps = []

    ok, detail = validation.check_health_version(BASE, "1.5.0", get=lambda url: next(answers), sleep=sleeps.append)

    assert ok
    assert detail.startswith("attempt 3:")
    assert len(sleeps) == 2


def test_health_version_fails_on_degraded_imap_monitor():
    """A stale IMAP monitor answers 500 "degraded" and fails the release check."""
    degraded = validation.Response(
        500, json.dumps({"status": "degraded", "unhealthy_accounts": ["me@example.org"], "version": "1.5.0"})
    )

    ok, detail = validation.check_health_version(BASE, "1.5.0", get=lambda url: degraded, sleep=lambda _: None)

    assert not ok
    assert "me@example.org" in detail
    assert f"attempt {validation.HEALTH_ATTEMPTS}:" in detail


def test_frontend_assets_reports_missing_bundle_files():
    """Every /assets/ file index.html references must be served."""
    index = '<script type="module" src="/assets/index-abc.js"></script><link rel="stylesheet" href="/assets/i.css">'
    get = _routes({"/": (200, index), "/assets/index-abc.js": (200, "js")})

    ok, detail = validation.check_frontend_assets(BASE, get=get)

    assert not ok
    assert "/assets/i.css (HTTP 404)" in detail

    get = _routes({"/": (200, index), "/assets/index-abc.js": (200, "js"), "/assets/i.css": (200, "css")})
    assert validation.check_frontend_assets(BASE, get=get) == (True, "index.html and 2 assets served")


def test_api_docs_closed_tells_spa_fallback_from_real_docs():
    """With docs off both paths serve the SPA (200 HTML); only the real schema or Swagger fails."""
    spa = (200, "<!doctype html><div id=root></div>")
    assert validation.check_api_docs_closed(BASE, get=_routes({"/openapi.json": spa, "/apidocs": spa}))[0]

    exposed = _routes({"/openapi.json": (200, '{"openapi": "3.1.0"}'), "/apidocs": (200, "<div id=swagger-ui>")})
    ok, detail = validation.check_api_docs_closed(BASE, get=exposed)
    assert not ok
    assert "/openapi.json" in detail and "/apidocs" in detail


def test_setup_closed_requires_404():
    """The public setup endpoint answers 404 when public setup is disabled."""
    assert validation.check_setup_closed(BASE, get=_routes({}))[0]
    assert not validation.check_setup_closed(BASE, get=_routes({"/api/auth/setup": (200, "{}")}))[0]


@pytest.mark.parametrize(
    ("probe_result", "ok"),
    [((101, 1008), True), ((403, None), True), ((101, 1000), False), ((101, None), False), ((502, None), False)],
)
def test_chat_websocket_must_turn_away_anonymous_clients(probe_result, ok):
    """Close code 1008 or a 401/403 handshake passes; a normal close or a gateway error fails."""
    assert validation.check_chat_websocket_rejects_anonymous(BASE, probe=lambda *_: probe_result)[0] is ok


def test_signed_in_checks_judge_worker_and_overview():
    """A stopped worker or a degraded service fails; the bearer token is sent with each request."""
    get = _routes(
        {
            "/api/runtime/triggers/worker": (200, json.dumps({"enabled": True, "running": False})),
            "/api/status/summary": (
                200,
                json.dumps(
                    {
                        "overall_status": "degraded",
                        "services": [{"name": "Mail", "status": "degraded", "summary": "1 of 2 monitors stale"}],
                    }
                ),
            ),
        }
    )

    worker_ok, worker_detail = validation.check_trigger_worker(BASE, "token-1", get=get)
    overview_ok, overview_detail = validation.check_status_overview(BASE, "token-1", get=get)

    assert not worker_ok and "running False" in worker_detail
    assert not overview_ok and "Mail: degraded" in overview_detail
    assert {token for _, token in get.calls} == {"token-1"}

    healthy = _routes(
        {
            "/api/runtime/triggers/worker": (200, json.dumps({"enabled": True, "running": True})),
            "/api/status/summary": (200, json.dumps({"overall_status": "operational", "services": []})),
        }
    )
    assert validation.check_trigger_worker(BASE, "t", get=healthy)[0]
    assert validation.check_status_overview(BASE, "t", get=healthy)[0]


def test_signed_in_checks_share_one_login(monkeypatch):
    """Both signed-in checks reuse one smoke-test login and its token."""
    logins = []
    seen_tokens = []

    def fake_login(base_url, username, password):
        logins.append(username)
        return "token-1"

    def fake_check(base_url, token):
        seen_tokens.append(token)
        return True, "ok"

    monkeypatch.setattr(validation, "smoke_login", fake_login)
    monkeypatch.setattr(validation, "check_trigger_worker", fake_check)
    monkeypatch.setattr(validation, "check_status_overview", fake_check)
    checks = {name: check for name, _, check in validation.build_checks(BASE, "1.5.0", "smoke", "secret")}

    assert checks["trigger-worker"]() == (True, "ok")
    assert checks["status-overview"]() == (True, "ok")
    assert logins == ["smoke"]
    assert seen_tokens == ["token-1", "token-1"]


def test_missing_smoke_credentials_only_warn():
    """Without the smoke-test secrets the signed-in checks are replaced by one warning."""
    checks = validation.build_checks(BASE, "1.5.0", None, None)

    (name, required, check) = checks[-1]
    assert (name, required) == ("smoke-user", False)
    assert "JANUS_SMOKE_USERNAME" in check()[1]
    assert "trigger-worker" not in [name for name, _, _ in checks]


def test_smoke_login_rejects_failed_login():
    """A failed login is an error, so both signed-in checks fail with the reason."""
    with pytest.raises(RuntimeError, match="HTTP 401"):
        validation.smoke_login(BASE, "smoke", "wrong", post=lambda url, payload: validation.Response(401, "{}"))
    token = validation.smoke_login(
        BASE, "smoke", "right", post=lambda url, payload: validation.Response(200, '{"access_token": "abc"}')
    )
    assert token == "abc"


def test_report_fails_only_on_required_checks(capsys):
    """A failing informational check warns; a failing required check fails the run."""
    assert validation.report([validation.CheckResult("optional", False, False, "meh")]) == 0
    assert "WARN  optional" in capsys.readouterr().out
    assert validation.report(validation.run_checks([("crash", True, lambda: 1 / 0)])) == 1
    assert "ZeroDivisionError" in capsys.readouterr().out


# --- release_host_checks.py ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "seconds"),
    [
        ("janus                            RUNNING   pid 4242, uptime 0:05:12", 312),
        ("janus RUNNING pid 1, uptime 2 days, 3:04:05", 2 * 86400 + 3 * 3600 + 4 * 60 + 5),
        ("janus STOPPED Oct 04 03:00 PM", None),
    ],
)
def test_parse_uptime(line, seconds):
    """Reads supervisor's uptime in both its short and its multi-day form."""
    assert host.parse_uptime(line) == seconds


def test_supervisor_status_requires_a_process_restarted_by_this_deploy():
    """RUNNING is not enough: an uptime older than the deploy means the old process survived."""
    now = 10_000.0
    fresh = "janus RUNNING pid 4242, uptime 0:01:00"
    stale = "janus RUNNING pid 17, uptime 3 days, 0:00:00"

    assert host.evaluate_supervisor_status(fresh, deployed_at=now - 90, now=now)[0]
    assert not host.evaluate_supervisor_status(stale, deployed_at=now - 90, now=now)[0]
    assert not host.evaluate_supervisor_status("janus FATAL Exited too quickly", deployed_at=now - 90, now=now)[0]


def test_scan_log_counts_only_records_since_the_restart():
    """Tracebacks and CRITICAL records fail; ERROR records are counted apart; older ones are ignored."""
    lines = [
        "2026-10-04 14:59:00,001 - janus.imap - ERROR - before the restart",
        "Traceback (most recent call last):",
        '  File "x.py", line 1',
        "2026-10-04 15:00:05,000 - janus.main - INFO - started",
        "2026-10-04 15:00:06,000 - janus.imap - ERROR - login failed",
        "2026-10-04 15:00:07,000 - janus.worker - ERROR - handler crashed",
        "Traceback (most recent call last):",
        "ValueError: boom",
        "2026-10-04 15:01:00,000 - janus.main - CRITICAL - out of disk",
    ]

    assert host.scan_log(lines, since=datetime(2026, 10, 4, 15, 0)) == (2, 2)


def test_host_checks_report_failures_without_stopping(capsys):
    """A crashing host check fails the run but the others still report."""
    results = host.run_checks([("boom", True, lambda: 1 / 0), ("fine", False, lambda: (True, "ok"))])

    assert host.report(results) == 1
    out = capsys.readouterr().out
    assert "FAIL  boom" in out and "PASS  fine" in out


def test_host_installed_version_matches_package_metadata():
    """The installed-version check compares against the distribution metadata of janus."""
    from importlib.metadata import version

    assert host.check_installed_version(version("janus-assistant"))[0]
    assert not host.check_installed_version("0.0.0")[0]


# --- pre_release_snapshot.py -----------------------------------------------------------------


def _make_db(path: Path, rows: int) -> None:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE IF NOT EXISTS t (n INTEGER)")
    conn.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(rows)])
    conn.commit()
    conn.close()


def test_snapshot_overwrites_the_previous_one(tmp_path):
    """Each release replaces the single snapshot next to the database."""
    db = tmp_path / "janus.db"
    _make_db(db, 3)
    url = f"sqlite:///{db}"

    assert snapshot.main(url) == 0
    _make_db(db, 2)
    assert snapshot.main(url) == 0

    snap = tmp_path / snapshot.SNAPSHOT_NAME
    assert sqlite3.connect(snap).execute("SELECT count(*) FROM t").fetchone()[0] == 5
    assert sorted(path.name for path in tmp_path.iterdir()) == ["janus.db", snapshot.SNAPSHOT_NAME]


def test_snapshot_fails_without_space_and_leaves_nothing_partial(tmp_path, monkeypatch, capsys):
    """Too little free space fails the snapshot (and so the deploy) before anything is written."""
    db = tmp_path / "janus.db"
    _make_db(db, 1)
    monkeypatch.setattr(snapshot.shutil, "disk_usage", lambda _: type("Usage", (), {"free": 1024})())

    assert snapshot.main(f"sqlite:///{db}") == 1
    assert "not enough free space" in capsys.readouterr().out
    assert not (tmp_path / snapshot.SNAPSHOT_NAME).exists()


def test_snapshot_skips_non_sqlite_and_fails_on_missing_file(tmp_path, capsys):
    """PostgreSQL deployments are skipped; a missing SQLite file is a failure."""
    assert snapshot.main("postgresql://user@db/janus") == 0
    assert "SKIP" in capsys.readouterr().out
    assert snapshot.main(f"sqlite:///{tmp_path / 'missing.db'}") == 1


def test_tls_check_skips_plain_http():
    """Only https URLs have a certificate to check."""
    assert validation.check_tls_certificate("http://localhost:8000/", now=datetime.now(UTC)) == (
        True,
        "not an https URL",
    )


def test_validator_rejects_non_http_base_url():
    """Only http(s) base URLs reach urllib, so file:// and similar schemes are refused."""
    with pytest.raises(SystemExit):
        validation.main(["--base-url", "file:///etc", "--expected-version", "1.5.0"])
