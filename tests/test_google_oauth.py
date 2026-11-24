"""Tests for Google planner OAuth and connector behavior."""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from janus import config, models, planner_service, security
from janus.connectors import google as google_connectors


@pytest.fixture(autouse=True)
def _no_google_client(monkeypatch):
    """Starts every test without a configured Google client; undone after the test."""
    monkeypatch.setattr(config.settings, "google_client_id", "")
    monkeypatch.setattr(config.settings, "google_client_secret", "")


def _set_google_client(client_id: str, secret: str) -> None:
    """Configures the Google OAuth client the way settings.toml does."""
    config.settings.google_client_id = client_id
    config.settings.google_client_secret = secret


@pytest.fixture(autouse=True)
def mock_google_userinfo():
    """Stubs the Google userinfo lookup planner callbacks use to link Sign-In.

    Keeps the planner callback tests network-free and deterministic; tests
    that care about a specific subject id or a lookup failure override this
    with their own patch of ``janus.services.google_client.httpx.Client``.
    """
    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = {"sub": "google-sub-default"}
    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = False
    mock_client.get.return_value = mock_response
    with patch("janus.services.google_client.httpx.Client", return_value=mock_client):
        yield


@pytest.fixture
def auth_client(client, db):
    """Returns a TestClient with an authenticated session cookie."""
    hashed_pw = security.security_service.get_password_hash("admin")
    user = models.User(username="admin", hashed_password=hashed_pw, is_admin=True)
    db.add(user)
    db.commit()
    response = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    client.headers[security.CSRF_HEADER_NAME] = response.json()["csrf_token"]
    return client, user


# ---------------------------------------------------------------------------
# Settings – google_client_id / google_client_secret
# ---------------------------------------------------------------------------


def test_connections_flag_follows_settings_toml_and_never_exposes_the_secret(auth_client):
    """The connection status only carries a boolean; the secret never reaches the API."""
    client, _ = auth_client
    assert client.get("/api/connections").json()["google"]["configured"] is False

    _set_google_client("my-client-id", "my-secret")
    body = client.get("/api/connections")

    assert body.json()["google"]["configured"] is True
    assert "my-secret" not in body.text


def test_connections_flag_false_when_only_client_id_set(auth_client):
    """Google counts as configured only with both the client id and the secret."""
    client, _ = auth_client
    _set_google_client("only-id", "")

    assert client.get("/api/connections").json()["google"]["configured"] is False


def test_google_has_a_single_redirect_uri_that_follows_base_url(auth_client, monkeypatch):
    """Connect and Sign-In share one callback, built from the configured public base URL."""
    client, _ = auth_client
    assert client.get("/api/connections").json()["google"]["redirect_uris"] == [
        "http://testserver/api/connections/google/callback"
    ]

    monkeypatch.setattr(config.settings, "base_url", "https://janus.example.com")

    assert client.get("/api/connections").json()["google"]["redirect_uris"] == [
        "https://janus.example.com/api/connections/google/callback"
    ]


def test_planner_settings_carry_no_google_client_fields(auth_client):
    """The client lives in settings.toml: not in the planner settings, and a PATCH of it changes nothing."""
    client, _ = auth_client

    response = client.patch("/api/planner/settings", json={"google_client_id": "cid", "google_client_secret": "s"})

    assert response.status_code == 200
    assert not [key for key in response.json() if key.startswith("google_")]


# ---------------------------------------------------------------------------
# GET /api/connections/google/auth-url
# ---------------------------------------------------------------------------


def test_auth_url_requires_credentials(auth_client, db):
    """auth-url returns 400 when no Google credentials are configured."""
    client, _ = auth_client
    response = client.get("/api/connections/google/auth-url")
    assert response.status_code == 400
    assert "settings.toml" in response.json()["detail"].lower()


def test_auth_url_returns_url(auth_client, db):
    """auth-url returns a Google authorization URL when credentials are present."""
    client, _ = auth_client

    # Store credentials first.
    _set_google_client("test-client-id", "test-secret")

    fake_url = "https://accounts.google.com/o/oauth2/auth?client_id=test-client-id&state=abc"
    mock_flow = MagicMock()
    mock_flow.authorization_url.return_value = (fake_url, "abc")
    mock_flow.code_verifier = "test-code-verifier"

    with patch("janus.services.google_client.Flow.from_client_config", return_value=mock_flow) as flow_factory:
        response = client.get("/api/connections/google/auth-url")

    assert response.status_code == 200
    data = response.json()
    assert "url" in data
    assert data["url"] == fake_url
    assert flow_factory.call_args.kwargs["scopes"] == [
        "openid",
        "email",
        "https://www.googleapis.com/auth/calendar.readonly",
        "https://www.googleapis.com/auth/tasks.readonly",
        "https://mail.google.com/",
    ]

    # A state token must have been persisted in the DB.
    state_rows = db.query(models.OAuthState).all()
    assert len(state_rows) == 1


def test_auth_url_uses_trusted_forwarded_origin(auth_client, db, monkeypatch):
    """Trusted proxy headers should drive the public callback URL Janus gives Google."""
    client, _ = auth_client
    monkeypatch.setattr(config.settings, "trusted_proxy_hops", 1)
    monkeypatch.setattr(config.settings, "allowed_hosts", ["testserver", "public.example.com"])

    _set_google_client("test-client-id", "test-secret")

    mock_flow = MagicMock()
    mock_flow.authorization_url.return_value = ("https://accounts.google.com/o/oauth2/auth", "abc")
    mock_flow.code_verifier = "test-code-verifier"

    with patch("janus.services.google_client.Flow.from_client_config", return_value=mock_flow) as flow_factory:
        response = client.get(
            "/api/connections/google/auth-url",
            headers={
                "x-forwarded-proto": "https",
                "x-forwarded-host": "public.example.com",
            },
        )

    assert response.status_code == 200
    assert flow_factory.call_args.kwargs["redirect_uri"] == "https://public.example.com/api/connections/google/callback"


def test_auth_url_uses_configured_base_url(auth_client, db, monkeypatch):
    """Configured base_url should override request-local origins for Google OAuth redirects."""
    client, _ = auth_client
    monkeypatch.setattr(config.settings, "base_url", "https://janus.example.com")

    _set_google_client("test-client-id", "test-secret")

    mock_flow = MagicMock()
    mock_flow.authorization_url.return_value = ("https://accounts.google.com/o/oauth2/auth", "abc")
    mock_flow.code_verifier = "test-code-verifier"

    with patch("janus.services.google_client.Flow.from_client_config", return_value=mock_flow) as flow_factory:
        response = client.get("/api/connections/google/auth-url")

    assert response.status_code == 200
    assert flow_factory.call_args.kwargs["redirect_uri"] == "https://janus.example.com/api/connections/google/callback"


def test_auth_url_requires_authentication(client, db):
    """auth-url returns 401 for unauthenticated requests."""
    response = client.get("/api/connections/google/auth-url")
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# GET /api/connections/google/callback
# ---------------------------------------------------------------------------


def test_callback_rejects_missing_state(client, db):
    """Callback without a code or state redirects to error URL."""
    response = client.get("/api/connections/google/callback", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert "error=google_invalid_callback" in response.headers["location"]


def test_callback_rejects_invalid_state(client, db):
    """Callback with an unknown state token redirects to state-invalid error."""
    response = client.get(
        "/api/connections/google/callback?code=abc&state=not-a-real-state",
        follow_redirects=False,
    )
    assert response.status_code in (302, 307)
    assert "error=google_state_invalid" in response.headers["location"]


def test_login_state_at_the_shared_callback_signs_in_and_never_connects(client, db):
    """A Sign-In state (no user) takes the login branch of the shared callback, not the connect one.

    Without a configured client it ends on the login page, and it never creates a planner
    account or connection (which would have no owner).
    """
    db.add(models.OAuthState(state="login-flow-state-token", user_id=None))
    db.commit()

    response = client.get(
        "/api/connections/google/callback?code=abc&state=login-flow-state-token",
        follow_redirects=False,
    )

    assert response.status_code in (302, 303, 307)
    assert response.headers["location"].startswith("/login?error=")
    assert db.query(models.PlannerAccount).count() == 0
    assert db.query(models.OAuthConnection).count() == 0


def test_connect_state_is_not_accepted_by_the_microsoft_callback(auth_client, db):
    """A state minted for Google cannot be replayed at another provider's callback."""
    client, user = auth_client
    db.add(models.OAuthState(state="google-state", user_id=user.id, code_verifier="v"))
    db.commit()

    response = client.get("/api/connections/microsoft/callback?code=abc&state=google-state", follow_redirects=False)

    assert response.headers["location"].endswith("error=microsoft_state_invalid")


def test_callback_rejects_google_error(client, db):
    """Callback with error param (user denied) redirects to denied error."""
    response = client.get(
        "/api/connections/google/callback?error=access_denied",
        follow_redirects=False,
    )
    assert response.status_code in (302, 307)
    assert "error=google_denied" in response.headers["location"]


def test_callback_creates_planner_accounts(auth_client, db):
    """Valid callback stores one credential set and upserts Google calendar and tasks accounts."""
    client, user = auth_client

    # Configure credentials so _build_google_flow won't raise.
    _set_google_client("cid", "csecret")

    # Persist a valid state token in the DB.
    state_token = "valid-state-token-abc123"
    oauth_state = models.OAuthState(state=state_token, user_id=user.id)
    db.add(oauth_state)
    db.commit()

    # Mock the Flow so no real HTTP call to Google is made.
    mock_creds = MagicMock()
    mock_creds.token = "access-token"
    mock_creds.refresh_token = "refresh-token"
    mock_creds.token_uri = "https://oauth2.googleapis.com/token"
    mock_creds.client_id = "cid"
    mock_creds.client_secret = "csecret"
    mock_creds.scopes = [
        "https://www.googleapis.com/auth/calendar.readonly",
        "https://www.googleapis.com/auth/tasks.readonly",
    ]
    mock_flow = MagicMock()
    mock_flow.credentials = mock_creds

    with patch("janus.services.google_client.Flow.from_client_config", return_value=mock_flow):
        response = client.get(
            f"/api/connections/google/callback?code=auth-code&state={state_token}",
            follow_redirects=False,
        )

    assert response.status_code in (302, 307)
    assert "connected=google" in response.headers["location"]

    calendar_account = (
        db.query(models.PlannerAccount)
        .filter_by(name="Google Calendar", provider=models.Provider.GOOGLE_CALENDAR)
        .first()
    )
    tasks_account = (
        db.query(models.PlannerAccount).filter_by(name="Google Tasks", provider=models.Provider.GOOGLE_TASKS).first()
    )
    assert calendar_account is not None
    assert tasks_account is not None
    assert calendar_account.token != "access-token"  # stored encrypted
    assert tasks_account.token == calendar_account.token
    # State token must be consumed (one-time use).
    assert db.query(models.OAuthState).filter_by(state=state_token).first() is None


def test_callback_updates_existing_account(auth_client, db):
    """Second callback replaces the token on existing Google planner accounts."""
    client, user = auth_client

    _set_google_client("cid", "csecret")

    # Create existing Google calendar/tasks accounts.
    existing_token = security.security_service.encrypt('{"old": "token"}')
    calendar_account = models.PlannerAccount(
        name="Google Calendar",
        provider=models.Provider.GOOGLE_CALENDAR,
        token=existing_token,
        user_id=user.id,
    )
    tasks_account = models.PlannerAccount(
        name="Google Tasks",
        provider=models.Provider.GOOGLE_TASKS,
        token=existing_token,
        user_id=user.id,
    )
    db.add_all([calendar_account, tasks_account])
    db.commit()
    old_token = calendar_account.token

    state_token = "state-token-for-update"
    db.add(models.OAuthState(state=state_token, user_id=user.id))
    db.commit()

    mock_creds = MagicMock()
    mock_creds.token = "new-access-token"
    mock_creds.refresh_token = "new-refresh"
    mock_creds.token_uri = "https://oauth2.googleapis.com/token"
    mock_creds.client_id = "cid"
    mock_creds.client_secret = "csecret"
    mock_creds.scopes = []
    mock_flow = MagicMock()
    mock_flow.credentials = mock_creds

    with patch("janus.services.google_client.Flow.from_client_config", return_value=mock_flow):
        response = client.get(
            f"/api/connections/google/callback?code=new-code&state={state_token}",
            follow_redirects=False,
        )

    assert response.status_code in (302, 307)
    db.refresh(calendar_account)
    db.refresh(tasks_account)
    assert calendar_account.token != old_token
    assert tasks_account.token == calendar_account.token


def test_callback_recreates_flow_with_original_state(auth_client, db):
    """Callback should pass the original OAuth state into the recreated Flow."""
    client, user = auth_client

    _set_google_client("cid", "csecret")

    state_token = "state-token-for-callback-flow"
    db.add(models.OAuthState(state=state_token, user_id=user.id))
    db.commit()

    mock_creds = MagicMock()
    mock_creds.token = "new-access-token"
    mock_creds.refresh_token = "new-refresh"
    mock_creds.token_uri = "https://oauth2.googleapis.com/token"
    mock_creds.client_id = "cid"
    mock_creds.client_secret = "csecret"
    mock_creds.scopes = []
    mock_flow = MagicMock()
    mock_flow.credentials = mock_creds

    with patch("janus.services.google_client.Flow.from_client_config", return_value=mock_flow) as flow_factory:
        response = client.get(
            f"/api/connections/google/callback?code=auth-code&state={state_token}",
            follow_redirects=False,
        )

    assert response.status_code in (302, 307)
    assert "connected=google" in response.headers["location"]
    flow_factory.assert_called_once()
    assert flow_factory.call_args.kwargs["state"] == state_token


def test_callback_exchanges_the_bare_code(auth_client, db):
    """Callback exchanges the bare code: the state is checked against oauth_states already (JAN-318).

    Passing the redirected URL made oauthlib refuse every plain-http callback
    (InsecureTransportError), e.g. the default http://localhost:8080 install.
    """
    client, user = auth_client

    _set_google_client("cid", "csecret")

    state_token = "state-token-for-authorization-response"
    db.add(models.OAuthState(state=state_token, user_id=user.id))
    db.commit()

    mock_creds = MagicMock()
    mock_creds.token = "new-access-token"
    mock_creds.refresh_token = "new-refresh"
    mock_creds.token_uri = "https://oauth2.googleapis.com/token"
    mock_creds.client_id = "cid"
    mock_creds.client_secret = "csecret"
    mock_creds.scopes = []
    mock_flow = MagicMock()
    mock_flow.credentials = mock_creds

    with patch("janus.services.google_client.Flow.from_client_config", return_value=mock_flow) as flow_factory:
        response = client.get(
            f"/api/connections/google/callback?code=auth-code&state={state_token}",
            follow_redirects=False,
        )

    assert response.status_code in (302, 307)
    mock_flow.fetch_token.assert_called_once()
    mock_flow.fetch_token.assert_called_once_with(code="auth-code")
    assert flow_factory.call_args.kwargs["redirect_uri"] == "http://testserver/api/connections/google/callback"


def test_callback_uses_trusted_forwarded_public_origin(auth_client, db, monkeypatch):
    """Trusted proxy headers should drive both the OAuth callback URL and frontend redirect."""
    client, user = auth_client
    monkeypatch.setattr(config.settings, "trusted_proxy_hops", 1)
    monkeypatch.setattr(config.settings, "allowed_hosts", ["testserver", "public.example.com"])

    _set_google_client("cid", "csecret")

    state_token = "state-token-for-forwarded-origin"
    db.add(models.OAuthState(state=state_token, user_id=user.id))
    db.commit()

    mock_creds = MagicMock()
    mock_creds.token = "new-access-token"
    mock_creds.refresh_token = "new-refresh"
    mock_creds.token_uri = "https://oauth2.googleapis.com/token"
    mock_creds.client_id = "cid"
    mock_creds.client_secret = "csecret"
    mock_creds.scopes = []
    mock_flow = MagicMock()
    mock_flow.credentials = mock_creds

    with patch("janus.services.google_client.Flow.from_client_config", return_value=mock_flow) as flow_factory:
        response = client.get(
            f"/api/connections/google/callback?code=auth-code&state={state_token}",
            headers={
                "x-forwarded-proto": "https",
                "x-forwarded-host": "public.example.com",
            },
            follow_redirects=False,
        )

    assert response.status_code in (302, 307)
    assert response.headers["location"] == "https://public.example.com/settings?tab=connections&connected=google"
    mock_flow.fetch_token.assert_called_once_with(code="auth-code")
    assert flow_factory.call_args.kwargs["redirect_uri"] == "https://public.example.com/api/connections/google/callback"


def test_callback_uses_configured_base_url(auth_client, db, monkeypatch):
    """Configured base_url should drive callback exchange and post-connect redirect URLs."""
    client, user = auth_client
    monkeypatch.setattr(config.settings, "base_url", "https://janus.example.com")

    _set_google_client("cid", "csecret")

    state_token = "state-token-for-configured-base-url"
    db.add(models.OAuthState(state=state_token, user_id=user.id))
    db.commit()

    mock_creds = MagicMock()
    mock_creds.token = "new-access-token"
    mock_creds.refresh_token = "new-refresh"
    mock_creds.token_uri = "https://oauth2.googleapis.com/token"
    mock_creds.client_id = "cid"
    mock_creds.client_secret = "csecret"
    mock_creds.scopes = []
    mock_flow = MagicMock()
    mock_flow.credentials = mock_creds

    with patch("janus.services.google_client.Flow.from_client_config", return_value=mock_flow) as flow_factory:
        response = client.get(
            f"/api/connections/google/callback?code=auth-code&state={state_token}",
            follow_redirects=False,
        )

    assert response.status_code in (302, 307)
    assert response.headers["location"] == "https://janus.example.com/settings?tab=connections&connected=google"
    mock_flow.fetch_token.assert_called_once_with(code="auth-code")
    assert flow_factory.call_args.kwargs["redirect_uri"] == "https://janus.example.com/api/connections/google/callback"


# ---------------------------------------------------------------------------
# DELETE /api/connections/google
# ---------------------------------------------------------------------------


def test_disconnect_removes_account(auth_client, db):
    """Disconnect deletes the standard Google Calendar and Google Tasks planner accounts."""
    client, user = auth_client

    calendar_account = models.PlannerAccount(
        name="Google Calendar",
        provider=models.Provider.GOOGLE_CALENDAR,
        token=security.security_service.encrypt("{}"),
        user_id=user.id,
    )
    tasks_account = models.PlannerAccount(
        name="Google Tasks",
        provider=models.Provider.GOOGLE_TASKS,
        token=security.security_service.encrypt("{}"),
        user_id=user.id,
    )
    db.add_all([calendar_account, tasks_account])
    db.commit()

    response = client.delete("/api/connections/google")
    assert response.status_code == 204

    assert db.query(models.PlannerAccount).filter_by(name="Google Calendar").first() is None
    assert db.query(models.PlannerAccount).filter_by(name="Google Tasks").first() is None


def test_disconnect_does_not_remove_non_google_account(auth_client, db):
    """Disconnect does not remove a non-Google account named 'Google Calendar'."""
    client, user = auth_client

    # The user's own account with the same name but a different provider should not be deleted.
    account = models.PlannerAccount(
        user_id=user.id,
        name="Google Calendar",
        provider=models.Provider.JIRA,
        token=security.security_service.encrypt("{}"),
    )
    db.add(account)
    db.commit()

    response = client.delete("/api/connections/google")
    # There is no google_calendar account, so 404 is returned.
    assert response.status_code == 404
    # And the unrelated account is still there.
    assert db.query(models.PlannerAccount).filter_by(id=account.id).first() is not None


def test_disconnect_404_when_not_connected(auth_client, db):
    """Disconnect returns 404 when no Google account exists."""
    client, _ = auth_client
    response = client.delete("/api/connections/google")
    assert response.status_code == 404


def test_disconnect_requires_authentication(client, db):
    """Disconnect returns 401 for unauthenticated requests."""
    response = client.delete("/api/connections/google")
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Expired OAuth state cleanup
# ---------------------------------------------------------------------------


def test_auth_url_cleans_up_expired_states(auth_client, db):
    """Requesting a new auth URL deletes expired state tokens."""
    from datetime import timedelta

    from janus.time_utils import utcnow

    client, user = auth_client

    # Configure credentials.
    _set_google_client("cid", "secret")

    # Insert an already-expired state directly into the DB.
    expired_state = models.OAuthState(state="expired-state-token", user_id=user.id)
    db.add(expired_state)
    db.commit()
    # Back-date the created_at timestamp beyond the TTL.
    expired_state.created_at = utcnow() - timedelta(minutes=15)
    db.commit()

    fake_url = "https://accounts.google.com/o/oauth2/auth?state=newstate"
    mock_flow = MagicMock()
    mock_flow.authorization_url.return_value = (fake_url, "newstate")
    mock_flow.code_verifier = "test-code-verifier"

    with patch("janus.services.google_client.Flow.from_client_config", return_value=mock_flow):
        response = client.get("/api/connections/google/auth-url")

    assert response.status_code == 200
    # The expired token must be gone.
    assert db.query(models.OAuthState).filter_by(state="expired-state-token").first() is None
    # One fresh token for this request should exist.
    assert db.query(models.OAuthState).count() == 1


# ---------------------------------------------------------------------------
# Callback provider-scoping
# ---------------------------------------------------------------------------


def test_callback_preserves_different_named_google_account(auth_client, db):
    """Callback upsert scopes by provider+name.

    A google_calendar account with a different name is not touched when the
    standard 'Google Calendar' account is created.
    """
    client, user = auth_client

    _set_google_client("cid", "csecret")

    # An existing Google Calendar account with a DIFFERENT name — should not be overwritten.
    other_token = security.security_service.encrypt('{"old": "token"}')
    other_account = models.PlannerAccount(
        user_id=user.id,
        name="Work Calendar",
        provider=models.Provider.GOOGLE_CALENDAR,
        token=other_token,
    )
    other_tasks_account = models.PlannerAccount(
        user_id=user.id,
        name="Work Tasks",
        provider=models.Provider.GOOGLE_TASKS,
        token=other_token,
    )
    db.add_all([other_account, other_tasks_account])
    db.commit()

    state_token = "state-provider-scope-test"
    db.add(models.OAuthState(state=state_token, user_id=user.id))
    db.commit()

    mock_creds = MagicMock()
    mock_creds.token = "goog-access"
    mock_creds.refresh_token = "goog-refresh"
    mock_creds.token_uri = "https://oauth2.googleapis.com/token"
    mock_creds.client_id = "cid"
    mock_creds.client_secret = "csecret"
    mock_creds.scopes = []
    mock_flow = MagicMock()
    mock_flow.credentials = mock_creds

    with patch("janus.services.google_client.Flow.from_client_config", return_value=mock_flow):
        response = client.get(
            f"/api/connections/google/callback?code=auth-code&state={state_token}",
            follow_redirects=False,
        )

    assert response.status_code in (302, 307)
    assert "connected=google" in response.headers["location"]

    # The unrelated Google Calendar account with a different name must be untouched.
    db.refresh(other_account)
    db.refresh(other_tasks_account)
    assert other_account.token == other_token
    assert other_tasks_account.token == other_token

    # A new account for the standard name must have been created.
    google_account = (
        db.query(models.PlannerAccount)
        .filter_by(provider=models.Provider.GOOGLE_CALENDAR, name="Google Calendar")
        .first()
    )
    assert google_account is not None
    google_tasks_account = (
        db.query(models.PlannerAccount).filter_by(provider=models.Provider.GOOGLE_TASKS, name="Google Tasks").first()
    )
    assert google_tasks_account is not None


def test_calendar_test_connection_refreshes_expired_credentials():
    """Google Calendar refreshes expired credentials and persists the updated token."""
    original_token = security.security_service.encrypt('{"token":"old"}')
    account = models.PlannerAccount(
        name="Google Calendar",
        provider=models.Provider.GOOGLE_CALENDAR,
        token=original_token,
    )
    connector = google_connectors.GoogleCalendarConnector(account)

    mock_creds = MagicMock()
    mock_creds.expired = True
    mock_creds.refresh_token = "refresh-token"
    mock_creds.to_json.return_value = '{"token":"new"}'

    mock_service = MagicMock()
    mock_service.calendars.return_value.get.return_value.execute.return_value = {"id": "primary"}

    with (
        patch("janus.connectors.google.Credentials.from_authorized_user_info", return_value=mock_creds),
        patch("janus.connectors.google.build", return_value=mock_service),
    ):
        success, message = connector.test_connection()

    assert success is True
    assert "Successfully connected" in message
    mock_creds.refresh.assert_called_once()
    assert security.security_service.decrypt(account.token) == '{"token":"new"}'


def test_tasks_connector_fetch():
    """Google Tasks connector reads tasks across task lists and normalizes them."""
    account = models.PlannerAccount(
        name="Google Tasks",
        provider=models.Provider.GOOGLE_TASKS,
        token=security.security_service.encrypt('{"token":"secret"}'),
    )
    connector = google_connectors.GoogleTasksConnector(account)

    mock_creds = MagicMock()
    mock_creds.expired = False
    mock_creds.refresh_token = None

    mock_service = MagicMock()
    mock_service.tasklists.return_value.list.return_value.execute.return_value = {
        "items": [{"id": "list-1"}, {"id": "list-2"}]
    }
    mock_service.tasks.return_value.list.return_value.execute.side_effect = [
        {
            "items": [
                {
                    "id": "task-1",
                    "title": "Prepare launch notes",
                    "status": "needsAction",
                    "due": "2026-05-19T09:00:00Z",
                }
            ]
        },
        {"items": [{"id": "task-2", "title": "Ship changelog", "status": "completed"}]},
    ]

    with (
        patch("janus.connectors.google.Credentials.from_authorized_user_info", return_value=mock_creds),
        patch("janus.connectors.google.build", return_value=mock_service),
    ):
        tasks = connector.fetch_tasks()

    assert tasks == [
        {
            "external_id": "task-1",
            "title": "Prepare launch notes",
            "status": models.TaskStatus.TODO,
            "due_date": datetime(2026, 5, 19, 9, 0, tzinfo=UTC),
        },
        {
            "external_id": "task-2",
            "title": "Ship changelog",
            "status": models.TaskStatus.DONE,
            "due_date": None,
        },
    ]
    assert mock_service.tasks.return_value.list.call_count == 2


def test_tasks_connector_fetch_events_empty():
    """Google Tasks does not expose calendar events through the planner contract."""
    account = models.PlannerAccount(
        name="Google Tasks",
        provider=models.Provider.GOOGLE_TASKS,
        token=security.security_service.encrypt('{"token":"secret"}'),
    )

    connector = google_connectors.GoogleTasksConnector(account)
    assert connector.fetch_events(datetime.now(UTC), datetime.now(UTC)) == []


def _planner_owner_id(db) -> int:
    """Creates the owner every planner account needs (``planner_accounts.user_id`` is NOT NULL)."""
    owner = models.User(username="planner-owner", hashed_password="hash", is_admin=True)
    db.add(owner)
    db.flush()
    return owner.id


def test_planner_service_syncs_google_tasks_account(db, monkeypatch):
    """PlannerService routes Google Tasks accounts through the Google Tasks connector."""
    account = models.PlannerAccount(
        user_id=_planner_owner_id(db),
        name="Google Tasks",
        provider=models.Provider.GOOGLE_TASKS,
        token=security.security_service.encrypt("{}"),
        is_active=True,
    )
    db.add(account)
    db.commit()

    due_date = datetime(2026, 5, 20, 12, 0, tzinfo=UTC)

    class FakeGoogleTasksConnector:
        def __init__(self, planner_account):
            self.account = planner_account

        def fetch_tasks(self):
            return [
                {
                    "external_id": "task-1",
                    "title": "Review planner parity",
                    "status": models.TaskStatus.TODO,
                    "due_date": due_date,
                }
            ]

        def fetch_events(self, start_time, end_time):
            return []

        def test_connection(self):
            return True, "ok"

    monkeypatch.setattr(planner_service.google, "GoogleTasksConnector", FakeGoogleTasksConnector)

    service = planner_service.PlannerService(db)
    service.sync_all()

    task = db.query(models.TaskRecord).filter_by(planner_account_id=account.id, external_id="task-1").one()
    assert task.title == "Review planner parity"
    assert task.status == models.TaskStatus.TODO
    assert task.due_date == due_date.replace(tzinfo=None)


# ---------------------------------------------------------------------------
# JAN-132: Google account linking, now folded into the planner connect flow
# ---------------------------------------------------------------------------


def _create_and_login_user(client, db, username="alice", password="secret", is_admin=True):
    hashed = security.security_service.get_password_hash(password)
    user = models.User(username=username, hashed_password=hashed, is_admin=is_admin)
    db.add(user)
    db.commit()
    resp = client.post("/api/auth/login", json={"username": username, "password": password})
    client.headers[security.CSRF_HEADER_NAME] = resp.json()["csrf_token"]
    return user


def _configure_google_credentials(client):
    _set_google_client("client-id", "client-secret")


def test_planner_google_callback_links_signin_account(auth_client, db):
    """Connecting Google Calendar/Tasks also links Google Sign-In for the same account."""
    client, user = auth_client
    _configure_google_credentials(client)

    state_token = "valid-state-token"
    db.add(models.OAuthState(state=state_token, user_id=user.id))
    db.commit()

    mock_creds = MagicMock()
    mock_creds.token = "access-token"
    mock_creds.refresh_token = "refresh-token"
    mock_creds.token_uri = "https://oauth2.googleapis.com/token"
    mock_creds.client_id = "client-id"
    mock_creds.client_secret = "client-secret"
    mock_creds.scopes = []
    mock_flow = MagicMock()
    mock_flow.credentials = mock_creds

    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = {"sub": "google-sub-12345"}
    mock_http_client = MagicMock()
    mock_http_client.__enter__.return_value = mock_http_client
    mock_http_client.__exit__.return_value = False
    mock_http_client.get.return_value = mock_response

    with (
        patch("janus.services.google_client.Flow.from_client_config", return_value=mock_flow),
        patch("janus.services.google_client.httpx.Client", return_value=mock_http_client),
    ):
        response = client.get(
            f"/api/connections/google/callback?code=auth-code&state={state_token}",
            follow_redirects=False,
        )

    assert response.status_code in (302, 307)
    assert "connected=google" in response.headers["location"]

    db.expire_all()
    updated_user = db.query(models.User).filter(models.User.id == user.id).first()
    assert updated_user.google_id == "google-sub-12345"


def test_planner_google_callback_skips_linking_when_already_linked_elsewhere(auth_client, db):
    """Connecting still succeeds when the Google account is linked to a different user."""
    client, user = auth_client
    _configure_google_credentials(client)

    other_user = models.User(
        username="other",
        hashed_password=security.security_service.get_password_hash("unused"),
        google_id="google-sub-taken",
    )
    db.add(other_user)
    db.commit()

    state_token = "valid-state-token-conflict"
    db.add(models.OAuthState(state=state_token, user_id=user.id))
    db.commit()

    mock_creds = MagicMock()
    mock_creds.token = "access-token"
    mock_creds.refresh_token = "refresh-token"
    mock_creds.token_uri = "https://oauth2.googleapis.com/token"
    mock_creds.client_id = "client-id"
    mock_creds.client_secret = "client-secret"
    mock_creds.scopes = []
    mock_flow = MagicMock()
    mock_flow.credentials = mock_creds

    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = {"sub": "google-sub-taken"}
    mock_http_client = MagicMock()
    mock_http_client.__enter__.return_value = mock_http_client
    mock_http_client.__exit__.return_value = False
    mock_http_client.get.return_value = mock_response

    with (
        patch("janus.services.google_client.Flow.from_client_config", return_value=mock_flow),
        patch("janus.services.google_client.httpx.Client", return_value=mock_http_client),
    ):
        response = client.get(
            f"/api/connections/google/callback?code=auth-code&state={state_token}",
            follow_redirects=False,
        )

    assert response.status_code in (302, 307)
    assert "connected=google" in response.headers["location"]

    db.expire_all()
    # The initiating user is not linked (the id belongs to someone else)...
    updated_user = db.query(models.User).filter(models.User.id == user.id).first()
    assert updated_user.google_id is None
    # ...and the other user's link is untouched.
    updated_other = db.query(models.User).filter(models.User.id == other_user.id).first()
    assert updated_other.google_id == "google-sub-taken"
    # Calendar/Tasks accounts are still created despite the linking conflict.
    assert db.query(models.PlannerAccount).filter_by(name="Google Calendar").first() is not None


def test_planner_google_callback_tolerates_userinfo_failure(auth_client, db):
    """A userinfo lookup failure doesn't block Calendar/Tasks connection."""
    client, user = auth_client
    _configure_google_credentials(client)

    state_token = "valid-state-token-userinfo-failure"
    db.add(models.OAuthState(state=state_token, user_id=user.id))
    db.commit()

    mock_creds = MagicMock()
    mock_creds.token = "access-token"
    mock_creds.refresh_token = "refresh-token"
    mock_creds.token_uri = "https://oauth2.googleapis.com/token"
    mock_creds.client_id = "client-id"
    mock_creds.client_secret = "client-secret"
    mock_creds.scopes = []
    mock_flow = MagicMock()
    mock_flow.credentials = mock_creds

    with (
        patch("janus.services.google_client.Flow.from_client_config", return_value=mock_flow),
        patch("janus.services.google_client.httpx.Client", side_effect=RuntimeError("network down")),
    ):
        response = client.get(
            f"/api/connections/google/callback?code=auth-code&state={state_token}",
            follow_redirects=False,
        )

    assert response.status_code in (302, 307)
    assert "connected=google" in response.headers["location"]

    db.expire_all()
    updated_user = db.query(models.User).filter(models.User.id == user.id).first()
    assert updated_user.google_id is None
    assert db.query(models.PlannerAccount).filter_by(name="Google Calendar").first() is not None


def test_google_unlink_removes_google_id(client, db):
    """DELETE /google/link clears the google_id from the user record."""
    user = _create_and_login_user(client, db)
    user.google_id = "google-sub-to-remove"
    db.commit()

    response = client.delete("/api/auth/google/link")
    assert response.status_code == 200
    assert response.json()["status"] == "success"

    db.expire_all()
    updated = db.query(models.User).filter(models.User.id == user.id).first()
    assert updated.google_id is None


def test_google_unlink_returns_400_when_no_account_linked(client, db):
    """DELETE /google/link returns 400 when no Google account is linked."""
    _create_and_login_user(client, db)
    response = client.delete("/api/auth/google/link")
    assert response.status_code == 400
    assert "No Google account" in response.json()["detail"]


# ---------------------------------------------------------------------------
# PKCE code_verifier round-trip
#
# Regression coverage for a production bug where each endpoint pair built its
# own Flow instance: the code_verifier auto-generated while producing the
# authorization URL never reached the callback's Flow, so every Google OAuth
# attempt failed at the token exchange. These tests assert the exact
# code_verifier generated during the "start" step is the one handed to
# Flow.from_client_config (with auto-generation disabled) during the
# callback, for every affected flow.
# ---------------------------------------------------------------------------


def test_planner_google_callback_reuses_auth_url_code_verifier(auth_client, db):
    """The planner callback rebuilds Flow with the exact verifier from auth-url."""
    client, _ = auth_client

    _set_google_client("cid", "csecret")

    start_mock_flow = MagicMock()
    start_mock_flow.authorization_url.return_value = ("https://accounts.google.com/o/oauth2/auth", "abc")
    start_mock_flow.code_verifier = "generated-pkce-verifier-planner"

    with patch("janus.services.google_client.Flow.from_client_config", return_value=start_mock_flow):
        auth_url_response = client.get("/api/connections/google/auth-url")
    assert auth_url_response.status_code == 200

    oauth_state = db.query(models.OAuthState).one()
    # The verifier auto-generated for the authorization URL must be persisted.
    assert oauth_state.code_verifier == "generated-pkce-verifier-planner"

    mock_creds = MagicMock()
    mock_creds.token = "access-token"
    mock_creds.refresh_token = "refresh-token"
    mock_creds.token_uri = "https://oauth2.googleapis.com/token"
    mock_creds.client_id = "cid"
    mock_creds.client_secret = "csecret"
    mock_creds.scopes = []
    callback_mock_flow = MagicMock()
    callback_mock_flow.credentials = mock_creds

    with patch("janus.services.google_client.Flow.from_client_config", return_value=callback_mock_flow) as flow_factory:
        response = client.get(
            f"/api/connections/google/callback?code=auth-code&state={oauth_state.state}",
            follow_redirects=False,
        )

    assert response.status_code in (302, 307)
    assert "connected=google" in response.headers["location"]
    flow_factory.assert_called_once()
    assert flow_factory.call_args.kwargs["code_verifier"] == "generated-pkce-verifier-planner"
    assert flow_factory.call_args.kwargs["autogenerate_code_verifier"] is False


def test_google_login_callback_reuses_auth_url_code_verifier(client, db):
    """The login callback rebuilds Flow with the exact verifier saved during /api/auth/google."""
    _create_and_login_user(client, db, username="admin", password="secret")
    _configure_google_credentials(client)
    matching_user = models.User(
        username="google-user",
        hashed_password=security.security_service.get_password_hash("unused"),
        google_id="google-sub-login",
    )
    db.add(matching_user)
    db.commit()

    start_mock_flow = MagicMock()
    start_mock_flow.authorization_url.return_value = ("https://accounts.google.com/o/oauth2/auth", "abc")
    start_mock_flow.code_verifier = "generated-pkce-verifier-login"

    with patch("janus.api.auth.Flow.from_client_config", return_value=start_mock_flow):
        start_response = client.get("/api/auth/google", follow_redirects=False)
    assert start_response.status_code in (302, 303, 307)

    oauth_state = db.query(models.OAuthState).filter(models.OAuthState.user_id.is_(None)).one()
    assert oauth_state.code_verifier == "generated-pkce-verifier-login"

    mock_creds = MagicMock()
    mock_creds.token = "access-token"
    callback_mock_flow = MagicMock()
    callback_mock_flow.credentials = mock_creds

    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = {"sub": "google-sub-login"}
    mock_http_client = MagicMock()
    mock_http_client.__enter__.return_value = mock_http_client
    mock_http_client.__exit__.return_value = False
    mock_http_client.get.return_value = mock_response

    with (
        patch("janus.api.auth.Flow.from_client_config", return_value=callback_mock_flow) as flow_factory,
        patch("janus.services.google_client.httpx.Client", return_value=mock_http_client),
    ):
        response = client.get(
            f"/api/connections/google/callback?code=auth-code&state={oauth_state.state}",
            follow_redirects=False,
        )

    assert response.status_code == 303
    flow_factory.assert_called_once()
    assert flow_factory.call_args.kwargs["code_verifier"] == "generated-pkce-verifier-login"
    assert flow_factory.call_args.kwargs["autogenerate_code_verifier"] is False


# ---------------------------------------------------------------------------
# Multi-calendar selection
# ---------------------------------------------------------------------------


def test_selected_calendar_ids_defaults_to_primary():
    """With no configured selection, the connector falls back to the primary calendar."""
    account = models.PlannerAccount(
        name="Google Calendar",
        provider=models.Provider.GOOGLE_CALENDAR,
        token=security.security_service.encrypt("{}"),
    )
    connector = google_connectors.GoogleCalendarConnector(account)
    assert connector.selected_calendar_ids() == ["primary"]


def test_fetch_events_covers_all_selected_calendars_with_qualified_ids():
    """fetch_events() queries every configured calendar and namespaces external_id by calendar."""
    account = models.PlannerAccount(
        name="Google Calendar",
        provider=models.Provider.GOOGLE_CALENDAR,
        token=security.security_service.encrypt('{"token":"secret"}'),
        selected_calendar_ids=["primary", "shared-calendar@group.calendar.google.com"],
    )
    connector = google_connectors.GoogleCalendarConnector(account)

    mock_creds = MagicMock()
    mock_creds.expired = False
    mock_creds.refresh_token = None

    mock_service = MagicMock()
    mock_service.events.return_value.list.return_value.execute.side_effect = [
        {
            "items": [
                {
                    "id": "evt-1",
                    "summary": "Standup",
                    "start": {"dateTime": "2026-09-27T09:00:00Z"},
                    "end": {"dateTime": "2026-09-27T09:30:00Z"},
                }
            ]
        },
        {
            "items": [
                {
                    "id": "evt-1",  # same id in a different calendar must not collide
                    "summary": "Shared planning",
                    "start": {"dateTime": "2026-09-27T10:00:00Z"},
                    "end": {"dateTime": "2026-09-27T11:00:00Z"},
                }
            ]
        },
    ]

    with (
        patch("janus.connectors.google.Credentials.from_authorized_user_info", return_value=mock_creds),
        patch("janus.connectors.google.build", return_value=mock_service),
    ):
        events = connector.fetch_events(datetime.now(UTC), datetime.now(UTC))

    assert [e["external_id"] for e in events] == [
        "primary:evt-1",
        "shared-calendar@group.calendar.google.com:evt-1",
    ]
    calls = mock_service.events.return_value.list.call_args_list
    assert calls[0].kwargs["calendarId"] == "primary"
    assert calls[1].kwargs["calendarId"] == "shared-calendar@group.calendar.google.com"


def test_fetch_events_propagates_connector_errors():
    """A failed Google API call must propagate instead of being swallowed as an empty list."""
    account = models.PlannerAccount(
        name="Google Calendar",
        provider=models.Provider.GOOGLE_CALENDAR,
        token=security.security_service.encrypt('{"token":"secret"}'),
    )
    connector = google_connectors.GoogleCalendarConnector(account)

    mock_creds = MagicMock()
    mock_creds.expired = False
    mock_creds.refresh_token = None

    mock_service = MagicMock()
    mock_service.events.return_value.list.return_value.execute.side_effect = RuntimeError("token expired")

    with (
        patch("janus.connectors.google.Credentials.from_authorized_user_info", return_value=mock_creds),
        patch("janus.connectors.google.build", return_value=mock_service),
        pytest.raises(RuntimeError, match="token expired"),
    ):
        connector.fetch_events(datetime.now(UTC), datetime.now(UTC))


def test_planner_service_records_sync_error_on_connector_failure(db):
    """A connector exception is recorded on last_sync_error instead of being silently dropped."""
    account = models.PlannerAccount(
        user_id=_planner_owner_id(db),
        name="Google Calendar",
        provider=models.Provider.GOOGLE_CALENDAR,
        token=security.security_service.encrypt("{}"),
        is_active=True,
    )
    db.add(account)
    db.commit()

    class FailingConnector:
        def __init__(self, planner_account):
            self.account = planner_account

        def fetch_tasks(self):
            return []

        def fetch_events(self, start_time, end_time):
            raise RuntimeError("invalid_grant")

    with patch("janus.planner_service.google.GoogleCalendarConnector", FailingConnector):
        service = planner_service.PlannerService(db)
        service.sync_all()

    db.refresh(account)
    assert account.last_sync_error is not None
    assert "invalid_grant" in account.last_sync_error


def test_list_calendars_returns_owned_and_shared_calendars():
    """list_calendars() surfaces every calendar visible to the account, including shared ones."""
    account = models.PlannerAccount(
        name="Google Calendar",
        provider=models.Provider.GOOGLE_CALENDAR,
        token=security.security_service.encrypt('{"token":"secret"}'),
    )
    connector = google_connectors.GoogleCalendarConnector(account)

    mock_creds = MagicMock()
    mock_creds.expired = False
    mock_creds.refresh_token = None

    mock_service = MagicMock()
    mock_service.calendarList.return_value.list.return_value.execute.return_value = {
        "items": [
            {"id": "primary", "summary": "thomas@example.com", "primary": True, "accessRole": "owner"},
            {
                "id": "shared-calendar@group.calendar.google.com",
                "summary": "Team Calendar",
                "accessRole": "reader",
            },
        ]
    }

    with (
        patch("janus.connectors.google.Credentials.from_authorized_user_info", return_value=mock_creds),
        patch("janus.connectors.google.build", return_value=mock_service),
    ):
        calendars = connector.list_calendars()

    assert calendars == [
        {"id": "primary", "summary": "thomas@example.com", "primary": True, "access_role": "owner"},
        {
            "id": "shared-calendar@group.calendar.google.com",
            "summary": "Team Calendar",
            "primary": False,
            "access_role": "reader",
        },
    ]


def test_get_google_calendars_endpoint_returns_selection_state(auth_client, db):
    """GET /api/planner/google/calendars lists calendars with the current selection flagged."""
    client, user = auth_client
    account = models.PlannerAccount(
        name="Google Calendar",
        provider=models.Provider.GOOGLE_CALENDAR,
        token=security.security_service.encrypt('{"token":"secret"}'),
        user_id=user.id,
        selected_calendar_ids=["primary"],
    )
    db.add(account)
    db.commit()

    with patch(
        "janus.connectors.google.GoogleCalendarConnector.list_calendars",
        return_value=[
            {"id": "primary", "summary": "Primary", "primary": True, "access_role": "owner"},
            {"id": "shared-cal", "summary": "Shared", "primary": False, "access_role": "reader"},
        ],
    ):
        response = client.get("/api/planner/google/calendars")

    assert response.status_code == 200
    data = response.json()
    assert data == [
        {"id": "primary", "summary": "Primary", "primary": True, "access_role": "owner", "selected": True},
        {"id": "shared-cal", "summary": "Shared", "primary": False, "access_role": "reader", "selected": False},
    ]


def test_get_google_calendars_requires_connected_account(auth_client, db):
    """GET /api/planner/google/calendars returns 404 when Google Calendar isn't connected."""
    client, _ = auth_client
    response = client.get("/api/planner/google/calendars")
    assert response.status_code == 404


def test_update_google_calendars_persists_selection(auth_client, db):
    """PUT /api/planner/google/calendars stores the new selection on the account."""
    client, user = auth_client
    account = models.PlannerAccount(
        name="Google Calendar",
        provider=models.Provider.GOOGLE_CALENDAR,
        token=security.security_service.encrypt('{"token":"secret"}'),
        user_id=user.id,
    )
    db.add(account)
    db.commit()

    with patch(
        "janus.connectors.google.GoogleCalendarConnector.list_calendars",
        return_value=[
            {"id": "primary", "summary": "Primary", "primary": True, "access_role": "owner"},
            {"id": "shared-cal", "summary": "Shared", "primary": False, "access_role": "reader"},
        ],
    ):
        response = client.put(
            "/api/planner/google/calendars",
            json={"calendar_ids": ["primary", "shared-cal"]},
        )

    assert response.status_code == 200
    assert {c["id"] for c in response.json() if c["selected"]} == {"primary", "shared-cal"}

    db.refresh(account)
    assert account.selected_calendar_ids == ["primary", "shared-cal"]


def test_sync_updates_existing_event_once_external_id_is_qualified(db):
    """A pre-qualified primary-calendar row (post-migration) must be updated, not duplicated.

    Regression test for a Codex review finding: fetch_events() now returns "primary:<id>"
    instead of the bare "<id>" it used before multi-calendar support, so a row that predates
    that change must already carry the migrated id (see the 8109db6b1275 migration) for
    PlannerService.sync_all()'s upsert-by-external_id to keep matching it.
    """
    account = models.PlannerAccount(
        user_id=_planner_owner_id(db),
        name="Google Calendar",
        provider=models.Provider.GOOGLE_CALENDAR,
        token=security.security_service.encrypt("{}"),
        is_active=True,
    )
    db.add(account)
    db.commit()

    existing_event = models.CalendarEvent(
        planner_account_id=account.id,
        external_id="primary:evt-1",
        summary="Standup (stale title)",
    )
    db.add(existing_event)
    db.commit()

    class FakeGoogleCalendarConnector:
        def __init__(self, planner_account):
            self.account = planner_account

        def fetch_tasks(self):
            return []

        def fetch_events(self, start_time, end_time):
            return [
                {
                    "external_id": "primary:evt-1",
                    "summary": "Standup",
                    "start_time": datetime(2026, 9, 28, 9, 0, tzinfo=UTC),
                    "end_time": datetime(2026, 9, 28, 9, 30, tzinfo=UTC),
                    "location": "",
                }
            ]

        def test_connection(self):
            return True, "ok"

    with patch("janus.planner_service.google.GoogleCalendarConnector", FakeGoogleCalendarConnector):
        service = planner_service.PlannerService(db)
        service.sync_all()

    rows = db.query(models.CalendarEvent).filter_by(planner_account_id=account.id).all()
    assert len(rows) == 1
    assert rows[0].summary == "Standup"


def test_update_google_calendars_prunes_events_from_deselected_calendars(auth_client, db):
    """Deselecting a calendar removes its already-imported events, not just the selection."""
    client, user = auth_client
    account = models.PlannerAccount(
        name="Google Calendar",
        provider=models.Provider.GOOGLE_CALENDAR,
        token=security.security_service.encrypt('{"token":"secret"}'),
        user_id=user.id,
        selected_calendar_ids=["primary", "shared-cal"],
    )
    db.add(account)
    db.commit()

    kept_event = models.CalendarEvent(planner_account_id=account.id, external_id="primary:evt-1", summary="Standup")
    stale_event = models.CalendarEvent(
        planner_account_id=account.id, external_id="shared-cal:evt-2", summary="Shared planning"
    )
    db.add_all([kept_event, stale_event])
    db.commit()

    with patch(
        "janus.connectors.google.GoogleCalendarConnector.list_calendars",
        return_value=[
            {"id": "primary", "summary": "Primary", "primary": True, "access_role": "owner"},
            {"id": "shared-cal", "summary": "Shared", "primary": False, "access_role": "reader"},
        ],
    ):
        response = client.put("/api/planner/google/calendars", json={"calendar_ids": ["primary"]})

    assert response.status_code == 200
    remaining = db.query(models.CalendarEvent).filter_by(planner_account_id=account.id).all()
    assert [row.external_id for row in remaining] == ["primary:evt-1"]


def test_update_google_calendars_does_not_persist_selection_when_listing_fails(auth_client, db):
    """A failed Google calendar listing must not silently apply the new selection."""
    client, user = auth_client
    account = models.PlannerAccount(
        name="Google Calendar",
        provider=models.Provider.GOOGLE_CALENDAR,
        token=security.security_service.encrypt('{"token":"secret"}'),
        user_id=user.id,
        selected_calendar_ids=["primary"],
    )
    db.add(account)
    db.commit()

    with patch(
        "janus.connectors.google.GoogleCalendarConnector.list_calendars",
        side_effect=RuntimeError("invalid_grant"),
    ):
        response = client.put("/api/planner/google/calendars", json={"calendar_ids": ["shared-cal"]})

    assert response.status_code == 502
    db.refresh(account)
    assert account.selected_calendar_ids == ["primary"]


def test_get_google_calendars_marks_primary_selected_under_default_selection(auth_client, db):
    """The default ``primary`` selection flags Google's primary calendar, which it lists by real id (JAN-221)."""
    client, user = auth_client
    account = models.PlannerAccount(
        name="Google Calendar",
        provider=models.Provider.GOOGLE_CALENDAR,
        token=security.security_service.encrypt('{"token":"secret"}'),
        user_id=user.id,
    )
    db.add(account)
    db.commit()

    with patch(
        "janus.connectors.google.GoogleCalendarConnector.list_calendars",
        return_value=[
            {"id": "me@example.com", "summary": "Me", "primary": True, "access_role": "owner"},
            {"id": "team@group.calendar.google.com", "summary": "Team", "primary": False, "access_role": "reader"},
        ],
    ):
        response = client.get("/api/planner/google/calendars")

    assert response.status_code == 200
    assert [(cal["id"], cal["selected"]) for cal in response.json()] == [
        ("me@example.com", True),
        ("team@group.calendar.google.com", False),
    ]


def test_disconnect_removes_imported_events_and_tasks(auth_client, db):
    """Disconnect deletes what the Google accounts imported, so a later account cannot inherit it (JAN-221)."""
    client, user = auth_client
    calendar_account = models.PlannerAccount(
        name="Google Calendar",
        provider=models.Provider.GOOGLE_CALENDAR,
        token=security.security_service.encrypt("{}"),
        user_id=user.id,
    )
    tasks_account = models.PlannerAccount(
        name="Google Tasks",
        provider=models.Provider.GOOGLE_TASKS,
        token=security.security_service.encrypt("{}"),
        user_id=user.id,
    )
    db.add_all([calendar_account, tasks_account])
    db.flush()
    start = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
    db.add_all(
        [
            models.CalendarEvent(
                planner_account_id=calendar_account.id,
                external_id="primary:standup",
                summary="Standup",
                start_time=start,
                end_time=start,
            ),
            models.TaskRecord(
                planner_account_id=tasks_account.id,
                external_id="task-1",
                title="Google task",
                status=models.TaskStatus.TODO,
            ),
        ]
    )
    db.commit()
    freed_id = calendar_account.id

    assert client.delete("/api/connections/google").status_code == 204

    db.expire_all()
    assert db.query(models.CalendarEvent).count() == 0
    assert db.query(models.TaskRecord).count() == 0
    # SQLite hands the freed id to the next planner account; it must start empty.
    response = client.post(
        "/api/planner/accounts",
        json={"name": "Jira", "provider": "jira", "token": "me@example.com:token", "api_url": "https://jira.test"},
    )
    assert response.status_code == 200
    assert response.json()["id"] == freed_id
    assert client.get("/api/planner/events").json() == []
    assert client.get("/api/planner/tasks").json() == []


def test_google_endpoints_default_to_google(monkeypatch):
    """Without JANUS_GOOGLE_API_BASE_URL every Google call goes to Google itself."""
    from janus.connectors import google_endpoints

    monkeypatch.setattr(config.settings, "google_api_base_url", None)
    assert google_endpoints.token_uri() == "https://oauth2.googleapis.com/token"
    assert google_endpoints.userinfo_url() == "https://www.googleapis.com/oauth2/v3/userinfo"
    assert google_endpoints.api_client_options("calendar/v3") is None
    creds = MagicMock()
    assert google_endpoints.with_fake_token_uri(creds) is creds
    creds.with_token_uri.assert_not_called()


def test_google_endpoints_follow_the_e2e_fake(monkeypatch):
    """JANUS_GOOGLE_API_BASE_URL sends token, userinfo, Calendar, Tasks, and refreshes to the fake (JAN-221)."""
    from google.oauth2.credentials import Credentials

    from janus.connectors import google_endpoints

    monkeypatch.setattr(config.settings, "google_api_base_url", "http://127.0.0.1:9999/google/")
    assert google_endpoints.token_uri() == "http://127.0.0.1:9999/google/token"
    assert google_endpoints.userinfo_url() == "http://127.0.0.1:9999/google/oauth2/v3/userinfo"
    assert google_endpoints.api_client_options("calendar/v3") == {
        "api_endpoint": "http://127.0.0.1:9999/google/calendar/v3/"
    }
    assert google_endpoints.api_client_options("") == {"api_endpoint": "http://127.0.0.1:9999/google/"}
    # google-auth resets a stored token URI to Google's on load; the connector re-applies the fake's.
    loaded = Credentials.from_authorized_user_info(
        {
            "refresh_token": "r",
            "client_id": "c",
            "client_secret": "s",
            "token_uri": "http://127.0.0.1:9999/google/token",
        }
    )
    assert loaded.token_uri == "https://oauth2.googleapis.com/token"
    assert google_endpoints.with_fake_token_uri(loaded).token_uri == "http://127.0.0.1:9999/google/token"


def test_login_denied_at_the_shared_callback_reports_access_denied(client, db):
    """Cancelling Google's consent screen on the login page keeps its own error code."""
    db.add(models.OAuthState(state="login-denied-state", user_id=None))
    db.commit()

    response = client.get(
        "/api/connections/google/callback?error=access_denied&state=login-denied-state",
        follow_redirects=False,
    )

    assert response.headers["location"] == "/login?error=access_denied"


def test_login_at_the_shared_callback_sets_the_session_cookie(client, db, monkeypatch):
    """A successful Sign-In puts the session cookie on the redirect that is actually returned."""
    from types import SimpleNamespace

    from janus.services import google_client, google_oauth

    db.add(models.User(username="gina", hashed_password="x", google_id="sub-gina"))
    db.add(models.OAuthState(state="login-ok-state", user_id=None, code_verifier="v"))
    db.commit()
    monkeypatch.setattr(google_client, "build_flow", lambda *args, **kwargs: object())
    monkeypatch.setattr(google_oauth, "exchange_code", lambda flow, code: SimpleNamespace(token="t"))
    monkeypatch.setattr(google_client, "fetch_userinfo", lambda token: {"sub": "sub-gina"})

    response = client.get("/api/connections/google/callback?code=abc&state=login-ok-state", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert "access_token=" in response.headers.get("set-cookie", "")
