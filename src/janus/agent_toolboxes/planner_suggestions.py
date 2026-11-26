"""Tools for reviewing planner suggestions in bulk from chat.

Mail processing and the afternoon digest record *pending* planner suggestions
for the user to approve or dismiss. With hundreds of them, clicking through
the review list one by one is impractical, so Janus can list, narrow, and
dismiss them on the user's word:

* ``planner.suggestions`` (read): list pending suggestions by text, type, or
  source email, and report groups of duplicates.
* ``planner.suggestions.review`` (write): dismiss suggestions by id or filter,
  collapse duplicates to their newest copy, and restore dismissed ones.
* ``planner.suggest`` (write): ``propose_planner_item``, a pending suggestion
  that no single email caused, for the Secretary (and any run that lists it).
  It goes through the same dedupe as mail suggestions, so a waiting or recently
  dismissed suggestion is not proposed again.

Every call is owner-scoped. Dismissing never touches planner tasks or events
and is reversible (``restore_planner_suggestions``); a dismissed suggestion
also keeps the same suggestion from being proposed again for a while
(``planner_suggestion_review.REVIEWED_SUGGESTION_MEMORY_DAYS``). Nothing here
approves a suggestion: applying one changes the planner and stays a click in
the dashboard.
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import BaseTool, tool

from janus import models
from janus.agent_toolboxes.shared import ToolContext
from janus.services import planner_suggestion_review as review
from janus.tool_registry import ToolRegistryEntry

PLANNER_SUGGESTIONS_TOOL_ID = "planner.suggestions"
PLANNER_SUGGESTIONS_REVIEW_TOOL_ID = "planner.suggestions.review"
PLANNER_SUGGEST_TOOL_ID = "planner.suggest"
_LIST_LIMIT_CEILING = 200
_COMPACT_FIELDS = (
    "id",
    "suggestion_type",
    "title",
    "due_at",
    "scheduled_for",
    "start_time",
    "dedupe_key",
    "source_email_id",
    "source_email_subject",
    "created_at",
)


def _parse_type(value: str | None) -> models.PlannerSuggestionType | None:
    if value is None or not value.strip():
        return None
    try:
        return models.PlannerSuggestionType(value.strip().lower())
    except ValueError as error:
        raise ValueError("suggestion_type must be task, event, task_update, or event_update") from error


def _compact(row: dict[str, Any]) -> dict[str, Any]:
    return {key: row.get(key) for key in _COMPACT_FIELDS if row.get(key) is not None}


def build_planner_suggestion_read_tools(context: ToolContext) -> list[BaseTool]:
    """Build the read-only planner suggestion tools for the context's user."""
    db = context.db
    user_id = context.user.id

    @tool("list_planner_suggestions")
    def list_planner_suggestions(
        text: str | None = None,
        suggestion_type: str | None = None,
        source_email_id: int | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """List pending planner suggestions, newest first.

        ``text`` matches title, description, and reason (case- and
        punctuation-insensitive); ``suggestion_type`` is task, event,
        task_update, or event_update. ``total`` is how many match, even when
        only ``limit`` (at most 200) are returned.
        """
        rows = review.search_pending_suggestions(
            db, user_id, text=text, suggestion_type=_parse_type(suggestion_type), source_email_id=source_email_id
        )
        bounded = max(0, min(limit, _LIST_LIMIT_CEILING))
        return {"total": len(rows), "suggestions": [_compact(row) for row in rows[:bounded]]}

    @tool("find_duplicate_planner_suggestions")
    def find_duplicate_planner_suggestions() -> dict[str, Any]:
        """Report pending suggestions that duplicate each other (same dedupe key, or same type and title).

        Each group lists its suggestion ids newest first; the first is the one
        ``dismiss_duplicate_planner_suggestions`` would keep.
        """
        groups = review.duplicate_suggestion_groups(db, user_id)
        return {
            "groups": len(groups),
            "duplicates": sum(len(group) - 1 for group in groups),
            "examples": [{"title": group[0].title, "ids": [row.id for row in group]} for group in groups[:20]],
        }

    return [list_planner_suggestions, find_duplicate_planner_suggestions]


def build_planner_suggestion_review_tools(context: ToolContext) -> list[BaseTool]:
    """Build the planner suggestion dismissal tools for the context's user."""
    db = context.db
    user_id = context.user.id

    @tool("dismiss_planner_suggestions")
    def dismiss_planner_suggestions(
        suggestion_ids: list[int] | None = None,
        text: str | None = None,
        suggestion_type: str | None = None,
        source_email_id: int | None = None,
        created_before: str | None = None,
        all_pending: bool = False,
    ) -> dict[str, Any]:
        """Dismiss pending planner suggestions; planner tasks and events are never changed.

        Every given filter must match: ``suggestion_ids``, ``text`` (title,
        description, reason), ``suggestion_type``, ``source_email_id``, and
        ``created_before`` (ISO-8601). With no filter, nothing is dismissed
        unless ``all_pending`` is true, which dismisses every pending
        suggestion -- only use it when the user asked to clear them all.
        Dismissed suggestions can be brought back with restore_planner_suggestions.
        """
        dismissed = review.dismiss_suggestions(
            db,
            user_id,
            suggestion_ids=suggestion_ids,
            text=text,
            suggestion_type=_parse_type(suggestion_type),
            source_email_id=source_email_id,
            created_before=created_before,
            all_pending=all_pending,
        )
        return {"dismissed": len(dismissed), "dismissed_ids": dismissed}

    @tool("dismiss_duplicate_planner_suggestions")
    def dismiss_duplicate_planner_suggestions() -> dict[str, Any]:
        """Keep the newest pending suggestion of every duplicate group and dismiss the other copies."""
        result = review.dismiss_duplicate_suggestions(db, user_id)
        return {"groups": result["groups"], "dismissed": len(result["dismissed_ids"]), **result}

    @tool("restore_planner_suggestions")
    def restore_planner_suggestions(suggestion_ids: list[int]) -> dict[str, Any]:
        """Put dismissed planner suggestions back into the user's review list."""
        restored = review.restore_suggestions(db, user_id, suggestion_ids)
        return {"restored": len(restored), "restored_ids": restored}

    return [dismiss_planner_suggestions, dismiss_duplicate_planner_suggestions, restore_planner_suggestions]


def build_planner_suggest_tools(context: ToolContext) -> list[BaseTool]:
    """Build ``propose_planner_item`` for the context's user."""
    db = context.db
    user_id = context.user.id

    @tool("propose_planner_item")
    def propose_planner_item(
        suggestion_type: str,
        title: str,
        description: str | None = None,
        due_at: str | None = None,
        scheduled_for: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        location: str | None = None,
        all_day: bool = False,
        priority: str | None = None,
        reason: str | None = None,
        confidence: str = "medium",
        dedupe_hint: str | None = None,
        target_task_id: int | None = None,
        target_event_id: int | None = None,
    ) -> dict[str, Any]:
        """Propose a planner task, event, or change for the user to approve or dismiss.

        Use this for something you noticed that no single email asked for. ``suggestion_type``
        is ``task``, ``event``, ``task_update``, or ``event_update`` (updates need
        ``target_task_id`` / ``target_event_id``); timestamps are ISO-8601. This never changes
        the planner. A pending suggestion with the same ``dedupe_hint`` or title is updated
        instead of duplicated; one the user already dismissed or applied is returned unchanged
        (its ``status`` says so) and is not proposed again.
        """
        if not title.strip():
            raise ValueError("title must not be blank")
        item = {
            "suggestion_type": suggestion_type,
            "title": title.strip(),
            "description": description,
            "due_at": due_at,
            "scheduled_for": scheduled_for,
            "start_time": start_time,
            "end_time": end_time,
            "location": location,
            "all_day": all_day,
            "priority": priority,
            "reason": reason,
            "confidence": confidence,
            "dedupe_hint": dedupe_hint,
            "target_task_id": target_task_id,
            "target_event_id": target_event_id,
        }
        row = review.record_suggestion(db, user_id, item, source=models.PlannerItemSource.JANUS_AGENT)
        if row is None:
            raise ValueError("suggestion_type must be task, event, task_update, or event_update")
        return _compact(row) | {"status": row["status"]}

    return [propose_planner_item]


PLANNER_SUGGESTIONS_ENTRY = ToolRegistryEntry(
    id=PLANNER_SUGGESTIONS_TOOL_ID,
    display_name="Read planner suggestions",
    description="List pending planner suggestions and find duplicates among them.",
    required_capabilities=("planner.local",),
    operation="read",
    risk_level="low",
    confirmation="never",
    docs_ref="tools.md#planner-suggestions",
    builder=lambda context: build_planner_suggestion_read_tools(context.tool_context),
    # Titles and reasons are derived from incoming mail, so treat them like mail.
    reads_untrusted_content=True,
)

PLANNER_SUGGESTIONS_REVIEW_ENTRY = ToolRegistryEntry(
    id=PLANNER_SUGGESTIONS_REVIEW_TOOL_ID,
    display_name="Dismiss planner suggestions",
    description="Dismiss pending planner suggestions in bulk, collapse duplicates, and restore dismissed ones.",
    required_capabilities=("planner.local",),
    operation="write",
    risk_level="low",
    confirmation="recommended",
    docs_ref="tools.md#planner-suggestions",
    builder=lambda context: build_planner_suggestion_review_tools(context.tool_context),
)

PLANNER_SUGGEST_ENTRY = ToolRegistryEntry(
    id=PLANNER_SUGGEST_TOOL_ID,
    display_name="Suggest planner items",
    description="Propose a planner task, event, or change for the user to approve; never changes the planner.",
    required_capabilities=("planner.local",),
    operation="write",
    risk_level="low",
    confirmation="recommended",
    docs_ref="tools.md#planner-suggestions",
    builder=lambda context: build_planner_suggest_tools(context.tool_context),
)

__all__ = [
    "PLANNER_SUGGESTIONS_ENTRY",
    "PLANNER_SUGGEST_ENTRY",
    "PLANNER_SUGGEST_TOOL_ID",
    "PLANNER_SUGGESTIONS_REVIEW_ENTRY",
    "PLANNER_SUGGESTIONS_REVIEW_TOOL_ID",
    "PLANNER_SUGGESTIONS_TOOL_ID",
]
