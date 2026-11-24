"""Self-hosted auto-update: release checks, eligibility, install, rollback, and restart."""

import hashlib
import io
import subprocess
import sys
import tarfile
from pathlib import Path

import httpx
import pytest

from janus import config
from janus.services import update_service
from janus.workers import update as update_worker

ASSET_URL = "https://github.com/thomasniebler/janus/releases/download/v1.1.0/janus-frontend-dist.tar.gz"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def _commit_version(repo: Path, version: str) -> None:
    (repo / "pyproject.toml").write_text(f'[project]\nname = "janus"\nversion = "{version}"\n')
    _git(repo, "add", "pyproject.toml")
    _git(repo, "commit", "-q", "-m", f"release {version}")
    _git(repo, "tag", f"v{version}")


@pytest.fixture
def repos(tmp_path, monkeypatch):
    """An upstream repo with v1.0.0 and v1.1.0, and a local clone on main at v1.0.0."""
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    _git(upstream, "init", "-q", "-b", "main")
    _git(upstream, "config", "user.email", "test@example.invalid")
    _git(upstream, "config", "user.name", "test")
    (upstream / ".gitignore").write_text(".venv/\nfrontend/dist/\n")
    _git(upstream, "add", ".gitignore")
    _commit_version(upstream, "1.0.0")
    _commit_version(upstream, "1.1.0")

    local = tmp_path / "local"
    _git(tmp_path, "clone", "-q", "--no-tags", str(upstream), str(local))
    _git(local, "reset", "-q", "--hard", "HEAD~1")
    _git(local, "fetch", "-q", "origin", "refs/tags/v1.0.0:refs/tags/v1.0.0")
    (local / ".venv").mkdir()
    (local / "frontend" / "dist").mkdir(parents=True)
    (local / "frontend" / "dist" / "index.html").write_text("old")

    monkeypatch.setattr(config.settings, "auto_update_enabled", True)
    monkeypatch.setattr(sys, "prefix", str(local / ".venv"))
    monkeypatch.setattr(update_service, "find_uv", lambda: "uv")
    monkeypatch.setattr(update_service, "release_source_url", lambda: str(upstream))
    monkeypatch.setattr(config.settings, "database_url", f"sqlite:///{tmp_path / 'janus.db'}")
    return upstream, local


@pytest.fixture
def commands(monkeypatch):
    """Record the child-process steps instead of running uv, Python and migrations."""
    recorded: list[str] = []
    failing: set[str] = set()

    def fake_run(command, repo_root, what):
        recorded.append(what)
        if what in failing:
            raise update_service.UpdateError(f"{what} failed")

    monkeypatch.setattr(update_service, "_run", fake_run)
    return recorded, failing


def _asset_bytes(index: str = "new") -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        data = index.encode()
        info = tarfile.TarInfo("./index.html")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def _client(asset: bytes, *, tag: str = "v1.1.0", digest: str | None = None, status: int = 200) -> httpx.Client:
    digest = digest if digest is not None else "sha256:" + hashlib.sha256(asset).hexdigest()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/releases/latest"):
            if status != 200:
                return httpx.Response(status)
            assets = [{"name": update_service.FRONTEND_ASSET_NAME, "browser_download_url": ASSET_URL, "digest": digest}]
            return httpx.Response(200, json={"tag_name": tag, "assets": assets})
        assert str(request.url) == ASSET_URL
        return httpx.Response(200, content=asset)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_parse_tag_accepts_only_release_tags():
    """Only vX.Y.Z tags are releases."""
    assert update_service.parse_tag("v1.12.0") == (1, 12, 0)
    assert update_service.parse_tag("1.12.0") is None
    assert update_service.parse_tag("v1.12.0-rc1") is None


def test_fetch_latest_release_reads_tag_and_asset():
    """The release's tag, version, frontend asset URL and digest are read."""
    release = update_service.fetch_latest_release("o/r", client=_client(b"x", digest="sha256:" + "a" * 64))
    assert release.tag == "v1.1.0"
    assert release.version == (1, 1, 0)
    assert release.asset_url == ASSET_URL
    assert release.asset_digest == "sha256:" + "a" * 64


@pytest.mark.parametrize("kwargs", [{"tag": "nightly"}, {"status": 500}])
def test_fetch_latest_release_rejects_bad_answers(kwargs):
    """A non-release tag or an HTTP error is an UpdateError."""
    with pytest.raises(update_service.UpdateError):
        update_service.fetch_latest_release("o/r", client=_client(b"x", **kwargs))


def test_check_for_update_compares_with_pyproject(repos):
    """Only a release newer than pyproject.toml's version is an update."""
    _, local = repos
    assert update_service.check_for_update(local, client=_client(b"x")).tag == "v1.1.0"
    assert update_service.check_for_update(local, client=_client(b"x", tag="v1.0.0")) is None


def test_eligibility_on_main_and_at_release_tag(repos):
    """A clean checkout on main or detached at a release tag may update."""
    _, local = repos
    assert update_service.eligibility(local).eligible
    assert update_service.eligibility(local).branch == "main"
    _git(local, "checkout", "-q", "--detach", "v1.0.0")
    status = update_service.eligibility(local)
    assert status.eligible and status.branch is None


def test_eligibility_refusals(repos, tmp_path, monkeypatch):
    """Non-git, dirty, feature-branch, untagged, foreign-venv and disabled installs never update."""
    _, local = repos
    assert "not a git checkout" in update_service.eligibility(tmp_path).reason

    (local / "pyproject.toml").write_text("changed")
    assert "uncommitted changes" in update_service.eligibility(local).reason
    _git(local, "checkout", "-q", "--", "pyproject.toml")

    _git(local, "checkout", "-q", "-b", "feature")
    assert "branch 'feature'" in update_service.eligibility(local).reason
    _git(local, "checkout", "-q", "--detach", "main")
    _git(local, "tag", "-d", "v1.0.0")
    assert "not at a release tag" in update_service.eligibility(local).reason

    monkeypatch.setattr(sys, "prefix", "/elsewhere")
    assert "not running from" in update_service.eligibility(local).reason
    monkeypatch.setattr(config.settings, "auto_update_enabled", False)
    assert update_service.eligibility(local).reason == "auto_update_enabled is off"


def test_apply_update_fast_forwards_main_and_swaps_frontend(repos, commands):
    """On main, the update fast-forwards to the tag and installs the new frontend."""
    upstream, local = repos
    recorded, _ = commands
    asset = _asset_bytes("new")
    release = update_service.fetch_latest_release(client=_client(asset))

    update_service.apply_update(release, local, client=_client(asset))

    assert _git(local, "rev-parse", "HEAD") == _git(upstream, "rev-parse", "v1.1.0")
    assert _git(local, "symbolic-ref", "--short", "HEAD") == "main"
    assert update_service.installed_version(local) == (1, 1, 0)
    assert (local / "frontend" / "dist" / "index.html").read_text() == "new"
    assert recorded == ["uv sync", "import check", "database migration"]


def test_apply_update_moves_a_detached_tag_checkout(repos, commands):
    """A checkout detached at a tag moves to the new tag."""
    upstream, local = repos
    _git(local, "checkout", "-q", "--detach", "v1.0.0")
    asset = _asset_bytes()
    release = update_service.fetch_latest_release(client=_client(asset))

    update_service.apply_update(release, local, client=_client(asset))

    assert _git(local, "rev-parse", "HEAD") == _git(upstream, "rev-parse", "v1.1.0")
    assert _git(local, "tag", "--points-at", "HEAD") == "v1.1.0"


def test_apply_update_refuses_a_digest_mismatch(repos, commands):
    """A frontend asset that does not match its digest stops the update before git moves."""
    _, local = repos
    asset = _asset_bytes()
    before = _git(local, "rev-parse", "HEAD")
    release = update_service.fetch_latest_release(client=_client(asset, digest="sha256:" + "0" * 64))

    with pytest.raises(update_service.UpdateError, match="digest"):
        update_service.apply_update(release, local, client=_client(asset))

    assert _git(local, "rev-parse", "HEAD") == before
    assert commands[0] == []


def test_apply_update_refuses_a_release_without_asset(repos, commands):
    """A release whose frontend asset is not uploaded yet is not installed."""
    _, local = repos
    release = update_service.Release(tag="v1.1.0", version=(1, 1, 0), asset_url=None, asset_digest=None)
    with pytest.raises(update_service.UpdateError, match="no janus-frontend-dist"):
        update_service.apply_update(release, local)


def test_apply_update_rolls_back_a_failed_migration(repos, commands):
    """A failed migration returns the checkout, dependencies and frontend to the old release."""
    _, local = repos
    recorded, failing = commands
    failing.add("database migration")
    asset = _asset_bytes("new")
    before = _git(local, "rev-parse", "HEAD")
    release = update_service.fetch_latest_release(client=_client(asset))

    with pytest.raises(update_service.UpdateError, match="database migration"):
        update_service.apply_update(release, local, client=_client(asset))

    assert _git(local, "rev-parse", "HEAD") == before
    assert _git(local, "symbolic-ref", "--short", "HEAD") == "main"
    assert (local / "frontend" / "dist" / "index.html").read_text() == "old"
    # The rollback re-syncs the old dependencies.
    assert recorded == ["uv sync", "import check", "database migration", "uv sync"]


def test_request_restart_needs_a_running_server(monkeypatch):
    """A restart is only requested when janus start registered its server."""
    monkeypatch.setattr(update_service, "_restart_handler", None)
    monkeypatch.setattr(update_service, "_restart_requested", update_service.threading.Event())
    assert update_service.request_restart() is False
    assert not update_service.restart_requested()

    calls = []
    update_service.set_restart_handler(lambda: calls.append(True))
    try:
        assert update_service.request_restart() is True
    finally:
        update_service.set_restart_handler(None)
    assert calls == [True]
    assert update_service.restart_requested()


def test_worker_installs_and_requests_restart(monkeypatch):
    """The worker installs a newer release and then asks for a restart."""
    release = update_service.Release(tag="v9.0.0", version=(9, 0, 0), asset_url=ASSET_URL, asset_digest=None)
    calls = []
    monkeypatch.setattr(update_service, "check_for_update", lambda: release)
    installed = update_service.Installed("v9.0.0", "abc123", "main")
    monkeypatch.setattr(update_service, "apply_update", lambda r: calls.append(("apply", r.tag)) or installed)
    monkeypatch.setattr(update_worker.update_boot, "write_pending", lambda i: calls.append(("pending", i)))
    monkeypatch.setattr(update_service, "request_restart", lambda: calls.append(("restart",)) or True)
    monkeypatch.setattr(update_worker.event_logger, "log", lambda *a, **k: None)

    assert update_worker.run_update_check() is True
    assert calls == [("apply", "v9.0.0"), ("pending", installed), ("restart",)]


def test_worker_does_not_restart_after_a_failed_install(monkeypatch):
    """A failed install is logged and never restarts the process."""
    release = update_service.Release(tag="v9.0.0", version=(9, 0, 0), asset_url=ASSET_URL, asset_digest=None)

    def fail(_release):
        raise update_service.UpdateError("boom")

    monkeypatch.setattr(update_service, "check_for_update", lambda: release)
    monkeypatch.setattr(update_service, "apply_update", fail)
    monkeypatch.setattr(update_service, "request_restart", lambda: pytest.fail("must not restart"))
    monkeypatch.setattr(update_worker.event_logger, "log", lambda *a, **k: None)

    assert update_worker.run_update_check() is False


def test_apply_update_refuses_while_another_install_holds_the_lock(repos, commands):
    """The worker and `janus update` never install into the same checkout at once."""
    _, local = repos
    asset = _asset_bytes()
    release = update_service.fetch_latest_release(client=_client(asset))
    with update_service._install_lock(local), pytest.raises(update_service.UpdateError, match="another update"):
        update_service.apply_update(release, local, client=_client(asset))
    assert commands[0] == []


def test_apply_update_runs_children_in_the_process_working_directory(repos, tmp_path, monkeypatch):
    """Snapshot, import check and migrations see the same .env and relative database_url as Janus."""
    _, local = repos
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    (workdir / "janus.db").write_bytes(b"")
    monkeypatch.setattr(config.settings, "database_url", "sqlite:///./janus.db")
    seen: dict[str, Path] = {}
    monkeypatch.setattr(update_service, "_run", lambda command, cwd, what: seen.__setitem__(what, cwd))
    asset = _asset_bytes()
    release = update_service.fetch_latest_release(client=_client(asset))

    update_service.apply_update(release, local, client=_client(asset))

    assert seen == {
        "database snapshot": workdir,
        "uv sync": local,
        "import check": workdir,
        "database migration": workdir,
    }


def test_installed_version_rejects_a_non_release_version(tmp_path):
    """A pyproject version that is not X.Y.Z is an UpdateError."""
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "1.2.dev0"\n')
    with pytest.raises(update_service.UpdateError, match="not X.Y.Z"):
        update_service.installed_version(tmp_path)


def test_find_uv_falls_back_to_local_bin(tmp_path, monkeypatch):
    """Without uv on PATH, uv's default install in ~/.local/bin is used."""
    monkeypatch.setattr(update_service.shutil, "which", lambda name: None)
    monkeypatch.setattr(update_service.Path, "home", lambda: tmp_path)
    assert update_service.find_uv() is None
    uv = tmp_path / ".local" / "bin" / "uv"
    uv.parent.mkdir(parents=True)
    uv.write_text("#!/bin/sh\n")
    uv.chmod(0o755)
    assert update_service.find_uv() == str(uv)


def test_eligibility_needs_git_and_uv(repos, monkeypatch):
    """Missing git or uv makes the install ineligible."""
    _, local = repos
    monkeypatch.setattr(update_service, "find_uv", lambda: None)
    assert update_service.eligibility(local).reason == "uv is not installed"
    monkeypatch.setattr(update_service.shutil, "which", lambda name: None)
    assert update_service.eligibility(local).reason == "git is not installed"


def test_run_reports_the_failing_command(tmp_path):
    """_run raises an UpdateError with the command's output tail, and passes on success."""
    update_service._run([sys.executable, "-c", "pass"], tmp_path, "noop")
    with pytest.raises(update_service.UpdateError, match=r"step failed \(exit 3\): boom"):
        update_service._run(
            [
                sys.executable,
                "-c",
                "import sys; sys.exit('boom') if False else (print('boom', file=sys.stderr), sys.exit(3))",
            ],
            tmp_path,
            "step",
        )
    with pytest.raises(update_service.UpdateError, match="missing failed"):
        update_service._run([str(tmp_path / "no-such-binary")], tmp_path, "missing")


def test_rollback_restores_the_database_snapshot(repos, commands, tmp_path):
    """A failure after migrations started puts the pre-release snapshot back into the database."""
    _, local = repos
    import sqlite3

    db_path = tmp_path / "janus.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("create table marker (value text)")
        conn.execute("insert into marker values ('before')")
    snapshot = db_path.with_name(update_service.SNAPSHOT_NAME)
    with sqlite3.connect(db_path) as source, sqlite3.connect(snapshot) as target:
        source.backup(target)
    with sqlite3.connect(db_path) as conn:
        conn.execute("update marker set value = 'migrated'")

    assert update_service._sqlite_path() == db_path.resolve()
    update_service.rollback(local, "uv", _git(local, "rev-parse", "HEAD"), "main", db_path)

    with sqlite3.connect(db_path) as conn:
        assert conn.execute("select value from marker").fetchone() == ("before",)


def test_sqlite_path_ignores_other_databases(monkeypatch):
    """Only a file-backed SQLite database is snapshotted."""
    monkeypatch.setattr(config.settings, "database_url", "postgresql://janus@localhost/janus")
    assert update_service._sqlite_path() is None


def test_apply_update_refuses_a_tarball_without_index(repos, commands):
    """An asset whose root has no index.html is refused before git moves."""
    _, local = repos
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        info = tarfile.TarInfo("./other.txt")
        tar.addfile(info, io.BytesIO(b""))
    asset = buffer.getvalue()
    release = update_service.fetch_latest_release(client=_client(asset))
    with pytest.raises(update_service.UpdateError, match="no index.html"):
        update_service.apply_update(release, local, client=_client(asset))


def test_apply_update_refuses_a_non_checkout(tmp_path):
    """apply_update never touches a directory without .git."""
    release = update_service.Release(tag="v1.1.0", version=(1, 1, 0), asset_url=ASSET_URL, asset_digest=None)
    with pytest.raises(update_service.UpdateError, match="not a git checkout"):
        update_service.apply_update(release, tmp_path)


def test_reexec_reuses_the_console_script_or_the_interpreter(tmp_path, monkeypatch):
    """The restart re-runs the janus console script, or `python -m janus.main` without one."""
    calls = []
    monkeypatch.setattr(update_service.os, "execv", lambda path, argv: calls.append((path, argv)))
    script = tmp_path / "janus"
    script.write_text("#!/bin/sh\n")
    script.chmod(0o755)
    monkeypatch.setattr(sys, "argv", [str(script), "start", "--server-mode"])
    update_service.reexec()
    monkeypatch.setattr(sys, "argv", [str(tmp_path / "gone"), "start"])
    update_service.reexec()
    assert calls[0] == (str(script), [str(script), "start", "--server-mode"])
    assert calls[1] == (sys.executable, [sys.executable, "-m", "janus.main", "start"])


def test_worker_logs_a_failed_check(monkeypatch):
    """A failed release check is a warning, not a crash."""

    def fail():
        raise update_service.UpdateError("offline")

    logged = []
    monkeypatch.setattr(update_service, "check_for_update", fail)
    monkeypatch.setattr(update_worker.event_logger, "log", lambda *a, **k: logged.append(a))
    assert update_worker.run_update_check() is False
    assert "offline" in logged[0][2]


def test_worker_loop_checks_on_its_interval(monkeypatch):
    """The loop heartbeats and sleeps auto_update_interval_minutes between checks."""
    sleeps = []

    class Stop(Exception):
        pass

    def fake_sleep(seconds):
        sleeps.append(seconds)
        raise Stop

    monkeypatch.setattr(update_worker, "run_update_check", lambda: False)
    monkeypatch.setattr(update_worker.time, "sleep", fake_sleep)
    monkeypatch.setattr(update_worker.worker_manager, "heartbeat", lambda label: None)
    monkeypatch.setattr(config.settings, "auto_update_interval_minutes", 30)
    with pytest.raises(Stop):
        update_worker.auto_update_loop()
    assert sleeps == [1800]

    sleeps.clear()
    monkeypatch.setattr(update_worker, "run_update_check", lambda: True)
    with pytest.raises(Stop):
        update_worker.auto_update_loop()
    assert sleeps == [3600]


_RELEASE = update_service.Release(tag="v9.0.0", version=(9, 0, 0), asset_url=ASSET_URL, asset_digest=None)


@pytest.fixture
def cli(monkeypatch):
    """Run `janus update` with the update service stubbed."""
    from typer.testing import CliRunner

    from janus import main

    monkeypatch.setattr(update_service, "installed_version", lambda *a: (1, 0, 0))
    monkeypatch.setattr(update_service, "eligibility", lambda *a: update_service.Eligibility(True, "eligible"))
    return lambda *args: CliRunner().invoke(main.app, ["update", *args])


def test_cli_update_reports_no_update(cli, monkeypatch):
    """`janus update` says so when the installed version is the latest."""
    monkeypatch.setattr(update_service, "check_for_update", lambda: None)
    result = cli()
    assert result.exit_code == 0
    assert "1.0.0 is the latest release" in result.output


def test_cli_update_check_only_reports(cli, monkeypatch):
    """`janus update --check` reports the release and eligibility without installing."""
    monkeypatch.setattr(update_service, "check_for_update", lambda: _RELEASE)
    monkeypatch.setattr(update_service, "apply_update", lambda r: pytest.fail("must not install"))
    result = cli("--check")
    assert result.exit_code == 0
    assert "v9.0.0 is available" in result.output
    assert "Auto-update: eligible." in result.output


def test_cli_update_installs(cli, monkeypatch):
    """`janus update` installs the release and asks for a restart."""
    installed = []
    monkeypatch.setattr(update_service, "check_for_update", lambda: _RELEASE)
    monkeypatch.setattr(update_service, "apply_update", lambda r: installed.append(r.tag))
    result = cli()
    assert result.exit_code == 0
    assert installed == ["v9.0.0"]
    assert "Installed v9.0.0" in result.output


def test_cli_update_failures_exit_nonzero(cli, monkeypatch):
    """A failed check or install exits 1."""

    def fail(*_args):
        raise update_service.UpdateError("nope")

    monkeypatch.setattr(update_service, "check_for_update", fail)
    assert cli().exit_code == 1
    monkeypatch.setattr(update_service, "check_for_update", lambda: _RELEASE)
    monkeypatch.setattr(update_service, "apply_update", fail)
    result = cli()
    assert result.exit_code == 1


def test_sync_command_keeps_installed_extras_the_checkout_defines(tmp_path, monkeypatch):
    """Installed extras stay managed by uv sync; extras the checkout does not define are left out."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nversion = "1.0.0"\n'
        '[project.optional-dependencies]\nwhatsapp = ["neonize"]\ndocuments = ["markitdown"]\n'
    )
    installed = {"neonize", "pystray"}
    monkeypatch.setattr(update_service, "_is_installed", lambda name: name in installed)

    # pystray is installed, but this (older) checkout has no `desktop` extra for it.
    assert update_service._sync_command("uv", tmp_path) == [
        "uv",
        "sync",
        "--frozen",
        "--inexact",
        "--extra",
        "whatsapp",
    ]

    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "1.0.0"\n')
    assert update_service._sync_command("uv", tmp_path) == ["uv", "sync", "--frozen", "--inexact"]


def test_is_installed_reads_the_running_environment():
    """An extra's marker counts as installed only when its distribution metadata is present."""
    assert update_service._is_installed("httpx") is True
    assert update_service._is_installed("janus-no-such-distribution") is False
