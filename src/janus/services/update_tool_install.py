"""Auto-update of a ``uv tool`` or ``pipx`` install of the release wheel (JAN-398).

``update_service.apply_update`` hands a tool install (see ``update_service.install_kind``) to
``apply_locked``, under the same install lock:

1. download the release's wheel and check its SHA-256 digest,
2. keep the running release's wheel in ``<state dir>/rollback/``, so a rollback needs no network,
3. snapshot the SQLite database to ``janus.pre-release.db`` with SQLite's online backup,
4. reinstall the tool from the new wheel (``uv tool install --force`` / ``pipx install --force``)
   with the optional extras installed now, and the extra packages added to the environment
   (``uv tool install --with`` / ``pipx inject``) passed back as ``--with`` / ``--preinstall``
   (JAN-409),
5. run the same import check and migrations as a checkout, in child processes.

A failure in step 4 or later reinstalls the kept wheel and, once migrations ran, restores the
snapshot (``rollback``); ``update_boot`` calls the same rollback when the new release does not
start. The restart is the checkout's ``os.execv`` of the ``janus`` entry point.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import sys
import tempfile
import tomllib
from collections.abc import Callable
from pathlib import Path
from urllib.parse import parse_qs, urlsplit, urlunsplit

import httpx

from janus.services import update_service as base

logger = logging.getLogger(__name__)

ROLLBACK_WHEEL_DIR = "rollback"
#: The uv-receipt.toml requirement keys ``_uv_with_argument`` can rebuild a requirement from.
_UV_RECEIPT_KEYS = frozenset({"name", "extras", "specifier", "marker", "url", "git", "directory", "path", "editable"})


def install_command(kind: str, tool: str, wheel: Path) -> list[str]:
    """Reinstall the tool from ``wheel``, keeping the optional extras and extra packages installed now."""
    extras = ",".join(extra for extra, marker in sorted(base.EXTRA_MARKERS.items()) if base._is_installed(marker))
    if kind == base.UV_TOOL:
        spec = f"{base.DISTRIBUTION}[{extras}] @ {wheel.as_uri()}" if extras else str(wheel)
        python = f"{sys.version_info.major}.{sys.version_info.minor}"
        return [tool, "tool", "install", "--force", "--python", python, *_uv_with_requirements(), spec]
    command = [tool, "install", "--force"]
    suffix = _pipx_suffix()
    if suffix:  # `pipx install --suffix` names the environment; without it pipx targets another one
        command.append(f"--suffix={suffix}")
    command += [f"--preinstall={package}" for package in _pipx_injected_packages()]
    return [*command, f"{wheel}[{extras}]" if extras else str(wheel)]


def _uv_with_requirements(prefix: str | None = None) -> list[str]:
    """The ``--with`` / ``--with-editable`` arguments of this uv tool environment, from its receipt.

    Without them a reinstall drops packages the user added next to Janus (for example PyGObject
    for the tray icon on Wayland). An entry whose source this cannot rebuild is left out (and
    logged) rather than passed as a bare name, which would resolve to another package.
    """
    try:
        receipt = tomllib.loads((Path(prefix or sys.prefix) / "uv-receipt.toml").read_text())
        entries = receipt["tool"]["requirements"]
    except (OSError, ValueError, KeyError, TypeError):
        return []
    arguments = []
    for entry in entries if isinstance(entries, list) else []:
        name = entry.get("name") if isinstance(entry, dict) else None
        if not isinstance(name, str) or name == base.DISTRIBUTION:
            continue
        argument = _uv_with_argument(name, entry)
        if argument is None:
            logger.warning("Auto-update cannot keep %s (unsupported uv receipt entry %s)", name, entry)
        else:
            arguments.append(argument)
    return arguments


def _uv_with_argument(name: str, entry: dict) -> str | None:
    """One receipt requirement as a ``--with`` (or ``--with-editable``) argument, or None."""
    if set(entry) - _UV_RECEIPT_KEYS:
        return None
    extras = entry.get("extras") or []
    requirement = f"{name}[{','.join(extras)}]" if extras else name
    if entry.get("editable"):
        return f"--with-editable={entry['editable']}"
    if entry.get("git"):
        git = urlsplit(entry["git"])
        query = parse_qs(git.query)
        ref = next((query[key][0] for key in ("rev", "tag", "branch") if key in query), "")
        url = urlunsplit(git._replace(query="")) + (f"@{ref}" if ref else "")
        if "subdirectory" in query:
            url += f"#subdirectory={query['subdirectory'][0]}"
        requirement += f" @ git+{url}"
    elif entry.get("directory") or entry.get("path"):
        requirement += f" @ {Path(entry.get('directory') or entry['path']).as_uri()}"
    elif entry.get("url"):
        requirement += f" @ {entry['url']}"
    elif entry.get("specifier"):
        requirement += str(entry["specifier"])
    if entry.get("marker"):
        requirement += f" ; {entry['marker']}"
    return f"--with={requirement}"


def _pipx_metadata(prefix: str | None = None) -> dict:
    """This pipx environment's ``pipx_metadata.json`` (empty when unreadable)."""
    try:
        metadata = json.loads((Path(prefix or sys.prefix) / "pipx_metadata.json").read_text())
    except (OSError, ValueError):
        return {}
    return metadata if isinstance(metadata, dict) else {}


def _pipx_injected_packages(prefix: str | None = None) -> list[str]:
    """The packages ``pipx inject`` added to this environment, which ``pipx install --force`` drops."""
    injected = _pipx_metadata(prefix).get("injected_packages")
    if not isinstance(injected, dict):
        return []
    packages = []
    for name, info in injected.items():
        package = info.get("package_or_url") if isinstance(info, dict) else None
        packages.append(package if isinstance(package, str) and package else name)
    return packages


def _pipx_suffix(prefix: str | None = None) -> str:
    """The ``--suffix`` this pipx environment was installed with (empty for none)."""
    main_package = _pipx_metadata(prefix).get("main_package")
    return str(main_package.get("suffix") or "") if isinstance(main_package, dict) else ""


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
    expected = (current.wheel_digest or "").removeprefix("sha256:")
    if kept.is_file() and hashlib.sha256(kept.read_bytes()).hexdigest() == expected:
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
