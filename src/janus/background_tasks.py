"""A minimal submit/status abstraction over background threads (JAN-138, JAN-207).

This is deliberately small: exactly what durable re-pickup of delegated child
tasks needs, and nothing that would grow into a second job system next to
the :class:`~janus.trigger_service.DurableTriggerWorker`.

* :class:`BackgroundTaskRunner` is the interface: ``submit`` a named job,
  optionally under a ``key`` that makes the submission idempotent while an
  earlier job with that key is still pending or running, and ask for the
  ``status`` of the latest job under a key.
* :class:`ThreadPoolTaskRunner` is the only implementation: a bounded
  ``ThreadPoolExecutor``.

This is **not a distributed or durable queue.** A runner lives in one
process and forgets everything when it stops. Durability comes from the
database rows a job works on plus the trigger worker's poll (a
``WorkerManager``-supervised thread), which re-submits whatever a restart
left behind. Long-lived supervised threads (IMAP monitors, the trigger
worker itself) stay on :class:`~janus.worker_manager.WorkerManager`.
"""

from __future__ import annotations

import enum
import logging
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Protocol

logger = logging.getLogger(__name__)


class TaskStatus(str, enum.Enum):
    """Lifecycle of one submitted job, as seen by this process."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


#: States in which a keyed job still blocks a new submission under its key.
_ACTIVE_STATUSES = frozenset({TaskStatus.PENDING, TaskStatus.RUNNING})

#: Finished keyed handles are kept for ``status`` until there are this many,
#: then pruned, so a long-lived process does not accumulate one per job.
_MAX_TRACKED_KEYS = 512


@dataclass
class TaskHandle:
    """A submitted job: its name, optional key, and current status."""

    name: str
    key: str | None
    status: TaskStatus = TaskStatus.PENDING
    error: str | None = None


class BackgroundTaskRunner(Protocol):
    """Run short background jobs and report their status."""

    def submit(self, name: str, fn: Callable[[], object], *, key: str | None = None) -> TaskHandle:
        """Schedule ``fn``; with a ``key`` that is still pending or running, return that job instead."""
        ...

    def status(self, key: str) -> TaskStatus | None:
        """Return the status of the latest job submitted under ``key``, or ``None`` if there is none."""
        ...


class ThreadPoolTaskRunner:
    """Thread-backed :class:`BackgroundTaskRunner` with a bounded worker count.

    A job's exception is logged and recorded on its handle, never re-raised:
    a pool thread has no caller to raise to. Jobs that must record failures
    durably (a delegation does) do so themselves.
    """

    def __init__(self, max_workers: int, *, thread_name_prefix: str) -> None:
        """Create the pool; ``max_workers`` bounds how many jobs run at once."""
        if max_workers < 1:
            raise ValueError("max_workers must be at least 1")
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix=thread_name_prefix)
        self._lock = threading.Lock()
        self._by_key: dict[str, TaskHandle] = {}

    def submit(self, name: str, fn: Callable[[], object], *, key: str | None = None) -> TaskHandle:
        """Schedule ``fn`` unless a job under the same ``key`` is still pending or running."""
        with self._lock:
            if key is not None:
                existing = self._by_key.get(key)
                if existing is not None and existing.status in _ACTIVE_STATUSES:
                    return existing
            handle = TaskHandle(name=name, key=key)
            if key is not None:
                if len(self._by_key) >= _MAX_TRACKED_KEYS:
                    self._prune_finished()
                self._by_key[key] = handle

        def job() -> None:
            with self._lock:
                handle.status = TaskStatus.RUNNING
            try:
                fn()
            except Exception as error:  # noqa: BLE001 - recorded on the handle, see class docstring
                logger.exception("Background task %s failed", name)
                with self._lock:
                    handle.status = TaskStatus.FAILED
                    handle.error = str(error) or error.__class__.__name__
                return
            with self._lock:
                handle.status = TaskStatus.SUCCEEDED

        # Status is tracked on the handle; nothing waits on the future.
        self._pool.submit(job)
        return handle

    def status(self, key: str) -> TaskStatus | None:
        """Return the status of the latest job submitted under ``key``."""
        with self._lock:
            handle = self._by_key.get(key)
            return None if handle is None else handle.status

    def _prune_finished(self) -> None:
        """Forget finished keyed handles (caller holds the lock)."""
        for key in [key for key, handle in self._by_key.items() if handle.status not in _ACTIVE_STATUSES]:
            del self._by_key[key]


__all__ = ["BackgroundTaskRunner", "TaskHandle", "TaskStatus", "ThreadPoolTaskRunner"]
