"""Owner-scoped planner account reads and mutations (JAN-288).

Every helper takes the owner's id and filters on it in the same query, so a
foreign account answers exactly like a missing one (see Tenant Isolation in
``docs/docs/tech-spec/auth-security.md``). ``api/planner.py`` calls these
instead of building ``PlannerAccount`` queries inline.
"""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from janus import models, security
from janus.schemas import planner as planner_schemas

GOOGLE_CALENDAR_ACCOUNT_NAME = "Google Calendar"
GOOGLE_TASKS_ACCOUNT_NAME = "Google Tasks"
MICROSOFT_CALENDAR_ACCOUNT_NAME = "Outlook Calendar"

#: The (provider, name) pairs the Google OAuth flow creates for each user.
GOOGLE_ACCOUNTS = (
    (models.Provider.GOOGLE_CALENDAR, GOOGLE_CALENDAR_ACCOUNT_NAME),
    (models.Provider.GOOGLE_TASKS, GOOGLE_TASKS_ACCOUNT_NAME),
)


def list_accounts_for_user(db: Session, user_id: int) -> list[models.PlannerAccount]:
    """Returns ``user_id``'s planner accounts."""
    return db.query(models.PlannerAccount).filter(models.PlannerAccount.user_id == user_id).all()


def get_google_calendar_account_for_user(db: Session, user_id: int) -> models.PlannerAccount | None:
    """Returns ``user_id``'s connected Google Calendar account; ``None`` if there is none."""
    return (
        db.query(models.PlannerAccount)
        .filter(
            models.PlannerAccount.user_id == user_id,
            models.PlannerAccount.provider == models.Provider.GOOGLE_CALENDAR,
            models.PlannerAccount.name == GOOGLE_CALENDAR_ACCOUNT_NAME,
        )
        .first()
    )


def get_google_refresh_token_for_user(db: Session, user_id: int, client_id: str | None) -> str | None:
    """Returns the refresh token stored for ``user_id``'s Google Calendar account, if usable.

    Google returns a refresh token only on the first consent for a client, so a
    reconnect that gets none keeps the stored one (JAN-318). It is reused only
    when it was issued to the same OAuth ``client_id``; an unreadable token
    answers ``None``.
    """
    account = get_google_calendar_account_for_user(db, user_id)
    if account is None or not account.token:
        return None
    try:
        stored = json.loads(security.security_service.decrypt(account.token))
    except ValueError:
        return None
    if not isinstance(stored, dict) or stored.get("client_id") != client_id:
        return None
    return stored.get("refresh_token") or None


def upsert_google_accounts_for_user(db: Session, user_id: int, encrypted_token: str) -> None:
    """Creates or reactivates ``user_id``'s Google Calendar and Google Tasks accounts with the token; no commit."""
    for provider, account_name in GOOGLE_ACCOUNTS:
        account = (
            db.query(models.PlannerAccount)
            .filter(
                models.PlannerAccount.provider == provider,
                models.PlannerAccount.name == account_name,
                models.PlannerAccount.user_id == user_id,
            )
            .first()
        )
        if account is None:
            db.add(
                models.PlannerAccount(
                    name=account_name,
                    provider=provider,
                    token=encrypted_token,
                    is_active=True,
                    user_id=user_id,
                )
            )
        else:
            account.token = encrypted_token
            account.is_active = True


def calendar_options(calendars: list[dict], selected: set[str]) -> list[planner_schemas.GoogleCalendarOption]:
    """Builds the API-facing calendar list, annotated with the given selection.

    Google lists the primary calendar under its real id (the account's address), while
    the default selection syncs the ``primary`` alias, so the alias marks the calendar
    flagged ``primary`` as selected (JAN-221).
    """
    if "primary" in selected:
        selected = selected | {cal["id"] for cal in calendars if cal["primary"]}
    return [
        planner_schemas.GoogleCalendarOption(
            id=cal["id"],
            summary=cal["summary"],
            primary=cal["primary"],
            access_role=cal["access_role"],
            selected=cal["id"] in selected,
        )
        for cal in calendars
    ]


def get_microsoft_calendar_account_for_user(db: Session, user_id: int) -> models.PlannerAccount | None:
    """Returns ``user_id``'s Outlook calendar source; ``None`` if Microsoft is not connected."""
    return (
        db.query(models.PlannerAccount)
        .filter(
            models.PlannerAccount.user_id == user_id,
            models.PlannerAccount.provider == models.Provider.MICROSOFT_CALENDAR,
        )
        .first()
    )


def upsert_microsoft_calendar_account_for_user(db: Session, user_id: int) -> None:
    """Creates or reactivates ``user_id``'s Outlook calendar source; no commit.

    It holds no token of its own: the connection owns the sign-in.
    """
    account = get_microsoft_calendar_account_for_user(db, user_id)
    if account is None:
        db.add(
            models.PlannerAccount(
                name=MICROSOFT_CALENDAR_ACCOUNT_NAME,
                provider=models.Provider.MICROSOFT_CALENDAR,
                token="",
                is_active=True,
                user_id=user_id,
            )
        )
    else:
        account.is_active = True
