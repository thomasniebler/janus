"""Persistence and compaction helpers for agent memory facts."""

import threading
from collections.abc import Iterable

from sqlalchemy import bindparam, select, text
from sqlalchemy.orm import Session

from janus import database, event_logger, llm_service, models, time_utils
from janus.services import knowledge_index


class MemoryFactNotFoundError(ValueError):
    """Raised when a memory fact does not exist or belongs to another user."""


def _escape_like(value: str) -> str:
    """Escape ``LIKE`` wildcards so ``value`` only matches itself (case-insensitively under ``ILIKE``)."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class MemoryService:
    """Manage durable agent memory facts and compaction runs."""

    def __init__(self, db: Session):
        """Initializes the service with an existing database session."""
        self.db = db

    @staticmethod
    def _clean_text(value: str | None) -> str | None:
        """Collapse whitespace and normalize empty strings to None."""
        if value is None:
            return None
        cleaned = " ".join(value.split())
        return cleaned or None

    def list_facts(
        self,
        user_id: int,
        *,
        query: str | None = None,
        include_archived: bool = False,
        source: models.AgentMemorySource | None = None,
        limit: int = 100,
        touch_accessed: bool = True,
    ) -> list[models.AgentMemoryFact]:
        """Return recent memory facts for a user, optionally filtered by origin and search text.

        The metadata filters (owner, status, ``source``) bound the search
        candidates before any similarity ranking runs (JAN-245).
        """
        filters = [models.AgentMemoryFact.user_id == user_id]
        if not include_archived:
            filters.append(models.AgentMemoryFact.status == models.AgentMemoryStatus.ACTIVE)
        if source is not None:
            filters.append(models.AgentMemoryFact.source == source)
        stmt = self.db.query(models.AgentMemoryFact).filter(*filters)

        search_text = self._clean_text(query)
        candidates = select(models.AgentMemoryFact.id).where(*filters)
        ranked_ids = knowledge_index.search_ids(self.db, user_id, knowledge_index.FACT, search_text, candidates)
        if ranked_ids is not None:
            results = self._rank_facts(stmt, ranked_ids, limit)
        else:
            results = (
                stmt.order_by(
                    models.AgentMemoryFact.status.asc(),
                    models.AgentMemoryFact.updated_at.desc(),
                    models.AgentMemoryFact.created_at.desc(),
                )
                .limit(limit)
                .all()
            )

        if results and touch_accessed:
            now = time_utils.utcnow()
            fact_ids = [fact.id for fact in results]
            # Use raw SQL so SQLAlchemy's onupdate hook for updated_at is NOT
            # triggered — only last_accessed_at is written to the database.
            # bindparam with expanding=True generates a safe parameterized
            # IN clause at query time.
            # SQLite stores datetimes without timezone info.
            naive_now = now.replace(tzinfo=None)
            self.db.execute(
                text("UPDATE agent_memory_facts SET last_accessed_at = :ts WHERE id IN :ids").bindparams(
                    bindparam("ids", expanding=True)
                ),
                {"ts": naive_now, "ids": fact_ids},
            )
            self.db.commit()
            # Expire the in-memory objects so subsequent reads reflect the
            # updated last_accessed_at without triggering a second ORM flush.
            for fact in results:
                self.db.expire(fact)

        return results

    @staticmethod
    def _rank_facts(stmt, ranked_ids: list[int], limit: int) -> list[models.AgentMemoryFact]:
        """Filters ``stmt`` to the search hits: active facts first, then by relevance."""
        candidates = stmt.filter(models.AgentMemoryFact.id.in_(ranked_ids)).all() if ranked_ids else []
        ranked = knowledge_index.order_by_rank(candidates, ranked_ids, key=lambda fact: fact.id)
        # Stable sort: active facts first (as in the unfiltered listing), each group by relevance.
        return sorted(ranked, key=lambda fact: fact.status != models.AgentMemoryStatus.ACTIVE)[:limit]

    def get_recent_facts(self, user_id: int, *, limit: int = 10) -> list[models.AgentMemoryFact]:
        """Return the most recent active facts for a user."""
        return (
            self.db.query(models.AgentMemoryFact)
            .filter(
                models.AgentMemoryFact.user_id == user_id,
                models.AgentMemoryFact.status == models.AgentMemoryStatus.ACTIVE,
            )
            .order_by(models.AgentMemoryFact.updated_at.desc(), models.AgentMemoryFact.created_at.desc())
            .limit(limit)
            .all()
        )

    def create_fact(
        self,
        *,
        user_id: int,
        summary: str,
        detail: str | None = None,
        source: models.AgentMemorySource = models.AgentMemorySource.USER,
        conversation_id: int | None = None,
    ) -> models.AgentMemoryFact:
        """Create or refresh a durable memory fact."""
        cleaned_summary = self._clean_text(summary)
        if cleaned_summary is None:
            raise ValueError("Memory facts require a summary.")

        cleaned_detail = self._clean_text(detail)
        existing = (
            self.db.query(models.AgentMemoryFact)
            .filter(
                models.AgentMemoryFact.user_id == user_id,
                models.AgentMemoryFact.status == models.AgentMemoryStatus.ACTIVE,
                # Same summary, ignoring case. Escaped: a `%` or `_` in the summary is text, not a
                # wildcard, so "50% spent" never refreshes "50 percent spent" (JAN-224).
                models.AgentMemoryFact.summary.ilike(_escape_like(cleaned_summary), escape="\\"),
            )
            .order_by(models.AgentMemoryFact.updated_at.desc())
            .first()
        )
        now = time_utils.utcnow()

        if existing is not None:
            existing.detail = cleaned_detail or existing.detail
            existing.source = source
            existing.source_conversation_id = conversation_id or existing.source_conversation_id
            existing.updated_at = now
            existing.last_accessed_at = now
            self.db.commit()
            knowledge_index.embed_owner(self.db, knowledge_index.FACT, existing.id)
            self.db.refresh(existing)
            return existing

        fact = models.AgentMemoryFact(
            user_id=user_id,
            summary=cleaned_summary,
            detail=cleaned_detail,
            source=source,
            status=models.AgentMemoryStatus.ACTIVE,
            source_conversation_id=conversation_id,
            last_accessed_at=now,
        )
        self.db.add(fact)
        self.db.commit()
        knowledge_index.embed_owner(self.db, knowledge_index.FACT, fact.id)
        self.db.refresh(fact)
        return fact

    def update_fact(
        self,
        *,
        user_id: int,
        fact_id: int,
        summary: str | None = None,
        detail: str | None = None,
    ) -> models.AgentMemoryFact:
        """Update a user's fact summary/detail."""
        fact = (
            self.db.query(models.AgentMemoryFact)
            .filter(
                models.AgentMemoryFact.id == fact_id,
                models.AgentMemoryFact.user_id == user_id,
            )
            .first()
        )
        if fact is None:
            raise MemoryFactNotFoundError("memory fact not found")

        if summary is None and detail is None:
            raise ValueError("provide at least one field to update")

        if summary is not None:
            cleaned_summary = self._clean_text(summary)
            if cleaned_summary is None:
                raise ValueError("summary must not be blank")
            fact.summary = cleaned_summary

        if detail is not None:
            fact.detail = self._clean_text(detail)

        fact.updated_at = time_utils.utcnow()
        self.db.commit()
        knowledge_index.embed_owner(self.db, knowledge_index.FACT, fact.id)
        self.db.refresh(fact)
        return fact

    def delete_fact(self, *, user_id: int, fact_id: int) -> bool:
        """Delete a user's memory fact."""
        fact = (
            self.db.query(models.AgentMemoryFact)
            .filter(
                models.AgentMemoryFact.id == fact_id,
                models.AgentMemoryFact.user_id == user_id,
            )
            .first()
        )
        if fact is None:
            return False

        self.db.query(models.AgentMemoryFact).filter(models.AgentMemoryFact.compacted_into_id == fact_id).update(
            {models.AgentMemoryFact.compacted_into_id: None}
        )
        self.db.delete(fact)
        self.db.commit()
        return True

    def group_facts(
        self,
        *,
        user_id: int,
        fact_ids: list[int],
        summary: str,
        detail: str | None = None,
        source: models.AgentMemorySource = models.AgentMemorySource.AGENT,
        conversation_id: int | None = None,
    ) -> tuple[models.AgentMemoryFact, list[int]]:
        """Create one merged fact from several active facts and archive the originals."""
        unique_ids = list(dict.fromkeys(fact_ids))
        if len(unique_ids) < 2:
            raise ValueError("group_facts requires at least two distinct fact ids")

        cleaned_summary = self._clean_text(summary)
        if cleaned_summary is None:
            raise ValueError("summary must not be blank")
        cleaned_detail = self._clean_text(detail)

        source_facts = (
            self.db.query(models.AgentMemoryFact)
            .filter(
                models.AgentMemoryFact.user_id == user_id,
                models.AgentMemoryFact.id.in_(unique_ids),
                models.AgentMemoryFact.status == models.AgentMemoryStatus.ACTIVE,
            )
            .order_by(models.AgentMemoryFact.updated_at.desc(), models.AgentMemoryFact.created_at.desc())
            .all()
        )
        facts_by_id = {fact.id: fact for fact in source_facts}
        missing_ids = [fact_id for fact_id in unique_ids if fact_id not in facts_by_id]
        if missing_ids:
            raise ValueError(f"active memory facts not found: {missing_ids}")

        now = time_utils.utcnow()
        merged_fact = models.AgentMemoryFact(
            user_id=user_id,
            summary=cleaned_summary,
            detail=cleaned_detail,
            source=source,
            status=models.AgentMemoryStatus.ACTIVE,
            source_conversation_id=conversation_id,
            last_accessed_at=now,
        )
        self.db.add(merged_fact)
        self.db.flush()

        for fact in source_facts:
            fact.status = models.AgentMemoryStatus.ARCHIVED
            fact.compacted_into_id = merged_fact.id
            fact.updated_at = now

        self.db.commit()
        knowledge_index.embed_owner(self.db, knowledge_index.FACT, merged_fact.id)
        self.db.refresh(merged_fact)
        return merged_fact, unique_ids

    def get_latest_compaction_run(self, user_id: int) -> models.AgentMemoryCompactionRun | None:
        """Return the most recent compaction run for the given user."""
        return (
            self.db.query(models.AgentMemoryCompactionRun)
            .filter(models.AgentMemoryCompactionRun.user_id == user_id)
            .order_by(models.AgentMemoryCompactionRun.created_at.desc())
            .first()
        )

    def start_compaction(self, user_id: int) -> tuple[models.AgentMemoryCompactionRun, bool]:
        """Start a background compaction run unless one is already active."""
        existing = (
            self.db.query(models.AgentMemoryCompactionRun)
            .filter(
                models.AgentMemoryCompactionRun.user_id == user_id,
                models.AgentMemoryCompactionRun.status.in_(
                    [
                        models.AgentMemoryCompactionStatus.QUEUED,
                        models.AgentMemoryCompactionStatus.RUNNING,
                    ]
                ),
            )
            .order_by(models.AgentMemoryCompactionRun.created_at.desc())
            .first()
        )
        if existing is not None:
            return existing, False

        run = models.AgentMemoryCompactionRun(user_id=user_id)
        self.db.add(run)
        self.db.commit()
        self.db.refresh(run)

        worker = threading.Thread(
            target=self._run_compaction,
            args=(run.id,),
            daemon=True,
            name=f"agent-memory-compact-{run.id}",
        )
        worker.start()
        return run, True

    @classmethod
    def _run_compaction(cls, run_id: int) -> None:
        """Execute a compaction run in a detached background thread."""
        db = database.SessionLocal()
        try:
            run = db.get(models.AgentMemoryCompactionRun, run_id)
            if run is None:
                return

            run.status = models.AgentMemoryCompactionStatus.RUNNING
            run.started_at = time_utils.utcnow()
            db.commit()

            facts = (
                db.query(models.AgentMemoryFact)
                .filter(
                    models.AgentMemoryFact.user_id == run.user_id,
                    models.AgentMemoryFact.status == models.AgentMemoryStatus.ACTIVE,
                )
                .order_by(models.AgentMemoryFact.updated_at.desc(), models.AgentMemoryFact.created_at.desc())
                .limit(100)
                .all()
            )
            run.facts_considered = len(facts)
            db.commit()

            if len(facts) < 2:
                run.status = models.AgentMemoryCompactionStatus.COMPLETED
                run.result_summary = "Not enough active facts to compact yet."
                run.completed_at = time_utils.utcnow()
                db.commit()
                return

            utility = llm_service.build_llm_service(
                db, llm_service.model_choice_from_settings(db, "utility"), purpose="utility"
            )
            suggestions = utility.suggest_memory_compactions(facts)
            facts_by_id = {fact.id: fact for fact in facts}
            used_fact_ids: set[int] = set()
            facts_created = 0
            facts_archived = 0

            for suggestion in suggestions:
                raw_source_ids = suggestion.get("source_ids", [])
                if not isinstance(raw_source_ids, Iterable) or isinstance(raw_source_ids, (str, bytes)):
                    continue
                source_ids = []
                for sid in raw_source_ids:
                    try:
                        int_id = int(sid)
                        if int_id in facts_by_id and int_id not in used_fact_ids:
                            source_ids.append(int_id)
                    except (ValueError, TypeError):
                        continue
                summary = cls._clean_text(suggestion.get("summary") if isinstance(suggestion, dict) else None)
                detail = cls._clean_text(suggestion.get("detail") if isinstance(suggestion, dict) else None)
                if summary is None or len(source_ids) < 2:
                    continue

                merged_fact = models.AgentMemoryFact(
                    user_id=run.user_id,
                    summary=summary,
                    detail=detail,
                    source=models.AgentMemorySource.COMPACTION,
                    status=models.AgentMemoryStatus.ACTIVE,
                    last_accessed_at=time_utils.utcnow(),
                )
                db.add(merged_fact)
                db.flush()

                for source_id in source_ids:
                    source_fact = facts_by_id[source_id]
                    source_fact.status = models.AgentMemoryStatus.ARCHIVED
                    source_fact.compacted_into_id = merged_fact.id
                    source_fact.updated_at = time_utils.utcnow()
                    used_fact_ids.add(source_id)
                    facts_archived += 1

                facts_created += 1

            run.facts_created = facts_created
            run.facts_archived = facts_archived
            run.status = models.AgentMemoryCompactionStatus.COMPLETED
            if facts_created:
                run.result_summary = f"Compacted {facts_archived} facts into {facts_created} merged memories."
            else:
                run.result_summary = "No overlapping facts were compacted."
            run.completed_at = time_utils.utcnow()
            db.commit()
            event_logger.log(
                models.EventType.SUCCESS,
                "AgentMemory",
                run.result_summary,
                {
                    "run_id": run.id,
                    "facts_considered": run.facts_considered,
                    "facts_archived": facts_archived,
                    "facts_created": facts_created,
                },
                user_id=run.user_id,
            )
        except Exception as exc:
            db.rollback()
            run = db.get(models.AgentMemoryCompactionRun, run_id)
            if run is not None:
                run.status = models.AgentMemoryCompactionStatus.FAILED
                run.error_message = str(exc)
                run.completed_at = time_utils.utcnow()
                db.commit()
            event_logger.log(
                models.EventType.ERROR,
                "AgentMemory",
                f"Memory compaction failed: {exc}",
                user_id=run.user_id if run is not None else None,
            )
        finally:
            db.close()
