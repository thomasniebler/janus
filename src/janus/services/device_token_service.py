"""Device tokens for the action inbox: create, list, revoke, authenticate.

A device token lets a script or device (an ``eww`` popup, a phone) read the owner's inbox and take
the low-risk inbox actions, and nothing else: ``security.get_token`` parses every bearer as a JWT, so
an opaque ``jdt_...`` token is refused on every other endpoint, and only the inbox router resolves it
(:func:`authenticate`). The plaintext is returned once by :func:`create_token`; only its SHA-256 and a
short prefix are stored, so a database leak does not leak a usable token.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta

from janus import models
from janus.services.base import BaseService

TOKEN_PREFIX = "jdt_"
#: Tokens one owner may hold at the same time (revoked ones do not count).
MAX_ACTIVE_TOKENS = 10
MAX_NAME_CHARS = 80
#: ``last_used_at`` is written at most this often, so a polling popup does not write on every request.
LAST_USED_RESOLUTION = timedelta(minutes=1)


class DeviceTokenError(ValueError):
    """Raised for a bad name or too many active tokens."""


def hash_token(raw: str) -> str:
    """Return the SHA-256 hex digest stored for a token."""
    return hashlib.sha256(raw.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class DeviceTokenService(BaseService):
    """Owner-scoped device token management."""

    def list_tokens(self, user_id: int) -> list[models.DeviceToken]:
        """Return the owner's active (not revoked) tokens, newest first."""
        return (
            self.db.query(models.DeviceToken)
            .filter(models.DeviceToken.user_id == user_id, models.DeviceToken.revoked_at.is_(None))
            .order_by(models.DeviceToken.id.desc())
            .all()
        )

    def create_token(self, user_id: int, name: str) -> tuple[models.DeviceToken, str]:
        """Create a token for ``user_id`` and return it with its plaintext, which is never stored."""
        clean = " ".join((name or "").split())
        if not clean:
            raise DeviceTokenError("the token needs a name, such as the device it is for")
        if len(clean) > MAX_NAME_CHARS:
            raise DeviceTokenError(f"the name may be at most {MAX_NAME_CHARS} characters")
        if len(self.list_tokens(user_id)) >= MAX_ACTIVE_TOKENS:
            raise DeviceTokenError(f"at most {MAX_ACTIVE_TOKENS} active tokens; revoke one first")
        raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
        record = models.DeviceToken(
            user_id=user_id, name=clean, token_hash=hash_token(raw), token_prefix=raw[: len(TOKEN_PREFIX) + 4]
        )
        self.db.add(record)
        self.db.commit()
        self.db.refresh(record)
        return record, raw

    def revoke_token(self, user_id: int, token_id: int) -> bool:
        """Revoke one of the owner's tokens; ``False`` for an unknown, foreign, or already revoked id."""
        record = (
            self.db.query(models.DeviceToken)
            .filter(
                models.DeviceToken.id == token_id,
                models.DeviceToken.user_id == user_id,
                models.DeviceToken.revoked_at.is_(None),
            )
            .first()
        )
        if record is None:
            return False
        record.revoked_at = _now()
        self.db.commit()
        return True

    def revoke_all(self, user_id: int) -> None:
        """Revoke every active token of the owner (no commit: the caller's transaction commits).

        Called when the owner's sessions are invalidated (password change, revoke sessions), so account
        recovery also closes the devices.
        """
        self.db.query(models.DeviceToken).filter(
            models.DeviceToken.user_id == user_id, models.DeviceToken.revoked_at.is_(None)
        ).update({models.DeviceToken.revoked_at: _now()}, synchronize_session=False)

    def delete_all(self, user_id: int) -> None:
        """Delete every token row of the owner (no commit), for account deletion.

        SQLite enforces no foreign key and may reuse a deleted user's id, so a row left behind could
        authenticate as a later user.
        """
        self.db.query(models.DeviceToken).filter(models.DeviceToken.user_id == user_id).delete(
            synchronize_session=False
        )

    def authenticate(self, raw: str | None) -> models.User | None:
        """Return the owner of an active token, or ``None``; records the use (at most once a minute)."""
        if not raw or not raw.startswith(TOKEN_PREFIX):
            return None
        digest = hash_token(raw)
        record = (
            self.db.query(models.DeviceToken)
            .filter(models.DeviceToken.token_hash == digest, models.DeviceToken.revoked_at.is_(None))
            .first()
        )
        # The lookup is by hash, but compare again in constant time before trusting the row.
        if record is None or not hmac.compare_digest(record.token_hash, digest):
            return None
        now = _now()
        if record.last_used_at is None or now - record.last_used_at >= LAST_USED_RESOLUTION:
            record.last_used_at = now
            self.db.commit()
        return self.db.get(models.User, record.user_id)


__all__ = [
    "LAST_USED_RESOLUTION",
    "MAX_ACTIVE_TOKENS",
    "MAX_NAME_CHARS",
    "TOKEN_PREFIX",
    "DeviceTokenError",
    "DeviceTokenService",
    "hash_token",
]
