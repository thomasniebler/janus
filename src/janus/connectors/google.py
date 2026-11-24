"""Google planner connectors for calendar and task integrations."""

import json
from datetime import UTC, datetime

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from janus import models, security
from janus.connectors import base, google_endpoints


def _parse_google_datetime(value: str | None) -> datetime | None:
    """Parses a Google RFC3339 or date-only timestamp."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.fromisoformat(value)


def _rfc3339_utc(value: datetime) -> str:
    """Formats a datetime as the UTC RFC3339 string Google expects (naive means UTC)."""
    value = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


#: Each API's discovery ``servicePath``: Calendar's method paths are relative to
#: ``calendar/v3/``, while Tasks' already start with ``tasks/v1/``.
_SERVICE_PATHS = {("calendar", "v3"): "calendar/v3", ("tasks", "v1"): ""}


def _build_service(api: str, version: str, creds: Credentials):
    """Builds a Google API client, pointed at the e2e fake Google when one is configured."""
    client_options = google_endpoints.api_client_options(_SERVICE_PATHS[(api, version)])
    return build(api, version, credentials=creds, client_options=client_options)


class GoogleCalendarConnector(base.Connector):
    """Connector for Google Calendar events."""

    def _get_credentials(self) -> Credentials:
        """Returns Google OAuth credentials and refreshes them when possible."""
        creds_data = json.loads(self.decrypted_token)
        creds = google_endpoints.with_fake_token_uri(Credentials.from_authorized_user_info(creds_data))
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            self.account.token = security.security_service.encrypt(creds.to_json())
        return creds

    def fetch_tasks(self) -> list[dict]:
        """Google Calendar doesn't provide tasks."""
        return []

    def selected_calendar_ids(self) -> list[str]:
        """Returns the calendar IDs configured for sync, defaulting to ``primary``."""
        configured = getattr(self.account, "selected_calendar_ids", None)
        return list(configured) if configured else ["primary"]

    def list_calendars(self) -> list[dict]:
        """Lists calendars visible to this account (owned, shared, and subscribed).

        Returns:
            List[dict]: Each entry has ``id``, ``summary``, ``primary``, and ``access_role``.
        """
        creds = self._get_credentials()
        service = _build_service("calendar", "v3", creds)
        calendars: list[dict] = []
        page_token = None
        while True:
            page = service.calendarList().list(pageToken=page_token).execute()
            for entry in page.get("items", []):
                calendars.append(
                    {
                        "id": entry["id"],
                        "summary": entry.get("summary", entry["id"]),
                        "primary": bool(entry.get("primary", False)),
                        "access_role": entry.get("accessRole", "reader"),
                    }
                )
            page_token = page.get("nextPageToken")
            if not page_token:
                break
        return calendars

    def fetch_events(self, start_time: datetime, end_time: datetime) -> list[dict]:
        """Fetch Google Calendar events across the configured calendars for a time range.

        Args:
            start_time (datetime): The start time for the range.
            end_time (datetime): The end time for the range.

        Returns:
            List[dict]: A list of dictionary representations of calendar events. Events are
                tagged with a calendar-qualified ``external_id`` so the same event id from two
                different calendars can't collide.
        """
        creds = self._get_credentials()
        service = _build_service("calendar", "v3", creds)
        results = []

        for calendar_id in self.selected_calendar_ids():
            events_result = (
                service.events()
                .list(
                    calendarId=calendar_id,
                    timeMin=_rfc3339_utc(start_time),
                    timeMax=_rfc3339_utc(end_time),
                    singleEvents=True,
                    orderBy="startTime",
                )
                .execute()
            )

            for event in events_result.get("items", []):
                start = event["start"].get("dateTime", event["start"].get("date"))
                end = event["end"].get("dateTime", event["end"].get("date"))

                results.append(
                    {
                        "external_id": f"{calendar_id}:{event['id']}",
                        "summary": event.get("summary", "No Title"),
                        "start_time": _parse_google_datetime(start),
                        "end_time": _parse_google_datetime(end),
                        "location": event.get("location", ""),
                    }
                )
        return results

    def test_connection(self) -> tuple[bool, str]:
        """Tests the connection to Google Calendar."""
        try:
            creds = self._get_credentials()
            service = _build_service("calendar", "v3", creds)
            # Try to get calendar details to verify credentials and connection
            service.calendars().get(calendarId="primary").execute()
            return True, "Successfully connected to Google Calendar."
        except Exception as e:
            return False, f"Google Calendar connection failed: {e}"


class GoogleTasksConnector(GoogleCalendarConnector):
    """Connector for Google Tasks."""

    def fetch_tasks(self) -> list[dict]:
        """Fetch active tasks across all Google Task lists."""
        creds = self._get_credentials()
        service = _build_service("tasks", "v1", creds)

        tasklists = service.tasklists().list().execute().get("items", [])
        results = []
        for tasklist in tasklists:
            tasks = (
                service.tasks()
                .list(tasklist=tasklist["id"], showCompleted=False, showDeleted=False, showHidden=False)
                .execute()
                .get("items", [])
            )
            for task in tasks:
                results.append(
                    {
                        "external_id": task["id"],
                        "title": task.get("title", "Untitled Task"),
                        "status": (
                            models.TaskStatus.DONE if task.get("status") == "completed" else models.TaskStatus.TODO
                        ),
                        "due_date": _parse_google_datetime(task.get("due")),
                    }
                )
        return results

    def fetch_events(self, start_time: datetime, end_time: datetime) -> list[dict]:
        """Google Tasks does not provide calendar events."""
        return []

    def test_connection(self) -> tuple[bool, str]:
        """Tests the connection to Google Tasks."""
        try:
            creds = self._get_credentials()
            service = _build_service("tasks", "v1", creds)
            service.tasklists().list(maxResults=1).execute()
            return True, "Successfully connected to Google Tasks."
        except Exception as e:
            return False, f"Google Tasks connection failed: {e}"
