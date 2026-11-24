"""Health and worker status endpoints."""

from datetime import UTC, datetime, timedelta
from importlib.metadata import PackageNotFoundError, version

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from janus import database, models, security, status_service
from janus.schemas import status as status_schemas
from janus.services import changelog_service
from janus.worker_manager import worker_manager

router = APIRouter(prefix="/api/status", tags=["status"])

_HEARTBEAT_TIMEOUT = timedelta(minutes=2)


def _janus_version() -> str:
    try:
        return version("janus")
    except PackageNotFoundError:
        return "unknown"


@router.get("/health")
async def health(response: Response, db: Session = Depends(database.get_db)):
    """Returns 200 if all active IMAP monitors are healthy, 500 otherwise."""
    now = datetime.now(UTC)
    accounts = db.query(models.EmailAccount).filter(models.EmailAccount.is_active).all()

    unhealthy = []
    for acc in accounts:
        if acc.last_heartbeat is None:
            # Never sent a heartbeat yet — give it a grace window
            continue
        # last_heartbeat may be stored as naive UTC; normalise
        hb = acc.last_heartbeat
        if hb.tzinfo is None:
            hb = hb.replace(tzinfo=UTC)
        if now - hb > _HEARTBEAT_TIMEOUT:
            unhealthy.append(acc.email)

    if unhealthy:
        response.status_code = 500
        return {"status": "degraded", "unhealthy_accounts": unhealthy, "version": _janus_version()}

    return {"status": "ok", "version": _janus_version()}


@router.get("/workers")
async def workers(
    db: Session = Depends(database.get_db),
    _user: models.User = Depends(security.get_current_user),
):
    """Returns live state for all IMAP monitors and planner accounts."""
    manager_status = worker_manager.status()

    imap_rows = []
    for acc in db.query(models.EmailAccount).all():
        key = f"imap:{acc.email}"
        ws = manager_status.get(key, {})
        hb = acc.last_heartbeat
        imap_rows.append(
            {
                "account_id": acc.id,
                "email": acc.email,
                "is_active": acc.is_active,
                "monitor_status": acc.monitor_status.value if acc.monitor_status else None,
                "last_heartbeat": hb.isoformat() if hb else None,
                "last_error": acc.last_error,
                "restart_count": ws.get("restart_count", 0),
                "thread_alive": ws.get("alive", False),
            }
        )

    planner_rows = []
    for pa in db.query(models.PlannerAccount).all():
        sat = pa.last_sync_at
        planner_rows.append(
            {
                "account_id": pa.id,
                "name": pa.name,
                "provider": pa.provider.value if pa.provider else None,
                "is_active": pa.is_active,
                "last_sync_at": sat.isoformat() if sat else None,
                "last_sync_error": pa.last_sync_error,
            }
        )

    return {"imap_monitors": imap_rows, "planner_accounts": planner_rows}


@router.get("/summary", response_model=status_schemas.StatusOverview)
async def summary(
    db: Session = Depends(database.get_db),
    _user: models.User = Depends(security.get_current_user),
):
    """Returns the normalized status overview used by the dashboard status page."""
    return status_service.get_status_overview(db)


@router.get("/version", response_model=status_schemas.VersionInfo)
def version_info(
    since: str | None = None,
    _user: models.User = Depends(security.get_current_user),
):
    """Returns the running version, the changelog links and the release notes after ``since``.

    ``since`` is the last version the client showed; without it no notes are returned.
    """
    current = _janus_version()
    releases = changelog_service.releases_since(changelog_service.load_releases(), since, current)
    return status_schemas.VersionInfo(
        version=current,
        changelog_url=changelog_service.CHANGELOG_DOCS_PATH,
        release_url=f"{changelog_service.releases_url()}/tag/v{current}",
        releases=[
            status_schemas.ReleaseNotes(
                version=release.version,
                date=release.date,
                groups=[
                    status_schemas.ReleaseChangeGroup(title=title, items=items)
                    for title, items in release.groups.items()
                ],
            )
            for release in releases
        ],
    )
