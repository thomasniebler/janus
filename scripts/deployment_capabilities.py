"""Deployment target capability checks.

Probes whether the Python runtime and host that run Janus provide what Janus needs (or is about
to need). Standard library only, so it runs in any environment; ``sqlite_vec`` is used when it is
importable. The ``Deployment capability checks`` workflow pipes this file over SSH into the
production host's Janus Python (see ``docs/docs/tech-spec/deployment-operations.md``); it also
runs locally with ``uv run --with sqlite-vec python scripts/deployment_capabilities.py``.

Each check is ``required`` (the run fails when it fails) or informational. Add a check here, in
the same change as the feature that needs the capability, and document it in the tech spec.
Checks must be read-only: they may inspect the host but never change it.
"""

import importlib
import platform
import shutil
import sqlite3
import struct
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

MIN_PYTHON = (3, 12)


@dataclass
class CheckResult:
    """Outcome of one capability check."""

    name: str
    required: bool
    ok: bool
    detail: str


def check_python_version() -> tuple[bool, str]:
    """Janus requires Python 3.12+ (``requires-python`` in pyproject.toml)."""
    version = platform.python_version()
    return sys.version_info[:2] >= MIN_PYTHON, f"{version} at {sys.executable}"


def check_sqlite_fts5() -> tuple[bool, str]:
    """FTS5 ranks keyword matches when present; without it Janus uses substring matching (JAN-245)."""
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE VIRTUAL TABLE probe USING fts5(body)")
        conn.execute("INSERT INTO probe(body) VALUES ('deployment capability probe')")
        hits = conn.execute("SELECT count(*) FROM probe WHERE probe MATCH 'capability'").fetchone()[0]
        return hits == 1, f"SQLite {sqlite3.sqlite_version}"
    except sqlite3.OperationalError as exc:
        return False, f"SQLite {sqlite3.sqlite_version}: {exc}"
    finally:
        conn.close()


def check_sqlite_load_extension() -> tuple[bool, str]:
    """``sqlite-vec`` is a loadable extension, so Python's sqlite3 must allow extension loading."""
    conn = sqlite3.connect(":memory:")
    try:
        if not hasattr(conn, "enable_load_extension"):
            return False, "this Python's sqlite3 was built without loadable-extension support"
        conn.enable_load_extension(True)
        conn.enable_load_extension(False)
        return True, "enable_load_extension available"
    except sqlite3.Error as exc:
        return False, str(exc)
    finally:
        conn.close()


def check_sqlite_vec() -> tuple[bool, str]:
    """``sqlite-vec`` loads and answers a nearest-neighbour query (knowledge layer, JAN-241)."""
    try:
        # Imported dynamically: sqlite-vec is not a Janus dependency yet, and the workflow
        # supplies it with `uv run --with`.
        sqlite_vec = importlib.import_module("sqlite_vec")
    except ImportError:
        return False, "sqlite_vec is not installed in this environment"
    conn = sqlite3.connect(":memory:")
    try:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        version = conn.execute("SELECT vec_version()").fetchone()[0]
        conn.execute("CREATE VIRTUAL TABLE probe USING vec0(embedding float[3])")
        vectors = {1: (1.0, 0.0, 0.0), 2: (0.0, 1.0, 0.0)}
        for rowid, vector in vectors.items():
            conn.execute("INSERT INTO probe(rowid, embedding) VALUES (?, ?)", (rowid, struct.pack("3f", *vector)))
        nearest = conn.execute(
            "SELECT rowid FROM probe WHERE embedding MATCH ? ORDER BY distance LIMIT 1",
            (struct.pack("3f", 0.9, 0.1, 0.0),),
        ).fetchone()[0]
        return nearest == 1, f"sqlite-vec {version}, nearest-neighbour query returned row {nearest}"
    except (AttributeError, sqlite3.Error) as exc:
        return False, str(exc)
    finally:
        conn.close()


def check_disk_space() -> tuple[bool, str]:
    """Report free space and the user quota; deploys have failed on a full quota before."""
    usage = shutil.disk_usage(Path.home())
    detail = f"filesystem free: {usage.free / 2**30:.1f} GiB of {usage.total / 2**30:.1f} GiB"
    quota = shutil.which("quota")
    if quota:
        # -g: group quota (Uberspace accounts are quota'd per group), -l: local filesystems only,
        # so quota never waits on remote (NFS/RPC) quota servers, which made plain -s time out.
        result = subprocess.run([quota, "-gsl"], capture_output=True, text=True, timeout=10, check=False)
        lines = [line.strip() for line in (result.stdout or result.stderr).splitlines() if line.strip()]
        if lines:
            detail += "; quota: " + " | ".join(lines[-2:])
    return True, detail


CHECKS: list[tuple[str, bool, Callable[[], tuple[bool, str]]]] = [
    ("python-version", True, check_python_version),
    ("sqlite-fts5", False, check_sqlite_fts5),
    ("sqlite-load-extension", True, check_sqlite_load_extension),
    ("sqlite-vec", True, check_sqlite_vec),
    ("disk-space", False, check_disk_space),
]


def run_checks(checks=CHECKS) -> list[CheckResult]:
    """Run every check; an unexpected exception counts as a failed check."""
    results = []
    for name, required, check in checks:
        try:
            ok, detail = check()
        except Exception as exc:  # noqa: BLE001 - a crashing probe is a failed capability
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        results.append(CheckResult(name, required, ok, detail))
    return results


def main(checks=CHECKS) -> int:
    """Print one line per check and return 1 when a required check failed."""
    results = run_checks(checks)
    for result in results:
        status = "PASS" if result.ok else ("FAIL" if result.required else "WARN")
        kind = "required" if result.required else "info"
        print(f"{status:4}  {result.name:22} ({kind})  {result.detail}")
    failed = [result.name for result in results if result.required and not result.ok]
    print(f"\n{len(failed)} required check(s) failed" + (f": {', '.join(failed)}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
