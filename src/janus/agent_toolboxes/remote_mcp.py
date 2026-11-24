"""Discovered remote MCP tools as chat tools (JAN-343).

Each enabled tool of an enabled MCP server whose last discovery succeeded
becomes one registry entry with the server's ``namespaced_id``
(``mcp.<server id>.<upstream name>``) and one LangChain tool whose arguments
come from the discovered JSON input schema (:func:`args_model_from_schema`).

Policy, fixed in this first slice:

* **Chat only.** ``enabled_when`` admits the entries only in a ``chat`` step,
  and ``tool_policy`` locks every ``mcp.*`` id for unattended runs, so
  triggers, automations, and delegated children never get them. Unattended
  support is follow-up JAN-344.
* **Confirmation by annotation.** A tool whose discovered MCP annotations say
  ``readOnlyHint: true`` is ``operation="read"``, ``confirmation="never"``
  and runs without asking; every other tool is ``confirmation="required"``:
  each call waits for the user's approval of its exact arguments (JAN-254)
  and is refused after a suspected prompt injection (JAN-127). The hint is
  the server's own claim: enabling a server is trusting it that far, and any
  tool can be switched off on its own.
* **Untrusted output.** Every entry is ``reads_untrusted_content``, so its
  output is spotlighted and scanned like a web page.
"""

from __future__ import annotations

import hashlib
import json
import keyword
import re
from collections.abc import Iterable
from functools import lru_cache
from typing import Any, Literal

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, ConfigDict, Field, create_model
from sqlalchemy.orm import Session, selectinload

from janus import models
from janus.services.mcp_metadata import (
    REMOTE_TOOL_ID_PREFIX,
    chat_confirmation,
    is_remote_tool_id,
    offerable,
)
from janus.tool_registry import ToolBuildContext, ToolRegistry, ToolRegistryEntry

REMOTE_DOCS_REF = "tools.md#remote-mcp-tools"
#: Schemas with more properties than this, or nested deeper, use the free-form fallback.
MAX_SCHEMA_PROPERTIES = 64
_TOOL_NAME_UNSAFE = re.compile(r"[^A-Za-z0-9_-]")
#: Names LangChain injects into or reserves on a tool call (``callbacks``, ``run_manager``, a ``config``
#: RunnableConfig, ...): a property with one of them would collide, so the schema falls back to free-form.
_LANGCHAIN_RESERVED_NAMES = frozenset({"callbacks", "callback_manager", "run_manager", "config", "tool_call_id"})
_RESERVED_FIELD_NAMES = frozenset(dir(BaseModel)) | _LANGCHAIN_RESERVED_NAMES


def tool_name(server_id: int, upstream_name: str) -> str:
    """Return a provider-safe tool name (``^[A-Za-z0-9_-]{1,64}$``) for one remote tool."""
    name = f"mcp_{server_id}_{_TOOL_NAME_UNSAFE.sub('_', upstream_name)}"
    if len(name) <= 64:
        return name
    digest = hashlib.sha256(upstream_name.encode()).hexdigest()[:8]
    return f"{name[:55]}_{digest}"


# --- JSON schema -> pydantic ------------------------------------------------------------------------------------


class FreeFormArguments(BaseModel):
    """Fallback arguments when a tool's input schema cannot be converted."""

    model_config = ConfigDict(extra="forbid")

    arguments: dict[str, Any] = Field(
        default_factory=dict,
        description="The tool's arguments as a JSON object, following the tool description.",
    )


class _Unsupported(ValueError):
    """The schema uses something the converter does not map; the caller falls back to free-form."""


_SCALARS: dict[str, Any] = {"string": str, "integer": int, "number": float, "boolean": bool}


def _python_type(schema: Any, depth: int) -> Any:
    """Map one property schema to a Python type; raise :class:`_Unsupported` for anything unclear."""
    if not isinstance(schema, dict):
        raise _Unsupported("property schema is not an object")
    if "$ref" in schema or "oneOf" in schema or "allOf" in schema:
        raise _Unsupported("references and combinators are not converted")
    if "enum" in schema:
        return _enum_type(schema["enum"])
    if "anyOf" in schema:
        return _nullable_type(schema["anyOf"], depth)
    kind = schema.get("type")
    if isinstance(kind, list):
        non_null = [item for item in kind if item != "null"]
        if len(non_null) != 1:
            raise _Unsupported("multi-type properties are not converted")
        return _python_type({**schema, "type": non_null[0]}, depth) | None
    if kind in _SCALARS:
        return _SCALARS[kind]
    if kind == "array":
        items = schema.get("items")
        if items is None or items == {}:
            return list[Any]
        if depth >= 3:
            raise _Unsupported("schema is nested too deeply")
        return list[_python_type(items, depth + 1)]  # type: ignore[misc]
    if kind == "object" or kind is None and "properties" in schema:
        # Nested objects stay plain dicts: the model sees their shape in the field description.
        return dict[str, Any]
    if kind is None:
        return Any
    raise _Unsupported(f"type {kind!r} is not converted")


def _enum_type(values: Any) -> Any:
    if not isinstance(values, list) or not values or not all(isinstance(v, str | int | bool) for v in values):
        raise _Unsupported("enum values must be scalars")
    return Literal[tuple(values)]  # type: ignore[valid-type]


def _nullable_type(options: Any, depth: int) -> Any:
    if not isinstance(options, list):
        raise _Unsupported("anyOf must be a list")
    non_null = [option for option in options if not (isinstance(option, dict) and option.get("type") == "null")]
    if len(non_null) != 1:
        raise _Unsupported("only 'X or null' unions are converted")
    return _python_type(non_null[0], depth) | None


def _field_description(schema: dict[str, Any]) -> str | None:
    description = schema.get("description")
    parts = [description.strip()] if isinstance(description, str) and description.strip() else []
    if schema.get("type") == "object" and isinstance(schema.get("properties"), dict):
        parts.append("JSON object with keys: " + ", ".join(sorted(schema["properties"]))[:300])
    # Server-written text: kept short (the full description was scanned at discovery).
    return " ".join(parts)[:500] or None


def _convert(schema: dict[str, Any], model_name: str) -> type[BaseModel]:
    if schema.get("type", "object") != "object":
        raise _Unsupported("the input schema is not an object")
    properties = schema.get("properties") or {}
    if not isinstance(properties, dict) or len(properties) > MAX_SCHEMA_PROPERTIES:
        raise _Unsupported("properties must be an object of bounded size")
    required = schema.get("required") or []
    if not isinstance(required, list):
        raise _Unsupported("required must be a list")
    additional = schema.get("additionalProperties")
    if not properties and additional is not False:
        raise _Unsupported("an object without declared properties has an unknown shape")
    fields: dict[str, Any] = {}
    for name, property_schema in properties.items():
        if (
            not isinstance(name, str)
            or not name.isidentifier()
            or keyword.iskeyword(name)
            or name.startswith("_")
            or name in _RESERVED_FIELD_NAMES
        ):
            raise _Unsupported(f"property name {name!r} is not usable as a field")
        python_type = _python_type(property_schema, 0)
        description = _field_description(property_schema)
        if name in required:
            fields[name] = (python_type, Field(..., description=description))
        else:
            default = property_schema.get("default") if isinstance(property_schema, dict) else None
            if not isinstance(default, str | int | float | bool | type(None)):
                default = None
            fields[name] = (python_type | None, Field(default=default, description=description))
    # JSON Schema allows extra keys unless the schema says ``additionalProperties: false``.
    extra = "forbid" if additional is False else "allow"
    return create_model(model_name, __config__=ConfigDict(extra=extra), **fields)


@lru_cache(maxsize=512)
def _cached_model(schema_json: str, model_name: str) -> type[BaseModel] | None:
    try:
        return _convert(json.loads(schema_json), model_name)
    except (_Unsupported, TypeError, ValueError):
        return None


def args_model_from_schema(schema: Any, *, model_name: str = "MCPToolArguments") -> type[BaseModel] | None:
    """Return a pydantic model for a discovered JSON input schema, or ``None`` to use :class:`FreeFormArguments`.

    Supports objects with scalar, enum, array, nullable, and nested-object
    (as ``dict``) properties, ``required``, defaults, and descriptions.
    ``$ref``, ``oneOf``/``allOf``, multi-type unions, unusable property names,
    and oversized schemas fall back. Results are cached per schema.
    """
    if not isinstance(schema, dict):
        return None
    try:
        schema_json = json.dumps(schema, sort_keys=True)
    except (TypeError, ValueError):
        return None
    return _cached_model(schema_json, model_name)


# --- registry entries -------------------------------------------------------------------------------------------


def discovered_tools(db: Session, user: models.User) -> list[tuple[models.MCPServer, models.MCPDiscoveredTool]]:
    """Return every discovered tool of the user's servers, whatever their state (the policy API lists them all)."""
    servers = (
        db.query(models.MCPServer)
        .options(selectinload(models.MCPServer.discovered_tools))
        .filter(models.MCPServer.user_id == user.id)
        .order_by(models.MCPServer.id)
        .all()
    )
    return [
        (server, tool)
        for server in servers
        for tool in sorted(server.discovered_tools, key=lambda item: item.upstream_name)
    ]


def offered_tools(db: Session, user: models.User) -> list[tuple[models.MCPServer, models.MCPDiscoveredTool]]:
    """Return the tools chat may offer: enabled server, last discovery succeeded, tool enabled and not flagged."""
    return [
        (server, tool)
        for server, tool in discovered_tools(db, user)
        if server.enabled and server.discovery_status == models.MCPDiscoveryStatus.READY and offerable(tool)
    ]


def chat_allowed_tool_ids(base: frozenset[str], registry: ToolRegistry) -> frozenset[str]:
    """Return a chat allowlist widened by the registry's remote tool ids (chat's per-message registry)."""
    return base | {entry.id for entry in registry.entries() if is_remote_tool_id(entry.id)}


def _builder(server_id: int, namespaced_id: str, name: str, description: str, schema: Any):
    args_model = args_model_from_schema(schema, model_name=f"MCPArgs_{name}")

    def build(context: ToolBuildContext) -> Iterable[BaseTool]:
        # Imported lazily: the MCP service pulls in httpx and the HTTP client.
        from janus.services.mcp_service import MCPService

        def call(arguments: dict[str, Any]) -> Any:
            return MCPService(context.tool_context.db).call_tool(context.user.id, server_id, namespaced_id, arguments)

        if args_model is None:

            def invoke_free_form(arguments: dict[str, Any] | None = None) -> Any:
                return call(dict(arguments or {}))

            func, schema_model = invoke_free_form, FreeFormArguments
        else:
            # Advertise the converted model's JSON schema (bounded, only keywords Janus understands), not the
            # raw upstream dict, and validate on call. A JSON-schema dict rather than the pydantic model:
            # LangChain would fill omitted pydantic fields with defaults or None, so the approval card and
            # the upstream call would disagree; a dict passes exactly the arguments the model supplied.
            def invoke_typed(**arguments: Any) -> Any:
                args_model.model_validate(arguments)  # raises before anything is sent upstream
                return call(dict(arguments))

            func, schema_model = invoke_typed, args_model.model_json_schema()
        yield StructuredTool.from_function(
            func=func, name=name, description=description, args_schema=schema_model, infer_schema=False
        )

    return build


def remote_tool_entries(
    db: Session,
    user: models.User,
    tools: list[tuple[models.MCPServer, models.MCPDiscoveredTool]] | None = None,
) -> list[ToolRegistryEntry]:
    """Return one chat-only registry entry per tool (default: :func:`offered_tools`)."""
    entries: list[ToolRegistryEntry] = []
    used_names: set[str] = set()
    for server, tool in offered_tools(db, user) if tools is None else tools:
        name = tool_name(server.id, tool.upstream_name)
        if name in used_names:
            name = tool_name(server.id, f"{tool.upstream_name}_{tool.id}")
        used_names.add(name)
        confirmation = chat_confirmation(tool)
        upstream = " ".join((tool.description or "").split())
        description = f"[{server.provider_name} via MCP] {upstream or f'Remote MCP tool {tool.upstream_name}'}"[:2000]
        approval_reason = f"Remote MCP tool {server.provider_name}: {tool.upstream_name}." + (
            f' The server describes it as (untrusted text): "{upstream[:300]}"' if upstream else ""
        )
        owner_id = server.user_id
        entries.append(
            ToolRegistryEntry(
                id=tool.namespaced_id,
                display_name=f"{server.provider_name}: {tool.upstream_name}",
                description=description,
                required_capabilities=("mcp",),
                operation="read" if confirmation == "never" else "mixed",
                risk_level="medium" if confirmation == "never" else "high",
                confirmation=confirmation,
                approval_reason=approval_reason,
                docs_ref=REMOTE_DOCS_REF,
                builder=_builder(server.id, tool.namespaced_id, name, description, tool.input_schema),
                # Upstream output is third-party content (JAN-127).
                reads_untrusted_content=True,
                # Chat, and unattended runs once the owner chose "Allow everything" (JAN-359): only that mode
                # builds a registry with these entries for an unattended run, and tool_policy unlocks them.
                enabled_when=lambda context, _owner=owner_id: (
                    (context.task_mode == "chat" or context.unattended_allow_all) and context.user.id == _owner
                ),
            )
        )
    return entries


__all__ = [
    "MAX_SCHEMA_PROPERTIES",
    "REMOTE_DOCS_REF",
    "REMOTE_TOOL_ID_PREFIX",
    "FreeFormArguments",
    "args_model_from_schema",
    "chat_allowed_tool_ids",
    "discovered_tools",
    "is_remote_tool_id",
    "offered_tools",
    "remote_tool_entries",
    "tool_name",
]
