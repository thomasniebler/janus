"""Memory-focused Janus agent tools."""

from __future__ import annotations

from typing import Any

from langchain_core.tools import BaseTool, tool

from janus import models
from janus.agent_toolboxes.shared import ToolContext, iso, run_isolated_tool_mutation
from janus.services.memory_service import MemoryService


def build_memory_tools(context: ToolContext) -> list[BaseTool]:
    """Build tools for listing and managing durable memory facts."""
    service = MemoryService(context.db)
    user_id = context.user.id

    def serialize_fact(row: models.AgentMemoryFact) -> dict[str, Any]:
        return {
            "id": row.id,
            "summary": row.summary,
            "detail": row.detail,
            "status": row.status.value,
            "source": row.source.value,
            "compacted_into_id": row.compacted_into_id,
            "created_at": iso(row.created_at),
            "updated_at": iso(row.updated_at),
            "last_accessed_at": iso(row.last_accessed_at),
            # How far the memory review checked the fact against its source conversation, and the exact
            # passage that supports it; treat an unverified or unsupported fact as a lead, not as settled.
            "verification": row.verification,
            "evidence_quote": row.evidence_quote,
            "in_memory_index": row.in_memory_index,
        }

    def returned(rows: list[models.AgentMemoryFact]) -> list[dict[str, Any]]:
        """Serialize the rows, then count the read in a separate session (the run's own session never commits)."""
        payload = [serialize_fact(row) for row in rows]
        run_isolated_tool_mutation(context, MemoryService, lambda counter: counter.record_access([r.id for r in rows]))
        return payload

    @tool("list_memory_facts")
    def list_memory_facts() -> list[dict[str, Any]]:
        """Returns the current user's active memory facts, most recently updated first."""
        return returned(service.list_facts(user_id, touch_accessed=False))

    @tool("search_facts")
    def search_facts(
        query: str, include_archived: bool = False, source: str | None = None, limit: int = 25
    ) -> list[dict[str, Any]]:
        """Searches the current user's memory facts by meaning and keywords.

        ``source`` narrows the search to facts from one origin before ranking:
        ``user`` (the user stated it), ``agent`` (Janus inferred it), or
        ``compaction`` (merged from other facts).
        """
        rows = service.list_facts(
            user_id,
            query=query,
            include_archived=include_archived,
            source=models.AgentMemorySource(source) if source else None,
            limit=limit,
            touch_accessed=False,
        )
        return returned(rows)

    @tool("add_memory_fact")
    def add_memory_fact(summary: str, detail: str | None = None) -> dict[str, Any]:
        """Remember a durable fact so a future session can rely on it without the user repeating it.

        Use it when the user asks you to remember something, or when something is clearly worth
        remembering; not for what you can look up again, and not for tracked topics. Put the user's
        exact words for anything precise (names, numbers, terms) in ``detail``. The fact starts
        unverified until the memory review checks it against this conversation.
        """
        return run_isolated_tool_mutation(
            context,
            MemoryService,
            lambda mutation_service: serialize_fact(
                mutation_service.create_fact(
                    user_id=user_id,
                    summary=summary,
                    detail=detail,
                    source=models.AgentMemorySource.AGENT,
                    conversation_id=context.conversation_id,
                )
            ),
        )

    @tool("update_fact")
    def update_fact(fact_id: int, summary: str | None = None, detail: str | None = None) -> dict[str, Any]:
        """Updates a memory fact's summary and/or detail; the fact becomes unverified until the next memory review."""
        return run_isolated_tool_mutation(
            context,
            MemoryService,
            lambda mutation_service: serialize_fact(
                mutation_service.update_fact(
                    user_id=user_id,
                    fact_id=fact_id,
                    summary=summary,
                    detail=detail,
                    conversation_id=context.conversation_id,
                )
            ),
        )

    @tool("delete_fact")
    def delete_fact(fact_id: int) -> dict[str, Any]:
        """Deletes one memory fact owned by the current user."""
        return run_isolated_tool_mutation(
            context,
            MemoryService,
            lambda mutation_service: {
                "fact_id": fact_id,
                "deleted": mutation_service.delete_fact(user_id=user_id, fact_id=fact_id),
            },
        )

    @tool("group_facts")
    def group_facts(fact_ids: list[int], summary: str, detail: str | None = None) -> dict[str, Any]:
        """Creates one merged fact from multiple active facts and archives the originals."""
        return run_isolated_tool_mutation(
            context,
            MemoryService,
            lambda mutation_service: _serialize_grouped_facts(
                mutation_service,
                fact_ids=fact_ids,
                summary=summary,
                detail=detail,
            ),
        )

    def _serialize_grouped_facts(
        mutation_service: MemoryService,
        *,
        fact_ids: list[int],
        summary: str,
        detail: str | None = None,
    ) -> dict[str, Any]:
        merged_fact, grouped_fact_ids = mutation_service.group_facts(
            user_id=user_id,
            fact_ids=fact_ids,
            summary=summary,
            detail=detail,
            source=models.AgentMemorySource.AGENT,
            conversation_id=context.conversation_id,
        )
        return {"grouped_fact_ids": grouped_fact_ids, "merged_fact": serialize_fact(merged_fact)}

    return [list_memory_facts, search_facts, add_memory_fact, update_fact, delete_fact, group_facts]
