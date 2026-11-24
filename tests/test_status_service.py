"""Tests for status aggregation helpers."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from janus import database, llm_service, models, planner_service, status_service


def _make_test_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    testing_session_local = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    database.Base.metadata.create_all(bind=engine)
    return testing_session_local


def test_run_llm_probe_persists_latest_result(monkeypatch):
    """Stores the latest successful LLM probe on the settings row."""
    testing_session_local = _make_test_session()
    db = testing_session_local()
    db.add(models.Settings(llm_provider=models.LLMProvider.OLLAMA))
    db.commit()
    db.close()

    monkeypatch.setattr(database, "SessionLocal", testing_session_local)

    class FakeLLMService:
        def __init__(self, db=None):
            assert db is not None

        def test_connection(self):
            return True, "Successfully connected to LLM provider."

    monkeypatch.setattr(llm_service, "LLMService", FakeLLMService)

    status_service.run_llm_probe()

    db = testing_session_local()
    settings = db.query(models.Settings).first()
    assert settings is not None
    assert settings.llm_status == status_service.STATUS_OPERATIONAL
    assert settings.llm_status_message == "Successfully connected to LLM provider."
    assert settings.llm_status_checked_at is not None
    db.close()


def test_log_status_transition_skips_initial_and_unknown_states(monkeypatch):
    """Initial and unknown transitions should not create incidents."""
    calls = []
    monkeypatch.setattr(status_service.event_logger, "log", lambda *args, **kwargs: calls.append((args, kwargs)))

    status_service.log_status_transition(
        service_id="llm",
        service_name="LLM",
        component_id="llm-active-backend",
        component_name="LLM backend",
        previous_status=None,
        new_status=status_service.STATUS_OPERATIONAL,
        message="Healthy",
    )
    status_service.log_status_transition(
        service_id="llm",
        service_name="LLM",
        component_id="llm-active-backend",
        component_name="LLM backend",
        previous_status=status_service.STATUS_DEGRADED,
        new_status=status_service.STATUS_UNKNOWN,
        message="No data",
    )

    assert calls == []


def test_get_status_overview_tolerates_malformed_incident_details():
    """Malformed incident JSON should not break the summary payload."""
    testing_session_local = _make_test_session()
    db = testing_session_local()
    db.add(
        models.Event(
            event_type=models.EventType.ERROR,
            source="SystemStatus",
            message="Planner Sync degraded for Work: bad details",
            details="{not-json",
        )
    )
    db.commit()

    overview = status_service.get_status_overview(db)

    assert overview["recent_incidents"][0]["service_name"] == "Unknown service"
    assert overview["recent_incidents"][0]["status"] == status_service.STATUS_DEGRADED
    db.close()


def test_planner_sync_logs_degraded_then_recovery(monkeypatch):
    """Planner incidents should match the same degraded/operational rules as the summary."""
    testing_session_local = _make_test_session()
    db = testing_session_local()
    recent_sync = datetime.now(UTC) - timedelta(minutes=5)
    owner = models.User(username="owner", hashed_password="hash", is_admin=True)
    db.add(owner)
    db.flush()
    account = models.PlannerAccount(
        user_id=owner.id,
        name="Work",
        provider=models.Provider.GOOGLE_CALENDAR,
        token="token",
        is_active=True,
        last_sync_at=recent_sync,
    )
    db.add(account)
    db.commit()
    db.refresh(account)

    transitions = []

    def capture_transition(**kwargs):
        transitions.append(kwargs)

    class FailingConnector:
        def fetch_tasks(self):
            raise RuntimeError("Temporary planner failure")

        def fetch_events(self, start_time, end_time):
            return []

    class HealthyConnector:
        def fetch_tasks(self):
            return []

        def fetch_events(self, start_time, end_time):
            return []

    service = planner_service.PlannerService(db=db)
    monkeypatch.setattr(status_service, "log_status_transition", capture_transition)
    service.get_connector = lambda _account: FailingConnector()

    service.sync_all()

    db.refresh(account)
    assert transitions[0]["previous_status"] == status_service.STATUS_OPERATIONAL
    assert transitions[0]["new_status"] == status_service.STATUS_DEGRADED

    service.get_connector = lambda _account: HealthyConnector()
    service.sync_all()

    assert transitions[1]["previous_status"] == status_service.STATUS_DEGRADED
    assert transitions[1]["new_status"] == status_service.STATUS_OPERATIONAL
    db.close()
