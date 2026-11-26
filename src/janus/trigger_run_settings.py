"""How a trigger definition's runs execute: the model choice and the run limits.

Model choice (JAN-233): an optional ``llm_provider`` / ``llm_model`` override;
blank means the default model.

Run limits: ``max_tool_calls`` bounds one run's tool-calling rounds and ``max_run_seconds``
its wall-clock time. ``None`` keeps Janus's default
(``agent_task_runner.AGENT_TASK_MAX_ITERATIONS`` / ``AGENT_TASK_MAX_SECONDS``),
``0`` switches that limit off for the definition, and anything else must be a
positive integer up to its ceiling. ``unattended_execution.run_limits_for``
turns the stored values into the bounds a run uses.
"""

from __future__ import annotations

from typing import Any

from janus import models

#: The definition fields holding run limits, in the order they are validated.
RUN_LIMIT_FIELDS = ("max_tool_calls", "max_run_seconds")

#: Upper bounds for an explicit limit (0 = no limit is always accepted).
MAX_TOOL_CALLS_CEILING = 500
MAX_RUN_SECONDS_CEILING = 24 * 60 * 60

_CEILINGS = {"max_tool_calls": MAX_TOOL_CALLS_CEILING, "max_run_seconds": MAX_RUN_SECONDS_CEILING}


def validate_model_choice(llm_provider: str | None, llm_model: str | None) -> dict[str, str | None]:
    """Validate an optional provider/model override (JAN-233); blank means the default."""
    from janus.trigger_definition_service import TriggerDefinitionError  # lazy: that module imports this one

    provider = (llm_provider or "").strip().lower() or None
    model = (llm_model or "").strip() or None
    if provider is not None and provider not in {item.value for item in models.LLMProvider}:
        raise TriggerDefinitionError(
            f"llm_provider must be one of: {', '.join(item.value for item in models.LLMProvider)}"
        )
    return {"llm_provider": provider, "llm_model": model}


def validate_run_limits(max_tool_calls: Any = None, max_run_seconds: Any = None) -> dict[str, int | None]:
    """Validate a definition's run limits and return their column values.

    Raises ``TriggerDefinitionError`` (a ``422`` at the API) for a value that is
    not ``None`` or an integer from 0 to its ceiling.
    """
    from janus.trigger_definition_service import TriggerDefinitionError  # lazy: that module imports this one

    values: dict[str, int | None] = {}
    for key, value in (("max_tool_calls", max_tool_calls), ("max_run_seconds", max_run_seconds)):
        ceiling = _CEILINGS[key]
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= ceiling):
            raise TriggerDefinitionError(f"{key} must be an integer from 0 (no limit) to {ceiling}")
        values[key] = value
    return values


__all__ = [
    "MAX_RUN_SECONDS_CEILING",
    "MAX_TOOL_CALLS_CEILING",
    "RUN_LIMIT_FIELDS",
    "validate_model_choice",
    "validate_run_limits",
]
