"""Delegated Microsoft sign-in for Graph (Outlook mail/calendar, Teams chats).

Janus acts *as the user* through its own Entra ID app registration
(``microsoft_client_id`` / ``microsoft_tenant_id``). It deliberately does not use
``DefaultAzureCredential`` for this: that chain would pick up an ``az login`` token
first, and an Azure CLI token carries only the permissions Microsoft pre-consented
for the CLI's own app, which exclude the Teams and mail scopes. Using the app
registration is what makes the consented ``Chat.*``/``Mail.*`` scopes available.

Sign-in is interactive and local (a browser opens once, via ``janus microsoft login``).
Afterwards every call is silent: MSAL renews the access token from the cached refresh
token. That cache is encrypted at rest with Janus's Fernet key (the same protection as
the GitHub and Linear tokens), so it needs no OS keyring or native library and no
plaintext refresh token is ever written. This is a single instance-wide Microsoft
identity, like those tokens, not a per-Janus-user connection.
"""

from __future__ import annotations

import base64
import json
import os
import threading
from pathlib import Path
from typing import Any

import msal

from janus import config, security
from janus.connectors.microsoft_graph import GRAPH_SCOPE_PREFIX, MicrosoftGraphClient

#: ``.default`` asks for every delegated permission already consented for the app, so a
#: missing scope surfaces as an actionable Graph 403 instead of a sign-in failure.
GRAPH_DEFAULT_SCOPE = f"{GRAPH_SCOPE_PREFIX}.default"
LOGIN_TIMEOUT_SECONDS = 300

_lock = threading.RLock()
_app: msal.PublicClientApplication | None = None
_cache: msal.SerializableTokenCache | None = None
_loaded_mtime: int | None = None


class MicrosoftAuthError(RuntimeError):
    """Microsoft access is not configured, not signed in, or the sign-in failed."""


def token_cache_path() -> Path:
    """Where the Fernet-encrypted MSAL token cache (and so the sign-in) lives."""
    return config.get_data_dir() / "microsoft_token_cache.enc"


def is_configured(settings: config.Settings | None = None) -> bool:
    """Return whether the app registration ids are set."""
    active = settings or config.settings
    return bool(active.microsoft_client_id.strip() and active.microsoft_tenant_id.strip())


def is_connected(settings: config.Settings | None = None) -> bool:
    """Return whether Janus is configured and a Microsoft account has signed in on this machine."""
    return is_configured(settings) and token_cache_path().is_file()


def _cache_mtime() -> int | None:
    try:
        return token_cache_path().stat().st_mtime_ns
    except OSError:
        return None


def _read_cache() -> msal.SerializableTokenCache:
    cache = msal.SerializableTokenCache()
    path = token_cache_path()
    if path.is_file():
        try:
            cache.deserialize(security.security_service.decrypt(path.read_text(encoding="utf-8")))
        except (OSError, ValueError) as exc:  # unreadable file or wrong Fernet key
            raise MicrosoftAuthError(
                "The stored Microsoft sign-in cannot be read (changed Fernet key?). Run `janus microsoft login`."
            ) from exc
    return cache


def _write_cache(cache: msal.SerializableTokenCache) -> None:
    """Persist the cache, encrypted, only when MSAL changed it."""
    if not cache.has_state_changed:
        return
    global _loaded_mtime
    path = token_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic and never world-readable: a crash mid-write must not destroy the only refresh token.
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(security.security_service.encrypt(cache.serialize()))
    tmp.replace(path)
    _loaded_mtime = _cache_mtime()
    cache.has_state_changed = False


def _application(
    settings: config.Settings, *, fresh: bool = False
) -> tuple[msal.PublicClientApplication, msal.SerializableTokenCache]:
    """Return the process-wide MSAL app and its cache (created lazily, so background threads share one).

    ``fresh`` starts from an empty cache without reading the stored one (used by login, which replaces it).
    """
    global _app, _cache, _loaded_mtime
    with _lock:
        if _app is not None and _cache is not None and _cache_mtime() != _loaded_mtime:
            # Another process (``janus microsoft login``) replaced the sign-in; do not overwrite it.
            _app = _cache = None
        if _app is None or _cache is None:
            _loaded_mtime = _cache_mtime()
            _cache = msal.SerializableTokenCache() if fresh else _read_cache()
            _app = msal.PublicClientApplication(
                settings.microsoft_client_id.strip(),
                authority=f"https://login.microsoftonline.com/{settings.microsoft_tenant_id.strip()}",
                token_cache=_cache,
            )
        return _app, _cache


def reset_credential() -> None:
    """Drop the in-memory app and cache (after login/logout, and between tests)."""
    global _app, _cache
    with _lock:
        _app = None
        _cache = None


def login(settings: config.Settings | None = None) -> str:
    """Open the browser, sign in, remember the account, and return its user name. CLI only."""
    active = settings or config.settings
    if not is_configured(active):
        raise MicrosoftAuthError(
            "Set JANUS_MICROSOFT_CLIENT_ID and JANUS_MICROSOFT_TENANT_ID (or the same keys in settings.toml) first."
        )
    with _lock:
        reset_credential()
        # A new sign-in replaces the old one: an unreadable cache must not block it, and a different
        # account must not end up behind the previous one (``get_graph_token`` uses the first account).
        application, cache = _application(active, fresh=True)
        cache.has_state_changed = True
        result = application.acquire_token_interactive([GRAPH_DEFAULT_SCOPE], timeout=LOGIN_TIMEOUT_SECONDS)
        if "access_token" not in result:
            raise MicrosoftAuthError(f"Microsoft sign-in failed: {_error_text(result)}")
        _write_cache(cache)
        claims = result.get("id_token_claims") or {}
        return str(claims.get("preferred_username") or claims.get("upn") or "unknown account")


def logout() -> bool:
    """Forget the signed-in account by deleting the encrypted cache. Returns whether one existed."""
    reset_credential()
    path = token_cache_path()
    if not path.is_file():
        return False
    path.unlink()
    return True


def get_graph_token(settings: config.Settings | None = None) -> str:
    """Return a current Graph access token, renewed silently from the cached refresh token. Never prompts."""
    active = settings or config.settings
    if not is_configured(active):
        raise MicrosoftAuthError("Microsoft access is not configured. Set the Microsoft client and tenant ids.")
    if not token_cache_path().is_file():
        raise MicrosoftAuthError("Not signed in to Microsoft. Run `janus microsoft login`.")
    with _lock:
        application, cache = _application(active)
        accounts = application.get_accounts()
        if not accounts:
            raise MicrosoftAuthError("Not signed in to Microsoft. Run `janus microsoft login`.")
        result = application.acquire_token_silent([GRAPH_DEFAULT_SCOPE], account=accounts[0])
        _write_cache(cache)
    if not result or "access_token" not in result:
        detail = _error_text(result) if result else "no cached token"
        raise MicrosoftAuthError(f"Microsoft sign-in expired ({detail}). Run `janus microsoft login` again.")
    return str(result["access_token"])


def build_graph_client(settings: config.Settings | None = None) -> MicrosoftGraphClient:
    """Return a Graph client that asks this module for a fresh token on every request."""
    active = settings or config.settings
    return MicrosoftGraphClient(lambda: get_graph_token(active))


def _error_text(result: dict[str, Any]) -> str:
    """One line from an MSAL error result, without echoing the whole payload."""
    return str(result.get("error_description") or result.get("error") or "unknown error").splitlines()[0][:300]


def decode_token_claims(token: str) -> dict[str, Any]:
    """Return the (unverified) claims of a JWT, for display only (never for authorization)."""
    try:
        payload = token.split(".")[1]
        padded = payload + "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(padded))
    except (IndexError, ValueError):
        return {}
    return claims if isinstance(claims, dict) else {}


def connection_status(settings: config.Settings | None = None) -> dict[str, Any]:
    """Summarize the connection for ``janus microsoft status``: account and granted scopes."""
    active = settings or config.settings
    status: dict[str, Any] = {
        "configured": is_configured(active),
        "connected": is_connected(active),
        "account": None,
        "scopes": [],
        "error": None,
    }
    if not status["connected"]:
        return status
    try:
        token = get_graph_token(active)
        accounts = _application(active)[0].get_accounts()
        status["account"] = accounts[0].get("username") if accounts else None
        status["scopes"] = sorted(str(decode_token_claims(token).get("scp", "")).split())
    except MicrosoftAuthError as exc:
        status["error"] = str(exc)
    return status
