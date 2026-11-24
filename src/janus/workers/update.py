"""Self-hosted auto-update worker loop (see ``services/update_service.py``)."""

import logging
import time

from janus import event_logger, models
from janus.config import settings
from janus.services import update_boot, update_service
from janus.worker_manager import worker_manager

WORKER_LABEL = "auto-update"
_SOURCE = "AutoUpdate"
logger = logging.getLogger(__name__)


def start_if_eligible() -> bool:
    """Start the supervised auto-update loop when this install may update itself."""
    status = update_service.eligibility()
    if not status.eligible:
        logger.info("Auto-update is off: %s.", status.reason)
        return False
    worker_manager.start(WORKER_LABEL, auto_update_loop, restart_policy=True)
    return True


def run_update_check() -> bool:
    """Check once; install and request a restart when a newer release exists. True if installed."""
    try:
        release = update_service.check_for_update()
    except update_service.UpdateError as exc:
        event_logger.log(models.EventType.WARNING, _SOURCE, f"Update check failed: {exc}")
        return False
    if release is None or release.tag == update_boot.skipped_tag():
        return False
    current = update_service.format_version(update_service.installed_version())
    event_logger.log(models.EventType.INFO, _SOURCE, f"Installing Janus {release.tag} (running {current}).")
    try:
        installed = update_service.apply_update(release)
    except update_service.UpdateError as exc:
        event_logger.log(models.EventType.ERROR, _SOURCE, f"Update to {release.tag} failed and was rolled back: {exc}")
        return False
    update_boot.write_pending(installed)
    event_logger.log(models.EventType.SUCCESS, _SOURCE, f"Installed Janus {release.tag}; restarting.")
    update_service.request_restart()
    return True


def auto_update_loop() -> None:
    """Check for a new release every ``auto_update_interval_minutes``; stop once one is installed."""
    while True:
        installed = run_update_check()
        worker_manager.heartbeat(WORKER_LABEL)
        if installed:
            # The process is about to re-execute; never install twice into the same process.
            while True:
                time.sleep(3600)
        time.sleep(settings.auto_update_interval_minutes * 60)
