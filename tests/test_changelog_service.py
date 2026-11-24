"""Release notes parsing and the version endpoint."""

import pytest

from janus import models, security
from janus.services import changelog_service

SAMPLE = """# Changelog

## [1.13.0](https://github.com/o/r/compare/v1.12.0...v1.13.0) (2026-11-11)


### Features

* **chat:** narrate multi-step turns ([#619](https://github.com/o/r/issues/619)) ([da9239f](https://github.com/o/r/commit/da9239f))
* **stats:** approval stats ([#618](https://github.com/o/r/issues/618)) ([6ccf440](https://github.com/o/r/commit/6ccf440))


### Bug Fixes

* **db:** WAL mode ([#613](https://github.com/o/r/issues/613)) ([5477207](https://github.com/o/r/commit/5477207))

## [1.12.0](https://github.com/o/r/compare/v1.11.0...v1.12.0) (2026-11-10)


### Features

* Microsoft 365 tools ([#610](https://github.com/o/r/issues/610)) ([b75f8f6](https://github.com/o/r/commit/b75f8f6))

## [1.11.0](https://github.com/o/r/compare/v1.10.0...v1.11.0) (2026-11-10)


### Features

* Older thing ([#1](https://github.com/o/r/issues/1)) ([abc](https://github.com/o/r/commit/abc))
"""


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


def test_parse_changelog_groups_plain_bullets_per_release():
    """Bullets lose the commit and issue links and keep their scope."""
    releases = changelog_service.parse_changelog(SAMPLE)

    assert [r.version for r in releases] == ["1.13.0", "1.12.0", "1.11.0"]
    assert releases[0].date == "2026-11-11"
    assert releases[0].groups == {
        "Features": ["**chat:** narrate multi-step turns", "**stats:** approval stats"],
        "Bug Fixes": ["**db:** WAL mode"],
    }


def test_releases_since_returns_the_unseen_releases_newest_first():
    """Everything after the last seen version up to the running one."""
    releases = changelog_service.parse_changelog(SAMPLE)

    assert [r.version for r in changelog_service.releases_since(releases, "1.11.0", "1.13.0")] == ["1.13.0", "1.12.0"]
    assert [r.version for r in changelog_service.releases_since(releases, "1.12.0", "1.13.0")] == ["1.13.0"]


def test_releases_since_is_empty_without_a_previous_version_or_an_update():
    """A first visit, an unchanged version and a downgrade have nothing to flash."""
    releases = changelog_service.parse_changelog(SAMPLE)

    assert changelog_service.releases_since(releases, None, "1.13.0") == []
    assert changelog_service.releases_since(releases, "1.13.0", "1.13.0") == []
    assert changelog_service.releases_since(releases, "1.14.0", "1.13.0") == []
    assert changelog_service.releases_since(releases, "nonsense", "1.13.0") == []


def test_a_missing_changelog_has_no_releases(tmp_path):
    """An install without CHANGELOG.md shows no notes instead of failing."""
    assert changelog_service.load_releases(tmp_path / "CHANGELOG.md") == []


def test_the_repository_changelog_parses():
    """The real release-please file keeps the shape the parser expects."""
    releases = changelog_service.load_releases()

    assert releases
    assert all(r.groups for r in releases[:3])


def test_version_endpoint_reports_the_version_and_unseen_notes(auth_client, monkeypatch):
    """The endpoint needs a login and returns the notes after ``since``."""
    client, _ = auth_client
    from janus.api import status as status_api

    monkeypatch.setattr(status_api, "_janus_version", lambda: "1.13.0")
    monkeypatch.setattr(changelog_service, "load_releases", lambda: changelog_service.parse_changelog(SAMPLE))

    body = client.get("/api/status/version?since=1.12.0").json()

    assert body["version"] == "1.13.0"
    assert body["changelog_url"] == changelog_service.CHANGELOG_DOCS_PATH
    assert body["release_url"].endswith("/tag/v1.13.0")
    assert [r["version"] for r in body["releases"]] == ["1.13.0"]
    assert body["releases"][0]["groups"][0] == {
        "title": "Features",
        "items": ["**chat:** narrate multi-step turns", "**stats:** approval stats"],
    }
    assert client.get("/api/status/version").json()["releases"] == []


def test_version_endpoint_requires_a_login(client):
    """Anonymous callers get 401."""
    assert client.get("/api/status/version").status_code == 401


def test_a_release_without_a_compare_link_keeps_its_date():
    """release-please's first release has no compare link; its date must not be eaten as the link."""
    releases = changelog_service.parse_changelog("## 1.0.0 (2026-01-01)\n\n### Features\n\n* First thing\n")

    assert (releases[0].version, releases[0].date) == ("1.0.0", "2026-01-01")


def test_an_unrecognised_heading_does_not_leak_into_the_previous_release():
    """A pre-release heading is skipped; its bullets are not added to the release above it."""
    text = SAMPLE.replace("## [1.12.0]", "## [1.12.0-rc.1]")

    releases = changelog_service.parse_changelog(text)

    assert [r.version for r in releases] == ["1.13.0", "1.11.0"]
    assert releases[0].groups == {
        "Features": ["**chat:** narrate multi-step turns", "**stats:** approval stats"],
        "Bug Fixes": ["**db:** WAL mode"],
    }


def test_load_releases_is_cached_until_the_file_changes(tmp_path):
    """The file is parsed once per modification, not on every request."""
    path = tmp_path / "CHANGELOG.md"
    path.write_text("## 1.0.0 (2026-01-01)\n\n### Features\n\n* One\n")

    first = changelog_service.load_releases(path)

    assert changelog_service.load_releases(path) is first
    path.write_text("## 1.1.0 (2026-02-01)\n\n### Features\n\n* Two\n")
    import os

    os.utime(path, (path.stat().st_atime, path.stat().st_mtime + 5))
    assert changelog_service.load_releases(path)[0].version == "1.1.0"
