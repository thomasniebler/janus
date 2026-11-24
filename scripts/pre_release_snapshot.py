"""Pre-release database snapshot.

Copies the live SQLite database to one fixed file next to it, ``janus.pre-release.db``, before a
release migrates the schema. Each release overwrites the previous snapshot, so there is only ever
one. The copy uses SQLite's online backup API, so it is consistent while the running Janus keeps
writing. The deploy workflow pipes this file over SSH into the production host's Janus Python
(``uv run --no-sync python -``) and stops before ``alembic upgrade`` when it fails. Restoring is a
manual step; see ``docs/docs/tech-spec/deployment-operations.md#pre-release-snapshot``.

Exit codes: 0 when the snapshot was written or the database is not SQLite (nothing to snapshot),
1 when it could not be written. A partial snapshot is removed.
"""

import shutil
import sqlite3
import sys
from pathlib import Path

from sqlalchemy.engine import make_url

SNAPSHOT_NAME = "janus.pre-release.db"
# Headroom kept free after the copy, on top of the database size.
MIN_FREE_BYTES_AFTER = 256 * 2**20


def sqlite_path(database_url: str) -> Path | None:
    """Return the database file of a file-backed SQLite URL, or None for anything else."""
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
        return None
    return Path(url.database).expanduser().resolve()


def snapshot(db_path: Path, snapshot_path: Path) -> str:
    """Replace ``snapshot_path`` with an online backup of ``db_path`` and return a summary line."""
    if not db_path.is_file():
        raise FileNotFoundError(f"database {db_path} does not exist")
    db_size = db_path.stat().st_size
    old_size = snapshot_path.stat().st_size if snapshot_path.exists() else 0
    free = shutil.disk_usage(snapshot_path.parent).free
    # The old snapshot is removed first, so its space counts as free.
    if free + old_size - db_size < MIN_FREE_BYTES_AFTER:
        raise OSError(
            f"not enough free space for a {db_size / 2**20:.1f} MiB snapshot "
            f"({free / 2**20:.1f} MiB free, {old_size / 2**20:.1f} MiB reclaimable)"
        )
    snapshot_path.unlink(missing_ok=True)
    source = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    target = sqlite3.connect(snapshot_path)
    try:
        source.backup(target)
    except BaseException:
        target.close()
        snapshot_path.unlink(missing_ok=True)
        raise
    finally:
        source.close()
    target.close()
    return f"snapshot of {db_path} ({db_size / 2**20:.1f} MiB) written to {snapshot_path}"


def main(database_url: str | None = None) -> int:
    """Snapshot the configured database; print one line and return the exit code."""
    if database_url is None:
        from janus.config import settings

        database_url = settings.database_url
    db_path = sqlite_path(database_url)
    if db_path is None:
        print("SKIP  database is not a SQLite file; no snapshot taken")
        return 0
    try:
        print(f"PASS  {snapshot(db_path, db_path.with_name(SNAPSHOT_NAME))}")
    except (OSError, sqlite3.Error) as exc:
        print(f"FAIL  pre-release snapshot: {type(exc).__name__}: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
