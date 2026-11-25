"""Auto-update of a ``uv tool`` or ``pipx`` install of the release wheel (JAN-398).

``update_service.apply_update`` hands a tool install (see ``update_service.install_kind``) to
``apply_locked``, under the same install lock:

1. download the release's wheel and check its SHA-256 digest,
2. keep the running release's wheel in ``<state dir>/rollback/``, so a rollback needs no network,
3. snapshot the SQLite database to ``janus.pre-release.db`` with SQLite's online backup,
4. reinstall the tool from the new wheel (``uv tool install --force`` / ``pipx install --force``)
   with the optional extras installed now,
5. run the same import check and migrations as a checkout, in child processes.

A failure in step 4 or later reinstalls the kept wheel and, once migrations ran, restores the
snapshot (``rollback``); ``update_boot`` calls the same rollback when the new release does not
start. The restart is the checkout's ``os.execv`` of the ``janus`` entry point.
"""

from __future__ import annotations

import logging
import sqlite3
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

import httpx

from janus.services import update_service as base

logger = logging.getLogger(__name__)

ROLLBACK_WHEEL_DIR = "rollback"


def install_command(kind: str, tool: str, wheel: Path) -> list[str]:
    """Reinstall the tool from ``wheel``, keeping the optional extras installed now."""
    extras = ",".join(extra for extra, marker in sorted(base.EXTRA_MARKERS.items()) if base._is_installed(marker))
    if kind == base.UV_TOOL:
        spec = f"janus[{extras}] @ {wheel.as_uri()}" if extras else str(wheel)
        python = f"{sys.version_info.major}.{sys.version_info.minor}"
        return [tool, "tool", "install", "--force", "--python", python, spec]
    return [tool, "install", "--force", f"{wheel}[{extras}]" if extras else str(wheel)]


def _snapshot_database(db_path: Path) -> None:
    """Write ``janus.pre-release.db`` next to the database with SQLite's online backup."""
    snapshot = db_path.with_name(base.SNAPSHOT_NAME)
    snapshot.unlink(missing_ok=True)
    try:
        source = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        target = sqlite3.connect(snapshot)
        try:
            source.backup(target)
        finally:
            source.close()
            target.close()
    except sqlite3.Error as exc:
        snapshot.unlink(missing_ok=True)
        raise base.UpdateError(f"database snapshot failed: {exc}") from exc


def _keep_rollback_wheel(repo_root: Path, *, client: httpx.Client | None) -> Path:
    """Download the running release's wheel into the state directory, for a later rollback."""
    current_tag = f"v{base.format_version(base.installed_version(repo_root))}"
    directory = base.state_dir(repo_root) / ROLLBACK_WHEEL_DIR
    directory.mkdir(parents=True, exist_ok=True)
    current = base.fetch_release(current_tag, client=client)
    kept = directory / current.wheel_name
    if kept.is_file():
        return kept
    for stale in directory.glob("*.whl"):
        stale.unlink()
    try:
        return base._download_asset(
            current.wheel_url, current.wheel_digest, current.wheel_name, directory, client=client
        )
    except base.UpdateError as exc:
        raise base.UpdateError(f"cannot keep {current_tag} for a rollback: {exc}") from exc


def apply_locked(release: base.Release, repo_root: Path, kind: str, client: httpx.Client | None) -> base.Installed:
    """Install ``release`` into this tool install; the caller holds the install lock."""
    status = base.eligibility(repo_root)
    if not status.eligible:
        raise base.UpdateError(f"not updating: {status.reason}")
    tool = base.tool_executable(kind)
    assert tool is not None  # eligibility checked it
    python = sys.executable
    db_path = base._sqlite_path()
    here = Path.cwd()

    with tempfile.TemporaryDirectory(prefix="janus-update-") as workdir:
        wheel = base._download_asset(
            release.wheel_url, release.wheel_digest, release.wheel_name, Path(workdir), client=client
        )
        previous = _keep_rollback_wheel(repo_root, client=client)
        if db_path is not None and db_path.is_file():
            _snapshot_database(db_path)
        migrated = False
        try:
            base._run(install_command(kind, tool, wheel), here, f"{kind} install")
            base._run([python, "-c", "import janus.main"], here, "import check")
            migrated = True
            base._run(
                [python, "-c", "from janus import database; database.ensure_schema_up_to_date()"],
                here,
                "database migration",
            )
        except Exception:
            logger.exception("Auto-update to %s failed; reinstalling %s", release.tag, previous.name)
            rollback(kind, str(previous), db_path if migrated else None)
            raise
    logger.info("Installed Janus %s; restart pending", release.tag)
    return base.Installed(release.tag, "", None, kind=kind, previous_wheel=str(previous))


def rollback(kind: str, previous_wheel: str, db_path: Path | None) -> None:
    """Best-effort reinstall of ``previous_wheel``; logs (never raises) what could not be undone."""
    steps: list[tuple[str, Callable[[], object]]] = []
    tool = base.tool_executable(kind)
    if tool is not None:
        command = install_command(kind, tool, Path(previous_wheel))
        steps.append((f"{kind} install", lambda: base._run(command, Path.cwd(), "reinstall")))
    else:
        logger.error("Auto-update rollback: %s is gone; reinstall %s by hand", kind, previous_wheel)
    if db_path is not None and db_path.with_name(base.SNAPSHOT_NAME).is_file():
        steps.append(("database restore", lambda: base._restore_snapshot(db_path)))
    for name, step in steps:
        try:
            step()
        except Exception:
            logger.exception("Auto-update rollback step '%s' failed; fix the install by hand", name)
