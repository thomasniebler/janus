"""Runtime helpers for persistent paths, logging, and single-instance startup."""

import atexit
import logging
import os
import shutil
import threading
from pathlib import Path

from sqlalchemy.engine import make_url

from janus.config import (
    get_default_database_url,
    get_default_fernet_key_path,
    settings,
)

logger = logging.getLogger(__name__)

_paths_prepared = False
_paths_lock = threading.Lock()
_logging_configured = False
_instance_lock: "SingleInstanceLock | None" = None


def get_instance_lock_path() -> Path:
    """Returns the absolute lock-file path for the Janus server runtime."""
    return settings.log_file_path.parent / "janus.lock"


def sqlite_database_path(database_url: str) -> Path | None:
    """Returns the filesystem path for a SQLite database URL, if any."""
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
        return None

    path = Path(url.database).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return path


def _move_legacy_file(source: Path, destination: Path) -> bool:
    """Moves a legacy runtime file into the persistent runtime directory."""
    if not source.exists() or destination.exists():
        return False

    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        shutil.move(str(source), str(destination))
    except FileNotFoundError:
        return False
    logger.info("Migrated legacy runtime file from %s to %s", source, destination)
    return True


def prepare_runtime_paths() -> None:
    """Creates persistent runtime directories and migrates legacy default files."""
    global _paths_prepared
    if _paths_prepared:
        return

    with _paths_lock:
        if _paths_prepared:
            return

        database_path = sqlite_database_path(settings.database_url)
        default_database_url = get_default_database_url()
        if database_path and settings.database_url == default_database_url:
            _move_legacy_file(Path.cwd() / "janus.db", database_path)

        if settings.fernet_key_path == get_default_fernet_key_path():
            _move_legacy_file(Path.cwd() / ".janus_key", settings.fernet_key_path)

        for path in (database_path, settings.fernet_key_path, settings.log_file_path, get_instance_lock_path()):
            if path is not None:
                path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)

        _paths_prepared = True


def configure_logging() -> None:
    """Configures Janus logging to the console and the persistent log file."""
    global _logging_configured
    if _logging_configured:
        return

    prepare_runtime_paths()

    formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    root = logging.getLogger()

    if not any(
        isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler)
        for handler in root.handlers
    ):
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        root.addHandler(stream_handler)

    log_file_path = settings.log_file_path.resolve()
    if not any(
        isinstance(handler, logging.FileHandler) and Path(handler.baseFilename).resolve() == log_file_path
        for handler in root.handlers
        if hasattr(handler, "baseFilename")
    ):
        file_handler = logging.FileHandler(log_file_path, encoding="utf-8")
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    if root.level == logging.NOTSET:
        root.setLevel(logging.INFO)

    _logging_configured = True


class SingleInstanceLock:
    """Advisory lock that keeps Janus in single-instance server mode."""

    def __init__(self, lock_path: Path) -> None:
        """Initializes the lock helper for the given runtime lock path."""
        self.lock_path = lock_path
        self._fd: int | None = None

    def acquire(self) -> None:
        """Acquires the runtime lock or raises if another Janus server owns it."""
        if self._fd is not None:
            return

        prepare_runtime_paths()
        fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            try:
                os.close(fd)
            except OSError:
                logger.exception("Failed to close runtime lock file descriptor after lock contention")
            raise RuntimeError(
                "Janus is already running for this runtime directory. "
                "The current deployment model is single-instance because SQLite storage and in-process workers "
                f"must not be shared across multiple server processes. Lock file: {self.lock_path}"
            ) from exc

        try:
            os.ftruncate(fd, 0)
            os.write(fd, str(os.getpid()).encode("utf-8"))
            os.fsync(fd)
        except OSError as exc:
            try:
                os.close(fd)
            except OSError:
                logger.exception("Failed to close runtime lock file descriptor after metadata write failure")
            raise RuntimeError(f"Failed to update Janus runtime lock file: {self.lock_path}") from exc

        self._fd = fd

    def release(self) -> None:
        """Releases the runtime lock if it is currently held by this process."""
        if self._fd is None:
            return

        try:
            if os.name == "nt":
                import msvcrt

                os.lseek(self._fd, 0, os.SEEK_SET)
                msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._fd, fcntl.LOCK_UN)
        finally:
            os.close(self._fd)
            self._fd = None


def enforce_single_instance() -> Path:
    """Acquires the Janus runtime lock and returns its filesystem path."""
    global _instance_lock
    if _instance_lock is None:
        instance_lock = SingleInstanceLock(get_instance_lock_path())
        instance_lock.acquire()
        _instance_lock = instance_lock
        atexit.register(_instance_lock.release)
    return _instance_lock.lock_path
