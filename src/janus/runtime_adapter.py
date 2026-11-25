"""Runtime adapter: inbound task/message -> plan step -> authorized toolset.

This module is the single production entrypoint that call sites (chat,
briefing, mail ingestion, and trigger-driven execution) should use to turn an
inbound task or message into a plan step, select an authorized, task-scoped
toolset via the existing :mod:`janus.tool_registry` / :func:`build_authorized_tool_set`
machinery, and perform a pre-execution policy/confirmation gate before any
tool is allowed to run.

Callers get:

* per-message (not per-conversation-cached) tool selection,
* a stable, auditable denial reason for every requested-but-not-selected
  tool (unknown tool, not allowlisted, disabled by context, missing
  capability, or awaiting confirmation), and
* a pre-execution confirmation gate for tools whose registry entry marks
  ``confirmation="required"`` (e.g. sending mail, writing to Jira/Linear/
  GitHub, or skill-library mutations) -- those tools are authorized in
  principle but withheld from the buildable toolset until the caller passes
  their id back in ``confirmed_tool_ids``, or (chat, JAN-254) passes an
  ``approval_gate`` that asks the user before each call.

Identity and system-prompt content never come from here: every surface renders
the user's character constitution (``janus.character``). See
``docs/docs/tech-spec/background-workers-runtime.md``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace

from langchain_core.tools import BaseTool, StructuredTool
from sqlalchemy.orm import Session

from janus import event_logger, models, prompt_injection
from janus.agent_toolboxes.shared import ToolContext, TurnToolCall, TurnTrace
from janus.agent_tools import DEFAULT_TOOL_REGISTRY
from janus.capability_profile import user_connected_capabilities
from janus.plan_graph import PlanStep, TaskContext
from janus.services import injection_classifier, skill_catalog_service
from janus.services.injection_classifier import InjectionClassifier
from janus.services.mcp_metadata import is_remote_tool_id
from janus.services.ui_actions import UI_ACTION_TOOL_NAMES
from janus.tool_policy import UNATTENDED_TASK_MODES, locked_reason
from janus.tool_registry import (
    ToolBuildContext,
    ToolRegistry,
    ToolRegistryEntry,
    ToolSelection,
    ToolSelectionDenial,
    ToolSelectionRequest,
)
from janus.toolset_selection import ToolsetChoice, narrow_selection

#: Default non-model tool allowlists for the supervised and system-pipeline
#: task modes. These are the "policy" half of tool authorization -- the model
#: only ever supplies ``requested_tool_ids``; it can never widen this
#: allowlist. The unattended modes (``trigger``, ``automation``,
#: ``delegated``) have no fixed list: their tools come from the user's
#: per-tool switch (``janus.tool_policy.unattended_tool_sets``, JAN-255) and
#: are always passed as an explicit ``allowed_tool_ids``.
TASK_MODE_ALLOWED_TOOL_IDS: dict[str, frozenset[str]] = {
    "chat": frozenset(
        {
            "core",
            "events",
            "trigger-history",
            "mail",
            "mail.label",
            "mail.send",
            "whatsapp",
            "whatsapp.send",  # JAN-360: confirmation="required", so each call is approved.
            "calendar",
            "jira",
            "planner.local",
            "planner.local.write",
            "dashboard",
            "github",
            "linear",
            "web_search",
            # One explicit web page (JAN-329); confirmation="required", so each call is approved.
            "web_fetch",
            "memory",
            "topics",
            "knowledge",
            "wiki",
            "wiki.propose",
            "triggers",
            # Chat only (JAN-235): Janus drafts automations the user approves;
            # also in tool_policy.LOCKED_UNATTENDED so no unattended run gets it.
            "triggers.propose",
            "rules",
            "rules.write",
            "delegation",
            "skills",
            # Read-only, first-party documentation (JAN-297).
            "docs",
            # Page actions (JAN-251): client-side only, chat only (also in LOCKED_UNATTENDED).
            "ui_actions",
            # Generative UI blocks (JAN-252): display only, chat only (also in LOCKED_UNATTENDED).
            "ui_blocks",
            # Cloud image generation (JAN-253): chat only (also in LOCKED_UNATTENDED).
            "images",
            # UI introspection (JAN-284): reads the user's open page, chat only (also in LOCKED_UNATTENDED).
            "ui_introspection",
            # Self-evolution loop (JAN-320): asks before each call in chat; unattended per its own switch.
            "self_evolution",
            # Adaptive character state (JAN-301): read it; propose changes in chat only (LOCKED_UNATTENDED).
            "character",
            "character.propose",
            "shell",  # Shell commands (JAN-353): operator opt-in, admins only, each call approved in chat.
            "coding_agents",  # Local coding-agent CLIs (JAN-403): operator opt-in, admins only, approved per call.
        }
    ),
    "briefing": frozenset({"core", "calendar", "jira", "dashboard", "topics", "knowledge", "wiki"}),
    "digest": frozenset({"core", "calendar", "jira", "dashboard", "topics", "knowledge", "wiki"}),
    "ingestion": frozenset({"core", "mail", "planner.local", "topics"}),
}

#: Every task mode the adapter can authorize, for callers that validate a mode name.
KNOWN_TASK_MODES: frozenset[str] = frozenset(TASK_MODE_ALLOWED_TOOL_IDS) | UNATTENDED_TASK_MODES

#: Registry confirmation levels that must be explicitly confirmed by the
#: caller before the adapter will build the tool, regardless of whether the
#: tool was otherwise authorized.
_CONFIRMATION_GATED_LEVELS = {"required"}

#: Individual tool *names* withheld per task mode even when their registry
#: entry is authorized. Registry entries are whole toolboxes, so this is the
#: finer-grained fence for the ``confirmation="recommended"`` toolboxes a
#: human-absent mode may reach (JAN-193): a trigger may add, update and merge
#: memory facts but not delete them, and may toggle dashboard widgets but not
#: rewrite or erase widget prompt overrides -- a persisted prompt is a durable
#: steering channel for later LLM runs (and a user-authored one, when
#: cleared), too consequential to change with no human watching.
TRIGGER_WITHHELD_TOOL_NAMES_BY_ENTRY: dict[str, frozenset[str]] = {
    "memory": frozenset({"delete_fact"}),
    "dashboard": frozenset({"set_dashboard_widget_prompt", "clear_dashboard_widget_prompt"}),
}
TASK_MODE_WITHHELD_TOOL_NAMES: dict[str, frozenset[str]] = {
    "trigger": frozenset().union(*TRIGGER_WITHHELD_TOOL_NAMES_BY_ENTRY.values()),
}


#: Tool names of ``confirmation="recommended"`` entries that, like every
#: ``confirmation="required"`` entry, are refused for the rest of a step once
#: something read in it looked like a prompt injection (JAN-127): they persist
#: state that steers later runs (memory, scheduled triggers, widget prompts).
#: The ``triggers.propose`` tools are deliberately *not* here (JAN-235,
#: resolved question 6): a draft is inert until the user approves it, so after
#: a detection it is still saved but labelled ``injection_suspected`` and
#: shown with a warning on its review card. Every page action (JAN-251) is
#: here too: a redirect or highlight can be an injection's goal. So is
#: ``generate_image`` (JAN-253): an injected mail must not spend money or push
#: its text to an image provider. So is ``start_conversation`` (JAN-319).
INJECTION_ESCALATED_TOOL_NAMES: frozenset[str] = UI_ACTION_TOOL_NAMES | frozenset(
    {
        "add_memory_fact",
        "update_fact",
        "delete_fact",
        "group_facts",
        "create_scheduled_trigger",
        "set_dashboard_widget_prompt",
        "clear_dashboard_widget_prompt",
        "generate_image",
        "start_conversation",
        "notify_me_on_whatsapp",
    }
)

#: Pseudo tool id/name recorded in the turn trace when an unattended
#: objective's embedded ``<event_data>``/``<email_content>``/``<prepared_context>`` blocks were scanned.
OBJECTIVE_CONTENT_TOOL_ID = "run_context"

#: Call-log detail of a tool refused because of a suspected prompt injection.
INJECTION_REFUSAL_REASON = "injection_suspected"

#: Denial reasons for which a requested tool is still offered to the model as a
#: refusal stub (JAN-229): the tool exists and was asked for, but policy or a
#: missing connection keeps it from running. ``unknown_tool``/``not_allowlisted``
#: are not stubbed -- there is nothing meaningful to call.
STUBBED_DENIAL_REASONS = frozenset({"confirmation_required", "missing_capability", "disabled_by_context"})

_REFUSAL_EXPLANATIONS = {
    "confirmation_required": (
        "the user has switched it off for unattended runs (Settings -> Tools & Connections) "
        "and nobody is here to allow it"
    ),
    "missing_capability": "the integration it needs is not connected",
    "disabled_by_context": "it is not available in this context",
}


@dataclass
class ToolCallRecord:
    """One refused or failed tool call, for the run's audit trail."""

    tool_name: str
    tool_id: str
    outcome: str
    detail: str


@dataclass
class ToolCallLog:
    """Mutable per-step record of refused and failed tool calls."""

    records: list[ToolCallRecord] = field(default_factory=list)

    def refused(self) -> list[ToolCallRecord]:
        """Return calls refused by policy or missing connections."""
        return [record for record in self.records if record.outcome == "refused"]

    def failed(self) -> list[ToolCallRecord]:
        """Return calls that raised while running."""
        return [record for record in self.records if record.outcome == "failed"]


def _clone_tool(tool: BaseTool, func) -> BaseTool:
    """Build a tool with the same name, description, and arguments that runs ``func``."""
    return StructuredTool.from_function(
        func=func,
        name=tool.name,
        description=tool.description,
        args_schema=tool.args_schema,
        infer_schema=False,
    )


def _refusal_stub(
    tool: BaseTool, tool_id: str, reason: str, log: ToolCallLog, user_id: int, docs_ref: str | None = None
) -> BaseTool:
    explanation = _REFUSAL_EXPLANATIONS.get(reason, reason)
    message = f"BLOCKED: {tool.name} was not run because {explanation}. Nothing was changed."
    if docs_ref:
        # Lets Janus (or the user) look up why with read_doc (JAN-297).
        message += f" See the docs: {docs_ref}."

    def refuse(**_kwargs):
        log.records.append(ToolCallRecord(tool.name, tool_id, "refused", reason))
        event_logger.log(
            models.EventType.WARNING,
            "RuntimeAdapter",
            f"Refused tool call {tool.name}",
            {"user_id": user_id, "tool_id": tool_id, "reason": reason},
            user_id=user_id,
        )
        return message

    return _clone_tool(tool, refuse)


def _escalates_on_injection(entry: ToolRegistryEntry, tool: BaseTool) -> bool:
    escalated = entry.confirmation == "required" or tool.name in INJECTION_ESCALATED_TOOL_NAMES
    return escalated or is_remote_tool_id(entry.id)  # remote MCP: model-chosen args go to a third party (JAN-343)


def _traced(
    tool: BaseTool,
    entry: ToolRegistryEntry,
    trace: TurnTrace,
    log: ToolCallLog,
    audit: dict[str, object],
    classifier: InjectionClassifier | None = None,
) -> BaseTool:
    """Record calls, spotlight and scan untrusted output (rules, then the opt-in classifier), escalate (JAN-127)."""
    escalates = _escalates_on_injection(entry, tool)

    def run(**kwargs):
        if escalates and trace.injection_suspected():
            log.records.append(ToolCallRecord(tool.name, entry.id, "refused", INJECTION_REFUSAL_REASON))
            event_logger.log(
                models.EventType.WARNING,
                "RuntimeAdapter",
                f"Refused tool call {tool.name} after a suspected prompt injection",
                {**audit, "tool_id": entry.id, "reason": INJECTION_REFUSAL_REASON},
            )
            return (
                f"BLOCKED: {tool.name} was not run because content read earlier looked like a prompt injection "
                "(instructions hidden in mail, web, or other outside content). Nothing was changed. Tell the user "
                "what you wanted to do and why; they can ask for it again in a new message. "
                "See the docs: tools.md#how-permissions-work."
            )
        result = tool.invoke(kwargs)
        if not entry.reads_untrusted_content:
            trace.record(TurnToolCall(entry.id, tool.name))
            return result
        rendered = prompt_injection.render_output(result)
        scan = prompt_injection.scan_output(result)
        if classifier is not None:
            scan = classifier.check(prompt_injection.output_text(result), scan)
        trace.record(TurnToolCall(entry.id, tool.name, True, scan.rules))
        if scan.suspected:
            _log_detection(audit, entry.id, tool.name, scan)
        return prompt_injection.spotlight(tool.name, rendered, scan)

    return _clone_tool(tool, run)


def _log_detection(audit: dict[str, object], tool_id: str, tool_name: str, scan) -> None:
    # Rule ids only, never the untrusted text: the event log can be read back
    # into chat (list_recent_events), which would replay the injection.
    event_logger.log(
        models.EventType.WARNING,
        "PromptInjectionGuard",
        f"Suspected prompt injection in {tool_name} output",
        {**audit, "tool_id": tool_id, "tool_name": tool_name, "rules": list(scan.rules)},
    )


def _scan_objective(
    objective: str, trace: TurnTrace, audit: dict[str, object], classifier: InjectionClassifier | None
) -> None:
    """Seed the trace with the scan of an unattended objective's untrusted blocks, if it has any."""
    if not prompt_injection.has_objective_blocks(objective):
        return
    scan = prompt_injection.scan_objective_blocks(objective)
    if classifier is not None:
        scan = classifier.check(prompt_injection.objective_block_text(objective), scan)
    trace.record(TurnToolCall(OBJECTIVE_CONTENT_TOOL_ID, OBJECTIVE_CONTENT_TOOL_ID, True, scan.rules))
    if scan.suspected:
        _log_detection(audit, OBJECTIVE_CONTENT_TOOL_ID, OBJECTIVE_CONTENT_TOOL_ID, scan)


def _guarded(tool: BaseTool, tool_id: str, log: ToolCallLog) -> BaseTool:
    def run(**kwargs):
        try:
            return tool.invoke(kwargs)
        except Exception as error:  # noqa: BLE001 - reported to the model instead of aborting the run
            detail = str(error).strip() or error.__class__.__name__
            log.records.append(ToolCallRecord(tool.name, tool_id, "failed", detail))
            return f"ERROR: {tool.name} failed: {detail}"

    return _clone_tool(tool, run)


#: Wraps one tool of a confirmation-required entry so each call waits for the
#: user's approval (``services/tool_approval.ToolApprovalBroker.gate``, JAN-254).
ApprovalGate = Callable[[BaseTool, ToolRegistryEntry, ToolCallLog, TurnTrace], BaseTool]


@dataclass(frozen=True)
class PreparedStep:
    """The result of adapting an inbound task/message into an authorized, policy-checked plan step.

    ``tools`` only ever contains tools that are allowlisted, capability-
    satisfied, enabled for this context, *and* not withheld by
    the confirmation gate -- nothing else should be handed to an LLM tool
    loop.
    """

    task: TaskContext
    step: PlanStep
    selection: ToolSelection
    tools: tuple[BaseTool, ...]
    awaiting_confirmation_tool_ids: tuple[str, ...] = ()
    #: Records refused and failed tool calls made during the step (see
    #: ``refusal_stubs`` / ``guard_tool_errors`` on :meth:`RuntimeAdapter.prepare_step`).
    call_log: ToolCallLog = field(default_factory=lambda: ToolCallLog())
    #: Successful tool calls of this step, untrusted reads, and prompt-injection
    #: detections (JAN-127); also reachable from tools as ``ToolContext.turn_trace``.
    trace: TurnTrace = field(default_factory=TurnTrace)
    #: Registry entry id of every offered tool (and refusal stub) by tool name,
    #: so streamed tool-call events can name the capability (JAN-249).
    tool_ids_by_name: Mapping[str, str] = field(default_factory=dict)
    toolset: ToolsetChoice | None = None  # the brain-selected toolset and why (JAN-300)

    @property
    def denials(self) -> tuple[ToolSelectionDenial, ...]:
        """Return the full, auditable set of denial reasons for this step."""
        return self.selection.denials


class RuntimeAdapter:
    """Adapts inbound tasks/messages into authorized, policy-checked plan steps.

    One adapter instance is scoped to a single request/message: construct it
    with the request's ``db`` session and authenticated ``user`` and call
    :meth:`prepare_step` once per message/trigger/ingestion decision.
    """

    def __init__(self, db: Session, user: models.User, *, registry: ToolRegistry | None = None) -> None:
        """Bind the adapter to a database session, user, and tool registry."""
        self.db = db
        self.user = user
        self.registry = registry if registry is not None else DEFAULT_TOOL_REGISTRY

    def prepare_step(  # noqa: C901
        self,
        *,
        task_id: str,
        objective: str,
        requested_tool_ids: Sequence[str] = (),
        task_mode: str = "chat",
        step_id: str | None = None,
        allowed_tool_ids: frozenset[str] | None = None,
        granted_capabilities: frozenset[str] = frozenset(),
        confirmed_tool_ids: frozenset[str] = frozenset(),
        conversation_id: int | None = None,
        blocked_tool_ids: frozenset[str] | None = None,
        refusal_stubs: bool = False,
        guard_tool_errors: bool = False,
        model_choice: object | None = None,
        scan_objective: bool = False,
        trigger_id: str | None = None,
        approval_gate: ApprovalGate | None = None,
        page_actions: object | None = None,
        ui_blocks: object | None = None,
        ui_introspection: object | None = None,
        toolset: ToolsetChoice | None = None,
        unattended_allow_all: bool = False,
    ) -> PreparedStep:
        """Turn one inbound task/message into an authorized, gated plan step.

        ``requested_tool_ids`` models what the calling surface (chat model,
        briefing generator, ingestion classifier, trigger handler) *wants*
        for this step; ``allowed_tool_ids``/``granted_capabilities`` are the
        non-model policy inputs the model can never widen. Tools whose
        registry entry requires confirmation are authorized but withheld
        from ``tools`` until their id appears in ``confirmed_tool_ids`` --
        callers should surface ``awaiting_confirmation_tool_ids`` to the user
        (or an automation's approval log) and re-call with the confirmation
        once granted.

        ``blocked_tool_ids`` replaces the registry default of which entries
        need confirmation (entries marked ``confirmation="required"``) with
        the owner's tool policy. With ``refusal_stubs``, requested tools denied
        for a reason in :data:`STUBBED_DENIAL_REASONS` are still offered, but
        answer ``BLOCKED: ...`` and are recorded in ``call_log`` instead of
        silently disappearing. With ``guard_tool_errors``, a tool that raises
        returns ``ERROR: ...`` to the model (and is logged) instead of
        aborting the whole run. ``trigger_id`` names the durable trigger run
        this step belongs to (see ``ToolBuildContext.trigger_id``).

        With ``approval_gate`` (chat only, JAN-254), an entry that needs
        confirmation and is not in ``confirmed_tool_ids`` is built anyway and
        each of its tools is wrapped (outermost) by the gate, which runs the
        call only once the user approved it; it is listed in the selection but
        not in ``authorized_tool_ids`` (nothing it spawns inherits it).
        ``page_actions`` (chat only, JAN-251) is the turn's page-action
        channel for the ``ui_actions`` tools (``ToolContext.page_actions``);
        ``ui_blocks`` (chat only, JAN-252) the turn's ``render_ui`` collector;
        ``ui_introspection`` (chat only, JAN-284) the connection's broker for
        the ``ui_introspection`` tools (``ToolContext.ui_introspection``).

        Every built tool is traced (JAN-127): successful calls land in
        ``PreparedStep.trace``; output of ``reads_untrusted_content`` entries is
        spotlighted and scanned for prompt injections; after a detection,
        consequential tools answer ``BLOCKED`` for the rest of the step. With
        ``scan_objective`` (unattended callers only -- never chat, whose
        objective is the user's own message), the objective's embedded
        ``<event_data>``/``<email_content>``/``<prepared_context>`` blocks are scanned up front.
        ``toolset`` (JAN-300), the brain's untrusted pick, can only remove authorized entries
        (``toolset_selection.narrow``, before the confirmation gate); see ``PreparedStep.toolset``.
        ``unattended_allow_all`` (JAN-359) sets ``ToolBuildContext.unattended_allow_all``.
        """
        effective_allowed = (
            allowed_tool_ids if allowed_tool_ids is not None else TASK_MODE_ALLOWED_TOOL_IDS.get(task_mode, frozenset())
        )
        deduped_requested = tuple(dict.fromkeys(requested_tool_ids))

        task = TaskContext(task_id=task_id, objective=objective, task_mode=task_mode)
        step = PlanStep(
            step_id=step_id or f"{task_id}::step-1",
            objective=objective,
            required_tool_ids=deduped_requested,
        )

        selection_request = ToolSelectionRequest(
            task_id=task_id,
            requested_tool_ids=deduped_requested,
            allowed_tool_ids=frozenset(effective_allowed),
            granted_capabilities=frozenset(granted_capabilities),
        )

        # Computed once here and reused by every registry entry's enabled_when
        # for this step (via ToolContext), instead of each gated entry
        # recomputing it independently -- avoids both the per-entry query
        # fan-out and a cross-entry inconsistency if connection state changed
        # mid-sweep. This is real per-user connection state, not the caller's
        # `granted_capabilities` policy input: enabled_when must stay
        # fail-closed against what's actually connected, not spoofable by a
        # caller-supplied capability set (which remains its own, separately
        # testable tier -- see ToolRegistry.authorize()'s missing_capability
        # check).
        connected_capabilities = user_connected_capabilities(self.db, self.user)

        trace = TurnTrace()
        audit: dict[str, object] = {
            "user_id": self.user.id,
            "task_id": task.task_id,
            "step_id": step.step_id,
            "task_mode": task_mode,
        }
        classifier = injection_classifier.for_user(self.db, self.user.id, audit)
        if scan_objective:
            _scan_objective(objective, trace, audit, classifier)

        tool_context = ToolContext(
            db=self.db,
            user=self.user,
            conversation_id=conversation_id,
            connected_capabilities=connected_capabilities,
            turn_trace=trace,
            page_actions=page_actions,
            ui_blocks=ui_blocks,
            ui_introspection=ui_introspection,
        )
        build_context = ToolBuildContext(
            tool_context=tool_context,
            skill_catalog=skill_catalog_service,
            model_choice=model_choice,
            task_id=task.task_id,
            step_id=step.step_id,
            task_mode=task_mode,
            trigger_id=trigger_id,
            unattended_allow_all=unattended_allow_all and task_mode in UNATTENDED_TASK_MODES,
        )

        raw_selection = self.registry.authorize(build_context, selection_request)
        raw_selection, effective_toolset = narrow_selection(toolset, raw_selection)  # only narrows (JAN-300)

        confirmed_entries = []
        gated_ids: set[str] = set()
        awaiting_confirmation: list[str] = []
        confirmation_denials: list[ToolSelectionDenial] = []

        def needs_confirmation(entry) -> bool:
            if blocked_tool_ids is not None:
                return entry.id in blocked_tool_ids
            return entry.confirmation in _CONFIRMATION_GATED_LEVELS

        for entry in raw_selection.entries:
            if needs_confirmation(entry) and entry.id not in confirmed_tool_ids:
                if approval_gate is not None:
                    gated_ids.add(entry.id)
                    confirmed_entries.append(entry)
                    continue
                awaiting_confirmation.append(entry.id)
                confirmation_denials.append(ToolSelectionDenial(entry.id, "confirmation_required"))
                continue
            confirmed_entries.append(entry)

        # Builders see exactly what this step was granted, so a toolbox that
        # spawns further work (``delegation``) can clamp it to the parent.
        # Chat-only (locked) entries never count as granted to anything spawned.
        build_context = replace(
            build_context,
            authorized_tool_ids=frozenset(
                entry.id for entry in confirmed_entries if entry.id not in gated_ids and locked_reason(entry.id) is None
            ),
        )
        withheld_names = TASK_MODE_WITHHELD_TOOL_NAMES.get(task_mode, frozenset())
        call_log = ToolCallLog()
        tools: list[BaseTool] = []
        tool_ids_by_name: dict[str, str] = {}
        for entry in confirmed_entries:
            for tool in entry.builder(build_context):
                if tool.name in withheld_names:
                    continue
                tool_ids_by_name[tool.name] = entry.id
                built = _traced(tool, entry, trace, call_log, audit, classifier)
                if guard_tool_errors:
                    built = _guarded(built, entry.id, call_log)
                if approval_gate is not None and entry.id in gated_ids:
                    built = approval_gate(built, entry, call_log, trace)
                tools.append(built)
        if refusal_stubs:
            entries_by_id = {entry.id: entry for entry in self.registry.entries()}
            offered = {tool.name for tool in tools}
            for denial in (*raw_selection.denials, *confirmation_denials):
                entry = entries_by_id.get(denial.tool_id)
                if entry is None or denial.reason not in STUBBED_DENIAL_REASONS:
                    continue
                try:
                    denied_tools = list(entry.builder(build_context))
                except Exception:  # noqa: BLE001 - an unbuildable toolbox simply gets no stub
                    continue
                for tool in denied_tools:
                    if tool.name in offered:
                        continue
                    offered.add(tool.name)
                    tool_ids_by_name[tool.name] = entry.id
                    tools.append(_refusal_stub(tool, entry.id, denial.reason, call_log, self.user.id, entry.docs_ref))

        final_selection = ToolSelection(
            entries=tuple(confirmed_entries),
            denials=raw_selection.denials + tuple(confirmation_denials),
        )

        return PreparedStep(
            task=task,
            step=step,
            selection=final_selection,
            tools=tuple(tools),
            awaiting_confirmation_tool_ids=tuple(awaiting_confirmation),
            call_log=call_log,
            trace=trace,
            tool_ids_by_name=tool_ids_by_name,
            toolset=effective_toolset,
        )


__all__ = [
    "ApprovalGate",
    "INJECTION_ESCALATED_TOOL_NAMES",
    "INJECTION_REFUSAL_REASON",
    "OBJECTIVE_CONTENT_TOOL_ID",
    "PreparedStep",
    "RuntimeAdapter",
    "STUBBED_DENIAL_REASONS",
    "ToolCallLog",
    "ToolCallRecord",
    "KNOWN_TASK_MODES",
    "TASK_MODE_ALLOWED_TOOL_IDS",
    "TASK_MODE_WITHHELD_TOOL_NAMES",
    "TRIGGER_WITHHELD_TOOL_NAMES_BY_ENTRY",
]
