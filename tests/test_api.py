"""Comprehensive tests for Janus API handlers."""

import json
import os
from datetime import UTC, datetime, timedelta

import pytest
from asgi_client import SyncASGIClient
from fastapi.routing import APIRoute, APIWebSocketRoute

from janus import config, database, llm_service, models, security, status_service, time_utils
from janus.api import dashboard as dashboard_api
from janus.api import github as github_api
from janus.api import linear as linear_api
from janus.api import memory as memory_api
from janus.app_factory import create_fastapi_app
from janus.services import auth_service
from janus.whatsapp_neonize import NeonizePairingCapability

# db and client fixtures are provided by conftest.py


@pytest.fixture
def auth_client(client, db):
    """Returns a client with an authenticated session."""
    hashed_pw = security.security_service.get_password_hash("admin")
    user = models.User(username="admin", hashed_password=hashed_pw, is_admin=True)
    db.add(user)
    db.commit()

    response = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    client.headers[security.CSRF_HEADER_NAME] = response.json()["csrf_token"]
    return client


# --- Auth Tests ---


def test_user_management(auth_client, db):
    """Test user creation and listing through the API."""
    # 1. List users
    response = auth_client.get("/api/auth/users")
    assert response.status_code == 200
    assert len(response.json()) == 1

    # 2. Create new user
    response = auth_client.post("/api/auth/users", json={"username": "newuser", "password": "newpass"})
    assert response.status_code == 200
    assert response.json()["username"] == "newuser"

    # 3. List again
    response = auth_client.get("/api/auth/users")
    assert len(response.json()) == 2


def test_cookie_authenticated_mutation_requires_csrf(client, db):
    """Mutating cookie-authenticated routes must reject requests without a CSRF signal."""
    hashed_pw = security.security_service.get_password_hash("admin")
    db.add(models.User(username="admin", hashed_password=hashed_pw, is_admin=True))
    db.commit()

    response = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    assert response.status_code == 200

    create_response = client.post("/api/auth/users", json={"username": "newuser", "password": "newpass"})

    assert create_response.status_code == 403
    assert create_response.json()["detail"] == "Invalid CSRF token."


def test_header_authenticated_mutation_does_not_require_csrf(client, db):
    """Header-authenticated API clients remain exempt from CSRF checks."""
    hashed_pw = security.security_service.get_password_hash("admin")
    db.add(models.User(username="admin", hashed_password=hashed_pw, is_admin=True))
    db.commit()

    token = security.security_service.create_access_token(data={"sub": "admin"})
    response = client.post(
        "/api/auth/users",
        json={"username": "cliuser", "password": "newpass"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json()["username"] == "cliuser"


def test_initial_setup_flow(client, db, monkeypatch):
    """Test creating the initial admin account with a one-time setup token."""
    monkeypatch.setattr(config.settings, "public_setup_enabled", True)
    response = client.get("/api/auth/setup")
    assert response.status_code == 200
    assert response.json() == {"needs_setup": True}

    token = auth_service.get_active_setup_token()
    assert token is not None

    response = client.post(
        "/api/auth/setup",
        json={"token": token, "username": "owner", "password": "secret-pass"},
    )
    assert response.status_code == 200
    assert response.json()["username"] == "owner"
    assert response.json()["is_admin"] is True

    response = client.get("/api/auth/setup")
    assert response.status_code == 200
    assert response.json() == {"needs_setup": False}


def test_public_setup_endpoints_can_be_disabled(client, db, monkeypatch):
    """Public setup routes should be unavailable when CLI-only bootstrap is configured."""
    monkeypatch.setattr(config.settings, "public_setup_enabled", False)

    auth_service.ensure_setup_token(db)
    assert auth_service.get_active_setup_token() is None

    response = client.get("/api/auth/setup")
    assert response.status_code == 404

    response = client.post(
        "/api/auth/setup",
        json={"token": "unused", "username": "owner", "password": "secret-pass"},
    )
    assert response.status_code == 404


def test_docs_api_requires_login(client):
    """The in-app docs (incl. tech specs and reviews/) are only served to a logged-in session (JAN-299)."""
    assert client.get("/api/docs/nav").status_code == 401
    assert client.get("/api/docs/index.md").status_code == 401
    assert client.get("/api/docs/tools.md").status_code == 401


def test_docs_navigation_endpoint(auth_client):
    """Test that docs navigation can be loaded from zensical config."""
    response = auth_client.get("/api/docs/nav")
    assert response.status_code == 200
    assert response.headers["content-security-policy"]
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    nav = response.json()
    assert isinstance(nav, list)
    assert any(item["name"] == "Getting Started" for item in nav)
    technical_spec = next(item for item in nav if item["name"] == "Technical Spec")
    assert "children" in technical_spec
    assert {"name": "Overview", "path": "tech-spec.md"} in technical_spec["children"]


def test_docs_page_endpoint_serves_markdown(auth_client):
    """Test docs markdown retrieval."""
    response = auth_client.get("/api/docs/index.md")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "# Getting Started with Janus" in response.text


def test_docs_page_endpoint_can_serve_root_tech_spec(auth_client):
    """Allowlisted root documentation files should be served through the docs API."""
    response = auth_client.get("/api/docs/tech-spec.md")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "# Janus Technical Specification" in response.text


def test_docs_page_endpoint_serves_the_changelog_through_its_root_alias(auth_client):
    """The changelog is a root file that docs/docs/changelog.md links; the alias lets the docs API read it."""
    response = auth_client.get("/api/docs/changelog.md")

    assert response.status_code == 200
    assert response.text.startswith("# Changelog")


def test_docs_page_endpoint_can_serve_child_tech_spec(auth_client):
    """Child technical specs should be normal in-app docs pages."""
    response = auth_client.get("/api/docs/tech-spec/backend-api.md")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "# Backend and API Architecture" in response.text


def test_docs_nav_nested_paths_are_correct(auth_client):
    """Nested nav items must use the path from zensical.toml verbatim, not a mangled base_path prefix."""
    response = auth_client.get("/api/docs/nav")
    assert response.status_code == 200
    nav = response.json()

    def collect_paths(items: list) -> list:
        paths = []
        for item in items:
            if "path" in item:
                paths.append(item["path"])
            if "children" in item:
                paths.extend(collect_paths(item["children"]))
        return paths

    paths = collect_paths(nav)
    # Nested Feature Specs path should start with "feature-specs/", not "feature specs/feature-specs/"
    feature_spec_paths = [p for p in paths if "feature-specs" in p]
    assert feature_spec_paths, "Expected at least one feature-specs path in nav"
    for p in feature_spec_paths:
        assert not p.startswith("feature specs"), f"Mangled path detected: {p!r}"
        assert p.startswith("feature-specs/"), f"Unexpected path prefix: {p!r}"

    tech_spec_paths = [p for p in paths if p.startswith("tech-spec")]
    assert "tech-spec.md" in paths
    assert "tech-spec/backend-api.md" in tech_spec_paths


def test_openapi_docs_are_disabled_by_default(client):
    """Interactive API docs should not be exposed publicly unless enabled."""
    response = client.get("/apidocs")
    assert response.status_code == 404

    openapi_response = client.get("/openapi.json")
    assert openapi_response.status_code == 404


def test_openapi_docs_can_be_enabled_explicitly(client, monkeypatch):
    """Operators can still opt into the interactive docs when needed."""
    monkeypatch.setattr(config.settings, "public_api_docs_enabled", True)

    with SyncASGIClient(create_fastapi_app()) as docs_client:
        docs_client.headers["origin"] = "http://testserver"
        response = docs_client.get("/apidocs")

    assert response.status_code == 200
    assert "swagger-ui" in response.text
    assert "/openapi.json" in response.text


def test_cors_allows_the_base_url_origin_without_listing_it(client, monkeypatch):
    """The dashboard served from base_url passes CORS even when allowed_origins only lists the dev server."""
    monkeypatch.setattr(config.settings, "allowed_origins", ["http://localhost:5173"])
    monkeypatch.setattr(config.settings, "base_url", "http://testserver:8080")
    monkeypatch.setattr(config.settings, "allowed_hosts", ["testserver"])
    preflight = {"access-control-request-method": "GET"}

    with SyncASGIClient(create_fastapi_app()) as cors_client:
        allowed = cors_client.request(
            "OPTIONS", "/api/status", headers={**preflight, "origin": "http://testserver:8080"}
        )
        rejected = cors_client.request(
            "OPTIONS", "/api/status", headers={**preflight, "origin": "http://testserver:9090"}
        )

    assert allowed.headers.get("access-control-allow-origin") == "http://testserver:8080"
    assert "access-control-allow-origin" not in rejected.headers


def test_no_route_is_registered_twice():
    """Every path and method pair maps to exactly one route."""
    seen: dict[tuple[str, str], int] = {}
    for route in create_fastapi_app().routes:
        if not isinstance(route, APIRoute | APIWebSocketRoute):
            continue
        for method in getattr(route, "methods", None) or {"WEBSOCKET"}:
            key = (route.path, method)
            seen[key] = seen.get(key, 0) + 1

    assert {key: count for key, count in seen.items() if count > 1} == {}


def test_unexpected_host_headers_are_rejected(client, monkeypatch):
    """Requests with a Host header outside the allowlist should fail closed."""
    monkeypatch.setattr(config.settings, "allowed_hosts", ["localhost"])

    response = client.get("/api/docs/nav", headers={"host": "evil.example"})

    assert response.status_code == 400
    assert response.text == "Invalid Host header"


def test_trusted_forwarded_host_can_satisfy_allowlist(auth_client, monkeypatch):
    """Trusted forwarded hosts should be validated as the externally visible host."""
    monkeypatch.setattr(config.settings, "allowed_hosts", ["public.example.com"])
    monkeypatch.setattr(config.settings, "trusted_proxy_hops", 1)

    response = auth_client.get(
        "/api/docs/nav",
        headers={
            "host": "internal.service",
            "x-forwarded-host": "public.example.com",
        },
    )

    assert response.status_code == 200


@pytest.mark.parametrize(
    "path",
    [
        "/api/docs/%2e%2e/%2e%2e/pyproject.toml",
        "/api/docs/..%2F..%2Fpyproject.toml",
        "/api/docs/%252e%252e/%252e%252e/pyproject.toml",
        "/api/docs/%2e%2e%5c%2e%2e%5cpyproject.toml",
    ],
)
def test_docs_page_endpoint_blocks_traversal(auth_client, path):
    """Test docs endpoint traversal protection for common path variants."""
    response = auth_client.get(path)
    assert response.status_code == 404
    assert 'name = "janus"' not in response.text


@pytest.mark.parametrize(
    "path",
    [
        "/api/docs/..",
        "/api/docs/%2e%2e",
        "/api/docs/.",
        "/api/docs/%2e",
        "/api/docs/feature-specs/%2e%2e",
        "/api/docs/feature-specs/%2e",
    ],
)
def test_docs_page_endpoint_rejects_directory_paths(auth_client, path):
    """Test docs endpoint rejects dot-path variants that resolve to directories."""
    response = auth_client.get(path)
    assert response.status_code == 404
    assert "# Getting Started with Janus" not in response.text


def test_dashboard_widget_preferences_return_defaults(auth_client):
    """Dashboard widget preferences should materialize shipped defaults for new users."""
    response = auth_client.get("/api/dashboard/widgets")

    assert response.status_code == 200
    assert response.json() == [
        {
            "widget_key": "briefing",
            "is_visible": True,
            "is_collapsed": False,
            "prompt_override": None,
            "briefing_email_window": None,
        },
        {
            "widget_key": "topic_focus",
            "is_visible": True,
            "is_collapsed": False,
            "prompt_override": None,
            "briefing_email_window": None,
        },
        {
            "widget_key": "important_emails",
            "is_visible": True,
            "is_collapsed": False,
            "prompt_override": None,
            "briefing_email_window": None,
        },
        {
            "widget_key": "whatsapp_messages",
            "is_visible": True,
            "is_collapsed": False,
            "prompt_override": None,
            "briefing_email_window": None,
        },
        {
            "widget_key": "schedule",
            "is_visible": True,
            "is_collapsed": False,
            "prompt_override": None,
            "briefing_email_window": None,
        },
        {
            "widget_key": "tasks",
            "is_visible": True,
            "is_collapsed": False,
            "prompt_override": None,
            "briefing_email_window": None,
        },
    ]


def test_dashboard_widget_preferences_update_and_persist(auth_client, db):
    """Widget preference updates should persist per user and return the full effective set."""
    response = auth_client.patch(
        "/api/dashboard/widgets",
        json={
            "widgets": [
                {"widget_key": "briefing", "is_collapsed": True, "prompt_override": "Keep it action-oriented."},
                {"widget_key": "topic_focus", "is_visible": False},
            ]
        },
    )

    assert response.status_code == 200
    assert response.json() == [
        {
            "widget_key": "briefing",
            "is_visible": True,
            "is_collapsed": True,
            "prompt_override": "Keep it action-oriented.",
            "briefing_email_window": None,
        },
        {
            "widget_key": "topic_focus",
            "is_visible": False,
            "is_collapsed": False,
            "prompt_override": None,
            "briefing_email_window": None,
        },
        {
            "widget_key": "important_emails",
            "is_visible": True,
            "is_collapsed": False,
            "prompt_override": None,
            "briefing_email_window": None,
        },
        {
            "widget_key": "whatsapp_messages",
            "is_visible": True,
            "is_collapsed": False,
            "prompt_override": None,
            "briefing_email_window": None,
        },
        {
            "widget_key": "schedule",
            "is_visible": True,
            "is_collapsed": False,
            "prompt_override": None,
            "briefing_email_window": None,
        },
        {
            "widget_key": "tasks",
            "is_visible": True,
            "is_collapsed": False,
            "prompt_override": None,
            "briefing_email_window": None,
        },
    ]

    persisted = (
        db.query(models.DashboardWidgetPreference).order_by(models.DashboardWidgetPreference.widget_key.asc()).all()
    )
    assert [
        (
            row.widget_key.value,
            row.is_visible,
            row.is_collapsed,
            row.prompt_override,
            row.briefing_email_window,
        )
        for row in persisted
    ] == [
        ("briefing", True, True, "Keep it action-oriented.", None),
        ("topic_focus", False, False, None, None),
    ]


def test_dashboard_widget_preferences_are_user_specific(client, db):
    """One user's widget preference changes must not leak to another user's session."""
    hashed_pw = security.security_service.get_password_hash("admin")
    db.add_all(
        [
            models.User(username="first-admin", hashed_password=hashed_pw, is_admin=True),
            models.User(username="second-admin", hashed_password=hashed_pw, is_admin=True),
        ]
    )
    db.commit()

    first_login = client.post("/api/auth/login", json={"username": "first-admin", "password": "admin"})
    client.headers[security.CSRF_HEADER_NAME] = first_login.json()["csrf_token"]
    update_response = client.patch(
        "/api/dashboard/widgets",
        json={"widgets": [{"widget_key": "schedule", "is_collapsed": True}]},
    )
    assert update_response.status_code == 200

    logout_response = client.post("/api/auth/logout")
    assert logout_response.status_code == 200

    second_login = client.post("/api/auth/login", json={"username": "second-admin", "password": "admin"})
    client.headers[security.CSRF_HEADER_NAME] = second_login.json()["csrf_token"]

    response = client.get("/api/dashboard/widgets")

    assert response.status_code == 200
    assert response.json() == [
        {
            "widget_key": "briefing",
            "is_visible": True,
            "is_collapsed": False,
            "prompt_override": None,
            "briefing_email_window": None,
        },
        {
            "widget_key": "topic_focus",
            "is_visible": True,
            "is_collapsed": False,
            "prompt_override": None,
            "briefing_email_window": None,
        },
        {
            "widget_key": "important_emails",
            "is_visible": True,
            "is_collapsed": False,
            "prompt_override": None,
            "briefing_email_window": None,
        },
        {
            "widget_key": "whatsapp_messages",
            "is_visible": True,
            "is_collapsed": False,
            "prompt_override": None,
            "briefing_email_window": None,
        },
        {
            "widget_key": "schedule",
            "is_visible": True,
            "is_collapsed": False,
            "prompt_override": None,
            "briefing_email_window": None,
        },
        {
            "widget_key": "tasks",
            "is_visible": True,
            "is_collapsed": False,
            "prompt_override": None,
            "briefing_email_window": None,
        },
    ]


def test_dashboard_widget_prompt_override_can_be_cleared(auth_client, db):
    """Prompt overrides can be explicitly reset back to the shipped default."""
    response = auth_client.patch(
        "/api/dashboard/widgets",
        json={"widgets": [{"widget_key": "briefing", "prompt_override": "Focus on deadlines first."}]},
    )
    assert response.status_code == 200

    response = auth_client.patch(
        "/api/dashboard/widgets",
        json={"widgets": [{"widget_key": "briefing", "prompt_override": None}]},
    )
    assert response.status_code == 200
    assert response.json()[0] == {
        "widget_key": "briefing",
        "is_visible": True,
        "is_collapsed": False,
        "prompt_override": None,
        "briefing_email_window": None,
    }

    persisted = (
        db.query(models.DashboardWidgetPreference).filter_by(widget_key=models.DashboardWidgetKey.BRIEFING).one()
    )
    assert persisted.prompt_override is None


def test_dashboard_widget_prompt_override_blank_values_round_trip_as_none(auth_client, db):
    """Blank prompt overrides should be normalized to the default-prompt null state."""
    response = auth_client.patch(
        "/api/dashboard/widgets",
        json={"widgets": [{"widget_key": "briefing", "prompt_override": "   \n\t  "}]},
    )
    assert response.status_code == 200
    assert response.json()[0] == {
        "widget_key": "briefing",
        "is_visible": True,
        "is_collapsed": False,
        "prompt_override": None,
        "briefing_email_window": None,
    }

    persisted = (
        db.query(models.DashboardWidgetPreference).filter_by(widget_key=models.DashboardWidgetKey.BRIEFING).one()
    )
    assert persisted.prompt_override is None

    db.add(
        models.DashboardWidgetPreference(
            user_id=persisted.user_id,
            widget_key=models.DashboardWidgetKey.TASKS,
            prompt_override="   ",
        )
    )
    db.commit()

    response = auth_client.get("/api/dashboard/widgets")
    assert response.status_code == 200
    tasks_preference = next(item for item in response.json() if item["widget_key"] == "tasks")
    assert tasks_preference["prompt_override"] is None


def test_dashboard_widget_endpoints_require_authentication(client, db):
    """Dashboard widget endpoints must reject unauthenticated requests."""
    get_response = client.get("/api/dashboard/widgets")
    assert get_response.status_code == 401

    patch_response = client.patch(
        "/api/dashboard/widgets",
        json={"widgets": [{"widget_key": "briefing", "is_visible": False}]},
    )
    assert patch_response.status_code == 401


def test_dashboard_aggregate_error_names_the_failing_widget(auth_client, monkeypatch):
    """Aggregate dashboard failures should point to the broken widget with actionable detail."""

    def boom(self, user):
        raise RuntimeError("planner backend is unavailable")

    monkeypatch.setattr(dashboard_api.DashboardService, "get_tasks_widget_data", boom)

    response = auth_client.get("/api/dashboard/")

    assert response.status_code == 500
    detail = response.json()["detail"]
    assert detail["code"] == "dashboard_widget_load_failed"
    assert detail["widget"] == "tasks"
    assert detail["message"] == "Failed to load the tasks widget."
    assert detail["hint"] == "Check planner task records and planner account connectivity."
    assert detail["cause"] == "planner backend is unavailable"


def test_dashboard_widget_endpoints_isolate_widget_failures(auth_client, monkeypatch):
    """One failing widget endpoint should not prevent healthy widgets from loading."""

    def boom(self, user):
        raise RuntimeError("planner backend is unavailable")

    monkeypatch.setattr(dashboard_api.DashboardService, "get_tasks_widget_data", boom)

    failed_response = auth_client.get("/api/dashboard/tasks")
    assert failed_response.status_code == 500
    assert failed_response.json()["detail"]["widget"] == "tasks"

    healthy_response = auth_client.get("/api/dashboard/important-emails")
    assert healthy_response.status_code == 200
    assert healthy_response.json() == []


def test_dashboard_schedule_and_task_widget_loaders_receive_authenticated_user(auth_client, monkeypatch):
    """Aggregate and dedicated widget routes should pass the authenticated user into scoped loaders."""
    calls: list[tuple[str, str]] = []

    def important_emails(self, user):
        return []

    def whatsapp_messages(self, user):
        calls.append(("whatsapp_messages", user.username))
        return []

    def recent_emails(self, user):
        return []

    def schedule(self, user):
        calls.append(("schedule", user.username))
        return []

    def tasks(self, user):
        calls.append(("tasks", user.username))
        return []

    def topics(self, user):
        return []

    monkeypatch.setattr(dashboard_api.DashboardService, "get_important_emails_widget_data", important_emails)
    monkeypatch.setattr(dashboard_api.DashboardService, "get_whatsapp_messages_widget_data", whatsapp_messages)
    monkeypatch.setattr(dashboard_api.DashboardService, "get_recent_emails_widget_data", recent_emails)
    monkeypatch.setattr(dashboard_api.DashboardService, "get_schedule_widget_data", schedule)
    monkeypatch.setattr(dashboard_api.DashboardService, "get_tasks_widget_data", tasks)
    monkeypatch.setattr(dashboard_api.DashboardService, "get_topic_focus_widget_data", topics)

    aggregate_response = auth_client.get("/api/dashboard/")
    whatsapp_response = auth_client.get("/api/dashboard/whatsapp-messages")
    events_response = auth_client.get("/api/dashboard/events")
    tasks_response = auth_client.get("/api/dashboard/tasks")

    assert aggregate_response.status_code == 200
    assert whatsapp_response.status_code == 200
    assert events_response.status_code == 200
    assert tasks_response.status_code == 200
    assert calls == [
        ("whatsapp_messages", "admin"),
        ("schedule", "admin"),
        ("tasks", "admin"),
        ("whatsapp_messages", "admin"),
        ("schedule", "admin"),
        ("tasks", "admin"),
    ]


def test_whatsapp_account_and_widget_endpoints(auth_client, db):
    """WhatsApp accounts and dashboard widget surfaces should be available to authenticated users."""
    user = db.query(models.User).filter(models.User.username == "admin").one()
    account = models.WhatsAppAccount(
        user_id=user.id,
        display_name="Personal WhatsApp",
        provider="whatsapp_neonize",
        connection_status=models.WhatsAppConnectionStatus.CONNECTED,
        initial_history_days=3,
        is_enabled=True,
    )
    db.add(account)
    db.flush()
    conversation = models.WhatsAppConversation(
        user_id=user.id,
        whatsapp_account_id=account.id,
        provider_chat_id="chat-1",
        conversation_type=models.WhatsAppConversationType.DIRECT,
        title="Alice",
        latest_message_at=datetime.now(UTC),
        latest_message_snippet="See you later",
    )
    db.add(conversation)
    db.flush()
    db.add(
        models.WhatsAppMessage(
            user_id=user.id,
            whatsapp_account_id=account.id,
            whatsapp_conversation_id=conversation.id,
            provider_message_id="wamid-1",
            provider_timestamp=datetime.now(UTC),
            direction=models.WhatsAppMessageDirection.INCOMING,
            sender_identifier="4912345@s.whatsapp.net",
            sender_display_name="Alice",
            body="See you later",
            snippet="See you later",
            message_type="text",
            priority=models.Priority.DIGEST,
        )
    )
    db.commit()

    accounts_response = auth_client.get("/api/whatsapp/accounts")
    widget_response = auth_client.get("/api/dashboard/whatsapp-messages")
    conversations_response = auth_client.get("/api/whatsapp/conversations")

    assert accounts_response.status_code == 200
    assert accounts_response.json()[0]["display_name"] == "Personal WhatsApp"
    assert accounts_response.json()[0]["session_configured"] is False

    assert widget_response.status_code == 200
    assert widget_response.json()[0]["sender_display_name"] == "Alice"
    assert widget_response.json()[0]["snippet"] == "See you later"

    assert conversations_response.status_code == 200
    assert conversations_response.json()[0]["title"] == "Alice"


def test_whatsapp_sync_requires_configured_neonize_session(auth_client, db):
    """Sync attempts should fail cleanly when the Neonize session is not configured."""
    create_response = auth_client.post(
        "/api/whatsapp/accounts",
        json={
            "display_name": "Phone",
            "initial_history_days": 4,
            "is_enabled": True,
        },
    )
    assert create_response.status_code == 200

    account_id = create_response.json()["id"]
    sync_response = auth_client.post(f"/api/whatsapp/accounts/{account_id}/sync")

    assert sync_response.status_code == 400
    assert "No Neonize session is configured for this account yet." in sync_response.json()["detail"]


def test_whatsapp_setup_session_lifecycle_endpoints(auth_client, db, monkeypatch):
    """The setup-session API should create, import fallback state, and complete into one account."""
    monkeypatch.setattr(
        "janus.whatsapp_neonize.NeonizeAdapter.get_pairing_capability",
        lambda self: NeonizePairingCapability(
            supported=False,
            message="Live pairing unavailable here.",
        ),
    )

    monkeypatch.setattr("janus.workers.whatsapp.start_account_connector", lambda account: None)

    create_response = auth_client.post(
        "/api/whatsapp/setup-sessions",
        json={"display_name": "Phone", "initial_history_days": 4},
    )
    assert create_response.status_code == 200
    session_id = create_response.json()["id"]
    assert create_response.json()["show_fallback_import"] is True
    assert create_response.json()["pairing_state"] == "unsupported"

    get_response = auth_client.get(f"/api/whatsapp/setup-sessions/{session_id}")
    assert get_response.status_code == 200
    assert get_response.json()["display_name"] == "Phone"

    fallback_response = auth_client.post(
        f"/api/whatsapp/setup-sessions/{session_id}/fallback-import",
        json={"session_database_path": "/tmp/neonize.db"},
    )
    assert fallback_response.status_code == 200
    assert fallback_response.json()["setup_status"] == "ready"
    assert fallback_response.json()["session_configured"] is True

    complete_response = auth_client.post(f"/api/whatsapp/setup-sessions/{session_id}/complete")
    assert complete_response.status_code == 200
    assert complete_response.json()["account"]["display_name"] == "Phone"
    assert complete_response.json()["sync_result"]["connector_started"] is True

    accounts = auth_client.get("/api/whatsapp/accounts")
    assert accounts.status_code == 200
    assert len(accounts.json()) == 1
    assert accounts.json()[0]["session_configured"] is True


def test_whatsapp_setup_session_without_history_days(auth_client, db, monkeypatch):
    """The setup UI no longer sends ``initial_history_days``; the API must not require it (old clients may)."""
    monkeypatch.setattr(
        "janus.whatsapp_neonize.NeonizeAdapter.get_pairing_capability",
        lambda self: NeonizePairingCapability(supported=False, message="Live pairing unavailable here."),
    )

    response = auth_client.post("/api/whatsapp/setup-sessions", json={"display_name": "Phone"})
    assert response.status_code == 200
    session = db.get(models.WhatsAppSetupSession, response.json()["id"])
    assert session is not None
    assert session.initial_history_days == 3

    legacy = auth_client.post(
        "/api/whatsapp/setup-sessions",
        json={"display_name": "Phone", "initial_history_days": 9},
    )
    assert legacy.status_code == 200


def test_whatsapp_setup_session_rejects_cross_user_access(auth_client, db):
    """Setup sessions should stay scoped to the authenticated user."""
    current_user = db.query(models.User).filter(models.User.username == "admin").one()
    other_user = models.User(
        username="other-admin",
        hashed_password=security.security_service.get_password_hash("admin"),
        is_admin=True,
    )
    db.add(other_user)
    db.flush()

    setup_session = models.WhatsAppSetupSession(
        user_id=other_user.id,
        display_name="Other Phone",
        initial_history_days=3,
        setup_status=models.WhatsAppSetupStatus.CREATED,
        pairing_state=models.WhatsAppPairingState.IDLE,
        pairing_supported=False,
        show_fallback_import=True,
        expires_at=time_utils.utcnow() + timedelta(minutes=15),
    )
    db.add(setup_session)
    db.commit()

    assert current_user.id != other_user.id

    response = auth_client.get(f"/api/whatsapp/setup-sessions/{setup_session.id}")
    assert response.status_code == 404


def test_whatsapp_setup_session_fallback_import_requires_artifact(auth_client):
    """Fallback import should reject empty payloads."""
    create_response = auth_client.post(
        "/api/whatsapp/setup-sessions",
        json={"display_name": "Phone", "initial_history_days": 4},
    )
    assert create_response.status_code == 200
    session_id = create_response.json()["id"]

    response = auth_client.post(
        f"/api/whatsapp/setup-sessions/{session_id}/fallback-import",
        json={},
    )
    assert response.status_code == 422


def test_whatsapp_setup_session_reconnect_reuses_existing_account(auth_client, db, monkeypatch):
    """Reconnect completion should update the existing account rather than duplicate it."""
    user = db.query(models.User).filter(models.User.username == "admin").one()
    account = models.WhatsAppAccount(
        user_id=user.id,
        display_name="Phone",
        provider="whatsapp_neonize",
        session_database_path="/tmp/old.db",
        connection_status=models.WhatsAppConnectionStatus.DEGRADED,
        initial_history_days=3,
        is_enabled=True,
    )
    db.add(account)
    db.commit()
    db.refresh(account)

    monkeypatch.setattr("janus.workers.whatsapp.start_account_connector", lambda acct: None)

    create_response = auth_client.post(
        "/api/whatsapp/setup-sessions",
        json={"display_name": "Phone", "initial_history_days": 7, "account_id": account.id},
    )
    assert create_response.status_code == 200
    session_id = create_response.json()["id"]

    fallback_response = auth_client.post(
        f"/api/whatsapp/setup-sessions/{session_id}/fallback-import",
        json={"session_database_path": "/tmp/new.db"},
    )
    assert fallback_response.status_code == 200

    complete_response = auth_client.post(f"/api/whatsapp/setup-sessions/{session_id}/complete")
    assert complete_response.status_code == 200

    db.refresh(account)
    assert db.query(models.WhatsAppAccount).count() == 1
    assert account.session_database_path == "/tmp/new.db"
    assert account.initial_history_days == 7


def test_dashboard_schedule_and_task_widget_data_is_scoped_to_authenticated_user(auth_client, db):
    """Dashboard schedule/task payloads should only include planner data owned by the current user."""
    current_user = db.query(models.User).filter(models.User.username == "admin").one()
    other_user = models.User(
        username="other-admin",
        hashed_password=security.security_service.get_password_hash("admin"),
        is_admin=True,
    )
    db.add(other_user)
    db.flush()

    current_account = models.PlannerAccount(
        name="Current User Planner",
        provider=models.Provider.GOOGLE_CALENDAR,
        token="encrypted-token",
        user_id=current_user.id,
    )
    other_account = models.PlannerAccount(
        name="Other User Planner",
        provider=models.Provider.GOOGLE_CALENDAR,
        token="encrypted-token",
        user_id=other_user.id,
    )
    db.add_all([current_account, other_account])
    db.flush()

    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    db.add_all(
        [
            models.CalendarEvent(
                planner_account_id=current_account.id,
                external_id="current-user-event",
                summary="Current user event",
                start_time=now,
                end_time=now,
            ),
            models.CalendarEvent(
                planner_account_id=other_account.id,
                external_id="other-user-event",
                summary="Other user event",
                start_time=now,
                end_time=now,
            ),
            models.TaskRecord(
                planner_account_id=current_account.id,
                external_id="JAN-1",
                title="Current user task",
                status=models.TaskStatus.TODO,
            ),
            models.TaskRecord(
                planner_account_id=other_account.id,
                external_id="JAN-2",
                title="Other user task",
                status=models.TaskStatus.TODO,
            ),
        ]
    )
    db.commit()

    events_response = auth_client.get("/api/dashboard/events")
    tasks_response = auth_client.get("/api/dashboard/tasks")
    aggregate_response = auth_client.get("/api/dashboard/")

    assert events_response.status_code == 200
    assert [event["title"] for event in events_response.json()] == ["Current user event"]

    assert tasks_response.status_code == 200
    assert [task["title"] for task in tasks_response.json()] == ["Current user task"]

    assert aggregate_response.status_code == 200
    assert [event["title"] for event in aggregate_response.json()["events"]] == ["Current user event"]
    assert [task["title"] for task in aggregate_response.json()["tasks"]] == ["Current user task"]


def test_dashboard_aggregate_widget_error_does_not_replay_healthy_loaders(auth_client, monkeypatch):
    """Aggregate widget failures should surface during the primary load without replaying healthy loaders."""
    call_counts = {
        "important_emails": 0,
        "whatsapp_messages": 0,
        "schedule": 0,
        "tasks": 0,
        "topics": 0,
    }

    def important_emails(self, user):
        call_counts["important_emails"] += 1
        return []

    def recent_emails(self, user):
        return []

    def whatsapp_messages(self, user):
        call_counts["whatsapp_messages"] += 1
        return []

    def schedule(self, user):
        call_counts["schedule"] += 1
        return []

    def tasks(self, user):
        call_counts["tasks"] += 1
        raise RuntimeError("planner backend is unavailable")

    def topics(self, user):
        call_counts["topics"] += 1
        return []

    monkeypatch.setattr(dashboard_api.DashboardService, "get_important_emails_widget_data", important_emails)
    monkeypatch.setattr(dashboard_api.DashboardService, "get_whatsapp_messages_widget_data", whatsapp_messages)
    monkeypatch.setattr(dashboard_api.DashboardService, "get_recent_emails_widget_data", recent_emails)
    monkeypatch.setattr(dashboard_api.DashboardService, "get_schedule_widget_data", schedule)
    monkeypatch.setattr(dashboard_api.DashboardService, "get_tasks_widget_data", tasks)
    monkeypatch.setattr(dashboard_api.DashboardService, "get_topic_focus_widget_data", topics)

    response = auth_client.get("/api/dashboard/")

    assert response.status_code == 500
    assert response.json()["detail"]["widget"] == "tasks"
    assert call_counts == {
        "important_emails": 1,
        "whatsapp_messages": 1,
        "schedule": 1,
        "tasks": 1,
        "topics": 0,
    }


def test_dashboard_widget_preference_full_lifecycle(auth_client, db):
    """Full round-trip: default state → toggle visibility and collapse → set prompt → verify GET."""
    # Step 1 — new user starts with all defaults
    response = auth_client.get("/api/dashboard/widgets")
    assert response.status_code == 200
    defaults = response.json()
    assert all(w["is_visible"] for w in defaults)
    assert all(not w["is_collapsed"] for w in defaults)
    assert all(w["prompt_override"] is None for w in defaults)
    assert all(w["briefing_email_window"] is None for w in defaults)

    # Step 2 — hide the schedule widget and collapse the tasks widget
    response = auth_client.patch(
        "/api/dashboard/widgets",
        json={
            "widgets": [
                {"widget_key": "schedule", "is_visible": False},
                {"widget_key": "tasks", "is_collapsed": True},
            ]
        },
    )
    assert response.status_code == 200
    prefs_by_key = {w["widget_key"]: w for w in response.json()}
    assert prefs_by_key["schedule"]["is_visible"] is False
    assert prefs_by_key["schedule"]["is_collapsed"] is False  # untouched
    assert prefs_by_key["tasks"]["is_visible"] is True  # untouched
    assert prefs_by_key["tasks"]["is_collapsed"] is True

    # Step 3 — add a briefing prompt override
    response = auth_client.patch(
        "/api/dashboard/widgets",
        json={"widgets": [{"widget_key": "briefing", "prompt_override": "Lead with calendar blocks."}]},
    )
    assert response.status_code == 200
    prefs_by_key = {w["widget_key"]: w for w in response.json()}
    assert prefs_by_key["briefing"]["prompt_override"] == "Lead with calendar blocks."
    assert prefs_by_key["briefing"]["briefing_email_window"] is None

    # Step 4 — GET must reflect all three persisted changes
    response = auth_client.get("/api/dashboard/widgets")
    assert response.status_code == 200
    prefs_by_key = {w["widget_key"]: w for w in response.json()}
    assert prefs_by_key["briefing"]["prompt_override"] == "Lead with calendar blocks."
    assert prefs_by_key["schedule"]["is_visible"] is False
    assert prefs_by_key["tasks"]["is_collapsed"] is True
    # unchanged widgets retain their defaults
    assert prefs_by_key["topic_focus"]["is_visible"] is True
    assert prefs_by_key["topic_focus"]["is_collapsed"] is False
    assert prefs_by_key["topic_focus"]["prompt_override"] is None
    assert prefs_by_key["topic_focus"]["briefing_email_window"] is None
    assert prefs_by_key["important_emails"]["is_visible"] is True
    assert prefs_by_key["important_emails"]["is_collapsed"] is False
    assert prefs_by_key["important_emails"]["prompt_override"] is None
    assert prefs_by_key["important_emails"]["briefing_email_window"] is None


def test_dashboard_widget_empty_patch_preserves_state(auth_client, db):
    """Patching with an empty widgets list must return current preferences unchanged."""
    # Establish some non-default state first
    auth_client.patch(
        "/api/dashboard/widgets",
        json={"widgets": [{"widget_key": "tasks", "is_visible": False}]},
    )

    response = auth_client.patch("/api/dashboard/widgets", json={"widgets": []})
    assert response.status_code == 200
    prefs_by_key = {w["widget_key"]: w for w in response.json()}
    # The earlier change must still be visible
    assert prefs_by_key["tasks"]["is_visible"] is False
    # Nothing else changed
    assert prefs_by_key["briefing"]["is_visible"] is True
    assert prefs_by_key["briefing"]["is_collapsed"] is False
    assert prefs_by_key["briefing"]["briefing_email_window"] is None


def test_dashboard_widget_repeated_updates_produce_one_db_row(auth_client, db):
    """Updating the same widget multiple times must upsert, not insert duplicate rows."""
    for is_collapsed in (True, False, True):
        auth_client.patch(
            "/api/dashboard/widgets",
            json={"widgets": [{"widget_key": "briefing", "is_collapsed": is_collapsed}]},
        )

    rows = db.query(models.DashboardWidgetPreference).filter_by(widget_key=models.DashboardWidgetKey.BRIEFING).all()
    assert len(rows) == 1
    assert rows[0].is_collapsed is True  # last write wins


def test_dashboard_widget_partial_update_preserves_other_fields(auth_client, db):
    """Updating only is_visible must leave is_collapsed and prompt_override intact."""
    # Set an initial state with all three fields populated
    auth_client.patch(
        "/api/dashboard/widgets",
        json={
            "widgets": [
                {
                    "widget_key": "briefing",
                    "is_visible": True,
                    "is_collapsed": True,
                    "prompt_override": "Be concise.",
                    "briefing_email_window": "yesterday",
                }
            ]
        },
    )

    # Now only toggle visibility
    response = auth_client.patch(
        "/api/dashboard/widgets",
        json={"widgets": [{"widget_key": "briefing", "is_visible": False}]},
    )
    assert response.status_code == 200
    briefing = next(w for w in response.json() if w["widget_key"] == "briefing")
    assert briefing["is_visible"] is False
    assert briefing["is_collapsed"] is True  # must be preserved
    assert briefing["prompt_override"] == "Be concise."  # must be preserved
    assert briefing["briefing_email_window"] == "yesterday"  # must be preserved


def test_dashboard_widget_briefing_email_window_can_be_saved_and_cleared(auth_client, db):
    """Briefing widgets should persist a configured email window independently of the prompt."""
    response = auth_client.patch(
        "/api/dashboard/widgets",
        json={"widgets": [{"widget_key": "briefing", "briefing_email_window": "yesterday"}]},
    )
    assert response.status_code == 200
    assert response.json()[0]["briefing_email_window"] == "yesterday"

    response = auth_client.patch(
        "/api/dashboard/widgets",
        json={"widgets": [{"widget_key": "briefing", "briefing_email_window": None}]},
    )
    assert response.status_code == 200
    assert response.json()[0]["briefing_email_window"] is None

    persisted = (
        db.query(models.DashboardWidgetPreference).filter_by(widget_key=models.DashboardWidgetKey.BRIEFING).one()
    )
    assert persisted.briefing_email_window is None


def test_dashboard_widget_update_with_invalid_key_returns_422(auth_client):
    """A PATCH request with an unrecognized widget key must be rejected."""
    response = auth_client.patch(
        "/api/dashboard/widgets",
        json={"widgets": [{"widget_key": "does_not_exist", "is_visible": False}]},
    )
    assert response.status_code == 422


def test_dashboard_widget_update_with_no_mutable_fields_returns_422(auth_client):
    """A widget update object that specifies no mutable fields must be rejected."""
    response = auth_client.patch(
        "/api/dashboard/widgets",
        json={"widgets": [{"widget_key": "briefing"}]},
    )
    assert response.status_code == 422


# --- Email Tests ---


def test_email_accounts_and_rules(auth_client, db):
    """Test creating email accounts and managing processing rules."""
    # 1. Add account
    acc_data = {
        "name": "Test Account",
        "email": "test@example.com",
        "imap_server": "imap.example.com",
        "imap_port": 993,
        "username": "test@example.com",
        "password": "password123",
    }
    response = auth_client.post("/api/emails/accounts", json=acc_data)
    assert response.status_code == 200
    account = response.json()
    assert account["username"] == "test@example.com"

    response = auth_client.patch(
        f"/api/emails/accounts/{account['id']}",
        json={
            "name": "Updated Account",
            "imap_server": "imap.mail.example.com",
            "imap_port": 995,
            "username": "updated@example.com",
        },
    )
    assert response.status_code == 200
    updated = response.json()
    assert updated["name"] == "Updated Account"
    assert updated["imap_server"] == "imap.mail.example.com"
    assert updated["imap_port"] == 995
    assert updated["username"] == "updated@example.com"

    # 2. Add rule
    rule_data = {"name": "Important Rule", "pattern": "urgent", "field": "subject", "priority": "important"}
    response = auth_client.post("/api/emails/rules", json=rule_data)
    assert response.status_code == 200
    response.json()["id"]

    # 3. List rules
    response = auth_client.get("/api/emails/rules")
    assert len(response.json()) == 1

    # 4. List emails (should be empty but 200)
    response = auth_client.get("/api/emails/")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_email_list_and_dashboard_include_related_topic_badges(auth_client, db):
    """Email and dashboard payloads should include related tracked topics for drill-down badges."""
    user = db.query(models.User).filter(models.User.username == "admin").one()
    account = models.EmailAccount(
        name="Primary",
        email="admin@example.com",
        imap_server="imap.example.com",
        imap_port=993,
        username="admin@example.com",
        password="encrypted",
        user_id=user.id,
    )
    db.add(account)
    db.flush()

    email = models.EmailRecord(
        message_id="<topic-email@example.com>",
        sender="alice@example.com",
        subject="Hiring update",
        snippet="We should move the final interview to Friday.",
        body="Can we move the final interview to Friday?",
        priority=models.Priority.IMPORTANT,
        received_at=datetime.now(UTC),
        account_id=account.id,
    )
    db.add(email)
    db.flush()

    pinned_topic = models.TrackedTopic(
        user_id=user.id,
        slug="hiring-pipeline",
        title="Hiring pipeline",
        summary="Interview coordination and offer planning.",
        status=models.TopicStatus.ACTIVE,
        pinned=True,
        origin=models.TopicOrigin.AUTO,
        confidence=models.ConfidenceLevel.HIGH,
    )
    newer_unpinned_topic = models.TrackedTopic(
        user_id=user.id,
        slug="candidate-follow-up",
        title="Candidate follow-up",
        summary="Collecting interview notes.",
        status=models.TopicStatus.SNOOZED,
        pinned=False,
        origin=models.TopicOrigin.AUTO,
        confidence=models.ConfidenceLevel.MEDIUM,
    )
    db.add_all([pinned_topic, newer_unpinned_topic])
    db.flush()

    db.add_all(
        [
            models.TopicEvidence(
                topic_id=pinned_topic.id,
                source_type=models.TopicEvidenceSource.EMAIL,
                source_id=email.id,
                excerpt="The offer committee is aligned.",
                evidence_role=models.TopicEvidenceRole.TRIGGER,
            ),
            models.TopicEvidence(
                topic_id=newer_unpinned_topic.id,
                source_type=models.TopicEvidenceSource.EMAIL,
                source_id=email.id,
                excerpt="Waiting for one more interviewer.",
                evidence_role=models.TopicEvidenceRole.SUPPORTING,
            ),
        ]
    )
    db.commit()

    email_response = auth_client.get("/api/emails/")
    assert email_response.status_code == 200
    assert email_response.json()[0]["topics"] == [
        {"id": pinned_topic.id, "title": "Hiring pipeline", "status": "active", "pinned": True},
        {"id": newer_unpinned_topic.id, "title": "Candidate follow-up", "status": "snoozed", "pinned": False},
    ]

    dashboard_response = auth_client.get("/api/dashboard/")
    assert dashboard_response.status_code == 200
    assert dashboard_response.json()["important_emails"][0]["topics"] == [
        {"id": pinned_topic.id, "title": "Hiring pipeline", "status": "active", "pinned": True},
        {"id": newer_unpinned_topic.id, "title": "Candidate follow-up", "status": "snoozed", "pinned": False},
    ]


def test_email_list_normalizes_legacy_nullable_preview_fields(auth_client, db):
    """Legacy email rows with nullable preview fields should still serialize successfully."""
    user = db.query(models.User).filter(models.User.username == "admin").one()
    account = models.EmailAccount(name="Legacy", email="legacy@test.com", password="hash", user_id=user.id)
    db.add(account)
    db.flush()

    email = models.EmailRecord(
        account_id=account.id,
        message_id=None,
        subject=None,
        sender="legacy@test.com",
        snippet=None,
        body="Recovered preview body.",
        priority=models.Priority.DIGEST,
    )
    db.add(email)
    db.commit()

    response = auth_client.get("/api/emails/?limit=10")

    assert response.status_code == 200
    payload = response.json()[0]
    assert payload["message_id"] == f"<legacy-email-{email.id}@janus.local>"
    assert payload["subject"] == "(no subject)"
    assert payload["snippet"] == "Recovered preview body."


def test_email_list_filters_by_priority_and_reaches_spam(auth_client, db):
    """The inbox leaves SPAM out, but ``priority=spam`` lists it (JAN-219).

    The Emails page's spam filter used to stay empty because the only list
    endpoint never returned SPAM.
    """
    user = db.query(models.User).filter(models.User.username == "admin").one()
    account = models.EmailAccount(name="Filters", email="filters@test.com", password="hash", user_id=user.id)
    db.add(account)
    db.flush()
    for subject, priority in [
        ("Keep", models.Priority.IMPORTANT),
        ("Later", models.Priority.DIGEST),
        ("Junk", models.Priority.SPAM),
    ]:
        db.add(models.EmailRecord(account_id=account.id, subject=subject, sender="a@test.com", priority=priority))
    db.commit()

    inbox = auth_client.get("/api/emails/?limit=10")
    spam = auth_client.get("/api/emails/?limit=10&priority=spam")
    digest = auth_client.get("/api/emails/?limit=10&priority=digest")
    invalid = auth_client.get("/api/emails/?limit=10&priority=urgent")

    assert sorted(email["subject"] for email in inbox.json()) == ["Keep", "Later"]
    assert [email["subject"] for email in spam.json()] == ["Junk"]
    assert [email["subject"] for email in digest.json()] == ["Later"]
    assert invalid.status_code == 422


def test_notification_preferences_get_and_update(auth_client, db):
    """Notification preferences should be created on first read and support partial updates."""
    response = auth_client.get("/api/emails/notifications/preferences")
    assert response.status_code == 200
    assert response.json()["notify_important"] is True
    assert response.json()["notify_digest"] is True
    assert response.json()["daily_digest_enabled"] is False
    assert response.json()["daily_digest_hour"] == 16
    assert response.json()["daily_digest_minute"] == 0
    assert response.json()["digest_lookback_hours"] == 24

    response = auth_client.patch(
        "/api/emails/notifications/preferences",
        json={
            "notify_important": False,
            "daily_digest_enabled": True,
            "daily_digest_hour": 9,
            "daily_digest_minute": 30,
            "digest_lookback_hours": 12,
            "sender_allowlist": "boss@example.com",
            "keyword_blocklist": "newsletter",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["notify_important"] is False
    assert payload["daily_digest_enabled"] is True
    assert payload["daily_digest_hour"] == 9
    assert payload["daily_digest_minute"] == 30
    assert payload["digest_lookback_hours"] == 12
    assert payload["sender_allowlist"] == "boss@example.com"
    assert payload["keyword_blocklist"] == "newsletter"

    preference = db.query(models.NotificationPreference).one()
    assert preference.notify_important is False
    assert preference.daily_digest_enabled is True
    assert preference.daily_digest_hour == 9


def test_email_priority_update(auth_client, db, monkeypatch):
    """Test manual email priority updates and their reflection in the API."""
    monkeypatch.setattr(
        llm_service.LLMService,
        "classify_email",
        lambda self, email_data, context: (
            models.Priority.IMPORTANT,
            "Matched manual correction context.",
            models.ConfidenceLevel.HIGH,
        ),
    )

    # 1. Create a mock email record
    user = db.query(models.User).filter(models.User.username == "admin").one()
    acc = models.EmailAccount(name="Test", email="test@test.com", password="hash", user_id=user.id)
    db.add(acc)
    db.commit()

    email = models.EmailRecord(
        account_id=acc.id,
        message_id="msg123",
        subject="Hello",
        sender="someone@else.com",
        snippet="How are you?",
        priority=models.Priority.DIGEST,
    )
    db.add(email)
    db.commit()

    # 2. Update to IMPORTANT
    # We mock the LLM reasoning by assuming the endpoint works even if Ollama is not running (fallback logic)
    response = auth_client.patch(f"/api/emails/{email.id}/priority?priority=important")
    assert response.status_code == 200
    assert response.json()["priority"] == "important"
    assert response.json()["classification_confidence"] == "low"
    assert "active sender rule" in response.json()["reasoning"]
    db.refresh(email)
    assert email.is_read is False

    rules_response = auth_client.get("/api/emails/rules")
    assert rules_response.status_code == 200
    assert len(rules_response.json()) == 1
    assert rules_response.json()[0]["pattern"] == "^someone@else\\.com$"
    assert rules_response.json()[0]["priority"] == "important"

    assert db.query(models.EmailClarificationRequest).count() == 0
    assert auth_client.get("/api/emails/clarifications").status_code == 404

    # 3. Update to SPAM
    response = auth_client.patch(f"/api/emails/{email.id}/priority?priority=spam")
    assert response.status_code == 200
    assert response.json()["priority"] == "spam"
    db.refresh(email)
    assert email.is_read is True

    response = auth_client.patch(f"/api/emails/{email.id}/priority?priority=ignore")
    assert response.status_code == 200
    assert response.json()["priority"] == "ignore"
    db.refresh(email)
    assert email.is_read is True


def test_email_priority_update_reactivates_matching_inactive_rule(auth_client, db, monkeypatch):
    """Manual corrections should reactivate an inactive matching rule."""
    monkeypatch.setattr(
        llm_service.LLMService,
        "classify_email",
        lambda self, email_data, context: (
            models.Priority.IMPORTANT,
            "Matched manual correction context.",
            models.ConfidenceLevel.HIGH,
        ),
    )

    user = db.query(models.User).filter(models.User.username == "admin").one()
    account = models.EmailAccount(name="Test", email="reactivate@test.com", password="hash", user_id=user.id)
    db.add(account)
    db.flush()

    existing_rule = models.Rule(
        name="Messages from inactive@test.com",
        pattern=r"^inactive@test\.com$",
        field="sender",
        priority=models.Priority.DIGEST,
        is_active=False,
        order=3,
        user_id=user.id,
    )
    email = models.EmailRecord(
        account_id=account.id,
        message_id="inactive-rule-email",
        subject="Needs attention",
        sender="inactive@test.com",
        snippet="Please review this.",
        priority=models.Priority.DIGEST,
    )
    db.add_all([existing_rule, email])
    db.commit()

    response = auth_client.patch(f"/api/emails/{email.id}/priority?priority=important")

    assert response.status_code == 200
    db.refresh(existing_rule)
    db.refresh(email)
    assert email.priority == models.Priority.IMPORTANT
    assert email.reasoning.endswith("active sender rule for similar messages.")
    assert existing_rule.is_active is True
    assert existing_rule.priority == models.Priority.IMPORTANT
    assert db.query(models.Rule).filter(models.Rule.pattern == existing_rule.pattern).count() == 1


def test_email_priority_update_reuses_same_pattern_rule_on_recorrection(auth_client, db, monkeypatch):
    """Re-correcting the same sender should update one stable rule identity."""
    monkeypatch.setattr(
        llm_service.LLMService,
        "classify_email",
        lambda self, email_data, context: (
            models.Priority.IMPORTANT,
            "Matched manual correction context.",
            models.ConfidenceLevel.HIGH,
        ),
    )

    user = db.query(models.User).filter(models.User.username == "admin").one()
    account = models.EmailAccount(name="Test", email="recorrect@test.com", password="hash", user_id=user.id)
    db.add(account)
    db.flush()

    first_email = models.EmailRecord(
        account_id=account.id,
        message_id="same-pattern-first",
        subject="First",
        sender="same@test.com",
        snippet="First snippet",
        priority=models.Priority.DIGEST,
    )
    second_email = models.EmailRecord(
        account_id=account.id,
        message_id="same-pattern-second",
        subject="Second",
        sender="same@test.com",
        snippet="Second snippet",
        priority=models.Priority.IMPORTANT,
    )
    db.add_all([first_email, second_email])
    db.commit()

    first_response = auth_client.patch(f"/api/emails/{first_email.id}/priority?priority=important")
    assert first_response.status_code == 200

    second_response = auth_client.patch(f"/api/emails/{second_email.id}/priority?priority=ignore")
    assert second_response.status_code == 200

    matching_rules = (
        db.query(models.Rule)
        .filter(models.Rule.field == "sender", models.Rule.pattern == r"^same@test\.com$")
        .order_by(models.Rule.id.asc())
        .all()
    )
    assert len(matching_rules) == 1
    assert matching_rules[0].priority == models.Priority.IGNORE
    assert matching_rules[0].is_active is True


# --- Planner Tests ---


def test_planner_integrations(auth_client, db):
    """Test planner account creation and task retrieval via the API."""
    # 1. Add planner account
    p_data = {"name": "My Jira", "provider": "jira", "token": "secret-token", "api_url": "https://jira.example.com"}
    response = auth_client.post("/api/planner/accounts", json=p_data)
    assert response.status_code == 200

    # 2. Get accounts
    response = auth_client.get("/api/planner/accounts")
    assert len(response.json()) == 1

    # 3. Get tasks
    response = auth_client.get("/api/planner/tasks")
    assert response.status_code == 200


def test_desktop_summary_is_compact_user_scoped_and_excludes_imported_tasks(auth_client, db):
    """Desktop aggregates count only the authenticated user's relevant records."""
    current_user = db.query(models.User).filter(models.User.username == "admin").one()
    other_user = models.User(username="other", hashed_password="not-used")
    db.add(other_user)
    db.flush()
    imported_account = models.PlannerAccount(
        name="Desktop imported",
        provider=models.Provider.JIRA,
        token="encrypted-token",
        user_id=current_user.id,
    )
    current_email_account = models.EmailAccount(
        name="Desktop mail",
        email="desktop@example.com",
        imap_server="imap.example.com",
        username="desktop",
        password="encrypted-password",
        user_id=current_user.id,
    )
    other_email_account = models.EmailAccount(
        name="Other mail",
        email="other@example.com",
        imap_server="imap.example.com",
        username="other",
        password="encrypted-password",
        user_id=other_user.id,
    )
    db.add_all([imported_account, current_email_account, other_email_account])
    db.flush()
    db.add_all(
        [
            models.PlannerTask(user_id=current_user.id, title="Open local"),
            models.PlannerTask(user_id=current_user.id, title="Done", status=models.TaskStatus.DONE),
            models.TaskRecord(
                planner_account_id=imported_account.id,
                external_id="imported",
                title="Imported open",
                status=models.TaskStatus.TODO,
            ),
            models.EmailRecord(
                account_id=current_email_account.id,
                message_id="unread",
                subject="Unread",
                sender="sender@example.com",
                snippet="unread",
                is_read=False,
            ),
            models.EmailRecord(
                account_id=current_email_account.id,
                message_id="read",
                subject="Read",
                sender="sender@example.com",
                snippet="read",
                is_read=True,
            ),
            models.EmailRecord(
                account_id=other_email_account.id,
                message_id="other-unread",
                subject="Other",
                sender="other@example.com",
                snippet="other",
                is_read=False,
            ),
        ]
    )
    db.commit()

    response = auth_client.get("/api/desktop/summary")
    assert response.status_code == 200
    assert response.json() == {"open_tasks": 1, "unread_emails": 1}

    without_email = auth_client.get("/api/desktop/summary?include_unread_emails=false")
    assert without_email.status_code == 200
    assert without_email.json() == {"open_tasks": 1, "unread_emails": None}


def test_desktop_summary_requires_authentication(client):
    """Desktop clients must authenticate through the API."""
    response = client.get("/api/desktop/summary")
    assert response.status_code == 401


def test_login_response_exposes_bearer_token_for_cli_clients(client, db):
    """CLI consumers should receive the bearer token needed for header-authenticated API calls."""
    hashed_pw = security.security_service.get_password_hash("password")
    db.add(models.User(username="cli-user", hashed_password=hashed_pw, is_admin=False))
    db.commit()

    response = client.post("/api/auth/login", json={"username": "cli-user", "password": "password"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["csrf_token"]


def test_local_planner_api_round_trip_and_imported_items_are_read_only(auth_client, db):
    """Local planner CRUD should coexist with imported read-only planner context."""
    current_user = db.query(models.User).filter(models.User.username == "admin").one()
    imported_account = models.PlannerAccount(
        name="Imported Calendar",
        provider=models.Provider.GOOGLE_CALENDAR,
        token="encrypted-token",
        user_id=current_user.id,
    )
    db.add(imported_account)
    db.flush()

    # Use "today" (rather than a hardcoded past date) so the schedule widget's
    # same-day filter in get_schedule_widget_data still includes these events
    # no matter when the suite runs.
    today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    start = today + timedelta(hours=9)
    db.add_all(
        [
            models.TaskRecord(
                id=501,
                planner_account_id=imported_account.id,
                external_id="imported-task",
                title="Imported task",
                status=models.TaskStatus.TODO,
            ),
            models.CalendarEvent(
                id=601,
                planner_account_id=imported_account.id,
                external_id="imported-event",
                summary="Imported event",
                start_time=start,
                end_time=start + timedelta(hours=1),
            ),
        ]
    )
    db.commit()

    created_task = auth_client.post(
        "/api/planner/local/tasks",
        json={"title": "Local task", "due_at": (today + timedelta(hours=12)).isoformat().replace("+00:00", "Z")},
    )
    created_event = auth_client.post(
        "/api/planner/local/events",
        json={
            "title": "Local event",
            "start_time": (today + timedelta(hours=13)).isoformat().replace("+00:00", "Z"),
            "end_time": (today + timedelta(hours=14)).isoformat().replace("+00:00", "Z"),
        },
    )

    assert created_task.status_code == 200
    assert created_event.status_code == 200
    assert created_task.json()["source"] == "manual"
    assert created_task.json()["read_only"] is False

    tasks_response = auth_client.get("/api/planner/tasks")
    events_response = auth_client.get(
        "/api/planner/events",
        params={
            "start": today.isoformat().replace("+00:00", "Z"),
            "end": (today + timedelta(days=1)).isoformat().replace("+00:00", "Z"),
        },
    )
    dashboard_tasks_response = auth_client.get("/api/dashboard/tasks")
    dashboard_events_response = auth_client.get("/api/dashboard/events")
    update_local_task = auth_client.patch(
        f"/api/planner/local/tasks/{created_task.json()['id']}",
        json={"status": "Done"},
    )
    update_imported_task = auth_client.patch("/api/planner/local/tasks/501", json={"status": "Done"})

    assert tasks_response.status_code == 200
    assert {task["title"] for task in tasks_response.json()} == {"Local task", "Imported task"}
    assert {task["source"] for task in tasks_response.json()} == {"manual", "imported"}
    assert events_response.status_code == 200
    assert {event["title"] for event in events_response.json()} == {"Local event", "Imported event"}
    assert dashboard_tasks_response.status_code == 200
    assert any(task["read_only"] for task in dashboard_tasks_response.json())
    assert dashboard_events_response.status_code == 200
    assert any(not event["read_only"] for event in dashboard_events_response.json())
    assert update_local_task.status_code == 200
    assert update_local_task.json()["status"] == "Done"
    assert update_imported_task.status_code == 404


def test_planner_suggestion_api_review_flow(auth_client, db):
    """Planner suggestions should support edit, approve, and dismiss review actions."""
    current_user = db.query(models.User).filter(models.User.username == "admin").one()
    source_email = models.EmailRecord(
        account_id=None,
        message_id="planner-review-email",
        sender="manager@example.com",
        subject="Kickoff next week",
        snippet="Please create the kickoff block and finish prep.",
        priority=models.Priority.IMPORTANT,
        received_at=datetime(2026, 6, 28, 8, 0, tzinfo=UTC),
    )
    existing_task = models.PlannerTask(
        user_id=current_user.id,
        title="Draft kickoff plan",
        status=models.TaskStatus.TODO,
        source=models.PlannerItemSource.MANUAL,
    )
    db.add_all([source_email, existing_task])
    db.flush()
    db.add_all(
        [
            models.PlannerSuggestion(
                user_id=current_user.id,
                suggestion_type=models.PlannerSuggestionType.TASK,
                source=models.PlannerItemSource.EMAIL_AGENT,
                title="Prepare kickoff deck",
                source_email_id=source_email.id,
            ),
            models.PlannerSuggestion(
                user_id=current_user.id,
                suggestion_type=models.PlannerSuggestionType.EVENT,
                source=models.PlannerItemSource.EMAIL_AGENT,
                title="Kickoff meeting",
                start_time=datetime(2026, 7, 1, 12, 0, tzinfo=UTC),
                end_time=datetime(2026, 7, 1, 13, 0, tzinfo=UTC),
                source_email_id=source_email.id,
            ),
            models.PlannerSuggestion(
                user_id=current_user.id,
                suggestion_type=models.PlannerSuggestionType.TASK_UPDATE,
                source=models.PlannerItemSource.EMAIL_AGENT,
                title="Draft kickoff plan and agenda",
                task_status=models.TaskStatus.IN_PROGRESS,
                target_task_id=existing_task.id,
                source_email_id=source_email.id,
            ),
            models.PlannerSuggestion(
                user_id=current_user.id,
                suggestion_type=models.PlannerSuggestionType.TASK,
                source=models.PlannerItemSource.EMAIL_AGENT,
                title="Dismiss me",
            ),
        ]
    )
    db.commit()

    suggestions = auth_client.get("/api/planner/suggestions")
    suggestion_ids = {item["title"]: item["id"] for item in suggestions.json()}

    edited = auth_client.patch(
        f"/api/planner/suggestions/{suggestion_ids['Prepare kickoff deck']}",
        json={"title": "Prepare kickoff deck v2"},
    )
    approved_task = auth_client.post(f"/api/planner/suggestions/{suggestion_ids['Prepare kickoff deck']}/approve")
    approved_event = auth_client.post(f"/api/planner/suggestions/{suggestion_ids['Kickoff meeting']}/approve")
    approved_update = auth_client.post(
        f"/api/planner/suggestions/{suggestion_ids['Draft kickoff plan and agenda']}/approve"
    )
    dismissed = auth_client.post(f"/api/planner/suggestions/{suggestion_ids['Dismiss me']}/dismiss")

    assert suggestions.status_code == 200
    assert len(suggestions.json()) == 4
    assert edited.status_code == 200
    assert edited.json()["title"] == "Prepare kickoff deck v2"
    assert approved_task.status_code == 200
    assert approved_task.json()["status"] == "applied"
    assert approved_event.status_code == 200
    assert approved_event.json()["target_event_id"] is not None
    assert approved_update.status_code == 200
    assert dismissed.status_code == 200
    assert dismissed.json()["status"] == "dismissed"

    db.refresh(existing_task)
    assert existing_task.title == "Draft kickoff plan and agenda"
    assert existing_task.status == models.TaskStatus.IN_PROGRESS
    assert db.query(models.PlannerTask).filter(models.PlannerTask.title == "Prepare kickoff deck v2").count() == 1
    assert db.query(models.PlannerEvent).filter(models.PlannerEvent.title == "Kickoff meeting").count() == 1


# --- Settings Tests ---


def test_system_settings(auth_client, db):
    """Test retrieving and updating system-wide LLM settings."""
    # 1. Get settings
    response = auth_client.get("/api/planner/settings")
    assert response.status_code == 200
    assert "initial_sync_email_limit" in response.json()
    assert "llm_provider" in response.json()
    assert "ollama_base_url" in response.json()
    assert response.json()["ollama_api_key_configured"] is False
    assert response.json()["openrouter_api_key_configured"] is False
    assert response.json()["gemini_api_key_configured"] is False

    # 2. Update settings
    response = auth_client.patch(
        "/api/planner/settings",
        json={
            "initial_sync_email_limit": 25,
            "llm_provider": "gemini",
            "gemini_model": "gemini-2.5-pro",
            "gemini_api_key": "secret-gemini-key",
            "ollama_base_url": "https://ollama.com/api",
            "ollama_api_key": "secret-ollama-key",
            "openrouter_base_url": "https://openrouter.ai/api/v1",
            "openrouter_api_key": "secret-openrouter-key",
        },
    )
    assert response.status_code == 200
    assert response.json()["initial_sync_email_limit"] == 25
    assert response.json()["llm_model"] == "gemini-2.5-pro"
    assert response.json()["ollama_base_url"] == "https://ollama.com/api"
    assert response.json()["ollama_api_key_configured"] is True
    assert response.json()["openrouter_base_url"] == "https://openrouter.ai/api/v1"
    assert response.json()["openrouter_api_key_configured"] is True
    assert response.json()["gemini_api_key_configured"] is True


def test_topic_confirmation_threshold_setting_round_trips(auth_client, db):
    """The topic confirmation threshold should be readable, updatable, and bounded."""
    response = auth_client.get("/api/planner/settings")

    assert response.status_code == 200
    assert response.json()["topic_tracking_default_min_confirmation_mentions"] == 2

    response = auth_client.patch(
        "/api/planner/settings",
        json={"topic_tracking_default_min_confirmation_mentions": 3},
    )

    assert response.status_code == 200
    assert response.json()["topic_tracking_default_min_confirmation_mentions"] == 3

    for rejected in (0, 11):
        response = auth_client.patch(
            "/api/planner/settings",
            json={"topic_tracking_default_min_confirmation_mentions": rejected},
        )
        assert response.status_code == 422


def test_system_settings_support_openrouter(auth_client, db):
    """System settings should persist OpenRouter-specific model and secret fields."""
    response = auth_client.patch(
        "/api/planner/settings",
        json={
            "llm_provider": "openrouter",
            "llm_model": "openrouter/openai/gpt-4o-mini",
            "openrouter_model": "openrouter/openai/gpt-4o-mini",
            "openrouter_base_url": "https://openrouter.ai/api/v1",
            "openrouter_api_key": "secret-openrouter-key",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["llm_provider"] == "openrouter"
    assert payload["llm_model"] == "openrouter/openai/gpt-4o-mini"
    assert payload["openrouter_model"] == "openrouter/openai/gpt-4o-mini"
    assert payload["openrouter_base_url"] == "https://openrouter.ai/api/v1"
    assert payload["openrouter_api_key_configured"] is True


def test_mail_ingestion_settings_no_longer_expose_agent_selection(auth_client, db):
    """Classifier guidance comes from the character constitution (JAN-201); no agent picker is exposed."""
    response = auth_client.get("/api/emails/settings")

    assert response.status_code == 200
    assert response.json() == {"initial_sync_email_limit": 100}


def test_mail_ingestion_settings_ignore_legacy_classifier_agent_field(auth_client, db):
    """Old clients sending mail_ingestion_agent_definition_id still save the limit; the id is ignored."""
    response = auth_client.patch(
        "/api/emails/settings",
        json={
            "initial_sync_email_limit": 40,
            "mail_ingestion_agent_definition_id": 1,
        },
    )

    assert response.status_code == 200
    assert response.json() == {"initial_sync_email_limit": 40}

    settings = db.query(models.Settings).first()
    assert settings is not None
    assert settings.initial_sync_email_limit == 40
    assert not hasattr(settings, "mail_ingestion_agent_definition_id")


def test_test_llm_uses_current_payload(auth_client, monkeypatch, testing_session_local):
    """Test LLM connection checks using the current unsaved provider payload."""

    class FakeGemini:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def invoke(self, prompt):
            assert prompt == "ping"
            return "pong"

    monkeypatch.setattr(llm_service, "ChatGoogleGenerativeAI", FakeGemini)
    monkeypatch.setattr(database, "SessionLocal", testing_session_local)

    response = auth_client.post(
        "/api/planner/test-llm",
        json={
            "llm_provider": "gemini",
            "gemini_model": "gemini-2.5-flash",
            "gemini_api_key": "test-key",
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] == "success"


def test_test_llm_uses_current_ollama_cloud_payload(auth_client, monkeypatch, testing_session_local):
    """Tests Ollama Cloud connection checks using the current unsaved provider payload."""
    captured: dict[str, object] = {}

    class FakeOllama:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def invoke(self, prompt):
            assert prompt == "ping"
            return "pong"

    monkeypatch.setattr(llm_service, "ChatOllama", FakeOllama)
    monkeypatch.setattr(database, "SessionLocal", testing_session_local)

    response = auth_client.post(
        "/api/planner/test-llm",
        json={
            "llm_provider": "ollama",
            "ollama_model": "gemma3:27b",
            "ollama_base_url": "https://ollama.com/api",
            "ollama_api_key": "test-ollama-key",
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] == "success"
    assert captured["model"] == "gemma3:27b"
    assert captured["base_url"] == "https://ollama.com"
    assert captured["client_kwargs"] == {"headers": {"Authorization": "Bearer test-ollama-key"}}


def test_system_settings_support_anthropic(auth_client, db):
    """System settings persist Anthropic model/base URL and store the key encrypted (JAN-214)."""
    response = auth_client.get("/api/planner/settings")
    assert response.status_code == 200
    assert response.json()["anthropic_model"] == "claude-opus-5"
    assert response.json()["anthropic_base_url"] is None
    assert response.json()["anthropic_api_key_configured"] is False

    response = auth_client.patch(
        "/api/planner/settings",
        json={
            "llm_provider": "anthropic",
            "anthropic_model": "claude-opus-5",
            "anthropic_api_key": "sk-ant-secret",
            "anthropic_base_url": "https://anthropic-proxy.internal",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["llm_provider"] == "anthropic"
    assert payload["llm_model"] == "claude-opus-5"
    assert payload["anthropic_model"] == "claude-opus-5"
    assert payload["anthropic_base_url"] == "https://anthropic-proxy.internal"
    assert payload["anthropic_api_key_configured"] is True
    assert "anthropic_api_key" not in payload

    db.expire_all()
    stored = db.query(models.Settings).first()
    assert stored.anthropic_api_key != "sk-ant-secret"
    assert security.security_service.decrypt(stored.anthropic_api_key) == "sk-ant-secret"

    # The legacy llm_model field routes to the active (Anthropic) provider column.
    response = auth_client.patch("/api/planner/settings", json={"llm_model": "claude-haiku-4-5"})
    assert response.json()["anthropic_model"] == "claude-haiku-4-5"

    # Clearing the key and base URL is explicit.
    response = auth_client.patch("/api/planner/settings", json={"anthropic_api_key": "", "anthropic_base_url": ""})
    assert response.json()["anthropic_api_key_configured"] is False
    assert response.json()["anthropic_base_url"] is None


def test_test_llm_uses_current_anthropic_payload(auth_client, monkeypatch, testing_session_local):
    """Test-connection builds the Claude client from the unsaved Anthropic draft."""
    captured: dict[str, object] = {}

    class FakeAnthropic:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def invoke(self, prompt):
            assert prompt == "ping"
            return "pong"

    monkeypatch.setattr(llm_service, "ChatAnthropic", FakeAnthropic)
    monkeypatch.setattr(database, "SessionLocal", testing_session_local)

    response = auth_client.post(
        "/api/planner/test-llm",
        json={"llm_provider": "anthropic", "anthropic_model": "claude-sonnet-5", "anthropic_api_key": "draft-key"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "success"
    assert captured["model"] == "claude-sonnet-5"
    assert captured["anthropic_api_key"] == "draft-key"


class _RecordingChatOpenAI:
    """Stands in for ChatOpenAI and records every client the test-connection builds."""

    built: list[dict[str, object]] = []

    def __init__(self, **kwargs):
        type(self).built.append(kwargs)

    def invoke(self, prompt):
        return "pong"


def test_test_llm_never_sends_stored_key_to_a_draft_base_url(auth_client, db, monkeypatch, testing_session_local):
    """A draft base URL cannot be used to exfiltrate the stored key through the connection test (JAN-222)."""
    db.add(
        models.Settings(
            openai_api_key=security.security_service.encrypt("stored-openai"),
            openai_base_url="https://llm.example/v1",
        )
    )
    db.commit()
    _RecordingChatOpenAI.built = []
    monkeypatch.setattr(llm_service, "ChatOpenAI", _RecordingChatOpenAI)
    monkeypatch.setattr(database, "SessionLocal", testing_session_local)

    response = auth_client.post(
        "/api/planner/test-llm",
        json={
            "llm_provider": "openai",
            "openai_model": "gpt-4o-mini",
            "openai_base_url": "https://attacker.example/v1",
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "error"
    assert "stored key is only sent to the base URL it was saved with" in response.json()["message"]
    assert _RecordingChatOpenAI.built == []


def test_test_llm_uses_stored_key_for_its_own_base_url(auth_client, db, monkeypatch, testing_session_local):
    """The stored key still tests its own base URL, and a typed key may test any URL (JAN-222)."""
    db.add(
        models.Settings(
            openai_api_key=security.security_service.encrypt("stored-openai"),
            openai_base_url="https://llm.example/v1",
        )
    )
    db.commit()
    _RecordingChatOpenAI.built = []
    monkeypatch.setattr(llm_service, "ChatOpenAI", _RecordingChatOpenAI)
    monkeypatch.setattr(database, "SessionLocal", testing_session_local)

    stored = auth_client.post(
        "/api/planner/test-llm",
        json={"llm_provider": "openai", "openai_model": "gpt-4o-mini", "openai_base_url": "https://llm.example/v1/"},
    )
    typed = auth_client.post(
        "/api/planner/test-llm",
        json={
            "llm_provider": "openai",
            "openai_model": "gpt-4o-mini",
            "openai_base_url": "https://other.example/v1",
            "openai_api_key": "typed-key",
        },
    )

    assert stored.json()["status"] == "success"
    assert typed.json()["status"] == "success"
    assert [(client["api_key"], client["base_url"]) for client in _RecordingChatOpenAI.built] == [
        ("stored-openai", "https://llm.example/v1/"),
        ("typed-key", "https://other.example/v1"),
    ]


def test_test_llm_requires_admin(client, db):
    """Like saving them, testing draft AI settings with the stored keys is admin only (JAN-222)."""
    db.add(
        models.User(
            username="member", hashed_password=security.security_service.get_password_hash("member"), is_admin=False
        )
    )
    db.commit()
    login = client.post("/api/auth/login", json={"username": "member", "password": "member"})
    client.headers[security.CSRF_HEADER_NAME] = login.json()["csrf_token"]

    response = client.post("/api/planner/test-llm", json={"llm_provider": "openai"})

    assert response.status_code == 403


@pytest.fixture
def mock_model_catalog_http(monkeypatch):
    """Routes the model catalog's outbound httpx calls through a MockTransport handler."""
    import httpx

    from janus.services import llm_model_catalog

    llm_model_catalog.clear_cache()
    state: dict[str, object] = {"requests": [], "response": httpx.Response(200, json={"data": []})}

    def handler(request):
        state["requests"].append(request)
        response = state["response"]
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(llm_model_catalog, "_new_client", lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    yield state
    llm_model_catalog.clear_cache()


def test_llm_models_lists_anthropic_models_with_draft_key(auth_client, mock_model_catalog_http):
    """The dropdown endpoint returns normalized models using the unsaved draft key (JAN-215)."""
    import httpx

    mock_model_catalog_http["response"] = httpx.Response(
        200, json={"data": [{"id": "claude-sonnet-5", "display_name": "Claude Sonnet 5"}]}
    )

    response = auth_client.post("/api/planner/llm-models", json={"provider": "anthropic", "api_key": "draft-key"})

    assert response.status_code == 200
    assert response.json() == {"models": [{"id": "claude-sonnet-5", "label": "Claude Sonnet 5"}], "error": None}
    request = mock_model_catalog_http["requests"][0]
    assert request.headers["x-api-key"] == "draft-key"


def test_llm_models_uses_stored_key_when_no_draft_key(auth_client, db, mock_model_catalog_http):
    """Without a draft key the stored (decrypted) key is used against the stored base URL."""
    import httpx

    db.add(models.Settings(openai_api_key=security.security_service.encrypt("stored-openai")))
    db.commit()
    mock_model_catalog_http["response"] = httpx.Response(200, json={"data": [{"id": "gpt-4o-mini"}]})

    response = auth_client.post("/api/planner/llm-models", json={"provider": "openai", "base_url": ""})

    assert response.status_code == 200
    assert response.json()["models"] == [{"id": "gpt-4o-mini", "label": "gpt-4o-mini"}]
    assert mock_model_catalog_http["requests"][0].headers["authorization"] == "Bearer stored-openai"


def test_llm_models_never_sends_stored_key_to_a_draft_base_url(auth_client, db, mock_model_catalog_http):
    """A draft base URL cannot be used to exfiltrate the stored key."""
    db.add(models.Settings(openai_api_key=security.security_service.encrypt("stored-openai")))
    db.commit()

    response = auth_client.post(
        "/api/planner/llm-models", json={"provider": "openai", "base_url": "https://attacker.example/v1"}
    )

    assert response.status_code == 200
    assert response.json()["models"] == []
    assert "custom base URL" in response.json()["error"]
    assert mock_model_catalog_http["requests"] == []


@pytest.mark.parametrize(
    ("provider", "draft"),
    [
        ("openai", {"api_key": "k"}),
        ("openrouter", {}),
        ("gemini", {"api_key": "k"}),
        ("anthropic", {"api_key": "k"}),
        ("ollama", {"base_url": "http://ollama:11434"}),
    ],
)
def test_llm_models_provider_failures_return_200_with_error(auth_client, mock_model_catalog_http, provider, draft):
    """Provider failures degrade to an empty list plus a short error, never a 5xx."""
    import httpx

    mock_model_catalog_http["response"] = httpx.Response(502, text="bad gateway")

    response = auth_client.post("/api/planner/llm-models", json={"provider": provider, **draft})

    assert response.status_code == 200
    assert response.json()["models"] == []
    assert "HTTP 502" in response.json()["error"]


def test_llm_models_missing_key_returns_error_without_network(auth_client, mock_model_catalog_http):
    """Key-required providers report a missing key instead of calling out."""
    response = auth_client.post("/api/planner/llm-models", json={"provider": "gemini"})

    assert response.status_code == 200
    assert response.json() == {"models": [], "error": "Enter a Gemini API key to load the model list."}
    assert mock_model_catalog_http["requests"] == []


def test_llm_models_requires_admin(client, db, mock_model_catalog_http):
    """Only administrators may trigger outbound model lookups with stored credentials."""
    db.add(
        models.User(
            username="member",
            hashed_password=security.security_service.get_password_hash("password"),
            is_admin=False,
        )
    )
    db.commit()
    login = client.post("/api/auth/login", json={"username": "member", "password": "password"})
    client.headers[security.CSRF_HEADER_NAME] = login.json()["csrf_token"]

    response = client.post("/api/planner/llm-models", json={"provider": "openrouter"})

    assert response.status_code == 403
    assert mock_model_catalog_http["requests"] == []


def test_linear_settings_get_update_and_clear(auth_client, db):
    """Linear settings can be retrieved, updated, and intentionally cleared."""
    response = auth_client.get("/api/linear/settings")
    assert response.status_code == 200
    assert response.json() == {"api_key_configured": False, "default_team_id": None}

    response = auth_client.patch(
        "/api/linear/settings",
        json={"api_key": "lin_api_test_key", "default_team_id": "team_123"},
    )
    assert response.status_code == 200
    assert response.json() == {
        "api_key_configured": True,
        "default_team_id": "team_123",
    }

    settings = db.query(models.Settings).first()
    assert settings is not None
    assert settings.linear_api_key is not None
    assert settings.linear_api_key != "lin_api_test_key"
    assert security.security_service.decrypt(settings.linear_api_key) == "lin_api_test_key"
    assert settings.linear_default_team_id == "team_123"

    response = auth_client.patch(
        "/api/linear/settings",
        json={"api_key": "", "default_team_id": ""},
    )
    assert response.status_code == 200
    assert response.json() == {"api_key_configured": False, "default_team_id": None}

    response = auth_client.get("/api/linear/settings")
    assert response.status_code == 200
    assert response.json() == {"api_key_configured": False, "default_team_id": None}


def test_linear_connection_test_uses_stored_key(auth_client, db, monkeypatch):
    """Connection testing decrypts and uses the stored Linear API key."""
    settings = models.Settings(
        linear_api_key=security.security_service.encrypt("lin_api_stored_key"),
    )
    db.add(settings)
    db.commit()

    captured: dict[str, str] = {}

    class FakeLinearService:
        def __init__(self, api_key: str):
            captured["api_key"] = api_key

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            return None

        def test_connection(self):
            return True, "Connected as Janus Admin"

    monkeypatch.setattr(linear_api, "LinearService", FakeLinearService)

    response = auth_client.post("/api/linear/test")
    assert response.status_code == 200
    assert response.json() == {"status": "success", "message": "Connected as Janus Admin"}
    assert captured["api_key"] == "lin_api_stored_key"


def test_linear_settings_strip_pasted_whitespace(auth_client, db):
    """A key or team pasted with surrounding whitespace is stored trimmed (Linear rejects the padded key)."""
    response = auth_client.patch(
        "/api/linear/settings",
        json={"api_key": "  lin_api_padded\n", "default_team_id": " ENG "},
    )
    assert response.status_code == 200
    assert response.json() == {"api_key_configured": True, "default_team_id": "ENG"}

    settings = db.query(models.Settings).first()
    db.refresh(settings)
    assert security.security_service.decrypt(settings.linear_api_key) == "lin_api_padded"

    response = auth_client.patch("/api/linear/settings", json={"api_key": "   "})
    assert response.json()["api_key_configured"] is False


def test_linear_connection_test_end_to_end_reports_linear_error(auth_client, db, monkeypatch):
    """The /test route surfaces Linear's own error text from a real (mocked-transport) LinearService."""
    import httpx

    from janus.services.linear_service import LinearService

    settings = models.Settings(linear_api_key=security.security_service.encrypt("lin_api_bad"))
    db.add(settings)
    db.commit()

    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            400, json={"errors": [{"message": "Authentication required, not authenticated"}]}
        )
    )
    monkeypatch.setattr(linear_api, "LinearService", lambda api_key: LinearService(api_key, transport=transport))

    response = auth_client.post("/api/linear/test")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "error"
    assert "Authentication required, not authenticated" in body["message"]


def test_linear_connection_test_requires_stored_key(auth_client):
    """Connection testing returns a helpful error when no key is configured."""
    response = auth_client.post("/api/linear/test")
    assert response.status_code == 400
    assert response.json()["detail"] == (
        "Linear API key is not configured. Add it in Settings → Connections → Developer."
    )


def test_create_linear_issue_uses_default_team(auth_client, db, monkeypatch):
    """Issue creation falls back to the stored default team."""
    settings = models.Settings(
        linear_api_key=security.security_service.encrypt("lin_api_stored_key"),
        linear_default_team_id="team_123",
    )
    db.add(settings)
    db.commit()

    captured: dict[str, object] = {}

    class FakeLinearService:
        def __init__(self, api_key: str):
            captured["api_key"] = api_key

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            return None

        def create_issue(self, team_id: str, title: str, description: str):
            captured["team_id"] = team_id
            captured["title"] = title
            captured["description"] = description
            return {
                "identifier": "JAN-49",
                "url": "https://linear.app/example/issue/JAN-49",
                "title": title,
            }

    monkeypatch.setattr(linear_api, "LinearService", FakeLinearService)

    response = auth_client.post(
        "/api/linear/issues",
        json={"title": "Add docs", "description": "Please add docs"},
    )
    assert response.status_code == 201
    assert response.json() == {
        "identifier": "JAN-49",
        "url": "https://linear.app/example/issue/JAN-49",
        "title": "Add docs",
    }
    assert captured == {
        "api_key": "lin_api_stored_key",
        "team_id": "team_123",
        "title": "Add docs",
        "description": "Please add docs",
    }


def test_create_linear_issue_requires_team(auth_client, db):
    """Issue creation fails when neither the payload nor settings provide a team."""
    settings = models.Settings(
        linear_api_key=security.security_service.encrypt("lin_api_stored_key"),
    )
    db.add(settings)
    db.commit()

    response = auth_client.post(
        "/api/linear/issues",
        json={"title": "Add docs", "description": "Please add docs"},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == ("No team specified and no default team configured in Linear settings.")


def test_create_linear_issue_surfaces_service_failures(auth_client, db, monkeypatch):
    """Issue creation returns a 422 when the Linear API rejects the request."""
    settings = models.Settings(
        linear_api_key=security.security_service.encrypt("lin_api_stored_key"),
        linear_default_team_id="team_123",
    )
    db.add(settings)
    db.commit()

    class FakeLinearService:
        def __init__(self, api_key: str):
            self.api_key = api_key

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            return None

        def create_issue(self, team_id: str, title: str, description: str):
            raise ValueError("Linear API error: Validation error.")

    monkeypatch.setattr(linear_api, "LinearService", FakeLinearService)

    response = auth_client.post(
        "/api/linear/issues",
        json={"title": "Add docs", "description": "Please add docs"},
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "Linear API error: Validation error."


def test_github_settings_get_update_and_clear(auth_client, db):
    """GitHub settings can be retrieved, updated, and intentionally cleared."""
    response = auth_client.get("/api/github/settings")
    assert response.status_code == 200
    assert response.json() == {"token_configured": False, "default_repo": None}

    response = auth_client.patch(
        "/api/github/settings",
        json={"token": "ghp_test_token", "default_repo": "thomasniebler/janus"},
    )
    assert response.status_code == 200
    assert response.json() == {
        "token_configured": True,
        "default_repo": "thomasniebler/janus",
    }

    settings = db.query(models.Settings).first()
    assert settings is not None
    assert settings.github_token is not None
    assert settings.github_token != "ghp_test_token"
    assert security.security_service.decrypt(settings.github_token) == "ghp_test_token"
    assert settings.github_default_repo == "thomasniebler/janus"

    response = auth_client.patch(
        "/api/github/settings",
        json={"token": "", "default_repo": ""},
    )
    assert response.status_code == 200
    assert response.json() == {"token_configured": False, "default_repo": None}


@pytest.mark.parametrize(
    ("path", "payload", "column"),
    [
        ("/api/github/settings", {"token": "ghp_member"}, "github_token"),
        ("/api/linear/settings", {"api_key": "lin_api_member"}, "linear_api_key"),
        ("/api/emails/settings", {"initial_sync_email_limit": 7}, "initial_sync_email_limit"),
    ],
)
def test_integration_settings_updates_require_admin(client, db, path, payload, column):
    """Instance-wide settings (GitHub token, Linear key, mail sync limit) are admin-only to change (JAN-222)."""
    db.add(models.Settings())
    db.add(
        models.User(
            username="member", hashed_password=security.security_service.get_password_hash("member"), is_admin=False
        )
    )
    db.commit()
    login = client.post("/api/auth/login", json={"username": "member", "password": "member"})
    client.headers[security.CSRF_HEADER_NAME] = login.json()["csrf_token"]

    assert client.get(path).status_code == 200
    before = getattr(db.query(models.Settings).first(), column)
    assert client.patch(path, json=payload).status_code == 403
    db.expire_all()
    assert getattr(db.query(models.Settings).first(), column) == before
    assert before != next(iter(payload.values()))


def test_github_connection_test_uses_stored_token(auth_client, db, monkeypatch):
    """Connection testing decrypts and uses the stored GitHub token."""
    db.add(models.Settings(github_token=security.security_service.encrypt("ghp_stored_token")))
    db.commit()

    captured: dict[str, str] = {}

    class FakeGitHubService:
        def __init__(self, token: str):
            captured["token"] = token

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            return None

        def test_connection(self):
            return True, "Connected as thomasniebler"

    monkeypatch.setattr(github_api, "GitHubService", FakeGitHubService)

    response = auth_client.post("/api/github/test")
    assert response.status_code == 200
    assert response.json() == {"status": "success", "message": "Connected as thomasniebler"}
    assert captured["token"] == "ghp_stored_token"


def test_github_connection_test_requires_stored_token(auth_client):
    """Connection testing returns a helpful error when no token is configured."""
    response = auth_client.post("/api/github/test")
    assert response.status_code == 400
    assert response.json()["detail"] == (
        "GitHub token is not configured. Add it in Settings → Connections → Developer."
    )


def test_create_github_issue_uses_default_repo(auth_client, db, monkeypatch):
    """Issue creation falls back to the stored default repository."""
    db.add(
        models.Settings(
            github_token=security.security_service.encrypt("ghp_stored_token"),
            github_default_repo="thomasniebler/janus",
        )
    )
    db.commit()

    captured: dict[str, object] = {}

    class FakeGitHubService:
        def __init__(self, token: str):
            captured["token"] = token

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            return None

        def create_issue(self, *, repo: str, title: str, body: str, labels: list[str] | None = None):
            captured["repo"] = repo
            captured["title"] = title
            captured["body"] = body
            captured["labels"] = labels
            return {
                "number": 185,
                "title": title,
                "url": "https://github.com/thomasniebler/janus/issues/185",
                "repo": repo,
            }

    monkeypatch.setattr(github_api, "GitHubService", FakeGitHubService)

    response = auth_client.post(
        "/api/github/issues",
        json={"title": "Track toolbox rollout", "body": "Ship the new toolboxes", "labels": ["automation"]},
    )
    assert response.status_code == 201
    assert response.json() == {
        "number": 185,
        "title": "Track toolbox rollout",
        "url": "https://github.com/thomasniebler/janus/issues/185",
        "repo": "thomasniebler/janus",
    }
    assert captured == {
        "token": "ghp_stored_token",
        "repo": "thomasniebler/janus",
        "title": "Track toolbox rollout",
        "body": "Ship the new toolboxes",
        "labels": ["automation"],
    }


def test_create_github_issue_requires_repo(auth_client, db):
    """Issue creation fails when neither the payload nor settings provide a repository."""
    db.add(models.Settings(github_token=security.security_service.encrypt("ghp_stored_token")))
    db.commit()

    response = auth_client.post(
        "/api/github/issues",
        json={"title": "Track toolbox rollout", "body": "Ship the new toolboxes"},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == (
        "No repository specified and no default repository configured in GitHub settings."
    )


def test_create_github_issue_surfaces_service_failures(auth_client, db, monkeypatch):
    """Issue creation returns a 422 when the GitHub API rejects the request."""
    db.add(
        models.Settings(
            github_token=security.security_service.encrypt("ghp_stored_token"),
            github_default_repo="thomasniebler/janus",
        )
    )
    db.commit()

    class FakeGitHubService:
        def __init__(self, token: str):
            self.token = token

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            return None

        def create_issue(self, *, repo: str, title: str, body: str, labels: list[str] | None = None):
            raise ValueError("Resource not accessible by integration")

    monkeypatch.setattr(github_api, "GitHubService", FakeGitHubService)

    response = auth_client.post(
        "/api/github/issues",
        json={"title": "Track toolbox rollout", "body": "Ship the new toolboxes"},
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "Resource not accessible by integration"


def test_status_health_endpoint_includes_version(client):
    """The unauthenticated health check reports the running janus version."""
    response = client.get("/api/status/health")
    assert response.status_code == 200

    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["version"]


def test_status_summary_endpoint(auth_client, db, monkeypatch):
    """Returns normalized service summaries and recent status incidents."""
    now = datetime.now(UTC)
    user = db.query(models.User).filter(models.User.username == "admin").one()

    email_account = models.EmailAccount(
        user_id=user.id,
        name="Work Mail",
        email="work@example.com",
        username="work@example.com",
        password=security.security_service.encrypt("secret"),
        is_active=True,
        monitor_status=models.MonitorStatus.RUNNING,
        last_heartbeat=now,
    )
    planner_account = models.PlannerAccount(
        user_id=user.id,
        name="Engineering Jira",
        provider=models.Provider.JIRA,
        token="secret-token",
        is_active=True,
        last_sync_at=now,
    )
    settings = models.Settings(
        llm_provider=models.LLMProvider.OLLAMA,
        llm_status="operational",
        llm_status_checked_at=now,
        llm_status_message="Successfully connected to LLM provider.",
    )
    incident = models.Event(
        event_type=models.EventType.ERROR,
        source="SystemStatus",
        message="Mail Sync outage for Work Mail: IMAP connection failed",
        details=json.dumps(
            {
                "service_id": "mail-sync",
                "service_name": "Mail Sync",
                "component_name": "Work Mail",
                "new_status": "outage",
            }
        ),
    )

    db.add_all([email_account, planner_account, settings, incident])
    db.commit()
    monkeypatch.setattr(status_service.worker_manager, "status", lambda: {"imap:work@example.com": {"alive": True}})

    response = auth_client.get("/api/status/summary")
    assert response.status_code == 200

    payload = response.json()
    assert payload["overall_status"] == "operational"
    assert [service["id"] for service in payload["services"]] == [
        "mail-sync",
        "planner-sync",
        "whatsapp-sync",
        "llm",
        "knowledge",
    ]
    assert payload["services"][4]["components"][0]["status"] == "operational"
    assert payload["services"][0]["components"][0]["status"] == "operational"
    assert payload["services"][1]["components"][0]["status"] == "operational"
    assert payload["services"][3]["components"][0]["status"] == "operational"
    assert payload["recent_incidents"][0]["status"] == "outage"
    assert payload["recent_incidents"][0]["component_name"] == "Work Mail"


# --- Agent Definition Tests ---


def test_agent_memory_api(auth_client, db):
    """Users can create and search durable agent memory facts."""
    response = auth_client.get("/api/memory/compact/latest")
    assert response.status_code == 200
    assert response.json() is None

    response = auth_client.post(
        "/api/memory/",
        json={"summary": "I prefer afternoon meetings.", "detail": "Mornings are best for focused work."},
    )
    assert response.status_code == 201
    fact = response.json()
    assert fact["summary"] == "I prefer afternoon meetings."
    assert fact["source"] == "user"
    assert fact["status"] == "active"
    assert "source_agent_definition_id" not in fact

    response = auth_client.get("/api/memory/?query=afternoon")
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["id"] == fact["id"]

    response = auth_client.delete(f"/api/memory/{fact['id']}")
    assert response.status_code == 204

    response = auth_client.get("/api/memory/")
    assert response.status_code == 200
    assert response.json() == []


def test_agent_memory_delete_missing_fact_returns_404(auth_client, db):
    """Deleting a nonexistent (or another user's) memory fact returns 404."""
    response = auth_client.delete("/api/memory/999999")
    assert response.status_code == 404


def test_agent_memory_delete_unlinks_compacted_source_facts(auth_client, db):
    """Deleting a merged fact clears compacted_into_id on the facts merged into it."""
    user = db.query(models.User).filter(models.User.username == "admin").one()
    merged_fact = models.AgentMemoryFact(
        user_id=user.id,
        summary="Prefers concise email summaries over full transcripts.",
        source=models.AgentMemorySource.COMPACTION,
        status=models.AgentMemoryStatus.ACTIVE,
    )
    db.add(merged_fact)
    db.commit()
    db.refresh(merged_fact)

    source_fact = models.AgentMemoryFact(
        user_id=user.id,
        summary="Old note about a since-completed project.",
        source=models.AgentMemorySource.USER,
        status=models.AgentMemoryStatus.ARCHIVED,
        compacted_into_id=merged_fact.id,
    )
    db.add(source_fact)
    db.commit()
    db.refresh(source_fact)
    merged_fact_id = merged_fact.id
    source_fact_id = source_fact.id

    response = auth_client.delete(f"/api/memory/{merged_fact_id}")
    assert response.status_code == 204
    db.expire_all()

    assert db.query(models.AgentMemoryFact).filter(models.AgentMemoryFact.id == merged_fact_id).first() is None
    remaining_source_fact = db.query(models.AgentMemoryFact).filter(models.AgentMemoryFact.id == source_fact_id).one()
    assert remaining_source_fact.compacted_into_id is None


def test_agent_memory_compaction_start(auth_client, db, monkeypatch):
    """Compaction endpoint returns the queued or running compaction run."""

    def fake_start_compaction(self, user_id):
        run = models.AgentMemoryCompactionRun(
            user_id=user_id,
            status=models.AgentMemoryCompactionStatus.QUEUED,
            created_at=time_utils.utcnow(),
        )
        self.db.add(run)
        self.db.commit()
        self.db.refresh(run)
        return run, True

    monkeypatch.setattr(memory_api.MemoryService, "start_compaction", fake_start_compaction)

    response = auth_client.post("/api/memory/compact")
    assert response.status_code == 202
    assert response.json()["status"] == "queued"


def test_topic_api_list_detail_update_and_suggestions(auth_client, db):
    """Tracked topics can be listed, updated, and have suggestion review actions."""
    user = db.query(models.User).filter(models.User.username == "admin").one()
    topic = models.TrackedTopic(
        user_id=user.id,
        slug="hiring-pipeline",
        title="Hiring pipeline",
        summary="Interview coordination and offer planning.",
        status=models.TopicStatus.ACTIVE,
        origin=models.TopicOrigin.AUTO,
        confidence=models.ConfidenceLevel.HIGH,
    )
    db.add(topic)
    db.commit()
    db.refresh(topic)

    db.add(
        models.TopicEvidence(
            topic_id=topic.id,
            source_type=models.TopicEvidenceSource.EMAIL,
            source_id=1,
            excerpt="Candidate feedback is due tomorrow.",
            evidence_role=models.TopicEvidenceRole.TRIGGER,
        )
    )
    db.commit()

    response = auth_client.get("/api/topics/")
    assert response.status_code == 200
    assert response.json()[0]["title"] == "Hiring pipeline"
    assert response.json()[0]["evidence_count"] == 1
    assert response.json()[0]["marker_keywords"] == []

    response = auth_client.get(f"/api/topics/{topic.id}")
    assert response.status_code == 200
    assert response.json()["evidence"][0]["source_type"] == "email"
    assert response.json()["marker_keywords"] == []

    response = auth_client.patch(f"/api/topics/{topic.id}", json={"pinned": True, "status": "resolved"})
    assert response.status_code == 200
    assert response.json()["pinned"] is True
    assert response.json()["status"] == "resolved"


def test_topic_delete_removes_topic_and_evidence(auth_client, db):
    """Deleting a topic removes it and its evidence, and unlinks (not deletes) suggestions."""
    user = db.query(models.User).filter(models.User.username == "admin").one()
    topic = models.TrackedTopic(
        user_id=user.id,
        slug="hiring-pipeline",
        title="Hiring pipeline",
        summary="Interview coordination and offer planning.",
        status=models.TopicStatus.ACTIVE,
        origin=models.TopicOrigin.AUTO,
        confidence=models.ConfidenceLevel.HIGH,
    )
    db.add(topic)
    db.commit()
    db.refresh(topic)

    db.add(
        models.TopicEvidence(
            topic_id=topic.id,
            source_type=models.TopicEvidenceSource.EMAIL,
            source_id=1,
            excerpt="Candidate feedback is due tomorrow.",
            evidence_role=models.TopicEvidenceRole.TRIGGER,
        )
    )
    suggestion = models.TopicSuggestion(
        user_id=user.id,
        topic_id=topic.id,
        suggestion_type=models.TopicSuggestionType.PIN,
        status=models.TopicSuggestionStatus.PENDING,
        payload_json='{"pinned": true}',
        reason="This topic keeps resurfacing in important emails.",
        confidence=models.ConfidenceLevel.HIGH,
    )
    db.add(suggestion)
    planner_suggestion = models.PlannerSuggestion(
        user_id=user.id,
        suggestion_type=models.PlannerSuggestionType.TASK,
        status=models.PlannerSuggestionStatus.PENDING,
        source_topic_id=topic.id,
        title="Follow up on hiring pipeline",
    )
    db.add(planner_suggestion)
    db.commit()
    db.refresh(suggestion)
    db.refresh(planner_suggestion)
    topic_id = topic.id
    suggestion_id = suggestion.id
    planner_suggestion_id = planner_suggestion.id

    response = auth_client.delete(f"/api/topics/{topic_id}")
    assert response.status_code == 204
    db.expire_all()

    assert db.query(models.TrackedTopic).filter(models.TrackedTopic.id == topic_id).first() is None
    assert db.query(models.TopicEvidence).filter(models.TopicEvidence.topic_id == topic_id).first() is None

    remaining_suggestion = db.query(models.TopicSuggestion).filter(models.TopicSuggestion.id == suggestion_id).one()
    assert remaining_suggestion.topic_id is None

    remaining_planner_suggestion = (
        db.query(models.PlannerSuggestion).filter(models.PlannerSuggestion.id == planner_suggestion_id).one()
    )
    assert remaining_planner_suggestion.source_topic_id is None

    response = auth_client.delete(f"/api/topics/{topic_id}")
    assert response.status_code == 404


def test_topic_create_and_patch_marker_keywords(auth_client, db):
    """Manual topics should be creatable with marker keywords and persist later edits."""
    response = auth_client.post(
        "/api/topics/",
        json={
            "title": "Home blog",
            "summary": "Posts, deployments, and GitHub workflow mail for the home blog.",
            "marker_keywords": ["example.com", "home blog", "GitHub workflow"],
        },
    )

    assert response.status_code == 201
    created = response.json()
    assert created["origin"] == "manual"
    assert created["user_confirmed"] is True
    assert created["marker_keywords"] == ["example.com", "home blog", "GitHub workflow"]

    topic_id = created["id"]
    response = auth_client.patch(
        f"/api/topics/{topic_id}",
        json={
            "summary": "Posts, deployments, and content workflow messages.",
            "marker_keywords": ["example.com", "deploy preview"],
        },
    )

    assert response.status_code == 200
    assert response.json()["summary"] == "Posts, deployments, and content workflow messages."
    assert response.json()["marker_keywords"] == ["example.com", "deploy preview"]

    topic = db.query(models.TrackedTopic).filter(models.TrackedTopic.id == topic_id).one()
    assert json.loads(topic.marker_keywords) == ["example.com", "deploy preview"]


def test_topic_patch_marker_keywords_without_summary_uses_existing_manual_summary(auth_client, db):
    """PATCH marker_keywords-only should reuse the stored manual summary instead of the unset sentinel."""
    user = db.query(models.User).filter(models.User.username == "admin").one()
    topic = models.TrackedTopic(
        user_id=user.id,
        slug="home-blog",
        title="Home blog",
        summary="Posts, deployments, and GitHub workflow mail for the home blog.",
        marker_keywords=json.dumps(["example.com"]),
        status=models.TopicStatus.ACTIVE,
        origin=models.TopicOrigin.MANUAL,
        user_confirmed=True,
    )
    db.add(topic)
    db.commit()
    db.refresh(topic)

    response = auth_client.patch(
        f"/api/topics/{topic.id}",
        json={"marker_keywords": ["example.com", "deploy preview"]},
    )

    assert response.status_code == 200
    assert response.json()["summary"] == "Posts, deployments, and GitHub workflow mail for the home blog."
    assert response.json()["marker_keywords"] == ["example.com", "deploy preview"]


def test_topic_patch_marker_keywords_without_any_manual_summary_is_rejected(auth_client, db):
    """PATCH marker_keywords-only should still enforce the manual-topic description requirement."""
    user = db.query(models.User).filter(models.User.username == "admin").one()
    topic = models.TrackedTopic(
        user_id=user.id,
        slug="legacy-home-blog",
        title="Legacy home blog",
        summary=None,
        marker_keywords=json.dumps(["example.com"]),
        status=models.TopicStatus.ACTIVE,
        origin=models.TopicOrigin.MANUAL,
        user_confirmed=True,
    )
    db.add(topic)
    db.commit()
    db.refresh(topic)

    response = auth_client.patch(
        f"/api/topics/{topic.id}",
        json={"marker_keywords": ["deploy preview"]},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "manual topics require a description"


def test_topic_patch_can_clear_summary_without_detail_fetch(auth_client, db, monkeypatch):
    """PATCH summary=null should clear the summary without reloading topic detail evidence."""
    user = db.query(models.User).filter(models.User.username == "admin").one()
    topic = models.TrackedTopic(
        user_id=user.id,
        slug="weekly-planning",
        title="Weekly planning",
        summary="Agenda and preparation notes.",
        status=models.TopicStatus.ACTIVE,
        origin=models.TopicOrigin.AUTO,
        confidence=models.ConfidenceLevel.HIGH,
    )
    db.add(topic)
    db.commit()
    db.refresh(topic)

    db.add(
        models.TopicEvidence(
            topic_id=topic.id,
            source_type=models.TopicEvidenceSource.EMAIL,
            source_id=1,
            excerpt="Planning doc is ready.",
            evidence_role=models.TopicEvidenceRole.TRIGGER,
        )
    )
    db.commit()

    def fail_get_topic_detail(*args, **kwargs):
        raise AssertionError("PATCH should not fetch topic detail")

    monkeypatch.setattr("janus.services.topic_service.TopicService.get_topic_detail", fail_get_topic_detail)

    response = auth_client.patch(f"/api/topics/{topic.id}", json={"summary": None})

    assert response.status_code == 200
    assert response.json()["summary"] is None
    assert response.json()["evidence_count"] == 1
    assert "evidence" not in response.json()
    db.refresh(topic)
    assert topic.summary is None


def test_topic_patch_leaves_summary_untouched_when_omitted(auth_client, db):
    """PATCH requests that omit summary should leave the stored summary unchanged."""
    user = db.query(models.User).filter(models.User.username == "admin").one()
    topic = models.TrackedTopic(
        user_id=user.id,
        slug="interview-loop",
        title="Interview loop",
        summary="Candidate scheduling and feedback.",
        status=models.TopicStatus.ACTIVE,
        origin=models.TopicOrigin.AUTO,
        confidence=models.ConfidenceLevel.HIGH,
    )
    db.add(topic)
    db.commit()
    db.refresh(topic)

    response = auth_client.patch(f"/api/topics/{topic.id}", json={"pinned": True})

    assert response.status_code == 200
    assert response.json()["pinned"] is True
    assert response.json()["summary"] == "Candidate scheduling and feedback."
    db.refresh(topic)
    assert topic.summary == "Candidate scheduling and feedback."


def test_pytest_environment_sets_test_database_url():
    """The pytest harness configures the test environment to use SQLite."""
    assert os.environ["JANUS_SECRET_KEY"] == "test-secret-key-for-testing-only-32ch"
    assert os.environ["DATABASE_URL"] == "sqlite://"


def test_tasks_widget_endpoint_serializes_nested_children(auth_client, db):
    """JAN-317: /api/dashboard/tasks keeps the nested children in its response model."""
    parent = auth_client.post("/api/planner/local/tasks", json={"title": "Home"})
    assert parent.status_code == 200
    child = auth_client.post(
        "/api/planner/local/tasks", json={"title": "Fix sink", "parent_task_id": parent.json()["id"]}
    )
    assert child.status_code == 200

    response = auth_client.get("/api/dashboard/tasks")
    aggregate = auth_client.get("/api/dashboard/")

    assert response.status_code == 200
    roots = response.json()
    assert [task["title"] for task in roots] == ["Home"]
    assert [task["title"] for task in roots[0]["children"]] == ["Fix sink"]
    assert roots[0]["open_child_count"] == 1
    assert aggregate.status_code == 200
    assert [task["title"] for task in aggregate.json()["tasks"][0]["children"]] == ["Fix sink"]


def test_local_planner_delete_endpoints(auth_client, db):
    """DELETE removes the caller's own local task or event; imported or missing ids answer 404 (JAN-221)."""
    task = auth_client.post("/api/planner/local/tasks", json={"title": "Throwaway task"}).json()
    event = auth_client.post(
        "/api/planner/local/events",
        json={"title": "Throwaway event", "start_time": "2026-10-11T09:00:00Z", "end_time": "2026-10-11T09:30:00Z"},
    ).json()

    assert auth_client.delete(f"/api/planner/local/tasks/{task['id']}").status_code == 204
    assert auth_client.delete(f"/api/planner/local/events/{event['id']}").status_code == 204
    assert auth_client.delete(f"/api/planner/local/tasks/{task['id']}").status_code == 404
    assert auth_client.delete(f"/api/planner/local/events/{event['id']}").status_code == 404
    assert auth_client.delete("/api/planner/local/suggestions/1").status_code == 422
    assert [t["title"] for t in auth_client.get("/api/planner/local/tasks").json()] == []
    assert [e["title"] for e in auth_client.get("/api/planner/local/events").json()] == []

    other = models.User(username="someone-else", hashed_password="hash")
    db.add(other)
    db.flush()
    foreign_task = models.PlannerTask(user_id=other.id, title="Not yours")
    foreign_event = models.PlannerEvent(
        user_id=other.id,
        title="Not yours either",
        start_time=datetime(2026, 10, 11, 9, 0),
        end_time=datetime(2026, 10, 11, 10, 0),
    )
    db.add_all([foreign_task, foreign_event])
    db.commit()

    assert auth_client.delete(f"/api/planner/local/tasks/{foreign_task.id}").status_code == 404
    assert auth_client.delete(f"/api/planner/local/events/{foreign_event.id}").status_code == 404
    db.expire_all()
    assert db.get(models.PlannerTask, foreign_task.id) is not None
    assert db.get(models.PlannerEvent, foreign_event.id) is not None
