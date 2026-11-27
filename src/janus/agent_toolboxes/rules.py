"""Classification-rule tools for Janus agents.

These tools are deliberate thin wrappers over the same ``EmailService``
methods the human-facing ``/api/emails/rules`` endpoints call, so an
agent-authored rule and a UI-authored rule stay byte-for-byte equivalent. Pattern validation reuses
``processor.validate_regex`` for the same reason.
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import BaseTool, tool

from janus import models, processor
from janus.agent_toolboxes.shared import ToolContext, parse_priority, run_isolated_tool_mutation
from janus.services.email_service import EmailService

#: The email fields ``EmailProcessor._evaluate_rules`` can match a rule against.
RULE_FIELDS = ("sender", "subject", "body")


def _parse_rule_field(value: str) -> str:
    """Normalize and validate the email field a rule matches against."""
    cleaned = value.strip().lower()
    if cleaned not in RULE_FIELDS:
        raise ValueError(f"field must be one of: {', '.join(RULE_FIELDS)}")
    return cleaned


def _validate_pattern(pattern: str) -> str:
    """Validate a rule pattern exactly like ``POST /api/emails/rules`` does."""
    cleaned = pattern.strip()
    if not cleaned:
        raise ValueError("pattern must not be blank")
    if not processor.validate_regex(cleaned):
        raise ValueError("Invalid regex pattern")
    return cleaned


def _serialize_rule(rule: models.Rule) -> dict[str, Any]:
    """Return a tool-friendly payload for a classification rule."""
    return {
        "id": rule.id,
        "name": rule.name,
        "pattern": rule.pattern,
        "field": rule.field,
        "priority": rule.priority.value if rule.priority else None,
        "is_active": rule.is_active,
        "order": rule.order,
    }


def build_rule_tools(context: ToolContext) -> list[BaseTool]:
    """Build the rule CRUD tools."""
    service = EmailService(context.db)
    user_id = context.user.id

    def _isolated_user(isolated: EmailService) -> models.User:
        """Re-resolve the authenticated user inside a mutation's own session."""
        isolated_user = isolated.db.get(models.User, user_id)
        if isolated_user is None:
            raise ValueError("authenticated user was not found")
        return isolated_user

    @tool("list_mail_rules")
    def list_mail_rules(include_inactive: bool = True, limit: int = 50) -> list[dict[str, Any]]:
        """List the current user's email classification rules in evaluation order."""
        if limit <= 0:
            return []
        rules = service.get_rules(user_id=user_id)
        if not include_inactive:
            rules = [rule for rule in rules if rule.is_active]
        return [_serialize_rule(rule) for rule in rules[:limit]]

    @tool("create_mail_rule")
    def create_mail_rule(
        name: str,
        pattern: str,
        field: str = "sender",
        priority: str = "important",
        is_active: bool = True,
    ) -> dict[str, Any]:
        """Create a durable regex classification rule for the current user.

        The pattern is validated with the same ReDoS-safe check the REST API
        uses. New rules are appended after the existing ones, so they never
        silently preempt rules the user already reviewed.
        """
        cleaned_name = name.strip()
        if not cleaned_name:
            raise ValueError("name must not be blank")
        rule_data = {
            "name": cleaned_name,
            "pattern": _validate_pattern(pattern),
            "field": _parse_rule_field(field),
            "priority": parse_priority(priority),
            "is_active": is_active,
        }
        return run_isolated_tool_mutation(
            context,
            EmailService,
            lambda isolated: _serialize_rule(
                isolated.add_rule(
                    {**rule_data, "order": isolated._next_rule_order(_isolated_user(isolated))}, user_id=user_id
                )
            ),
        )

    @tool("update_mail_rule")
    def update_mail_rule(rule_id: int, is_active: bool | None = None, order: int | None = None) -> dict[str, Any]:
        """Enable, disable, or reorder one of the current user's rules."""
        if is_active is None and order is None:
            raise ValueError("provide is_active or order to update a rule")

        def apply(isolated: EmailService) -> dict[str, Any]:
            """Mirror PATCH /api/emails/rules/{id} inside an isolated session."""
            rule = isolated.update_rule_for_user(rule_id, user_id, order=order, is_active=is_active)
            if rule is None:
                raise ValueError(f"rule {rule_id} not found")
            return _serialize_rule(rule)

        return run_isolated_tool_mutation(context, EmailService, apply)

    @tool("delete_mail_rule")
    def delete_mail_rule(rule_id: int) -> dict[str, Any]:
        """Delete one of the current user's classification rules."""

        def apply(isolated: EmailService) -> dict[str, Any]:
            """Mirror DELETE /api/emails/rules/{id} inside an isolated session."""
            if not isolated.delete_rule_for_user(rule_id, user_id):
                raise ValueError(f"rule {rule_id} not found")
            return {"status": "success", "rule_id": rule_id}

        return run_isolated_tool_mutation(context, EmailService, apply)

    return [
        list_mail_rules,
        create_mail_rule,
        update_mail_rule,
        delete_mail_rule,
    ]
