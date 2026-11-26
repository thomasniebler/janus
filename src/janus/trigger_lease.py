"""Keep a running trigger's claim alive while its handler works.

A claim expires ``lease_seconds`` after ``claimed_at``; an expired ``running``
row is re-claimable as abandoned. Without renewal, a run longer than the lease
-- a slow model, or a definition whose run limits are switched off -- would be
re-claimed and executed a second time concurrently. ``lease_heartbeat`` renews
the claim from a daemon thread while the handler runs; a crashed worker stops
renewing, so its run is still re-claimed once the lease lapses.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from threading import Event, Thread

from sqlalchemy import update
from sqlalchemy.orm import Session

from janus import models

logger = logging.getLogger(__name__)


def renew_trigger_claim(db: Session, trigger_id: int, claim_token: str, *, now: datetime | None = None) -> bool:
    """Move a running trigger's ``claimed_at`` to now while ``claim_token`` still owns it."""
    if not claim_token.strip():
        raise ValueError("claim_token must not be blank")
    moment = (now or datetime.now(UTC)).astimezone(UTC).replace(tzinfo=None)
    result = db.execute(
        update(models.RuntimeTrigger)
        .where(
            models.RuntimeTrigger.id == trigger_id,
            models.RuntimeTrigger.status == models.RuntimeTriggerStatus.RUNNING.value,
            models.RuntimeTrigger.claim_token == claim_token,
        )
        .values(claimed_at=moment)
    )
    db.commit()
    return result.rowcount == 1


@contextmanager
def lease_heartbeat(
    session_factory: Callable[[], Session], trigger_id: int, claim_token: str, *, lease_seconds: int
) -> Iterator[None]:
    """Renew the claim every third of ``lease_seconds`` (at least every second) while the block runs."""
    stop = Event()

    def _beat() -> None:
        while not stop.wait(max(1.0, lease_seconds / 3)):
            db = session_factory()
            try:
                if not renew_trigger_claim(db, trigger_id, claim_token):
                    return  # the claim was lost or finished; nothing left to keep alive
            except Exception:  # noqa: BLE001 - a missed beat must not kill the run; the next one retries
                logger.exception("Renewing the lease of trigger %s failed", trigger_id)
            finally:
                db.close()

    thread = Thread(target=_beat, name=f"trigger-lease-{trigger_id}", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=5)


__all__ = ["lease_heartbeat", "renew_trigger_claim"]
