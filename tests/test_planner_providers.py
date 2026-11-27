"""Planner providers: only those with a connector are offered, and the capability profile lists the real ones."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from janus import cli, models, security
from janus.capability_profile import CALENDAR_PROVIDERS, TASK_PROVIDERS, user_connected_capabilities
from janus.planner_service import PlannerService


def _login(client, db) -> models.User:
    user = models.User(username="owner", hashed_password=security.security_service.get_password_hash("pw"))
    db.add(user)
    db.commit()
    response = client.post("/api/auth/login", json={"username": "owner", "password": "pw"})
    client.headers[security.CSRF_HEADER_NAME] = response.json()["csrf_token"]
    return user


def _account(db, user, provider: models.Provider) -> models.PlannerAccount:
    account = models.PlannerAccount(name=provider.value, provider=provider, token="t", user_id=user.id)
    db.add(account)
    db.commit()
    return account


def test_every_offered_provider_has_a_connector(db):
    """The offered set is exactly the providers ``PlannerService.get_connector`` can build."""
    user = models.User(username="owner", hashed_password="x")
    db.add(user)
    db.commit()
    service = PlannerService(db)
    with_connector = set()
    for provider in models.Provider:
        try:
            connector = service.get_connector(_account(db, user, provider))
        except RuntimeError:  # Microsoft calendar: has a connector, but needs the account owner's sign-in.
            connector = object()
        if connector is not None:
            with_connector.add(provider)

    assert with_connector == models.CONNECTED_PLANNER_PROVIDERS
    assert {models.Provider.O365, models.Provider.ADO}.isdisjoint(models.CONNECTED_PLANNER_PROVIDERS)


def test_capability_profile_lists_only_providers_with_a_connector():
    """Outlook calendars back ``calendar``; the connector-less o365 and ado back nothing."""
    assert models.Provider.MICROSOFT_CALENDAR in CALENDAR_PROVIDERS
    assert CALENDAR_PROVIDERS | TASK_PROVIDERS <= models.CONNECTED_PLANNER_PROVIDERS


def test_an_outlook_calendar_account_grants_the_calendar_capability(db):
    """A synced Microsoft calendar account makes the calendar tools available."""
    user = models.User(username="owner", hashed_password="x")
    db.add(user)
    db.commit()
    _account(db, user, models.Provider.O365)
    _account(db, user, models.Provider.ADO)
    assert not {"calendar", "jira"} & user_connected_capabilities(db, user)

    _account(db, user, models.Provider.MICROSOFT_CALENDAR)
    assert "calendar" in user_connected_capabilities(db, user)


@pytest.mark.parametrize("provider", ["o365", "ado"])
def test_the_cli_refuses_a_provider_without_a_connector(monkeypatch, provider):
    """``janus planner add`` fails before asking for a token and sends nothing."""
    sent = []
    monkeypatch.setattr(cli.client, "add_planner", sent.append)

    result = CliRunner().invoke(cli.app, ["planner", "add", "Work", provider])

    assert result.exit_code == 2
    assert "Invalid value" in result.output and "microsoft_calendar" in result.output
    assert "Token" not in result.output
    assert sent == []


def test_the_cli_adds_a_provider_with_a_connector(monkeypatch):
    """A supported provider is sent to the API as before."""
    sent = []
    monkeypatch.setattr(cli.client, "add_planner", sent.append)

    result = CliRunner().invoke(cli.app, ["planner", "add", "Work", "jira", "--token", "secret"])

    assert result.exit_code == 0, result.output
    assert sent == [{"name": "Work", "provider": "jira", "token": "secret", "api_url": None}]


@pytest.mark.parametrize("provider", ["o365", "ado"])
def test_the_api_refuses_a_provider_without_a_connector(client, db, provider):
    """The API that backs the CLI rejects the connector-less providers too, and stores nothing."""
    _login(client, db)

    response = client.post("/api/planner/accounts", json={"name": "Work", "provider": provider, "token": "t"})

    assert response.status_code == 422
    assert db.query(models.PlannerAccount).count() == 0


def test_stored_connector_less_accounts_still_load(client, db):
    """Existing o365/ado rows stay readable: the enum keeps their values."""
    user = _login(client, db)
    _account(db, user, models.Provider.ADO)

    response = client.get("/api/planner/accounts")

    assert response.status_code == 200
    assert [account["provider"] for account in response.json()] == ["ado"]
