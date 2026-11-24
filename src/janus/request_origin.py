"""Helpers for reconstructing the externally visible request origin."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from functools import cache
from http import HTTPStatus
from ipaddress import ip_address
from typing import TypeVar
from urllib.parse import urlsplit

from fastapi import Request
from starlette.datastructures import URL, Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from janus.config import Settings
from janus.config import settings as app_settings


def _split_header_values(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


T = TypeVar("T")


def _select_trusted_value(values: Sequence[T], trusted_proxy_hops: int) -> T | None:  # noqa: UP047
    if trusted_proxy_hops <= 0 or not values:
        return None
    if len(values) >= trusted_proxy_hops:
        return values[-trusted_proxy_hops]
    return None


def _parse_forwarded_header(value: str | None) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for item in _split_header_values(value):
        params: dict[str, str] = {}
        for part in item.split(";"):
            if "=" not in part:
                continue
            key, raw_value = part.split("=", 1)
            params[key.strip().lower()] = raw_value.strip().strip('"')
        if params:
            entries.append(params)
    return entries


def _parse_forwarded_client_ip(value: str | None) -> str | None:
    if value is None:
        return None

    candidate = value.strip().strip('"')
    if not candidate or candidate.lower() == "unknown" or candidate.startswith("_"):
        return None

    if candidate.startswith("["):
        host, separator, remainder = candidate[1:].partition("]")
        if not separator or not host:
            return None
        if remainder and not remainder.startswith(":"):
            return None
        candidate = host
    elif candidate.count(":") == 1 and "." in candidate:
        candidate = candidate.rsplit(":", 1)[0]

    try:
        return str(ip_address(candidate))
    except ValueError:
        return None


def _split_host_port(host: str, scheme: str) -> tuple[str, int] | None:
    try:
        parsed = urlsplit(f"{scheme}://{host}")
    except ValueError:
        return None
    if parsed.hostname is None:
        return None
    default_port = 443 if scheme in {"https", "wss"} else 80
    return parsed.hostname, parsed.port or default_port


def _parse_host_header(host: str) -> tuple[str, int | None] | None:
    try:
        parsed = urlsplit(f"http://{host}")
    except ValueError:
        return None
    if parsed.hostname is None:
        return None
    return parsed.hostname, parsed.port


def _host_is_allowed(host: str | None, allowed_hosts: Sequence[str]) -> bool:
    if host is None:
        return False

    requested = _parse_host_header(host)
    if requested is None:
        return False
    requested_name, requested_port = requested

    for allowed_name, allowed_port in _parsed_allowed_hosts(tuple(allowed_hosts)):
        if requested_name != allowed_name:
            continue
        if allowed_port is not None and requested_port != allowed_port:
            continue
        return True

    return False


def _origin_is_allowed(origin: str | None, allowed_origins: Sequence[str]) -> bool:
    requested = _parse_origin(origin)
    if requested is None:
        return False

    for allowed in _parsed_allowed_origins(tuple(allowed_origins)):  # noqa: SIM110
        if allowed == requested:
            return True

    return False


@cache
def _parsed_allowed_hosts(allowed_hosts: tuple[str, ...]) -> tuple[tuple[str, int | None], ...]:
    parsed_hosts = []
    for allowed_host in allowed_hosts:
        parsed_allowed = _parse_host_header(allowed_host)
        if parsed_allowed is None:
            raise ValueError(f"Invalid host allowlist entry: {allowed_host!r}")
        parsed_hosts.append(parsed_allowed)
    return tuple(parsed_hosts)


def _parse_origin(origin: str | None) -> tuple[str, str, int] | None:
    if origin is None:
        return None

    value = origin.strip()
    if not value:
        return None

    try:
        parsed = urlsplit(value)
    except ValueError:
        return None

    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"} or parsed.hostname is None:
        return None
    if parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password:
        return None

    default_port = 80 if scheme == "http" else 443
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    return scheme, host, parsed.port or default_port


@cache
def _parsed_allowed_origins(allowed_origins: tuple[str, ...]) -> tuple[tuple[str, str, int], ...]:
    parsed_origins = []
    for allowed_origin in allowed_origins:
        parsed_allowed = _parse_origin(allowed_origin)
        if parsed_allowed is None:
            continue
        parsed_origins.append(parsed_allowed)
    return tuple(parsed_origins)


def _public_endpoint_is_disabled(path: str, settings: Settings) -> bool:
    if settings.public_api_docs_enabled:
        return False
    return (
        path == "/openapi.json"
        or path == "/apidocs"
        or path.startswith("/apidocs/")
        or path == "/apiredoc"
        or path.startswith("/apiredoc/")
    )


def _security_headers() -> dict[str, str]:
    return {
        "Content-Security-Policy": (
            "default-src 'self'; "
            "base-uri 'self'; "
            "connect-src 'self'; "
            "font-src 'self' data:; "
            "frame-ancestors 'none'; "
            "img-src 'self' data:; "
            "object-src 'none'; "
            "script-src 'self'; "
            "style-src 'self' 'unsafe-inline'"
        ),
        "X-Frame-Options": "DENY",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "strict-origin-when-cross-origin",
    }


def _normalize_scheme(scope_type: str, scheme: str | None) -> str | None:
    if scheme is None:
        return None

    normalized = scheme.lower()
    if scope_type == "websocket":
        if normalized == "https":
            return "wss"
        if normalized == "http":
            return "ws"
        if normalized in {"ws", "wss"}:
            return normalized
        return None

    if normalized == "wss":
        return "https"
    if normalized == "ws":
        return "http"
    if normalized in {"http", "https"}:
        return normalized
    return None


def _trusted_forwarded_origin(headers: Mapping[str, str], trusted_proxy_hops: int) -> tuple[str | None, str | None]:
    if trusted_proxy_hops <= 0:
        return None, None

    forwarded_entries = _parse_forwarded_header(headers.get("forwarded"))
    if forwarded_entries:
        forwarded_entry = _select_trusted_value(forwarded_entries, trusted_proxy_hops)
        if forwarded_entry is not None:
            return forwarded_entry.get("proto"), forwarded_entry.get("host")

    scheme = _select_trusted_value(_split_header_values(headers.get("x-forwarded-proto")), trusted_proxy_hops)
    host = _select_trusted_value(_split_header_values(headers.get("x-forwarded-host")), trusted_proxy_hops)
    return scheme, host


def _trusted_forwarded_client_ip(headers: Mapping[str, str], trusted_proxy_hops: int) -> str | None:
    if trusted_proxy_hops <= 0:
        return None

    forwarded_entries = _parse_forwarded_header(headers.get("forwarded"))
    if forwarded_entries:
        forwarded_entry = _select_trusted_value(forwarded_entries, trusted_proxy_hops)
        if forwarded_entry is not None:
            return _parse_forwarded_client_ip(forwarded_entry.get("for"))

    forwarded_for = _select_trusted_value(_split_header_values(headers.get("x-forwarded-for")), trusted_proxy_hops)
    return _parse_forwarded_client_ip(forwarded_for)


class TrustedProxyHeadersMiddleware:
    """Applies trusted forwarded headers and production request hardening."""

    def __init__(self, app: ASGIApp, trusted_proxy_hops: int = 0, settings: Settings | None = None) -> None:
        """Initializes the middleware with the configured trusted proxy depth."""
        self.app = app
        self.trusted_proxy_hops = trusted_proxy_hops
        self.settings = settings or app_settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Updates request scope origin fields before handing off to the app."""
        trusted_proxy_hops = self.settings.trusted_proxy_hops
        if trusted_proxy_hops == 0:
            trusted_proxy_hops = self.trusted_proxy_hops
        if trusted_proxy_hops > 0 and scope["type"] in {"http", "websocket"}:
            self._apply_trusted_origin(scope, trusted_proxy_hops)

        if scope["type"] in {"http", "websocket"} and not self._host_allowed(scope):
            await self._reject_host(scope, send)
            return

        if scope["type"] == "websocket" and not self._origin_allowed(scope):
            await self._reject_origin(send)
            return

        if scope["type"] == "http" and _public_endpoint_is_disabled(scope["path"], self.settings):
            await self._send_http_response(send, HTTPStatus.NOT_FOUND, "Not Found")
            return

        if scope["type"] in {"http", "websocket"}:
            await self.app(scope, receive, self._secure_send(send))
            return

        await self.app(scope, receive, send)

    def _apply_trusted_origin(self, scope: Scope, trusted_proxy_hops: int) -> None:
        headers = Headers(scope=scope)
        forwarded_scheme, forwarded_host = _trusted_forwarded_origin(headers, trusted_proxy_hops)

        normalized_scheme = _normalize_scheme(scope["type"], forwarded_scheme)
        if normalized_scheme is not None:
            scope["scheme"] = normalized_scheme

        if forwarded_host:
            applied_scheme = scope["scheme"]
            host_and_port = _split_host_port(forwarded_host, applied_scheme)
            if host_and_port is None:
                return

            scope["server"] = host_and_port
            mutable_headers = MutableHeaders(scope=scope)
            mutable_headers["host"] = forwarded_host

    def _host_allowed(self, scope: Scope) -> bool:
        headers = Headers(scope=scope)
        return _host_is_allowed(headers.get("host"), self.settings.allowed_hosts)

    def _origin_allowed(self, scope: Scope) -> bool:
        headers = Headers(scope=scope)
        return _origin_is_allowed(headers.get("origin"), self.settings.allowed_origins)

    async def _reject_host(self, scope: Scope, send: Send) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008, "reason": "Invalid Host header"})
            return
        await self._send_http_response(send, HTTPStatus.BAD_REQUEST, "Invalid Host header")

    async def _reject_origin(self, send: Send) -> None:
        await send({"type": "websocket.close", "code": 1008, "reason": "Invalid Origin header"})

    async def _send_http_response(self, send: Send, status_code: HTTPStatus, body: str) -> None:
        headers = [(b"content-type", b"text/plain; charset=utf-8")]
        for key, value in _security_headers().items():
            headers.append((key.lower().encode("latin-1"), value.encode("latin-1")))

        await send({"type": "http.response.start", "status": int(status_code), "headers": headers})
        await send({"type": "http.response.body", "body": body.encode("utf-8")})

    def _secure_send(self, send: Send) -> Send:
        async def secure_send(message: Message) -> None:
            if message["type"] in {"http.response.start", "websocket.accept"}:
                raw_headers = message.setdefault("headers", [])
                headers = MutableHeaders(raw=raw_headers)
                for key, value in _security_headers().items():
                    if key not in headers:
                        headers[key] = value
            await send(message)

        return secure_send


def is_secure_request(request: Request) -> bool:
    """Returns whether the externally visible request was HTTPS."""
    return request.url.scheme == "https"


def request_client_ip(request: Request, settings: Settings | None = None) -> str | None:
    """Returns the trusted client IP when reverse-proxy headers are configured."""
    effective_settings = settings or app_settings
    if effective_settings.trusted_proxy_hops > 0:
        return _trusted_forwarded_client_ip(request.headers, effective_settings.trusted_proxy_hops)
    if request.client is None:
        return None
    return request.client.host


def external_url(request: Request, path: str) -> str:
    """Builds an absolute same-origin URL for the current external request."""
    base = str(request.base_url).rstrip("/")
    if not path.startswith("/"):
        path = f"/{path}"
    return f"{base}{path}"


_DEFAULT_BASE_URL = "http://localhost:8080"


def public_base_url(request: Request, settings: Settings | None = None) -> str:
    """Returns the public base URL OAuth redirects should use, without a trailing slash.

    Prefers the operator-configured ``base_url`` so reverse-proxy deployments produce the
    right redirect URI even when the upstream sees an internal address; the default
    ``http://localhost:8080`` counts as unset and falls back to the request's own origin.
    """
    effective_settings = settings or app_settings
    configured = (effective_settings.base_url or "").strip().rstrip("/")
    if configured and configured != _DEFAULT_BASE_URL:
        return str(URL(configured).replace(path="", query="", fragment="")).rstrip("/")
    return str(request.base_url).rstrip("/")
