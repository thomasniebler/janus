"""Authentication service for Janus."""

import logging
import math
import secrets
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Lock

from sqlalchemy.exc import IntegrityError

from janus import config, event_logger, models, security
from janus.services import base
from janus.services.device_token_service import DeviceTokenService

logger = logging.getLogger(__name__)
_SETUP_TOKEN_LIFETIME_MINUTES = 15
_setup_token: str | None = None
_setup_token_expires_at: datetime | None = None
_AUTH_EVENT_SOURCE = "Auth"
_REVOKED_SESSION_PRUNE_INTERVAL = timedelta(minutes=15)
_last_revoked_session_prune_at: datetime | None = None
_revoked_session_prune_lock = Lock()


@dataclass(frozen=True)
class RateLimitStatus:
    """Current throttle status for a failed-attempt bucket."""

    limited: bool
    retry_after_seconds: int | None = None


class FailedAttemptRateLimiter:
    """In-memory rate limiter for repeated failed auth attempts."""

    def __init__(
        self,
        *,
        max_attempts: int = 5,
        window: timedelta = timedelta(minutes=15),
        max_buckets: int = 4096,
    ):
        """Initializes the limiter with a fixed maximum count and rolling window."""
        self.max_attempts = max_attempts
        self.window = window
        self.max_buckets = max_buckets
        self._lock = Lock()
        self._failures: dict[str, deque[datetime]] = {}

    def _normalize_identifier(self, identifier: str | None) -> str | None:
        if identifier is None:
            return None
        value = identifier.strip().lower()
        return value or None

    def _bucket_key(self, scope: str, identifier: str | None) -> str | None:
        normalized = self._normalize_identifier(identifier)
        if normalized is None:
            return None
        return f"{scope}:{normalized}"

    def _prune_bucket(self, bucket: deque[datetime], now: datetime) -> None:
        while bucket and now - bucket[0] >= self.window:
            bucket.popleft()

    def _prune_all_buckets(self, now: datetime) -> None:
        empty_keys = []
        for bucket_key, bucket in self._failures.items():
            self._prune_bucket(bucket, now)
            if not bucket:
                empty_keys.append(bucket_key)
        for bucket_key in empty_keys:
            self._failures.pop(bucket_key, None)

    def _evict_oldest_bucket(self) -> None:
        if not self._failures:
            return
        oldest_key = min(
            self._failures.items(),
            key=lambda item: item[1][-1] if item[1] else datetime.min.replace(tzinfo=UTC),
        )[0]
        self._failures.pop(oldest_key, None)

    def check(self, scope: str, identifier: str | None) -> RateLimitStatus:
        """Returns whether the bucket is currently rate-limited."""
        bucket_key = self._bucket_key(scope, identifier)
        if bucket_key is None:
            return RateLimitStatus(limited=False)

        now = datetime.now(UTC)
        with self._lock:
            bucket = self._failures.get(bucket_key)
            if not bucket:
                return RateLimitStatus(limited=False)

            self._prune_bucket(bucket, now)
            if not bucket:
                self._failures.pop(bucket_key, None)
                return RateLimitStatus(limited=False)

            if len(bucket) < self.max_attempts:
                return RateLimitStatus(limited=False)

            retry_after = bucket[0] + self.window - now
            return RateLimitStatus(limited=True, retry_after_seconds=max(1, math.ceil(retry_after.total_seconds())))

    def record_failure(self, scope: str, identifier: str | None) -> None:
        """Adds a failed attempt to the bucket."""
        bucket_key = self._bucket_key(scope, identifier)
        if bucket_key is None:
            return

        now = datetime.now(UTC)
        with self._lock:
            bucket = self._failures.get(bucket_key)
            if bucket is None and len(self._failures) >= self.max_buckets:
                self._prune_all_buckets(now)
            bucket = self._failures.get(bucket_key)
            if bucket is None:
                while len(self._failures) >= self.max_buckets:
                    self._evict_oldest_bucket()
                bucket = deque()
                self._failures[bucket_key] = bucket
            else:
                self._prune_bucket(bucket, now)
            bucket.append(now)

    def reset(self, scope: str, identifier: str | None) -> None:
        """Clears the bucket after a successful auth action."""
        bucket_key = self._bucket_key(scope, identifier)
        if bucket_key is None:
            return
        with self._lock:
            self._failures.pop(bucket_key, None)

    def reset_all(self) -> None:
        """Clears all stored failures."""
        with self._lock:
            self._failures.clear()


auth_failure_rate_limiter = FailedAttemptRateLimiter()


def _clear_setup_token() -> None:
    """Clears any in-memory first-run setup token."""
    global _setup_token, _setup_token_expires_at
    _setup_token = None
    _setup_token_expires_at = None


def reset_runtime_state() -> None:
    """Resets global auth runtime state used by tests and first-run setup."""
    global _last_revoked_session_prune_at
    _clear_setup_token()
    _last_revoked_session_prune_at = None
    auth_failure_rate_limiter.reset_all()


def _auth_log_details(action: str, outcome: str, client_ip: str | None, username: str | None) -> dict[str, str]:
    details = {
        "action": action,
        "outcome": outcome,
        "client_ip": client_ip or "unknown",
    }
    if username:
        details["username"] = username
    return details


def log_auth_failure(action: str, outcome: str, client_ip: str | None, username: str | None) -> None:
    """Writes an audit-friendly warning for a failed auth-related attempt."""
    action_label = action.capitalize()
    normalized_outcome = outcome.rstrip(".")
    event_logger.log(
        models.EventType.WARNING,
        _AUTH_EVENT_SOURCE,
        f"{action_label} attempt failed: {normalized_outcome}.",
        _auth_log_details(action, normalized_outcome, client_ip, username),
    )


def log_auth_throttle(
    action: str,
    client_ip: str | None,
    username: str | None,
    retry_after_seconds: int | None,
) -> None:
    """Writes an audit-friendly warning for a throttled auth-related attempt."""
    action_label = action.capitalize()
    details = _auth_log_details(action, "throttled", client_ip, username)
    if retry_after_seconds is not None:
        details["retry_after_seconds"] = str(retry_after_seconds)
    event_logger.log(
        models.EventType.WARNING,
        _AUTH_EVENT_SOURCE,
        f"{action_label} attempt throttled.",
        details,
    )


def maybe_prune_expired_revoked_sessions(db) -> None:
    """Deletes expired per-session revocation rows at a bounded interval."""
    global _last_revoked_session_prune_at

    now = datetime.now(UTC)
    with _revoked_session_prune_lock:
        if (
            _last_revoked_session_prune_at is not None
            and now - _last_revoked_session_prune_at < _REVOKED_SESSION_PRUNE_INTERVAL
        ):
            return
        _last_revoked_session_prune_at = now

    db.query(models.RevokedSession).filter(
        models.RevokedSession.expires_at.is_not(None), models.RevokedSession.expires_at < now
    ).delete(synchronize_session=False)
    # Commit even when nothing matched: the DELETE alone opens a write transaction that
    # holds SQLite's write lock, so a failed login or setup (which writes nothing else on
    # this session) would make its own audit event, logged on another connection, wait
    # out the busy timeout and then be lost to "database is locked" (JAN-218).
    db.commit()


def _strongest_rate_limit(*statuses: RateLimitStatus) -> RateLimitStatus:
    limited = [status for status in statuses if status.limited]
    if not limited:
        return RateLimitStatus(limited=False)
    retry_after = max(status.retry_after_seconds or 0 for status in limited) or None
    return RateLimitStatus(limited=True, retry_after_seconds=retry_after)


def check_failed_attempt_rate_limit(action: str, client_ip: str | None, username: str | None = None) -> RateLimitStatus:
    """Checks the IP and username failed-attempt buckets for an auth action."""
    return _strongest_rate_limit(
        auth_failure_rate_limiter.check(f"{action}:ip", client_ip),
        auth_failure_rate_limiter.check(f"{action}:username", username),
    )


def record_failed_attempt(action: str, client_ip: str | None, username: str | None, outcome: str) -> None:
    """Records a failed auth-related attempt and writes the audit log entry."""
    auth_failure_rate_limiter.record_failure(f"{action}:ip", client_ip)
    auth_failure_rate_limiter.record_failure(f"{action}:username", username)
    log_auth_failure(action, outcome, client_ip, username)


def reset_failed_attempts(action: str, client_ip: str | None, username: str | None = None) -> None:
    """Clears the IP and username failure buckets after a success."""
    auth_failure_rate_limiter.reset(f"{action}:ip", client_ip)
    auth_failure_rate_limiter.reset(f"{action}:username", username)


def _payload_expiration(payload: dict) -> datetime | None:
    raw_exp = payload.get("exp")
    if raw_exp is None or isinstance(raw_exp, bool):
        return None
    if isinstance(raw_exp, datetime):
        return raw_exp
    if isinstance(raw_exp, (int, float)):
        return datetime.fromtimestamp(raw_exp, tz=UTC)
    if isinstance(raw_exp, str):
        try:
            return datetime.fromtimestamp(float(raw_exp), tz=UTC)
        except ValueError:
            return None
    return None


def get_active_setup_token() -> str | None:
    """Returns the active first-run setup token, if it is still valid."""
    global _setup_token, _setup_token_expires_at
    now = datetime.now(UTC)
    if _setup_token and _setup_token_expires_at and _setup_token_expires_at > now:
        return _setup_token
    _clear_setup_token()
    return None


def ensure_setup_token(db) -> None:
    """Creates and logs a first-run setup token when the system has no users."""
    if not config.settings.public_setup_enabled:
        _clear_setup_token()
        return

    if db.query(models.User).count() > 0:
        _clear_setup_token()
        return

    if get_active_setup_token():
        return

    global _setup_token, _setup_token_expires_at
    _setup_token = secrets.token_urlsafe(32)
    _setup_token_expires_at = datetime.now(UTC) + timedelta(minutes=_SETUP_TOKEN_LIFETIME_MINUTES)
    logger.warning(
        "Janus first-run setup is required.\nUse this one-time token within %s minutes: %s",
        _SETUP_TOKEN_LIFETIME_MINUTES,
        _setup_token,
    )


def needs_initial_setup(db) -> bool:
    """Reports whether the application still needs its initial admin account."""
    if db.query(models.User).count() > 0:
        _clear_setup_token()
        return False

    ensure_setup_token(db)
    return True


def create_initial_admin(db, token: str, username: str, password: str) -> models.User:
    """Consumes the setup token and creates the first admin user."""
    maybe_prune_expired_revoked_sessions(db)
    if not needs_initial_setup(db):
        raise ValueError("Initial setup has already been completed.")

    active_token = get_active_setup_token()
    if not active_token or token != active_token:
        raise ValueError("Setup token is invalid or has expired.")

    existing_user = db.query(models.User).filter(models.User.username == username).first()
    if existing_user:
        raise ValueError("User already exists.")

    hashed_password = security.security_service.get_password_hash(password)
    new_user = models.User(username=username, hashed_password=hashed_password, is_admin=True)
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    _clear_setup_token()
    return new_user


def bootstrap_initial_admin(db, username: str, password: str) -> models.User:
    """Creates the first admin user directly for SSH/CLI bootstrap flows."""
    maybe_prune_expired_revoked_sessions(db)
    if db.query(models.User).count() > 0:
        _clear_setup_token()
        raise ValueError("Initial setup has already been completed.")

    hashed_password = security.security_service.get_password_hash(password)
    new_user = models.User(username=username, hashed_password=hashed_password, is_admin=True)
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    _clear_setup_token()
    return new_user


class AuthService(base.BaseService):
    """Handles user authentication and management."""

    def authenticate_user(self, username: str, password: str) -> models.User | None:
        """Authenticates a user against the database.

        Args:
            username (str): The username.
            password (str): The plain-text password.

        Returns:
            Optional[models.User]: The user object if authenticated, else None.
        """
        maybe_prune_expired_revoked_sessions(self.db)
        user = self.db.query(models.User).filter(models.User.username == username).first()
        if not user:
            # Mitigation against timing attacks
            security.security_service.verify_password(
                password, "$2b$12$EixZaYVK1fsbw1ZfbX3OXePaWxn96p36WQoeG6L6s57Wy60Q2iSMi"
            )
            return None

        if not security.security_service.verify_password(password, user.hashed_password):
            return None

        # Update last login
        user.last_login = datetime.now(UTC)
        self.db.commit()
        return user

    def get_user_by_username(self, username: str) -> models.User | None:
        """Retrieves a user by username.

        Args:
            username (str): The username.

        Returns:
            Optional[models.User]: The user object if found.
        """
        return self.db.query(models.User).filter(models.User.username == username).first()

    def create_user(self, username: str, password: str) -> models.User:
        """Creates a new system user.

        Args:
            username (str): The username.
            password (str): The plain-text password.

        Returns:
            models.User: The created user object.
        """
        hashed_password = security.security_service.get_password_hash(password)
        new_user = models.User(username=username, hashed_password=hashed_password)
        self.db.add(new_user)
        self.db.commit()
        self.db.refresh(new_user)
        return new_user

    def change_password(self, user: models.User, current_password: str, new_password: str) -> None:
        """Updates a user's password and invalidates existing sessions."""
        maybe_prune_expired_revoked_sessions(self.db)
        if not security.security_service.verify_password(current_password, user.hashed_password):
            raise ValueError("Current password is incorrect.")

        user.hashed_password = security.security_service.get_password_hash(new_password)
        user.session_generation += 1
        DeviceTokenService(self.db).revoke_all(user.id)
        self.db.commit()
        event_logger.log(
            models.EventType.INFO,
            _AUTH_EVENT_SOURCE,
            f"Password changed for user {user.username}.",
            {"action": "password_change", "username": user.username},
            user_id=user.id,
        )

    def revoke_user_sessions(self, user: models.User, *, reason: str, actor: str | None = None) -> None:
        """Invalidates all existing sessions for the given user."""
        maybe_prune_expired_revoked_sessions(self.db)
        user.session_generation += 1
        DeviceTokenService(self.db).revoke_all(user.id)
        self.db.commit()
        details = {"action": "revoke_sessions", "reason": reason, "username": user.username}
        details.update({"actor": actor} if actor else {})
        event_logger.log(
            models.EventType.INFO,
            _AUTH_EVENT_SOURCE,
            f"Revoked sessions for user {user.username}.",
            details,
            user_id=user.id,
        )

    def update_ui_preference(self, user: models.User, ui_version: str) -> models.User:
        """Updates a user's UI version preference.

        Args:
            user (models.User): The user object to update.
            ui_version (str): The new UI version preference (e.g., "v1", "v2", "v3").

        Returns:
            models.User: The updated user object.
        """
        user.ui_version = ui_version
        self.db.commit()
        self.db.refresh(user)
        return user

    def revoke_token(self, token: str, *, reason: str, actor: str | None = None) -> None:
        """Invalidates a specific token when possible, falling back to user-wide revocation."""
        maybe_prune_expired_revoked_sessions(self.db)
        payload = security.security_service.decode_token(token)
        if not payload:
            return

        username = payload.get("sub")
        if not isinstance(username, str) or not username:
            return

        user = self.get_user_by_username(username)
        if user is None:
            return

        session_id = payload.get("sid")
        if isinstance(session_id, str) and session_id:
            self.db.add(
                models.RevokedSession(
                    session_id=session_id,
                    user_id=user.id,
                    reason=reason,
                    expires_at=_payload_expiration(payload),
                )
            )
            try:
                self.db.commit()
            except IntegrityError:
                self.db.rollback()
                return

            details = {"action": "revoke_session", "reason": reason, "username": user.username}
            details.update({"actor": actor} if actor else {})
            event_logger.log(
                models.EventType.INFO,
                _AUTH_EVENT_SOURCE,
                f"Revoked session for user {user.username}.",
                details,
                user_id=user.id,
            )
            return

        self.revoke_user_sessions(user, reason=reason, actor=actor)
