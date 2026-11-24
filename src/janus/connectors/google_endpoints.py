"""The Google endpoints the backend itself calls, and the e2e test seam that replaces them (JAN-221).

With ``JANUS_GOOGLE_API_BASE_URL`` (``config.settings.google_api_base_url``) set, the
OAuth token exchange and refreshes, the userinfo lookup, and the Calendar and Tasks API
clients go to a fake Google at that base URL; the Playwright e2e backend points it at a
local fake. Unset, which is every real install, they are Google's own endpoints. A leaf
module so both the connectors and the OAuth callbacks can use it.
"""

from google.oauth2.credentials import Credentials

from janus import config

GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"


def _fake_google_base_url() -> str | None:
    """Returns the e2e fake Google base URL (``JANUS_GOOGLE_API_BASE_URL``), if set."""
    base_url = (config.settings.google_api_base_url or "").strip().rstrip("/")
    return base_url or None


def token_uri() -> str:
    """Returns Google's OAuth2 token endpoint, or the e2e fake's."""
    base_url = _fake_google_base_url()
    return f"{base_url}/token" if base_url else GOOGLE_TOKEN_URI


def with_fake_token_uri(credentials: Credentials) -> Credentials:
    """Points stored credentials' refreshes at the e2e fake; unchanged without one.

    ``Credentials.from_authorized_user_info`` always resets the token URI to
    Google's, so connectors re-apply the fake's after loading.
    """
    return credentials.with_token_uri(token_uri()) if _fake_google_base_url() else credentials


def userinfo_url() -> str:
    """Returns Google's OpenID userinfo endpoint, or the e2e fake's."""
    base_url = _fake_google_base_url()
    return f"{base_url}/oauth2/v3/userinfo" if base_url else GOOGLE_USERINFO_URL


def api_client_options(service_path: str) -> dict[str, str] | None:
    """Returns ``googleapiclient`` client options that point one API at the e2e fake.

    ``service_path`` is the API's discovery ``servicePath`` (``calendar/v3`` for
    Calendar, empty for Tasks), served below the fake's base URL. ``None`` (no
    override) keeps Google's real endpoint.
    """
    base_url = _fake_google_base_url()
    if not base_url:
        return None
    path = service_path.strip("/")
    return {"api_endpoint": f"{base_url}/{path}/" if path else f"{base_url}/"}
