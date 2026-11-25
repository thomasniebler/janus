"""Security service for encrypting and decrypting sensitive data."""

import logging
import os
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import bcrypt
import jwt
from cryptography.fernet import Fernet
from fastapi import Cookie, Depends, Header, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jwt import PyJWTError
from sqlalchemy.orm import Session
from starlette.requests import HTTPConnection

from janus import database, models, runtime
from janus.config import settings

logger = logging.getLogger(__name__)

# bcrypt only consumes the first 72 bytes of its input. bcrypt>=5 raises instead of
# silently truncating, so new passwords above this limit are rejected outright (see
# ``validate_password_length``) while verification truncates to stay compatible with
# hashes created by passlib + bcrypt<4, which truncated silently.
BCRYPT_MAX_PASSWORD_BYTES = 72
_BCRYPT_ROUNDS = 12

# We keep this only for OpenAPI documentation purposes
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/auth/login", auto_error=False)
CSRF_HEADER_NAME = "X-CSRF-Token"
SAFE_HTTP_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}
CSRF_EXEMPT_PATHS = {
    "/api/auth/login",
    "/api/auth/logout",
    "/api/auth/setup",
    "/api/auth/google",
}
#: Path prefixes authenticated by something other than the session cookie: inbound webhook deliveries
#: carry an HMAC signature (JAN-406), so a stray Janus cookie on the sender must not turn them into 403s.
CSRF_EXEMPT_PREFIXES = ("/api/webhooks/in/",)


@dataclass
class AuthToken:
    """Normalized authentication token plus the transport it arrived through."""

    token: str
    source: str


class SecurityService:
    """Service for handling data encryption, password hashing, and JWT tokens."""

    def __init__(self):
        """Initializes the SecurityService and loads the encryption key."""
        self._key = self._load_or_create_key()
        self._fernet = Fernet(self._key)

    def _load_or_create_key(self) -> bytes:
        """Loads the encryption key from a file or creates a new one.

        Returns:
            bytes: The encryption key.
        """
        runtime.prepare_runtime_paths()
        key_path = settings.fernet_key_path
        if key_path.exists():
            with open(key_path, "rb") as f:
                return f.read()

        # Create a new key
        logger.info("Generating new encryption key in %s", key_path)
        key = Fernet.generate_key()

        # Create file with restricted permissions (0o600)
        fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(key)

        return key

    def encrypt(self, data: str) -> str:
        """Encrypts a string of data.

        Args:
            data (str): The plain text string to encrypt.

        Returns:
            str: The encrypted data as a string.
        """
        if not data:
            return ""
        return self._fernet.encrypt(data.encode()).decode()

    def decrypt(self, encrypted_data: str) -> str:
        """Decrypts an encrypted string of data.

        Args:
            encrypted_data (str): The encrypted data as a string.

        Returns:
            str: The decrypted plain text string.

        Raises:
            ValueError: If decryption fails.
        """
        if not encrypted_data:
            return ""
        try:
            return self._fernet.decrypt(encrypted_data.encode()).decode()
        except Exception as e:
            logger.error(f"Failed to decrypt data: {e}")
            raise ValueError(f"Decryption failed. Ensure the encryption key is correct: {e}")  # noqa: B904

    # Password Hashing
    def verify_password(self, plain_password: str, hashed_password: str) -> bool:
        """Verifies a plain password against a bcrypt hash.

        Input beyond ``BCRYPT_MAX_PASSWORD_BYTES`` is truncated before checking, matching how
        legacy hashes (passlib + bcrypt<4) were created, so existing long passwords keep working.

        Args:
            plain_password (str): The plain text password.
            hashed_password (str): The hashed password to verify against.

        Returns:
            bool: True if password is valid, False otherwise (including for a malformed hash).
        """
        password_bytes = plain_password.encode("utf-8")[:BCRYPT_MAX_PASSWORD_BYTES]
        try:
            return bcrypt.checkpw(password_bytes, hashed_password.encode("utf-8"))
        except ValueError:
            logger.warning("Stored password hash is not a valid bcrypt hash; rejecting login.")
            return False

    def get_password_hash(self, password: str) -> str:
        """Generates a bcrypt hash of a password.

        Args:
            password (str): The plain text password.

        Returns:
            str: The hashed password (``$2b$`` format).

        Raises:
            ValueError: If the password exceeds ``BCRYPT_MAX_PASSWORD_BYTES`` UTF-8 bytes.
        """
        validate_password_length(password)
        return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)).decode("utf-8")

    # JWT Tokens
    def create_access_token(self, data: dict, expires_delta: timedelta | None = None) -> str:
        """Creates a JWT access token.

        Args:
            data (dict): The data to encode in the token (e.g., subject).
            expires_delta (Optional[timedelta]): An optional timedelta for token expiration.
                                                 If not provided, defaults to `settings.access_token_expire_minutes`.

        Returns:
            str: The encoded JWT access token.
        """
        to_encode = data.copy()
        if expires_delta:
            expire = datetime.now(UTC) + expires_delta
        else:
            expire = datetime.now(UTC) + timedelta(minutes=settings.access_token_expire_minutes)
        to_encode.update({"exp": expire})
        encoded_jwt = jwt.encode(to_encode, settings.janus_secret_key, algorithm=settings.algorithm)
        return encoded_jwt

    def decode_token(self, token: str) -> dict | None:
        """Decodes and validates a JWT token.

        Args:
            token (str): The JWT token to decode.

        Returns:
            Optional[dict]: The decoded payload, or None if invalid.
        """
        try:
            payload = jwt.decode(token, settings.janus_secret_key, algorithms=[settings.algorithm])
            return payload
        except PyJWTError:
            return None


def validate_password_length(password: str) -> str:
    """Rejects passwords bcrypt cannot hash without truncation.

    Args:
        password (str): The plain text password.

    Returns:
        str: The unchanged password, so this can back a pydantic validator.

    Raises:
        ValueError: If the password exceeds ``BCRYPT_MAX_PASSWORD_BYTES`` UTF-8 bytes.
    """
    if len(password.encode("utf-8")) > BCRYPT_MAX_PASSWORD_BYTES:
        raise ValueError(f"Password must be at most {BCRYPT_MAX_PASSWORD_BYTES} bytes long.")
    return password


# Module-level instance to avoid redundant initialization and file I/O
security_service = SecurityService()


def _normalize_session_generation(value: object) -> int | None:
    """Parses a session-generation claim into an integer when possible."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def create_session_id() -> str:
    """Returns a random opaque identifier for a JWT-backed session."""
    return secrets.token_urlsafe(32)


async def get_token(
    authorization: str | None = Header(None),
    access_token: str | None = Cookie(None),
) -> AuthToken | None:
    """Extracts the JWT token from headers or cookies.

    Compatible with both HTTP and WebSockets.
    """
    # 1. Try Cookie (Dashboard)
    if access_token:
        return AuthToken(token=access_token, source="cookie")

    # 2. Try Authorization Header (CLI/API)
    if authorization and authorization.startswith("Bearer "):
        return AuthToken(token=authorization.replace("Bearer ", ""), source="header")

    return None


async def get_user_or_none(
    db: Session = Depends(database.get_db),
    token: AuthToken | None = Depends(get_token),
) -> models.User | None:
    """Validates a JWT and returns the user or None (does NOT raise)."""
    if not token:
        return None

    payload = security_service.decode_token(token.token)
    if not payload:
        return None

    username: str = payload.get("sub")
    if username is None:
        return None

    user = db.query(models.User).filter(models.User.username == username).first()
    if user is None:
        return None

    token_generation = _normalize_session_generation(payload.get("sg"))
    if token_generation is None:
        if user.session_generation != 0:
            return None
    elif token_generation != user.session_generation:
        return None

    session_id = payload.get("sid")
    if isinstance(session_id, str) and session_id:
        revoked_session = db.query(models.RevokedSession).filter(models.RevokedSession.session_id == session_id).first()
        if revoked_session is not None:
            return None

    return user


async def get_websocket_user_or_none(
    token: AuthToken | None = Depends(get_token),
) -> models.User | None:
    """Authenticate a WebSocket handshake with a short-lived DB session (JAN-336).

    ``get_user_or_none`` takes its session from ``Depends(database.get_db)``, and FastAPI
    only tears a dependency down when the endpoint returns, which for a socket is when it
    closes. A ``/ws/chat`` socket authenticated that way kept a second pooled connection
    checked out for the whole tab session, next to the one its handler opens, so a few
    open tabs exhausted the connection pool. This dependency closes its session before
    the handler runs and returns the user detached (column attributes stay loaded);
    a handler that needs the user in a session re-loads it into its own.
    """
    db = database.SessionLocal()
    try:
        user = await get_user_or_none(db=db, token=token)
        if user is not None:
            db.expunge(user)
        return user
    finally:
        db.close()


async def get_current_user(
    user: models.User | None = Depends(get_user_or_none),
) -> models.User:
    """Dependency that ensures a user is authenticated."""
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return user


async def admin_required(
    current_user: models.User = Depends(get_current_user),
) -> models.User:
    """Dependency that ensures the authenticated user is an admin."""
    if not current_user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return current_user


def create_csrf_token() -> str:
    """Returns a random CSRF token suitable for a signed session claim."""
    return secrets.token_urlsafe(32)


async def enforce_csrf_for_cookie_auth(
    connection: HTTPConnection,
    access_token: str | None = Cookie(None),
    csrf_token: str | None = Header(None, alias=CSRF_HEADER_NAME),
) -> None:
    """Requires a valid CSRF header for mutating requests that use cookie auth."""
    if connection.scope["type"] != "http":
        return
    if connection.method.upper() in SAFE_HTTP_METHODS:
        return
    if connection.url.path in CSRF_EXEMPT_PATHS or connection.url.path.startswith(CSRF_EXEMPT_PREFIXES):
        return
    if not access_token:
        return

    payload = security_service.decode_token(access_token)
    if not payload:
        return

    expected_token = payload.get("csrf")
    if not isinstance(expected_token, str) or not expected_token:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF protection is required.")
    if not csrf_token or not secrets.compare_digest(csrf_token, expected_token):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid CSRF token.")
