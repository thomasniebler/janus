"""Persistence and extraction helpers for structured tracked topics."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from janus import llm_service, models, reminder_service, time_utils
from janus.services import knowledge_index, topic_text

_CONFIDENCE_RANK = {
    models.ConfidenceLevel.LOW: 1,
    models.ConfidenceLevel.MEDIUM: 2,
    models.ConfidenceLevel.HIGH: 3,
}
TOPIC_UPDATE_UNSET = object()


class TopicService:
    """Manage tracked topics, evidence, and topic preferences."""

    def __init__(self, db: Session):
        """Initialize the service with an existing database session."""
        self.db = db
        self._llm: llm_service.LLMService | None = None

    def _get_llm(self) -> llm_service.LLMService:
        if self._llm is None:
            self._llm = llm_service.LLMService(db=self.db)
        return self._llm

    @staticmethod
    def _parse_confidence(value: str | models.ConfidenceLevel | None) -> models.ConfidenceLevel | None:
        if isinstance(value, models.ConfidenceLevel):
            return value
        if not isinstance(value, str):
            return None
        cleaned = value.strip().lower()
        for confidence in models.ConfidenceLevel:
            if cleaned in {confidence.value.lower(), confidence.name.lower()}:
                return confidence
        return None

    @staticmethod
    def _parse_topic_status(value: str | models.TopicStatus | None) -> models.TopicStatus | None:
        if isinstance(value, models.TopicStatus):
            return value
        if not isinstance(value, str):
            return None
        cleaned = value.strip().lower()
        for status in models.TopicStatus:
            if cleaned in {status.value.lower(), status.name.lower()}:
                return status
        return None

    @staticmethod
    def _parse_extraction_scope(
        value: str | models.TopicExtractionScope | None,
    ) -> models.TopicExtractionScope | None:
        if isinstance(value, models.TopicExtractionScope):
            return value
        if not isinstance(value, str):
            return None
        cleaned = value.strip().lower()
        for scope in models.TopicExtractionScope:
            if cleaned in {scope.value.lower(), scope.name.lower()}:
                return scope
        return None

    def _ensure_settings(self) -> models.Settings:
        settings = self.db.query(models.Settings).first()
        if settings is None:
            settings = models.Settings()
            self.db.add(settings)
            self.db.commit()
            self.db.refresh(settings)
        return settings

    def get_default_user(self) -> models.User | None:
        """Return the default user used by background email processing."""
        return self.db.query(models.User).order_by(models.User.id.asc()).first()

    def _resolve_user_id(self, user_id: int | None) -> int | None:
        if user_id is not None:
            return user_id
        user = self.get_default_user()
        return user.id if user is not None else None

    def get_or_create_preference(self, user_id: int) -> models.TopicTrackingPreference:
        """Return persisted topic preferences for the user, creating defaults when needed."""
        preference = (
            self.db.query(models.TopicTrackingPreference)
            .filter(models.TopicTrackingPreference.user_id == user_id)
            .first()
        )
        if preference is not None:
            return preference

        settings = self._ensure_settings()
        preference = models.TopicTrackingPreference(
            user_id=user_id,
            enabled=settings.topic_tracking_default_enabled,
            extraction_scope=settings.topic_tracking_default_scope,
            minimum_confidence=settings.topic_tracking_default_min_confidence,
            min_confirmation_mentions=settings.topic_tracking_default_min_confirmation_mentions,
            max_active_topics=settings.topic_tracking_max_active_topics,
        )
        self.db.add(preference)
        self.db.commit()
        self.db.refresh(preference)
        return preference

    def _refresh_expired_snoozes(self, user_id: int | None = None) -> None:
        """Unsnooze topics whose snooze window has elapsed."""
        now = time_utils.utcnow()
        query = self.db.query(models.TrackedTopic).filter(
            models.TrackedTopic.status == models.TopicStatus.SNOOZED,
            models.TrackedTopic.snoozed_until.is_not(None),
            models.TrackedTopic.snoozed_until <= now,
        )
        if user_id is not None:
            query = query.filter(models.TrackedTopic.user_id == user_id)
        topics = query.all()
        if not topics:
            return
        for topic in topics:
            topic.status = models.TopicStatus.ACTIVE
            topic.snoozed_until = None
        self.db.commit()

    @staticmethod
    def _priority_in_scope(priority: models.Priority, scope: models.TopicExtractionScope) -> bool:
        if scope == models.TopicExtractionScope.IMPORTANT_ONLY:
            return priority == models.Priority.IMPORTANT
        if scope == models.TopicExtractionScope.IMPORTANT_AND_DIGEST:
            return priority in {models.Priority.IMPORTANT, models.Priority.DIGEST}
        return priority not in {models.Priority.SPAM, models.Priority.IGNORE}

    @staticmethod
    def _confidence_meets_threshold(
        confidence: models.ConfidenceLevel | None,
        threshold: models.ConfidenceLevel,
    ) -> bool:
        return _CONFIDENCE_RANK.get(confidence or models.ConfidenceLevel.LOW, 0) >= _CONFIDENCE_RANK[threshold]

    def _serialize_topic(self, topic: models.TrackedTopic, evidence_count: int = 0) -> dict[str, Any]:
        return {
            "id": topic.id,
            "title": topic.title,
            "summary": topic.summary,
            "marker_keywords": topic_text.load_marker_keywords(topic.marker_keywords),
            "status": topic.status.value,
            "pinned": topic.pinned,
            "user_confirmed": topic.user_confirmed,
            "origin": topic.origin.value,
            "confidence": topic.confidence.value if topic.confidence else None,
            "first_seen_at": topic.first_seen_at,
            "last_seen_at": topic.last_seen_at,
            "snoozed_until": topic.snoozed_until,
            "last_briefed_at": topic.last_briefed_at,
            "last_digested_at": topic.last_digested_at,
            "created_at": topic.created_at,
            "updated_at": topic.updated_at,
            "evidence_count": evidence_count,
        }

    def serialize_topic_summary(self, topic: models.TrackedTopic) -> dict[str, Any]:
        """Return one topic in the list/summary response shape."""
        evidence_count = (
            self.db.query(func.count(models.TopicEvidence.id))
            .filter(models.TopicEvidence.topic_id == topic.id)
            .scalar()
        )
        return self._serialize_topic(topic, evidence_count=evidence_count or 0)

    def list_topics(
        self,
        user_id: int,
        *,
        query: str | None = None,
        status: models.TopicStatus | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Return tracked topics for one user."""
        self._refresh_expired_snoozes(user_id)
        filters = [models.TrackedTopic.user_id == user_id, *([models.TrackedTopic.status == status] if status else [])]
        stmt = (
            self.db.query(models.TrackedTopic, func.count(models.TopicEvidence.id).label("evidence_count"))
            .outerjoin(models.TopicEvidence, models.TopicEvidence.topic_id == models.TrackedTopic.id)
            .filter(*filters)
        )
        search_text = topic_text.clean_text(query)
        candidates = select(models.TrackedTopic.id).where(*filters)  # filter first, then rank (JAN-245)
        ranked_ids = knowledge_index.search_ids(self.db, user_id, knowledge_index.TOPIC, search_text, candidates)
        if ranked_ids is not None:
            rows = stmt.filter(models.TrackedTopic.id.in_(ranked_ids)).group_by(models.TrackedTopic.id).all()
            rows = knowledge_index.order_by_rank(rows, ranked_ids, key=lambda row: row[0].id)[:limit]
            return [self._serialize_topic(topic, evidence_count=count) for topic, count in rows]

        rows = (
            stmt.group_by(models.TrackedTopic.id)
            .order_by(
                models.TrackedTopic.pinned.desc(),
                models.TrackedTopic.last_seen_at.desc(),
                models.TrackedTopic.created_at.desc(),
            )
            .limit(limit)
            .all()
        )
        return [self._serialize_topic(topic, evidence_count=evidence_count) for topic, evidence_count in rows]

    def build_active_topic_summary(self, user_id: int | None = None, *, limit: int = 5) -> list[dict[str, Any]]:
        """Return active topic summaries suitable for dashboard and planner contexts."""
        resolved_user_id = self._resolve_user_id(user_id)
        if resolved_user_id is None:
            return []
        return self.list_topics(resolved_user_id, status=models.TopicStatus.ACTIVE, limit=limit)

    def build_context_topic_summary(self, user_id: int | None = None) -> list[dict[str, Any]]:
        """Return active topics for briefing/chat contexts, preferring not-yet-briefed threads."""
        resolved_user_id = self._resolve_user_id(user_id)
        if resolved_user_id is None:
            return []

        settings = self._ensure_settings()
        if not settings.topic_tracking_available:
            return []

        preference = self.get_or_create_preference(resolved_user_id)
        if not preference.enabled or not preference.chat_context_enabled:
            return []

        self._refresh_expired_snoozes(resolved_user_id)
        limit = max(1, min(preference.max_active_topics, settings.topic_tracking_max_context_topics))
        rows = (
            self.db.query(models.TrackedTopic, func.count(models.TopicEvidence.id).label("evidence_count"))
            .outerjoin(models.TopicEvidence, models.TopicEvidence.topic_id == models.TrackedTopic.id)
            .filter(
                models.TrackedTopic.user_id == resolved_user_id,
                models.TrackedTopic.status == models.TopicStatus.ACTIVE,
            )
            .group_by(models.TrackedTopic.id)
            .order_by(
                models.TrackedTopic.pinned.desc(),
                func.coalesce(models.TrackedTopic.last_briefed_at, models.TrackedTopic.first_seen_at).asc(),
                models.TrackedTopic.last_seen_at.desc(),
                models.TrackedTopic.created_at.desc(),
            )
            .limit(limit)
            .all()
        )
        return [self._serialize_topic(topic, evidence_count=evidence_count or 0) for topic, evidence_count in rows]

    def is_digest_grouping_enabled(self, user_id: int | None = None) -> bool:
        """Return whether topic-aware digest grouping is enabled for the user."""
        resolved_user_id = self._resolve_user_id(user_id)
        if resolved_user_id is None:
            return False

        settings = self._ensure_settings()
        if not settings.topic_tracking_available:
            return False

        preference = self.get_or_create_preference(resolved_user_id)
        return preference.enabled and preference.digest_grouping_enabled

    def mark_topics_briefed(
        self,
        *,
        topic_ids: list[int],
        user_id: int | None = None,
        when: datetime | None = None,
    ) -> None:
        """Record that topics were surfaced in a briefing."""
        resolved_user_id = self._resolve_user_id(user_id)
        if resolved_user_id is None or not topic_ids:
            return

        timestamp = when or time_utils.utcnow()
        (
            self.db.query(models.TrackedTopic)
            .filter(
                models.TrackedTopic.user_id == resolved_user_id,
                models.TrackedTopic.id.in_(sorted(set(topic_ids))),
            )
            .update({models.TrackedTopic.last_briefed_at: timestamp}, synchronize_session=False)
        )
        self.db.commit()

    def mark_topics_digested(
        self,
        *,
        topic_ids: list[int],
        user_id: int | None = None,
        when: datetime | None = None,
    ) -> None:
        """Record that topics were surfaced in a digest."""
        resolved_user_id = self._resolve_user_id(user_id)
        if resolved_user_id is None or not topic_ids:
            return

        timestamp = when or time_utils.utcnow()
        (
            self.db.query(models.TrackedTopic)
            .filter(
                models.TrackedTopic.user_id == resolved_user_id,
                models.TrackedTopic.id.in_(sorted(set(topic_ids))),
            )
            .update({models.TrackedTopic.last_digested_at: timestamp}, synchronize_session=False)
        )
        self.db.commit()

    def list_topic_badges_for_source_ids(
        self,
        *,
        user_id: int,
        source_type: models.TopicEvidenceSource,
        source_ids: list[int],
    ) -> dict[int, list[dict[str, Any]]]:
        """Return compact topic badge payloads keyed by evidence source ID."""
        if not source_ids:
            return {}

        rows = (
            self.db.query(models.TopicEvidence.source_id, models.TrackedTopic)
            .join(models.TrackedTopic, models.TrackedTopic.id == models.TopicEvidence.topic_id)
            .filter(
                models.TrackedTopic.user_id == user_id,
                models.TopicEvidence.source_type == source_type,
                models.TopicEvidence.source_id.in_(source_ids),
            )
            .order_by(
                models.TopicEvidence.source_id.asc(),
                models.TrackedTopic.pinned.desc(),
                models.TrackedTopic.last_seen_at.desc(),
                models.TrackedTopic.created_at.desc(),
                models.TrackedTopic.id.desc(),
            )
            .all()
        )

        badges_by_source: dict[int, list[dict[str, Any]]] = {}
        seen_topic_ids_by_source: dict[int, set[int]] = {}
        for source_id, topic in rows:
            if source_id is None:
                continue
            source_seen_ids = seen_topic_ids_by_source.setdefault(source_id, set())
            if topic.id in source_seen_ids:
                continue
            source_seen_ids.add(topic.id)
            badges_by_source.setdefault(source_id, []).append(
                {
                    "id": topic.id,
                    "title": topic.title,
                    "status": topic.status,
                    "pinned": topic.pinned,
                }
            )

        return badges_by_source

    def get_topic_detail(self, user_id: int, topic_id: int) -> dict[str, Any]:
        """Return one topic plus its evidence rows."""
        topic = (
            self.db.query(models.TrackedTopic)
            .filter(models.TrackedTopic.id == topic_id, models.TrackedTopic.user_id == user_id)
            .first()
        )
        if topic is None:
            raise ValueError("topic not found")
        evidence_rows = (
            self.db.query(models.TopicEvidence)
            .filter(models.TopicEvidence.topic_id == topic.id)
            .order_by(models.TopicEvidence.created_at.desc(), models.TopicEvidence.id.desc())
            .all()
        )
        payload = self._serialize_topic(topic, evidence_count=len(evidence_rows))
        payload["evidence"] = [
            {
                "id": row.id,
                "source_type": row.source_type.value,
                "source_id": row.source_id,
                "excerpt": row.excerpt,
                "evidence_role": row.evidence_role.value,
                "created_at": row.created_at,
            }
            for row in evidence_rows
        ]
        return payload

    def create_topic(
        self,
        *,
        user_id: int,
        title: str,
        summary: str | None = None,
        marker_keywords: list[str] | None = None,
        pinned: bool | None = None,
    ) -> models.TrackedTopic:
        """Create or confirm one manual topic for the user."""
        cleaned_title = topic_text.clean_text(title)
        if cleaned_title is None:
            raise ValueError("title must not be blank")

        slug = topic_text.slugify(cleaned_title)
        if slug is None:
            raise ValueError("title must contain at least one alphanumeric character")

        cleaned_summary = topic_text.clean_text(summary)
        normalized_keywords = topic_text.normalize_marker_keywords(marker_keywords)
        if marker_keywords is not None:
            if cleaned_summary is None:
                raise ValueError("manual topics require a description")
            if not normalized_keywords:
                raise ValueError("manual topics require at least one marker keyword")

        now = time_utils.utcnow()
        if marker_keywords is not None:
            existing = (
                self.db.query(models.TrackedTopic)
                .filter(models.TrackedTopic.user_id == user_id, models.TrackedTopic.slug == slug)
                .first()
            )
            if existing is not None:
                raise ValueError("another topic already uses that title")

            topic = models.TrackedTopic(
                user_id=user_id,
                slug=slug,
                title=cleaned_title,
                summary=cleaned_summary,
                marker_keywords=topic_text.dump_marker_keywords(normalized_keywords),
                status=models.TopicStatus.ACTIVE,
                pinned=bool(pinned),
                user_confirmed=True,
                origin=models.TopicOrigin.MANUAL,
                first_seen_at=now,
                last_seen_at=now,
            )
            self.db.add(topic)
        else:
            topic = (
                self.db.query(models.TrackedTopic)
                .filter(
                    models.TrackedTopic.user_id == user_id,
                    models.TrackedTopic.slug == slug,
                )
                .first()
            )
            if topic is None:
                topic = models.TrackedTopic(
                    user_id=user_id,
                    slug=slug,
                    title=cleaned_title,
                    summary=cleaned_summary,
                    status=models.TopicStatus.ACTIVE,
                    pinned=bool(pinned),
                    user_confirmed=True,
                    origin=models.TopicOrigin.MANUAL,
                    first_seen_at=now,
                    last_seen_at=now,
                )
                self.db.add(topic)
            else:
                topic.title = cleaned_title
                if cleaned_summary is not None:
                    topic.summary = cleaned_summary
                if pinned is not None:
                    topic.pinned = pinned
                topic.status = models.TopicStatus.ACTIVE
                topic.snoozed_until = None
                topic.user_confirmed = True
                topic.origin = models.TopicOrigin.MANUAL
                topic.last_seen_at = now
                topic.updated_at = now
        self.db.commit()
        knowledge_index.embed_owner(self.db, knowledge_index.TOPIC, topic.id)
        self.db.refresh(topic)
        return topic

    def update_topic(  # noqa: C901
        self,
        *,
        user_id: int,
        topic_id: int,
        title: str | None | object = TOPIC_UPDATE_UNSET,
        summary: str | None | object = TOPIC_UPDATE_UNSET,
        marker_keywords: list[str] | None | object = TOPIC_UPDATE_UNSET,
        status: models.TopicStatus | None | object = TOPIC_UPDATE_UNSET,
        pinned: bool | None | object = TOPIC_UPDATE_UNSET,
        snoozed_until: datetime | None | object = TOPIC_UPDATE_UNSET,
    ) -> models.TrackedTopic:
        """Update a topic owned by one user."""
        topic = (
            self.db.query(models.TrackedTopic)
            .filter(models.TrackedTopic.id == topic_id, models.TrackedTopic.user_id == user_id)
            .first()
        )
        if topic is None:
            raise ValueError("topic not found")

        if all(
            value is TOPIC_UPDATE_UNSET
            for value in (
                title,
                summary,
                marker_keywords,
                status,
                pinned,
                snoozed_until,
            )
        ):
            raise ValueError("provide at least one field to update")

        if title is not TOPIC_UPDATE_UNSET:
            cleaned_title = topic_text.clean_text(title)
            if cleaned_title is None:
                raise ValueError("title must not be blank")
            new_slug = topic_text.slugify(cleaned_title)
            if new_slug is None:
                raise ValueError("title must contain at least one alphanumeric character")
            existing = (
                self.db.query(models.TrackedTopic)
                .filter(
                    models.TrackedTopic.user_id == user_id,
                    models.TrackedTopic.slug == new_slug,
                    models.TrackedTopic.id != topic_id,
                )
                .first()
            )
            if existing is not None:
                raise ValueError("another topic already uses that title")
            topic.title = cleaned_title
            topic.slug = new_slug
            topic.user_confirmed = True

        if summary is not TOPIC_UPDATE_UNSET:
            cleaned_summary = topic_text.clean_text(summary)
            if topic.origin == models.TopicOrigin.MANUAL and cleaned_summary is None:
                raise ValueError("manual topics require a description")
            topic.summary = cleaned_summary
            topic.user_confirmed = True

        effective_summary = topic.summary if summary is TOPIC_UPDATE_UNSET else cleaned_summary

        if marker_keywords is not TOPIC_UPDATE_UNSET:
            normalized_keywords = topic_text.normalize_marker_keywords(marker_keywords)
            if topic.origin == models.TopicOrigin.MANUAL and not normalized_keywords:
                raise ValueError("manual topics require at least one marker keyword")
            if normalized_keywords and effective_summary is None:
                raise ValueError("manual topics require a description")
            topic.marker_keywords = topic_text.dump_marker_keywords(normalized_keywords)
            if normalized_keywords:
                topic.origin = models.TopicOrigin.MANUAL
            topic.user_confirmed = True

        if pinned is not TOPIC_UPDATE_UNSET:
            if pinned is None:
                raise ValueError("pinned must not be null")
            topic.pinned = pinned
            topic.user_confirmed = True

        if status is not TOPIC_UPDATE_UNSET:
            if status is None:
                raise ValueError("status must not be null")
            topic.status = status
            topic.user_confirmed = True
            if status != models.TopicStatus.SNOOZED:
                topic.snoozed_until = None

        if snoozed_until is not TOPIC_UPDATE_UNSET:
            topic.snoozed_until = snoozed_until
            if status is TOPIC_UPDATE_UNSET:
                topic.status = models.TopicStatus.SNOOZED if snoozed_until else models.TopicStatus.ACTIVE
            topic.user_confirmed = True

        # Curating an unconfirmed candidate is itself confirmation that the topic is worth tracking,
        # so it graduates without waiting for more corroborating mail.
        if status is TOPIC_UPDATE_UNSET and topic.status == models.TopicStatus.CANDIDATE:
            topic.status = models.TopicStatus.ACTIVE

        topic.updated_at = time_utils.utcnow()
        self.db.commit()
        knowledge_index.embed_owner(self.db, knowledge_index.TOPIC, topic.id)
        self.db.refresh(topic)
        return topic

    def delete_topic(self, *, user_id: int, topic_id: int) -> bool:
        """Delete a topic owned by one user, along with its evidence."""
        topic = (
            self.db.query(models.TrackedTopic)
            .filter(models.TrackedTopic.id == topic_id, models.TrackedTopic.user_id == user_id)
            .first()
        )
        if topic is None:
            return False

        self.db.query(models.TopicEvidence).filter(models.TopicEvidence.topic_id == topic_id).delete()
        self.db.query(models.TopicSuggestion).filter(models.TopicSuggestion.topic_id == topic_id).update(
            {models.TopicSuggestion.topic_id: None}
        )
        self.db.query(models.PlannerSuggestion).filter(models.PlannerSuggestion.source_topic_id == topic_id).update(
            {models.PlannerSuggestion.source_topic_id: None}
        )
        self.db.delete(topic)
        self.db.commit()
        return True

    def _marker_keywords_match_email(self, topic: models.TrackedTopic, email_record: models.EmailRecord) -> bool:
        """Return whether a manual topic's marker keywords match one processed email."""
        marker_keywords = topic_text.load_marker_keywords(topic.marker_keywords)
        if not marker_keywords:
            return False
        searchable_text = topic_text.build_email_search_text(email_record)
        return any(keyword.lower() in searchable_text for keyword in marker_keywords)

    def _attach_email_topic_evidence(
        self,
        *,
        topic: models.TrackedTopic,
        email_record: models.EmailRecord,
    ) -> None:
        """Attach one email as topic evidence when not already linked."""
        existing_evidence = (
            self.db.query(models.TopicEvidence)
            .filter(
                models.TopicEvidence.topic_id == topic.id,
                models.TopicEvidence.source_type == models.TopicEvidenceSource.EMAIL,
                models.TopicEvidence.source_id == email_record.id,
            )
            .first()
        )
        if existing_evidence is not None:
            return

        self.db.add(
            models.TopicEvidence(
                topic_id=topic.id,
                source_type=models.TopicEvidenceSource.EMAIL,
                source_id=email_record.id,
                excerpt=topic_text.clean_text(email_record.snippet or email_record.body),
                evidence_role=models.TopicEvidenceRole.TRIGGER,
            )
        )

    def _refresh_topic_from_email(
        self,
        *,
        topic: models.TrackedTopic,
        email_record: models.EmailRecord,
        when: datetime,
    ) -> bool:
        """Refresh one tracked topic from an email hit and attach supporting evidence."""
        if topic.status == models.TopicStatus.IGNORED:
            return False

        if not (topic.status == models.TopicStatus.SNOOZED and topic.snoozed_until and topic.snoozed_until > when):
            topic.last_seen_at = when
        self._attach_email_topic_evidence(topic=topic, email_record=email_record)
        return True

    def _distinct_email_evidence_count(self, topic: models.TrackedTopic) -> int:
        """Return how many distinct emails have been recorded as evidence for one topic."""
        count = (
            self.db.query(func.count(func.distinct(models.TopicEvidence.source_id)))
            .filter(
                models.TopicEvidence.topic_id == topic.id,
                models.TopicEvidence.source_type == models.TopicEvidenceSource.EMAIL,
                models.TopicEvidence.source_id.is_not(None),
            )
            .scalar()
        )
        return int(count or 0)

    def _promote_candidate_if_corroborated(
        self,
        *,
        topic: models.TrackedTopic,
        preference: models.TopicTrackingPreference,
        status_hint: models.TopicStatus | None,
    ) -> None:
        """Promote a corroborated candidate topic once enough distinct emails mention it."""
        if topic.status != models.TopicStatus.CANDIDATE:
            return

        required_mentions = max(1, preference.min_confirmation_mentions)
        if self._distinct_email_evidence_count(topic) < required_mentions:
            return

        # A "candidate" hint would leave a corroborated topic stuck, so promotion falls back to active.
        if status_hint is None or status_hint == models.TopicStatus.CANDIDATE:
            topic.status = models.TopicStatus.ACTIVE
        else:
            topic.status = status_hint

    def _match_manual_topics(
        self,
        *,
        user_id: int,
        email_record: models.EmailRecord,
        when: datetime,
    ) -> list[models.TrackedTopic]:
        """Refresh manual topics whose marker keywords match the incoming email."""
        matching_topics: list[models.TrackedTopic] = []
        manual_topics = (
            self.db.query(models.TrackedTopic)
            .filter(
                models.TrackedTopic.user_id == user_id,
                models.TrackedTopic.origin == models.TopicOrigin.MANUAL,
                models.TrackedTopic.marker_keywords.is_not(None),
            )
            .all()
        )
        for topic in manual_topics:
            if not self._marker_keywords_match_email(topic, email_record):
                continue
            if self._refresh_topic_from_email(topic=topic, email_record=email_record, when=when):
                matching_topics.append(topic)
        return matching_topics

    def _fetch_context_topics(
        self, user_id: int, status: models.TopicStatus, *, limit: int
    ) -> list[models.TrackedTopic]:
        """Return up to ``limit`` topics of one status, most relevant first."""
        if limit <= 0:
            return []
        return (
            self.db.query(models.TrackedTopic)
            .filter(
                models.TrackedTopic.user_id == user_id,
                models.TrackedTopic.status == status,
            )
            .order_by(
                models.TrackedTopic.pinned.desc(),
                models.TrackedTopic.last_seen_at.desc(),
                models.TrackedTopic.created_at.desc(),
            )
            .limit(limit)
            .all()
        )

    def _build_extraction_context_topics(self, user_id: int, *, limit: int) -> dict[str, list[dict[str, Any]]]:
        """Return compact active and watched topics, with the dedupe hints extraction should reuse."""
        self._refresh_expired_snoozes(user_id)
        # Reserve half the budget for candidates so pinned/long-running active
        # topics can never crowd every watched dedupe hint out of context --
        # losing a candidate's hint here fragments its corroborating mail into
        # a new slug and blocks promotion.
        candidate_rows = self._fetch_context_topics(user_id, models.TopicStatus.CANDIDATE, limit=max(limit // 2, 1))
        active_rows = self._fetch_context_topics(
            user_id, models.TopicStatus.ACTIVE, limit=max(limit - len(candidate_rows), 0)
        )

        def _to_dict(topic: models.TrackedTopic) -> dict[str, Any]:
            return {
                "id": topic.id,
                "title": topic.title,
                "summary": topic.summary,
                "dedupe_hint": topic.slug,
            }

        return {
            "active": [_to_dict(topic) for topic in active_rows],
            "candidate": [_to_dict(topic) for topic in candidate_rows],
        }

    def capture_email_topics(
        self,
        email_record: models.EmailRecord,
        *,
        user_id: int | None = None,
        planner_context: dict[str, Any] | None = None,
    ) -> list[models.TrackedTopic]:
        """Extract and upsert tracked topics for one processed email."""
        if email_record.priority in {models.Priority.SPAM, models.Priority.IGNORE}:
            return []

        resolved_user_id = self._resolve_user_id(user_id)
        if resolved_user_id is None:
            return []

        settings = self._ensure_settings()
        if not settings.topic_tracking_available:
            return []

        preference = self.get_or_create_preference(resolved_user_id)
        if not preference.enabled:
            return []
        if not self._priority_in_scope(email_record.priority, preference.extraction_scope):
            return []

        now = email_record.received_at or time_utils.utcnow()
        created_or_updated_by_id: dict[int, models.TrackedTopic] = {}
        for topic in self._match_manual_topics(
            user_id=resolved_user_id,
            email_record=email_record,
            when=now,
        ):
            created_or_updated_by_id[topic.id] = topic

        reminder_context = (
            reminder_service.ReminderService(self.db).build_email_context(resolved_user_id, email_record.received_at)
            if preference.reminder_linking_enabled
            else {}
        )
        known_topics = self._build_extraction_context_topics(
            resolved_user_id,
            limit=min(preference.max_active_topics, settings.topic_tracking_max_context_topics),
        )
        candidates = self._get_llm().extract_email_topics(
            {
                "account_id": email_record.account_id,
                "message_id": email_record.message_id,
                "sender": email_record.sender,
                "subject": email_record.subject,
                "snippet": email_record.snippet,
                "body": email_record.body,
                "received_at": email_record.received_at,
                "priority": email_record.priority.value,
            },
            {
                "active_topics": known_topics["active"],
                "watched_topics": known_topics["candidate"],
                "planner_context": planner_context or {},
                "reminder_context": reminder_context,
            },
        )
        if not candidates:
            self.db.flush()
            return list(created_or_updated_by_id.values())

        for topic in self._apply_topic_candidates(
            user_id=resolved_user_id, email_record=email_record, candidates=candidates, preference=preference, now=now
        ):
            created_or_updated_by_id[topic.id] = topic

        self.db.flush()
        return list(created_or_updated_by_id.values())

    def _apply_topic_candidates(
        self,
        *,
        user_id: int,
        email_record: models.EmailRecord,
        candidates: list[Any],
        preference: models.TopicTrackingPreference,
        now: Any,
    ) -> list[models.TrackedTopic]:
        """Upsert extracted/agent-supplied topic candidates and attach this email as evidence."""
        resolved_user_id = user_id
        created_or_updated_by_id: dict[int, models.TrackedTopic] = {}
        for candidate in candidates:
            title = topic_text.clean_text(candidate.get("title") if isinstance(candidate, dict) else None)
            if title is None:
                continue
            confidence = self._parse_confidence(candidate.get("confidence") if isinstance(candidate, dict) else None)
            if not self._confidence_meets_threshold(confidence, preference.minimum_confidence):
                continue
            dedupe_hint = candidate.get("dedupe_hint") if isinstance(candidate, dict) else None
            slug = topic_text.slugify(dedupe_hint) or topic_text.slugify(title)
            if slug is None:
                continue
            summary = topic_text.clean_text(candidate.get("summary") if isinstance(candidate, dict) else None)
            status_hint_raw = candidate.get("status_hint") if isinstance(candidate, dict) else None
            status_hint = self._parse_topic_status(status_hint_raw)
            topic = (
                self.db.query(models.TrackedTopic)
                .filter(
                    models.TrackedTopic.user_id == resolved_user_id,
                    models.TrackedTopic.slug == slug,
                )
                .first()
            )
            if topic is None:
                # One email is weak evidence, so a newly discovered topic waits for corroboration
                # from further mail before it is promoted and surfaced downstream.
                topic = models.TrackedTopic(
                    user_id=resolved_user_id,
                    slug=slug,
                    title=title,
                    summary=summary,
                    status=models.TopicStatus.CANDIDATE,
                    origin=models.TopicOrigin.AUTO,
                    confidence=confidence,
                    first_seen_at=now,
                    last_seen_at=now,
                )
                self.db.add(topic)
                self.db.flush()
            else:
                if topic.status == models.TopicStatus.IGNORED:
                    continue
                if not topic.user_confirmed:
                    topic.title = title
                    if summary is not None:
                        topic.summary = summary
                    # A candidate's status is decided below, once this email's evidence is attached.
                    if topic.status != models.TopicStatus.CANDIDATE:
                        if status_hint is not None:
                            topic.status = status_hint
                        elif topic.status == models.TopicStatus.RESOLVED:
                            topic.status = models.TopicStatus.ACTIVE
                elif topic.summary is None and summary is not None:
                    topic.summary = summary
                topic.confidence = confidence or topic.confidence

            refreshed = self._refresh_topic_from_email(topic=topic, email_record=email_record, when=now)
            if not topic.user_confirmed:
                self.db.flush()
                self._promote_candidate_if_corroborated(
                    topic=topic,
                    preference=preference,
                    status_hint=status_hint,
                )
            if refreshed:
                created_or_updated_by_id[topic.id] = topic

        return list(created_or_updated_by_id.values())

    def record_email_topic(
        self,
        email_record: models.EmailRecord,
        *,
        user_id: int,
        title: str,
        summary: str | None = None,
        status_hint: str | None = None,
        confidence: str | None = None,
        dedupe_hint: str | None = None,
    ) -> list[models.TrackedTopic]:
        """Link one email to a tracked topic chosen by Janus during mail processing (JAN-232).

        Applies the same candidate/corroboration rules and preference gates as
        automatic extraction; returns an empty list when topic tracking is off
        or the confidence is below the user's threshold.
        """
        settings = self._ensure_settings()
        if not settings.topic_tracking_available:
            return []
        preference = self.get_or_create_preference(user_id)
        if not preference.enabled:
            return []
        topics = self._apply_topic_candidates(
            user_id=user_id,
            email_record=email_record,
            candidates=[
                {
                    "title": title,
                    "summary": summary,
                    "status_hint": status_hint,
                    "confidence": confidence,
                    "dedupe_hint": dedupe_hint,
                }
            ],
            preference=preference,
            now=email_record.received_at or time_utils.utcnow(),
        )
        self.db.flush()
        return topics
