"""Planner synchronization worker loop."""

import time

from janus import event_logger, models, planner_service


def planner_sync_loop():
    """Periodically synchronize planner data."""
    while True:
        try:
            event_logger.log(models.EventType.INFO, "PlannerSync", "Starting background planner sync.")
            service = planner_service.PlannerService()
            try:
                service.sync_all()
                event_logger.log(
                    models.EventType.SUCCESS, "PlannerSync", "Background planner sync completed successfully."
                )
            finally:
                service.db.close()
        except Exception as e:
            event_logger.log(models.EventType.ERROR, "PlannerSync", f"Planner sync error: {e}")
        time.sleep(1800)
