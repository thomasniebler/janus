"""Tests for process startup and runtime trigger worker wiring."""

import sys
from unittest.mock import MagicMock, patch

import pytest

from janus import startup
from janus.config import Settings, settings


def _stub_unrelated_startup_steps(monkeypatch):
    """Mock every ensure_runtime_initialized step unrelated to trigger wiring."""
    monkeypatch.setattr(startup.runtime, "configure_logging", MagicMock())
    monkeypatch.setattr(startup.runtime, "enforce_single_instance", MagicMock())
    monkeypatch.setattr(startup.notifications.NotificationService, "configure", MagicMock())
    monkeypatch.setattr(settings, "janus_secret_key", "not-the-placeholder-secret")
    monkeypatch.setattr(startup.database, "ensure_schema_up_to_date", MagicMock())

    class FakeSession:
        def close(self):
            return None

    monkeypatch.setattr(startup.database, "SessionLocal", lambda: FakeSession())
    monkeypatch.setattr(startup.auth_service, "ensure_setup_token", MagicMock())
    monkeypatch.setattr(startup.worker_manager, "start", MagicMock())
    monkeypatch.setattr(startup, "prepare_knowledge_search", MagicMock())


def test_runtime_trigger_worker_is_enabled_by_default(monkeypatch, tmp_path):
    """Submitted triggers must execute out of the box instead of sitting queued (JAN-199)."""
    monkeypatch.delenv("JANUS_RUNTIME_TRIGGER_WORKER_ENABLED", raising=False)
    monkeypatch.delenv("runtime_trigger_worker_enabled", raising=False)
    with patch.object(Settings, "_toml_config_file", tmp_path / "missing-settings.toml"):
        fresh = Settings(_env_file=None)

    assert Settings.model_fields["runtime_trigger_worker_enabled"].default is True
    assert fresh.runtime_trigger_worker_enabled is True


def test_runtime_trigger_worker_does_not_start_when_disabled(monkeypatch):
    """Operators who opt out must not get a worker started implicitly."""
    monkeypatch.setattr(settings, "runtime_trigger_worker_enabled", False)
    start_mock = MagicMock()
    monkeypatch.setattr(startup.worker_manager, "start", start_mock)

    assert startup.start_runtime_trigger_worker() is False
    start_mock.assert_not_called()


def test_runtime_trigger_worker_requires_a_configured_handler(monkeypatch):
    """Opting in without a runtime composition must fail before a worker starts."""
    monkeypatch.setattr(settings, "runtime_trigger_worker_enabled", True)

    with pytest.raises(RuntimeError, match="no runtime trigger handler is configured"):
        startup.start_runtime_trigger_worker()

    assert startup._runtime_trigger_stop_event is None


def test_runtime_trigger_worker_starts_and_stops_through_worker_manager(monkeypatch):
    """Startup should pass a cooperative stop event to the supervised worker."""
    monkeypatch.setattr(settings, "runtime_trigger_worker_enabled", True)
    handler = MagicMock()
    factory = MagicMock(return_value=handler)
    startup.configure_runtime_trigger_handler(factory)
    start_mock = MagicMock()
    stop_mock = MagicMock()
    monkeypatch.setattr(startup.worker_manager, "start", start_mock)
    monkeypatch.setattr(startup.worker_manager, "stop", stop_mock)

    assert startup.start_runtime_trigger_worker() is True
    factory.assert_called_once_with()
    start_mock.assert_called_once()
    assert start_mock.call_args.args[0] == "runtime-triggers"
    assert start_mock.call_args.kwargs["args"] == (startup._runtime_trigger_stop_event,)
    assert start_mock.call_args.kwargs["restart_policy"] is True

    assert startup.start_runtime_trigger_worker() is False
    assert startup.stop_runtime_trigger_worker() is True
    stop_mock.assert_called_once_with("runtime-triggers")
    assert startup._runtime_trigger_stop_event is None


def test_runtime_trigger_worker_status_reports_disabled(monkeypatch):
    """Status must reflect the configuration, not a hardcoded UI assumption."""
    monkeypatch.setattr(settings, "runtime_trigger_worker_enabled", False)

    assert startup.runtime_trigger_worker_status() == {"enabled": False, "running": False}


def test_runtime_trigger_worker_status_reports_running_worker(monkeypatch):
    """An enabled, started worker with a live thread reports running."""
    monkeypatch.setattr(settings, "runtime_trigger_worker_enabled", True)
    startup.configure_runtime_trigger_handler(MagicMock())
    monkeypatch.setattr(startup.worker_manager, "start", MagicMock())
    monkeypatch.setattr(startup.worker_manager, "stop", MagicMock())
    monkeypatch.setattr(
        startup.worker_manager,
        "status",
        MagicMock(return_value={"runtime-triggers": {"alive": True}}),
    )

    assert startup.runtime_trigger_worker_status() == {"enabled": True, "running": False}
    assert startup.start_runtime_trigger_worker() is True
    assert startup.runtime_trigger_worker_status() == {"enabled": True, "running": True}

    startup.worker_manager.status.return_value = {"runtime-triggers": {"alive": False}}
    assert startup.runtime_trigger_worker_status() == {"enabled": True, "running": False}

    assert startup.stop_runtime_trigger_worker() is True


def test_ensure_runtime_initialized_skips_trigger_toolbox_import_when_disabled(monkeypatch):
    """A process that opted out must not pull in the trigger runtime's toolbox graph.

    Regression test: ``ensure_runtime_initialized`` used to import
    ``janus.trigger_runtime`` (and, transitively, the full tool
    registry/toolbox graph, including native connector dependencies like the
    WhatsApp/Neonize toolbox) unconditionally on every process start,
    regardless of ``runtime_trigger_worker_enabled``. On hosts where that
    native dependency can't even be imported, this crashed the process
    outright before it could serve anything.
    """
    monkeypatch.setattr(settings, "runtime_trigger_worker_enabled", False)
    _stub_unrelated_startup_steps(monkeypatch)
    sys.modules.pop("janus.trigger_runtime", None)

    startup.ensure_runtime_initialized()

    assert "janus.trigger_runtime" not in sys.modules
    assert startup._runtime_trigger_handler_factory is None


def test_ensure_runtime_initialized_configures_trigger_handler_when_enabled(monkeypatch):
    """When enabled (the default), startup must wire up a real trigger handler."""
    monkeypatch.setattr(settings, "runtime_trigger_worker_enabled", True)
    _stub_unrelated_startup_steps(monkeypatch)

    startup.ensure_runtime_initialized()

    assert startup._runtime_trigger_handler_factory is not None
