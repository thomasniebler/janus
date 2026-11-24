"""Managed MCP server lifecycle, HTTP transport, and OAuth PKCE services."""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import logging
import secrets
import socket
from datetime import timedelta
from typing import Any
from urllib.parse import urlencode, urlsplit, urlunsplit

import httpx
from sqlalchemy.orm import Session, selectinload

from janus import config, models, security, time_utils
from janus.services import mcp_metadata as metadata
from janus.services import mcp_static_token as static_tokens
from janus.services.mcp_errors import AUTH_REQUIRED, MCPServiceError
from janus.services.mcp_status import mark_failed, reset_discovery

logger = logging.getLogger(__name__)

MCP_OAUTH_CALLBACK_PATH = "/api/tools/oauth/callback"
MCP_HTTP_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=10.0, pool=5.0)
MCP_MAX_RESPONSE_BYTES = 1_000_000
MCP_PROTOCOL_VERSION = "2025-03-26"
MCP_OAUTH_STATE_TTL = timedelta(minutes=10)


def _is_safe_address(value: str) -> bool:
    """Return whether an address is safe for a user-configured outbound request."""
    address = ipaddress.ip_address(value)
    return address.is_loopback or (
        address.is_global
        and not address.is_private
        and not address.is_link_local
        and not address.is_multicast
        and not address.is_reserved
        and not address.is_unspecified
    )


def _resolved_addresses(hostname: str, port: int) -> list[str]:
    """Resolve a hostname for SSRF checks without making a network request."""
    try:
        return sorted({item[4][0] for item in socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)})
    except (OSError, ValueError) as exc:
        raise MCPServiceError("The MCP endpoint host could not be resolved safely.") from exc


def validate_mcp_endpoint(endpoint: str, *, allow_http_loopback: bool = True) -> str:
    """Validate and normalize a remote MCP endpoint before any outbound request.

    Loopback HTTP is intentionally available for local prototypes. All other
    HTTP endpoints require TLS, and DNS/IP results must not resolve to private
    or otherwise special-use networks.
    """
    value = endpoint.strip()
    if len(value) > 2048:
        raise MCPServiceError("The MCP endpoint is too long.")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise MCPServiceError("The MCP endpoint is invalid.") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or not parsed.hostname:
        raise MCPServiceError("The MCP endpoint must be an absolute HTTP(S) URL.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise MCPServiceError("The MCP endpoint must not contain credentials, a query, or a fragment.")
    if port is None:
        port = 443 if parsed.scheme == "https" else 80

    addresses = _resolved_addresses(parsed.hostname, port)
    if not addresses or not all(_is_safe_address(address) for address in addresses):
        raise MCPServiceError("The MCP endpoint resolves to a blocked network address.")
    loopback_only = all(ipaddress.ip_address(address).is_loopback for address in addresses)
    if parsed.scheme == "http" and (not allow_http_loopback or not loopback_only):
        raise MCPServiceError("Non-loopback MCP endpoints must use HTTPS.")

    normalized_netloc = parsed.hostname
    if ":" in normalized_netloc and not normalized_netloc.startswith("["):
        normalized_netloc = f"[{normalized_netloc}]"
    if parsed.port is not None:
        normalized_netloc = f"{normalized_netloc}:{parsed.port}"
    return urlunsplit((parsed.scheme, normalized_netloc, parsed.path or "/", "", ""))


def _safe_oauth_endpoint(endpoint: str) -> str:
    """Validate OAuth endpoints with the same outbound SSRF policy."""
    return validate_mcp_endpoint(endpoint)


def _event(
    db: Session,
    event_type: models.EventType,
    action: str,
    message: str,
    *,
    server: models.MCPServer,
    **details: Any,
) -> None:
    """Persist a redacted MCP audit event in the request transaction, owned by the server's owner (JAN-272)."""
    db.add(
        models.Event(
            event_type=event_type,
            source="MCPRegistry",
            message=f"{action}: {message}",
            details=json.dumps({"server_id": server.id, **details}),
            user_id=server.user_id,
        )
    )


def _public_base_url(request: Any | None = None) -> str:
    """Return the configured public origin, using the request for local tests."""
    configured = (config.settings.base_url or "").strip().rstrip("/")
    if configured and configured != "http://localhost:8080":
        return configured
    return str(request.base_url).rstrip("/") if request is not None else configured


def oauth_redirect_uri(request: Any | None = None) -> str:
    """Return the exact callback URI registered for MCP OAuth clients."""
    return f"{_public_base_url(request)}{MCP_OAUTH_CALLBACK_PATH}"


def _json_response(response: httpx.Response, *, allow_empty: bool = False) -> tuple[dict[str, Any] | None, str | None]:
    """Parse one bounded JSON or server-sent-event MCP response."""
    if len(response.content) > MCP_MAX_RESPONSE_BYTES:
        raise MCPServiceError("Remote MCP response is too large.")
    if response.status_code == 401:
        raise MCPServiceError(AUTH_REQUIRED, auth_required=True, status_code=401)
    if response.status_code >= 400:
        raise MCPServiceError(
            f"Remote MCP server returned HTTP {response.status_code}.", status_code=response.status_code
        )
    if not response.content:
        if allow_empty:
            return None, response.headers.get("mcp-session-id")
        raise MCPServiceError("Remote MCP server returned an empty response.")
    content_type = response.headers.get("content-type", "")
    raw = response.text
    if "text/event-stream" in content_type:
        data_lines = [line[5:].strip() for line in raw.splitlines() if line.startswith("data:")]
        raw = data_lines[-1] if data_lines else ""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise MCPServiceError("Remote MCP server returned invalid JSON.") from exc
    if not isinstance(payload, dict):
        raise MCPServiceError("Remote MCP server returned an invalid JSON-RPC response.")
    if "error" in payload:
        raise MCPServiceError("Remote MCP server rejected the MCP request.")
    return payload, response.headers.get("mcp-session-id")


def _read_bounded_response(response: httpx.Response) -> httpx.Response:
    """Buffer a streamed response while enforcing the MCP body limit."""
    chunks: list[bytes] = []
    size = 0
    for chunk in response.iter_bytes():
        size += len(chunk)
        if size > MCP_MAX_RESPONSE_BYTES:
            raise MCPServiceError("Remote MCP response is too large.")
        chunks.append(chunk)
    return httpx.Response(
        response.status_code,
        headers=response.headers,
        content=b"".join(chunks),
        request=response.request,
    )


class MCPHTTPClient:
    """Small JSON-RPC client for the streamable HTTP MCP transport."""

    def __init__(self, endpoint: str, *, access_token: str | None = None):
        """Initialize a client after validating the destination endpoint."""
        self.endpoint = validate_mcp_endpoint(endpoint)
        self.access_token = access_token
        self.session_id: str | None = None
        self._request_id = 0

    def _post(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        notification: bool = False,
    ) -> dict[str, Any] | None:
        """Send one bounded JSON-RPC request and return its safe result."""
        if not notification:
            self._request_id += 1
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if not notification:
            payload["id"] = self._request_id
        if params is not None:
            payload["params"] = params
        headers = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
        if self.session_id:
            headers["mcp-session-id"] = self.session_id
        if self.access_token:
            headers["authorization"] = f"Bearer {self.access_token}"
        with httpx.Client(timeout=MCP_HTTP_TIMEOUT, follow_redirects=False) as client:  # noqa: SIM117
            with client.stream("POST", self.endpoint, json=payload, headers=headers) as streamed_response:
                if streamed_response.status_code == 401:
                    raise MCPServiceError(AUTH_REQUIRED, auth_required=True, status_code=401)
                if streamed_response.status_code >= 400:
                    code = streamed_response.status_code
                    raise MCPServiceError(f"Remote MCP server returned HTTP {code}.", status_code=code)
                response = _read_bounded_response(streamed_response)
        result, session_id = _json_response(response, allow_empty=notification)
        if session_id:
            self.session_id = session_id
        return result

    def discover_tools(self) -> list[dict[str, Any]]:
        """Initialize the MCP session and return validated tool metadata."""
        initialized = self._post(
            "initialize",
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "Janus", "version": "0.1"},
            },
        )
        if not initialized or not isinstance(initialized.get("result"), dict):
            raise MCPServiceError("Remote MCP server returned an invalid initialize response.")
        self._post("notifications/initialized", notification=True)
        response = self._post("tools/list")
        result = response.get("result") if response else None
        tools = result.get("tools") if isinstance(result, dict) else None
        if not isinstance(tools, list):
            raise MCPServiceError("Remote MCP server returned no tool list.")
        normalized: list[dict[str, Any]] = []
        for item in tools:
            if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not item["name"].strip():
                continue
            input_schema = item.get("inputSchema")
            normalized.append(
                {
                    "upstream_name": item["name"].strip(),
                    "description": str(item.get("description") or "")[: metadata.MAX_DESCRIPTION_CHARS],
                    "input_schema": metadata.bound_schema(input_schema),
                    "annotations": metadata.normalize_annotations(item.get("annotations")),
                }
            )
        return normalized

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        """Invoke one discovered tool through the same bounded MCP boundary."""
        response = self._post("tools/call", {"name": name, "arguments": arguments})
        result = response.get("result") if response else None
        if not isinstance(result, dict):
            raise MCPServiceError("Remote MCP server returned an invalid tool result.")
        return result


class MCPService:
    """Persistence and lifecycle operations for managed MCP connections."""

    def __init__(self, db: Session):
        """Initialize a service using the request-owned database session."""
        self.db = db

    def get_server(self, user_id: int, server_id: int) -> models.MCPServer | None:
        """Load one server only when it belongs to the authenticated owner."""
        return (
            self.db.query(models.MCPServer)
            .options(selectinload(models.MCPServer.discovered_tools), selectinload(models.MCPServer.credentials))
            .filter(models.MCPServer.id == server_id, models.MCPServer.user_id == user_id)
            .first()
        )

    def list_servers(self, user_id: int) -> list[models.MCPServer]:
        """List only the current user's server records."""
        return (
            self.db.query(models.MCPServer)
            .options(selectinload(models.MCPServer.discovered_tools), selectinload(models.MCPServer.credentials))
            .filter(models.MCPServer.user_id == user_id)
            .order_by(models.MCPServer.created_at.asc())
            .all()
        )

    def create_server(self, user: models.User, values: dict[str, Any]) -> models.MCPServer:
        """Validate and persist a disabled remote server before discovery."""
        endpoint = validate_mcp_endpoint(str(values["endpoint"]))
        self._validate_oauth_values(values)
        token = static_tokens.token_for_create(values)
        server = models.MCPServer(
            user_id=user.id,
            provider_name=str(values["provider_name"]).strip(),
            endpoint=endpoint,
            transport=models.MCPTransport.STREAMABLE_HTTP,
            enabled=False,
            oauth_authorization_endpoint=values.get("oauth_authorization_endpoint"),
            oauth_token_endpoint=values.get("oauth_token_endpoint"),
            oauth_client_id=values.get("oauth_client_id"),
            oauth_scopes=values.get("oauth_scopes"),
        )
        self.db.add(server)
        self.db.flush()
        if values.get("enabled"):
            server.enabled = True
        _event(self.db, models.EventType.SUCCESS, "connect", "MCP server registered", server=server)
        if token is not None:
            static_tokens.store_static_token(self.db, server, token)
            _event(self.db, models.EventType.INFO, "static_token", "MCP bearer token saved", server=server)
        self.db.commit()
        return self.get_server(user.id, server.id)  # type: ignore[return-value]

    @staticmethod
    def _validate_oauth_values(values: dict[str, Any]) -> None:
        """Validate optional OAuth metadata without accepting provider secrets."""
        auth_endpoint = values.get("oauth_authorization_endpoint")
        token_endpoint = values.get("oauth_token_endpoint")
        client_id = values.get("oauth_client_id")
        if any((auth_endpoint, token_endpoint, client_id)) and not all((auth_endpoint, token_endpoint, client_id)):
            raise MCPServiceError("OAuth authorization endpoint, token endpoint, and client ID are all required.")
        if auth_endpoint:
            _safe_oauth_endpoint(str(auth_endpoint))
            _safe_oauth_endpoint(str(token_endpoint))

    def update_server(self, user_id: int, server_id: int, values: dict[str, Any]) -> models.MCPServer | None:
        """Update owner-controlled metadata and invalidate stale discovery on endpoint changes."""
        server = self.get_server(user_id, server_id)
        if server is None:
            return None
        if "endpoint" in values and values["endpoint"] is not None:
            values["endpoint"] = validate_mcp_endpoint(str(values["endpoint"]))
        self._validate_oauth_values({**server.__dict__, **values})
        token, drop_static = static_tokens.token_change_for_update(server, values)
        for field, value in values.items():
            if field in {
                "endpoint",
                "provider_name",
                "enabled",
                "oauth_authorization_endpoint",
                "oauth_token_endpoint",
                "oauth_client_id",
                "oauth_scopes",
            }:
                setattr(server, field, value.strip() if isinstance(value, str) else value)
        if "endpoint" in values:
            reset_discovery(server)
        _event(self.db, models.EventType.INFO, "update", "MCP server settings updated", server=server)
        if token is not None:
            static_tokens.store_static_token(self.db, server, token)
            reset_discovery(server, only_if_failed=True)
            _event(self.db, models.EventType.INFO, "static_token", "MCP bearer token saved", server=server)
        elif drop_static:
            static_tokens.remove_static_token(self.db, server)
            reset_discovery(server, only_if_failed=True)
            _event(self.db, models.EventType.INFO, "static_token", "MCP bearer token removed", server=server)
        self.db.commit()
        return self.get_server(user_id, server.id)

    def disconnect(self, user_id: int, server_id: int) -> bool:
        """Delete a server, its discovered metadata, credentials, and pending OAuth state."""
        server = self.get_server(user_id, server_id)
        if server is None:
            return False
        _event(self.db, models.EventType.INFO, "disconnect", "MCP server disconnected", server=server)
        self.db.query(models.OAuthState).filter(models.OAuthState.mcp_server_id == server.id).delete(
            synchronize_session=False
        )
        metadata.purge_tool_policies(self.db, server.user_id, server_id=server.id)
        self.db.delete(server)
        self.db.commit()
        return True

    def discover(self, user_id: int, server_id: int) -> models.MCPServer | None:
        """Discover and transactionally replace one owner's tool snapshot."""
        server = self.get_server(user_id, server_id)
        if server is None:
            return None
        try:
            discovered = self._remote(server, lambda client: client.discover_tools())
            existing = {tool.upstream_name: tool for tool in server.discovered_tools}
            incoming_names = {item["upstream_name"] for item in discovered}
            metadata.remove_vanished(self.db, server, incoming_names)
            for item in discovered:
                tool = existing.get(item["upstream_name"])
                if tool is None:
                    tool = models.MCPDiscoveredTool(
                        server_id=server.id,
                        upstream_name=item["upstream_name"],
                        namespaced_id=metadata.namespaced_tool_id(server.id, item["upstream_name"]),
                        enabled=True,
                    )
                    self.db.add(tool)
                metadata.apply_discovered(tool, item)
            server.discovery_status = models.MCPDiscoveryStatus.READY
            server.last_health_status = "healthy"
            server.last_health_at = time_utils.utcnow()
            server.last_error = None
            _event(
                self.db,
                models.EventType.SUCCESS,
                "discovery",
                "MCP tools discovered",
                server=server,
                discovered_count=len(discovered),
            )
            self.db.commit()
        except Exception as exc:
            self._record_failure(server, exc, "MCP discovery failed")
            raise MCPServiceError(
                server.last_error,
                auth_required=server.discovery_status == models.MCPDiscoveryStatus.AUTH_REQUIRED,
            ) from exc
        return self.get_server(user_id, server.id)

    def _remote(self, server: models.MCPServer, action: Any) -> Any:
        """Run ``action(client)`` against the server with its credential; report failures in one wording."""
        client = MCPHTTPClient(server.endpoint, access_token=self._access_token(server))
        try:
            return action(client)
        except MCPServiceError as exc:
            translated = static_tokens.translate(server, exc)
            if translated is exc:
                raise
            raise translated from exc

    def _record_failure(self, server: models.MCPServer, exc: Exception, message: str) -> None:
        """Persist a failed contact with the server (status, health, redacted error) and commit."""
        mark_failed(server, exc)
        _event(self.db, models.EventType.WARNING, "discovery", message, server=server)
        self.db.commit()

    def _access_token(self, server: models.MCPServer) -> str | None:
        """Return a usable token, refreshing owner-scoped credentials when needed."""
        credentials = server.credentials
        if credentials is None:
            return None
        now = time_utils.utcnow()
        expires_at = time_utils.normalize_timestamp(credentials.expires_at)
        if expires_at is None or expires_at > now + timedelta(seconds=60):
            return self._decrypt_credential(credentials.encrypted_access_token)
        if not credentials.encrypted_refresh_token or not server.oauth_token_endpoint:
            return self._decrypt_credential(credentials.encrypted_access_token)
        refresh_token = self._decrypt_credential(credentials.encrypted_refresh_token)
        try:
            token_payload = self._exchange_token(
                server.oauth_token_endpoint,
                {
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": server.oauth_client_id,
                },
            )
            access_token = token_payload.get("access_token")
            if not isinstance(access_token, str) or not access_token:
                raise MCPServiceError("OAuth refresh returned no access token.")
            credentials.encrypted_access_token = security.security_service.encrypt(access_token)
            if isinstance(token_payload.get("refresh_token"), str):
                credentials.encrypted_refresh_token = security.security_service.encrypt(token_payload["refresh_token"])
            expires_in = token_payload.get("expires_in")
            credentials.expires_at = now + timedelta(seconds=int(expires_in)) if expires_in else None
            self.db.commit()
            return access_token
        except Exception as exc:
            raise MCPServiceError("OAuth token refresh failed.", auth_required=True) from exc

    @staticmethod
    def _decrypt_credential(value: str) -> str:
        """Decrypt one credential without leaking cryptographic failure details."""
        try:
            return security.security_service.decrypt(value)
        except Exception as exc:
            raise MCPServiceError("MCP credentials are unavailable.", auth_required=True) from exc

    def start_oauth(self, user_id: int, server_id: int, request: Any | None = None) -> str | None:
        """Create owner-bound PKCE state and return a safe authorization URL."""
        server = self.get_server(user_id, server_id)
        if server is None:
            return None
        if static_tokens.credential_status(server) == "static":
            raise MCPServiceError("This MCP server uses a bearer token. Clear the token before connecting with OAuth.")
        if not server.oauth_authorization_endpoint or not server.oauth_token_endpoint or not server.oauth_client_id:
            raise MCPServiceError("OAuth configuration is incomplete for this MCP server.")
        self._validate_oauth_values(
            {
                "oauth_authorization_endpoint": server.oauth_authorization_endpoint,
                "oauth_token_endpoint": server.oauth_token_endpoint,
                "oauth_client_id": server.oauth_client_id,
            }
        )
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        state_value = secrets.token_urlsafe(32)
        cutoff = time_utils.utcnow() - MCP_OAUTH_STATE_TTL
        self.db.query(models.OAuthState).filter(
            models.OAuthState.mcp_server_id.isnot(None), models.OAuthState.created_at < cutoff
        ).delete(synchronize_session=False)
        self.db.query(models.OAuthState).filter(
            models.OAuthState.user_id == user_id, models.OAuthState.mcp_server_id == server.id
        ).delete(synchronize_session=False)
        self.db.add(
            models.OAuthState(
                state=state_value,
                user_id=user_id,
                mcp_server_id=server.id,
                code_verifier=verifier,
            )
        )
        query = {
            "response_type": "code",
            "client_id": server.oauth_client_id,
            "redirect_uri": oauth_redirect_uri(request),
            "state": state_value,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        if server.oauth_scopes:
            query["scope"] = server.oauth_scopes
        self.db.commit()
        return f"{server.oauth_authorization_endpoint}?{urlencode(query)}"

    def complete_oauth(self, state_value: str, code: str, request: Any | None = None) -> tuple[int, int]:
        """Consume PKCE state once and persist encrypted owner-scoped tokens."""
        state = self.db.query(models.OAuthState).filter(models.OAuthState.state == state_value).first()
        if state is None or state.mcp_server_id is None or state.user_id is None or not state.code_verifier:
            raise MCPServiceError("OAuth state is invalid or expired.")
        created_at = time_utils.normalize_timestamp(state.created_at)
        if created_at is None or created_at < time_utils.utcnow() - MCP_OAUTH_STATE_TTL:
            self.db.delete(state)
            self.db.commit()
            raise MCPServiceError("OAuth state is invalid or expired.")
        server = self.get_server(state.user_id, state.mcp_server_id)
        self.db.delete(state)
        if server is None or not server.oauth_token_endpoint or not server.oauth_client_id:
            self.db.commit()
            raise MCPServiceError("MCP server is no longer available.")
        if static_tokens.credential_status(server) == "static":
            self.db.commit()
            raise MCPServiceError("This MCP server uses a bearer token; OAuth was not connected.")
        try:
            token_payload = self._exchange_token(
                server.oauth_token_endpoint,
                {
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": oauth_redirect_uri(request),
                    "client_id": server.oauth_client_id,
                    "code_verifier": state.code_verifier,
                },
            )
            access_token = token_payload.get("access_token")
            if not isinstance(access_token, str) or not access_token:
                raise MCPServiceError("OAuth token exchange returned no access token.")
            refresh_token = token_payload.get("refresh_token")
            credentials = server.credentials or models.MCPCredential(user_id=server.user_id, server_id=server.id)
            credentials.encrypted_access_token = security.security_service.encrypt(access_token)
            credentials.encrypted_refresh_token = (
                security.security_service.encrypt(refresh_token) if isinstance(refresh_token, str) else None
            )
            credentials.token_type = str(token_payload.get("token_type") or "Bearer")
            expires_in = token_payload.get("expires_in")
            credentials.expires_at = time_utils.utcnow() + timedelta(seconds=int(expires_in)) if expires_in else None
            credentials.scopes = str(token_payload.get("scope") or server.oauth_scopes or "")
            if credentials.id is None:
                self.db.add(credentials)
            _event(self.db, models.EventType.SUCCESS, "oauth_connect", "MCP OAuth connected", server=server)
            self.db.commit()
            return state.user_id, server.id
        except Exception as exc:
            _event(self.db, models.EventType.WARNING, "oauth_failure", "MCP OAuth exchange failed", server=server)
            self.db.commit()
            if isinstance(exc, MCPServiceError):
                raise
            raise MCPServiceError("OAuth token exchange failed.") from exc

    @staticmethod
    def _exchange_token(endpoint: str, data: dict[str, Any]) -> dict[str, Any]:
        """Exchange or refresh OAuth tokens without logging provider responses."""
        with (
            httpx.Client(timeout=MCP_HTTP_TIMEOUT, follow_redirects=False) as client,
            client.stream(
                "POST",
                _safe_oauth_endpoint(endpoint),
                data={k: v for k, v in data.items() if v is not None},
            ) as streamed_response,
        ):
            if streamed_response.status_code >= 400:
                raise MCPServiceError("OAuth provider rejected the token request.")
            response = _read_bounded_response(streamed_response)
        try:
            payload = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise MCPServiceError("OAuth provider returned invalid token data.") from exc
        if not isinstance(payload, dict):
            raise MCPServiceError("OAuth provider returned invalid token data.")
        return payload

    def set_tool_enabled(
        self,
        user_id: int,
        server_id: int,
        tool_id: int,
        enabled: bool,
    ) -> models.MCPDiscoveredTool | None:
        """Toggle one discovered tool through owner-scoped server resolution."""
        server = self.get_server(user_id, server_id)
        if server is None:
            return None
        tool = next((item for item in server.discovered_tools if item.id == tool_id), None)
        if tool is None:
            return None
        tool.enabled = enabled
        if enabled:
            metadata.acknowledge_scan(tool)  # switching a flagged tool on accepts its current text
        _event(
            self.db,
            models.EventType.INFO,
            "enable" if enabled else "disable",
            "MCP tool availability changed",
            server=server,
            tool_id=tool.namespaced_id,
        )
        self.db.commit()
        return tool

    def call_tool(self, user_id: int, server_id: int, tool_id: str, arguments: dict[str, Any]) -> Any:
        """Invoke an enabled, discovered tool without allowing metadata to widen policy."""
        server = self.get_server(user_id, server_id)
        if server is None:
            raise MCPServiceError("MCP server not found.")
        tool = next((item for item in server.discovered_tools if item.namespaced_id == tool_id), None)
        if tool is None or not server.enabled or not tool.enabled:
            _event(
                self.db,
                models.EventType.WARNING,
                "policy_denial",
                "MCP tool is not enabled",
                server=server,
                tool_id=tool_id,
            )
            self.db.commit()
            raise MCPServiceError("MCP tool is not enabled.")
        try:
            return self._remote(server, lambda client: client.call_tool(tool.upstream_name, arguments))
        except MCPServiceError as exc:
            if exc.auth_required:
                # Settings must show what the agent hits: the same state a failed discovery records.
                self._record_failure(server, exc, "MCP tool call was not authorized")
            raise
