"""Supervised background worker manager for Janus daemon threads."""

import logging
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from janus import database, event_logger, models

logger = logging.getLogger(__name__)

# Backoff delays in seconds: 10s, 30s, 60s, 300s, then cap at 600s
_BACKOFF = [10, 30, 60, 300, 600]


class PermanentWorkerError(Exception):
    """Raised by a worker body for a failure that retrying cannot fix.

    Example: a native library that cannot load on this host. The supervisor records it like
    any crash but never restarts the worker.
    """


@dataclass
class WorkerState:
    """Tracks lifecycle state for a supervised worker thread."""

    label: str
    target: Callable
    args: tuple
    restart_policy: bool
    thread: threading.Thread | None = None
    error_queue: queue.Queue = field(default_factory=queue.Queue)
    last_error: str | None = None
    restart_count: int = 0
    last_heartbeat: datetime | None = None
    restart_scheduled: bool = False
    stopped: bool = False
    permanently_failed: bool = False


class WorkerManager:
    """Supervises daemon threads and restarts them with exponential backoff on failure."""

    def __init__(self, start_supervisor: bool = True) -> None:
        """Starts the supervisor loop and initializes the worker registry."""
        self._workers: dict[str, WorkerState] = {}
        self._lock = threading.Lock()
        self._label_locks: dict[str, threading.Lock] = {}
        if start_supervisor:
            supervisor = threading.Thread(target=self._supervisor_loop, daemon=True, name="worker-supervisor")
            supervisor.start()

    def _lock_for_label(self, label: str) -> threading.Lock:
        """Return a lock serializing start()/stop() lifecycle changes for one label."""
        with self._lock:
            lock = self._label_locks.get(label)
            if lock is None:
                lock = threading.Lock()
                self._label_locks[label] = lock
            return lock

    def start(
        self,
        label: str,
        target: Callable,
        args: tuple = (),
        restart_policy: bool = True,
    ) -> None:
        """Register and start a supervised worker thread, replacing any existing one under this label.

        Stops and joins a still-running previous worker under this label first: overwriting
        `_workers[label]` without this would silently drop the old WorkerState from supervision
        (so a later crash of that orphaned thread is never logged or restarted) while its thread
        may still be alive and running concurrently with the new one. The whole replace sequence
        runs under this label's lock so two concurrent `start()` calls for the same label (e.g. a
        double restart request) can't both read the same existing state and each install their
        own new one — the second one waits for the first to finish before it even looks.

        Raises RuntimeError instead of launching a replacement if the previous worker doesn't
        stop within the join timeout: `WorkerManager` has no way to force a thread to exit (e.g.
        a Neonize connector blocked in a third-party blocking call), so launching anyway would
        silently run two workers against the same underlying resource — worse than refusing.
        """
        with self._lock_for_label(label):
            with self._lock:
                existing = self._workers.get(label)
                old_thread = None
                if existing is not None:
                    existing.stopped = True
                    old_thread = existing.thread
            if old_thread is not None and old_thread.is_alive() and old_thread is not threading.current_thread():
                old_thread.join(timeout=5.0)
                if old_thread.is_alive():
                    with self._lock:
                        if existing is not None:
                            existing.stopped = False
                    logger.warning(
                        "Worker '%s' did not stop within timeout; refusing to start a replacement "
                        "while the previous worker may still be running.",
                        label,
                    )
                    raise RuntimeError(
                        f"Worker '{label}' did not stop within the timeout; refusing to start a duplicate."
                    )
            with self._lock:
                state = WorkerState(label=label, target=target, args=args, restart_policy=restart_policy)
                self._workers[label] = state
            self._launch(state)

    def heartbeat(self, label: str) -> None:
        """Called by a worker to record liveness."""
        with self._lock:
            state = self._workers.get(label)
            if state:
                state.last_heartbeat = datetime.now(UTC)

    def stop(self, label: str, *, timeout: float = 5.0) -> bool:
        """Stop a named worker and prevent the supervisor from restarting it."""
        with self._lock_for_label(label):
            with self._lock:
                state = self._workers.get(label)
                if state is None:
                    return False
                state.stopped = True
                thread = state.thread
            if thread is not None and thread.is_alive() and thread is not threading.current_thread():
                thread.join(timeout=max(0.0, timeout))
            return True

    def status(self) -> dict[str, dict]:
        """Return a snapshot of all worker states."""
        with self._lock:
            return {
                label: {
                    "alive": s.thread is not None and s.thread.is_alive(),
                    "restart_count": s.restart_count,
                    "last_error": s.last_error,
                    "last_heartbeat": s.last_heartbeat.isoformat() if s.last_heartbeat else None,
                    "permanently_failed": s.permanently_failed,
                }
                for label, s in self._workers.items()
            }

    def _launch(self, state: WorkerState) -> None:
        def _wrapper():
            try:
                state.target(*state.args)
            except Exception as exc:
                state.error_queue.put(exc)

        t = threading.Thread(target=_wrapper, daemon=True, name=f"worker-{state.label}")
        t.start()
        with self._lock:
            state.thread = t
            state.restart_scheduled = False

    def _schedule_restart(self, state: WorkerState, delay: int) -> None:
        def _delayed_restart(s=state, d=delay):
            time.sleep(d)
            with self._lock_for_label(s.label):
                if s.stopped:
                    return
                with self._lock:
                    # An explicit start()/stop() may have already replaced this label's
                    # worker while this timer was sleeping; relaunching a superseded
                    # state would run it unregistered alongside whatever replaced it.
                    if self._workers.get(s.label) is not s:
                        return
                    if s.thread is not None and s.thread.is_alive():
                        s.restart_scheduled = False
                        return
                logger.info("Restarting worker '%s'", s.label)
                self._launch(s)

        threading.Thread(target=_delayed_restart, daemon=True).start()

    def _handle_dead_worker(self, state: WorkerState) -> None:
        if state.restart_scheduled or state.permanently_failed:
            return

        # Thread is dead — collect error
        try:
            exc = state.error_queue.get_nowait()
            state.last_error = str(exc)
        except queue.Empty:
            exc = None
            state.last_error = "Worker exited without error (unexpected return)"

        if isinstance(exc, PermanentWorkerError):
            state.permanently_failed = True
            self._log_crash(state)
            logger.error(
                "Worker '%s' failed permanently; it will not be restarted. Error: %s", state.label, state.last_error
            )
            return

        self._log_crash(state)

        if not state.restart_policy:
            return

        delay = _BACKOFF[min(state.restart_count, len(_BACKOFF) - 1)]
        logger.warning(
            "Worker '%s' crashed (attempt %d). Restarting in %ds. Error: %s",
            state.label,
            state.restart_count + 1,
            delay,
            state.last_error,
        )
        state.restart_count += 1
        state.restart_scheduled = True
        self._schedule_restart(state, delay)

    def _supervisor_loop(self) -> None:
        while True:
            time.sleep(5)
            with self._lock:
                workers = list(self._workers.values())

            for state in workers:
                if state.stopped or state.thread is None:
                    continue
                if state.thread.is_alive():
                    continue
                self._handle_dead_worker(state)

    def _log_crash(self, state: WorkerState) -> None:
        try:
            db = database.SessionLocal()
            try:
                event_logger.log(
                    models.EventType.ERROR,
                    "WorkerManager",
                    f"Worker '{state.label}' crashed (restart #{state.restart_count}): {state.last_error}",
                )
                # Update EmailAccount.monitor_status if this is an IMAP worker
                if state.label.startswith("imap:"):
                    email = state.label.split("imap:", 1)[1]
                    account = db.query(models.EmailAccount).filter(models.EmailAccount.email == email).first()
                    if account:
                        account.monitor_status = models.MonitorStatus.ERROR
                        account.last_error = state.last_error
                        db.commit()
                # Update WhatsAppAccount.connection_status if this is a WhatsApp connector worker
                elif state.label.startswith("whatsapp:"):
                    try:
                        account_id = int(state.label.split("whatsapp:", 1)[1])
                    except ValueError:
                        account_id = None
                    if account_id is not None:
                        whatsapp_account = (
                            db.query(models.WhatsAppAccount).filter(models.WhatsAppAccount.id == account_id).first()
                        )
                        if whatsapp_account:
                            whatsapp_account.connection_status = models.WhatsAppConnectionStatus.DEGRADED
                            whatsapp_account.last_sync_error = state.last_error
                            db.commit()
            finally:
                db.close()
        except Exception as e:
            logger.error("WorkerManager: failed to log crash for '%s': %s", state.label, e)


# Module-level singleton
worker_manager = WorkerManager()
