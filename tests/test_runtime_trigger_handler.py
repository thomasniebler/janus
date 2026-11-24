"""Tests for the durable-trigger to plan-runtime adapter."""

from unittest.mock import MagicMock

import pytest

from janus.agent_runtime import PlanRunResult
from janus.plan_graph import PlanGraphNode, TaskContext
from janus.runtime_trigger_handler import PlanRuntimeTriggerHandler, task_context_from_trigger
from janus.triggers import TriggerEnvelope, TriggerKind


def _trigger(payload: dict) -> TriggerEnvelope:
    """Build a valid trigger for handler tests."""
    return TriggerEnvelope(
        trigger_id="trigger-1",
        idempotency_key="key-1",
        task_id="task-1",
        kind=TriggerKind.EVENT,
        source="test",
        payload=payload,
    )


def test_task_context_from_trigger_accepts_only_runtime_context_fields():
    """The adapter maps task identity and bounded context without accepting tool policy."""
    context = task_context_from_trigger(
        _trigger({"objective": "Review the inbox", "task_mode": "event", "max_steps": 4, "tool_ids": ["send_email"]})
    )

    assert context == TaskContext(task_id="task-1", objective="Review the inbox", task_mode="event", max_steps=4)
    assert not hasattr(context, "tool_ids")


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({}, "objective"),
        ({"objective": " "}, "objective"),
        ({"objective": "Do work", "task_mode": " "}, "task_mode"),
        ({"objective": "Do work", "max_steps": True}, "max_steps"),
        ({"objective": "Do work", "max_steps": "4"}, "max_steps"),
    ],
)
def test_task_context_from_trigger_rejects_invalid_payload(payload, message):
    """Malformed or unbounded trigger context fails closed before runtime execution."""
    with pytest.raises(ValueError, match=message):
        task_context_from_trigger(_trigger(payload))


def test_handler_delegates_context_to_injected_plan_runtime():
    """The handler does not select tools or graph nodes; it delegates to the runtime."""
    runtime = MagicMock()
    expected = PlanRunResult(state=MagicMock(node=PlanGraphNode.COMPLETE))
    runtime.run.return_value = expected
    handler = PlanRuntimeTriggerHandler(runtime)

    result = handler(_trigger({"objective": "Run the planned task"}))

    assert result is expected
    runtime.run.assert_called_once_with(
        TaskContext(task_id="task-1", objective="Run the planned task", task_mode="default", max_steps=100)
    )
