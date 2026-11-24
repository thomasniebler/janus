"""Delegated Microsoft sign-in: configuration, login, silent tokens, encrypted cache, and status (MSAL faked)."""

from __future__ import annotations

import base64
import json
import stat

import msal
import pytest

from janus import config, security
from janus.services import microsoft_auth_service as svc

SECRET = "SUPER-SECRET-REFRESH-TOKEN"
CACHE_JSON = json.dumps({"RefreshToken": {"k": {"secret": SECRET}}})


def _settings(**overrides):
    """Build Settings with the app registration ids set."""
    values = {"microsoft_client_id": "client-1", "microsoft_tenant_id": "tenant-1", **overrides}
    return config.Settings(**values)


def _jwt(claims: dict | list) -> str:
    """Make an unsigned JWT-shaped string around the given claims."""

    def part(data) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")

    return f"{part({'alg': 'none'})}.{part(claims)}.sig"


def _store_signed_in_cache(content: str = CACHE_JSON) -> None:
    """Write an encrypted cache file as a previous `janus microsoft login` would have."""
    path = svc.token_cache_path()
    path.write_text(security.security_service.encrypt(content), encoding="utf-8")


class FakeApp:
    """Stands in for msal.PublicClientApplication and records how it is used."""

    instances: list[FakeApp] = []
    accounts: list[dict] = []
    interactive_result: dict = {}
    silent_result: dict | None = {}
    silent_refreshes_cache = False

    def __init__(self, client_id, authority=None, token_cache=None):
        """Remember construction arguments."""
        self.client_id = client_id
        self.authority = authority
        self.token_cache = token_cache
        self.interactive_calls: list[dict] = []
        self.silent_calls: list[dict] = []
        FakeApp.instances.append(self)

    def get_accounts(self):
        """Return the accounts a sign-in left in the cache."""
        return list(FakeApp.accounts)

    def acquire_token_interactive(self, scopes, timeout=None, **kwargs):
        """Pretend the browser flow ran; on success MSAL would have filled the cache."""
        self.interactive_calls.append({"scopes": list(scopes), "timeout": timeout})
        if "access_token" in FakeApp.interactive_result:
            self.token_cache.deserialize(CACHE_JSON)
            self.token_cache.has_state_changed = True
            FakeApp.accounts = [{"username": "me@example.com"}]
        return FakeApp.interactive_result

    def acquire_token_silent(self, scopes, account):
        """Return the configured silent result, optionally refreshing the cache like MSAL does."""
        self.silent_calls.append({"scopes": list(scopes), "account": account})
        if FakeApp.silent_refreshes_cache:
            self.token_cache.deserialize(json.dumps({"RefreshToken": {"k": {"secret": "ROTATED-TOKEN"}}}))
            self.token_cache.has_state_changed = True
        return FakeApp.silent_result


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    """Keep the cache in tmp, fake MSAL, and reset module state around each test."""
    monkeypatch.setattr(config, "get_data_dir", lambda: tmp_path)
    monkeypatch.setattr(msal, "PublicClientApplication", FakeApp)
    FakeApp.instances = []
    FakeApp.accounts = [{"username": "me@example.com"}]
    FakeApp.interactive_result = {
        "access_token": "interactive-token",
        "id_token_claims": {"preferred_username": "me@example.com"},
    }
    FakeApp.silent_result = {"access_token": "access-token"}
    FakeApp.silent_refreshes_cache = False
    svc.reset_credential()
    yield
    svc.reset_credential()


def test_configured_requires_both_ids():
    """Both the client id and the tenant id must be set."""
    assert svc.is_configured(_settings())
    assert not svc.is_configured(_settings(microsoft_client_id=""))
    assert not svc.is_configured(_settings(microsoft_tenant_id="  "))


def test_connected_needs_ids_and_a_signed_in_account():
    """`connected` means configured and a sign-in cache exists on this machine."""
    settings = _settings()
    assert not svc.is_connected(settings)

    _store_signed_in_cache()
    assert svc.is_connected(settings)
    assert not svc.is_connected(_settings(microsoft_client_id=""))


def test_login_requires_configuration():
    """Without ids there is nothing to sign in to, and no browser is opened."""
    with pytest.raises(svc.MicrosoftAuthError, match="JANUS_MICROSOFT_CLIENT_ID"):
        svc.login(_settings(microsoft_client_id=""))
    assert FakeApp.instances == []


def test_login_is_interactive_against_the_tenant_with_a_timeout():
    """Only login opens a browser; it asks for the consented Graph permissions and gives up eventually."""
    username = svc.login(_settings())

    app = FakeApp.instances[0]
    assert app.client_id == "client-1"
    assert app.authority == "https://login.microsoftonline.com/tenant-1"
    assert app.interactive_calls == [{"scopes": [svc.GRAPH_DEFAULT_SCOPE], "timeout": svc.LOGIN_TIMEOUT_SECONDS}]
    assert username == "me@example.com"


def test_login_stores_the_cache_encrypted_and_private():
    """The refresh token never reaches disk in plaintext; only Janus's Fernet key can read it."""
    svc.login(_settings())

    path = svc.token_cache_path()
    raw = path.read_text(encoding="utf-8")
    assert SECRET not in raw
    assert SECRET in security.security_service.decrypt(raw)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_login_failure_is_reported_and_leaves_no_cache():
    """A failed or declined sign-in surfaces the Microsoft error and stores nothing."""
    FakeApp.interactive_result = {
        "error": "invalid_grant",
        "error_description": "AADSTS65001: consent required\r\nTrace",
    }

    with pytest.raises(svc.MicrosoftAuthError, match="AADSTS65001: consent required"):
        svc.login(_settings())
    assert not svc.token_cache_path().exists()


def test_tokens_are_silent_and_reuse_one_application():
    """Background calls renew from the cache for the signed-in account and never open a browser."""
    _store_signed_in_cache()

    assert svc.get_graph_token(_settings()) == "access-token"
    assert svc.get_graph_token(_settings()) == "access-token"

    assert len(FakeApp.instances) == 1
    app = FakeApp.instances[0]
    assert app.interactive_calls == []
    assert [call["scopes"] for call in app.silent_calls] == [[svc.GRAPH_DEFAULT_SCOPE]] * 2
    assert app.silent_calls[0]["account"] == {"username": "me@example.com"}


def test_a_token_refresh_is_persisted_encrypted():
    """MSAL rotates refresh tokens; the new cache must replace the old one on disk, still encrypted."""
    _store_signed_in_cache()
    FakeApp.silent_refreshes_cache = True

    svc.get_graph_token(_settings())

    raw = svc.token_cache_path().read_text(encoding="utf-8")
    assert "ROTATED-TOKEN" not in raw
    assert "ROTATED-TOKEN" in security.security_service.decrypt(raw)


def test_an_unchanged_cache_is_not_rewritten():
    """Reads that change nothing leave the file (and its mtime) alone."""
    _store_signed_in_cache()
    before = svc.token_cache_path().stat().st_mtime_ns

    svc.get_graph_token(_settings())

    assert svc.token_cache_path().stat().st_mtime_ns == before


def test_token_without_sign_in_tells_the_user_to_log_in():
    """No cache file: a clear instruction, and no application is even built."""
    with pytest.raises(svc.MicrosoftAuthError, match="janus microsoft login"):
        svc.get_graph_token(_settings())
    assert FakeApp.instances == []


def test_token_without_configuration_is_rejected_first():
    """An unconfigured instance is reported before anything else is touched."""
    with pytest.raises(svc.MicrosoftAuthError, match="not configured"):
        svc.get_graph_token(_settings(microsoft_tenant_id=""))
    assert FakeApp.instances == []


def test_a_cache_without_accounts_means_signed_out():
    """An emptied cache is the same as no sign-in."""
    _store_signed_in_cache()
    FakeApp.accounts = []

    with pytest.raises(svc.MicrosoftAuthError, match="Not signed in"):
        svc.get_graph_token(_settings())


@pytest.mark.parametrize(
    ("silent_result", "expected"),
    [
        (None, "no cached token"),
        (
            {"error": "invalid_grant", "error_description": "AADSTS70043: refresh token expired\nTrace ID: x"},
            "AADSTS70043",
        ),
    ],
)
def test_expired_sign_in_is_reported_never_retried_interactively(silent_result, expected):
    """When silent renewal fails the caller is told to log in again; a browser is never opened."""
    _store_signed_in_cache()
    FakeApp.silent_result = silent_result

    with pytest.raises(svc.MicrosoftAuthError, match=expected) as excinfo:
        svc.get_graph_token(_settings())

    assert "janus microsoft login" in str(excinfo.value)
    assert "Trace ID" not in str(excinfo.value), "only the first line of an MSAL error is surfaced"
    assert FakeApp.instances[0].interactive_calls == []


def test_an_unreadable_cache_is_a_clear_error_not_a_crash(tmp_path):
    """A cache encrypted with a different Fernet key (or garbage) must not blow up a tool call."""
    svc.token_cache_path().write_text("not-fernet-data", encoding="utf-8")

    with pytest.raises(svc.MicrosoftAuthError, match="cannot be read"):
        svc.get_graph_token(_settings())


def test_logout_forgets_the_account_and_drops_the_in_memory_app():
    """Logout deletes the cache and later calls need a fresh login."""
    _store_signed_in_cache()
    svc.get_graph_token(_settings())

    assert svc.logout() is True
    assert not svc.token_cache_path().exists()
    assert svc.logout() is False
    with pytest.raises(svc.MicrosoftAuthError):
        svc.get_graph_token(_settings())


def test_graph_client_asks_for_a_token_per_request():
    """The client never holds a token; expiry is handled by asking again."""
    _store_signed_in_cache()
    client = svc.build_graph_client(_settings())
    try:
        FakeApp.silent_result = {"access_token": "first"}
        assert client._token_provider() == "first"
        FakeApp.silent_result = {"access_token": "second"}
        assert client._token_provider() == "second"
    finally:
        client.close()


def test_decode_token_claims_reads_unverified_payload_and_tolerates_garbage():
    """Claims are for display only; malformed tokens yield an empty dict."""
    assert svc.decode_token_claims(_jwt({"scp": "Mail.Read Chat.Read"})) == {"scp": "Mail.Read Chat.Read"}
    assert svc.decode_token_claims("not-a-jwt") == {}
    assert svc.decode_token_claims("a.%%%.c") == {}
    assert svc.decode_token_claims(_jwt([1, 2])) == {}


def test_connection_status_reports_account_and_sorted_scopes():
    """Status shows who is signed in and which Graph permissions the token carries."""
    settings = _settings()
    assert svc.connection_status(settings) == {
        "configured": True,
        "connected": False,
        "account": None,
        "scopes": [],
        "error": None,
    }

    _store_signed_in_cache()
    FakeApp.silent_result = {"access_token": _jwt({"scp": "Mail.Read Chat.ReadWrite User.Read"})}
    status = svc.connection_status(settings)

    assert status["account"] == "me@example.com"
    assert status["scopes"] == ["Chat.ReadWrite", "Mail.Read", "User.Read"]
    assert status["error"] is None


def test_connection_status_surfaces_an_expired_sign_in():
    """A dead refresh token shows up as an error in status, not an exception."""
    _store_signed_in_cache()
    FakeApp.silent_result = None

    status = svc.connection_status(_settings())

    assert status["connected"] is True
    assert "expired" in status["error"]


def test_login_replaces_an_unreadable_cache():
    """After a Fernet key change the old cache is unreadable; login must still succeed and overwrite it."""
    svc.token_cache_path().write_text("not-decryptable", encoding="utf-8")

    assert svc.login(_settings()) == "me@example.com"
    assert json.loads(
        security.security_service.decrypt(svc.token_cache_path().read_text(encoding="utf-8"))
    ) == json.loads(CACHE_JSON)


def test_a_new_login_does_not_inherit_the_previous_account():
    """Login starts from an empty cache, so the new account is not hidden behind the old one."""
    _store_signed_in_cache(json.dumps({"RefreshToken": {"old": {"secret": "OLD"}}}))
    svc.login(_settings())

    assert FakeApp.instances[-1].token_cache is not None
    assert "OLD" not in security.security_service.decrypt(svc.token_cache_path().read_text(encoding="utf-8"))


def test_server_reloads_when_the_cache_file_was_replaced_by_another_process():
    """`janus microsoft login` runs in another process; a running server must pick up the new sign-in."""
    settings = _settings()
    _store_signed_in_cache()
    svc.get_graph_token(settings)
    first = len(FakeApp.instances)

    svc.get_graph_token(settings)
    assert len(FakeApp.instances) == first  # unchanged file: same app

    path = svc.token_cache_path()
    path.write_text(security.security_service.encrypt(CACHE_JSON), encoding="utf-8")
    import os

    os.utime(path, ns=(1, path.stat().st_mtime_ns + 5_000_000_000))
    svc.get_graph_token(settings)
    assert len(FakeApp.instances) == first + 1


def test_cache_write_leaves_no_temp_file_and_is_private():
    """The cache is written atomically (temp file then rename) with mode 0600."""
    svc.login(_settings())

    path = svc.token_cache_path()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not path.with_name(path.name + ".tmp").exists()
