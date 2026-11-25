"""Confirm or undo an auto-update across its restart (see ``services/update_service.py``).

Before the auto-update worker restarts Janus on a newly installed release, ``write_pending``
records the release, what it replaced (the commit, or a tool install's previous wheel), and the
process id in ``janus-update-pending.json`` (in ``.git``, or the state directory of a tool
install; ``update_service.state_dir``). ``os.execv`` keeps the process id, so only the re-executed
``janus start`` acts on the record:

- once its server has started, ``attach_server``'s watcher confirms the release: the record
  and the previous frontend build (``frontend/.dist-previous``) are removed;
- if ``janus start`` ends before that (an exception, ``typer.Exit``, a server that never
  started), ``recover_on_failed_boot`` rolls the checkout, the virtualenv and the database
  snapshot back (a tool install reinstalls its previous wheel), restores the previous frontend,
  remembers the tag in ``janus-update-skip`` so the worker does not install it again, and re-executes Janus on
  the previous release.

The recovery runs on the new release's code, so a release that breaks this module itself cannot
recover; that is why the import check and migrations run before the restart.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import shutil
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from janus import event_logger, models
from janus.services import update_service, update_tool_install

logger = logging.getLogger(__name__)

_SOURCE = "AutoUpdate"
_CONFIRM_TIMEOUT_SECONDS = 600
_confirmed = threading.Event()


def _pending_path(repo_root: Path) -> Path:
    return update_service.state_dir(repo_root) / "janus-update-pending.json"


def _skip_path(repo_root: Path) -> Path:
    return update_service.state_dir(repo_root) / "janus-update-skip"


def write_pending(installed: update_service.Installed, repo_root: Path = update_service.REPO_ROOT) -> None:
    """Record the install the next boot of this process must confirm or undo."""
    record = {
        "tag": installed.tag,
        "previous_commit": installed.previous_commit,
        "branch": installed.branch,
        "kind": installed.kind,
        "previous_wheel": installed.previous_wheel,
        "pid": os.getpid(),
    }
    _pending_path(repo_root).write_text(json.dumps(record))


def _read_pending(repo_root: Path) -> dict[str, Any] | None:
    try:
        record = json.loads(_pending_path(repo_root).read_text())
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) and record.get("pid") == os.getpid() else None


def skipped_tag(repo_root: Path = update_service.REPO_ROOT) -> str | None:
    """The release tag that failed to start here and is not installed again."""
    try:
        return _skip_path(repo_root).read_text().strip() or None
    except OSError:
        return None


def confirm(repo_root: Path = update_service.REPO_ROOT) -> None:
    """The running release started: forget the pending record and the previous frontend."""
    _confirmed.set()
    if _read_pending(repo_root) is None:
        return
    _pending_path(repo_root).unlink(missing_ok=True)
    shutil.rmtree(repo_root / "frontend" / ".dist-previous", ignore_errors=True)
    event_logger.log(models.EventType.SUCCESS, _SOURCE, "The updated release started and is confirmed.")


def _watch_started(server: Any, repo_root: Path) -> None:
    deadline = time.monotonic() + _CONFIRM_TIMEOUT_SECONDS
    while time.monotonic() < deadline and not server.should_exit:
        if server.started:
            confirm(repo_root)
            return
        time.sleep(0.5)


def attach_server(server: Any, repo_root: Path = update_service.REPO_ROOT) -> None:
    """Let an installed update stop ``server``, and confirm a pending update once it started."""
    update_service.set_restart_handler(lambda: setattr(server, "should_exit", True))
    threading.Thread(target=_watch_started, args=(server, repo_root), daemon=True, name="update-confirm").start()


def recover(record: dict[str, Any], repo_root: Path = update_service.REPO_ROOT) -> None:
    """Return to the release ``record`` replaced, and never install its tag again here."""
    tag = str(record.get("tag"))
    db_path = update_service._sqlite_path()
    kind = record.get("kind") or update_service.CHECKOUT
    if kind == update_service.CHECKOUT:
        uv = update_service.find_uv() or "uv"
        update_service.rollback(repo_root, uv, str(record["previous_commit"]), record.get("branch"), db_path)
    else:
        update_tool_install.rollback(kind, str(record["previous_wheel"]), db_path)
    previous = repo_root / "frontend" / ".dist-previous"
    if previous.is_dir():
        current = repo_root / "frontend" / "dist"
        shutil.rmtree(current, ignore_errors=True)
        previous.rename(current)
    _skip_path(repo_root).write_text(tag)
    _pending_path(repo_root).unlink(missing_ok=True)
    event_logger.log(
        models.EventType.ERROR,
        _SOURCE,
        f"Janus {tag} did not start; rolled back to the previous release, which will not update to {tag} again.",
    )


@contextlib.contextmanager
def recover_on_failed_boot(repo_root: Path = update_service.REPO_ROOT) -> Iterator[None]:
    """Wrap ``janus start``: re-execute after an install, and undo an update that did not start."""
    try:
        yield
    finally:
        record = _read_pending(repo_root)
        if update_service.restart_requested():
            logger.info("Restarting Janus on the newly installed release.")
            update_service.reexec()
        elif record is not None and not _confirmed.is_set():
            logger.error("Janus %s did not start; rolling back.", record.get("tag"))
            recover(record, repo_root)
            update_service.reexec()
