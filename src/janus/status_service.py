"""Status aggregation and background probe helpers for Janus."""

import json
import time
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from janus import database, event_logger, llm_service, models
from janus.services import knowledge_index
from janus.time_utils import normalize_timestamp, utcnow
from janus.worker_manager import worker_manager

STATUS_OPERATIONAL = "operational"
STATUS_DEGRADED = "degraded"
STATUS_OUTAGE = "outage"
STATUS_UNKNOWN = "unknown"
STATUS_RESOLVED = "resolved"

MAIL_HEARTBEAT_TIMEOUT = timedelta(minutes=2)
PLANNER_STALE_THRESHOLD = timedelta(minutes=90)
LLM_STALE_THRESHOLD = timedelta(minutes=15)
LLM_PROBE_INTERVAL_SECONDS = 300
RECENT_INCIDENT_LIMIT = 10


def _combine_statuses(statuses: list[str]) -> str:
    if not statuses:
        return STATUS_UNKNOWN
    if any(status == STATUS_OUTAGE for status in statuses):
        if all(status == STATUS_OUTAGE for status in statuses):
            return STATUS_OUTAGE
        return STATUS_DEGRADED
    if any(status == STATUS_DEGRADED for status in statuses):
        return STATUS_DEGRADED
    if any(status == STATUS_OPERATIONAL for status in statuses):
        return STATUS_OPERATIONAL
    return STATUS_UNKNOWN


def _overall_status(statuses: list[str]) -> str:
    if not statuses:
        return STATUS_UNKNOWN
    if any(status == STATUS_OUTAGE for status in statuses):
        return STATUS_OUTAGE
    if any(status == STATUS_DEGRADED for status in statuses):
        return STATUS_DEGRADED
    if any(status == STATUS_OPERATIONAL for status in statuses):
        return STATUS_OPERATIONAL
    return STATUS_UNKNOWN


def _service_summary(name: str, components: list[dict[str, Any]]) -> str:
    if not components:
        return f"No active {name.lower()} components are configured."

    counts = {
        STATUS_OPERATIONAL: 0,
        STATUS_DEGRADED: 0,
        STATUS_OUTAGE: 0,
        STATUS_UNKNOWN: 0,
    }
    for component in components:
        counts[component["status"]] += 1

    return (
        f"{counts[STATUS_OPERATIONAL]} operational, "
        f"{counts[STATUS_DEGRADED]} degraded, "
        f"{counts[STATUS_OUTAGE]} outage, "
        f"{counts[STATUS_UNKNOWN]} unknown"
    )


def _latest_timestamp(components: list[dict[str, Any]]) -> datetime | None:
    timestamps = [component["updated_at"] for component in components if component["updated_at"] is not None]
    return max(timestamps) if timestamps else None


def log_status_transition(
    *,
    service_id: str,
    service_name: str,
    component_id: str,
    component_name: str,
    previous_status: str | None,
    new_status: str,
    message: str,
) -> None:
    """Persist a structured service-status transition event."""
    if previous_status == new_status or previous_status is None or new_status == STATUS_UNKNOWN:
        return

    if new_status == STATUS_OPERATIONAL:
        if previous_status not in {STATUS_DEGRADED, STATUS_OUTAGE}:
            return
        event_type = models.EventType.SUCCESS
        event_message = f"{service_name} recovered for {component_name}: {message}"
    elif new_status == STATUS_DEGRADED:
        event_type = models.EventType.WARNING
        event_message = f"{service_name} degraded for {component_name}: {message}"
    elif new_status == STATUS_OUTAGE:
        event_type = models.EventType.ERROR
        event_message = f"{service_name} outage for {component_name}: {message}"
    else:
        return

    event_logger.log(
        event_type,
        "SystemStatus",
        event_message,
        {
            "service_id": service_id,
            "service_name": service_name,
            "component_id": component_id,
            "component_name": component_name,
            "previous_status": previous_status,
            "new_status": new_status,
        },
    )


def _planner_status_details(
    last_sync_at: datetime | None, last_sync_error: str | None, now: datetime
) -> tuple[str, str]:
    last_sync_at = normalize_timestamp(last_sync_at)
    if last_sync_error and (last_sync_at is None or now - last_sync_at > PLANNER_STALE_THRESHOLD):
        return STATUS_OUTAGE, last_sync_error
    if last_sync_error:
        return STATUS_DEGRADED, last_sync_error
    if last_sync_at is None:
        return STATUS_DEGRADED, "Waiting for the first successful sync."
    if now - last_sync_at > PLANNER_STALE_THRESHOLD:
        return STATUS_DEGRADED, "The latest planner sync is stale."
    return STATUS_OPERATIONAL, "Planner sync is healthy."


def _mail_component_status(account: models.EmailAccount, worker_state: dict[str, Any], now: datetime) -> dict[str, Any]:
    heartbeat = normalize_timestamp(account.last_heartbeat)
    thread_alive = bool(worker_state.get("alive", False))

    if account.monitor_status == models.MonitorStatus.ERROR:
        status = STATUS_OUTAGE
        message = account.last_error or "The IMAP monitor reported an error."
    elif heartbeat is None:
        status = STATUS_DEGRADED
        message = "Waiting for the monitor heartbeat."
    elif now - heartbeat > MAIL_HEARTBEAT_TIMEOUT:
        status = STATUS_DEGRADED
        message = "The latest heartbeat is stale."
    elif not thread_alive:
        status = STATUS_DEGRADED
        message = "The monitor thread is not currently alive."
    else:
        status = STATUS_OPERATIONAL
        message = "The monitor is running normally."

    return {
        "id": f"mail-account-{account.id}",
        "name": account.name or account.email,
        "subtitle": account.email,
        "status": status,
        "message": message,
        "updated_at": heartbeat,
    }


def _planner_component_status(account: models.PlannerAccount, now: datetime) -> dict[str, Any]:
    last_sync_at = normalize_timestamp(account.last_sync_at)
    status, message = _planner_status_details(last_sync_at, account.last_sync_error, now)

    return {
        "id": f"planner-account-{account.id}",
        "name": account.name,
        "subtitle": account.provider.value if account.provider else None,
        "status": status,
        "message": message,
        "updated_at": last_sync_at,
    }


def _whatsapp_component_status(account: models.WhatsAppAccount, now: datetime) -> dict[str, Any]:
    last_sync_at = normalize_timestamp(account.last_synced_at)
    del now
    if account.last_sync_error and account.connection_status == models.WhatsAppConnectionStatus.DEGRADED:
        status = STATUS_DEGRADED
        message = account.last_sync_error
    elif not account.is_enabled:
        status = STATUS_UNKNOWN
        message = "The WhatsApp account is disabled."
    elif last_sync_at is None:
        status = STATUS_DEGRADED
        message = "Waiting for the first successful WhatsApp sync."
    elif account.connection_status == models.WhatsAppConnectionStatus.CONNECTED:
        status = STATUS_OPERATIONAL
        message = "WhatsApp sync is healthy."
    else:
        status = STATUS_DEGRADED
        message = account.last_sync_error or "WhatsApp sync has not reported a healthy state yet."

    return {
        "id": f"whatsapp-account-{account.id}",
        "name": account.display_name,
        "subtitle": account.provider,
        "status": status,
        "message": message,
        "updated_at": last_sync_at,
    }


def _llm_component_status(settings: models.Settings | None, now: datetime) -> dict[str, Any]:
    if settings is None:
        return {
            "id": "llm-active-backend",
            "name": "LLM backend",
            "subtitle": None,
            "status": STATUS_UNKNOWN,
            "message": "LLM settings have not been initialized yet.",
            "updated_at": None,
        }

    last_checked_at = normalize_timestamp(settings.llm_status_checked_at)
    provider = settings.llm_provider.value if settings.llm_provider else "unknown"
    model_name = llm_service.get_active_model_name(settings, settings.llm_provider)
    subtitle = f"{provider} · {model_name}"

    if last_checked_at is None:
        status = STATUS_DEGRADED
        message = "Waiting for the first background probe."
    elif settings.llm_status == STATUS_OUTAGE:
        status = STATUS_OUTAGE
        message = settings.llm_status_message or "The latest LLM probe failed."
    elif now - last_checked_at > LLM_STALE_THRESHOLD:
        status = STATUS_DEGRADED
        message = "The latest LLM probe is stale."
    else:
        status = STATUS_OPERATIONAL
        message = settings.llm_status_message or "The latest LLM probe succeeded."

    return {
        "id": "llm-active-backend",
        "name": "LLM backend",
        "subtitle": subtitle,
        "status": status,
        "message": message,
        "updated_at": last_checked_at,
    }


def _knowledge_component_status(db: Session) -> dict[str, Any]:
    """Vector/keyword search capability and embedding coverage (JAN-241)."""
    snapshot = knowledge_index.status_snapshot(db)
    model = snapshot["embedding_model"]
    worker = snapshot["worker"]
    keyword = "FTS5 keyword search" if snapshot["keyword_index_available"] else "substring search"
    unavailable_reason = (
        snapshot["capability_error"]
        if snapshot["dialect"] == "sqlite"
        else f"no vector index is implemented for {snapshot['dialect']} yet"
    )
    if model is None:
        status = STATUS_OPERATIONAL
        message = f"Embeddings are off; memory and topic search use {keyword}."
        if not snapshot["vector_search_available"]:
            message += f" Vector search would be unavailable: {unavailable_reason}"
    elif not snapshot["vector_search_available"]:
        status = STATUS_DEGRADED
        message = f"Vector search is unavailable ({unavailable_reason}); falling back to {keyword}."
    elif worker["last_error"]:
        status = STATUS_DEGRADED
        message = f"Embedding failed: {worker['last_error']}"
    else:
        status = STATUS_OPERATIONAL
        message = f"Semantic search with {keyword}."
        if snapshot["pending"]:
            message += " Some facts or topics are still being embedded; until then they match by keyword only."
    return {
        "id": "knowledge-search",
        "name": "Memory and topic search",
        "subtitle": model or "embeddings off",
        "status": status,
        "message": message,
        "updated_at": worker["last_run_at"],
    }


def get_status_overview(db: Session) -> dict[str, Any]:
    """Build the authenticated status overview payload."""
    now = utcnow()
    worker_status = worker_manager.status()

    mail_components = [
        _mail_component_status(account, worker_status.get(f"imap:{account.email}", {}), now)
        for account in db.query(models.EmailAccount).filter(models.EmailAccount.is_active).all()
    ]
    planner_components = [
        _planner_component_status(account, now)
        for account in db.query(models.PlannerAccount).filter(models.PlannerAccount.is_active).all()
    ]
    whatsapp_components = [
        _whatsapp_component_status(account, now)
        for account in db.query(models.WhatsAppAccount).filter(models.WhatsAppAccount.is_enabled).all()
    ]
    settings = db.query(models.Settings).first()
    llm_components = [_llm_component_status(settings, now)]
    knowledge_components = [_knowledge_component_status(db)]

    services = [
        {
            "id": "mail-sync",
            "name": "Mail Sync",
            "status": _combine_statuses([component["status"] for component in mail_components]),
            "summary": _service_summary("mail", mail_components),
            "updated_at": _latest_timestamp(mail_components),
            "components": mail_components,
        },
        {
            "id": "planner-sync",
            "name": "Planner Sync",
            "status": _combine_statuses([component["status"] for component in planner_components]),
            "summary": _service_summary("planner", planner_components),
            "updated_at": _latest_timestamp(planner_components),
            "components": planner_components,
        },
        {
            "id": "whatsapp-sync",
            "name": "WhatsApp Sync",
            "status": _combine_statuses([component["status"] for component in whatsapp_components]),
            "summary": _service_summary("whatsapp", whatsapp_components),
            "updated_at": _latest_timestamp(whatsapp_components),
            "components": whatsapp_components,
        },
        {
            "id": "llm",
            "name": "LLM",
            "status": _combine_statuses([component["status"] for component in llm_components]),
            "summary": _service_summary("llm", llm_components),
            "updated_at": _latest_timestamp(llm_components),
            "components": llm_components,
        },
        {
            "id": "knowledge",
            "name": "Knowledge search",
            "status": _combine_statuses([component["status"] for component in knowledge_components]),
            "summary": _service_summary("knowledge", knowledge_components),
            "updated_at": _latest_timestamp(knowledge_components),
            "components": knowledge_components,
        },
    ]

    overall_status = _overall_status([service["status"] for service in services])
    overall_messages = {
        STATUS_OPERATIONAL: "All monitored services are operational.",
        STATUS_DEGRADED: "Some services are degraded.",
        STATUS_OUTAGE: "One or more services are experiencing an outage.",
        STATUS_UNKNOWN: "Janus does not have enough status data yet.",
    }

    recent_incidents = []
    events = (
        db.query(models.Event)
        .filter(models.Event.source == "SystemStatus")
        .order_by(models.Event.timestamp.desc())
        .limit(RECENT_INCIDENT_LIMIT)
        .all()
    )
    for event in events:
        try:
            details = json.loads(event.details) if event.details else {}
        except json.JSONDecodeError:
            details = {}
        incident_status = (
            STATUS_RESOLVED
            if event.event_type == models.EventType.SUCCESS
            else details.get("new_status", STATUS_DEGRADED)
        )
        recent_incidents.append(
            {
                "id": event.id,
                "timestamp": normalize_timestamp(event.timestamp),
                "status": incident_status,
                "title": details.get("service_name", "Service status"),
                "message": event.message,
                "service_id": details.get("service_id", "unknown"),
                "service_name": details.get("service_name", "Unknown service"),
                "component_name": details.get("component_name"),
            }
        )

    return {
        "overall_status": overall_status,
        "overall_message": overall_messages[overall_status],
        "generated_at": now,
        "services": services,
        "recent_incidents": recent_incidents,
    }


def run_llm_probe() -> None:
    """Probe the active LLM backend and persist the latest result."""
    db = database.SessionLocal()
    try:
        settings = db.query(models.Settings).first()
        if settings is None:
            settings = models.Settings()
            db.add(settings)
            db.commit()
            db.refresh(settings)

        previous_status = settings.llm_status
        now = utcnow()

        try:
            service = llm_service.LLMService(db=db)
            success, message = service.test_connection()
        except Exception as exc:
            success = False
            message = f"LLM connection failed: {exc}"

        new_status = STATUS_OPERATIONAL if success else STATUS_OUTAGE
        settings.llm_status = new_status
        settings.llm_status_checked_at = now
        settings.llm_status_message = message
        db.commit()

        log_status_transition(
            service_id="llm",
            service_name="LLM",
            component_id="llm-active-backend",
            component_name="LLM backend",
            previous_status=previous_status,
            new_status=new_status,
            message=message,
        )
    finally:
        db.close()


def llm_status_probe_loop() -> None:
    """Continuously refresh the persisted LLM status."""
    while True:
        try:
            run_llm_probe()
        except Exception as exc:
            event_logger.log(models.EventType.ERROR, "LLMStatusProbe", f"Background LLM probe failed: {exc}")
        time.sleep(LLM_PROBE_INTERVAL_SECONDS)
