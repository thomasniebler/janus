"""Auto-update across its restart: confirm a release that started, undo one that did not."""

import json
import threading
from types import SimpleNamespace

import pytest

from janus.services import update_boot, update_service
from janus.workers import update as update_worker


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A checkout layout with a .git dir, the new frontend, and the previous one kept aside."""
    (tmp_path / ".git").mkdir()
    (tmp_path / "frontend" / "dist").mkdir(parents=True)
    (tmp_path / "frontend" / "dist" / "index.html").write_text("new")
    (tmp_path / "frontend" / ".dist-previous").mkdir()
    (tmp_path / "frontend" / ".dist-previous" / "index.html").write_text("old")
    monkeypatch.setattr(update_boot, "_confirmed", threading.Event())
    monkeypatch.setattr(update_service, "_restart_requested", threading.Event())
    monkeypatch.setattr(update_boot.event_logger, "log", lambda *a, **k: None)
    return tmp_path


@pytest.fixture
def actions(monkeypatch):
    """Record rollbacks and re-execs instead of doing them."""
    done = []
    monkeypatch.setattr(update_service, "rollback", lambda *args: done.append(("rollback", args[2], args[3])))
    monkeypatch.setattr(update_service, "reexec", lambda: done.append(("reexec",)))
    monkeypatch.setattr(update_service, "_sqlite_path", lambda: None)
    return done


def _pending(repo):
    update_boot.write_pending(update_service.Installed("v2.0.0", "abc123", "main"), repo)


def test_write_pending_is_only_seen_by_this_process(repo):
    """The record names this process id, which os.execv keeps; any other process ignores it."""
    _pending(repo)
    record = update_boot._read_pending(repo)
    assert record["tag"] == "v2.0.0" and record["previous_commit"] == "abc123"

    path = repo / ".git" / "janus-update-pending.json"
    path.write_text(json.dumps({**record, "pid": -1}))
    assert update_boot._read_pending(repo) is None
    path.write_text("not json")
    assert update_boot._read_pending(repo) is None


def test_confirm_removes_the_record_and_the_previous_frontend(repo):
    """A release that started is kept for good."""
    _pending(repo)
    update_boot.confirm(repo)
    assert not (repo / ".git" / "janus-update-pending.json").exists()
    assert not (repo / "frontend" / ".dist-previous").exists()
    assert (repo / "frontend" / "dist" / "index.html").read_text() == "new"


def test_failed_boot_rolls_back_skips_the_tag_and_restarts(repo, actions):
    """Janus start ending before the server started undoes the update and re-executes."""
    _pending(repo)
    with pytest.raises(RuntimeError), update_boot.recover_on_failed_boot(repo):
        raise RuntimeError("startup crashed")

    assert actions == [("rollback", "abc123", "main"), ("reexec",)]
    assert (repo / "frontend" / "dist" / "index.html").read_text() == "old"
    assert update_boot.skipped_tag(repo) == "v2.0.0"
    assert not (repo / ".git" / "janus-update-pending.json").exists()


def test_confirmed_boot_is_left_alone(repo, actions):
    """After confirmation, a normal shutdown does nothing."""
    _pending(repo)
    update_boot.confirm(repo)
    with update_boot.recover_on_failed_boot(repo):
        pass
    assert actions == []


def test_boot_without_a_pending_update_is_left_alone(repo, actions):
    """An ordinary start that fails is not an update failure."""
    with pytest.raises(RuntimeError), update_boot.recover_on_failed_boot(repo):
        raise RuntimeError("port in use")
    assert actions == []
    assert update_boot.skipped_tag(repo) is None


def test_installed_update_re_executes(repo, actions):
    """After the worker installed a release, janus start re-executes instead of recovering."""
    _pending(repo)
    update_service._restart_requested.set()
    with update_boot.recover_on_failed_boot(repo):
        pass
    assert actions == [("reexec",)]


def test_attach_server_confirms_once_started_and_wires_the_restart(repo, monkeypatch):
    """The watcher confirms as soon as uvicorn reports started; a restart request stops it."""
    _pending(repo)
    server = SimpleNamespace(started=True, should_exit=False)
    update_boot._watch_started(server, repo)
    assert update_boot._confirmed.is_set()
    assert not (repo / ".git" / "janus-update-pending.json").exists()

    monkeypatch.setattr(update_service, "_restart_handler", None)
    started = []
    monkeypatch.setattr(update_boot.threading, "Thread", lambda **kw: SimpleNamespace(start=lambda: started.append(kw)))
    update_boot.attach_server(server, repo)
    assert started[0]["target"] is update_boot._watch_started
    assert update_service.request_restart() is True
    assert server.should_exit is True
    update_service.set_restart_handler(None)


def test_watcher_gives_up_when_the_server_stops(repo):
    """A server that is shutting down before it started is never confirmed."""
    update_boot._watch_started(SimpleNamespace(started=False, should_exit=True), repo)
    assert not update_boot._confirmed.is_set()


def test_worker_skips_a_tag_that_failed_to_start(monkeypatch):
    """The worker never reinstalls a release that was rolled back after failing to start."""
    release = update_service.Release(tag="v2.0.0", version=(2, 0, 0), asset_url=None, asset_digest=None)
    monkeypatch.setattr(update_service, "check_for_update", lambda: release)
    monkeypatch.setattr(update_boot, "skipped_tag", lambda: "v2.0.0")
    monkeypatch.setattr(update_service, "apply_update", lambda r: pytest.fail("must not install"))
    assert update_worker.run_update_check() is False


def test_start_if_eligible(monkeypatch):
    """The loop only starts on an eligible install."""
    started = []
    monkeypatch.setattr(update_worker.worker_manager, "start", lambda label, *a, **k: started.append(label))
    monkeypatch.setattr(update_service, "eligibility", lambda: update_service.Eligibility(False, "not a git checkout"))
    assert update_worker.start_if_eligible() is False
    monkeypatch.setattr(update_service, "eligibility", lambda: update_service.Eligibility(True, "eligible"))
    assert update_worker.start_if_eligible() is True
    assert started == [update_worker.WORKER_LABEL]
