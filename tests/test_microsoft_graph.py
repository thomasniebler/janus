"""Microsoft Graph connector: request shaping, trimming, and error handling (mock transport, no network)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest

from janus.connectors.microsoft_graph import GraphError, MicrosoftGraphClient, html_to_text


def _client(handler, token="tok-1"):
    tokens = iter([token] if isinstance(token, str) else token)
    calls = {"count": 0}

    def provider():
        calls["count"] += 1
        return next(tokens, "tok-last")

    client = MicrosoftGraphClient(provider, transport=httpx.MockTransport(handler))
    return client, calls


def test_list_messages_orders_newest_first_and_filters_unread():
    """List messages orders newest first and filters unread."""
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["request"] = request
        return httpx.Response(
            200,
            json={
                "value": [
                    {
                        "id": "m1",
                        "subject": "Hello",
                        "from": {"emailAddress": {"name": "Ann", "address": "ann@example.com"}},
                        "toRecipients": [{"emailAddress": {"address": "me@example.com"}}],
                        "receivedDateTime": "2026-11-10T08:00:00Z",
                        "isRead": False,
                        "bodyPreview": "hi",
                        "internetMessageHeaders": [{"name": "X-Tracking", "value": "drop me"}],
                    }
                ]
            },
        )

    client, _ = _client(handler)
    messages = client.list_messages(unread_only=True, top=5)

    request = seen["request"]
    assert request.url.path == "/v1.0/me/mailFolders/inbox/messages"
    assert request.headers["Authorization"] == "Bearer tok-1"
    assert request.url.params["$orderby"] == "receivedDateTime desc"
    assert request.url.params["$filter"] == "isRead eq false and receivedDateTime ge 1900-01-01T00:00:00Z"
    assert request.url.params["$top"] == "5"
    assert messages == [
        {
            "id": "m1",
            "subject": "Hello",
            "from": {"name": "Ann", "address": "ann@example.com"},
            "to": [{"name": None, "address": "me@example.com"}],
            "received": "2026-11-10T08:00:00Z",
            "is_read": False,
            "has_attachments": None,
            "conversation_id": None,
            "preview": "hi",
            "web_link": None,
        }
    ]


def test_search_drops_orderby_and_quotes_the_query():
    """Graph rejects $orderby with $search, and an embedded quote must not break out of the phrase."""
    seen = {}

    def handler(request):
        seen["params"] = request.url.params
        return httpx.Response(200, json={"value": []})

    client, _ = _client(handler)
    client.list_messages(search='invoice "Q3"', unread_only=True)

    assert seen["params"]["$search"] == '"invoice  Q3 "'
    assert "$orderby" not in seen["params"]
    assert "$filter" not in seen["params"]


def test_top_is_clamped():
    """Top is clamped."""
    seen = []

    def handler(request):
        seen.append(request.url.params["$top"])
        return httpx.Response(200, json={"value": []})

    client, _ = _client(handler)
    client.list_messages(top=10_000)
    client.list_messages(top=-3)

    assert seen == ["50", "1"]


def test_get_message_requests_text_body_and_truncates():
    """Get message requests text body and truncates."""
    seen = {}

    def handler(request):
        seen["prefer"] = request.headers["Prefer"]
        return httpx.Response(
            200,
            json={"id": "m1", "subject": "S", "body": {"contentType": "text", "content": "x" * 50}, "ccRecipients": []},
        )

    client, _ = _client(handler)
    message = client.get_message("m1", max_chars=10)

    assert seen["prefer"] == 'outlook.body-content-type="text"'
    assert message["body"] == "x" * 10
    assert message["body_truncated"] is True


def test_ids_are_quoted_as_single_path_segments():
    """Chat ids contain ':' and '@'; a hostile id must not add path segments."""
    paths = []

    def handler(request):
        paths.append(request.url.raw_path.decode().split("?")[0])
        return httpx.Response(200, json={"value": []})

    client, _ = _client(handler)
    client.list_chat_messages("19:abc@unq.gbl.spaces")
    client.list_chat_messages("../../me/drive")

    assert paths[0] == "/v1.0/me/chats/19%3Aabc%40unq.gbl.spaces/messages"
    assert paths[1] == "/v1.0/me/chats/..%2F..%2Fme%2Fdrive/messages"
    with pytest.raises(ValueError):
        client.list_chat_messages("   ")


def test_send_mail_posts_message_and_saves_to_sent_items():
    """Send mail posts message and saves to sent items."""
    seen = {}

    def handler(request):
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["json"] = request.read()
        return httpx.Response(202)

    client, _ = _client(handler)
    result = client.send_mail(to=["a@example.com"], subject="Hi", body="Body", cc=["c@example.com"])

    assert (seen["method"], seen["path"]) == ("POST", "/v1.0/me/sendMail")
    payload = json.loads(seen["json"])
    assert payload["saveToSentItems"] is True
    assert payload["message"]["body"] == {"contentType": "Text", "content": "Body"}
    assert payload["message"]["toRecipients"] == [{"emailAddress": {"address": "a@example.com"}}]
    assert payload["message"]["ccRecipients"] == [{"emailAddress": {"address": "c@example.com"}}]
    assert result == {"status": "sent", "to": ["a@example.com"], "cc": ["c@example.com"], "subject": "Hi"}


def test_list_events_uses_calendar_view_in_utc():
    """List events uses calendar view in utc."""
    seen = {}

    def handler(request):
        seen["params"] = request.url.params
        seen["prefer"] = request.headers["Prefer"]
        return httpx.Response(
            200,
            json={
                "value": [
                    {
                        "id": "e1",
                        "subject": "Standup",
                        "start": {"dateTime": "2026-11-10T09:00:00", "timeZone": "UTC"},
                        "end": {"dateTime": "2026-11-10T09:15:00", "timeZone": "UTC"},
                        "location": {"displayName": ""},
                        "organizer": {"emailAddress": {"name": "Bo", "address": "bo@example.com"}},
                    }
                ]
            },
        )

    client, _ = _client(handler)
    events = client.list_events(datetime(2026, 11, 10, tzinfo=UTC), datetime(2026, 11, 17, tzinfo=UTC))

    assert seen["params"]["startDateTime"] == "2026-11-10T00:00:00+00:00"
    assert seen["prefer"] == 'outlook.timezone="UTC"'
    assert events[0]["subject"] == "Standup"
    assert events[0]["location"] is None
    assert events[0]["organizer"] == {"name": "Bo", "address": "bo@example.com"}


def test_list_chats_expands_members_and_flattens_preview_html():
    """List chats expands members and flattens preview html."""
    seen = {}

    def handler(request):
        seen["params"] = request.url.params
        return httpx.Response(
            200,
            json={
                "value": [
                    {
                        "id": "19:x@unq.gbl.spaces",
                        "chatType": "oneOnOne",
                        "topic": None,
                        "members": [{"displayName": "Ann"}, {"displayName": "Me"}],
                        "lastMessagePreview": {
                            "createdDateTime": "2026-11-10T07:00:00Z",
                            "body": {"content": "<p>see&nbsp;you<br>soon</p>"},
                        },
                    }
                ]
            },
        )

    client, _ = _client(handler)
    chats = client.list_chats(top=3)

    assert seen["params"]["$expand"] == "members,lastMessagePreview"
    assert chats[0]["members"] == ["Ann", "Me"]
    assert chats[0]["last_message_preview"] == "see\xa0you\nsoon"


def test_chat_messages_html_is_flattened_and_text_is_kept():
    """Chat messages html is flattened and text is kept."""

    def handler(request):
        return httpx.Response(
            200,
            json={
                "value": [
                    {
                        "id": "1",
                        "body": {"contentType": "html", "content": "<div>Hello <b>there</b></div>"},
                        "from": {"user": {"displayName": "Ann"}},
                        "messageType": "message",
                    },
                    {"id": "2", "body": {"contentType": "text", "content": "a < b"}, "from": None},
                ]
            },
        )

    client, _ = _client(handler)
    first, second = client.list_chat_messages("c1")

    assert first["text"] == "Hello there"
    assert first["from"] == "Ann"
    assert second["text"] == "a < b"
    assert second["from"] is None


def test_send_chat_message_posts_plain_text():
    """Send chat message posts plain text."""
    seen = {}

    def handler(request):
        seen["json"] = request.read()
        return httpx.Response(201, json={"id": "msg-9"})

    client, _ = _client(handler)
    result = client.send_chat_message("c1", "hi there")

    assert json.loads(seen["json"]) == {"body": {"contentType": "text", "content": "hi there"}}
    assert result == {"status": "sent", "chat_id": "c1", "message_id": "msg-9"}


def test_a_fresh_token_is_requested_for_every_call():
    """A fresh token is requested for every call."""

    def handler(request):
        return httpx.Response(200, json={"value": []})

    client, calls = _client(handler, token=["t1", "t2"])
    client.list_chats()
    client.list_chats()

    assert calls["count"] == 2


@pytest.mark.parametrize("status", [401, 403])
def test_auth_failures_point_at_login_and_consent(status):
    """Auth failures point at login and consent."""

    def handler(request):
        return httpx.Response(status, json={"error": {"code": "Forbidden", "message": "Insufficient privileges"}})

    client, _ = _client(handler)
    with pytest.raises(GraphError) as excinfo:
        client.list_chats()

    assert excinfo.value.status_code == status
    assert "Insufficient privileges" in str(excinfo.value)
    assert "janus microsoft login" in str(excinfo.value)


def test_non_json_error_body_is_not_echoed():
    """Non json error body is not echoed."""

    def handler(request):
        return httpx.Response(500, text="<html>secret stack trace</html>")

    client, _ = _client(handler)
    with pytest.raises(GraphError) as excinfo:
        client.list_chats()

    assert "secret stack trace" not in str(excinfo.value)
    assert excinfo.value.status_code == 500


def test_throttling_retries_once_when_retry_after_is_short(monkeypatch):
    """Throttling retries once when retry after is short."""
    monkeypatch.setattr("janus.connectors.microsoft_graph.time.sleep", lambda _: None)
    attempts = []

    def handler(request):
        attempts.append(1)
        if len(attempts) == 1:
            return httpx.Response(429, headers={"Retry-After": "1"})
        return httpx.Response(200, json={"value": []})

    client, _ = _client(handler)

    assert client.list_chats() == []
    assert len(attempts) == 2


def test_throttling_is_surfaced_when_retry_after_is_long_or_repeats(monkeypatch):
    """Throttling is surfaced when retry after is long or repeats."""
    monkeypatch.setattr("janus.connectors.microsoft_graph.time.sleep", lambda _: None)
    attempts = []

    def long_wait(request):
        attempts.append(1)
        return httpx.Response(429, headers={"Retry-After": "120"})

    client, _ = _client(long_wait)
    with pytest.raises(GraphError) as excinfo:
        client.list_chats()
    assert excinfo.value.status_code == 429
    assert len(attempts) == 1

    attempts.clear()

    def repeats(request):
        attempts.append(1)
        return httpx.Response(429, headers={"Retry-After": "1"})

    client, _ = _client(repeats)
    with pytest.raises(GraphError):
        client.list_chats()
    assert len(attempts) == 2


def test_transport_errors_become_graph_errors():
    """Transport errors become graph errors."""

    def handler(request):
        raise httpx.ConnectError("boom")

    client, _ = _client(handler)
    with pytest.raises(GraphError) as excinfo:
        client.list_chats()

    assert excinfo.value.status_code == 0
    assert "ConnectError" in str(excinfo.value)


def test_html_to_text_handles_blocks_entities_and_blank_runs():
    """Html to text handles blocks entities and blank runs."""
    assert html_to_text("<p>a</p><p>b</p><div></div><div></div><p>c</p>") == "a\nb\n\nc"
    assert html_to_text("Tom &amp; Jerry") == "Tom & Jerry"


def test_unread_filter_includes_the_orderby_property():
    """Graph rejects $orderby on a property missing from $filter (InefficientFilter)."""
    seen = {}

    def handler(request):
        seen["params"] = request.url.params
        return httpx.Response(200, json={"value": []})

    client, _ = _client(handler)
    client.list_messages(unread_only=True)

    assert "receivedDateTime" in seen["params"]["$filter"]
    assert seen["params"]["$filter"].startswith("isRead eq false")


def test_list_chats_expands_the_last_message_preview():
    """LastMessagePreview is a navigation property and is only returned when expanded."""
    seen = {}

    def handler(request):
        seen["params"] = request.url.params
        return httpx.Response(200, json={"value": []})

    client, _ = _client(handler)
    client.list_chats()

    assert seen["params"]["$expand"] == "members,lastMessagePreview"


@pytest.mark.parametrize("bad", [".", ".."])
def test_dot_segment_ids_are_rejected(bad):
    """'..' survives quoting and would be normalized away by the HTTP client, re-targeting the call."""
    client, _ = _client(lambda request: httpx.Response(200, json={}))
    with pytest.raises(ValueError):
        client.get_message(bad)
    with pytest.raises(ValueError):
        client.send_chat_message(bad, "x")


def test_html_to_text_drops_hidden_content():
    """Script/style blocks and display:none elements must not reach the model."""
    html_in = '<p>shown</p><style>p{}</style><script>evil()</script><div style="display:none">hidden</div><p>end</p>'
    assert html_to_text(html_in) == "shown\nend"


def test_non_json_success_body_becomes_graph_error():
    """A 200 with a non-JSON body is a GraphError, not a bare ValueError."""
    client, _ = _client(lambda request: httpx.Response(200, text="<html>proxy</html>"))
    with pytest.raises(GraphError):
        client.list_chats()
