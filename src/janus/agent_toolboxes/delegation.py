"""Plan-driven delegation tools (JAN-200).

A running task hands a bounded piece of work to a *child task* in the same
Janus runtime instead of to a second named agent. The tools are thin,
owner-scoped wrappers over :mod:`janus.delegation_service` (persistence and
policy) and :mod:`janus.delegation_runtime` (execution).

Invariants that matter more than convenience:

* **Ownership.** The owner and parent task come from the authenticated
  :class:`~janus.tool_registry.ToolBuildContext`, never from a model argument.
  Another user's delegation reads as "not found" rather than "forbidden".
* **No widening.** A child may only use tools that are both in the
  ``delegated`` task-mode allowlist and authorized for *this* parent step
  (``ToolBuildContext.authorized_tool_ids``). The child is prepared with no
  pre-confirmed tools, so chat's own pre-confirmation never propagates.
* **Bounded.** Depth and fan-out caps are enforced by the service.
* **Waiting is for trigger runs only** (JAN-207). ``wait_for_delegated_tasks``
  is built only for a trigger-driven step and accepts only that step's own
  children; :mod:`janus.delegation_wait` decides whether the step may wait.
"""

from __future__ import annotations

from datetime import UTC
from typing import Any

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field
from sqlalchemy.orm import sessionmaker

from janus import models
from janus.agent_toolboxes.shared import iso
from janus.delegation_service import (
    DELEGATED_TASK_MODE,
    create_delegation,
    get_owned_delegation,
    list_owned_delegations,
)
from janus.delegation_wait import ensure_waiting_allowed, request_wait
from janus.tool_registry import ToolBuildContext


class DelegateTaskArgs(BaseModel):
    """Schema exposed to the model for delegating a bounded child task."""

    objective: str = Field(
        description=(
            "A complete, self-contained description of the work to delegate. The delegated task "
            "cannot see this conversation, so include every detail it needs."
        )
    )
    tool_ids: list[str] | None = Field(
        default=None,
        description=(
            "Optional tool group IDs the delegated task may use (for example 'calendar'). "
            "Omit to allow every tool group permitted for delegated tasks."
        ),
    )


class WaitForDelegatedTasksArgs(BaseModel):
    """Schema exposed to the model for waiting on its own delegated tasks (JAN-207)."""

    delegation_ids: list[str] = Field(
        description="The delegation_id values (from delegate_task) of the tasks this run should wait for."
    )


def _iso_utc(value) -> str | None:
    """Serialize a naive-UTC column as an explicitly UTC-aware string."""
    if value is None:
        return None
    return iso(value.replace(tzinfo=UTC) if value.tzinfo is None else value)


def serialize_delegation(record: models.RuntimeDelegation) -> dict[str, Any]:
    """Return a tool-friendly, owner-safe view of one delegation (no claim token)."""
    return {
        "delegation_id": record.delegation_id,
        "parent_task_id": record.parent_task_id,
        "child_task_id": record.child_task_id,
        "status": record.status,
        "objective": record.objective,
        "allowed_tool_ids": list(record.allowed_tool_ids or ()),
        "requested_tool_ids": list(record.requested_tool_ids or ()),
        "result": record.result,
        "error": record.error,
        "created_at": _iso_utc(record.created_at),
        "claimed_at": _iso_utc(record.claimed_at),
        "completed_at": _iso_utc(record.completed_at),
    }


def delegable_tool_ids(context: ToolBuildContext) -> frozenset[str]:
    """Return the tools a child of this step may use: the parent's grant, read-only tools only."""
    # Imported lazily: agent_tools imports this module to register the toolbox.
    from janus.agent_tools import DEFAULT_TOOL_REGISTRY
    from janus.tool_policy import delegable_tool_ids as read_only_grant

    return read_only_grant(context.authorized_tool_ids, DEFAULT_TOOL_REGISTRY.entries())


def build_delegation_tools(context: ToolBuildContext) -> list[BaseTool]:
    """Build the owner-scoped delegation toolbox for one authorized plan step."""
    if context.task_id is None:
        raise ValueError("delegation tools require a plan-runtime task identity")
    tool_context = context.tool_context
    db = tool_context.db
    user_id = tool_context.user.id
    parent_task_id = context.task_id
    parent_step_id = context.step_id
    conversation_id = tool_context.conversation_id
    allowed = delegable_tool_ids(context)
    # A delegated child is depth 1, so anything it spawned would be depth 2.
    # Children only get read-only tools, which already keeps this toolbox
    # away from them; computing the real depth makes the service's
    # MAX_DELEGATION_DEPTH check an independent second fence, not a constant.
    depth = 2 if context.task_mode == DELEGATED_TASK_MODE else 1
    session_factory = sessionmaker(bind=db.get_bind(), autocommit=False, autoflush=False)
    model_choice = context.model_choice

    @tool("delegate_task", args_schema=DelegateTaskArgs)
    def delegate_task(objective: str, tool_ids: list[str] | None = None) -> dict[str, Any]:
        """Delegate a bounded, self-contained piece of work to a background Janus task.

        Returns immediately with a ``delegation_id``; the delegated task runs in the
        background using read-only tools only. Check on it later with
        ``get_delegated_task`` (in an automation you can instead wait for it with
        ``wait_for_delegated_tasks``). Delegated tasks cannot delegate further.
        """
        mutation_db = session_factory()
        try:
            record = create_delegation(
                mutation_db,
                owner_id=user_id,
                parent_task_id=parent_task_id,
                parent_step_id=parent_step_id,
                conversation_id=conversation_id,
                objective=objective,
                requested_tool_ids=tool_ids or (),
                allowed_tool_ids=allowed,
                depth=depth,
                # A child always runs on its parent's model (JAN-233).
                llm_provider=getattr(model_choice, "provider", None),
                llm_model=getattr(model_choice, "model", None),
            )
            payload = serialize_delegation(record)
            record_id = record.id
        finally:
            mutation_db.close()
        # Imported lazily: delegation_runtime reaches runtime_adapter, which
        # imports the registry module that imports this toolbox.
        from janus.delegation_runtime import dispatch_delegation

        dispatch_delegation(session_factory, record_id)
        return payload

    @tool("get_delegated_task")
    def get_delegated_task(delegation_id: str) -> dict[str, Any]:
        """Return the status and, once finished, the result or error of one of your delegated tasks."""
        read_db = session_factory()
        try:
            record = get_owned_delegation(read_db, user_id, delegation_id.strip())
            if record is None:
                raise ValueError(f"no delegated task of yours has the ID: {delegation_id}")
            return serialize_delegation(record)
        finally:
            read_db.close()

    @tool("list_delegated_tasks")
    def list_delegated_tasks(limit: int = 10) -> list[dict[str, Any]]:
        """List the tasks delegated from this conversation or task, newest first."""
        read_db = session_factory()
        try:
            return [
                serialize_delegation(record)
                for record in list_owned_delegations(
                    read_db, user_id, parent_task_id=parent_task_id, limit=max(1, min(limit, 50))
                )
            ]
        finally:
            read_db.close()

    tools: list[BaseTool] = [delegate_task, get_delegated_task, list_delegated_tasks]
    if context.trigger_id is None:
        # Only a trigger run has a persisted plan that can park (JAN-207);
        # chat (and a delegated child, which never gets this toolbox) polls.
        return tools

    @tool("wait_for_delegated_tasks", args_schema=WaitForDelegatedTasksArgs)
    def wait_for_delegated_tasks(delegation_ids: list[str]) -> dict[str, Any]:
        """Pause this automation after your answer until the listed delegated tasks finish.

        Use it when you need a delegated task's result to finish the objective. It
        returns immediately: write your answer so far and stop. Once every listed task
        has finished, this run continues automatically with their results. A run can
        wait only once, and only for tasks it delegated itself.
        """
        # Say first whether this step may wait at all: that is the real reason a
        # continuation, a built-in run, or a later step is refused.
        ensure_waiting_allowed(parent_task_id, parent_step_id or "")
        cleaned = list(dict.fromkeys(item.strip() for item in delegation_ids if isinstance(item, str) and item.strip()))
        if not cleaned:
            raise ValueError("name at least one delegation_id returned by delegate_task")
        read_db = session_factory()
        try:
            for delegation_id in cleaned:
                record = get_owned_delegation(read_db, user_id, delegation_id)
                # Only children of this very step: another task's (or another
                # owner's) delegation reads as missing, not forbidden.
                if record is None or record.parent_task_id != parent_task_id or record.parent_step_id != parent_step_id:
                    raise ValueError(f"no task delegated by this run has the ID: {delegation_id}")
        finally:
            read_db.close()
        waiting_for = request_wait(parent_task_id, parent_step_id or "", cleaned)
        return {
            "waiting_for": waiting_for,
            "note": (
                "Finish your answer now with what you have. This run pauses after your answer and "
                "continues automatically with these tasks' results once they have all finished."
            ),
        }

    tools.append(wait_for_delegated_tasks)
    return tools


__all__ = [
    "DelegateTaskArgs",
    "WaitForDelegatedTasksArgs",
    "build_delegation_tools",
    "delegable_tool_ids",
    "serialize_delegation",
]
