"""Outlook / Microsoft 365 calendar connector for the planner sync.

A leaf adapter: the caller injects ``client_factory``, a callable returning a
``MicrosoftGraphClient`` for the account owner's connected Microsoft account, so this module
knows nothing about connections or tokens.
"""

from collections.abc import Callable
from datetime import UTC, datetime

from janus import models
from janus.connectors import base
from janus.connectors.microsoft_graph import MicrosoftGraphClient


def _parse_graph_datetime(value: dict | None) -> datetime | None:
    """Parses Graph's ``{"dateTime": "2026-11-12T10:00:00.0000000"}`` (requested in UTC)."""
    raw = (value or {}).get("dateTime")
    if not raw:
        return None
    head, _, fraction = raw.partition(".")
    parsed = datetime.fromisoformat(f"{head}.{fraction[:6]}" if fraction else head)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


class MicrosoftCalendarConnector(base.Connector):
    """Connector for the selected Outlook calendars of a connected Microsoft account."""

    def __init__(self, account: models.PlannerAccount, client_factory: Callable[[], MicrosoftGraphClient]) -> None:
        """Binds the account and the Graph client source."""
        super().__init__(account)
        self._client_factory = client_factory

    def selected_calendar_ids(self) -> list[str]:
        """Returns the configured calendar ids; empty means the default calendar."""
        configured = getattr(self.account, "selected_calendar_ids", None)
        return list(configured) if configured else []

    def list_calendars(self) -> list[dict]:
        """Lists the user's Outlook calendars."""
        client = self._client_factory()
        try:
            return client.list_calendars()
        finally:
            client.close()

    def fetch_tasks(self) -> list[dict]:
        """Outlook calendars carry no tasks."""
        return []

    def fetch_events(self, start_time: datetime, end_time: datetime) -> list[dict]:
        """Fetches events of the selected calendars (the default one when none is selected)."""
        client = self._client_factory()
        try:
            calendar_ids = self.selected_calendar_ids()
            if not calendar_ids:
                calendar_ids = [cal["id"] for cal in client.list_calendars() if cal["is_default"]][:1]
            results: list[dict] = []
            for calendar_id in calendar_ids:
                for event in client.list_calendar_events(calendar_id, start_time, end_time):
                    results.append(
                        {
                            "external_id": f"{calendar_id}:{event['id']}",
                            "summary": event.get("subject") or "No Title",
                            "start_time": _parse_graph_datetime(event.get("start")),
                            "end_time": _parse_graph_datetime(event.get("end")),
                            "location": (event.get("location") or {}).get("displayName") or "",
                        }
                    )
            return results
        finally:
            client.close()

    def test_connection(self) -> tuple[bool, str]:
        """Tests the connection by listing calendars."""
        try:
            count = len(self.list_calendars())
        except Exception as exc:
            return False, f"Outlook calendar connection failed: {exc}"
        return True, f"Connected to Outlook ({count} calendars)."
