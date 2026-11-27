"""Tests for trusted request origin reconstruction helpers."""

from unittest.mock import patch

from janus.config import Settings
from janus.request_origin import (
    TrustedProxyHeadersMiddleware,
    _host_is_allowed,
    _origin_is_allowed,
    _parse_origin,
    _public_endpoint_is_disabled,
    _select_trusted_value,
    _trusted_forwarded_client_ip,
    _trusted_forwarded_origin,
    browser_origins,
)


def test_select_trusted_value_requires_enough_proxy_hops():
    """A shorter-than-expected forwarded chain must not be trusted."""
    assert _select_trusted_value(["https"], trusted_proxy_hops=2) is None


def test_select_trusted_value_preserves_forwarded_entry_type():
    """Trusted forwarded entries should retain their mapping type."""
    forwarded_entry = {"proto": "https", "host": "public.example.com"}

    assert _select_trusted_value([forwarded_entry], trusted_proxy_hops=1) == forwarded_entry
    assert _select_trusted_value([forwarded_entry], trusted_proxy_hops=2) is None


def test_trusted_forwarded_origin_ignores_short_header_chain():
    """A short forwarded chain should not override the public request origin."""
    headers = {
        "x-forwarded-proto": "https",
        "x-forwarded-host": "public.example.com",
    }

    assert _trusted_forwarded_origin(headers, trusted_proxy_hops=2) == (None, None)


def test_trusted_forwarded_client_ip_selects_the_configured_proxy_hop():
    """Trusted client IP extraction should use the configured forwarded hop."""
    headers = {"x-forwarded-for": "198.51.100.11, 203.0.113.8"}

    assert _trusted_forwarded_client_ip(headers, trusted_proxy_hops=1) == "203.0.113.8"
    assert _trusted_forwarded_client_ip(headers, trusted_proxy_hops=2) == "198.51.100.11"


def test_trusted_forwarded_client_ip_ignores_short_header_chain():
    """A shorter-than-expected client IP chain must fail closed."""
    headers = {"x-forwarded-for": "198.51.100.11"}

    assert _trusted_forwarded_client_ip(headers, trusted_proxy_hops=2) is None


def test_host_allowlist_matches_hostname_without_requiring_port():
    """Allowlist entries without a port should accept the matching hostname."""
    assert _host_is_allowed("janus.example.com:8443", ["janus.example.com"]) is True


def test_host_allowlist_rejects_unexpected_host():
    """Unexpected Host headers should be rejected."""
    assert _host_is_allowed("evil.example.com", ["janus.example.com"]) is False


def test_host_allowlist_honours_explicit_port_when_configured():
    """Allowlist entries with a port should only accept that port."""
    assert _host_is_allowed("janus.example.com:8443", ["janus.example.com:8443"]) is True
    assert _host_is_allowed("janus.example.com:8080", ["janus.example.com:8443"]) is False


def test_origin_allowlist_accepts_matching_origin():
    """The configured allowlist should accept the exact public browser origin."""
    assert _origin_is_allowed("https://janus.example.com", ["https://janus.example.com"]) is True


def test_origin_allowlist_rejects_unexpected_origin():
    """Unexpected WebSocket origins should be rejected."""
    assert _origin_is_allowed("https://evil.example.com", ["https://janus.example.com"]) is False


def test_origin_allowlist_rejects_non_canonical_origin_values():
    """WebSocket Origin headers must be bare origins without path, query, fragment, or userinfo."""
    assert _parse_origin("https://janus.example.com/") is None
    assert _parse_origin("https://janus.example.com/path") is None
    assert _parse_origin("https://janus.example.com?x=1") is None
    assert _parse_origin("https://user@janus.example.com") is None


def test_origin_allowlist_rejects_missing_origin():
    """Missing WebSocket origins should be rejected by default."""
    assert _origin_is_allowed(None, ["https://janus.example.com"]) is False


def test_origin_allowlist_ignores_invalid_allowlist_entries():
    """Unexpected monkeypatched allowlist entries must fail closed instead of crashing handshakes."""
    assert _origin_is_allowed("https://janus.example.com", ["https://janus.example.com", "not a valid origin"]) is True
    assert _origin_is_allowed("https://janus.example.com", ["not a valid origin"]) is False


def test_public_docs_matcher_does_not_overmatch_similar_paths():
    """Docs hardening should not block unrelated similarly named paths."""
    settings = Settings(
        _env_file=None,
        JANUS_SECRET_KEY="test-secret-key-for-unit-tests",
        allowed_hosts=["localhost"],
    )

    assert _public_endpoint_is_disabled("/apidocs", settings) is True
    assert _public_endpoint_is_disabled("/apidocs/oauth2-redirect", settings) is True
    assert _public_endpoint_is_disabled("/apidocs_fake", settings) is False


def test_websocket_origin_check_always_allows_the_base_url_origin():
    """A fresh install serves the dashboard from base_url, so its origin passes without being listed."""
    settings = Settings(
        _env_file=None,
        JANUS_SECRET_KEY="test-secret-key-for-unit-tests",
        allowed_origins=["http://localhost:5173"],
        base_url="http://localhost:8080",
    )
    middleware = TrustedProxyHeadersMiddleware(app=None, settings=settings)

    def scope(origin: str) -> dict:
        return {"type": "websocket", "headers": [(b"origin", origin.encode())]}

    assert middleware._origin_allowed(scope("http://localhost:8080")) is True
    assert middleware._origin_allowed(scope("http://localhost:5173")) is True
    assert middleware._origin_allowed(scope("http://localhost:9999")) is False


def test_browser_origins_add_the_base_url_origin(tmp_path, monkeypatch):
    """The base_url origin is always a browser origin, once, without path or default port."""
    for name in ("ALLOWED_ORIGINS", "BASE_URL", "JANUS_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    with patch.object(Settings, "_toml_config_file", tmp_path / "missing.toml"):
        fresh = Settings(_env_file=None, JANUS_SECRET_KEY="test-secret-key-for-unit-tests")
        proxied = Settings(
            _env_file=None,
            JANUS_SECRET_KEY="test-secret-key-for-unit-tests",
            base_url="https://janus.example.com:443/janus/",
            allowed_origins=["https://janus.example.com"],
        )

    assert browser_origins(fresh) == ["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:8080"]
    assert fresh.allowed_origins == ["http://localhost:5173", "http://127.0.0.1:5173"]
    assert browser_origins(proxied) == ["https://janus.example.com"]
