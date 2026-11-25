"""Tests for inbound webhooks and the ``webhook.received`` event (JAN-406)."""

from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest

from janus import models, security
from janus import trigger_definition_service as definitions
from janus.services import webhook_service
from janus.trigger_events import EVENTS, TriggerEvent


def _user(db, name="hook-user") -> models.User:
    user = models.User(username=name, hashed_password=security.security_service.get_password_hash("secret"))
    db.add(user)
    db.commit()
    return user


def _definition(db, user_id, **overrides):
    values = {
        "name": "On webhook",
        "objective": "Summarize the delivery.",
        "tool_ids": ["core"],
        "kind": "event",
        "event_type": TriggerEvent.WEBHOOK_RECEIVED.value,
    }
    values.update(overrides)
    return definitions.create_definition(db, user_id, **values)


def _janus_headers(secret: str, body: bytes, timestamp: int | None = None) -> dict[str, str]:
    ts = str(int(time.time()) if timestamp is None else timestamp)
    digest = hmac.new(secret.encode(), ts.encode() + b"." + body, hashlib.sha256).hexdigest()
    return {"X-Janus-Timestamp": ts, "X-Janus-Signature": f"sha256={digest}", "Content-Type": "application/json"}


def _hub_headers(secret: str, body: bytes, event: str = "push") -> dict[str, str]:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return {"X-Hub-Signature-256": f"sha256={digest}", "X-GitHub-Event": event}


def _runs(db, user_id) -> list[models.RuntimeTrigger]:
    return db.query(models.RuntimeTrigger).filter(models.RuntimeTrigger.user_id == user_id).all()


@pytest.fixture(autouse=True)
def _fresh_rate_limits():
    webhook_service._recent.clear()
    yield
    webhook_service._recent.clear()


# --- service ---------------------------------------------------------------------------------


def test_create_stores_only_a_key_hash_and_an_encrypted_secret(db):
    """Neither the key nor the secret is stored in plain text."""
    created = webhook_service.create(db, _user(db), "  GitHub  pushes ")
    endpoint = created.endpoint
    assert endpoint.name == "GitHub pushes"
    assert endpoint.key_hash == hashlib.sha256(created.key.encode()).hexdigest()
    assert created.key not in endpoint.key_hash and endpoint.key_prefix == created.key[:8]
    assert created.secret not in endpoint.encrypted_secret
    assert security.security_service.decrypt(endpoint.encrypted_secret) == created.secret


def test_signed_delivery_emits_the_event_and_starts_a_run(db):
    """A Janus-signed JSON delivery starts one run with the delivery as untrusted event data."""
    user = _user(db)
    _definition(db, user.id)
    created = webhook_service.create(db, user, "Deploys")
    body = json.dumps({"status": "failed", "service": "api"}).encode()
    runs = webhook_service.receive(db, created.key, body, _janus_headers(created.secret, body))
    assert runs == 1
    (run,) = _runs(db, user.id)
    event = run.payload["event"]
    assert event["type"] == "webhook.received"
    assert event["webhook"] == "Deploys"
    assert json.loads(event["body"]) == {"status": "failed", "service": "api"}
    assert "<event_data>" in json.dumps(run.payload)
    assert created.endpoint.received_count == 1 and created.endpoint.last_received_at is not None


def test_github_style_signature_and_event_header(db):
    """X-Hub-Signature-256 is accepted and X-GitHub-Event becomes filterable."""
    user = _user(db)
    _definition(db, user.id, event_filter=[{"field": "event_header", "op": "in", "values": ["push"]}])
    created = webhook_service.create(db, user, "GitHub")
    body = b'{"ref":"refs/heads/main"}'
    assert webhook_service.receive(db, created.key, body, _hub_headers(created.secret, body, "push")) == 1
    assert webhook_service.receive(db, created.key, b'{"a":1}', _hub_headers(created.secret, b'{"a":1}', "issues")) == 0


def test_the_same_delivery_twice_starts_one_run(db):
    """Redelivery of identical signed material is deduplicated by the occurrence key."""
    user = _user(db)
    _definition(db, user.id)
    created = webhook_service.create(db, user, "Dup")
    body = b'{"x":1}'
    headers = _janus_headers(created.secret, body)
    assert webhook_service.receive(db, created.key, body, headers) == 1
    assert webhook_service.receive(db, created.key, body, headers) == 0
    assert len(_runs(db, user.id)) == 1


@pytest.mark.parametrize(
    ("make_headers", "status"),
    [
        (lambda secret, body: {}, 401),
        (lambda secret, body: _janus_headers("wrong-secret", body), 401),
        (lambda secret, body: _janus_headers(secret, body, timestamp=int(time.time()) - 3600), 401),
        (lambda secret, body: _hub_headers("wrong-secret", body), 401),
        (lambda secret, body: {**_janus_headers(secret, body), "X-Janus-Timestamp": "soon"}, 401),
    ],
)
def test_unsigned_wrongly_signed_or_stale_deliveries_are_refused(db, make_headers, status):
    """Nothing is emitted without a valid, fresh signature."""
    user = _user(db)
    _definition(db, user.id)
    created = webhook_service.create(db, user, "Strict")
    body = b'{"x":1}'
    with pytest.raises(webhook_service.WebhookError) as raised:
        webhook_service.receive(db, created.key, body, make_headers(created.secret, body))
    assert raised.value.status_code == status
    assert _runs(db, user.id) == [] and created.endpoint.received_count == 0


def test_unknown_disabled_oversized_and_non_json_deliveries(db):
    """Unknown or switched-off URLs are 404; too-large bodies 413; signed non-JSON 400."""
    user = _user(db)
    created = webhook_service.create(db, user, "Edge")
    with pytest.raises(webhook_service.WebhookError) as unknown:
        webhook_service.receive(db, "not-a-key", b"{}", {})
    assert unknown.value.status_code == 404
    big = b'{"x":"' + b"a" * webhook_service.MAX_BODY_BYTES + b'"}'
    with pytest.raises(webhook_service.WebhookError) as large:
        webhook_service.receive(db, created.key, big, _janus_headers(created.secret, big))
    assert large.value.status_code == 413
    with pytest.raises(webhook_service.WebhookError) as not_json:
        webhook_service.receive(db, created.key, b"hello", _janus_headers(created.secret, b"hello"))
    assert not_json.value.status_code == 400
    webhook_service.update(created.endpoint, enabled=False)
    with pytest.raises(webhook_service.WebhookError) as disabled:
        webhook_service.receive(db, created.key, b"{}", _janus_headers(created.secret, b"{}"))
    assert disabled.value.status_code == 404


def test_rate_limit_per_endpoint(db, monkeypatch):
    """Deliveries beyond the per-minute limit are refused with 429."""
    monkeypatch.setattr(webhook_service, "MAX_DELIVERIES_PER_MINUTE", 2)
    created = webhook_service.create(db, _user(db), "Busy")
    for index in range(2):
        body = json.dumps({"n": index}).encode()
        webhook_service.receive(db, created.key, body, _janus_headers(created.secret, body))
    with pytest.raises(webhook_service.WebhookError) as limited:
        webhook_service.receive(db, created.key, b'{"n":9}', _janus_headers(created.secret, b'{"n":9}'))
    assert limited.value.status_code == 429


def test_rotating_the_secret_invalidates_the_old_one(db):
    """After a rotation only the new secret signs valid deliveries."""
    created = webhook_service.create(db, _user(db), "Rotate")
    new_secret = webhook_service.rotate_secret(created.endpoint)
    with pytest.raises(webhook_service.WebhookError):
        webhook_service.receive(db, created.key, b"{}", _janus_headers(created.secret, b"{}"))
    assert webhook_service.receive(db, created.key, b"{}", _janus_headers(new_secret, b"{}")) == 0


def test_runs_reach_only_the_owner_and_the_name_filter_narrows(db):
    """A delivery starts runs only for the endpoint's owner, and a name filter picks one sender."""
    owner, other = _user(db, "owner"), _user(db, "other")
    _definition(db, owner.id, event_filter=[{"field": "webhook", "op": "in", "values": ["Alerts"]}])
    _definition(db, other.id)
    alerts = webhook_service.create(db, owner, "Alerts")
    noise = webhook_service.create(db, owner, "Noise")
    assert webhook_service.receive(db, noise.key, b"{}", _janus_headers(noise.secret, b"{}")) == 0
    assert webhook_service.receive(db, alerts.key, b"{}", _janus_headers(alerts.secret, b"{}")) == 1
    assert _runs(db, other.id) == []


def test_catalog_entry_has_a_default_rate_limit():
    """The event is catalogued with a default limit, since senders can flood."""
    spec = EVENTS[TriggerEvent.WEBHOOK_RECEIVED]
    assert spec.default_max_runs_per_hour == 30
    assert {field.name for field in spec.payload} == {"webhook", "webhook_id", "event_header", "received_at", "body"}


# --- API -------------------------------------------------------------------------------------


@pytest.fixture
def auth_client(client, db):
    """A cookie-authenticated client with CSRF configured."""
    _user(db, "hook-api-user")
    response = client.post("/api/auth/login", json={"username": "hook-api-user", "password": "secret"})
    client.headers[security.CSRF_HEADER_NAME] = response.json()["csrf_token"]
    return client


def test_api_lifecycle_and_public_delivery(auth_client, client):
    """Create shows the URL and secret once; delivery needs no session; list never shows secrets."""
    created = auth_client.post("/api/webhooks", json={"name": "CI"})
    assert created.status_code == 201, created.text
    body = created.json()
    path, secret = body["path"], body["secret"]
    assert path.startswith("/api/webhooks/in/") and secret.startswith("whsec_")
    listed = auth_client.get("/api/webhooks").text
    assert secret not in listed and path.rsplit("/", 1)[1] not in listed

    payload = b'{"ok":true}'
    # The sender has no session: drop the cookies and the CSRF header for the delivery.
    saved_cookies = dict(client.cookies)
    csrf = client.headers.pop(security.CSRF_HEADER_NAME)
    client.cookies.clear()
    try:
        delivered = client.post(path, content=payload, headers=_janus_headers(secret, payload))
        assert delivered.status_code == 202, delivered.text
        assert delivered.json() == {"accepted": True, "runs": 0}
        assert client.post(path, content=payload, headers={}).status_code == 401
    finally:
        client.cookies.update(saved_cookies)
        client.headers[security.CSRF_HEADER_NAME] = csrf

    endpoint_id = body["webhook"]["id"]
    assert auth_client.get("/api/webhooks").json()[0]["received_count"] == 1
    rotated = auth_client.post(f"/api/webhooks/{endpoint_id}/rotate-secret").json()
    assert rotated["secret"] != secret and rotated["path"] is None
    assert auth_client.patch(f"/api/webhooks/{endpoint_id}", json={"enabled": False}).json()["enabled"] is False
    assert auth_client.delete(f"/api/webhooks/{endpoint_id}").status_code == 204
    assert auth_client.get("/api/webhooks").json() == []


def test_api_is_owner_scoped(auth_client, db):
    """Another user's webhook is a 404."""
    theirs = webhook_service.create(db, _user(db, "someone"), "Theirs")
    db.commit()
    assert auth_client.patch(f"/api/webhooks/{theirs.endpoint.id}", json={"enabled": False}).status_code == 404
    assert auth_client.post(f"/api/webhooks/{theirs.endpoint.id}/rotate-secret").status_code == 404


def test_delivery_ignores_a_session_cookie_without_csrf_header(auth_client):
    """Regression: a sender that holds a Janus session cookie (but no CSRF header) is judged by the signature only."""
    created = auth_client.post("/api/webhooks", json={"name": "Cookie"}).json()
    csrf = auth_client.headers.pop(security.CSRF_HEADER_NAME)
    try:
        payload = b'{"from":"browser"}'
        delivered = auth_client.post(
            created["path"], content=payload, headers=_janus_headers(created["secret"], payload)
        )
        assert delivered.status_code == 202, delivered.text
        # The CSRF check still guards the cookie-authenticated management API.
        assert auth_client.post("/api/webhooks", json={"name": "No CSRF"}).status_code == 403
    finally:
        auth_client.headers[security.CSRF_HEADER_NAME] = csrf


def test_redelivery_to_a_replacement_endpoint_is_not_taken_for_a_duplicate(db):
    """Regression: an endpoint that reuses a deleted endpoint's row id still starts its own runs."""
    user = _user(db)
    _definition(db, user.id)
    old = webhook_service.create(db, user, "Old")
    body = b'{"same":"body"}'
    assert webhook_service.receive(db, old.key, body, _hub_headers(old.secret, body)) == 1
    old_id = old.endpoint.id
    webhook_service.delete(db, old.endpoint)
    db.commit()
    new = webhook_service.create(db, user, "New")
    # The scenario: SQLite handed the new endpoint the deleted one's id.
    assert new.endpoint.id == old_id
    assert webhook_service.receive(db, new.key, body, _hub_headers(new.secret, body)) == 1


def test_deleting_a_user_removes_their_webhooks_and_remote_agents(client, db):
    """Regression: SQLite does not cascade, so user deletion removes credential-bearing rows explicitly."""
    admin = models.User(
        username="hook-admin", hashed_password=security.security_service.get_password_hash("secret"), is_admin=True
    )
    db.add(admin)
    victim = _user(db, "leaving")
    webhook_service.create(db, victim, "Theirs")
    db.add(models.A2AAgent(user_id=victim.id, name="Agent", base_url="https://agent.example.com", encrypted_token="x"))
    db.commit()
    login = client.post("/api/auth/login", json={"username": "hook-admin", "password": "secret"})
    client.headers[security.CSRF_HEADER_NAME] = login.json()["csrf_token"]
    assert client.delete("/api/auth/users/leaving").status_code == 200
    db.expire_all()
    assert db.query(models.WebhookEndpoint).count() == 0
    assert db.query(models.A2AAgent).count() == 0
