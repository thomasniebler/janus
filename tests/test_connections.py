"""Connected Google and Microsoft accounts: status, mailboxes, Outlook calendars, disconnect."""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from janus import config, models, security
from janus.connectors import microsoft_calendar
from janus.services import (
    google_client,
    mailbox_oauth_service,
    microsoft_connect_service,
    oauth_connection_service,
    planner_account_service,
)

_CALENDARS = [
    {"id": "cal-default", "name": "Calendar", "is_default": True, "can_edit": True},
    {"id": "cal-team", "name": "Team", "is_default": False, "can_edit": False},
]


@pytest.fixture(autouse=True)
def _providers_unconfigured(monkeypatch):
    for key in ("google_client_id", "google_client_secret", "microsoft_client_id", "microsoft_tenant_id"):
        monkeypatch.setattr(config.settings, key, "")


@pytest.fixture
def auth_client(client, db):
    """A TestClient signed in as an admin."""
    user = models.User(
        username="admin", hashed_password=security.security_service.get_password_hash("admin"), is_admin=True
    )
    db.add(user)
    db.commit()
    response = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    client.headers[security.CSRF_HEADER_NAME] = response.json()["csrf_token"]
    return client, user


def _connect(db, user, provider, scopes, email="me@example.com"):
    connection = oauth_connection_service.upsert_connection(
        db, user.id, provider, account_email=email, credentials="{}", scopes=scopes
    )
    db.commit()
    return connection


def test_overview_reports_unconfigured_and_unconnected(auth_client):
    """Overview reports unconfigured and unconnected."""
    client, _ = auth_client

    data = client.get("/api/connections").json()

    for provider in ("google", "microsoft"):
        assert data[provider]["configured"] is False
        assert data[provider]["connected"] is False
    assert data["microsoft"]["redirect_uris"] == ["http://testserver/api/connections/microsoft/callback"]
    assert data["microsoft"]["config_keys"] == ["microsoft_client_id", "microsoft_tenant_id"]
    assert "http://testserver/api/connections/google/callback" in data["google"]["redirect_uris"]


def test_overview_reflects_configuration_and_connection(auth_client, db, monkeypatch):
    """Overview reflects configuration and connection."""
    client, user = auth_client
    monkeypatch.setattr(config.settings, "microsoft_client_id", "client")
    monkeypatch.setattr(config.settings, "microsoft_tenant_id", "tenant")
    _connect(db, user, "microsoft", [microsoft_connect_service.MAIL_SCOPE])

    microsoft = client.get("/api/connections").json()["microsoft"]

    assert microsoft["configured"] is True
    assert microsoft["connected"] is True
    assert microsoft["account_email"] == "me@example.com"
    assert microsoft["mail_supported"] is True
    assert microsoft["mail_ready"] is True


def test_microsoft_auth_url_requires_configuration(auth_client):
    """Microsoft auth url requires configuration."""
    client, _ = auth_client

    response = client.get("/api/connections/microsoft/auth-url")

    assert response.status_code == 400
    assert "settings.toml" in response.json()["detail"]


def test_microsoft_callback_with_unknown_state_redirects_with_an_error(client):
    """Microsoft callback with unknown state redirects with an error."""
    response = client.get("/api/connections/microsoft/callback?code=x&state=nope", follow_redirects=False)

    assert response.status_code in (302, 307)
    assert response.headers["location"].endswith("error=microsoft_state_invalid")


def test_microsoft_callback_reports_denial(client):
    """Microsoft callback reports denial."""
    response = client.get("/api/connections/microsoft/callback?error=access_denied", follow_redirects=False)

    assert response.headers["location"].endswith("error=microsoft_denied")


def test_add_mailbox_requires_a_connection(auth_client):
    """Add mailbox requires a connection."""
    client, _ = auth_client

    response = client.post("/api/connections/google/mailbox", json={})

    assert response.status_code == 409
    assert "Connect Google first" in response.json()["detail"]


def test_add_google_mailbox_uses_gmail_imap_with_oauth(auth_client, db):
    """Add google mailbox uses gmail imap with oauth."""
    client, user = auth_client
    connection = _connect(db, user, "google", [google_client.MAIL_SCOPE], email="me@gmail.com")

    response = client.post("/api/connections/google/mailbox", json={})

    assert response.status_code == 200
    body = response.json()
    assert body["auth_type"] == "google"
    assert body["imap_server"] == "imap.gmail.com"
    account = db.query(models.EmailAccount).one()
    assert account.connection_id == connection.id
    assert account.password is None
    assert account.user_id == user.id


def test_add_mailbox_needs_the_mail_scope(auth_client, db):
    """Add mailbox needs the mail scope."""
    client, user = auth_client
    _connect(db, user, "google", [google_client.CALENDAR_SCOPE])

    response = client.post("/api/connections/google/mailbox", json={})

    assert response.status_code == 409
    assert "mail access" in response.json()["detail"]


def test_outlook_mailbox_is_added_on_a_connection_with_mail_access(auth_client, db):
    """An Outlook mailbox needs Mail.Read and is read over Graph (no password, placeholder IMAP host)."""
    client, user = auth_client
    _connect(db, user, "microsoft", [microsoft_connect_service.GRAPH_SCOPES[0]], email="me@outlook.com")
    assert client.post("/api/connections/microsoft/mailbox", json={}).status_code == 409

    _connect(db, user, "microsoft", [microsoft_connect_service.MAIL_SCOPE], email="me@outlook.com")
    response = client.post("/api/connections/microsoft/mailbox", json={})

    assert response.status_code == 200
    account = db.query(models.EmailAccount).one()
    assert account.auth_type == "microsoft"
    assert account.password is None


def test_add_mailbox_twice_is_rejected(auth_client, db):
    """Add mailbox twice is rejected."""
    client, user = auth_client
    _connect(db, user, "google", [google_client.MAIL_SCOPE], email="me@gmail.com")

    assert client.post("/api/connections/google/mailbox", json={}).status_code == 200
    again = client.post("/api/connections/google/mailbox", json={})

    assert again.status_code == 409
    assert db.query(models.EmailAccount).count() == 1


def test_imap_access_token_fails_once_the_connection_is_gone(db):
    """Imap access token fails once the connection is gone."""
    account = models.EmailAccount(
        name="x", email="x@x", imap_server="s", imap_port=993, username="x", auth_type="google", connection_id=None
    )

    with pytest.raises(oauth_connection_service.ConnectionUnusableError):
        mailbox_oauth_service.imap_access_token(account)


def test_disconnect_google_deactivates_its_mailbox_and_removes_the_connection(auth_client, db):
    """Disconnect google deactivates its mailbox and removes the connection."""
    client, user = auth_client
    _connect(db, user, "google", [google_client.MAIL_SCOPE])
    client.post("/api/connections/google/mailbox", json={})

    response = client.delete("/api/connections/google")

    assert response.status_code == 204
    assert oauth_connection_service.get_connection(db, user.id, "google") is None
    db.expire_all()
    assert db.query(models.EmailAccount).one().is_active is False


def test_disconnect_when_not_connected_is_404(auth_client):
    """Disconnect when not connected is 404."""
    client, _ = auth_client

    assert client.delete("/api/connections/microsoft").status_code == 404
    assert client.delete("/api/connections/bogus").status_code == 404


def test_connections_are_per_user(auth_client, db):
    """Connections are per user."""
    client, user = auth_client
    other = models.User(username="other", hashed_password="x")
    db.add(other)
    db.commit()
    _connect(db, other, "microsoft", [microsoft_connect_service.MAIL_SCOPE])

    assert client.get("/api/connections").json()["microsoft"]["connected"] is False
    assert client.get("/api/planner/microsoft/calendars").status_code == 404


def _outlook_setup(db, user):
    _connect(db, user, "microsoft", [microsoft_connect_service.MAIL_SCOPE])
    planner_account_service.upsert_microsoft_calendar_account_for_user(db, user.id)
    db.commit()


def _graph(calendars=None):
    graph = MagicMock()
    graph.list_calendars.return_value = _CALENDARS if calendars is None else calendars
    return graph


def test_outlook_calendars_default_to_the_default_calendar(auth_client, db):
    """Outlook calendars default to the default calendar."""
    client, user = auth_client
    _outlook_setup(db, user)

    with patch("janus.api.planner_sources.microsoft_connect_service.graph_client", return_value=_graph()):
        data = client.get("/api/planner/microsoft/calendars").json()

    assert [(c["id"], c["selected"]) for c in data] == [("cal-default", True), ("cal-team", False)]
    assert data[1]["access_role"] == "reader"


def test_outlook_selection_is_saved_and_prunes_deselected_events(auth_client, db):
    """Outlook selection is saved and prunes deselected events."""
    client, user = auth_client
    _outlook_setup(db, user)
    account = planner_account_service.get_microsoft_calendar_account_for_user(db, user.id)
    db.add(
        models.CalendarEvent(
            planner_account_id=account.id,
            external_id="cal-default:e1",
            summary="old",
            start_time=datetime(2026, 1, 1, tzinfo=UTC),
            end_time=datetime(2026, 1, 1, 1, tzinfo=UTC),
        )
    )
    db.commit()

    with patch("janus.api.planner_sources.microsoft_connect_service.graph_client", return_value=_graph()):
        response = client.put("/api/planner/microsoft/calendars", json={"calendar_ids": ["cal-team"]})

    assert response.status_code == 200
    assert [(c["id"], c["selected"]) for c in response.json()] == [("cal-default", False), ("cal-team", True)]
    assert db.query(models.CalendarEvent).count() == 0
    db.expire_all()
    assert planner_account_service.get_microsoft_calendar_account_for_user(db, user.id).selected_calendar_ids == [
        "cal-team"
    ]


def test_outlook_selection_rejects_unknown_calendars(auth_client, db):
    """Outlook selection rejects unknown calendars."""
    client, user = auth_client
    _outlook_setup(db, user)

    with patch("janus.api.planner_sources.microsoft_connect_service.graph_client", return_value=_graph()):
        response = client.put("/api/planner/microsoft/calendars", json={"calendar_ids": ["nope"]})

    assert response.status_code == 422


def test_outlook_listing_failure_is_a_bad_gateway(auth_client, db):
    """Outlook listing failure is a bad gateway."""
    client, user = auth_client
    _outlook_setup(db, user)
    graph = _graph()
    graph.list_calendars.side_effect = RuntimeError("boom")

    with patch("janus.api.planner_sources.microsoft_connect_service.graph_client", return_value=graph):
        response = client.get("/api/planner/microsoft/calendars")

    assert response.status_code == 502


def test_sync_now_reports_synced_sources_and_errors(auth_client, db):
    """Sync now reports synced sources and errors."""
    client, user = auth_client
    _outlook_setup(db, user)

    with patch("janus.api.planner_sources.PlannerService") as service:
        response = client.post("/api/planner/sync")

    service.return_value.sync_all.assert_called_once_with(user_id=user.id)
    assert response.status_code == 200
    assert response.json() == {"synced_accounts": 1, "errors": []}


def test_connector_fetches_events_of_the_selected_calendars_with_qualified_ids():
    """Connector fetches events of the selected calendars with qualified ids."""
    account = MagicMock(selected_calendar_ids=["cal-team"])
    graph = _graph()
    graph.list_calendar_events.return_value = [
        {
            "id": "e1",
            "subject": "Review",
            "start": {"dateTime": "2026-11-12T10:00:00.0000000"},
            "end": {"dateTime": "2026-11-12T11:00:00.0000000"},
            "location": {"displayName": "Room 4"},
        }
    ]
    connector = microsoft_calendar.MicrosoftCalendarConnector(account, lambda: graph)

    events = connector.fetch_events(datetime(2026, 11, 1, tzinfo=UTC), datetime(2026, 12, 1, tzinfo=UTC))

    assert [e["external_id"] for e in events] == ["cal-team:e1"]
    assert events[0]["summary"] == "Review"
    assert events[0]["location"] == "Room 4"
    assert events[0]["start_time"] == datetime(2026, 11, 12, 10, tzinfo=UTC)
    assert graph.list_calendar_events.call_args.args[0] == "cal-team"


def test_connector_without_selection_reads_the_default_calendar():
    """Connector without selection reads the default calendar."""
    connector = microsoft_calendar.MicrosoftCalendarConnector(MagicMock(selected_calendar_ids=None), lambda: g)
    g = _graph()
    g.list_calendar_events.return_value = []

    connector.fetch_events(datetime(2026, 11, 1, tzinfo=UTC), datetime(2026, 12, 1, tzinfo=UTC))

    assert g.list_calendar_events.call_args.args[0] == "cal-default"


@pytest.mark.parametrize("provider", ["google", "microsoft"])
def test_every_provider_shares_the_same_auth_url_and_callback_behaviour(auth_client, provider):
    """Auth-url, callback errors and disconnect behave alike for Google and Microsoft."""
    client, _ = auth_client

    unconfigured = client.get(f"/api/connections/{provider}/auth-url")
    assert unconfigured.status_code == 400
    assert "settings.toml" in unconfigured.json()["detail"]

    callback = f"/api/connections/{provider}/callback"
    denied = client.get(f"{callback}?error=access_denied", follow_redirects=False)
    assert denied.headers["location"].endswith(f"error={provider}_denied")
    invalid = client.get(f"{callback}?state=x", follow_redirects=False)
    assert invalid.headers["location"].endswith(f"error={provider}_invalid_callback")
    unknown_state = client.get(f"{callback}?code=c&state=nope", follow_redirects=False)
    assert unknown_state.headers["location"].endswith(f"error={provider}_state_invalid")

    assert client.delete(f"/api/connections/{provider}").status_code == 404


@pytest.mark.parametrize("provider", ["google", "microsoft"])
def test_each_provider_reports_one_callback_uri(auth_client, provider):
    """Each provider registers exactly one redirect URI, on the same pattern."""
    client, _ = auth_client

    uris = client.get("/api/connections").json()[provider]["redirect_uris"]

    assert uris == [f"http://testserver/api/connections/{provider}/callback"]


def test_disconnect_google_removes_planner_accounts_that_predate_connections(auth_client, db):
    """A Google calendar connected before connections existed still disconnects through the shared route."""
    client, user = auth_client
    planner_account_service.upsert_google_accounts_for_user(db, user.id, "token")
    db.commit()

    assert client.delete("/api/connections/google").status_code == 204
    assert db.query(models.PlannerAccount).count() == 0


def test_calendar_routes_are_shared_and_reject_unknown_providers(auth_client):
    """The calendar routes take the provider as a path segment."""
    client, _ = auth_client

    assert client.get("/api/planner/bogus/calendars").status_code == 404
    assert client.get("/api/planner/google/calendars").status_code == 404
    assert client.get("/api/planner/microsoft/calendars").status_code == 404


def test_reconnecting_reattaches_the_mailbox_that_a_disconnect_left_behind(auth_client, db):
    """Disconnect keeps the mailbox; adding it again after reconnecting reuses and reactivates it."""
    client, user = auth_client
    _connect(db, user, "google", [google_client.MAIL_SCOPE], email="me@gmail.com")
    first = client.post("/api/connections/google/mailbox", json={}).json()
    client.delete("/api/connections/google")
    _connect(db, user, "google", [google_client.MAIL_SCOPE], email="me@gmail.com")

    again = client.post("/api/connections/google/mailbox", json={})

    assert again.status_code == 200
    assert again.json()["id"] == first["id"]
    db.expire_all()
    account = db.query(models.EmailAccount).one()
    assert account.is_active is True
    assert account.connection_id is not None
