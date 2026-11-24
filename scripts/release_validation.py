"""Post-release checks against the public Janus URL.

Confirms from outside that a release landed: the live version, the frontend bundle, surfaces that
production keeps closed, the chat WebSocket's refusal of anonymous clients, the TLS certificate,
and, signed in as a dedicated non-admin smoke-test user, the trigger worker and the status
overview. The ``Deploy to Uberspace`` workflow runs it on the GitHub runner right after the deploy
and again after the soak period; see
``docs/docs/tech-spec/deployment-operations.md#post-release-validation``.

Standard library only. The smoke-test credentials come from ``JANUS_SMOKE_USERNAME`` and
``JANUS_SMOKE_PASSWORD``; without them the signed-in checks report a warning instead of running.
Run locally with ``python scripts/release_validation.py --base-url URL --expected-version X``.
"""

import argparse
import base64
import json
import os
import re
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urljoin, urlsplit

TIMEOUT_SECONDS = 15
MIN_CERT_DAYS = 14
HEALTH_ATTEMPTS = 6
HEALTH_RETRY_SECONDS = 10
_ASSET_REF = re.compile(r"""(?:src|href)=["'](/assets/[^"']+)["']""")
# The chat WebSocket accepts, then closes an unauthenticated client with 1008 (policy violation).
WS_POLICY_VIOLATION = 1008


@dataclass
class CheckResult:
    """Outcome of one release check."""

    name: str
    required: bool
    ok: bool
    detail: str


@dataclass
class Response:
    """The parts of an HTTP response the checks look at."""

    status: int
    body: str


def http_get(url: str, token: str | None = None) -> Response:
    """GET ``url`` and return status and body; HTTP error statuses are returned, not raised."""
    request = urllib.request.Request(url, headers={"User-Agent": "janus-release-validation"})
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    return _send(request)


def http_post_json(url: str, payload: dict) -> Response:
    """POST ``payload`` as JSON to ``url``."""
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "janus-release-validation"},
        method="POST",
    )
    return _send(request)


def _send(request: urllib.request.Request) -> Response:
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310 - fixed https URL
            return Response(response.status, response.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        return Response(exc.code, exc.read().decode("utf-8", errors="replace"))


def _json(response: Response) -> dict:
    try:
        data = json.loads(response.body)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def check_health_version(base_url: str, expected: str, get=http_get, sleep=time.sleep) -> tuple[bool, str]:
    """``/api/status/health`` answers 200 with the deployed version; retried while Janus restarts.

    A stale IMAP monitor makes the endpoint answer 500 ("degraded"), which fails this check.
    """
    detail = "no attempt made"
    for attempt in range(1, HEALTH_ATTEMPTS + 1):
        try:
            response = get(urljoin(base_url, "/api/status/health"))
        except OSError as exc:
            detail = f"attempt {attempt}: {type(exc).__name__}: {exc}"
        else:
            data = _json(response)
            version = data.get("version")
            detail = f"attempt {attempt}: HTTP {response.status}, status {data.get('status')!r}, version {version!r}"
            if response.status == 200 and version == expected:
                return True, detail
            if data.get("unhealthy_accounts"):
                detail += f", unhealthy accounts {data['unhealthy_accounts']}"
        if attempt < HEALTH_ATTEMPTS:
            sleep(HEALTH_RETRY_SECONDS)
    return False, f"{detail} (expected version {expected!r})"


def check_frontend_assets(base_url: str, get=http_get) -> tuple[bool, str]:
    """The SPA's ``index.html`` loads and every ``/assets/`` file it references is served."""
    index = get(urljoin(base_url, "/"))
    if index.status != 200:
        return False, f"/ answered HTTP {index.status}"
    assets = sorted(set(_ASSET_REF.findall(index.body)))
    if not assets:
        return False, "index.html references no /assets/ files"
    missing = [f"{path} (HTTP {status})" for path in assets if (status := get(urljoin(base_url, path)).status) != 200]
    if missing:
        return False, f"{len(missing)} of {len(assets)} assets missing: {', '.join(missing)}"
    return True, f"index.html and {len(assets)} assets served"


def check_api_docs_closed(base_url: str, get=http_get) -> tuple[bool, str]:
    """Interactive API docs and the OpenAPI schema are off (``public_api_docs_enabled = false``).

    With docs off, both paths fall through to the SPA, so the check looks at what is served.
    """
    schema = get(urljoin(base_url, "/openapi.json"))
    docs = get(urljoin(base_url, "/apidocs"))
    exposed = []
    if schema.status == 200 and "openapi" in _json(schema):
        exposed.append("/openapi.json serves the OpenAPI schema")
    if docs.status == 200 and "swagger-ui" in docs.body.lower():
        exposed.append("/apidocs serves Swagger UI")
    return not exposed, "; ".join(exposed) or "API docs and OpenAPI schema are not served"


def check_setup_closed(base_url: str, get=http_get) -> tuple[bool, str]:
    """The public first-run setup endpoint is disabled (``public_setup_enabled = false``)."""
    response = get(urljoin(base_url, "/api/auth/setup"))
    return response.status == 404, f"/api/auth/setup answered HTTP {response.status}"


def websocket_close_code(base_url: str, path: str) -> tuple[int, int | None]:
    """Open a WebSocket without credentials; return the handshake status and the server's close code."""
    parts = urlsplit(base_url)
    secure = parts.scheme == "https"
    port = parts.port or (443 if secure else 80)
    raw = socket.create_connection((parts.hostname, port), timeout=TIMEOUT_SECONDS)
    conn = ssl.create_default_context().wrap_socket(raw, server_hostname=parts.hostname) if secure else raw
    try:
        key = base64.b64encode(os.urandom(16)).decode()
        conn.sendall(
            (
                f"GET {path} HTTP/1.1\r\nHost: {parts.netloc}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n"
                f"Origin: {parts.scheme}://{parts.netloc}\r\n\r\n"
            ).encode()
        )
        reader = conn.makefile("rb")
        status = int(reader.readline().split()[1])
        while reader.readline() not in (b"\r\n", b""):
            pass
        if status != 101:
            return status, None
        # Read frames until the server's close frame (opcode 8); its payload starts with the code.
        while True:
            header = reader.read(2)
            if len(header) < 2:
                return status, None
            opcode, length = header[0] & 0x0F, header[1] & 0x7F
            if length == 126:
                length = int.from_bytes(reader.read(2), "big")
            elif length == 127:
                length = int.from_bytes(reader.read(8), "big")
            payload = reader.read(length)
            if opcode == 8:
                return status, int.from_bytes(payload[:2], "big") if len(payload) >= 2 else None
    finally:
        conn.close()


def check_chat_websocket_rejects_anonymous(base_url: str, probe=websocket_close_code) -> tuple[bool, str]:
    """``/ws/chat`` turns away a client without a session (close 1008, or a 401/403 handshake)."""
    status, code = probe(base_url, "/ws/chat")
    if status == 101:
        return code == WS_POLICY_VIOLATION, f"handshake 101, close code {code}"
    return status in (401, 403), f"handshake answered HTTP {status}"


def check_tls_certificate(base_url: str, now: datetime | None = None) -> tuple[bool, str]:
    """The TLS certificate is valid for at least ``MIN_CERT_DAYS`` more days."""
    parts = urlsplit(base_url)
    if parts.scheme != "https":
        return True, "not an https URL"
    context = ssl.create_default_context()
    with (
        socket.create_connection((parts.hostname, parts.port or 443), timeout=TIMEOUT_SECONDS) as raw,
        context.wrap_socket(raw, server_hostname=parts.hostname) as conn,
    ):
        not_after = conn.getpeercert()["notAfter"]
    expires = datetime.fromtimestamp(ssl.cert_time_to_seconds(not_after), UTC)
    days = (expires - (now or datetime.now(UTC))).days
    return days >= MIN_CERT_DAYS, f"expires {expires:%Y-%m-%d} ({days} days left, minimum {MIN_CERT_DAYS})"


def smoke_login(base_url: str, username: str, password: str, post=http_post_json) -> str:
    """Sign in the smoke-test user and return its bearer token."""
    response = post(urljoin(base_url, "/api/auth/login"), {"username": username, "password": password})
    token = _json(response).get("access_token")
    if response.status != 200 or not token:
        raise RuntimeError(f"smoke-test login answered HTTP {response.status}")
    return token


def check_trigger_worker(base_url: str, token: str, get=http_get) -> tuple[bool, str]:
    """The durable trigger worker is enabled and running in the live process."""
    response = get(urljoin(base_url, "/api/runtime/triggers/worker"), token)
    data = _json(response)
    ok = response.status == 200 and data.get("enabled") is True and data.get("running") is True
    return ok, f"HTTP {response.status}, enabled {data.get('enabled')!r}, running {data.get('running')!r}"


def check_status_overview(base_url: str, token: str, get=http_get) -> tuple[bool, str]:
    """The status overview reports no degraded service and no outage."""
    response = get(urljoin(base_url, "/api/status/summary"), token)
    if response.status != 200:
        return False, f"/api/status/summary answered HTTP {response.status}"
    data = _json(response)
    overall = data.get("overall_status")
    unhealthy = [
        f"{service.get('name')}: {service.get('status')} ({service.get('summary')})"
        for service in data.get("services", [])
        if service.get("status") in ("degraded", "outage")
    ]
    detail = f"overall {overall!r}" + (f"; {'; '.join(unhealthy)}" if unhealthy else "")
    return overall not in ("degraded", "outage"), detail


def build_checks(
    base_url: str, expected_version: str, username: str | None, password: str | None
) -> list[tuple[str, bool, Callable[[], tuple[bool, str]]]]:
    """The public and signed-in checks for one release, in reporting order."""
    checks: list[tuple[str, bool, Callable[[], tuple[bool, str]]]] = [
        ("health-version", True, lambda: check_health_version(base_url, expected_version)),
        ("frontend-assets", True, lambda: check_frontend_assets(base_url)),
        ("api-docs-closed", True, lambda: check_api_docs_closed(base_url)),
        ("setup-closed", True, lambda: check_setup_closed(base_url)),
        ("chat-ws-anonymous", True, lambda: check_chat_websocket_rejects_anonymous(base_url)),
        ("tls-certificate", True, lambda: check_tls_certificate(base_url)),
    ]
    if not (username and password):
        missing = "JANUS_SMOKE_USERNAME/JANUS_SMOKE_PASSWORD are not set; signed-in checks skipped"
        checks.append(("smoke-user", False, lambda: (False, missing)))
        return checks

    token: dict[str, str] = {}

    def signed_in(check: Callable[[str, str], tuple[bool, str]]) -> Callable[[], tuple[bool, str]]:
        def run() -> tuple[bool, str]:
            if "value" not in token:
                token["value"] = smoke_login(base_url, username, password)
            return check(base_url, token["value"])

        return run

    checks += [
        ("trigger-worker", True, signed_in(check_trigger_worker)),
        ("status-overview", True, signed_in(check_status_overview)),
    ]
    return checks


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
    """Parse arguments, run the checks and return the exit code."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", required=True, help="Public Janus URL, e.g. https://janus.example.org")
    parser.add_argument("--expected-version", required=True)
    args = parser.parse_args(argv)
    if urlsplit(args.base_url).scheme not in ("http", "https"):
        parser.error("--base-url must be an http(s) URL")
    checks = build_checks(
        args.base_url.rstrip("/") + "/",
        args.expected_version,
        os.environ.get("JANUS_SMOKE_USERNAME"),
        os.environ.get("JANUS_SMOKE_PASSWORD"),
    )
    return report(run_checks(checks))


if __name__ == "__main__":
    sys.exit(main())
