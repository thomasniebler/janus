"""Error maintenance worker loop."""

import time

from janus import database, event_logger, models
from janus.services.error_service import ErrorService
from janus.worker_manager import worker_manager


def error_compaction_loop():
    """Compact grouped error events once per day."""
    while True:
        db = database.SessionLocal()
        try:
            event_logger.log(models.EventType.INFO, "ErrorCompaction", "Starting background error compaction.")
            result = ErrorService(db).compact_errors()
            event_logger.log(
                models.EventType.SUCCESS,
                "ErrorCompaction",
                "Background error compaction completed successfully.",
                result,
            )
        except Exception as e:
            event_logger.log(models.EventType.ERROR, "ErrorCompaction", f"Error compaction failed: {e}")
        finally:
            db.close()
            worker_manager.heartbeat("error-compaction")
        time.sleep(86400)
