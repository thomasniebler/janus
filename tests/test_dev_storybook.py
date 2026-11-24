"""Tests for the admin-gated /dev/storybook static-file route."""

import pytest
from fastapi import HTTPException

from janus import models, security
from janus.api import dev as dev_api

# db and client fixtures are provided by conftest.py


@pytest.fixture
def auth_client(client, db):
    """Returns a client with an authenticated admin session."""
    hashed_pw = security.security_service.get_password_hash("admin")
    user = models.User(username="admin", hashed_password=hashed_pw, is_admin=True)
    db.add(user)
    db.commit()

    response = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    client.headers[security.CSRF_HEADER_NAME] = response.json()["csrf_token"]
    return client


def _write_fake_storybook_build(tmp_path):
    (tmp_path / "index.html").write_text("<html><body>Storybook</body></html>", encoding="utf-8")
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    (assets_dir / "main.js").write_text("console.log('storybook');", encoding="utf-8")
    return tmp_path


def test_storybook_index_served_for_admin(auth_client, monkeypatch, tmp_path):
    """An admin can load the Storybook index page."""
    monkeypatch.setattr(dev_api, "STORYBOOK_DIR", _write_fake_storybook_build(tmp_path))

    response = auth_client.get("/dev/storybook")

    assert response.status_code == 200
    assert "Storybook" in response.text


def test_storybook_root_redirects_to_trailing_slash(auth_client, monkeypatch, tmp_path):
    """The bare (no trailing slash) route redirects so relative asset URLs resolve correctly."""
    monkeypatch.setattr(dev_api, "STORYBOOK_DIR", _write_fake_storybook_build(tmp_path))

    response = auth_client.get("/dev/storybook", follow_redirects=False)

    assert response.status_code in (307, 308)
    assert response.headers["location"] == "/dev/storybook/"


def test_storybook_asset_served_for_admin(auth_client, monkeypatch, tmp_path):
    """An admin can load a specific built asset file."""
    monkeypatch.setattr(dev_api, "STORYBOOK_DIR", _write_fake_storybook_build(tmp_path))

    response = auth_client.get("/dev/storybook/assets/main.js")

    assert response.status_code == 200
    assert "storybook" in response.text


def test_storybook_unknown_path_falls_back_to_index(auth_client, monkeypatch, tmp_path):
    """A client-side Storybook route with no matching file still serves the index shell."""
    monkeypatch.setattr(dev_api, "STORYBOOK_DIR", _write_fake_storybook_build(tmp_path))

    response = auth_client.get("/dev/storybook/some/client/route")

    assert response.status_code == 200
    assert "Storybook" in response.text


def test_storybook_requires_admin(client, db, monkeypatch, tmp_path):
    """A non-admin user is forbidden from the Storybook route."""
    monkeypatch.setattr(dev_api, "STORYBOOK_DIR", _write_fake_storybook_build(tmp_path))
    db.add(
        models.User(
            username="member",
            hashed_password=security.security_service.get_password_hash("password"),
            is_admin=False,
        )
    )
    db.commit()
    login = client.post("/api/auth/login", json={"username": "member", "password": "password"})
    client.headers[security.CSRF_HEADER_NAME] = login.json()["csrf_token"]

    response = client.get("/dev/storybook")

    assert response.status_code == 403


def test_storybook_requires_auth(client, monkeypatch, tmp_path):
    """An anonymous request is rejected."""
    monkeypatch.setattr(dev_api, "STORYBOOK_DIR", _write_fake_storybook_build(tmp_path))

    response = client.get("/dev/storybook")

    assert response.status_code == 401


def test_storybook_missing_build_returns_404(auth_client, monkeypatch, tmp_path):
    """A missing Storybook build reports 404 instead of a stray SPA fallback."""
    monkeypatch.setattr(dev_api, "STORYBOOK_DIR", tmp_path / "does-not-exist")

    response = auth_client.get("/dev/storybook")

    assert response.status_code == 404


def test_resolve_storybook_file_rejects_path_traversal(monkeypatch, tmp_path):
    """A path-traversal attempt is rejected rather than escaping the Storybook build directory.

    Exercised directly against the resolver: an HTTP client normalizes ".." segments out of a
    URL before the request is even sent, so this can't be reliably driven through `auth_client`.
    """
    monkeypatch.setattr(dev_api, "STORYBOOK_DIR", _write_fake_storybook_build(tmp_path))

    with pytest.raises(HTTPException) as exc_info:
        dev_api._resolve_storybook_file("../../pyproject.toml")

    assert exc_info.value.status_code == 400
