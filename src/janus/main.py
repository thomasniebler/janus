"""Compatibility entry point for the Janus CLI and dashboard."""

import threading

import uvicorn

from janus import cli as _cli
from janus import database, llm_service, notifications, planner_service, status_service
from janus.app_factory import (
    RedirectException,
    api,
    create_fastapi_app,
    frontend_dir,
    initialize_first_run_setup,
    markdown_filter,
    redirect_exception_handler,
    serve_spa,
    template_dir,
    templates,
)
from janus.cli import (
    account_app,
    add_account,
    add_planner,
    add_rule,
    app,
    bootstrap_admin,
    brief,
    client,
    config_app,
    config_doctor,
    config_init,
    config_show,
    create_user,
    digest,
    list_accounts,
    list_planners,
    list_rules,
    list_users,
    login,
    main,
    me,
    planner_app,
    rule_app,
    user_app,
)
from janus.config import _SETTINGS_TEMPLATE, _normalize_allowed_origin, get_config_file_path, settings
from janus.desktop.tray import create_tray_icon
from janus.processor import validate_regex
from janus.services.dashboard_service import DashboardService
from janus.services.topic_service import TopicService
from janus.startup import ensure_runtime_initialized
from janus.websocket.briefing import ws_briefing
from janus.websocket.chat import _build_janus_system_prompt, ws_chat
from janus.worker_manager import worker_manager
from janus.workers.email import (
    _as_utc_aware,
    _build_digest_generation_payloads,
    _email_matches_notification_filters,
    _get_background_notification_preference,
    _parse_notification_filter_values,
    _should_notify_important_email,
    email_callback,
    initial_sync_worker,
)
from janus.workers.errors import error_compaction_loop
from janus.workers.planner import planner_sync_loop
from janus.workers.whatsapp import start_account_connector


def start(
    host: str = settings.host,
    port: int = settings.port,
    tray: bool = False,
    server_mode: bool = settings.server_mode,
):
    """Start Janus while preserving legacy ``janus.main`` monkeypatch paths."""
    _cli.ensure_runtime_initialized = ensure_runtime_initialized
    _cli.database = database
    _cli.notifications = notifications
    _cli.threading = threading
    _cli.uvicorn = uvicorn
    _cli.worker_manager = worker_manager
    return _cli.start(host=host, port=port, tray=tray, server_mode=server_mode)


__all__ = [
    "RedirectException",
    "DashboardService",
    "TopicService",
    "_SETTINGS_TEMPLATE",
    "_as_utc_aware",
    "_build_janus_system_prompt",
    "_build_digest_generation_payloads",
    "_email_matches_notification_filters",
    "_get_background_notification_preference",
    "_normalize_allowed_origin",
    "_parse_notification_filter_values",
    "_should_notify_important_email",
    "account_app",
    "add_account",
    "add_planner",
    "add_rule",
    "api",
    "app",
    "bootstrap_admin",
    "brief",
    "client",
    "config_app",
    "config_doctor",
    "config_init",
    "config_show",
    "create_fastapi_app",
    "create_tray_icon",
    "create_user",
    "database",
    "digest",
    "email_callback",
    "ensure_runtime_initialized",
    "error_compaction_loop",
    "frontend_dir",
    "get_config_file_path",
    "initialize_first_run_setup",
    "initial_sync_worker",
    "list_accounts",
    "list_planners",
    "list_rules",
    "list_users",
    "llm_service",
    "login",
    "main",
    "markdown_filter",
    "me",
    "notifications",
    "planner_app",
    "planner_service",
    "planner_sync_loop",
    "start_account_connector",
    "redirect_exception_handler",
    "rule_app",
    "serve_spa",
    "settings",
    "status_service",
    "start",
    "template_dir",
    "templates",
    "threading",
    "user_app",
    "uvicorn",
    "validate_regex",
    "worker_manager",
    "ws_briefing",
    "ws_chat",
]


if __name__ == "__main__":
    app()
