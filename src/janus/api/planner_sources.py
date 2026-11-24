"""Planner sources: calendar selection (one route pair for every connected provider) and on-demand sync."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from janus import database, models, security
from janus.connectors import google as google_connector
from janus.connectors import microsoft_calendar
from janus.planner_service import PlannerService
from janus.schemas import connections as connection_schemas
from janus.schemas import planner as planner_schemas
from janus.services import microsoft_connect_service, oauth_connection_service, planner_account_service

router = APIRouter(prefix="/api/planner", tags=["planner"])


@dataclass(frozen=True)
class CalendarSource:
    """How one provider's calendars are listed, shown and chosen; the routes below serve every provider."""

    label: str
    get_account: Callable[[Session, int], models.PlannerAccount | None]
    #: ``connector(db, user_id, account)``; both connectors expose ``list_calendars``/``selected_calendar_ids``.
    connector: Callable[[Session, int, models.PlannerAccount], Any]
    #: Annotates the provider's calendars with the selection for the API.
    options: Callable[[list[dict], set[str]], list[planner_schemas.GoogleCalendarOption]]
    #: The calendars synced while nothing was chosen explicitly.
    default_selection: Callable[[list[dict]], set[str]]
    #: The part of a requested selection that exists; an empty result is rejected.
    chosen: Callable[[list[str], list[dict]], list[str]]


def _outlook_connector(db: Session, user_id: int, account: models.PlannerAccount):
    connection = oauth_connection_service.get_connection(db, user_id, oauth_connection_service.MICROSOFT)
    if connection is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Microsoft is not connected.")
    return microsoft_calendar.MicrosoftCalendarConnector(
        account, lambda: microsoft_connect_service.graph_client(db, connection)
    )


def _outlook_options(calendars: list[dict], selected: set[str]) -> list[planner_schemas.GoogleCalendarOption]:
    return [
        planner_schemas.GoogleCalendarOption(
            id=cal["id"],
            summary=cal["name"],
            primary=cal["is_default"],
            access_role="owner" if cal["can_edit"] else "reader",
            selected=cal["id"] in (selected or {c["id"] for c in calendars if c["is_default"]}),
        )
        for cal in calendars
    ]


SOURCES: dict[str, CalendarSource] = {
    oauth_connection_service.GOOGLE: CalendarSource(
        label="Google",
        get_account=planner_account_service.get_google_calendar_account_for_user,
        connector=lambda db, user_id, account: google_connector.GoogleCalendarConnector(account),
        options=planner_account_service.calendar_options,
        default_selection=lambda calendars: {"primary"},
        chosen=lambda requested, calendars: requested,
    ),
    oauth_connection_service.MICROSOFT: CalendarSource(
        label="Microsoft",
        get_account=planner_account_service.get_microsoft_calendar_account_for_user,
        connector=_outlook_connector,
        options=_outlook_options,
        default_selection=lambda calendars: {cal["id"] for cal in calendars if cal["is_default"]},
        chosen=lambda requested, calendars: [c for c in requested if c in {cal["id"] for cal in calendars}],
    ),
}


def _source(db: Session, user: models.User, provider: str):
    """Returns ``(source, account, connector)`` for ``provider``, or raises 404 when it is not connected."""
    source = SOURCES.get(provider)
    if source is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown provider.")
    account = source.get_account(db, user.id)
    if account is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{source.label} is not connected.")
    return source, account, source.connector(db, user.id, account)


def _list_calendars(source: CalendarSource, connector) -> list[dict]:
    try:
        return connector.list_calendars()
    except Exception as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Failed to list {source.label} calendars: {exc}") from exc


@router.get("/{provider}/calendars", response_model=list[planner_schemas.GoogleCalendarOption])
def list_calendars(
    provider: str,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Lists the connected account's calendars (owned, shared, subscribed) with the current selection."""
    source, _, connector = _source(db, user, provider)
    return source.options(_list_calendars(source, connector), set(connector.selected_calendar_ids()))


@router.put("/{provider}/calendars", response_model=list[planner_schemas.GoogleCalendarOption])
def update_calendars(
    provider: str,
    payload: planner_schemas.GoogleCalendarSelectionUpdate,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Sets which calendars Janus syncs; deselected calendars lose their imported events.

    The provider is asked first, so a listing failure (expired token, outage) never leaves a
    selection persisted that the caller was told failed to save.
    """
    source, account, connector = _source(db, user, provider)
    calendars = _list_calendars(source, connector)
    chosen = source.chosen(payload.calendar_ids, calendars)
    if not chosen:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Select at least one existing calendar.")
    previous = set(connector.selected_calendar_ids()) or source.default_selection(calendars)
    for calendar_id in previous - set(chosen):
        db.query(models.CalendarEvent).filter(
            models.CalendarEvent.planner_account_id == account.id,
            models.CalendarEvent.external_id.like(f"{calendar_id}:%"),
        ).delete(synchronize_session=False)
    account.selected_calendar_ids = chosen
    db.commit()
    return source.options(calendars, set(chosen))


@router.post("/sync", response_model=connection_schemas.SyncResult)
def sync_now(
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Syncs the current user's planner sources right away instead of waiting for the 30-minute cycle."""
    accounts = [a for a in planner_account_service.list_accounts_for_user(db, user.id) if a.is_active]
    PlannerService(db).sync_all(user_id=user.id)
    db.expire_all()
    refreshed = [a for a in planner_account_service.list_accounts_for_user(db, user.id) if a.is_active]
    errors = [f"{a.name}: {a.last_sync_error}" for a in refreshed if a.last_sync_error]
    return connection_schemas.SyncResult(synced_accounts=len(accounts), errors=errors)
