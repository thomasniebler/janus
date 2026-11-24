"""Self-hosted auto-update: install new GitHub releases into a git checkout and restart.

A self-hosted Janus is a git clone run from its own ``.venv``. ``check_for_update`` asks the
GitHub Releases API for the latest release of ``settings.auto_update_repository``;
``apply_update`` installs it:

1. download the release's prebuilt ``janus-frontend-dist.tar.gz`` and check its SHA-256 digest,
2. snapshot the SQLite database (``scripts/pre_release_snapshot.py``),
3. fetch the release tag from the repository and move the checkout to it,
4. ``uv sync --frozen --inexact`` (plus each installed optional extra), import the new code, and
   run the migrations in a child process,
5. swap the new frontend into ``frontend/dist``.

A failure in step 3 or later rolls the checkout, the virtualenv and (once migrations ran) the
database back to where they were. After a successful install, ``request_restart`` asks the
running server to shut down, and ``janus start`` re-executes itself on the new code.

Only an eligible checkout updates (``eligibility``): a git work tree with no tracked changes,
on ``main`` or detached at a release tag, whose ``.venv`` is the running interpreter. Anything
else (the Uberspace deploy has no ``.git``, a feature branch, a pip install) is left alone.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import importlib.metadata
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import threading
import tomllib
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import httpx
from sqlalchemy.engine import make_url

from janus.config import settings

logger = logging.getLogger(__name__)

FRONTEND_ASSET_NAME = "janus-frontend-dist.tar.gz"
SNAPSHOT_NAME = "janus.pre-release.db"
_TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
_HTTP_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
_MAX_ASSET_BYTES = 100 * 2**20
_COMMAND_TIMEOUT_SECONDS = 900

REPO_ROOT = Path(__file__).resolve().parents[3]


class UpdateError(Exception):
    """An update could not be checked or installed; the install is left as it was."""


@dataclass(frozen=True)
class Release:
    """The latest GitHub release and the frontend asset it carries."""

    tag: str
    version: tuple[int, int, int]
    asset_url: str | None
    asset_digest: str | None


@dataclass(frozen=True)
class Eligibility:
    """Whether this install may update itself, and why not."""

    eligible: bool
    reason: str
    branch: str | None = None


# --- versions -----------------------------------------------------------------------------


def parse_tag(tag: str) -> tuple[int, int, int] | None:
    """Return the version of a ``vX.Y.Z`` release tag, or None for any other tag."""
    match = _TAG_RE.match(tag)
    return (int(match[1]), int(match[2]), int(match[3])) if match else None


def installed_version(repo_root: Path = REPO_ROOT) -> tuple[int, int, int]:
    """Return the version in the checkout's ``pyproject.toml``."""
    with (repo_root / "pyproject.toml").open("rb") as handle:
        raw = tomllib.load(handle)["project"]["version"]
    match = _VERSION_RE.match(raw)
    if not match:
        raise UpdateError(f"pyproject.toml version {raw!r} is not X.Y.Z")
    return (int(match[1]), int(match[2]), int(match[3]))


def format_version(version: tuple[int, int, int]) -> str:
    """Render a version tuple as ``X.Y.Z``."""
    return ".".join(str(part) for part in version)


# --- GitHub -------------------------------------------------------------------------------


def fetch_latest_release(repository: str | None = None, *, client: httpx.Client | None = None) -> Release:
    """Ask the GitHub Releases API for the latest published (non-draft, non-prerelease) release."""
    repository = repository or settings.auto_update_repository
    url = f"https://api.github.com/repos/{repository}/releases/latest"
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "janus-auto-update"}
    try:
        if client is None:
            with httpx.Client(timeout=_HTTP_TIMEOUT) as owned:
                response = owned.get(url, headers=headers)
        else:
            response = client.get(url, headers=headers)
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise UpdateError(f"could not read the latest release of {repository}: {exc}") from exc

    tag = str(payload.get("tag_name") or "")
    version = parse_tag(tag)
    if version is None:
        raise UpdateError(f"latest release tag {tag!r} is not vX.Y.Z")
    asset = next((a for a in payload.get("assets") or [] if a.get("name") == FRONTEND_ASSET_NAME), None)
    return Release(
        tag=tag,
        version=version,
        asset_url=asset.get("browser_download_url") if asset else None,
        asset_digest=asset.get("digest") if asset else None,
    )


# --- eligibility --------------------------------------------------------------------------


def _git(repo_root: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo_root, capture_output=True, text=True, timeout=_COMMAND_TIMEOUT_SECONDS
    )
    if check and result.returncode != 0:
        raise UpdateError(f"git {' '.join(args)} failed: {(result.stderr or result.stdout).strip()}")
    return result.stdout.strip()


def find_uv() -> str | None:
    """Return the uv executable, also looking in ``~/.local/bin`` (uv's default install)."""
    found = shutil.which("uv")
    if found:
        return found
    candidate = Path.home() / ".local" / "bin" / "uv"
    return str(candidate) if candidate.is_file() and os.access(candidate, os.X_OK) else None


def eligibility(repo_root: Path = REPO_ROOT) -> Eligibility:
    """Decide whether this install may update itself."""
    if not settings.auto_update_enabled:
        return Eligibility(False, "auto_update_enabled is off")
    if not (repo_root / ".git").exists():
        return Eligibility(False, f"{repo_root} is not a git checkout")
    if shutil.which("git") is None:
        return Eligibility(False, "git is not installed")
    if find_uv() is None:
        return Eligibility(False, "uv is not installed")
    if Path(sys.prefix).resolve() != (repo_root / ".venv").resolve():
        return Eligibility(False, f"Janus is not running from {repo_root / '.venv'}")
    try:
        if _git(repo_root, "status", "--porcelain", "--untracked-files=no"):
            return Eligibility(False, "the checkout has uncommitted changes")
        branch = _git(repo_root, "symbolic-ref", "--quiet", "--short", "HEAD", check=False) or None
        if branch is None:
            tags = _git(repo_root, "tag", "--points-at", "HEAD").split()
            if not any(parse_tag(tag) for tag in tags):
                return Eligibility(False, "HEAD is detached but not at a release tag")
        elif branch != "main":
            return Eligibility(False, f"the checkout is on branch {branch!r}, not main or a release tag")
    except (UpdateError, OSError, subprocess.TimeoutExpired) as exc:
        return Eligibility(False, f"could not inspect the checkout: {exc}")
    return Eligibility(True, "eligible", branch=branch)


def check_for_update(repo_root: Path = REPO_ROOT, *, client: httpx.Client | None = None) -> Release | None:
    """Return the latest release when it is newer than the installed version, else None."""
    release = fetch_latest_release(client=client)
    return release if release.version > installed_version(repo_root) else None


# --- install ------------------------------------------------------------------------------


def _download_frontend(release: Release, dest_dir: Path, *, client: httpx.Client | None = None) -> Path:
    """Download and unpack the release's frontend build into ``dest_dir``; return the dist dir."""
    if not release.asset_url:
        raise UpdateError(f"release {release.tag} has no {FRONTEND_ASSET_NAME} asset (yet)")
    digest = (release.asset_digest or "").removeprefix("sha256:")
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise UpdateError(f"release {release.tag}'s {FRONTEND_ASSET_NAME} has no SHA-256 digest")

    archive = dest_dir / FRONTEND_ASSET_NAME
    sha = hashlib.sha256()
    size = 0
    try:
        owned = client is None
        http = client or httpx.Client(timeout=_HTTP_TIMEOUT, follow_redirects=True)
        try:
            with http.stream("GET", release.asset_url, headers={"User-Agent": "janus-auto-update"}) as response:
                response.raise_for_status()
                with archive.open("wb") as handle:
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > _MAX_ASSET_BYTES:
                            raise UpdateError(f"{FRONTEND_ASSET_NAME} is larger than {_MAX_ASSET_BYTES} bytes")
                        sha.update(chunk)
                        handle.write(chunk)
        finally:
            if owned:
                http.close()
    except httpx.HTTPError as exc:
        raise UpdateError(f"could not download {FRONTEND_ASSET_NAME}: {exc}") from exc
    if sha.hexdigest() != digest:
        raise UpdateError(f"{FRONTEND_ASSET_NAME} does not match its published SHA-256 digest")

    dist = dest_dir / "dist"
    dist.mkdir()
    try:
        with tarfile.open(archive, "r:gz") as tar:
            tar.extractall(dist, filter="data")
    except (tarfile.TarError, OSError) as exc:
        raise UpdateError(f"could not unpack {FRONTEND_ASSET_NAME}: {exc}") from exc
    if not (dist / "index.html").is_file():
        raise UpdateError(f"{FRONTEND_ASSET_NAME} has no index.html at its root")
    return dist


def _run(command: list[str], cwd: Path, what: str) -> None:
    try:
        result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=_COMMAND_TIMEOUT_SECONDS)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise UpdateError(f"{what} failed: {exc}") from exc
    if result.returncode != 0:
        tail = (result.stderr or result.stdout).strip().splitlines()[-20:]
        raise UpdateError(f"{what} failed (exit {result.returncode}): " + "\n".join(tail))


#: Optional extras -> a distribution that is installed exactly when the extra is.
_EXTRA_MARKERS = {"desktop": "pystray", "documents": "markitdown", "whatsapp": "neonize"}


def _sync_command(uv: str, repo_root: Path) -> list[str]:
    """``uv sync`` that keeps managing (and upgrading) the optional extras already installed.

    ``--inexact`` alone would leave an installed extra's packages untouched, so they would
    never pick up a new lockfile's versions. Only extras the checked-out ``pyproject.toml``
    defines are passed, since uv rejects an unknown extra (a rollback to an older release).
    """
    try:
        defined = tomllib.loads((repo_root / "pyproject.toml").read_text())["project"]["optional-dependencies"]
    except (OSError, KeyError, tomllib.TOMLDecodeError):
        defined = {}
    command = [uv, "sync", "--frozen", "--inexact"]
    for extra, marker in sorted(_EXTRA_MARKERS.items()):
        if extra in defined and _is_installed(marker):
            command += ["--extra", extra]
    return command


def _is_installed(distribution: str) -> bool:
    try:
        importlib.metadata.distribution(distribution)
    except importlib.metadata.PackageNotFoundError:
        return False
    return True


def release_source_url() -> str:
    """The git URL release tags are fetched from (the configured repository, not ``origin``)."""
    return f"https://github.com/{settings.auto_update_repository}.git"


def _sqlite_path() -> Path | None:
    url = make_url(settings.database_url)
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
        return None
    return Path(url.database).expanduser().resolve()


def _restore_snapshot(db_path: Path) -> None:
    snapshot = db_path.with_name(SNAPSHOT_NAME)
    source = sqlite3.connect(f"file:{snapshot}?mode=ro", uri=True)
    target = sqlite3.connect(db_path)
    try:
        source.backup(target)
    finally:
        source.close()
        target.close()


def _swap_frontend(new_dist: Path, repo_root: Path) -> None:
    current = repo_root / "frontend" / "dist"
    staged = repo_root / "frontend" / ".dist-update"
    previous = repo_root / "frontend" / ".dist-previous"
    for leftover in (staged, previous):
        shutil.rmtree(leftover, ignore_errors=True)
    shutil.copytree(new_dist, staged)
    if current.exists():
        current.rename(previous)
    staged.rename(current)
    # .dist-previous stays until the restarted release is confirmed (services/update_boot.py).


@contextlib.contextmanager
def _install_lock(repo_root: Path) -> Iterator[None]:
    """Hold ``.git/janus-update.lock`` so the worker and ``janus update`` never install at once."""
    with (repo_root / ".git" / "janus-update.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise UpdateError("another update of this checkout is in progress") from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


@dataclass(frozen=True)
class Installed:
    """What an install replaced, so a failed restart can return to it."""

    tag: str
    previous_commit: str
    branch: str | None


def apply_update(release: Release, repo_root: Path = REPO_ROOT, *, client: httpx.Client | None = None) -> Installed:
    """Install ``release`` into the checkout, rolling back on failure. Does not restart."""
    if not (repo_root / ".git").is_dir():
        raise UpdateError(f"not updating: {repo_root} is not a git checkout")
    with _install_lock(repo_root):
        return _apply_update_locked(release, repo_root, client)


def _apply_update_locked(release: Release, repo_root: Path, client: httpx.Client | None) -> Installed:
    status = eligibility(repo_root)
    if not status.eligible:
        raise UpdateError(f"not updating: {status.reason}")
    uv = find_uv()
    assert uv is not None  # eligibility checked it
    python = sys.executable
    db_path = _sqlite_path()
    # The children run where this process runs, so they load the same .env and resolve a
    # relative database_url to the same file; only uv sync works in the checkout itself.
    here = Path.cwd()

    with tempfile.TemporaryDirectory(prefix="janus-update-") as workdir:
        new_dist = _download_frontend(release, Path(workdir), client=client)
        if db_path is not None and db_path.is_file():
            _run([python, str(repo_root / "scripts" / "pre_release_snapshot.py")], here, "database snapshot")

        old_commit = _git(repo_root, "rev-parse", "HEAD")
        _git(repo_root, "fetch", "--no-tags", release_source_url(), f"+refs/tags/{release.tag}:refs/tags/{release.tag}")
        migrated = False
        try:
            if status.branch == "main":
                _git(repo_root, "merge", "--ff-only", release.tag)
            else:
                _git(repo_root, "checkout", "--detach", release.tag)
            _run(_sync_command(uv, repo_root), repo_root, "uv sync")
            _run([python, "-c", "import janus.main"], here, "import check")
            migrated = True
            _run(
                [python, "-c", "from janus import database; database.ensure_schema_up_to_date()"],
                here,
                "database migration",
            )
            _swap_frontend(new_dist, repo_root)
        except Exception:
            logger.exception("Auto-update to %s failed; rolling back to %s", release.tag, old_commit)
            rollback(repo_root, uv, old_commit, status.branch, db_path if migrated else None)
            raise
    logger.info("Installed Janus %s; restart pending", release.tag)
    return Installed(release.tag, old_commit, status.branch)


def rollback(repo_root: Path, uv: str, old_commit: str, branch: str | None, db_path: Path | None) -> None:
    """Best-effort return to ``old_commit``; logs (never raises) whatever could not be undone."""
    steps: list[tuple[str, Callable[[], object]]] = [
        (
            "checkout",
            lambda: (
                _git(repo_root, "reset", "--keep", old_commit)
                if branch == "main"
                else _git(repo_root, "checkout", "--detach", old_commit)
            ),
        ),
        ("uv sync", lambda: _run(_sync_command(uv, repo_root), repo_root, "uv sync")),
    ]
    if db_path is not None and db_path.with_name(SNAPSHOT_NAME).is_file():
        steps.append(("database restore", lambda: _restore_snapshot(db_path)))
    for name, step in steps:
        try:
            step()
        except Exception:
            logger.exception("Auto-update rollback step '%s' failed; fix the install by hand", name)


# --- restart ------------------------------------------------------------------------------

_restart_requested = threading.Event()
_restart_handler: Callable[[], None] | None = None


def set_restart_handler(handler: Callable[[], None] | None) -> None:
    """Register how the running server is asked to shut down (``janus start`` sets this)."""
    global _restart_handler
    _restart_handler = handler


def request_restart() -> bool:
    """Ask the running server to stop so ``janus start`` re-executes; False without a server."""
    if _restart_handler is None:
        return False
    _restart_requested.set()
    _restart_handler()
    return True


def restart_requested() -> bool:
    """Whether an installed update is waiting for the process to re-execute."""
    return _restart_requested.is_set()


def reexec() -> None:
    """Replace this process with a fresh ``janus`` on the newly installed code."""
    argv0 = Path(sys.argv[0])
    if argv0.is_file() and os.access(argv0, os.X_OK):
        os.execv(str(argv0), sys.argv)
    else:
        os.execv(sys.executable, [sys.executable, "-m", "janus.main", *sys.argv[1:]])
