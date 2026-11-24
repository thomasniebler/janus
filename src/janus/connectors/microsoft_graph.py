"""Microsoft Graph client for Outlook mail/calendar and Teams chats.

A leaf module (see the import contracts): it knows nothing about Janus users, the
database, or how a token is obtained. The caller hands it ``token_provider``, a
callable returning a current delegated access token, and every request asks for a
fresh one so expiry and refresh stay the credential's problem.

Responses are trimmed to the few fields an agent needs. That keeps tool output small
and avoids passing whole Graph payloads (and their tracking metadata) to the model.
"""

from __future__ import annotations

import html
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

import httpx

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
GRAPH_SCOPE_PREFIX = "https://graph.microsoft.com/"

_MAX_RETRY_AFTER_SECONDS = 10.0
_MESSAGE_SELECT = (
    "id,subject,from,toRecipients,receivedDateTime,isRead,bodyPreview,hasAttachments,conversationId,webLink"
)
_HIDDEN_RE = re.compile(
    r"<(script|style)\b[^>]*>.*?</\1\s*>|<(\w+)\b[^>]*display\s*:\s*none[^>]*>.*?</\2\s*>",
    re.IGNORECASE | re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")
_BLOCK_BREAK_RE = re.compile(r"</(p|div|li|tr)>|<br\s*/?>", re.IGNORECASE)


class GraphError(RuntimeError):
    """A Graph request failed. ``status_code`` is the HTTP status (0 for transport errors)."""

    def __init__(self, message: str, status_code: int = 0) -> None:
        """Keep the HTTP status next to the one-line message."""
        super().__init__(message)
        self.status_code = status_code


def _path_segment(value: str) -> str:
    """Quote one path segment; Graph ids and chat ids contain ``:``, ``@`` and ``=``."""
    cleaned = value.strip()
    if not cleaned or cleaned in {".", ".."}:
        # "." and ".." survive quoting and get normalized away by the HTTP client, re-targeting the request.
        raise ValueError("id must not be blank or a dot segment")
    return quote(cleaned, safe="")


def html_to_text(value: str) -> str:
    """Flatten the small HTML subset Teams and Outlook bodies use into plain text."""
    value = _HIDDEN_RE.sub("", value)  # invisible text must not reach the model while the user cannot see it
    text = _BLOCK_BREAK_RE.sub("\n", value)
    text = _TAG_RE.sub("", text)
    text = html.unescape(text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _address(entry: dict[str, Any] | None) -> dict[str, str | None]:
    email = (entry or {}).get("emailAddress") or {}
    return {"name": email.get("name"), "address": email.get("address")}


def _slim_message(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": raw.get("id"),
        "subject": raw.get("subject"),
        "from": _address(raw.get("from")),
        "to": [_address(item) for item in raw.get("toRecipients") or []],
        "received": raw.get("receivedDateTime"),
        "is_read": raw.get("isRead"),
        "has_attachments": raw.get("hasAttachments"),
        "conversation_id": raw.get("conversationId"),
        "preview": raw.get("bodyPreview"),
        "web_link": raw.get("webLink"),
    }


def _slim_event(raw: dict[str, Any]) -> dict[str, Any]:
    location = (raw.get("location") or {}).get("displayName")
    return {
        "id": raw.get("id"),
        "subject": raw.get("subject"),
        "start": (raw.get("start") or {}).get("dateTime"),
        "end": (raw.get("end") or {}).get("dateTime"),
        "time_zone": (raw.get("start") or {}).get("timeZone"),
        "location": location or None,
        "organizer": _address(raw.get("organizer")),
        "is_online_meeting": raw.get("isOnlineMeeting"),
        "is_cancelled": raw.get("isCancelled"),
        "show_as": raw.get("showAs"),
    }


def _slim_chat(raw: dict[str, Any]) -> dict[str, Any]:
    preview = raw.get("lastMessagePreview") or {}
    members = [member.get("displayName") for member in raw.get("members") or [] if isinstance(member, dict)]
    return {
        "id": raw.get("id"),
        "chat_type": raw.get("chatType"),
        "topic": raw.get("topic"),
        "members": [name for name in members if name],
        "last_message_at": preview.get("createdDateTime"),
        "last_message_preview": html_to_text((preview.get("body") or {}).get("content") or "") or None,
        "web_url": raw.get("webUrl"),
    }


def _slim_chat_message(raw: dict[str, Any]) -> dict[str, Any]:
    body = raw.get("body") or {}
    content = body.get("content") or ""
    if (body.get("contentType") or "").lower() == "html":
        content = html_to_text(content)
    sender = ((raw.get("from") or {}).get("user") or {}).get("displayName")
    return {
        "id": raw.get("id"),
        "created": raw.get("createdDateTime"),
        "from": sender,
        "message_type": raw.get("messageType"),
        "text": content,
        "deleted": raw.get("deletedDateTime") is not None,
    }


class MicrosoftGraphClient:
    """Small synchronous Graph client (Janus background work is thread-based, not async)."""

    def __init__(
        self,
        token_provider: Callable[[], str],
        *,
        base_url: str = GRAPH_BASE_URL,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        """Bind the token source; ``transport`` lets tests inject a mock."""
        self._token_provider = token_provider
        self._client = httpx.Client(base_url=base_url, timeout=timeout, transport=transport)

    def close(self) -> None:
        """Release the underlying connection pool."""
        self._client.close()

    def _send(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None,
        json: dict[str, Any] | None,
        headers: dict[str, str],
    ) -> httpx.Response:
        try:
            return self._client.request(method, path, params=params, json=json, headers=headers)
        except httpx.HTTPError as exc:
            raise GraphError(f"Microsoft Graph request failed: {exc.__class__.__name__}") from exc

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        request_headers = {"Authorization": f"Bearer {self._token_provider()}", **(headers or {})}
        response = self._send(method, path, params, json, request_headers)
        if response.status_code == 429:
            # One bounded retry on throttling; longer waits are surfaced instead of blocking a worker.
            delay = _retry_after_seconds(response)
            if delay is not None:
                time.sleep(delay)
                response = self._send(method, path, params, json, request_headers)
        if response.status_code >= 400:
            raise GraphError(_error_message(response), response.status_code)
        if response.status_code == 204 or not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise GraphError("Microsoft Graph returned a response that is not JSON", response.status_code) from exc

    def _paged(
        self, path: str, params: dict[str, Any], headers: dict[str, str] | None = None, *, max_pages: int = 10
    ) -> list[dict[str, Any]]:
        """Collects ``value`` items, following ``@odata.nextLink`` for at most ``max_pages`` pages."""
        items: list[dict[str, Any]] = []
        next_path, next_params = path, params
        for _ in range(max_pages):
            data = self._request("GET", next_path, params=next_params, headers=headers) or {}
            items.extend(data.get("value", []))
            next_link = data.get("@odata.nextLink")
            if not next_link:
                break
            next_path, next_params = next_link, None  # the link already carries the query
        return items

    # --- Outlook mail -------------------------------------------------------------

    def list_messages(
        self,
        *,
        folder: str = "inbox",
        top: int = 10,
        unread_only: bool = False,
        search: str | None = None,
    ) -> list[dict[str, Any]]:
        """List recent messages in a mail folder, newest first, or full-text search them."""
        params: dict[str, Any] = {"$top": _clamp(top, 1, 50), "$select": _MESSAGE_SELECT}
        if search and search.strip():
            # Graph rejects $orderby together with $search; relevance order is what search gives anyway.
            params["$search"] = f'"{search.strip().replace(chr(34), " ")}"'
        else:
            params["$orderby"] = "receivedDateTime desc"
            if unread_only:
                # Graph requires every $orderby property to also appear in $filter (else InefficientFilter).
                params["$filter"] = "isRead eq false and receivedDateTime ge 1900-01-01T00:00:00Z"
        data = self._request("GET", f"/me/mailFolders/{_path_segment(folder)}/messages", params=params)
        return [_slim_message(item) for item in (data or {}).get("value", [])]

    def list_inbox_since(
        self, since: datetime, *, newest_first: bool = False, max_pages: int = 10
    ) -> list[dict[str, Any]]:
        """Lists inbox messages received at or after ``since`` with plain-text bodies.

        Oldest first by default, newest first with ``newest_first`` (for a capped first sync).
        Returns Graph's own field names (``id``, ``internetMessageId``, ``subject``, ``from``,
        ``receivedDateTime``, ``hasAttachments``, ``body``), at most ``max_pages`` pages of 50.
        """
        stamp = since.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        return self._paged(
            "/me/mailFolders/inbox/messages",
            {
                "$filter": f"receivedDateTime ge {stamp}",
                "$orderby": f"receivedDateTime {'desc' if newest_first else 'asc'}",
                "$top": 50,
                "$select": "id,internetMessageId,subject,from,receivedDateTime,hasAttachments,body,bodyPreview",
            },
            {"Prefer": 'outlook.body-content-type="text"'},
            max_pages=max_pages,
        )

    def get_message(self, message_id: str, *, max_chars: int = 12000) -> dict[str, Any]:
        """Read one message including its plain-text body (truncated to ``max_chars``)."""
        data = self._request(
            "GET",
            f"/me/messages/{_path_segment(message_id)}",
            params={"$select": f"{_MESSAGE_SELECT},body,ccRecipients"},
            headers={"Prefer": 'outlook.body-content-type="text"'},
        )
        message = _slim_message(data)
        body = (data.get("body") or {}).get("content") or ""
        message["cc"] = [_address(item) for item in data.get("ccRecipients") or []]
        message["body"] = body[:max_chars]
        message["body_truncated"] = len(body) > max_chars
        return message

    def send_mail(self, *, to: list[str], subject: str, body: str, cc: list[str] | None = None) -> dict[str, Any]:
        """Send a plain-text mail from the signed-in user's mailbox."""
        payload = {
            "message": {
                "subject": subject,
                "body": {"contentType": "Text", "content": body},
                "toRecipients": [{"emailAddress": {"address": address}} for address in to],
                "ccRecipients": [{"emailAddress": {"address": address}} for address in cc or []],
            },
            "saveToSentItems": True,
        }
        self._request("POST", "/me/sendMail", json=payload)
        return {"status": "sent", "to": to, "cc": cc or [], "subject": subject}

    # --- Outlook calendar ---------------------------------------------------------

    def list_events(self, start: datetime, end: datetime, *, top: int = 25) -> list[dict[str, Any]]:
        """List calendar events overlapping ``[start, end]`` with recurring events expanded."""
        data = self._request(
            "GET",
            "/me/calendarView",
            params={
                "startDateTime": start.isoformat(),
                "endDateTime": end.isoformat(),
                "$top": _clamp(top, 1, 100),
                "$orderby": "start/dateTime",
                "$select": "id,subject,start,end,location,organizer,isOnlineMeeting,isCancelled,showAs",
            },
            headers={"Prefer": 'outlook.timezone="UTC"'},
        )
        return [_slim_event(item) for item in (data or {}).get("value", [])]

    def list_calendars(self) -> list[dict[str, Any]]:
        """List every calendar of the signed-in user (own, shared, delegated)."""
        items = self._paged("/me/calendars", {"$top": 100})
        return [
            {
                "id": item.get("id"),
                "name": item.get("name") or item.get("id"),
                "is_default": bool(item.get("isDefaultCalendar")),
                "can_edit": bool(item.get("canEdit")),
                "owner": (item.get("owner") or {}).get("address"),
            }
            for item in items
            if item.get("id")
        ]

    def list_calendar_events(
        self, calendar_id: str, start: datetime, end: datetime, *, top: int = 100
    ) -> list[dict[str, Any]]:
        """List one calendar's events overlapping ``[start, end]``, recurring ones expanded, in UTC."""
        items = self._paged(
            f"/me/calendars/{_path_segment(calendar_id)}/calendarView",
            {
                "startDateTime": start.isoformat(),
                "endDateTime": end.isoformat(),
                "$top": _clamp(top, 1, 200),
                "$orderby": "start/dateTime",
                "$select": "id,subject,start,end,location,isCancelled,isAllDay",
            },
            {"Prefer": 'outlook.timezone="UTC"'},
        )
        return [item for item in items if not item.get("isCancelled")]

    # --- Teams chats --------------------------------------------------------------

    def list_chats(self, *, top: int = 20) -> list[dict[str, Any]]:
        """List the user's chats (1:1, group, meeting), most recently active first."""
        data = self._request(
            "GET",
            "/me/chats",
            params={
                "$top": _clamp(top, 1, 50),
                "$expand": "members,lastMessagePreview",
                "$orderby": "lastMessagePreview/createdDateTime desc",
            },
        )
        return [_slim_chat(item) for item in (data or {}).get("value", [])]

    def list_chat_messages(self, chat_id: str, *, top: int = 20) -> list[dict[str, Any]]:
        """Read the newest messages of one chat, newest first."""
        data = self._request(
            "GET",
            f"/me/chats/{_path_segment(chat_id)}/messages",
            params={"$top": _clamp(top, 1, 50)},
        )
        return [_slim_chat_message(item) for item in (data or {}).get("value", [])]

    def send_chat_message(self, chat_id: str, text: str) -> dict[str, Any]:
        """Post a plain-text message into a chat as the signed-in user."""
        data = self._request(
            "POST",
            f"/me/chats/{_path_segment(chat_id)}/messages",
            json={"body": {"contentType": "text", "content": text}},
        )
        return {"status": "sent", "chat_id": chat_id, "message_id": (data or {}).get("id")}


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, int(value)))


def _retry_after_seconds(response: httpx.Response) -> float | None:
    try:
        delay = float(response.headers.get("Retry-After", ""))
    except ValueError:
        return None
    return delay if 0 <= delay <= _MAX_RETRY_AFTER_SECONDS else None


def _error_message(response: httpx.Response) -> str:
    """Turn a Graph error body into one actionable line, never echoing the whole payload."""
    code = ""
    detail = ""
    try:
        error = (response.json() or {}).get("error") or {}
        code = str(error.get("code") or "")
        detail = str(error.get("message") or "")
    except ValueError:
        pass
    hint = ""
    if response.status_code in (401, 403):
        hint = (
            " The Microsoft sign-in may have expired or lacks the delegated permission for this call;"
            " run `janus microsoft login` and check the app registration's consent."
        )
    elif response.status_code == 429:
        hint = " Microsoft Graph is throttling requests; try again later."
    return f"Microsoft Graph returned {response.status_code} {code}: {detail[:300]}.{hint}".replace(" : ", " ")
