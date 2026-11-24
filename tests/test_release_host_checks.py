"""`scripts/release_host_checks.sh`: SSH retry only on a closed connection (exit 255), never on a failed check."""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "release_host_checks.sh"

# A stand-in for ssh: exits with the next code in EXIT_CODES (one per attempt,
# the last one repeats), echoes the remote command's stdin length so the report
# proves the check script was streamed, and counts its invocations.
_FAKE_SSH = """#!/usr/bin/env bash
count_file="$FAKE_SSH_COUNT"
n=$(( $(cat "$count_file" 2>/dev/null || echo 0) + 1 ))
echo "$n" > "$count_file"
IFS=, read -r -a codes <<< "$FAKE_SSH_EXIT_CODES"
idx=$(( n - 1 )); [ "$idx" -ge "${#codes[@]}" ] && idx=$(( ${#codes[@]} - 1 ))
code="${codes[$idx]}"
bytes=$(wc -c < /dev/stdin)
# The host is the first argument that is not an option or an option value.
host=""; while [ $# -gt 0 ]; do case "$1" in -o) shift 2;; -*) shift;; *) host="$1"; break;; esac; done
echo "attempt $n: streamed $bytes bytes to $host, exit $code"
exit "$code"
"""


@pytest.fixture
def run_checks(tmp_path: Path):
    """Runs the helper with a fake `ssh` on PATH; returns (completed process, attempt count)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_ssh = bin_dir / "ssh"
    fake_ssh.write_text(_FAKE_SSH)
    fake_ssh.chmod(fake_ssh.stat().st_mode | stat.S_IXUSR)
    count_file = tmp_path / "count"

    def _run(exit_codes: str, report: Path) -> tuple[subprocess.CompletedProcess[str], int]:
        env = {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "FAKE_SSH_COUNT": str(count_file),
            "FAKE_SSH_EXIT_CODES": exit_codes,
            "DEPLOY_SSH_TARGET": "user@host.example",
            "EXPECTED_VERSION": "1.7.0",
            "RESTARTED_AT": "1791286734",
            "RELEASE_HOST_CHECK_RETRY_SLEEP": "0",
        }
        proc = subprocess.run(["bash", str(SCRIPT), str(report)], env=env, capture_output=True, text=True, check=False)
        attempts = int(count_file.read_text()) if count_file.exists() else 0
        return proc, attempts

    return _run


def test_retries_a_closed_connection_and_then_succeeds(run_checks, tmp_path: Path):
    """Two dropped sessions (255) are retried; the third attempt's report is written and the run passes."""
    report = tmp_path / "validate-host.txt"
    proc, attempts = run_checks("255,255,0", report)

    assert proc.returncode == 0, proc.stderr
    assert attempts == 3
    assert "attempt 3:" in report.read_text()
    assert proc.stderr.count("retrying") == 2


def test_a_failed_check_is_never_retried(run_checks, tmp_path: Path):
    """Exit 1 from the remote check is a real finding: reported once, no retry."""
    report = tmp_path / "validate-host.txt"
    proc, attempts = run_checks("1,0", report)

    assert proc.returncode == 1
    assert attempts == 1
    assert "attempt 1:" in report.read_text()
    assert "retrying" not in proc.stderr


def test_gives_up_after_three_closed_connections(run_checks, tmp_path: Path):
    """Three 255s in a row end with 255 so the job fails for the connection problem."""
    proc, attempts = run_checks("255", tmp_path / "soak-host.txt")

    assert proc.returncode == 255
    assert attempts == 3
    assert "giving up" in proc.stderr


def test_streams_the_check_script_to_the_host(run_checks, tmp_path: Path):
    """The remote python reads scripts/release_host_checks.py from stdin on every attempt."""
    report = tmp_path / "validate-host.txt"
    proc, _ = run_checks("0", report)

    expected = (ROOT / "scripts" / "release_host_checks.py").stat().st_size
    assert proc.returncode == 0
    assert f"streamed {expected} bytes to user@host.example" in report.read_text()


def test_an_unwritable_report_fails_even_when_the_check_passed(run_checks, tmp_path: Path):
    """A lost report (tee failure) must not pass silently; previously pipefail caught this."""
    proc, attempts = run_checks("0", tmp_path / "missing-dir" / "validate-host.txt")

    assert proc.returncode != 0
    assert attempts == 1
    assert "could not write the report" in proc.stderr
