"""Regression tests for the Google planner token exchange (JAN-318).

Unlike ``test_google_oauth.py``, these run the real ``google_auth_oauthlib`` /
``requests_oauthlib`` / ``oauthlib`` code path and stub only the HTTP call to
Google's token endpoint, because the failures lived inside those libraries:
oauthlib refused the plain-http callback URL and raised a ``Warning`` when
Google reported ``email`` as ``https://www.googleapis.com/auth/userinfo.email``.
"""

import json
from unittest.mock import MagicMock, patch

import pytest
import requests
from sqlalchemy.exc import IntegrityError

from janus import config, models, security

_CALENDAR = "https://www.googleapis.com/auth/calendar.readonly"
_TASKS = "https://www.googleapis.com/auth/tasks.readonly"
#: The scope string Google really returns for openid + email + calendar + tasks.
_GOOGLE_GRANTED_SCOPE = f"openid https://www.googleapis.com/auth/userinfo.email {_CALENDAR} {_TASKS}"
_CLIENT_SECRET = "client-secret-value-jan318"
_REFRESH_TOKEN = "refresh-token-value-jan318"
_ACCESS_TOKEN = "access-token-value-jan318"


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
def no_userinfo_lookup():
    """Keeps the best-effort Sign-In linking lookup off the network."""
    with patch("janus.services.google_client.fetch_userinfo", return_value={}):
        yield


def _token_endpoint_response(status_code=200, **body):
    """Builds the response ``requests`` would hand back from Google's token endpoint."""
    response = MagicMock()
    response.status_code = status_code
    response.text = json.dumps(body)
    response.headers = {"Content-Type": "application/json"}
    return response


def _granted(scope=_GOOGLE_GRANTED_SCOPE, refresh_token=_REFRESH_TOKEN):
    """Google's success body; ``refresh_token=None`` leaves it out."""
    body = {"access_token": _ACCESS_TOKEN, "expires_in": 3599, "scope": scope, "token_type": "Bearer"}
    if refresh_token:
        body["refresh_token"] = refresh_token
    return _token_endpoint_response(**body)


def _add_user(db, username):
    user = models.User(username=username, hashed_password=security.security_service.get_password_hash("pw"))
    db.add(user)
    db.commit()
    return user


@pytest.fixture
def configured(client, db):
    """An admin with Google OAuth client credentials saved in Planner settings."""
    admin = models.User(
        username="admin", hashed_password=security.security_service.get_password_hash("admin"), is_admin=True
    )
    db.add(admin)
    db.commit()
    login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    client.headers[security.CSRF_HEADER_NAME] = login.json()["csrf_token"]
    _set_google_client("client-id", _CLIENT_SECRET)
    return client, admin


def _callback(client, db, user, token_response, state="state-jan318"):
    """Runs the planner callback for ``user`` with Google's token endpoint answering ``token_response``."""
    db.add(models.OAuthState(state=state, user_id=user.id, code_verifier="v" * 64))
    db.commit()
    side_effect = token_response if isinstance(token_response, Exception) else None
    with patch(
        "requests.sessions.Session.request",
        return_value=None if side_effect else token_response,
        side_effect=side_effect,
    ) as token_request:
        response = client.get(f"/api/connections/google/callback?code=auth-code&state={state}", follow_redirects=False)
    assert response.status_code in (302, 307)
    return response.headers["location"], token_request


def _stored_credentials(db, user):
    account = (
        db.query(models.PlannerAccount)
        .filter_by(user_id=user.id, provider=models.Provider.GOOGLE_CALENDAR, name="Google Calendar")
        .one()
    )
    return json.loads(security.security_service.decrypt(account.token))


def test_plain_http_install_with_googles_canonical_scopes_connects(configured, db):
    """Default http://localhost-style install + Google's real scope string used to fail every time."""
    client, admin = configured

    location, token_request = _callback(client, db, admin, _granted())

    assert location == "http://testserver/settings?tab=connections&connected=google"
    assert token_request.call_args.kwargs["data"]["code"] == "auth-code"
    assert token_request.call_args.kwargs["data"]["redirect_uri"] == "http://testserver/api/connections/google/callback"
    stored = _stored_credentials(db, admin)
    assert stored["token"] == _ACCESS_TOKEN
    assert stored["refresh_token"] == _REFRESH_TOKEN
    assert stored["client_secret"] == _CLIENT_SECRET
    assert {_CALENDAR, _TASKS} <= set(stored["scopes"])
    assert db.query(models.PlannerAccount).filter_by(user_id=admin.id).count() == 2


def test_scopes_granted_earlier_do_not_break_the_exchange(configured, db):
    """include_granted_scopes adds earlier grants (e.g. Sign-In's profile scope) to the response."""
    client, admin = configured
    scope = f"{_GOOGLE_GRANTED_SCOPE} https://www.googleapis.com/auth/userinfo.profile"

    location, _ = _callback(client, db, admin, _granted(scope=scope))

    assert location.endswith("&connected=google")


def test_second_user_can_connect_google(configured, db):
    """Account names were globally unique, so the second user's "Google Calendar" insert failed."""
    client, admin = configured
    second = _add_user(db, "bob")

    first_location, _ = _callback(client, db, admin, _granted(), state="state-admin")
    second_location, _ = _callback(client, db, second, _granted(), state="state-bob")

    assert first_location.endswith("&connected=google")
    assert second_location.endswith("&connected=google")
    for user in (admin, second):
        names = {account.name for account in db.query(models.PlannerAccount).filter_by(user_id=user.id)}
        assert names == {"Google Calendar", "Google Tasks"}


def test_planner_account_name_stays_unique_per_owner(db):
    """The new constraint still refuses one owner holding two accounts with the same name."""
    user = _add_user(db, "carol")
    for _ in range(2):
        db.add(models.PlannerAccount(user_id=user.id, name="Google Calendar", provider=models.Provider.GOOGLE_CALENDAR))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


@pytest.mark.parametrize(
    ("token_response", "error_code"),
    [
        (_token_endpoint_response(400, error="invalid_grant", error_description="Bad Request"), "google_invalid_grant"),
        (
            _token_endpoint_response(401, error="invalid_client", error_description="Unauthorized"),
            "google_invalid_client",
        ),
        (_token_endpoint_response(400, error="redirect_uri_mismatch"), "google_redirect_uri_mismatch"),
        (_token_endpoint_response(400, error="temporarily_unavailable"), "google_token_exchange_failed"),
        (_token_endpoint_response(200, unexpected="body"), "google_token_exchange_failed"),
        (requests.ConnectionError("connection refused"), "google_unreachable"),
        (_granted(scope=f"openid {_CALENDAR}"), "google_scope_missing"),
        (_granted(refresh_token=None), "google_no_refresh_token"),
    ],
    ids=[
        "invalid-grant",
        "invalid-client",
        "redirect-uri-mismatch",
        "other-oauth-error",
        "no-access-token",
        "network-error",
        "scope-unticked",
        "no-refresh-token",
    ],
)
def test_each_exchange_failure_maps_to_its_own_code(configured, db, caplog, token_response, error_code):
    """Each cause gets its own safe code, nothing is stored, and no secret reaches the log."""
    client, admin = configured

    location, _ = _callback(client, db, admin, token_response)

    assert location == f"http://testserver/settings?tab=connections&error={error_code}"
    assert db.query(models.PlannerAccount).count() == 0
    for secret in (_CLIENT_SECRET, _REFRESH_TOKEN, _ACCESS_TOKEN):
        assert secret not in caplog.text


def test_reconnect_without_refresh_token_keeps_the_stored_one(configured, db):
    """Google may omit refresh_token on a repeat consent; the stored one for the same client is kept."""
    client, admin = configured
    _callback(client, db, admin, _granted(), state="state-first")

    location, _ = _callback(client, db, admin, _granted(refresh_token=None), state="state-second")

    assert location.endswith("&connected=google")
    assert _stored_credentials(db, admin)["refresh_token"] == _REFRESH_TOKEN


def test_reconnect_does_not_reuse_a_refresh_token_from_another_client(configured, db):
    """A refresh token issued to a previous OAuth client cannot refresh tokens for the new one."""
    client, admin = configured
    _callback(client, db, admin, _granted(), state="state-first")
    _set_google_client("other-client", "other")

    location, _ = _callback(client, db, admin, _granted(refresh_token=None), state="state-second")

    assert location.endswith("&error=google_no_refresh_token")


def test_callback_reports_missing_client_credentials(configured, db):
    """Credentials cleared between auth-url and callback no longer read as a token exchange failure."""
    client, admin = configured
    _set_google_client("", "")

    location, token_request = _callback(client, db, admin, _granted())

    assert location.endswith("&error=google_not_configured")
    token_request.assert_not_called()


def test_callback_reports_a_failed_save(configured, db):
    """A database error while storing the accounts redirects with its own code instead of a 500."""
    client, admin = configured

    with patch(
        "janus.services.google_connect_service.planner_account_service.upsert_google_accounts_for_user",
        side_effect=IntegrityError("INSERT", {}, Exception("UNIQUE constraint failed: planner_accounts.name")),
    ):
        location, _ = _callback(client, db, admin, _granted())

    assert location.endswith("&error=google_account_save_failed")
    assert db.query(models.PlannerAccount).count() == 0


def test_google_sign_in_survives_googles_canonical_scopes_over_http(client, db):
    """The login callback shares the exchange, so it no longer fails on http or on Google's scope names."""
    user = models.User(
        username="signin", hashed_password=security.security_service.get_password_hash("pw"), google_id="sub-1"
    )
    db.add(user)
    _set_google_client("client-id", _CLIENT_SECRET)
    db.add(models.OAuthState(state="login-state", user_id=None, code_verifier="v" * 64))
    db.commit()

    granted = _granted(
        scope="openid https://www.googleapis.com/auth/userinfo.email https://www.googleapis.com/auth/userinfo.profile",
        refresh_token=None,
    )

    with (
        patch("requests.sessions.Session.request", return_value=granted),
        patch("janus.services.google_client.fetch_userinfo", return_value={"sub": "sub-1"}),
    ):
        response = client.get(
            "/api/connections/google/callback?code=auth-code&state=login-state", follow_redirects=False
        )

    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_granted_scopes_prefer_the_token_response_over_the_requested_ones():
    """An unticked optional scope (Gmail) is not stored as granted."""
    from janus.services import google_oauth

    requested = ["a", "b", "mail"]

    assert google_oauth.granted_scopes({"scope": "a b"}, requested) == ["a", "b"]
    assert google_oauth.granted_scopes({"scope": ["a"]}, requested) == ["a"]
    assert google_oauth.granted_scopes({}, requested) == requested
