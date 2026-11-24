"""Tests for error API."""

from janus import models, security


def _login_user(client, db, *, username: str, is_admin: bool) -> None:
    """Create and authenticate a test user."""
    user = models.User(
        username=username,
        hashed_password=security.security_service.get_password_hash("password"),
        is_admin=is_admin,
    )
    db.add(user)
    db.commit()

    response = client.post("/api/auth/login", json={"username": username, "password": "password"})
    assert response.status_code == 200
    client.headers[security.CSRF_HEADER_NAME] = response.json()["csrf_token"]


def test_list_errors_requires_auth(client):
    """List errors endpoint requires authentication."""
    response = client.get("/api/errors/groups")
    assert response.status_code == 401


def test_error_endpoints_require_admin(client, db):
    """Error grouping endpoints should be restricted to admin users."""
    _login_user(client, db, username="viewer", is_admin=False)

    group = models.ErrorGroup(
        fingerprint="group-1",
        source="PlannerSync",
        message_template="Job failed <NUM>",
    )
    db.add(group)
    db.commit()

    list_response = client.get("/api/errors/groups")
    detail_response = client.get(f"/api/errors/groups/{group.id}")
    delete_response = client.delete(f"/api/errors/groups/{group.id}")
    compact_response = client.post("/api/errors/compact")

    assert list_response.status_code == 403
    assert detail_response.status_code == 403
    assert delete_response.status_code == 403
    assert compact_response.status_code == 403


def test_admin_can_list_and_get_error_groups(client, db):
    """Admin users should be able to view grouped error details."""
    _login_user(client, db, username="admin", is_admin=True)

    group = models.ErrorGroup(
        fingerprint="group-2",
        source="PlannerSync",
        message_template="Job failed <NUM>",
        occurrence_count=2,
        sample_details='{"request_id": 42}',
    )
    db.add(group)
    db.commit()

    list_response = client.get("/api/errors/groups")
    detail_response = client.get(f"/api/errors/groups/{group.id}")

    assert list_response.status_code == 200
    assert list_response.json()[0]["id"] == group.id
    assert detail_response.status_code == 200
    assert detail_response.json()["sample_details"] == '{"request_id": 42}'
