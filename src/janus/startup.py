"""Process startup initialization for Janus and its runtime trigger worker."""

import logging
import threading
from collections.abc import Callable
from threading import Event

from janus import database, notifications, runtime
from janus.config import settings
from janus.services import auth_service
from janus.trigger_service import DurableTriggerWorker
from janus.triggers import TriggerHandler
from janus.worker_manager import worker_manager

logger = logging.getLogger(__name__)
_startup_init_lock = threading.Lock()
_startup_initialized = False
_runtime_trigger_worker_lock = threading.Lock()
_runtime_trigger_handler_factory: Callable[[], TriggerHandler] | None = None
_runtime_trigger_stop_event: Event | None = None
_RUNTIME_TRIGGER_WORKER_LABEL = "runtime-triggers"
_KNOWLEDGE_INDEX_WORKER_LABEL = "knowledge-index"


def configure_runtime_trigger_handler(factory: Callable[[], TriggerHandler]) -> None:
    """Register the runtime handler used by the opt-in durable trigger worker."""
    global _runtime_trigger_handler_factory
    if not callable(factory):
        raise TypeError("runtime trigger handler factory must be callable")
    with _runtime_trigger_worker_lock:
        if _runtime_trigger_stop_event is not None:
            raise RuntimeError("runtime trigger worker is already running")
        _runtime_trigger_handler_factory = factory


def start_runtime_trigger_worker() -> bool:
    """Start the durable trigger worker when enabled (the default) and configured."""
    global _runtime_trigger_stop_event
    if not settings.runtime_trigger_worker_enabled:
        return False
    with _runtime_trigger_worker_lock:
        if _runtime_trigger_stop_event is not None:
            return False
        if _runtime_trigger_handler_factory is None:
            raise RuntimeError("runtime trigger worker is enabled but no runtime trigger handler is configured")
        stop_event = Event()
        worker = DurableTriggerWorker(
            database.SessionLocal,
            _runtime_trigger_handler_factory(),
            max_attempts=settings.runtime_trigger_max_attempts,
            retry_backoff_seconds=settings.runtime_trigger_retry_backoff_seconds,
            scheduler=_run_trigger_scheduler_tick,
        )
        worker_manager.start(
            _RUNTIME_TRIGGER_WORKER_LABEL,
            worker.run_forever,
            args=(stop_event,),
            restart_policy=True,
        )
        _runtime_trigger_stop_event = stop_event
        return True


def _run_trigger_scheduler_tick(now=None) -> None:
    """Seed built-in definitions, enqueue due recurring runs, then maintain delegations.

    Runs at the start of every trigger-worker poll (JAN-228/231, JAN-207).
    """
    # Imported lazily for the same reason as the handler factory below.
    from janus import builtin_triggers, delegation_runtime, trigger_definition_service

    db = database.SessionLocal()
    try:
        builtin_triggers.ensure_builtin_definitions_for_all_users(db)
        trigger_definition_service.run_due_schedules(db, now=now)
    finally:
        db.close()
    # Durable re-pickup of delegations a restart orphaned, and resuming
    # trigger runs whose delegated children have all finished (JAN-207).
    # Kept apart so a failure here never stops schedules from being enqueued.
    try:
        delegation_runtime.run_delegation_maintenance(database.SessionLocal, now=now)
    except Exception:  # noqa: BLE001 - retried on the next poll
        logger.exception("Delegation maintenance tick failed")


def stop_runtime_trigger_worker() -> bool:
    """Signal the runtime worker to stop and prevent supervised restarts."""
    global _runtime_trigger_stop_event
    with _runtime_trigger_worker_lock:
        stop_event = _runtime_trigger_stop_event
        if stop_event is None:
            return False
        stop_event.set()
        _runtime_trigger_stop_event = None
    worker_manager.stop(_RUNTIME_TRIGGER_WORKER_LABEL)
    return True


def runtime_trigger_worker_status() -> dict[str, bool]:
    """Report whether the runtime trigger worker is enabled and currently running.

    ``enabled`` mirrors the ``runtime_trigger_worker_enabled`` setting;
    ``running`` is true only while this process has started the worker and its
    supervised thread is alive, so the UI can tell "disabled by configuration"
    apart from "enabled but not running" (for example, crashed or not started).
    """
    with _runtime_trigger_worker_lock:
        started = _runtime_trigger_stop_event is not None
    worker_state = worker_manager.status().get(_RUNTIME_TRIGGER_WORKER_LABEL, {})
    return {
        "enabled": settings.runtime_trigger_worker_enabled,
        "running": started and bool(worker_state.get("alive", False)),
    }


def prepare_knowledge_search() -> None:
    """Logs missing search capabilities and builds missing keyword tables (JAN-241).

    Never fatal: without sqlite-vec or FTS5, search falls back to keyword or
    substring matching, which system status reports.
    """
    from janus.services import vector_index

    vector_index.log_capabilities()
    db = database.SessionLocal()
    try:
        keyword_index = vector_index.get_keyword_index(db)
        if keyword_index is not None:
            keyword_index.ensure_tables(db)
            db.commit()
    except Exception:
        db.rollback()
        logging.getLogger(__name__).exception("Failed to prepare the knowledge keyword index")
    finally:
        db.close()


def start_knowledge_index_worker() -> bool:
    """Start the supervised embedding sweep when enabled (the default)."""
    if not settings.knowledge_index_worker_enabled:
        return False
    from janus.services.knowledge_index import knowledge_index_loop

    worker_manager.start(_KNOWLEDGE_INDEX_WORKER_LABEL, knowledge_index_loop, restart_policy=True)
    return True


def ensure_runtime_initialized() -> None:
    """Run one-time process initialization shared by CLI start and API startup."""
    global _startup_initialized
    if _startup_initialized:
        return

    with _startup_init_lock:
        if _startup_initialized:
            return

        runtime.configure_logging()
        runtime.enforce_single_instance()
        notifications.NotificationService.configure(enabled=not settings.server_mode)
        settings.require_secret_key()
        database.ensure_schema_up_to_date()
        db = database.SessionLocal()
        try:
            auth_service.ensure_setup_token(db)
        finally:
            db.close()
        if settings.runtime_trigger_worker_enabled and _runtime_trigger_handler_factory is None:
            # Lazily imported and only when the worker is enabled (the
            # default; operators can opt out with the setting):
            # this pulls in the full tool registry/toolbox graph, including
            # native connector dependencies (e.g. the WhatsApp/Neonize
            # toolbox), which must not be a hard requirement for every
            # process start regardless of whether runtime triggers are used.
            from janus.trigger_runtime import build_runtime_trigger_handler

            configure_runtime_trigger_handler(lambda: build_runtime_trigger_handler(database.SessionLocal))
        start_runtime_trigger_worker()
        prepare_knowledge_search()
        start_knowledge_index_worker()
        _startup_initialized = True
