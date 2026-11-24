"""Deploy and availability metrics for the hosted Janus (JAN-269).

``deploy-uberspace.yml`` and ``nightly-health-check.yml`` call this script; the workflow YAML only
passes arguments. Everything here runs locally too (``task ops:*``). Commands::

    expected-version --ref REF [--pyproject pyproject.toml]
        The version a deploy of REF must report. A release tag (vX.Y.Z) must match
        pyproject.toml at that ref, or the command fails.
    verify-version --base-url URL --expected VERSION [--attempts 12] [--delay 10] [--json F] [--summary F]
        Poll /api/status/health until it answers HTTP 200 with the expected version, at most
        --attempts times, --delay seconds apart. HTTP 200 with another version fails.
    probe --base-url URL [--samples 5] [--interval 30] [--expected VERSION] [--json F] [--summary F]
        Take a fixed number of health samples at a fixed interval (the availability metric).
        Fails only when no sample was healthy.
    deploy-record --started-at EPOCH --restarted-at EPOCH --verify-json F --deploy-result R
                  --validate-result R --ref REF --version V --out F
        Build one deploy metrics record (duration, outcome) from the deploy job's outputs.
    publish --dataset deploys|availability --record F [--repo .] [--remote origin] [--branch ops-metrics]
        Append the record to the durable history branch (scripts/history_branch.py).
    summary --dataset deploys|availability [--file F | --repo . --remote origin] [--days 30]
        Markdown summary of the history (availability with its sampling interval, deploy durations).
    export --dataset deploys|availability [--file F | --repo . --remote origin] --format csv|jsonl

Definitions (also in docs/docs/tech-spec/deployment-operations.md#deploy-and-availability-metrics):

* **Deploy duration** starts when the ``deploy`` job's first step runs on the runner (queue time
  and the wait for the ``deploy-uberspace-production`` concurrency group are excluded) and ends
  when the public health endpoint first answers HTTP 200 with the expected version. A deploy
  that never reaches that point has no duration; it is recorded with outcome ``failure``.
* **Restart to healthy** is the part of that duration from the ``supervisorctl restart`` until
  the same verification, an upper bound for the user-visible restart gap.
* **Availability** is sampled, not continuous uptime: the share of health samples that answered
  HTTP 200 with ``status: ok``. Each nightly run takes ``--samples`` samples ``--interval``
  seconds apart; the summary always states that interval next to the percentage.

Records carry a fixed allowlist of fields and are checked for anything that looks like a secret
before they are written: no credentials, tokens or account e-mail addresses (the health
endpoint's ``unhealthy_accounts`` list is reduced to a count). Standard library only.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import io
import json
import os
import re
import statistics
import sys
import time
import tomllib
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin, urlsplit


def _load_sibling(name: str):
    """Load a sibling script (scripts/ is not a package)."""
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().with_name(f"{name}.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


history = _load_sibling("history_branch")

HEALTH_PATH = "/api/status/health"
TIMEOUT_SECONDS = 10
HISTORY_BRANCH = "ops-metrics"
DATASETS = {
    "deploys": "deploys/deploys.jsonl",
    "availability": "availability/samples.jsonl",
}
DEPLOY_SCHEMA = "janus.deploy-metrics/1"
AVAILABILITY_SCHEMA = "janus.availability/1"
VERSION_CHECK_SCHEMA = "janus.version-check/1"
_RELEASE_TAG = re.compile(r"^v?(\d+\.\d+\.\d+(?:[-+.][0-9A-Za-z.-]+)?)$")
_SECRET_KEY = re.compile(r"pass(word)?|secret|token|api[_-]?key|authorization|cookie|credential", re.IGNORECASE)
_SECRET_VALUE = re.compile(
    r"(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_|lin_api_|sk-[A-Za-z0-9]{20,}|Bearer\s+\S|-----BEGIN"
    r"|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})"
)


# --- time and environment ---------------------------------------------------------------------


def iso(moment: datetime | float | None) -> str | None:
    """UTC ISO 8601 with a ``Z`` suffix, second precision; ``None`` stays ``None``."""
    if moment is None:
        return None
    if not isinstance(moment, datetime):
        moment = datetime.fromtimestamp(float(moment), UTC)
    return moment.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_time(value: str) -> datetime:
    """Parse an epoch-seconds or ISO 8601 timestamp to an aware UTC datetime."""
    value = value.strip()
    if re.fullmatch(r"\d+(\.\d+)?", value):
        return datetime.fromtimestamp(float(value), UTC)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def run_info(env: dict[str, str] | None = None) -> dict:
    """The GitHub Actions run that produced a record (empty values outside Actions)."""
    env = os.environ if env is None else env
    server, repo, run_id = (
        env.get("GITHUB_SERVER_URL", ""),
        env.get("GITHUB_REPOSITORY", ""),
        env.get("GITHUB_RUN_ID", ""),
    )
    return {
        "workflow": env.get("GITHUB_WORKFLOW") or None,
        "id": run_id or None,
        "attempt": env.get("GITHUB_RUN_ATTEMPT") or None,
        "event": env.get("GITHUB_EVENT_NAME") or None,
        "url": f"{server}/{repo}/actions/runs/{run_id}" if server and repo and run_id else None,
    }


# --- secrets guard ----------------------------------------------------------------------------


def find_secrets(record: object, path: str = "$") -> list[str]:
    """JSON paths in ``record`` whose key or string value looks like a secret or an e-mail address."""
    hits = []
    if isinstance(record, dict):
        for key, value in record.items():
            child = f"{path}.{key}"
            if _SECRET_KEY.search(str(key)):
                hits.append(child)
            hits += find_secrets(value, child)
    elif isinstance(record, list):
        for index, value in enumerate(record):
            hits += find_secrets(value, f"{path}[{index}]")
    elif isinstance(record, str) and _SECRET_VALUE.search(record):
        hits.append(path)
    return hits


def ensure_exportable(record: dict) -> dict:
    """Return ``record`` unchanged, or raise when it contains anything that looks like a secret."""
    hits = find_secrets(record)
    if hits:
        raise ValueError(f"refusing to export a record with secret-looking fields: {', '.join(hits)}")
    return record


# --- HTTP ---------------------------------------------------------------------------------------


@dataclass
class HealthSample:
    """One request to the health endpoint."""

    at: datetime
    http_status: int | None
    health_status: str | None
    version: str | None
    latency_ms: int | None
    unhealthy_account_count: int
    error: str | None

    @property
    def ok(self) -> bool:
        """Healthy: HTTP 200 and ``status: ok``."""
        return self.http_status == 200 and self.health_status == "ok"

    def as_record(self) -> dict:
        """The exportable form (no e-mail addresses, no response body)."""
        return {
            "at": iso(self.at),
            "ok": self.ok,
            "http_status": self.http_status,
            "health_status": self.health_status,
            "version": self.version,
            "latency_ms": self.latency_ms,
            "unhealthy_account_count": self.unhealthy_account_count,
            "error": self.error,
        }


def fetch_health(base_url: str, now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> HealthSample:
    """GET the health endpoint once; network errors become a sample with ``error`` set."""
    url = urljoin(base_url.rstrip("/") + "/", HEALTH_PATH.lstrip("/"))
    if urlsplit(url).scheme not in ("http", "https"):
        raise ValueError(f"not an http(s) URL: {base_url}")
    request = urllib.request.Request(url, headers={"User-Agent": "janus-ops-metrics"})
    clock = time.monotonic()
    status: int | None
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310 - http(s) only, checked in main()
            status, body = response.status, response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        status, body = exc.code, exc.read().decode("utf-8", errors="replace")
    except OSError as exc:
        # Only the exception type: messages can echo hosts or proxy details nobody needs to keep.
        return HealthSample(now(), None, None, None, None, 0, type(exc).__name__)
    # Stamped when the response is complete: a deploy counts as verified only once the
    # endpoint has actually answered, so the request's own latency belongs to the duration.
    finished = now()
    latency = round((time.monotonic() - clock) * 1000)
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        data = {}
    data = data if isinstance(data, dict) else {}
    accounts = data.get("unhealthy_accounts")
    return HealthSample(
        at=finished,
        http_status=status,
        health_status=data.get("status") if isinstance(data.get("status"), str) else None,
        version=data.get("version") if isinstance(data.get("version"), str) else None,
        latency_ms=latency,
        unhealthy_account_count=len(accounts) if isinstance(accounts, list) else 0,
        error=None,
    )


# --- expected version ---------------------------------------------------------------------------


def expected_version(ref: str, pyproject_text: str) -> str:
    """The version a deploy of ``ref`` must report: pyproject's, which a release tag must equal."""
    version = tomllib.loads(pyproject_text)["project"]["version"]
    tag = _RELEASE_TAG.match(ref.removeprefix("refs/tags/"))
    if tag and tag.group(1) != version:
        raise ValueError(f"release tag {ref!r} does not match pyproject.toml version {version!r}")
    return version


def normalize_version(value: str) -> str:
    """``v1.2.3`` and ``refs/tags/v1.2.3`` become ``1.2.3``; other values pass through."""
    match = _RELEASE_TAG.match(value.strip().removeprefix("refs/tags/"))
    return match.group(1) if match else value.strip()


# --- version verification -------------------------------------------------------------------------


def verify_version(
    base_url: str,
    expected: str,
    attempts: int,
    delay: float,
    fetch: Callable[[str], HealthSample] | None = None,
    sleep: Callable[[float], None] | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> dict:
    """Poll until HTTP 200 with ``expected``; bounded by ``attempts``. Returns the version-check record."""
    fetch, sleep = fetch or fetch_health, sleep or time.sleep
    expected = normalize_version(expected)
    started = now()
    sample: HealthSample | None = None
    attempt = 0
    for attempt in range(1, attempts + 1):
        sample = fetch(base_url)
        if sample.http_status == 200 and sample.version == expected:
            break
        if attempt < attempts:
            sleep(delay)
    ok = sample is not None and sample.http_status == 200 and sample.version == expected
    if sample is None:
        detail = "no attempt made"
    elif ok:
        detail = f"HTTP 200 with version {expected} after {attempt} attempt(s)"
    elif sample.http_status == 200:
        detail = f"HTTP 200 but version {sample.version!r}, expected {expected!r}"
    else:
        detail = f"last answer HTTP {sample.http_status}" + (f" ({sample.error})" if sample.error else "")
    return {
        "schema": VERSION_CHECK_SCHEMA,
        "url": urljoin(base_url.rstrip("/") + "/", HEALTH_PATH.lstrip("/")),
        "expected_version": expected,
        "actual_version": sample.version if sample else None,
        "ok": ok,
        "attempts": attempt,
        "max_attempts": attempts,
        "delay_seconds": delay,
        "last_http_status": sample.http_status if sample else None,
        "health_status": sample.health_status if sample else None,
        "started_at": iso(started),
        "verified_at": iso(sample.at) if ok and sample else None,
        "detail": detail,
    }


def version_check_markdown(check: dict) -> str:
    """Job summary block with expected and actual version."""
    verdict = "PASS" if check["ok"] else "FAIL"
    return (
        f"### Release version check: {verdict}\n\n"
        "| Expected version | Actual version | HTTP | Attempts | Detail |\n"
        "|---|---|---|---|---|\n"
        f"| `{check['expected_version']}` | `{check['actual_version']}` | {check['last_http_status']} "
        f"| {check['attempts']} of {check['max_attempts']} ({check['delay_seconds']:g} s apart) | {check['detail']} |\n"
    )


# --- availability probe ------------------------------------------------------------------------


def probe(
    base_url: str,
    samples: int,
    interval: float,
    expected: str | None = None,
    fetch: Callable[[str], HealthSample] | None = None,
    sleep: Callable[[float], None] | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    env: dict[str, str] | None = None,
) -> dict:
    """Take ``samples`` health samples ``interval`` seconds apart; return the availability record."""
    fetch, sleep = fetch or fetch_health, sleep or time.sleep
    taken = []
    for index in range(samples):
        taken.append(fetch(base_url))
        if index < samples - 1:
            sleep(interval)
    ok = sum(sample.ok for sample in taken)
    expected = normalize_version(expected) if expected else None
    return ensure_exportable(
        {
            "schema": AVAILABILITY_SCHEMA,
            "recorded_at": iso(now()),
            "run": run_info(env),
            "url": urljoin(base_url.rstrip("/") + "/", HEALTH_PATH.lstrip("/")),
            "expected_version": expected,
            "version_matches": (all(s.version == expected for s in taken if s.ok) if expected and ok else None),
            "sampling": {"samples": samples, "interval_seconds": interval, "timeout_seconds": TIMEOUT_SECONDS},
            "ok_samples": ok,
            "total_samples": len(taken),
            "availability": round(ok / len(taken), 4) if taken else None,
            "samples": [sample.as_record() for sample in taken],
        }
    )


def probe_markdown(record: dict) -> str:
    """Job summary block for one probe run."""
    sampling = record["sampling"]
    lines = [
        f"### Availability sample: {record['ok_samples']} of {record['total_samples']} healthy\n",
        f"Sampling: {sampling['samples']} requests {sampling['interval_seconds']:g} s apart "
        f"({sampling['timeout_seconds']} s timeout); healthy = HTTP 200 with `status: ok`.\n",
        "| At (UTC) | OK | HTTP | Status | Version | Latency (ms) | Error |",
        "|---|---|---|---|---|---|---|",
    ]
    for sample in record["samples"]:
        lines.append(
            f"| {sample['at']} | {'yes' if sample['ok'] else 'no'} | {sample['http_status']} "
            f"| {sample['health_status']} | {sample['version']} | {sample['latency_ms']} | {sample['error'] or ''} |"
        )
    if record.get("expected_version"):
        lines.append(f"\nExpected version (main): `{record['expected_version']}`; matches: {record['version_matches']}")
    return "\n".join(lines) + "\n"


# --- deploy record ----------------------------------------------------------------------------


def deploy_record(
    *,
    started_at: str,
    restarted_at: str | None,
    check: dict | None,
    deploy_result: str,
    validate_result: str,
    ref: str,
    version: str,
    commit: str | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    env: dict[str, str] | None = None,
) -> dict:
    """One deploy: outcome, duration (first deploy step -> verified version) and restart-to-healthy."""
    env = os.environ if env is None else env
    started = parse_time(started_at)
    restarted = parse_time(restarted_at) if restarted_at else None
    verified = parse_time(check["verified_at"]) if check and check.get("verified_at") else None
    succeeded = deploy_result == "success" and verified is not None
    return ensure_exportable(
        {
            "schema": DEPLOY_SCHEMA,
            "recorded_at": iso(now()),
            "run": run_info(env),
            "ref": ref,
            # The deployed revision; a manual redeploy of an older ref (a rollback) differs from
            # GITHUB_SHA, which is only the workflow's own revision.
            "commit": commit or env.get("GITHUB_SHA") or None,
            "expected_version": normalize_version(version),
            "actual_version": check.get("actual_version") if check else None,
            "outcome": "success" if succeeded else "failure",
            "jobs": {"deploy": deploy_result, "validate": validate_result},
            "timing": {
                "started_at": iso(started),
                "restarted_at": iso(restarted),
                "verified_at": iso(verified),
                "duration_seconds": round((verified - started).total_seconds()) if succeeded else None,
                "restart_to_healthy_seconds": (
                    round((verified - restarted).total_seconds()) if succeeded and restarted else None
                ),
            },
            "verification": {
                key: check.get(key) if check else None
                for key in ("ok", "attempts", "max_attempts", "delay_seconds", "last_http_status")
            },
        }
    )


def _or_na(value: object) -> object:
    return "n/a" if value is None else value


def deploy_markdown(record: dict) -> str:
    """Job summary block for one deploy record."""
    timing = record["timing"]
    duration, gap = (_or_na(timing[key]) for key in ("duration_seconds", "restart_to_healthy_seconds"))
    return (
        f"### Deploy metrics: {record['outcome']}\n\n"
        "| Ref | Expected | Actual | Duration (s) | Restart to healthy (s) | deploy | validate |\n"
        "|---|---|---|---|---|---|---|\n"
        f"| `{record['ref']}` | `{record['expected_version']}` | `{record['actual_version']}` "
        f"| {duration} | {gap} "
        f"| {record['jobs']['deploy']} | {record['jobs']['validate']} |\n\n"
        "Duration: first step of the `deploy` job on the runner until the public health endpoint "
        "answered HTTP 200 with the expected version.\n"
    )


# --- history summaries and export -------------------------------------------------------------


def _within(records: list[dict], key: str, days: int, now: datetime) -> list[dict]:
    cutoff = now - timedelta(days=days)
    return [record for record in records if record.get(key) and parse_time(record[key]) >= cutoff]


def availability_summary(records: list[dict], days: int, now: datetime) -> dict:
    """Sampled availability over the last ``days`` days, with the sampling intervals that produced it."""
    window = _within(records, "recorded_at", days, now)
    total = sum(record.get("total_samples", 0) for record in window)
    ok = sum(record.get("ok_samples", 0) for record in window)
    intervals = sorted({record["sampling"]["interval_seconds"] for record in window if record.get("sampling")})
    return {
        "days": days,
        "runs": len(window),
        "samples": total,
        "ok_samples": ok,
        "availability": round(ok / total, 4) if total else None,
        "interval_seconds": intervals,
        "first": min((record["recorded_at"] for record in window), default=None),
        "last": max((record["recorded_at"] for record in window), default=None),
    }


def deploy_summary(records: list[dict], days: int, now: datetime) -> dict:
    """Deploy count, success rate and duration statistics over the last ``days`` days."""
    window = _within(records, "recorded_at", days, now)
    durations = [r["timing"]["duration_seconds"] for r in window if r["timing"].get("duration_seconds") is not None]
    successes = sum(record["outcome"] == "success" for record in window)
    return {
        "days": days,
        "deploys": len(window),
        "successes": successes,
        "failures": len(window) - successes,
        "median_duration_seconds": round(statistics.median(durations)) if durations else None,
        "max_duration_seconds": max(durations) if durations else None,
    }


def summary_markdown(dataset: str, summary: dict) -> str:
    """Markdown for ``summary``."""
    if not summary.get("samples" if dataset == "availability" else "deploys"):
        return f"### {dataset.capitalize()}, last {summary['days']} days\n\nNothing recorded in this window yet.\n"
    if dataset == "availability":
        share = "n/a" if summary["availability"] is None else f"{summary['availability'] * 100:.2f}%"
        intervals = ", ".join(f"{value:g} s" for value in summary["interval_seconds"]) or "n/a"
        return (
            f"### Sampled availability, last {summary['days']} days: {share}\n\n"
            f"{summary['ok_samples']} of {summary['samples']} samples healthy across {summary['runs']} probe runs "
            f"(one run per nightly schedule; samples within a run {intervals} apart; {summary['first']} to "
            f"{summary['last']}). Sampled, not continuous uptime: outages between samples are not seen.\n"
        )
    return (
        f"### Deploys, last {summary['days']} days\n\n"
        f"{summary['deploys']} deploys, {summary['successes']} succeeded, {summary['failures']} failed; "
        f"median duration {summary['median_duration_seconds']} s, max {summary['max_duration_seconds']} s.\n"
    )


def _flatten(record: dict, prefix: str = "") -> dict:
    flat = {}
    for key, value in record.items():
        name = f"{prefix}{key}"
        if isinstance(value, dict):
            flat.update(_flatten(value, f"{name}."))
        elif isinstance(value, list):
            flat[name] = json.dumps(value, sort_keys=True)
        else:
            flat[name] = value
    return flat


def export_csv(records: list[dict]) -> str:
    """Records as CSV with dotted column names; list fields (samples) stay JSON-encoded."""
    rows = [_flatten(record) for record in records]
    columns = sorted({column for row in rows for column in row})
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue()


# --- CLI ----------------------------------------------------------------------------------------


def _write(path: str | None, text: str, append: bool = False) -> None:
    if path:
        with open(path, "a" if append else "w", encoding="utf-8") as handle:
            handle.write(text)


def _load_records(args) -> list[dict]:
    if args.file:
        return history.read_jsonl(Path(args.file).read_text(encoding="utf-8"))
    return history.read_history(Path(args.repo), args.remote, args.branch, DATASETS[args.dataset])


def _base_url(parser: argparse.ArgumentParser, value: str) -> str:
    if urlsplit(value).scheme not in ("http", "https"):
        parser.error("--base-url must be an http(s) URL")
    return value


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)

    cmd = commands.add_parser("expected-version")
    cmd.add_argument("--ref", required=True)
    cmd.add_argument("--pyproject", default="pyproject.toml")

    cmd = commands.add_parser("verify-version")
    cmd.add_argument("--base-url", required=True)
    cmd.add_argument("--expected", required=True)
    cmd.add_argument("--attempts", type=int, default=12)
    cmd.add_argument("--delay", type=float, default=10)
    cmd.add_argument("--json")
    cmd.add_argument("--summary")

    cmd = commands.add_parser("probe")
    cmd.add_argument("--base-url", required=True)
    cmd.add_argument("--samples", type=int, default=5)
    cmd.add_argument("--interval", type=float, default=30)
    cmd.add_argument("--expected")
    cmd.add_argument("--json")
    cmd.add_argument("--summary")

    cmd = commands.add_parser("deploy-record")
    cmd.add_argument("--started-at", required=True)
    cmd.add_argument("--restarted-at", default="")
    cmd.add_argument("--verify-json")
    cmd.add_argument("--deploy-result", required=True)
    cmd.add_argument("--validate-result", default="skipped")
    cmd.add_argument("--ref", required=True)
    cmd.add_argument("--version", required=True)
    cmd.add_argument("--commit", default="", help="Commit SHA of the deployed ref (defaults to GITHUB_SHA)")
    cmd.add_argument("--out", required=True)
    cmd.add_argument("--summary")

    for name in ("publish", "summary", "export"):
        cmd = commands.add_parser(name)
        cmd.add_argument("--dataset", required=True, choices=sorted(DATASETS))
        cmd.add_argument("--repo", default=".")
        cmd.add_argument("--remote", default="origin")
        cmd.add_argument("--branch", default=HISTORY_BRANCH)
        cmd.add_argument("--out")
        if name == "publish":
            cmd.add_argument("--record", required=True)
        else:
            cmd.add_argument("--file", help="Read a local JSON Lines file instead of the history branch")
        if name == "summary":
            cmd.add_argument("--days", type=int, default=30)
        if name == "export":
            cmd.add_argument("--format", choices=("csv", "jsonl"), default="csv")
    return parser


def _cmd_expected_version(args, parser) -> int:
    try:
        print(expected_version(args.ref, Path(args.pyproject).read_text(encoding="utf-8")))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def _cmd_verify_version(args, parser) -> int:
    if args.attempts < 1:
        parser.error("--attempts must be at least 1")
    check = verify_version(_base_url(parser, args.base_url), args.expected, args.attempts, args.delay)
    _write(args.json, json.dumps(check, indent=2, sort_keys=True) + "\n")
    _write(args.summary, version_check_markdown(check), append=True)
    verdict = "PASS" if check["ok"] else "FAIL"
    print(f"{verdict}  expected {check['expected_version']}, actual {check['actual_version']}: {check['detail']}")
    return 0 if check["ok"] else 1


def _cmd_probe(args, parser) -> int:
    if args.samples < 1:
        parser.error("--samples must be at least 1")
    record = probe(_base_url(parser, args.base_url), args.samples, args.interval, args.expected)
    _write(args.json, json.dumps(record, indent=2, sort_keys=True) + "\n")
    markdown = probe_markdown(record)
    _write(args.summary, markdown, append=True)
    print(markdown)
    return 0 if record["ok_samples"] else 1


def _cmd_deploy_record(args, parser) -> int:
    check = None
    if args.verify_json and Path(args.verify_json).is_file():
        check = json.loads(Path(args.verify_json).read_text(encoding="utf-8"))
    record = deploy_record(
        started_at=args.started_at,
        restarted_at=args.restarted_at or None,
        check=check,
        deploy_result=args.deploy_result,
        validate_result=args.validate_result,
        ref=args.ref,
        version=args.version,
        commit=args.commit or None,
    )
    _write(args.out, json.dumps(record, indent=2, sort_keys=True) + "\n")
    _write(args.summary, deploy_markdown(record), append=True)
    print(deploy_markdown(record))
    return 0


def _cmd_publish(args, parser) -> int:
    record = ensure_exportable(json.loads(Path(args.record).read_text(encoding="utf-8")))
    path = DATASETS[args.dataset]
    message = f"metrics({args.dataset}): {record.get('recorded_at')}"
    commit = history.append_and_push(Path(args.repo), args.remote, args.branch, path, [record], message)
    print(f"appended to {args.branch}:{path} at {commit}")
    return 0


def _cmd_summary(args, parser) -> int:
    summarize = availability_summary if args.dataset == "availability" else deploy_summary
    markdown = summary_markdown(args.dataset, summarize(_load_records(args), args.days, datetime.now(UTC)))
    _write(args.out, markdown, append=True)
    print(markdown)
    return 0


def _cmd_export(args, parser) -> int:
    records = _load_records(args)
    text = export_csv(records) if args.format == "csv" else history.dump_jsonl(records)
    if args.out:
        _write(args.out, text)
    else:
        sys.stdout.write(text)
    return 0


_COMMANDS = {
    "expected-version": _cmd_expected_version,
    "verify-version": _cmd_verify_version,
    "probe": _cmd_probe,
    "deploy-record": _cmd_deploy_record,
    "publish": _cmd_publish,
    "summary": _cmd_summary,
    "export": _cmd_export,
}


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and run one command; returns the exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    return _COMMANDS[args.command](args, parser)


if __name__ == "__main__":
    sys.exit(main())
