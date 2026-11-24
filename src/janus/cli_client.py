"""HTTP client for the Janus CLI to communicate with the backend API."""

import json
import logging
import os
import tempfile
from pathlib import Path

import requests

from janus.config import get_config_dir

logger = logging.getLogger(__name__)

# Keep these module-level names so extensions that previously patched TOKEN_FILE
# continue to work. New installs always write the cache under the XDG config
# directory; the legacy location is read only long enough to migrate it.
TOKEN_FILE = get_config_dir() / "token.json"
LEGACY_TOKEN_FILE = Path.home() / ".janus_token"


def _read_token_file(path: Path) -> dict[str, str] | None:
    """Returns cached token data when *path* contains a JSON object."""
    try:
        with path.open("r", encoding="utf-8") as token_file:
            data = json.load(token_file)
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _access_token(token_data: dict[str, str] | None) -> str | None:
    """Return a non-empty access token from cached JSON data."""
    if token_data is None:
        return None
    token = token_data.get("access_token")
    return token if isinstance(token, str) and token else None


def _write_token_file(token_data: dict[str, str]) -> None:
    """Atomically persist token data with owner-only file permissions."""
    token_path = Path(TOKEN_FILE)
    token_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=token_path.parent, prefix=f".{token_path.name}-", delete=False
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            json.dump(token_data, temporary_file)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())

        os.chmod(temporary_path, 0o600)
        os.replace(temporary_path, token_path)
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                logger.warning("Unable to remove temporary Janus token cache file: %s", temporary_path)


def _remove_token_file(path: Path) -> None:
    """Remove a token cache file when it exists, without masking HTTP errors."""
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.warning("Unable to remove Janus token cache file: %s", path)


#: Login is a quick round trip; without a timeout an unreachable server hangs the CLI.
LOGIN_TIMEOUT_SECONDS = 30


class JanusClient:
    """Client for interacting with the Janus REST API."""

    def __init__(self, base_url: str = "http://localhost:8080"):
        """Initializes the client.

        Args:
            base_url (str): The base URL of the Janus API.
        """
        self.base_url = base_url.rstrip("/")
        self.token = self._load_token()

    def _load_token(self) -> str | None:
        """Loads the JWT token, migrating the legacy home-directory cache."""
        token_path = Path(TOKEN_FILE)
        data = _read_token_file(token_path)
        token = _access_token(data)
        if token is not None:
            _remove_token_file(Path(LEGACY_TOKEN_FILE))
            return token

        legacy_path = Path(LEGACY_TOKEN_FILE)
        data = _read_token_file(legacy_path)
        token = _access_token(data)
        if token is not None:
            try:
                assert data is not None
                _write_token_file(data)
                _remove_token_file(legacy_path)
            except OSError:
                logger.warning("Unable to migrate Janus token cache from %s to %s", legacy_path, token_path)
            return token
        return None

    def save_token(self, token_data: dict[str, str]):
        """Saves the JWT token to the local token file."""
        _write_token_file(token_data)
        _remove_token_file(Path(LEGACY_TOKEN_FILE))
        self.token = token_data.get("access_token")

    def _get_headers(self) -> dict[str, str]:
        """Returns headers with the authorization token if available."""
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def request(self, method: str, path: str, **kwargs) -> requests.Response:
        """Performs an HTTP request to the API."""
        url = f"{self.base_url}/{path.lstrip('/')}"
        headers = self._get_headers()

        # Merge provided headers if any
        if "headers" in kwargs:
            headers.update(kwargs.pop("headers"))

        response = requests.request(method, url, headers=headers, **kwargs)

        if response.status_code == 401:
            # Token is invalid or expired, clear it
            _remove_token_file(Path(TOKEN_FILE))
            _remove_token_file(Path(LEGACY_TOKEN_FILE))
            self.token = None

        return response

    def login(self, username: str, password: str) -> bool:
        """Authenticates with the API and saves the token."""
        response = requests.post(
            f"{self.base_url}/api/auth/login",
            json={"username": username, "password": password},
            timeout=LOGIN_TIMEOUT_SECONDS,
        )
        if response.status_code == 200:
            self.save_token(response.json())
            return True
        return False

    def get_me(self) -> dict | None:
        """Gets current user info."""
        response = self.request("GET", "/api/auth/me")
        return response.json() if response.status_code == 200 else None

    # Account methods
    def list_accounts(self) -> list[dict]:
        """Lists all configured email accounts."""
        response = self.request("GET", "/api/emails/accounts")
        response.raise_for_status()
        return response.json()

    def add_account(self, data: dict) -> dict:
        """Adds a new email account."""
        response = self.request("POST", "/api/emails/accounts", json=data)
        response.raise_for_status()
        return response.json()

    # Rule methods
    def list_rules(self) -> list[dict]:
        """Lists all active prioritization rules."""
        response = self.request("GET", "/api/emails/rules")
        response.raise_for_status()
        return response.json()

    def add_rule(self, data: dict) -> dict:
        """Adds a new prioritization rule."""
        response = self.request("POST", "/api/emails/rules", json=data)
        response.raise_for_status()
        return response.json()

    # Planner methods
    def list_planners(self) -> list[dict]:
        """Lists all active planner integrations."""
        response = self.request("GET", "/api/planner/accounts")
        response.raise_for_status()
        return response.json()

    def add_planner(self, data: dict) -> dict:
        """Adds a new planner integration."""
        response = self.request("POST", "/api/planner/accounts", json=data)
        response.raise_for_status()
        return response.json()

    def get_desktop_summary(self, include_unread_emails: bool = True) -> dict[str, int | None]:
        """Fetch the authenticated aggregate used by desktop widgets."""
        path = "/api/desktop/summary"
        if not include_unread_emails:
            path += "?include_unread_emails=false"
        response = self.request("GET", path)
        response.raise_for_status()
        return response.json()
