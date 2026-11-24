#!/usr/bin/env bash
# Runs scripts/release_host_checks.py inside the production uv environment over
# SSH and tees its report to the given file, as the deploy workflow's `validate`
# and `soak` jobs do.
#
# The host sometimes drops a fresh SSH session right after the deploy's own
# connections ("Connection closed by ... port 22", ssh exit 255). Only that
# connection failure is retried, up to RELEASE_HOST_CHECK_ATTEMPTS times (3),
# RELEASE_HOST_CHECK_RETRY_SLEEP seconds apart (20). A failed check (exit 1), a
# missing report file (tee failure) or any other exit code ends the run at once.
#
# Usage: release_host_checks.sh <report-file>
# Env:   DEPLOY_SSH_TARGET, EXPECTED_VERSION, RESTARTED_AT (as in the workflow);
#        DEPLOY_PATH (default: janus, relative to the SSH user's home).
set -uo pipefail

report="${1:?usage: release_host_checks.sh <report-file>}"
attempts="${RELEASE_HOST_CHECK_ATTEMPTS:-3}"
retry_sleep="${RELEASE_HOST_CHECK_RETRY_SLEEP:-20}"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

for attempt in $(seq 1 "$attempts"); do
  ssh -o ConnectTimeout=20 "$DEPLOY_SSH_TARGET" \
    "cd ${DEPLOY_PATH:-janus} && PATH=\"\$HOME/.local/bin:/usr/bin:/bin\" uv run --no-sync python - --expected-version $EXPECTED_VERSION --deployed-at $RESTARTED_AT" \
    < "$script_dir/release_host_checks.py" | tee "$report"
  statuses=("${PIPESTATUS[@]}")  # one assignment: the first one would reset PIPESTATUS
  ssh_status=${statuses[0]}
  tee_status=${statuses[1]}
  if [ "$tee_status" -ne 0 ]; then
    echo "could not write the report $report (tee exit $tee_status)" >&2
    exit "$tee_status"
  fi
  if [ "$ssh_status" -ne 255 ]; then
    exit "$ssh_status"
  fi
  if [ "$attempt" -lt "$attempts" ]; then
    echo "ssh connection failed (attempt $attempt/$attempts); retrying in ${retry_sleep}s" >&2
    sleep "$retry_sleep"
  fi
done
echo "ssh connection failed $attempts times; giving up" >&2
exit 255
