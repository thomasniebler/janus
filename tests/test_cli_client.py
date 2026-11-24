"""Tests for the CLI's local token cache persistence."""

import json
import stat

import requests

from janus import cli_client


def _set_token_paths(monkeypatch, tmp_path):
    token_path = tmp_path / "config" / "janus" / "token.json"
    legacy_path = tmp_path / ".janus_token"
    monkeypatch.setattr(cli_client, "TOKEN_FILE", token_path)
    monkeypatch.setattr(cli_client, "LEGACY_TOKEN_FILE", legacy_path)
    return token_path, legacy_path


def test_save_token_uses_xdg_config_location_with_private_permissions(monkeypatch, tmp_path):
    """New CLI logins cache their token only in the Janus config directory."""
    token_path, legacy_path = _set_token_paths(monkeypatch, tmp_path)

    client = cli_client.JanusClient()
    client.save_token({"access_token": "new-token"})

    assert json.loads(token_path.read_text(encoding="utf-8")) == {"access_token": "new-token"}
    assert not legacy_path.exists()
    assert stat.S_IMODE(token_path.stat().st_mode) == 0o600


def test_load_token_migrates_legacy_home_cache(monkeypatch, tmp_path):
    """An existing ~/.janus_token cache is moved without logging the user out."""
    token_path, legacy_path = _set_token_paths(monkeypatch, tmp_path)
    legacy_path.write_text('{"access_token": "legacy-token"}', encoding="utf-8")

    client = cli_client.JanusClient()

    assert client.token == "legacy-token"
    assert json.loads(token_path.read_text(encoding="utf-8")) == {"access_token": "legacy-token"}
    assert not legacy_path.exists()


def test_current_token_cache_takes_precedence_and_removes_legacy_cache(monkeypatch, tmp_path):
    """A current cache is never replaced by a stale token left in the old location."""
    token_path, legacy_path = _set_token_paths(monkeypatch, tmp_path)
    token_path.parent.mkdir(parents=True)
    token_path.write_text('{"access_token": "current-token"}', encoding="utf-8")
    legacy_path.write_text('{"access_token": "legacy-token"}', encoding="utf-8")

    client = cli_client.JanusClient()

    assert client.token == "current-token"
    assert not legacy_path.exists()


def test_malformed_current_cache_does_not_prevent_legacy_migration(monkeypatch, tmp_path):
    """A malformed new cache cannot log out a user with a valid legacy token."""
    token_path, legacy_path = _set_token_paths(monkeypatch, tmp_path)
    token_path.parent.mkdir(parents=True)
    token_path.write_text("[]", encoding="utf-8")
    legacy_path.write_text('{"access_token": "legacy-token"}', encoding="utf-8")

    client = cli_client.JanusClient()

    assert client.token == "legacy-token"
    assert json.loads(token_path.read_text(encoding="utf-8")) == {"access_token": "legacy-token"}


def test_unauthorized_response_clears_current_and_legacy_caches(monkeypatch, tmp_path):
    """A rejected session cannot leave either cache location reusable."""
    token_path, legacy_path = _set_token_paths(monkeypatch, tmp_path)
    token_path.parent.mkdir(parents=True)
    token_path.write_text('{"access_token": "current-token"}', encoding="utf-8")
    legacy_path.write_text('{"access_token": "legacy-token"}', encoding="utf-8")
    client = cli_client.JanusClient()

    response = requests.Response()
    response.status_code = 401
    monkeypatch.setattr(cli_client.requests, "request", lambda *args, **kwargs: response)

    client.request("GET", "/api/auth/me")

    assert client.token is None
    assert not token_path.exists()
    assert not legacy_path.exists()
