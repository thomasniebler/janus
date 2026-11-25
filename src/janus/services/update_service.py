"""Self-hosted auto-update: install new GitHub releases and restart.

Janus updates itself in two kinds of install (``install_kind``): a git clone run from its own
``.venv``, or the release wheel installed with ``uv tool install`` or ``pipx install``.
``check_for_update`` asks the GitHub Releases API for the latest release of
``settings.auto_update_repository``; in a checkout, ``apply_update`` installs it:

1. download the release's prebuilt ``janus-frontend-dist.tar.gz`` and check its SHA-256 digest,
2. snapshot the SQLite database (``scripts/pre_release_snapshot.py``),
3. fetch the release tag from the repository and move the checkout to it,
4. ``uv sync --frozen --inexact`` (plus each installed optional extra), import the new code, and
   run the migrations in a child process,
5. swap the new frontend into ``frontend/dist``.

A failure in step 3 or later rolls the checkout, the virtualenv and (once migrations ran) the
database back to where they were.

A tool install is updated by ``update_tool_install.py`` instead.

After a successful install, ``request_restart`` asks the running server to shut down, and
``janus start`` re-executes itself on the new code.

Only an eligible install updates (``eligibility``): a git work tree with no tracked changes,
on ``main`` or detached at a release tag, whose ``.venv`` is the running interpreter; or a
``uv tool`` / ``pipx`` install whose installer is on the PATH. Anything else (the Uberspace
deploy has no ``.git``, a feature branch, a plain pip install) is left alone.
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

from janus.config import get_state_dir, settings

logger = logging.getLogger(__name__)

FRONTEND_ASSET_NAME = "janus-frontend-dist.tar.gz"
SNAPSHOT_NAME = "janus.pre-release.db"
_TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
_HTTP_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
_MAX_ASSET_BYTES = 100 * 2**20
_COMMAND_TIMEOUT_SECONDS = 900

REPO_ROOT = Path(__file__).resolve().parents[3]
#: The distribution name on PyPI and in wheel file names (the command and package are ``janus``).
DISTRIBUTION = "janus-assistant"

#: Install kinds (``install_kind``).
CHECKOUT = "checkout"
UV_TOOL = "uv tool"
PIPX = "pipx"


class UpdateError(Exception):
    """An update could not be checked or installed; the install is left as it was."""


@dataclass(frozen=True)
class Release:
    """The latest GitHub release and the frontend asset it carries."""

    tag: str
    version: tuple[int, int, int]
    asset_url: str | None
    asset_digest: str | None
    wheel_url: str | None = None
    wheel_digest: str | None = None

    @property
    def wheel_name(self) -> str:
        """File name of the release's wheel asset."""
        return wheel_name(self.version)


@dataclass(frozen=True)
class Eligibility:
    """Whether this install may update itself, and why not."""

    eligible: bool
    reason: str
    branch: str | None = None
    kind: str | None = None


# --- versions -----------------------------------------------------------------------------


def parse_tag(tag: str) -> tuple[int, int, int] | None:
    """Return the version of a ``vX.Y.Z`` release tag, or None for any other tag."""
    match = _TAG_RE.match(tag)
    return (int(match[1]), int(match[2]), int(match[3])) if match else None


def installed_version(repo_root: Path = REPO_ROOT) -> tuple[int, int, int]:
    """Return the checkout's ``pyproject.toml`` version, or the installed distribution's."""
    pyproject = repo_root / "pyproject.toml"
    if pyproject.is_file():
        with pyproject.open("rb") as handle:
            raw = tomllib.load(handle)["project"]["version"]
    else:
        raw = importlib.metadata.version(DISTRIBUTION)
    match = _VERSION_RE.match(raw)
    if not match:
        raise UpdateError(f"pyproject.toml version {raw!r} is not X.Y.Z")
    return (int(match[1]), int(match[2]), int(match[3]))


def format_version(version: tuple[int, int, int]) -> str:
    """Render a version tuple as ``X.Y.Z``."""
    return ".".join(str(part) for part in version)


def wheel_name(version: tuple[int, int, int]) -> str:
    """The release wheel's file name (``janus_assistant-X.Y.Z-py3-none-any.whl``)."""
    return f"{DISTRIBUTION.replace('-', '_')}-{format_version(version)}-py3-none-any.whl"


# --- GitHub -------------------------------------------------------------------------------


def fetch_latest_release(repository: str | None = None, *, client: httpx.Client | None = None) -> Release:
    """Ask the GitHub Releases API for the latest published (non-draft, non-prerelease) release."""
    return fetch_release("latest", repository, client=client)


def fetch_release(tag: str, repository: str | None = None, *, client: httpx.Client | None = None) -> Release:
    """Read one release from the GitHub Releases API: ``"latest"`` or a ``vX.Y.Z`` tag."""
    repository = repository or settings.auto_update_repository
    path = "latest" if tag == "latest" else f"tags/{tag}"
    url = f"https://api.github.com/repos/{repository}/releases/{path}"
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
        raise UpdateError(f"could not read the {tag} release of {repository}: {exc}") from exc

    tag = str(payload.get("tag_name") or "")
    version = parse_tag(tag)
    if version is None:
        raise UpdateError(f"release tag {tag!r} is not vX.Y.Z")
    assets = {a.get("name"): a for a in payload.get("assets") or []}
    frontend = assets.get(FRONTEND_ASSET_NAME) or {}
    wheel = assets.get(wheel_name(version)) or {}
    return Release(
        tag=tag,
        version=version,
        asset_url=frontend.get("browser_download_url"),
        asset_digest=frontend.get("digest"),
        wheel_url=wheel.get("browser_download_url"),
        wheel_digest=wheel.get("digest"),
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


def install_kind(repo_root: Path = REPO_ROOT, prefix: str | None = None) -> str | None:
    """How this Janus was installed: ``CHECKOUT``, ``UV_TOOL``, ``PIPX``, or None (unknown)."""
    if (repo_root / ".git").exists():
        return CHECKOUT
    environment = Path(prefix or sys.prefix)
    if (environment / "uv-receipt.toml").is_file():
        return UV_TOOL
    if (environment / "pipx_metadata.json").is_file():
        return PIPX
    return None


def tool_executable(kind: str) -> str | None:
    """The installer that manages a tool install: uv or pipx."""
    return find_uv() if kind == UV_TOOL else shutil.which("pipx")


def state_dir(repo_root: Path = REPO_ROOT) -> Path:
    """Where update bookkeeping lives: ``.git`` in a checkout, else the XDG state directory."""
    git_dir = repo_root / ".git"
    if git_dir.is_dir():
        return git_dir
    directory = get_state_dir()
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def eligibility(repo_root: Path = REPO_ROOT) -> Eligibility:
    """Decide whether this install may update itself."""
    kind = install_kind(repo_root)
    if not settings.auto_update_enabled:
        return Eligibility(False, "auto_update_enabled is off", kind=kind)
    if kind in (UV_TOOL, PIPX):
        if tool_executable(kind) is None:
            return Eligibility(False, f"this is a {kind} install, but {kind.split()[0]} is not on the PATH", kind=kind)
        return Eligibility(True, "eligible", kind=kind)
    if kind is None:
        return Eligibility(False, f"{repo_root} is not a git checkout, and Janus is not a uv tool or pipx install")
    if shutil.which("git") is None:
        return Eligibility(False, "git is not installed", kind=CHECKOUT)
    if find_uv() is None:
        return Eligibility(False, "uv is not installed", kind=CHECKOUT)
    if Path(sys.prefix).resolve() != (repo_root / ".venv").resolve():
        return Eligibility(False, f"Janus is not running from {repo_root / '.venv'}", kind=CHECKOUT)
    try:
        if _git(repo_root, "status", "--porcelain", "--untracked-files=no"):
            return Eligibility(False, "the checkout has uncommitted changes", kind=CHECKOUT)
        branch = _git(repo_root, "symbolic-ref", "--quiet", "--short", "HEAD", check=False) or None
        if branch is None:
            tags = _git(repo_root, "tag", "--points-at", "HEAD").split()
            if not any(parse_tag(tag) for tag in tags):
                return Eligibility(False, "HEAD is detached but not at a release tag", kind=CHECKOUT)
        elif branch != "main":
            return Eligibility(False, f"the checkout is on branch {branch!r}, not main or a release tag", kind=CHECKOUT)
    except (UpdateError, OSError, subprocess.TimeoutExpired) as exc:
        return Eligibility(False, f"could not inspect the checkout: {exc}", kind=CHECKOUT)
    return Eligibility(True, "eligible", branch=branch, kind=CHECKOUT)


def check_for_update(repo_root: Path = REPO_ROOT, *, client: httpx.Client | None = None) -> Release | None:
    """Return the latest release when it is newer than the installed version, else None."""
    release = fetch_latest_release(client=client)
    return release if release.version > installed_version(repo_root) else None


# --- install ------------------------------------------------------------------------------


def _download_asset(
    url: str | None, digest: str | None, name: str, dest: Path, *, client: httpx.Client | None = None
) -> Path:
    """Download one release asset to ``dest / name`` and check it against its published SHA-256."""
    if not url:
        raise UpdateError(f"the release has no {name} asset (yet)")
    expected = (digest or "").removeprefix("sha256:")
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise UpdateError(f"{name} has no SHA-256 digest")

    path = dest / name
    partial = dest / f".{name}.part"  # renamed into place only once the digest matched
    sha = hashlib.sha256()
    size = 0
    try:
        owned = client is None
        http = client or httpx.Client(timeout=_HTTP_TIMEOUT, follow_redirects=True)
        try:
            with http.stream("GET", url, headers={"User-Agent": "janus-auto-update"}) as response:
                response.raise_for_status()
                with partial.open("wb") as handle:
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > _MAX_ASSET_BYTES:
                            raise UpdateError(f"{name} is larger than {_MAX_ASSET_BYTES} bytes")
                        sha.update(chunk)
                        handle.write(chunk)
        finally:
            if owned:
                http.close()
    except (httpx.HTTPError, UpdateError) as exc:
        partial.unlink(missing_ok=True)
        if isinstance(exc, UpdateError):
            raise
        raise UpdateError(f"could not download {name}: {exc}") from exc
    if sha.hexdigest() != expected:
        partial.unlink(missing_ok=True)
        raise UpdateError(f"{name} does not match its published SHA-256 digest")
    partial.replace(path)
    return path


def _download_frontend(release: Release, dest_dir: Path, *, client: httpx.Client | None = None) -> Path:
    """Download and unpack the release's frontend build into ``dest_dir``; return the dist dir."""
    archive = _download_asset(release.asset_url, release.asset_digest, FRONTEND_ASSET_NAME, dest_dir, client=client)

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
EXTRA_MARKERS = {
    "desktop": "pystray",
    "documents": "markitdown",
    "postgres": "psycopg2-binary",
    "whatsapp": "neonize",
}


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
    for extra, marker in sorted(EXTRA_MARKERS.items()):
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
    """Hold ``janus-update.lock`` so the worker and ``janus update`` never install at once."""
    with (state_dir(repo_root) / "janus-update.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise UpdateError("another update of this install is in progress") from exc
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
    kind: str = CHECKOUT
    #: Tool installs: the wheel of the release that was replaced (``previous_commit`` is unused).
    previous_wheel: str | None = None


def apply_update(release: Release, repo_root: Path = REPO_ROOT, *, client: httpx.Client | None = None) -> Installed:
    """Install ``release``, rolling back on failure. Does not restart."""
    kind = install_kind(repo_root)
    if kind is None:
        raise UpdateError(f"not updating: {repo_root} is not a git checkout or a uv tool / pipx install")
    with _install_lock(repo_root):
        if kind == CHECKOUT:
            return _apply_update_locked(release, repo_root, client)
        from janus.services import update_tool_install  # imports this module

        return update_tool_install.apply_locked(release, repo_root, kind, client)


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
