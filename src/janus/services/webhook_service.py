"""Inbound webhooks: owner-scoped URLs that emit ``webhook.received`` (JAN-406).

A user creates an endpoint and gets two values, each shown once: the URL
(``/api/webhooks/in/<key>``) and an HMAC secret. Only a SHA-256 hash of the key
is stored; the secret is Fernet-encrypted because verifying needs it.

A delivery is accepted only when it is signed with the endpoint's secret, in
one of two styles:

* **Janus style** (replay-protected): ``X-Janus-Timestamp: <unix seconds>`` and
  ``X-Janus-Signature: sha256=<hex HMAC-SHA256 of "<timestamp>." + body>``; the
  timestamp must be within :data:`MAX_CLOCK_SKEW_SECONDS` of now.
* **GitHub style**, so common senders work unchanged: ``X-Hub-Signature-256:
  sha256=<hex HMAC-SHA256 of the body>``.

The body must be JSON (at most :data:`MAX_BODY_BYTES`). Each delivery's event id
is a hash of the signed material, so the same delivery sent twice starts at
most one run per definition (``emit_event``'s occurrence key). The payload is
untrusted content: the trigger run sees it inside the escaped ``<event_data>``
block, and definitions narrow it with payload filters and their rate limit.
A simple per-endpoint limit (:data:`MAX_DELIVERIES_PER_MINUTE`) bounds floods.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
import time
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from janus import models, security, time_utils, trigger_definition_service
from janus.trigger_events import TriggerEvent

MAX_BODY_BYTES = 64 * 1024
MAX_CLOCK_SKEW_SECONDS = 300
MAX_DELIVERIES_PER_MINUTE = 60
MAX_BODY_CHARS = 4000
MAX_NAME_CHARS = 80
MAX_ENDPOINTS_PER_USER = 20
#: Event-type headers common senders use; the first present one becomes ``event_header``.
EVENT_HEADERS = ("x-github-event", "x-gitlab-event", "x-event-type", "x-janus-event")

_recent: dict[int, deque[float]] = {}
_recent_lock = threading.Lock()


class WebhookError(ValueError):
    """A request refused; ``status_code`` is the HTTP status the API answers with."""

    def __init__(self, message: str, status_code: int = 400):
        """Keep the HTTP status to answer with."""
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class CreatedEndpoint:
    """A new endpoint with the two values that are shown only now."""

    endpoint: models.WebhookEndpoint
    key: str
    secret: str


def _hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def _clean_name(name: str) -> str:
    cleaned = " ".join(str(name or "").split())
    if not cleaned or len(cleaned) > MAX_NAME_CHARS:
        raise WebhookError(f"give the webhook a name of at most {MAX_NAME_CHARS} characters")
    return cleaned


def _new_secret() -> str:
    return f"whsec_{secrets.token_urlsafe(32)}"


def create(db: Session, user: models.User, name: str) -> CreatedEndpoint:
    """Create an enabled endpoint; returns the key and secret, which are never stored in plain text."""
    count = db.query(models.WebhookEndpoint).filter(models.WebhookEndpoint.user_id == user.id).count()
    if count >= MAX_ENDPOINTS_PER_USER:
        raise WebhookError(f"a user can have at most {MAX_ENDPOINTS_PER_USER} webhooks")
    key = secrets.token_urlsafe(24)
    secret = _new_secret()
    endpoint = models.WebhookEndpoint(
        user_id=user.id,
        name=_clean_name(name),
        key_hash=_hash_key(key),
        key_prefix=key[:8],
        encrypted_secret=security.security_service.encrypt(secret),
        enabled=True,
    )
    db.add(endpoint)
    db.flush()
    return CreatedEndpoint(endpoint=endpoint, key=key, secret=secret)


def list_endpoints(db: Session, user: models.User) -> list[models.WebhookEndpoint]:
    """The user's endpoints, oldest first."""
    return (
        db.query(models.WebhookEndpoint)
        .filter(models.WebhookEndpoint.user_id == user.id)
        .order_by(models.WebhookEndpoint.id)
        .all()
    )


def get_endpoint(db: Session, user: models.User, endpoint_id: int) -> models.WebhookEndpoint | None:
    """One of the user's endpoints, or ``None``."""
    return (
        db.query(models.WebhookEndpoint)
        .filter(models.WebhookEndpoint.user_id == user.id, models.WebhookEndpoint.id == endpoint_id)
        .first()
    )


def update(endpoint: models.WebhookEndpoint, *, name: str | None = None, enabled: bool | None = None) -> None:
    """Rename or switch an endpoint on/off."""
    if name is not None:
        endpoint.name = _clean_name(name)
    if enabled is not None:
        endpoint.enabled = enabled


def rotate_secret(endpoint: models.WebhookEndpoint) -> str:
    """Replace the secret; the old one stops working at once. Returns the new secret (shown once)."""
    secret = _new_secret()
    endpoint.encrypted_secret = security.security_service.encrypt(secret)
    return secret


def _expected(secret: str, message: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def _verify(secret: str, body: bytes, headers: Mapping[str, str], now: float) -> str:
    """Check the signature and return the material the event id is derived from."""
    janus_signature = headers.get("x-janus-signature")
    if janus_signature:
        timestamp = headers.get("x-janus-timestamp", "")
        if not timestamp.isdigit() or abs(now - int(timestamp)) > MAX_CLOCK_SKEW_SECONDS:
            raise WebhookError("the X-Janus-Timestamp header is missing or too far from now", 401)
        signed = timestamp.encode() + b"." + body
        if not hmac.compare_digest(janus_signature.strip(), _expected(secret, signed)):
            raise WebhookError("the signature does not match", 401)
        return hashlib.sha256(signed).hexdigest()
    hub_signature = headers.get("x-hub-signature-256")
    if hub_signature:
        if not hmac.compare_digest(hub_signature.strip(), _expected(secret, body)):
            raise WebhookError("the signature does not match", 401)
        return hashlib.sha256(body).hexdigest()
    raise WebhookError("the request is not signed (X-Janus-Signature or X-Hub-Signature-256)", 401)


def _admit_rate(endpoint_id: int, now: float) -> None:
    with _recent_lock:
        window = _recent.setdefault(endpoint_id, deque())
        while window and now - window[0] > 60:
            window.popleft()
        if len(window) >= MAX_DELIVERIES_PER_MINUTE:
            raise WebhookError("too many deliveries to this webhook; try again in a minute", 429)
        window.append(now)


def receive(db: Session, key: str, body: bytes, headers: Mapping[str, str], *, now: float | None = None) -> int:
    """Verify and publish one delivery; returns how many runs it started. Raises :class:`WebhookError`."""
    now = time.time() if now is None else now
    endpoint = db.query(models.WebhookEndpoint).filter(models.WebhookEndpoint.key_hash == _hash_key(key)).first()
    if endpoint is None or not endpoint.enabled:
        raise WebhookError("no such webhook", 404)
    if len(body) > MAX_BODY_BYTES:
        raise WebhookError(f"the body is larger than {MAX_BODY_BYTES} bytes", 413)
    secret = security.security_service.decrypt(endpoint.encrypted_secret)
    material = _verify(secret, body, {name.lower(): value for name, value in headers.items()}, now)
    try:
        parsed: Any = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise WebhookError("the body is not JSON") from exc
    _admit_rate(endpoint.id, now)
    lowered = {name.lower(): value for name, value in headers.items()}
    event_header = next((lowered[name] for name in EVENT_HEADERS if lowered.get(name)), "")
    compact = json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
    received_at = time_utils.utcnow()
    data = {
        "webhook": endpoint.name,
        "webhook_id": endpoint.id,
        "event_header": event_header[:100],
        "received_at": received_at.isoformat(),
        "body": compact[:MAX_BODY_CHARS],
    }
    runs = trigger_definition_service.emit_event(
        db, endpoint.user_id, TriggerEvent.WEBHOOK_RECEIVED, f"{endpoint.id}:{material[:32]}", data
    )
    endpoint.received_count = (endpoint.received_count or 0) + 1
    endpoint.last_received_at = received_at
    db.flush()
    return len(runs)


def delete(db: Session, endpoint: models.WebhookEndpoint) -> None:
    """Delete an endpoint; its URL stops working at once."""
    db.delete(endpoint)
    db.flush()


__all__ = [
    "MAX_BODY_BYTES",
    "MAX_CLOCK_SKEW_SECONDS",
    "MAX_DELIVERIES_PER_MINUTE",
    "CreatedEndpoint",
    "WebhookError",
    "create",
    "delete",
    "get_endpoint",
    "list_endpoints",
    "receive",
    "rotate_secret",
    "update",
]
