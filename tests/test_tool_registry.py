"""Tests for the compatibility-first Janus tool registry."""

import pytest

from janus import agent_tools, models
from janus.agent_toolboxes.shared import ToolContext
from janus.tool_registry import ToolBuildContext, ToolRegistry, ToolRegistryEntry, ToolSelectionRequest


def _context(*, mail_connected: bool = False) -> ToolBuildContext:
    """Build a registry context without requiring a database session."""
    user = models.User(username="registry-test", hashed_password="not-a-password", is_admin=False)
    connected = frozenset({"mail"}) if mail_connected else frozenset()
    return ToolBuildContext(ToolContext(db=None, user=user, connected_capabilities=connected))  # type: ignore[arg-type]


def _mail_connected(context: ToolBuildContext) -> bool:
    """Gate an entry on the real mail connection snapshot, like the default registry does."""
    return "mail" in context.tool_context.connected_capabilities


def test_registry_rejects_duplicate_ids():
    """Registry IDs must be unique because they are durable references."""
    entry = ToolRegistryEntry(
        id="example",
        display_name="Example",
        description="Example tool group.",
        required_capabilities=(),
        operation="read",
        risk_level="low",
        confirmation="never",
        docs_ref=None,
        builder=lambda _context: (),
    )
    registry = ToolRegistry([entry])

    with pytest.raises(ValueError, match="duplicate tool registry ID"):
        registry.register(entry)


def test_registry_selection_is_ordered_and_metadata_is_serializable():
    """Selection and metadata preserve registration order and declared fields."""
    registry = ToolRegistry(
        [
            ToolRegistryEntry(
                id="always",
                display_name="Always",
                description="Always enabled.",
                required_capabilities=(),
                operation="read",
                risk_level="low",
                confirmation="never",
                docs_ref="tools/always",
                builder=lambda _context: (),
            ),
            ToolRegistryEntry(
                id="mail",
                display_name="Mail",
                description="Mail access.",
                required_capabilities=("mail",),
                operation="write",
                risk_level="high",
                confirmation="required",
                docs_ref="tools/mail",
                builder=lambda _context: (),
                enabled_when=_mail_connected,
            ),
        ]
    )

    assert [entry.id for entry in registry.select(_context(mail_connected=True))] == ["always", "mail"]
    assert [entry.id for entry in registry.select(_context(mail_connected=False))] == ["always"]
    assert registry.describe()[1]["required_capabilities"] == ["mail"]
    assert registry.describe()[1]["operation"] == "write"
    assert registry.describe()[1]["confirmation"] == "required"


def test_default_registry_marks_mixed_toolboxes_as_mixed_operations():
    """Group metadata must not misrepresent mixed read/write toolboxes."""
    metadata = {entry["id"]: entry for entry in agent_tools.DEFAULT_TOOL_REGISTRY.describe()}

    for tool_id in ("dashboard", "github", "linear", "memory", "topics", "triggers"):
        assert metadata[tool_id]["operation"] == "mixed"


def test_default_registry_splits_mail_planner_and_rules_by_operation():
    """Read toolboxes stay read-only; their writers live in separately-gated toolboxes."""
    metadata = {entry["id"]: entry for entry in agent_tools.DEFAULT_TOOL_REGISTRY.describe()}

    for tool_id in ("mail", "planner.local", "rules"):
        assert metadata[tool_id]["operation"] == "read"
        assert metadata[tool_id]["confirmation"] == "never"
    for tool_id in ("mail.label", "mail.processing"):
        assert metadata[tool_id]["operation"] == "write"
        assert metadata[tool_id]["confirmation"] == "recommended"
    for tool_id in ("mail.send", "planner.local.write", "rules.write"):
        assert metadata[tool_id]["operation"] == "write"
        assert metadata[tool_id]["confirmation"] == "required"


def test_default_registry_preserves_existing_toolbox_order():
    """The default registry keeps its toolbox ordering stable."""
    assert [entry.id for entry in agent_tools.DEFAULT_TOOL_REGISTRY.entries()] == [
        "core",
        "events",
        "trigger-history",
        "mail",
        "mail.label",
        "mail.send",
        "mail.processing",
        "whatsapp",
        "whatsapp.send",
        "whatsapp.notify_me",
        "calendar",
        "microsoft.mail",
        "microsoft.calendar",
        "microsoft.teams",
        "microsoft.mail.send",
        "microsoft.teams.send",
        "azure.resources",
        "jira",
        "planner.local",
        "planner.local.write",
        "dashboard",
        "github",
        "linear",
        "web_search",
        "web_fetch",
        "memory",
        "topics",
        "knowledge",
        "wiki",
        "wiki.propose",
        "triggers",
        "triggers.propose",
        "rules",
        "rules.write",
        "delegation",
        "skills",
        "docs",
        "ui_actions",
        "ui_blocks",
        "ui_introspection",
        "images",
        "self_evolution",
        "conversations.start",
        "character",
        "character.propose",
        "shell",
    ]


def test_authorize_intersects_request_allowlist_capabilities_and_context():
    """The model request cannot widen non-model policy inputs."""
    registry = ToolRegistry(
        [
            ToolRegistryEntry(
                id="mail",
                display_name="Mail",
                description="Mail access.",
                required_capabilities=("mail",),
                operation="mixed",
                risk_level="high",
                confirmation="required",
                docs_ref=None,
                builder=lambda _context: (),
                enabled_when=_mail_connected,
            ),
            ToolRegistryEntry(
                id="calendar",
                display_name="Calendar",
                description="Calendar access.",
                required_capabilities=("calendar",),
                operation="read",
                risk_level="low",
                confirmation="never",
                docs_ref=None,
                builder=lambda _context: (),
            ),
        ]
    )

    selection = registry.authorize(
        _context(mail_connected=True),
        ToolSelectionRequest(
            task_id="task-1",
            requested_tool_ids=("calendar", "mail", "unknown"),
            allowed_tool_ids=frozenset({"mail", "calendar"}),
            granted_capabilities=frozenset({"mail"}),
        ),
    )

    assert [entry.id for entry in selection.entries] == ["mail"]
    assert [(denial.tool_id, denial.reason) for denial in selection.denials] == [
        ("calendar", "missing_capability"),
        ("unknown", "unknown_tool"),
    ]


def test_authorize_denies_a_tool_disabled_by_context():
    """A task allowlist cannot bypass an entry's enabled_when gate."""
    registry = ToolRegistry(
        [
            ToolRegistryEntry(
                id="mail",
                display_name="Mail",
                description="Mail access.",
                required_capabilities=("mail",),
                operation="mixed",
                risk_level="high",
                confirmation="required",
                docs_ref=None,
                builder=lambda _context: (),
                enabled_when=_mail_connected,
            )
        ]
    )

    selection = registry.authorize(
        _context(mail_connected=False),
        ToolSelectionRequest(
            task_id="task-1",
            requested_tool_ids=("mail",),
            allowed_tool_ids=frozenset({"mail"}),
            granted_capabilities=frozenset({"mail"}),
        ),
    )

    assert selection.entries == ()
    assert selection.denials[0].reason == "disabled_by_context"


def test_build_authorized_builds_only_the_effective_selection():
    """Denied entries never reach a toolbox builder."""
    built: list[str] = []
    registry = ToolRegistry(
        [
            ToolRegistryEntry(
                id="allowed",
                display_name="Allowed",
                description="Allowed tool.",
                required_capabilities=(),
                operation="read",
                risk_level="low",
                confirmation="never",
                docs_ref=None,
                builder=lambda _context: built.append("allowed") or (),
            ),
            ToolRegistryEntry(
                id="denied",
                display_name="Denied",
                description="Denied tool.",
                required_capabilities=(),
                operation="read",
                risk_level="low",
                confirmation="never",
                docs_ref=None,
                builder=lambda _context: built.append("denied") or (),
            ),
        ]
    )

    selection, tools = registry.build_authorized(
        _context(),
        ToolSelectionRequest(
            task_id="task-1",
            requested_tool_ids=("allowed", "denied"),
            allowed_tool_ids=frozenset({"allowed"}),
        ),
    )

    assert [entry.id for entry in selection.entries] == ["allowed"]
    assert tools == []
    assert built == ["allowed"]
