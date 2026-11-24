"""Schemas for the Tasks page: persisted plan-runtime task state (JAN-204).

A *task* here is one unit of unattended plan-runtime work the database keeps:
a trigger run (ad hoc or from a saved definition) or a delegated child task.
Its plan steps and tool trace come from the durable ``PlanRuntimeState``
snapshot; nothing here reads a raw trigger payload.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

#: Where a task came from: an ad-hoc trigger run, a run of a saved trigger
#: definition, or a delegated child task.
RuntimeTaskSource = Literal["trigger", "definition", "delegated"]

#: Lifecycle statuses across both tables (a delegation uses a subset; ``waiting``
#: is a trigger run parked on its delegated children, JAN-207).
RuntimeTaskStatus = Literal["queued", "running", "waiting", "completed", "failed", "exhausted", "cancelled"]


class RuntimeTaskRef(BaseModel):
    """A link to another task on the Tasks page."""

    #: ``trigger`` for any trigger run (ad hoc or definition), ``delegated`` for a child task.
    kind: Literal["trigger", "delegated"]
    #: ``trigger_id`` or ``delegation_id``.
    ref_id: str


class RuntimeTaskSummary(BaseModel):
    """One row of the Tasks list."""

    source: RuntimeTaskSource
    #: ``trigger_id`` for trigger runs, ``delegation_id`` for delegated tasks.
    ref_id: str
    task_id: str
    #: The definition name, else the first line of the objective, else the trigger source.
    title: str
    status: RuntimeTaskStatus
    created_at: datetime
    completed_at: datetime | None = None
    #: ``PlanStatus`` of the persisted plan (``None`` before the plan exists).
    plan_status: str | None = None
    step_count: int = 0
    completed_step_count: int = 0
    failed_step_count: int = 0
    #: Objective of the step the plan cursor is on, while one is in progress.
    current_step: str | None = None
    definition_id: int | None = None
    definition_name: str | None = None
    #: The chat a delegated task was spawned from.
    conversation_id: int | None = None
    #: How many delegated child tasks this trigger run spawned.
    child_count: int = 0


class RuntimeTaskPage(BaseModel):
    """A newest-first page of tasks."""

    items: list[RuntimeTaskSummary]
    total: int
    limit: int
    offset: int
    has_more: bool


class RuntimeStepTrace(BaseModel):
    """The tool trace recorded for one executed step (decoded from its audit header).

    The unattended runtime writes this header into ``PlanStep.result``
    (``unattended_execution.format_step_result``); there is no per-call table
    for unattended runs, so this is everything that is persisted.
    """

    model: str | None = None
    authorized_tools: list[str] = []
    invoked_tools: list[str] = []
    #: ``tool:reason`` list of authorized tools that were unavailable.
    skipped: str | None = None
    #: Tool calls policy refused (e.g. switched off for unattended runs).
    refused: str | None = None
    #: Tool calls that raised.
    failed: str | None = None
    #: Tool reads that looked like a prompt injection.
    suspected_injection: str | None = None
    #: Which authorized toolboxes the brain selected for the step, and why (JAN-300).
    tool_selection: str | None = None


class RuntimeTaskStep(BaseModel):
    """One persisted plan step."""

    step_id: str
    objective: str
    status: str
    depends_on: list[str] = []
    required_tool_ids: list[str] = []
    #: ``None`` when the step has not run or recorded no audit header.
    trace: RuntimeStepTrace | None = None
    #: The model's answer for the step, without the audit header. Untrusted text.
    output: str | None = None
    error: str | None = None


class RuntimeTaskDetail(RuntimeTaskSummary):
    """Everything the database keeps about one task."""

    objective: str | None = None
    task_mode: str | None = None
    #: ``PlanGraphNode`` the cursor stopped on, and the recorded reason.
    node: str | None = None
    reason: str | None = None
    steps: list[RuntimeTaskStep] = []
    #: Trigger runs only: the trigger source, attempts, and the latest error.
    trigger_source: str | None = None
    attempt_count: int | None = None
    last_error: str | None = None
    #: Delegated tasks only: the tools the child was granted, its result or error.
    allowed_tool_ids: list[str] = []
    delegation_result: str | None = None
    delegation_error: str | None = None
    #: The trigger run that spawned or scheduled this task, when it is the owner's.
    parent: RuntimeTaskRef | None = None
    children: list[RuntimeTaskSummary] = []
