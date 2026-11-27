"""LangChain tool wrappers for Janus-internal MCP-style capabilities."""

from __future__ import annotations

from collections.abc import Iterable

from langchain_core.tools import BaseTool
from sqlalchemy.orm import Session

from janus import models
from janus.agent_toolboxes import azure as azure_toolbox
from janus.agent_toolboxes import microsoft as microsoft_toolbox
from janus.agent_toolboxes.automation_proposals import build_automation_proposal_tools
from janus.agent_toolboxes.calendar import build_calendar_tools
from janus.agent_toolboxes.character import CHARACTER_ENTRIES
from janus.agent_toolboxes.coding_agents import CODING_AGENTS_ENTRY
from janus.agent_toolboxes.common import build_common_tools
from janus.agent_toolboxes.conversations import CONVERSATIONS_START_ENTRY
from janus.agent_toolboxes.dashboard import build_dashboard_tools
from janus.agent_toolboxes.delegation import build_delegation_tools
from janus.agent_toolboxes.docs import build_docs_tools
from janus.agent_toolboxes.events import build_event_tools, build_trigger_history_tools
from janus.agent_toolboxes.github import build_github_tools
from janus.agent_toolboxes.images import IMAGES_ENTRY
from janus.agent_toolboxes.knowledge import build_knowledge_tools
from janus.agent_toolboxes.linear import build_linear_tools
from janus.agent_toolboxes.local_tasks import build_local_task_tools
from janus.agent_toolboxes.mail import build_mail_tools
from janus.agent_toolboxes.mail_processing import build_mail_processing_tools
from janus.agent_toolboxes.memory import build_memory_tools
from janus.agent_toolboxes.planner_suggestions import (
    PLANNER_SUGGEST_ENTRY,
    PLANNER_SUGGESTIONS_ENTRY,
    PLANNER_SUGGESTIONS_REVIEW_ENTRY,
)
from janus.agent_toolboxes.remote_agents import remote_agent_entries
from janus.agent_toolboxes.remote_mcp import discovered_tools, remote_tool_entries
from janus.agent_toolboxes.rules import build_rule_tools
from janus.agent_toolboxes.self_evolution import SELF_EVOLUTION_ENTRY
from janus.agent_toolboxes.shared import ToolContext
from janus.agent_toolboxes.shell import SHELL_ENTRY
from janus.agent_toolboxes.skills import build_skill_tools
from janus.agent_toolboxes.tasks import build_task_tools
from janus.agent_toolboxes.telegram import TELEGRAM_NOTIFY_ENTRY
from janus.agent_toolboxes.topics import build_topic_tools
from janus.agent_toolboxes.triggers import build_trigger_tools
from janus.agent_toolboxes.ui_actions import UI_ACTIONS_ENTRY
from janus.agent_toolboxes.ui_blocks import UI_BLOCKS_ENTRY
from janus.agent_toolboxes.ui_introspection import UI_INTROSPECTION_ENTRY
from janus.agent_toolboxes.web_fetch import WEB_FETCH_ENTRY
from janus.agent_toolboxes.web_search import build_web_search_tools
from janus.agent_toolboxes.whatsapp import WHATSAPP_ENTRY, WHATSAPP_NOTIFY_ENTRY, WHATSAPP_SEND_ENTRY
from janus.agent_toolboxes.wiki import build_wiki_propose_tools, build_wiki_tools
from janus.capability_profile import user_connected_capabilities
from janus.services import skill_catalog_service
from janus.tool_registry import (
    ToolBuildContext,
    ToolRegistry,
    ToolRegistryEntry,
    ToolSelection,
    ToolSelectionRequest,
)


def _only(tools: Iterable[BaseTool], names: frozenset[str]) -> list[BaseTool]:
    """Keep the named tools of a toolbox, so one builder can back several capabilities."""
    return [tool for tool in tools if tool.name in names]


_MAIL_READ_TOOLS = frozenset({"search_emails", "get_email_detail", "get_email_details_batch"})
_MAIL_LABEL_TOOLS = frozenset({"relabel_email", "relabel_emails_batch"})
_MAIL_SEND_TOOLS = frozenset({"send_email"})
_RULE_READ_TOOLS = frozenset({"list_mail_rules"})
_LOCAL_TASK_READ_TOOLS = frozenset({"list_local_tasks", "get_local_task"})


def _build_default_registry() -> ToolRegistry:
    """Build the compatibility registry for the existing toolbox builders."""
    return ToolRegistry(
        [
            ToolRegistryEntry(
                id="core",
                display_name="Core tools",
                description="Always-available Janus utility tools.",
                required_capabilities=(),
                operation="read",
                risk_level="low",
                confirmation="never",
                docs_ref="tools.md#core-tools",
                builder=lambda context: build_common_tools(context.tool_context),
            ),
            ToolRegistryEntry(
                id="events",
                reads_untrusted_content=True,
                display_name="System introspection tools",
                description="Read the Janus event log entries about this user.",
                required_capabilities=(),
                operation="read",
                risk_level="low",
                confirmation="never",
                docs_ref="tools.md#system-introspection-tools",
                # Owner-scoped (JAN-272): only a human-watched chat turn gets
                # the GET /api/events view (an admin's whole log); every other
                # mode, unattended runs included, reads only the owner's rows.
                builder=lambda context: build_event_tools(
                    context.tool_context, own_rows_only=context.task_mode != "chat"
                ),
            ),
            ToolRegistryEntry(
                id="trigger-history",
                reads_untrusted_content=True,
                display_name="Trigger history",
                description="Read this user's own runtime trigger outcomes, so a run can check prior results.",
                required_capabilities=(),
                operation="read",
                risk_level="low",
                confirmation="never",
                docs_ref="tools.md#trigger-history",
                builder=lambda context: build_trigger_history_tools(context.tool_context),
                # Owner-scoped and payload-free, so it is safe for unattended
                # runs too (split out of "events" in JAN-195).
            ),
            ToolRegistryEntry(
                id="mail",
                reads_untrusted_content=True,
                display_name="Read mail",
                description="Search and read stored email.",
                required_capabilities=("mail",),
                operation="read",
                risk_level="medium",
                confirmation="never",
                docs_ref="tools.md#read-mail",
                builder=lambda context: _only(build_mail_tools(context.tool_context), _MAIL_READ_TOOLS),
                enabled_when=lambda context: "mail" in context.tool_context.connected_capabilities,
            ),
            ToolRegistryEntry(
                id="mail.label",
                display_name="Relabel mail",
                description="Change the Janus priority label (IMPORTANT/DIGEST/IGNORE/SPAM) of stored email.",
                required_capabilities=("mail",),
                operation="write",
                risk_level="medium",
                confirmation="recommended",
                docs_ref="tools.md#relabel-mail",
                builder=lambda context: _only(build_mail_tools(context.tool_context), _MAIL_LABEL_TOOLS),
                enabled_when=lambda context: "mail" in context.tool_context.connected_capabilities,
            ),
            ToolRegistryEntry(
                id="mail.send",
                display_name="Send mail",
                description="Send email from one of the user's accounts.",
                required_capabilities=("mail",),
                operation="write",
                risk_level="high",
                confirmation="required",
                docs_ref="tools.md#send-mail",
                builder=lambda context: _only(build_mail_tools(context.tool_context), _MAIL_SEND_TOOLS),
                enabled_when=lambda context: "mail" in context.tool_context.connected_capabilities,
            ),
            ToolRegistryEntry(
                id="mail.processing",
                display_name="Mail processing",
                description=(
                    "Classify an incoming email and record its reminders, tracked topics, and planner suggestions."
                ),
                required_capabilities=("mail",),
                operation="write",
                risk_level="medium",
                confirmation="recommended",
                docs_ref="tools.md#mail-processing",
                builder=lambda context: build_mail_processing_tools(context.tool_context),
                enabled_when=lambda context: "mail" in context.tool_context.connected_capabilities,
            ),
            WHATSAPP_ENTRY,  # read-only WhatsApp conversations and messages (JAN-347)
            WHATSAPP_SEND_ENTRY,  # send WhatsApp text messages, approved per call (JAN-360)
            WHATSAPP_NOTIFY_ENTRY,  # the Secretary's WhatsApp alert to the owner's own number
            TELEGRAM_NOTIFY_ENTRY,  # an unattended run's alert to the owner's linked Telegram chat (JAN-405)
            ToolRegistryEntry(
                id="calendar",
                reads_untrusted_content=True,
                display_name="Calendar tools",
                description="Inspect connected calendar events.",
                required_capabilities=("calendar",),
                operation="read",
                risk_level="low",
                confirmation="never",
                docs_ref="tools.md#calendar-tools",
                builder=lambda context: build_calendar_tools(context.tool_context),
                enabled_when=lambda context: "calendar" in context.tool_context.connected_capabilities,
            ),
            *microsoft_toolbox.registry_entries(),
            *azure_toolbox.registry_entries(),
            ToolRegistryEntry(
                id="jira",
                reads_untrusted_content=True,
                display_name="Jira task tools",
                description="Inspect planner tasks from the Jira integration.",
                required_capabilities=("jira",),
                operation="read",
                risk_level="low",
                confirmation="never",
                docs_ref="tools.md#jira-task-tools",
                builder=lambda context: build_task_tools(context.tool_context),
                enabled_when=lambda context: "jira" in context.tool_context.connected_capabilities,
            ),
            ToolRegistryEntry(
                id="planner.local",
                display_name="Read local planner",
                description="Read Janus-owned local planner tasks.",
                required_capabilities=("planner.local",),
                operation="read",
                risk_level="low",
                confirmation="never",
                docs_ref="tools.md#read-local-planner",
                builder=lambda context: build_local_task_tools(context.tool_context, include_write=False),
                # Ungated (JAN-201): the single Janus agent always gets its own
                # local planner.
            ),
            ToolRegistryEntry(
                id="planner.local.write",
                display_name="Change local planner",
                description="Create, update, complete, move, and reorder Janus-owned local planner tasks.",
                required_capabilities=("planner.local",),
                operation="write",
                risk_level="medium",
                confirmation="required",
                docs_ref="tools.md#change-local-planner",
                builder=lambda context: [
                    tool
                    for tool in build_local_task_tools(context.tool_context, include_write=True)
                    if tool.name not in _LOCAL_TASK_READ_TOOLS
                ],
            ),
            PLANNER_SUGGESTIONS_ENTRY,  # list pending suggestions and their duplicates
            PLANNER_SUGGESTIONS_REVIEW_ENTRY,  # dismiss them in bulk, collapse duplicates, restore
            PLANNER_SUGGEST_ENTRY,  # propose one not caused by an email (the Secretary)
            ToolRegistryEntry(
                id="dashboard",
                reads_untrusted_content=True,
                display_name="Dashboard tools",
                description="Read and update dashboard context and preferences.",
                required_capabilities=("dashboard",),
                operation="mixed",
                risk_level="medium",
                confirmation="recommended",
                docs_ref="tools.md#dashboard-tools",
                builder=lambda context: build_dashboard_tools(context.tool_context),
                enabled_when=lambda context: "dashboard" in context.tool_context.connected_capabilities,
            ),
            ToolRegistryEntry(
                id="github",
                reads_untrusted_content=True,
                display_name="GitHub tools",
                description="Inspect and manage authorized GitHub pull requests.",
                required_capabilities=("github",),
                operation="mixed",
                risk_level="high",
                confirmation="required",
                docs_ref="tools.md#github-tools",
                builder=lambda context: build_github_tools(context.tool_context),
                # GitHub is a single, instance-wide integration (Settings.github_token),
                # not a per-user connection. Always visible; the capability tier
                # (user_connected_capabilities) denies it with a real, auditable
                # missing_capability reason when unconfigured.
            ),
            ToolRegistryEntry(
                id="linear",
                reads_untrusted_content=True,
                display_name="Linear tools",
                description="Inspect and manage authorized Linear work items.",
                required_capabilities=("linear",),
                operation="mixed",
                risk_level="high",
                confirmation="required",
                docs_ref="tools.md#linear-tools",
                builder=lambda context: build_linear_tools(context.tool_context),
                # Same rationale as "github" above: instance-wide integration, always
                # visible, real enforcement lives in the capability tier + the tool's own
                # actionable error on invocation.
            ),
            ToolRegistryEntry(
                id="web_search",
                reads_untrusted_content=True,
                display_name="Web search",
                description="Search the public web through the configured privacy-respecting backend.",
                required_capabilities=("web_search",),
                # Read-only outbound query; nothing local is mutated, so
                # "never" per the tier definitions. Only the model-written
                # query leaves Janus, and results come back wrapped as
                # untrusted content (services/web_search_service.py).
                operation="read",
                risk_level="low",
                confirmation="never",
                docs_ref="tools.md#web-search",
                builder=lambda context: build_web_search_tools(context.tool_context),
                # Unattended runs get it unless the owner switches it off on the Tools page
                # (JAN-255; the default follows its confirmation level, like every other tool).
                # The task_id gate keeps it out of any registry build outside a plan step. The
                # capability tier denies it with missing_capability until a backend is configured.
                enabled_when=lambda context: context.task_id is not None,
            ),
            WEB_FETCH_ENTRY,  # read one explicit web page (JAN-329)
            ToolRegistryEntry(
                id="memory",
                display_name="Memory tools",
                description="Search and manage the user’s durable Janus memory facts.",
                required_capabilities=("memory",),
                operation="mixed",
                risk_level="medium",
                confirmation="recommended",
                docs_ref="tools.md#memory-tools",
                builder=lambda context: build_memory_tools(context.tool_context),
                # Ungated (JAN-201): always available to the single Janus agent.
            ),
            ToolRegistryEntry(
                id="topics",
                reads_untrusted_content=True,
                display_name="Topic tools",
                description="Inspect and update the user’s tracked topics.",
                required_capabilities=("topics",),
                operation="mixed",
                risk_level="medium",
                confirmation="recommended",
                docs_ref="tools.md#topic-tools",
                builder=lambda context: build_topic_tools(context.tool_context),
                # Ungated (JAN-201): always available to the single Janus agent.
            ),
            ToolRegistryEntry(
                id="knowledge",
                reads_untrusted_content=True,
                display_name="Knowledge search",
                description=(
                    "Search everything Janus has ingested (mail, attachments, chats, calendar, tasks, documents) "
                    "and look up extracted people, organizations, and projects with their relations."
                ),
                required_capabilities=("knowledge",),
                operation="read",
                risk_level="low",
                confirmation="never",
                docs_ref="tools.md#knowledge-search",
                builder=lambda context: build_knowledge_tools(context.tool_context),
                # Read-only over the owner's own data (JAN-243); always available.
            ),
            ToolRegistryEntry(
                id="wiki",
                reads_untrusted_content=True,
                display_name="Read the wiki",
                description="List and read the user's wiki pages about tracked topics and frequent entities.",
                required_capabilities=("wiki",),
                operation="read",
                risk_level="low",
                confirmation="never",
                docs_ref="tools.md#read-the-wiki",
                builder=lambda context: build_wiki_tools(context.tool_context),
                # Read-only over the owner's own pages (JAN-244); always available.
            ),
            ToolRegistryEntry(
                id="wiki.propose",
                display_name="Propose wiki edits",
                description="Propose a new text for a wiki page; the user reviews and accepts or dismisses it.",
                required_capabilities=("wiki",),
                operation="write",
                risk_level="medium",
                confirmation="required",
                docs_ref="tools.md#propose-wiki-edits",
                builder=lambda context: build_wiki_propose_tools(context.tool_context),
            ),
            ToolRegistryEntry(
                id="triggers",
                display_name="Trigger tools",
                description="Schedule, inspect, and cancel the user’s own runtime follow-up triggers.",
                required_capabilities=("triggers",),
                operation="mixed",
                risk_level="medium",
                confirmation="recommended",
                docs_ref="tools.md#trigger-tools",
                # Unattended runs may use it (JAN-274): the toolbox records each
                # follow-up's parent run and refuses runaway chains itself.
                builder=lambda context: build_trigger_tools(
                    context.tool_context, task_mode=context.task_mode, run_trigger_id=context.trigger_id
                ),
            ),
            ToolRegistryEntry(
                id="triggers.propose",
                display_name="Propose automations",
                description=(
                    "Draft trigger definitions (automations) for the user to review; a draft never runs "
                    "until the user approves it on Settings -> Triggers."
                ),
                required_capabilities=("triggers",),
                # Writes inert rows only; the real gate is the user's approval (JAN-235).
                operation="write",
                risk_level="medium",
                confirmation="recommended",
                docs_ref="tools.md#propose-automations",
                builder=lambda context: build_automation_proposal_tools(context.tool_context),
                # Chat only: locked out of every unattended run (tool_policy.LOCKED_UNATTENDED).
            ),
            ToolRegistryEntry(
                id="rules",
                display_name="Read mail rules",
                description="List email classification rules.",
                required_capabilities=("rules",),
                operation="read",
                risk_level="low",
                confirmation="never",
                docs_ref="tools.md#read-mail-rules",
                builder=lambda context: _only(build_rule_tools(context.tool_context), _RULE_READ_TOOLS),
                # Rules classify incoming mail, so they only make sense once a
                # mail account is connected.
                enabled_when=lambda context: "mail" in context.tool_context.connected_capabilities,
            ),
            ToolRegistryEntry(
                id="rules.write",
                display_name="Manage mail rules",
                description="Create, update, and delete classification rules.",
                required_capabilities=("rules",),
                operation="write",
                risk_level="medium",
                confirmation="required",
                docs_ref="tools.md#manage-mail-rules",
                builder=lambda context: [
                    tool for tool in build_rule_tools(context.tool_context) if tool.name not in _RULE_READ_TOOLS
                ],
                enabled_when=lambda context: "mail" in context.tool_context.connected_capabilities,
            ),
            ToolRegistryEntry(
                id="delegation",
                display_name="Task delegation tools",
                description="Delegate bounded, read-only child tasks and check their results.",
                required_capabilities=(),
                # Writes durable rows and spends LLM budget, but every tool a
                # child can reach is read-only (see tool_policy.delegable_tool_ids),
                # so it is "recommended", not "required".
                operation="write",
                risk_level="medium",
                confirmation="recommended",
                docs_ref="tools.md#task-delegation-tools",
                builder=build_delegation_tools,
                # Needs a plan-runtime task identity to link the child to its
                # parent, so it never appears in a registry build outside a
                # plan step.
                enabled_when=lambda context: context.task_id is not None,
            ),
            ToolRegistryEntry(
                id="skills",
                display_name="Skill library tools",
                description="Create, update, search, and import skills in the shared Janus skill library.",
                required_capabilities=("skills",),
                operation="admin",
                risk_level="high",
                confirmation="required",
                docs_ref="tools.md#skill-library-tools",
                builder=lambda context: build_skill_tools(context.tool_context, skill_catalog=context.skill_catalog),
                # Admin-only: skills are instance-wide prompt content, so editing
                # them steers every user's Janus.
                enabled_when=lambda context: context.user.is_admin,
            ),
            ToolRegistryEntry(
                id="docs",
                display_name="Janus documentation",
                description="List, read, and search Janus's own documentation to answer questions about Janus itself.",
                required_capabilities=(),
                operation="read",
                risk_level="low",
                confirmation="never",
                docs_ref="tools.md#janus-documentation",
                builder=lambda context: build_docs_tools(context.tool_context),
                # First-party pages shipped with the code, so deliberately not reads_untrusted_content (JAN-297,
                # JAN-127); reviews/ is excluded by the toolbox. Always available, read-only, and therefore
                # delegable; unattended runs follow the switch (default Allowed).
            ),
            UI_ACTIONS_ENTRY,  # chat-only page actions (JAN-251)
            UI_BLOCKS_ENTRY,  # chat-only generative UI blocks (JAN-252)
            UI_INTROSPECTION_ENTRY,  # chat-only page outline and element styles (JAN-284)
            IMAGES_ENTRY,  # chat-only cloud image generation (JAN-253)
            SELF_EVOLUTION_ENTRY,  # opt-in autonomous self-evolution loop (JAN-320)
            CONVERSATIONS_START_ENTRY,  # unattended runs start a conversation with their owner (JAN-319)
            *CHARACTER_ENTRIES,  # adaptive character state: read, and propose from chat only (JAN-301)
            SHELL_ENTRY,  # run a command on the Janus host: operator opt-in, admins only (JAN-353)
            CODING_AGENTS_ENTRY,  # ask a local coding-agent CLI: operator opt-in, admins only (JAN-403)
        ]
    )


DEFAULT_TOOL_REGISTRY = _build_default_registry()


def build_managed_registry(db: Session, user: models.User) -> ToolRegistry:
    """Return the default registry plus the user's offered remote MCP tools and remote agents (JAN-343, JAN-404).

    Chat builds this per message (``websocket/chat.py``); the remote entries
    are enabled only in chat steps. Every other path keeps
    ``DEFAULT_TOOL_REGISTRY``, which never contains remote tools.
    """
    return ToolRegistry(
        [*DEFAULT_TOOL_REGISTRY.entries(), *remote_tool_entries(db, user), *remote_agent_entries(db, user)]
    )


def build_policy_registry(db: Session, user: models.User) -> ToolRegistry:
    """Return the defaults plus *every* discovered remote tool of the user, offered or not (JAN-343).

    The tool-policy API uses it, so a switch on a tool whose server is disabled or failed discovery can
    still be listed and reset.
    """
    from janus.services.a2a_service import A2AService

    remote = [*remote_tool_entries(db, user, discovered_tools(db, user))]
    remote += remote_agent_entries(db, user, A2AService(db).list_agents(user))
    return ToolRegistry([*DEFAULT_TOOL_REGISTRY.entries(), *remote])


def build_tool_set(
    db: Session,
    user: models.User,
    *,
    conversation_id: int | None = None,
) -> list[BaseTool]:
    """Build every built-in tool enabled for this user, outside any plan step.

    Introspection/test helper: live execution goes through
    `RuntimeAdapter.prepare_step()`, which applies the task-mode allowlist and
    confirmation gate. Because no task identity is supplied here, plan-step-only
    toolboxes (``delegation``, ``web_search``, ``web_fetch``) are never built by this helper.
    """
    context = ToolContext(
        db=db,
        user=user,
        conversation_id=conversation_id,
        connected_capabilities=user_connected_capabilities(db, user),
    )
    return DEFAULT_TOOL_REGISTRY.build(ToolBuildContext(tool_context=context, skill_catalog=skill_catalog_service))


def build_authorized_tool_set(
    db: Session,
    user: models.User,
    selection_request: ToolSelectionRequest,
    *,
    conversation_id: int | None = None,
) -> tuple[ToolSelection, list[BaseTool]]:
    """Build a task-scoped toolset while returning the authorization decision."""
    context = ToolContext(
        db=db,
        user=user,
        conversation_id=conversation_id,
        # Computed once here and reused by every gated entry's enabled_when.
        # This is real connection state, not `selection_request.granted_capabilities`
        # (a separately testable policy tier -- see ToolRegistry.authorize()).
        connected_capabilities=user_connected_capabilities(db, user),
    )
    return DEFAULT_TOOL_REGISTRY.build_authorized(
        ToolBuildContext(tool_context=context, skill_catalog=skill_catalog_service),
        selection_request,
    )
