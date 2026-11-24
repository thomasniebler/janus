"""Synchronous loopback HTTP client that preserves exact request paths."""

from __future__ import annotations

import atexit
import json
import socket
import threading
import time
from collections.abc import Mapping
from http.client import HTTPResponse
from http.cookies import SimpleCookie
from typing import Any
from urllib.parse import urlencode, urljoin, urlsplit

import httpx
import uvicorn
from websockets.sync.client import ClientConnection
from websockets.sync.client import connect as ws_connect

_REDIRECT_STATUS_CODES = {301, 302, 303, 307, 308}


class WebSocketTestSession:
    """Thin wrapper around a real ``websockets`` sync connection for tests."""

    def __init__(self, connection: ClientConnection, inflight: _InflightRequests | None = None) -> None:
        """Store the live WebSocket connection to the loopback test server."""
        self._connection = connection
        self._inflight = inflight
        self._open = inflight is not None
        if inflight is not None:
            inflight.client_socket_opened()

    def __enter__(self) -> WebSocketTestSession:
        """Return this session for use in a ``with`` block."""
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        """Close the underlying connection when the ``with`` block exits."""
        self.close()

    def send_text(self, data: str) -> None:
        """Send a text frame."""
        self._connection.send(data)

    def send_json(self, data: Any) -> None:
        """Send a JSON-encoded text frame."""
        self._connection.send(json.dumps(data))

    def receive_text(self, timeout: float | None = 30) -> str:
        """Wait for and return the next text frame."""
        message = self._connection.recv(timeout=timeout)
        if isinstance(message, bytes):
            return message.decode("utf-8")
        return message

    def receive_json(self, timeout: float | None = 30) -> Any:
        """Wait for the next frame and decode it as JSON."""
        return json.loads(self.receive_text(timeout=timeout))

    def close(self, code: int = 1000) -> None:
        """Close the connection (ignoring errors if it is already closed) and wait for the server's handler to end.

        A handler keeps working after its last frame (``/ws/chat`` commits the
        title and memory suggestion after ``__END__``, then closes its
        sessions). Waiting until it has returned keeps that late work off the
        shared test connection while the test continues.
        """
        try:  # noqa: SIM105
            self._connection.close(code=code)
        except Exception:
            pass
        if self._open and self._inflight is not None:
            self._open = False
            self._inflight.client_socket_closed()


class _SocketResponseAdapter:
    """Expose a socket file handle with ``makefile`` for ``HTTPResponse``."""

    def __init__(self, sock: socket.socket) -> None:
        """Store the live socket used for one HTTP response."""
        self.sock = sock

    def makefile(self, mode: str) -> Any:
        """Return a file-like wrapper for ``HTTPResponse`` parsing."""
        return self.sock.makefile(mode)


class _InflightRequests:
    """ASGI wrapper that counts HTTP requests the app has not fully finished.

    FastAPI sends the response *before* it exits request-scoped ``yield``
    dependencies, so ``get_db``'s ``db.close()`` (a ``ROLLBACK``) runs on the
    server thread after the client already has the response. The test
    database is one shared in-memory SQLite connection (``StaticPool``), so
    that late rollback could discard a write the test thread had just flushed
    but not yet committed (an intermittent ``Could not refresh instance``).
    Waiting until the app has returned makes every request fully finished
    before the test continues.
    """

    def __init__(self, app: Any) -> None:
        """Wrap ``app`` and start with no requests in flight."""
        self.app = app
        self._inflight = 0
        self._sockets = 0
        self._client_sockets = 0
        self._idle = threading.Condition()

    async def __call__(self, scope, receive, send) -> None:
        """Forward to the app, counting HTTP requests and WebSocket handlers until the app returns."""
        if scope["type"] == "websocket":
            await self._call_websocket(scope, receive, send)
            return
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        with self._idle:
            self._inflight += 1
        try:
            await self.app(scope, receive, send)
        finally:
            with self._idle:
                self._inflight -= 1
                self._idle.notify_all()

    async def _call_websocket(self, scope, receive, send) -> None:
        with self._idle:
            self._sockets += 1
        try:
            await self.app(scope, receive, send)
        finally:
            with self._idle:
                self._sockets -= 1
                self._idle.notify_all()

    def client_socket_opened(self) -> None:
        """Count a test WebSocket that is open on the client side."""
        with self._idle:
            self._client_sockets += 1

    def client_socket_closed(self, timeout: float = 10) -> None:
        """Block until no more WebSocket handlers run than test sockets are still open.

        Server handlers are not matched to client sockets one by one, so this
        waits for the counts: once a test closed a socket, its handler (or one
        that already ended) has returned.
        """
        with self._idle:
            self._client_sockets -= 1
            if not self._idle.wait_for(lambda: self._sockets <= self._client_sockets, timeout=timeout):
                raise RuntimeError("Timed out waiting for the test server to finish a WebSocket handler")

    def wait_until_idle(self, timeout: float = 10) -> None:
        """Block until no HTTP request is still running inside the app."""
        with self._idle:
            if not self._idle.wait_for(lambda: self._inflight == 0, timeout=timeout):
                raise RuntimeError("Timed out waiting for the test server to finish a request")


class SyncASGIClient:
    """Sync HTTP test client backed by a local uvicorn server."""

    # Keyed by id(app) so distinct app instances (e.g. a test-local
    # `create_fastapi_app()` build) each get their own loopback server
    # instead of silently reusing whichever app happened to start first.
    _shared_servers: dict[int, tuple[uvicorn.Server, threading.Thread, str, _InflightRequests]] = {}
    _server_lock = threading.Lock()

    def __init__(
        self,
        app: Any,
        *,
        base_url: str = "http://testserver",
        headers: Mapping[str, str] | None = None,
        raise_server_exceptions: bool = True,
    ) -> None:
        """Initialize the client with app state, defaults, and cookie storage."""
        self.app = app
        self.base_url = base_url.rstrip("/")
        self.headers = httpx.Headers(headers or {})
        self.cookies = httpx.Cookies()
        self._raise_server_exceptions = raise_server_exceptions
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self._server_url: str | None = None
        self._inflight: _InflightRequests | None = None

    def __enter__(self) -> SyncASGIClient:
        """Start the shared loopback server and return this client."""
        self._start_server()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        """Leave the shared loopback server running for later test reuse."""
        return None

    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """Send one request and optionally follow redirects."""
        follow_redirects = kwargs.pop("follow_redirects", True)
        max_redirects = kwargs.pop("max_redirects", 10)
        response = self._send_once(method, url, **kwargs)

        redirects_followed = 0
        current_method = method
        current_url = url
        while follow_redirects and response.status_code in _REDIRECT_STATUS_CODES:
            location = response.headers.get("location")
            if not location or redirects_followed >= max_redirects:
                break
            redirects_followed += 1
            current_url = urljoin(self._absolute_url(current_url), location)
            if response.status_code in {302, 303}:
                current_method = "GET"
                kwargs.pop("json", None)
                kwargs.pop("content", None)
            response = self._send_once(current_method, current_url, **kwargs)
        return response

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        """Send a GET request."""
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> httpx.Response:
        """Send a POST request."""
        return self.request("POST", url, **kwargs)

    def put(self, url: str, **kwargs: Any) -> httpx.Response:
        """Send a PUT request."""
        return self.request("PUT", url, **kwargs)

    def patch(self, url: str, **kwargs: Any) -> httpx.Response:
        """Send a PATCH request."""
        return self.request("PATCH", url, **kwargs)

    def delete(self, url: str, **kwargs: Any) -> httpx.Response:
        """Send a DELETE request."""
        return self.request("DELETE", url, **kwargs)

    def websocket_connect(self, url: str, **kwargs: Any) -> WebSocketTestSession:
        """Open a real WebSocket connection to the loopback test server."""
        if self._server_url is None:
            raise RuntimeError("Client server has not been started")

        absolute_url = self._absolute_url(url)
        parsed = urlsplit(absolute_url)
        request_target = parsed.path or "/"
        if parsed.query:
            request_target = f"{request_target}?{parsed.query}"

        # Build the WebSocket URI against the *logical* base_url host (e.g.
        # "testserver") so the library's own Host header matches what the
        # app's allowed_hosts checks expect, then connect the socket to the
        # real loopback server manually. Passing our own "host" header via
        # additional_headers would only add a second Host header rather than
        # replacing the library's default, since Headers supports repeated
        # keys.
        base = urlsplit(self.base_url)
        ws_scheme = "wss" if base.scheme == "https" else "ws"
        ws_url = f"{ws_scheme}://{base.netloc}{request_target}"

        server = urlsplit(self._server_url)
        sock = socket.create_connection((server.hostname or "127.0.0.1", server.port or 80), timeout=30)

        headers = httpx.Headers(self.headers)
        request_headers = kwargs.pop("headers", None)
        if request_headers:
            for key, value in request_headers.items():
                headers[key] = value
        cookie_header = self._cookie_header()
        if cookie_header:
            headers["cookie"] = cookie_header

        origin = headers.pop("origin", None)
        connection = ws_connect(
            ws_url,
            sock=sock,
            additional_headers=list(headers.multi_items()),
            origin=origin,
            # We manage the connection's lifecycle via WebSocketTestSession
            # instead of a `with connect(...) as ws:` block.
            legacy=True,
            **kwargs,
        )
        return WebSocketTestSession(connection, self._inflight)

    def _send_once(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        absolute_url = self._absolute_url(url)
        parsed = urlsplit(absolute_url)
        request_target = parsed.path or "/"

        query_string = self._query_string(kwargs.pop("params", None))
        if parsed.query:
            query_string = parsed.query
        if query_string:
            request_target = f"{request_target}?{query_string}"

        files = kwargs.pop("files", None)
        body = self._encode_body(kwargs.pop("json", None), kwargs.pop("content", None))
        multipart_type = None
        if files is not None:
            # Let httpx build the multipart body and its boundary, then send it raw.
            encoded = httpx.Request("POST", "http://testserver/", files=files)
            body = encoded.read()
            multipart_type = encoded.headers["content-type"]
        merged_headers = httpx.Headers(self.headers)
        request_headers = kwargs.pop("headers", None)
        if request_headers:
            for key, value in request_headers.items():
                merged_headers[key] = value

        merged_headers["host"] = urlsplit(self.base_url).netloc
        merged_headers["connection"] = "close"
        cookie_header = self._cookie_header()
        if cookie_header:
            merged_headers["cookie"] = cookie_header
        if multipart_type is not None:
            merged_headers["content-type"] = multipart_type
        if body is not None and "content-type" not in merged_headers:
            merged_headers["content-type"] = "application/json"
        if body is not None:
            merged_headers["content-length"] = str(len(body))

        response = self._perform_socket_request(method.upper(), request_target, merged_headers, body)
        if self._inflight is not None:
            self._inflight.wait_until_idle()
        self._store_response_cookies(response.headers)
        return response

    def _perform_socket_request(
        self,
        method: str,
        request_target: str,
        headers: httpx.Headers,
        body: bytes | None,
    ) -> httpx.Response:
        server_url = self._server_url
        if server_url is None:
            raise RuntimeError("Client server has not been started")

        server = urlsplit(server_url)
        sock = socket.create_connection((server.hostname or "127.0.0.1", server.port or 80), timeout=10)
        try:
            request_lines = [f"{method} {request_target} HTTP/1.1"]
            for key, value in headers.multi_items():
                request_lines.append(f"{key}: {value}")
            request_blob = ("\r\n".join(request_lines) + "\r\n\r\n").encode("latin-1")
            if body:
                request_blob += body
            sock.sendall(request_blob)

            raw_response = HTTPResponse(_SocketResponseAdapter(sock))
            raw_response.begin()
            content = raw_response.read()
            raw_headers = raw_response.getheaders()
            response_headers = httpx.Headers(raw_headers)
            return httpx.Response(
                status_code=raw_response.status,
                headers=response_headers,
                content=content,
                request=httpx.Request(method, f"{server_url}{request_target}", headers=headers),
            )
        finally:
            sock.close()

    def _start_server(self) -> None:
        app_key = id(self.app)
        with self._server_lock:
            cached = self._shared_servers.get(app_key)
            if cached is not None:
                self._server, self._thread, self._server_url, self._inflight = cached
                return

            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as temp_sock:
                temp_sock.bind(("127.0.0.1", 0))
                _, port = temp_sock.getsockname()

            inflight = _InflightRequests(self.app)
            config = uvicorn.Config(
                inflight,
                host="127.0.0.1",
                port=port,
                log_level="error",
                access_log=False,
                # Tests exercise this app's own reverse-proxy header handling
                # (trusted_proxy_hops); uvicorn's built-in proxy-headers
                # support would otherwise blindly trust X-Forwarded-* from
                # the loopback client and mask bugs in that handling.
                proxy_headers=False,
            )
            server = uvicorn.Server(config)
            server.install_signal_handlers = lambda: None

            thread = threading.Thread(target=server.run, daemon=True)
            thread.start()

            deadline = time.time() + 10
            while time.time() < deadline:
                if server.started:
                    server_url = f"http://127.0.0.1:{port}"
                    self._server = server
                    self._thread = thread
                    self._server_url = server_url
                    self._inflight = inflight
                    self.__class__._shared_servers[app_key] = (server, thread, server_url, inflight)
                    atexit.register(self._stop_shared_server, app_key)
                    return
                if not thread.is_alive():
                    break
                time.sleep(0.05)
        raise RuntimeError("Timed out starting uvicorn test server")

    def _stop_server(self) -> None:
        if self._server is None:
            return
        self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=10)
        self._server = None
        self._thread = None
        self._server_url = None

    @classmethod
    def _stop_shared_server(cls, app_key: int) -> None:
        cached = cls._shared_servers.pop(app_key, None)
        if cached is None:
            return
        server, thread, _, _ = cached
        server.should_exit = True
        thread.join(timeout=10)

    def _absolute_url(self, url: str) -> str:
        if "://" in url:
            return url
        return f"{self.base_url}{url}"

    def _cookie_header(self) -> str:
        jar = self.cookies.jar
        if not jar:
            return ""
        return "; ".join(f"{cookie.name}={cookie.value}" for cookie in jar)

    def _store_response_cookies(self, headers: httpx.Headers) -> None:
        for header_value in headers.get_list("set-cookie"):
            cookie = SimpleCookie()
            cookie.load(header_value)
            for morsel in cookie.values():
                if morsel["max-age"] == "0" or morsel.value == "":
                    self.cookies.delete(morsel.key)
                    continue
                self.cookies.set(morsel.key, morsel.value)

    def _query_string(self, params: Any) -> str:
        if not params:
            return ""
        return urlencode(params, doseq=True)

    def _encode_body(self, json_body: Any, content: Any) -> bytes | None:
        if json_body is not None:
            return json.dumps(json_body).encode("utf-8")
        if content is None:
            return None
        if isinstance(content, bytes):
            return content
        if isinstance(content, str):
            return content.encode("utf-8")
        raise TypeError(f"Unsupported request body type: {type(content)!r}")
