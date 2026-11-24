"""Registry and metadata for Janus-internal toolboxes.

The first registry iteration keeps the existing toolbox builders intact.  Each
entry describes one compatibility-preserving toolbox boundary and delegates
construction to the existing builder.  Later iterations can register
individual tools without changing the selection contract.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, Literal

from langchain_core.tools import BaseTool

from janus import models
from janus.agent_toolboxes.shared import ToolContext

ToolOperation = Literal["read", "write", "delete", "admin", "mixed"]
ToolRisk = Literal["low", "medium", "high"]


@dataclass(frozen=True)
class ToolSelectionRequest:
    """Task-scoped tool request and the non-model policy inputs."""

    task_id: str
    requested_tool_ids: tuple[str, ...]
    allowed_tool_ids: frozenset[str]
    granted_capabilities: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        """Reject ambiguous task requests before selection begins."""
        if not self.task_id.strip():
            raise ValueError("tool selection task_id must not be blank")
        if any(not tool_id.strip() for tool_id in self.requested_tool_ids):
            raise ValueError("requested tool IDs must not be blank")
        if len(self.requested_tool_ids) != len(set(self.requested_tool_ids)):
            raise ValueError("requested tool IDs must be unique")


@dataclass(frozen=True)
class ToolSelectionDenial:
    """Auditable reason why one requested tool was not selected."""

    tool_id: str
    reason: str


@dataclass(frozen=True)
class ToolSelection:
    """Effective tool selection after request and policy intersection."""

    entries: tuple[ToolRegistryEntry, ...]
    denials: tuple[ToolSelectionDenial, ...] = ()


@dataclass(frozen=True)
class ToolBuildContext:
    """Context supplied to registry selection and toolbox builders."""

    tool_context: ToolContext
    skill_catalog: Any = None
    #: Plan-runtime identity of the step being built for. ``None`` only when a
    #: registry is built outside a plan step (tests, registry introspection);
    #: toolboxes that need one (``delegation``, ``web_search``) are disabled then.
    task_id: str | None = None
    step_id: str | None = None
    task_mode: str | None = None
    #: The durable ``RuntimeTrigger.trigger_id`` of the run this step belongs
    #: to, for trigger-driven steps only (``None`` in chat and delegated
    #: children). The ``triggers`` toolbox records it as the parent of any
    #: follow-up the run schedules, which is what bounds chained
    #: self-scheduling (JAN-274).
    trigger_id: str | None = None
    #: Registry entry IDs that passed authorization *and* the confirmation gate
    #: for this step. Set by ``RuntimeAdapter.prepare_step`` just before the
    #: builders run, so a builder can clamp anything it spawns (a delegated
    #: child task) to what its parent was actually granted. Empty means
    #: "nothing is known to be authorized", which clamps to nothing.
    authorized_tool_ids: frozenset[str] = frozenset()
    #: The step's model choice (``llm_service.ModelChoice`` or ``None`` for the
    #: default), so a delegated child can run on its parent's model (JAN-233).
    model_choice: Any = None
    #: Set for an unattended step whose owner chose *Unattended: Allow everything* (JAN-359); only then may
    #: the chat-only image and remote MCP entries be built outside chat.
    unattended_allow_all: bool = False

    @property
    def user(self) -> models.User:
        """Return the authenticated user used by current builders."""
        return self.tool_context.user


ToolBuilder = Callable[[ToolBuildContext], Iterable[BaseTool]]
ToolEnabled = Callable[[ToolBuildContext], bool]


@dataclass(frozen=True)
class ToolRegistryEntry:
    """Metadata and builder for one registry toolbox boundary."""

    id: str
    display_name: str
    description: str
    required_capabilities: tuple[str, ...]
    operation: ToolOperation
    risk_level: ToolRisk
    confirmation: Literal["never", "recommended", "required"]
    docs_ref: str | None
    builder: ToolBuilder
    enabled_when: ToolEnabled = lambda _context: True
    #: The entry's tools return text other people wrote (mail, web, tickets,
    #: chunks, MCP output). The runtime adapter spotlights and scans their
    #: output for prompt injections and records the read (JAN-127).
    reads_untrusted_content: bool = False
    #: Shown as the approval card's "why" instead of ``description`` (JAN-343): remote MCP tools put the
    #: server-written description there only as quoted, untrusted text.
    approval_reason: str | None = None


class ToolRegistry:
    """Ordered registry for discoverable and buildable Janus toolboxes."""

    def __init__(self, entries: Iterable[ToolRegistryEntry] = ()) -> None:
        """Create a registry and validate its initial entries."""
        self._entries: list[ToolRegistryEntry] = []
        for entry in entries:
            self.register(entry)

    def register(self, entry: ToolRegistryEntry) -> None:
        """Register an entry, rejecting duplicate or invalid IDs."""
        if not entry.id or entry.id.strip() != entry.id:
            raise ValueError("tool registry IDs must be non-empty and whitespace-free")
        if any(existing.id == entry.id for existing in self._entries):
            raise ValueError(f"duplicate tool registry ID: {entry.id}")
        if not entry.display_name.strip():
            raise ValueError(f"tool registry entry has no display name: {entry.id}")
        if not entry.description.strip():
            raise ValueError(f"tool registry entry has no description: {entry.id}")
        self._entries.append(entry)

    def entries(self) -> tuple[ToolRegistryEntry, ...]:
        """Return entries in deterministic registration order."""
        return tuple(self._entries)

    def select(self, context: ToolBuildContext) -> tuple[ToolRegistryEntry, ...]:
        """Return the entries enabled for the current context."""
        return tuple(entry for entry in self._entries if entry.enabled_when(context))

    def authorize(self, context: ToolBuildContext, request: ToolSelectionRequest) -> ToolSelection:
        """Return only requested entries allowed by context and task policy.

        The model can provide requested IDs, but it cannot provide the
        allowlist or capabilities. Unknown, disabled, and under-capable tools
        are denied with a stable reason so callers can audit or explain the
        effective task toolset.
        """
        entries_by_id = {entry.id: entry for entry in self._entries}
        selected: list[ToolRegistryEntry] = []
        denials: list[ToolSelectionDenial] = []
        for tool_id in request.requested_tool_ids:
            entry = entries_by_id.get(tool_id)
            if entry is None:
                denials.append(ToolSelectionDenial(tool_id, "unknown_tool"))
                continue
            if tool_id not in request.allowed_tool_ids:
                denials.append(ToolSelectionDenial(tool_id, "not_allowlisted"))
                continue
            if not entry.enabled_when(context):
                denials.append(ToolSelectionDenial(tool_id, "disabled_by_context"))
                continue
            missing = set(entry.required_capabilities) - request.granted_capabilities
            if missing:
                denials.append(ToolSelectionDenial(tool_id, "missing_capability"))
                continue
            selected.append(entry)
        return ToolSelection(entries=tuple(selected), denials=tuple(denials))

    def describe(self) -> list[dict[str, Any]]:
        """Return UI- and documentation-ready metadata for all entries."""
        return [
            {
                "id": entry.id,
                "display_name": entry.display_name,
                "description": entry.description,
                "required_capabilities": list(entry.required_capabilities),
                "operation": entry.operation,
                "risk_level": entry.risk_level,
                "confirmation": entry.confirmation,
                "docs_ref": entry.docs_ref,
            }
            for entry in self._entries
        ]

    def build(self, context: ToolBuildContext) -> list[BaseTool]:
        """Build tools from all entries enabled for the current context."""
        tools: list[BaseTool] = []
        for entry in self.select(context):
            tools.extend(entry.builder(context))
        return tools

    def build_authorized(
        self, context: ToolBuildContext, request: ToolSelectionRequest
    ) -> tuple[ToolSelection, list[BaseTool]]:
        """Authorize and build a task-scoped toolset in registry order."""
        selection = self.authorize(context, request)
        tools: list[BaseTool] = []
        for entry in selection.entries:
            tools.extend(entry.builder(context))
        return selection, tools
