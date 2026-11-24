"""Authenticated management API for the built-in and remote MCP registry."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from janus import database, models, security, tool_policy
from janus.agent_tools import DEFAULT_TOOL_REGISTRY, build_policy_registry
from janus.capability_profile import user_connected_capabilities
from janus.schemas import mcp as mcp_schemas
from janus.services import injection_classifier
from janus.services.mcp_metadata import chat_policy
from janus.services.mcp_service import MCP_OAUTH_CALLBACK_PATH, MCPService, MCPServiceError, oauth_redirect_uri
from janus.services.mcp_static_token import credential_status

router = APIRouter(prefix="/api/tools", tags=["tools"])


def _server_response(server: models.MCPServer) -> mcp_schemas.MCPServerResponse:
    """Serialize remote metadata without credentials or provider responses."""
    status_value = credential_status(server)
    return mcp_schemas.MCPServerResponse(
        id=server.id,
        provider_name=server.provider_name,
        endpoint=server.endpoint,
        transport=server.transport.value,
        enabled=server.enabled,
        discovery_status=server.discovery_status.value,
        last_health_status=server.last_health_status,
        last_health_at=server.last_health_at,
        last_error=server.last_error,
        # A rejected static token needs a new token, not an OAuth sign-in (JAN-342).
        oauth_required=server.discovery_status == models.MCPDiscoveryStatus.AUTH_REQUIRED and status_value != "static",
        oauth_configured=bool(
            server.oauth_authorization_endpoint and server.oauth_token_endpoint and server.oauth_client_id
        ),
        credential_configured=server.credentials is not None,
        credential_status=status_value,
        # Each tool with how chat treats it (JAN-343).
        tools=[
            mcp_schemas.MCPToolResponse.model_validate(tool).model_copy(update=chat_policy(tool))
            for tool in server.discovered_tools
        ],
        created_at=server.created_at,
        updated_at=server.updated_at,
    )


def _get_server_or_404(db: Session, user: models.User, server_id: int) -> models.MCPServer:
    """Resolve an owner-scoped server or return the same non-disclosing 404."""
    server = MCPService(db).get_server(user.id, server_id)
    if server is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="MCP server not found")
    return server


@router.get("/registry", response_model=mcp_schemas.MCPRegistryResponse)
def get_registry(
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Return built-in registry metadata (with the user's unattended switch) and the user's remote servers."""
    policies = {
        row["tool_id"]: row for row in tool_policy.describe_policies(db, user.id, DEFAULT_TOOL_REGISTRY.entries())
    }
    connected = user_connected_capabilities(db, user)
    builtins = [
        {
            **entry,
            **{key: value for key, value in policies[entry["id"]].items() if key not in {"tool_id", "overridden"}},
            "connected": set(entry["required_capabilities"]) <= connected,
        }
        for entry in DEFAULT_TOOL_REGISTRY.describe()
    ]
    return {
        "builtins": builtins,
        "remote_servers": [_server_response(server) for server in MCPService(db).list_servers(user.id)],
    }


@router.get("/policies", response_model=list[mcp_schemas.ToolPolicyResponse])
def list_tool_policies(
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Return each capability's switches: the built-ins plus the user's remote MCP tools chat offers (JAN-343)."""
    return tool_policy.describe_policies(db, user.id, build_policy_registry(db, user).entries())


@router.put("/policies/{tool_id}", response_model=mcp_schemas.ToolPolicyResponse)
def update_tool_policy(
    tool_id: str,
    body: mcp_schemas.ToolPolicyUpdate,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Change one capability's unattended and/or In chat switch, or reset either to its default.

    Only the fields present in the body change. ``chat_mode`` exists only for
    ``confirmation="required"`` capabilities (JAN-306); setting it elsewhere is ``422``.
    """
    entries = build_policy_registry(db, user).entries()
    entry = next((candidate for candidate in entries if candidate.id == tool_id), None)
    if entry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown tool capability")
    changes = {field: getattr(body, field) for field in body.model_fields_set}
    try:
        tool_policy.update_policy(db, user.id, tool_id, entry=entry, **changes)
    except (tool_policy.LockedToolError, tool_policy.ChatSwitchError) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return next(row for row in tool_policy.describe_policies(db, user.id, entries) if row["tool_id"] == tool_id)


@router.get("/policy-modes", response_model=mcp_schemas.ToolPolicyModes)
def get_tool_policy_modes(
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Return the user's blanket Unattended and In chat modes (JAN-359)."""
    return tool_policy.get_modes(db, user.id)


@router.put("/policy-modes", response_model=mcp_schemas.ToolPolicyModes)
def update_tool_policy_modes(
    body: mcp_schemas.ToolPolicyModesUpdate,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Switch unattended runs and/or chat between per-tool switches and *Allow all* (JAN-359)."""
    try:
        return tool_policy.set_modes(db, user.id, unattended_mode=body.unattended_mode, chat_mode=body.chat_mode)
    except tool_policy.ModeError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@router.get("/injection-classifier", response_model=mcp_schemas.InjectionClassifierStatus)
def get_injection_classifier(
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Whether the optional prompt-injection classifier runs, with which model, and this month's usage (JAN-289)."""
    return injection_classifier.status(db, user.id)


@router.put("/injection-classifier", response_model=mcp_schemas.InjectionClassifierStatus)
def update_injection_classifier(
    body: mcp_schemas.InjectionClassifierUpdate,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Turns the optional prompt-injection classifier on or off and sets its monthly token budget."""
    return injection_classifier.update_settings(
        db, user.id, enabled=body.enabled, monthly_token_budget=body.monthly_token_budget
    )


@router.get("/servers", response_model=list[mcp_schemas.MCPServerResponse])
def list_servers(
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """List the current user's managed remote MCP servers."""
    return [_server_response(server) for server in MCPService(db).list_servers(user.id)]


@router.post("/servers", response_model=mcp_schemas.MCPServerResponse, status_code=status.HTTP_201_CREATED)
def create_server(
    body: mcp_schemas.MCPServerCreate,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Register one remote HTTP MCP server without contacting it yet."""
    try:
        server = MCPService(db).create_server(user, body.model_dump())
    except MCPServiceError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return _server_response(server)


@router.get("/servers/{server_id}", response_model=mcp_schemas.MCPServerResponse)
def get_server(
    server_id: int,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Return one owner-scoped remote server."""
    return _server_response(_get_server_or_404(db, user, server_id))


@router.patch("/servers/{server_id}", response_model=mcp_schemas.MCPServerResponse)
def update_server(
    server_id: int,
    body: mcp_schemas.MCPServerUpdate,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Update remote settings; a bearer token (JAN-342) is accepted write-only and never echoed."""
    try:
        server = MCPService(db).update_server(user.id, server_id, body.model_dump(exclude_unset=True))
    except MCPServiceError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    if server is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="MCP server not found")
    return _server_response(server)


@router.delete("/servers/{server_id}", status_code=status.HTTP_204_NO_CONTENT)
def disconnect_server(
    server_id: int,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Disconnect and remove one owner-scoped remote server and its credentials."""
    if not MCPService(db).disconnect(user.id, server_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="MCP server not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/servers/{server_id}/discover", response_model=mcp_schemas.MCPDiscoveryResponse)
@router.post("/servers/{server_id}/refresh", response_model=mcp_schemas.MCPDiscoveryResponse)
def discover_server(
    server_id: int,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Run bounded HTTP MCP discovery and persist the safe metadata snapshot."""
    try:
        server = MCPService(db).discover(user.id, server_id)
    except MCPServiceError as exc:
        if exc.auth_required:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    if server is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="MCP server not found")
    return {"server": _server_response(server), "discovered_count": len(server.discovered_tools)}


@router.patch("/servers/{server_id}/tools/{tool_id}", response_model=mcp_schemas.MCPToolResponse)
def update_tool(
    server_id: int,
    tool_id: int,
    body: mcp_schemas.MCPToolUpdate,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Enable or disable one discovered tool without changing server policy."""
    tool = MCPService(db).set_tool_enabled(user.id, server_id, tool_id, body.enabled)
    if tool is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="MCP tool not found")
    return tool


@router.post("/servers/{server_id}/oauth/start", response_model=mcp_schemas.MCPOAuthStartResponse)
def start_oauth(
    server_id: int,
    request: Request,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Start owner-scoped OAuth PKCE setup and return only the browser URL."""
    try:
        authorization_url = MCPService(db).start_oauth(user.id, server_id, request)
    except MCPServiceError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    if authorization_url is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="MCP server not found")
    return {"authorization_url": authorization_url}


@router.get("/oauth/callback")
def oauth_callback(
    request: Request,
    state: str | None = None,
    code: str | None = None,
    error: str | None = None,
    db: Session = Depends(database.get_db),
):
    """Consume an OAuth callback and redirect without exposing credential data."""
    redirect_base = oauth_redirect_uri(request).removesuffix(MCP_OAUTH_CALLBACK_PATH)
    if error or not state or not code:
        return RedirectResponse(f"{redirect_base}/settings?tab=tools&oauth_error=invalid_callback")
    try:
        MCPService(db).complete_oauth(state, code, request)
    except MCPServiceError:
        return RedirectResponse(f"{redirect_base}/settings?tab=tools&oauth_error=exchange_failed")
    return RedirectResponse(f"{redirect_base}/settings?tab=tools&oauth_connected=1")
