"""Tests for error service."""

import threading
from datetime import timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from janus import models, time_utils
from janus.database import Base
from janus.services.error_service import ErrorService


def test_normalize_message_replaces_dynamic_values(db):
    """Error service normalizes emails, UUIDs, quoted strings, paths, and numbers."""
    service = ErrorService(db)
    normalized = service.normalize_message(
        "Failed for user@example.com at /tmp/janus/run with request 42 and id "
        "550e8400-e29b-41d4-a716-446655440000 using \"debug\" and 'detail'"
    )
    assert normalized == ("Failed for <EMAIL> at <PATH> with request <NUM> and id <UUID> using \"<STR>\" and '<STR>'")


def test_group_error_uses_flush_without_committing(db, monkeypatch):
    """Grouping a single error should not commit the surrounding transaction."""
    service = ErrorService(db)
    commit_called = False

    def fail_commit():
        nonlocal commit_called
        commit_called = True
        raise AssertionError("group_error should not commit")

    monkeypatch.setattr(db, "commit", fail_commit)

    group = service.group_error("EmailProcessor", "Failed for user@example.com", {"attempt": 1})

    assert group.id is not None
    assert group.occurrence_count == 1
    assert commit_called is False


def test_group_error_preserves_existing_sample_details_when_new_details_missing(db):
    """Grouping follow-up events without details should keep the last captured sample details."""
    service = ErrorService(db)

    first = service.group_error("PlannerSync", "Failed job 42", {"job_id": 42})
    db.commit()

    second = service.group_error("PlannerSync", "Failed job 99", {})
    db.commit()

    assert second.id == first.id
    assert second.occurrence_count == 2
    assert second.sample_details == '{"job_id": 42}'


def test_group_error_refreshes_updated_group_and_preserves_event_timestamps(db):
    """Updated groups should reload incremented counts and derive first/last seen from event time."""
    service = ErrorService(db)
    now = time_utils.utcnow()

    first = service.group_error(
        "PlannerSync",
        "Failed job 42",
        {"job_id": 42},
        event_timestamp=now,
    )
    db.commit()

    second = service.group_error(
        "PlannerSync",
        "Failed job 99",
        {"job_id": 99},
        event_timestamp=now - timedelta(hours=2),
    )
    db.commit()

    assert second.id == first.id
    assert second.occurrence_count == 2
    assert second.first_seen == (now - timedelta(hours=2)).replace(tzinfo=None)
    assert second.last_seen == now.replace(tzinfo=None)
    assert second.sample_details == '{"job_id": 99}'


def test_compact_errors_processes_batches_and_uses_retention_days(db):
    """Compaction should process batches once and delete grouped raw errors."""
    now = time_utils.utcnow()
    db.add_all(
        [
            models.Event(
                timestamp=now - timedelta(hours=index),
                event_type=models.EventType.ERROR,
                source="PlannerSync",
                message=f"Job failed for request {index} with id 550e8400-e29b-41d4-a716-446655440000",
                details='{"request_id": %d}' % index,  # noqa: UP031
            )
            for index in range(1, 6)
        ]
        + [
            models.Event(
                timestamp=now - timedelta(days=29),
                event_type=models.EventType.ERROR,
                source="PlannerSync",
                message="Old failure 9000",
            ),
            models.Event(
                timestamp=now - timedelta(days=29),
                event_type=models.EventType.INFO,
                source="PlannerSync",
                message="Old info event",
            ),
        ]
    )
    db.commit()

    service = ErrorService(db)
    result = service.compact_errors(retention_days=28, batch_size=2)

    assert result == {
        "events_processed": 5,
        "groups_updated": 1,
        "events_deleted": 6,
        "groups_deleted": 0,
    }

    groups = db.query(models.ErrorGroup).all()
    assert len(groups) == 1
    assert groups[0].occurrence_count == 5
    assert groups[0].message_template == "Job failed for request <NUM> with id <UUID>"
    assert groups[0].first_seen == (now - timedelta(hours=5)).replace(tzinfo=None)
    assert groups[0].last_seen == (now - timedelta(hours=1)).replace(tzinfo=None)
    assert groups[0].last_compacted_at is not None

    remaining_events = db.query(models.Event).order_by(models.Event.timestamp.asc()).all()
    assert [event.message for event in remaining_events] == ["Old info event"]


def test_compact_errors_commits_each_batch(db, monkeypatch):
    """Compaction should commit each processed batch instead of one giant transaction."""
    now = time_utils.utcnow()
    db.add_all(
        [
            models.Event(
                timestamp=now - timedelta(minutes=index),
                event_type=models.EventType.ERROR,
                source="PlannerSync",
                message=f"Batch failure {index}",
            )
            for index in range(1, 6)
        ]
    )
    db.commit()

    service = ErrorService(db)
    original_commit = db.commit
    commit_count = 0

    def counting_commit():
        nonlocal commit_count
        commit_count += 1
        return original_commit()

    monkeypatch.setattr(db, "commit", counting_commit)

    result = service.compact_errors(retention_days=28, batch_size=2)

    assert result["events_processed"] == 5
    assert commit_count == 4


def test_compact_errors_is_idempotent_for_already_grouped_events(db):
    """Compaction should not double count events that were already grouped and removed."""
    now = time_utils.utcnow()
    db.add_all(
        [
            models.Event(
                timestamp=now - timedelta(minutes=index),
                event_type=models.EventType.ERROR,
                source="PlannerSync",
                message=f"Job failed for request {index}",
                details='{"request_id": %d}' % index,  # noqa: UP031
            )
            for index in range(1, 4)
        ]
    )
    db.commit()

    service = ErrorService(db)
    first_result = service.compact_errors(retention_days=28, batch_size=2)
    second_result = service.compact_errors(retention_days=28, batch_size=2)

    group = db.query(models.ErrorGroup).one()
    assert first_result["events_processed"] == 3
    assert second_result == {
        "events_processed": 0,
        "groups_updated": 0,
        "events_deleted": 0,
        "groups_deleted": 0,
    }
    assert group.occurrence_count == 3


def test_compact_errors_handles_legacy_naive_error_group_timestamps(db):
    """Compaction should tolerate legacy naive timestamps already stored in SQLite."""
    now = time_utils.utcnow()
    service = ErrorService(db)
    message_template = service.normalize_message("Legacy failure 42")
    db.add(
        models.ErrorGroup(
            fingerprint=service.compute_fingerprint("ErrorCompaction", message_template),
            source="ErrorCompaction",
            message_template=message_template,
            first_seen=(now - timedelta(days=2)).replace(tzinfo=None),
            last_seen=(now - timedelta(days=2)).replace(tzinfo=None),
            occurrence_count=1,
            created_at=(now - timedelta(days=2)).replace(tzinfo=None),
        )
    )
    db.add(
        models.Event(
            timestamp=(now - timedelta(hours=1)).replace(tzinfo=None),
            event_type=models.EventType.ERROR,
            source="ErrorCompaction",
            message="Legacy failure 42",
        )
    )
    db.commit()

    result = service.compact_errors(retention_days=28, batch_size=10)

    assert result["events_processed"] == 1
    fingerprint = service.compute_fingerprint("ErrorCompaction", message_template)
    group = db.query(models.ErrorGroup).filter(models.ErrorGroup.fingerprint == fingerprint).one()
    assert group.occurrence_count == 2
    assert group.last_compacted_at is not None


def test_compact_errors_normalizes_none_rowcounts_to_zero(db, monkeypatch):
    """Compaction result counters should stay integer-valued when a driver omits rowcount."""
    service = ErrorService(db)
    original_execute = db.execute

    class _DeleteResult:
        rowcount = None

    def execute_with_missing_group_rowcount(statement, *args, **kwargs):
        result = original_execute(statement, *args, **kwargs)
        if getattr(statement, "table", None) is models.ErrorGroup.__table__ and statement.is_delete:
            return _DeleteResult()
        return result

    monkeypatch.setattr(db, "execute", execute_with_missing_group_rowcount)

    result = service.compact_errors(retention_days=28, batch_size=10)

    assert result == {
        "events_processed": 0,
        "groups_updated": 0,
        "events_deleted": 0,
        "groups_deleted": 0,
    }
    assert isinstance(result["groups_deleted"], int)


def test_compact_errors_skips_overlapping_run(tmp_path, isolate_runtime_paths):
    """A second compaction attempt should bail out while another run holds the lock."""
    database_path = tmp_path / "error-service.db"
    engine = create_engine(
        f"sqlite:///{database_path}",
        connect_args={"check_same_thread": False},
    )
    session_local = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    seed_session = session_local()
    now = time_utils.utcnow()
    seed_session.add_all(
        [
            models.Event(
                timestamp=now - timedelta(minutes=index),
                event_type=models.EventType.ERROR,
                source="PlannerSync",
                message=f"Concurrent failure {index}",
            )
            for index in range(1, 3)
        ]
    )
    seed_session.commit()
    seed_session.close()

    first_session = session_local()
    second_session = session_local()
    first_service = ErrorService(first_session)
    second_service = ErrorService(second_session)

    original_group_error = ErrorService.group_error
    entered_first_run = threading.Event()
    allow_first_run_to_finish = threading.Event()
    first_run_result: dict[str, int] = {}

    def blocking_group_error(self, source, message, details=None, event_timestamp=None):
        if self is first_service and not entered_first_run.is_set():
            entered_first_run.set()
            allow_first_run_to_finish.wait(timeout=5)
        return original_group_error(self, source, message, details, event_timestamp)

    try:
        ErrorService.group_error = blocking_group_error

        def run_first_compaction():
            nonlocal first_run_result
            first_run_result = first_service.compact_errors(retention_days=28, batch_size=1)

        worker = threading.Thread(target=run_first_compaction)
        worker.start()
        assert entered_first_run.wait(timeout=5)

        overlapping_result = second_service.compact_errors(retention_days=28, batch_size=1)
        allow_first_run_to_finish.set()
        worker.join(timeout=5)
    finally:
        ErrorService.group_error = original_group_error
        first_session.close()
        second_session.close()
        engine.dispose()

    assert first_run_result["events_processed"] == 2
    assert overlapping_result == {
        "events_processed": 0,
        "groups_updated": 0,
        "events_deleted": 0,
        "groups_deleted": 0,
    }
