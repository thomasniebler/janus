"""`janus microsoft login|status|logout`: output and exit codes (auth service faked)."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from janus import cli
from janus.services import microsoft_auth_service as svc

runner = CliRunner()


def _invoke(*args):
    return runner.invoke(cli.app, ["microsoft", *args])


def test_login_reports_the_signed_in_account(monkeypatch):
    """Login reports the signed in account."""
    monkeypatch.setattr(svc, "login", lambda: "me@example.com")

    result = _invoke("login")

    assert result.exit_code == 0
    assert "Signed in as me@example.com" in result.output


def test_login_failure_exits_nonzero_with_the_reason(monkeypatch):
    """Login failure exits nonzero with the reason."""

    def fail():
        raise svc.MicrosoftAuthError("Microsoft sign-in failed: AADSTS65001")

    monkeypatch.setattr(svc, "login", fail)

    result = _invoke("login")

    assert result.exit_code == 1
    assert "AADSTS65001" in result.output


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ({"configured": False, "connected": False}, "Not configured"),
        ({"configured": True, "connected": False}, "janus microsoft login"),
        ({"configured": True, "connected": True, "error": "Microsoft sign-in expired."}, "expired"),
    ],
)
def test_status_exits_nonzero_unless_fully_connected(monkeypatch, status, expected):
    """Status exits nonzero unless fully connected."""
    monkeypatch.setattr(svc, "connection_status", lambda: {"account": None, "scopes": [], "error": None, **status})

    result = _invoke("status")

    assert result.exit_code == 1
    assert expected in result.output


def test_status_lists_account_and_permissions(monkeypatch):
    """Status lists account and permissions."""
    monkeypatch.setattr(
        svc,
        "connection_status",
        lambda: {
            "configured": True,
            "connected": True,
            "account": "me@example.com",
            "scopes": ["Chat.ReadWrite", "Mail.Read"],
            "error": None,
        },
    )

    result = _invoke("status")

    assert result.exit_code == 0
    assert "Signed in as me@example.com" in result.output
    assert "Chat.ReadWrite, Mail.Read" in result.output


@pytest.mark.parametrize(("existed", "expected"), [(True, "Signed out"), (False, "Not signed in")])
def test_logout(monkeypatch, existed, expected):
    """Logout."""
    monkeypatch.setattr(svc, "logout", lambda: existed)

    result = _invoke("logout")

    assert result.exit_code == 0
    assert expected in result.output
