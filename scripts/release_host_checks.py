"""Post-release checks on the production host.

Confirms on the host itself that a release landed: the installed Janus version, the database
schema at the Alembic head, a freshly restarted supervised process, a clean log since the deploy,
and a trigger queue that keeps moving. The ``Deploy to Uberspace`` workflow pipes this file over
SSH into the production host's Janus Python (``uv run --no-sync python - --expected-version X
--deployed-at EPOCH``), right after the deploy and again after the soak period; see
``docs/docs/tech-spec/deployment-operations.md#post-release-validation``.

Checks are read-only: they may inspect the host but never change it. Each one is ``required``
(the run fails when it fails) or informational.
"""

import argparse
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

# A queued run that is due but untouched for this long means the trigger worker is not working.
STALE_QUEUE_AFTER = timedelta(minutes=15)
# Slack between the workflow's deploy timestamp and supervisor's restart, for clock skew.
UPTIME_SLACK_SECONDS = 120
LOG_TAIL_BYTES = 4 * 2**20
_LOG_LINE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),\d+ - \S+ - (\w+) - ")
_UPTIME = re.compile(r"uptime (?:(\d+) days?, )?(\d+):(\d\d):(\d\d)")


@dataclass
class CheckResult:
    """Outcome of one host check."""

    name: str
    required: bool
    ok: bool
    detail: str


def check_installed_version(expected: str) -> tuple[bool, str]:
    """The installed ``janus-assistant`` distribution is the release that was deployed."""
    from importlib.metadata import version

    installed = version("janus-assistant")
    return installed == expected, f"installed {installed}, expected {expected}"


def check_alembic_head() -> tuple[bool, str]:
    """The database schema is at every Alembic head of the deployed code."""
    from alembic.config import Config
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory
    from sqlalchemy import create_engine

    from janus.config import settings

    heads = set(ScriptDirectory.from_config(Config("alembic.ini")).get_heads())
    engine = create_engine(settings.database_url)
    try:
        with engine.connect() as connection:
            current = set(MigrationContext.configure(connection).get_current_heads())
    finally:
        engine.dispose()
    return current == heads, f"current {sorted(current)}, heads {sorted(heads)}"


def parse_uptime(status_line: str) -> int | None:
    """Return the uptime in seconds from a ``supervisorctl status`` line, or None."""
    match = _UPTIME.search(status_line)
    if not match:
        return None
    days, hours, minutes, seconds = (int(part or 0) for part in match.groups())
    return ((days * 24 + hours) * 60 + minutes) * 60 + seconds


def check_supervisor(deployed_at: float, now: float | None = None) -> tuple[bool, str]:
    """``janus`` is RUNNING under supervisor and was restarted by this deploy."""
    result = subprocess.run(
        ["supervisorctl", "status", "janus"], capture_output=True, text=True, timeout=30, check=False
    )
    line = (result.stdout or result.stderr).strip()
    return evaluate_supervisor_status(line, deployed_at, time.time() if now is None else now)


def evaluate_supervisor_status(line: str, deployed_at: float, now: float) -> tuple[bool, str]:
    """Judge one ``supervisorctl status janus`` line against the deploy time."""
    if " RUNNING " not in f" {line} ":
        return False, line or "no output from supervisorctl"
    uptime = parse_uptime(line)
    if uptime is None:
        return False, f"could not read the uptime: {line}"
    since_deploy = now - deployed_at
    return uptime <= since_deploy + UPTIME_SLACK_SECONDS, f"{line} (deploy started {since_deploy:.0f}s ago)"


def scan_log(lines: list[str], since: datetime) -> tuple[int, int]:
    """Count tracebacks/CRITICAL records and ERROR records logged at or after ``since``.

    Log timestamps are the host's local time; continuation lines (traceback frames) belong to the
    last timestamped record before them.
    """
    severe = errors = 0
    current: datetime | None = None
    for line in lines:
        match = _LOG_LINE.match(line)
        if match:
            current = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S")
            level = match.group(2)
            if current >= since:
                if level == "CRITICAL":
                    severe += 1
                elif level == "ERROR":
                    errors += 1
        elif line.startswith("Traceback (most recent call last)") and current is not None and current >= since:
            severe += 1
    return severe, errors


def _log_lines_since(deployed_at: float) -> tuple[list[str], datetime, Path]:
    from janus.config import settings

    path = Path(settings.log_file_path)
    with path.open("rb") as handle:
        handle.seek(max(0, path.stat().st_size - LOG_TAIL_BYTES))
        text = handle.read().decode("utf-8", errors="replace")
    return text.splitlines(), datetime.fromtimestamp(deployed_at), path


def check_log_tracebacks(deployed_at: float) -> tuple[bool, str]:
    """No traceback or CRITICAL record in the Janus log since the deploy started."""
    lines, since, path = _log_lines_since(deployed_at)
    severe, _ = scan_log(lines, since)
    return severe == 0, f"{severe} traceback/CRITICAL record(s) in {path} since {since:%Y-%m-%d %H:%M:%S}"


def check_log_errors(deployed_at: float) -> tuple[bool, str]:
    """Report ERROR records since the deploy; informational, since some come from mail servers."""
    lines, since, path = _log_lines_since(deployed_at)
    _, errors = scan_log(lines, since)
    return errors == 0, f"{errors} ERROR record(s) in {path} since {since:%Y-%m-%d %H:%M:%S}"


def check_trigger_queue() -> tuple[bool, str]:
    """No due trigger run has sat in ``queued`` for longer than the stale threshold."""
    from sqlalchemy import create_engine, func, or_, select

    from janus import models
    from janus.config import settings

    cutoff = datetime.now(UTC).replace(tzinfo=None) - STALE_QUEUE_AFTER
    trigger = models.RuntimeTrigger
    query = (
        select(func.count())
        .select_from(trigger)
        .where(
            trigger.status == models.RuntimeTriggerStatus.QUEUED.value,
            trigger.created_at < cutoff,
            or_(trigger.next_attempt_at.is_(None), trigger.next_attempt_at < cutoff),
        )
    )
    # A plain engine, not janus.database.SessionLocal: that one upgrades the schema on first use.
    engine = create_engine(settings.database_url)
    try:
        with engine.connect() as connection:
            stale = connection.scalar(query)
    finally:
        engine.dispose()
    minutes = int(STALE_QUEUE_AFTER.total_seconds() // 60)
    return not stale, f"{stale or 0} trigger run(s) due for more than {minutes} min but still queued"


def check_disk_space() -> tuple[bool, str]:
    """Report free space; a full quota has broken deploys before."""
    usage = shutil.disk_usage(Path.home())
    return True, f"filesystem free: {usage.free / 2**30:.1f} GiB of {usage.total / 2**30:.1f} GiB"


def build_checks(expected_version: str, deployed_at: float) -> list[tuple[str, bool, Callable[[], tuple[bool, str]]]]:
    """The host checks for one release, in reporting order."""
    return [
        ("installed-version", True, lambda: check_installed_version(expected_version)),
        ("alembic-at-head", True, check_alembic_head),
        ("supervisor-restarted", True, lambda: check_supervisor(deployed_at)),
        ("log-tracebacks", True, lambda: check_log_tracebacks(deployed_at)),
        ("log-errors", False, lambda: check_log_errors(deployed_at)),
        ("trigger-queue", True, check_trigger_queue),
        ("disk-space", False, check_disk_space),
    ]


def run_checks(checks) -> list[CheckResult]:
    """Run every check; an unexpected exception counts as a failed check."""
    results = []
    for name, required, check in checks:
        try:
            ok, detail = check()
        except Exception as exc:  # noqa: BLE001 - a crashing probe is a failed check
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        results.append(CheckResult(name, required, ok, detail))
    return results


def report(results: list[CheckResult]) -> int:
    """Print one line per check and return 1 when a required check failed."""
    for result in results:
        status = "PASS" if result.ok else ("FAIL" if result.required else "WARN")
        kind = "required" if result.required else "info"
        print(f"{status:4}  {result.name:22} ({kind})  {result.detail}")
    failed = [result.name for result in results if result.required and not result.ok]
    print(f"\n{len(failed)} required check(s) failed" + (f": {', '.join(failed)}" if failed else ""))
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, run the host checks and return the exit code."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--deployed-at", type=float, required=True, help="Unix time the deploy started")
    args = parser.parse_args(argv)
    return report(run_checks(build_checks(args.expected_version, args.deployed_at)))


if __name__ == "__main__":
    sys.exit(main())
