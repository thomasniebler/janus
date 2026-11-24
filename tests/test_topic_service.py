"""Tests for TopicService extraction-context topic selection."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from janus import database, models
from janus.services.topic_service import TopicService


def test_extraction_context_reserves_slots_for_candidate_topics():
    """Pinned active topics must not crowd every candidate dedupe hint out of context."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    database.Base.metadata.create_all(bind=engine)
    db = sessionmaker(autocommit=False, autoflush=False, bind=engine)()
    try:
        user = models.User(username="owner", hashed_password="hash", is_admin=True)
        db.add_all([user, models.Settings()])
        db.commit()
        db.refresh(user)

        # More pinned active topics than the context limit, so without a
        # reserved candidate budget they would fill every slot.
        for i in range(5):
            db.add(
                models.TrackedTopic(
                    user_id=user.id,
                    slug=f"active-{i}",
                    title=f"Active {i}",
                    status=models.TopicStatus.ACTIVE,
                    origin=models.TopicOrigin.AUTO,
                    pinned=True,
                )
            )
        candidate = models.TrackedTopic(
            user_id=user.id,
            slug="candidate-topic",
            title="Candidate topic",
            status=models.TopicStatus.CANDIDATE,
            origin=models.TopicOrigin.AUTO,
        )
        db.add(candidate)
        db.commit()

        context = TopicService(db)._build_extraction_context_topics(user.id, limit=5)

        assert [topic["title"] for topic in context["candidate"]] == ["Candidate topic"]
        assert len(context["active"]) == 4
    finally:
        db.close()
