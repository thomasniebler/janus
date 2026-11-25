"""Advisory metadata of discovered remote MCP tools and what chat makes of it (JAN-343).

A server's tool names, descriptions, input schemas, and annotations are
untrusted: the server can write anything there, including instructions for
the model ("MCP tool poisoning"). This module keeps that metadata bounded and
scanned before chat sees it:

* descriptions are cut at :data:`MAX_DESCRIPTION_CHARS`, input schemas over
  :data:`MAX_SCHEMA_CHARS` (as JSON) are replaced by ``{}`` (the tool then
  takes a free-form ``arguments`` object);
* the description plus every property description is scanned with the
  prompt-injection rules at discovery; a suspected tool is switched off and
  stays off for chat until the owner switches it on again on Settings, which
  records that they accepted that exact text (:func:`apply_discovered`,
  :func:`acknowledge_scan`);
* only the boolean hint annotations are kept, and an exact
  ``readOnlyHint: true`` is what lets chat run a tool without asking
  (:func:`chat_confirmation`).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from typing import Any, Literal

from sqlalchemy.orm import Session

from janus import models, prompt_injection

#: Prefix of every remote MCP tool id; a tool is ``mcp.<server id>.<upstream name>``.
REMOTE_TOOL_ID_PREFIX = "mcp."
#: Longest tool description kept from a server.
MAX_DESCRIPTION_CHARS = 2000
#: Largest input schema kept from a server, measured as compact JSON.
MAX_SCHEMA_CHARS = 20_000
#: MCP tool annotation hints Janus keeps; anything else the server sends is dropped.
_ANNOTATION_HINTS = ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint")


def namespaced_tool_id(server_id: int, upstream_name: str) -> str:
    """Return the registry id of one discovered tool."""
    return f"{REMOTE_TOOL_ID_PREFIX}{server_id}.{upstream_name}"


#: Prefix of every remote A2A agent's tool id, ``a2a.<agent id>`` (JAN-404; defined here so the policy
#: modules can test for it without importing the A2A service).
REMOTE_AGENT_TOOL_ID_PREFIX = "a2a."


def is_remote_tool_id(tool_id: str) -> bool:
    """Return whether a registry id names a remote tool: an MCP tool or a remote A2A agent (JAN-404).

    Both reach a third party with model-written arguments, so the policy treats them alike: locked for
    unattended runs unless *Allow everything*, confirmation-escalated, and widened into chat's allowlist.
    """
    return tool_id.startswith((REMOTE_TOOL_ID_PREFIX, REMOTE_AGENT_TOOL_ID_PREFIX))


def normalize_annotations(value: Any) -> dict[str, bool] | None:
    """Keep only the boolean MCP hint annotations; ``None`` when there are none."""
    if not isinstance(value, dict):
        return None
    kept = {key: value[key] for key in _ANNOTATION_HINTS if isinstance(value.get(key), bool)}
    return kept or None


def bound_schema(schema: Any) -> dict[str, Any]:
    """Return the schema when it is an object of at most :data:`MAX_SCHEMA_CHARS` as JSON, else ``{}``."""
    if not isinstance(schema, dict):
        return {}
    try:
        size = len(json.dumps(schema, separators=(",", ":")))
    except (TypeError, ValueError):
        return {}
    return schema if size <= MAX_SCHEMA_CHARS else {}


def read_only_hint(tool: models.MCPDiscoveredTool) -> bool:
    """Return whether the server marked the tool ``readOnlyHint: true`` (only an exact ``True`` counts)."""
    annotations = tool.annotations if isinstance(tool.annotations, dict) else {}
    return annotations.get("readOnlyHint") is True


def chat_confirmation(tool: models.MCPDiscoveredTool) -> Literal["never", "required"]:
    """Return chat's confirmation level: ``never`` for a read-only-hinted tool, else ``required``."""
    return "never" if read_only_hint(tool) else "required"


def chat_policy(tool: models.MCPDiscoveredTool) -> dict[str, Any]:
    """Return how chat treats the tool, for the API: ``read_only``, ``confirmation``, and the scan state."""
    scan = tool.description_scan if isinstance(tool.description_scan, dict) else None
    return {
        "read_only": read_only_hint(tool),
        "confirmation": chat_confirmation(tool),
        "injection_suspected": bool(scan and scan.get("rules")),
        "injection_acknowledged": bool(scan and scan.get("acknowledged")),
    }


# --- description scanning ---------------------------------------------------------------------------------------


def _property_descriptions(schema: Any, out: list[str], depth: int = 0) -> None:
    if not isinstance(schema, dict) or depth > 6:
        return
    for key in ("description", "title"):
        if isinstance(schema.get(key), str):
            out.append(schema[key])
    for child_key in ("properties", "$defs", "definitions"):
        children = schema.get(child_key)
        if isinstance(children, dict):
            for child in children.values():
                _property_descriptions(child, out, depth + 1)
    for child_key in ("items", "additionalProperties"):
        _property_descriptions(schema.get(child_key), out, depth + 1)
    for child_key in ("anyOf", "oneOf", "allOf"):
        if isinstance(schema.get(child_key), list):
            for child in schema[child_key]:
                _property_descriptions(child, out, depth + 1)


def scanned_text(description: str, schema: Any) -> str:
    """Return everything a server wrote that reaches the model: the description and every schema description."""
    parts = [description]
    _property_descriptions(schema, parts)
    return "\n".join(part for part in parts if part)


def apply_discovered(tool: models.MCPDiscoveredTool, item: dict[str, Any]) -> None:
    """Copy one discovered item onto its row, scan its text, and switch a newly suspicious tool off.

    A tool whose text matches a prompt-injection rule is disabled and marked
    (``description_scan``: rules, text hash, ``acknowledged=False``). If the
    owner already switched on that exact text before (same hash, acknowledged),
    it stays as they left it.
    """
    tool.description = item["description"]
    tool.input_schema = item["input_schema"]
    tool.annotations = item.get("annotations")
    text = scanned_text(item["description"], item["input_schema"])
    scan = prompt_injection.scan_text(text)
    if not scan.suspected:
        tool.description_scan = None
        return
    digest = hashlib.sha256(text.encode()).hexdigest()
    previous = tool.description_scan if isinstance(tool.description_scan, dict) else {}
    if previous.get("hash") == digest and previous.get("acknowledged"):
        return
    tool.description_scan = {"rules": list(scan.rules), "hash": digest, "acknowledged": False}
    tool.enabled = False


def acknowledge_scan(tool: models.MCPDiscoveredTool) -> None:
    """Record that the owner switched a flagged tool on: chat may offer it until its text changes."""
    if isinstance(tool.description_scan, dict) and tool.description_scan.get("rules"):
        tool.description_scan = {**tool.description_scan, "acknowledged": True}


def offerable(tool: models.MCPDiscoveredTool) -> bool:
    """Return whether chat may offer the tool: enabled, and not flagged unless the owner accepted it."""
    scan = tool.description_scan if isinstance(tool.description_scan, dict) else None
    return bool(tool.enabled) and not (scan and scan.get("rules") and not scan.get("acknowledged"))


# --- tool policies ----------------------------------------------------------------------------------------------


def purge_tool_policies(
    db: Session, user_id: int, *, tool_ids: Iterable[str] = (), server_id: int | None = None
) -> None:
    """Delete the owner's tool-policy rows of vanished remote tools (by id, or every tool of a server).

    Server ids can be reused (SQLite), so a stale "Off in chat" must not outlive its tool.
    """
    query = db.query(models.ToolPolicy).filter(models.ToolPolicy.user_id == user_id)
    if server_id is not None:
        query = query.filter(
            models.ToolPolicy.tool_id.startswith(f"{REMOTE_TOOL_ID_PREFIX}{server_id}.", autoescape=True)
        )
    else:
        ids = [tool_id for tool_id in tool_ids if is_remote_tool_id(tool_id)]
        if not ids:
            return
        query = query.filter(models.ToolPolicy.tool_id.in_(ids))
    query.delete(synchronize_session=False)


def remove_vanished(db: Session, server: models.MCPServer, incoming_names: set[str]) -> None:
    """Delete the server's tools a rediscovery no longer lists, and their tool-policy rows."""
    vanished = [tool for tool in server.discovered_tools if tool.upstream_name not in incoming_names]
    purge_tool_policies(db, server.user_id, tool_ids=[tool.namespaced_id for tool in vanished])
    for tool in vanished:
        db.delete(tool)
