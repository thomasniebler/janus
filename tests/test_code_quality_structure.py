"""Structural guardrails for maintainability-sensitive Python modules."""

from __future__ import annotations

import ast
from pathlib import Path

#: Hotspot budgets are a bounded baseline (JAN-264): each exception is the file's
#: size when it was last set, at most this many lines above its real size, so a
#: hotspot cannot quietly grow into old slack, and an exception for a file back
#: under the default limit is stale. Shrink the budget when a hotspot shrinks. The
#: follow-up list is "Baselined hotspots" in docs/docs/tech-spec/architecture-overview.md.
EXCEPTION_SLACK = 10

API_MODULE_LINE_LIMIT = 350
SERVICE_MODULE_LINE_LIMIT = 500
CORE_MODULE_LINE_LIMIT = 600

API_MODULE_BUDGET_EXCEPTIONS = {
    # Raised from the 400 default for deleting a user's device tokens (action inbox; SQLite may reuse the id),
    # then to 408 for deleting their webhooks and remote agents the same way (JAN-406, JAN-404),
    # then to 410 for their Telegram bot (JAN-405).
    "src/janus/api/auth.py": 410,
    # Raised from 771 for the Google multi-calendar selection endpoints
    # (GET/PUT /api/planner/google/calendars), then from 822 for the chat and
    # utility model fields (JAN-233; their update rules live in llm_settings),
    # then from 831 for the embedding settings (JAN-241; also in llm_settings),
    # then from 832 for the image-generation settings (JAN-253; also in llm_settings);
    # lowered when the provider fields of the settings payload moved to llm_settings (JAN-340).
    "src/janus/api/planner.py": 382,
}

SERVICE_MODULE_BUDGET_EXCEPTIONS = {
    # Raised from the 500 default for revoking device tokens on password change and session revoke (action inbox).
    "src/janus/services/auth_service.py": 503,
    # Raised from 650 for the owner-scoped *_for_user account/rule/email methods
    # that replaced router-level ownership checks (JAN-137), then from 680 for
    # the owner-scoped email query/outgoing-account resolver (JAN-286) and the
    # per-owner correction-rule scope (JAN-287).
    "src/janus/services/email_service.py": 701,
    "src/janus/services/local_planner_service.py": 992,
    # Raised from 638 for the bearer token hooks (JAN-342; see mcp_static_token.py and mcp_status.py),
    # then to 652 for storing discovered tool annotations (JAN-343; see mcp_metadata.py).
    "src/janus/services/mcp_service.py": 651,
    # JAN-232 record_email_topic for the mail.received tools, plus manual topic
    # deletion (delete_topic) from main.
    "src/janus/services/topic_service.py": 995,
    # Raised from 500 for install kinds, release wheels (named after the janus-assistant
    # distribution) and the update state directory of uv tool / pipx installs (JAN-398, JAN-400);
    # their install and rollback live in update_tool_install.py. 544 for downloads that land
    # atomically (a .part file renamed once the digest matched).
    "src/janus/services/update_service.py": 544,
}

CORE_MODULE_BUDGET_EXCEPTIONS = {
    # Documented settings.toml template for the Google/Microsoft OAuth client keys;
    # raised to 675 for the coding agents tool's settings and template block (JAN-403),
    # then to 680 for the Telegram Bot API test seam (JAN-405).
    "src/janus/config.py": 680,
    # 1199 after the tool-call event recording of JAN-249; 1166 after the per-provider
    # model-name helper, the GitHub Models / Azure OpenAI dispatch, and resolved_value
    # moving to services/llm_provider_clients.py (JAN-340).
    # Raised to 1172 for routing OpenAI proxies to Chat Completions unless the model is
    # Responses-only (JAN-354; the helpers live in llm_provider_clients).
    # Raised to 1173 for attaching the LLM usage callback to every model (JAN-408; the logic is llm_usage.py).
    "src/janus/llm_service.py": 1173,
    # Raised from 600 for the heartbeat and first-sync-limit helpers the Outlook monitor shares
    # (graph_mail_service.py).
    "src/janus/imap_service.py": 605,
    # Raised from 600 for the proposal activation guards (JAN-235); the
    # approval rules themselves live in trigger_approval.py and the proposal
    # lifecycle in trigger_proposal_service.py. Raised to 660 to thread the event filter and
    # rate limit (JAN-363) through validation, create/update and emit_event; their logic
    # lives in trigger_event_filters.py.
    "src/janus/trigger_definition_service.py": 660,
    # Raised from 600 for start_conversation in INJECTION_ESCALATED_TOOL_NAMES (JAN-319),
    # then to 608 for the brain-selected toolset hook of prepare_step (JAN-300; the
    # selection itself lives in toolset_selection.py), then to 611 for the chat
    # allowlist's character-state entries (JAN-301), then to 612 for its WhatsApp entry (JAN-347),
    # then to 613 for its shell entry (JAN-353), then to 617 for its WhatsApp send entry (JAN-360)
    # and the "Allow everything" build flag of prepare_step (JAN-359), then to 618 for the
    # secretary's WhatsApp alert in the injection-escalated tool names, then to 619 for the
    # chat allowlist's coding agents entry (JAN-403).
    "src/janus/runtime_adapter.py": 619,
    # Raised from 650 for the extra `collections.abc` import line of the pyupgrade
    # auto-fix (JAN-259); lowered to 644 when the timestamp parsing moved to
    # time_utils.epoch_to_utc (JAN-346); raised to 667 for the trigger-event hooks of the
    # live connector (JAN-361: own identity on connect or first message, live/new/incoming/
    # has-content gate in _ingest), then to 672 for the review fixes; the event logic itself
    # lives in whatsapp_trigger_events.py. Raised to 676 for telling a missing optional
    # `whatsapp` extra apart from a native-library load failure in the import probe.
    "src/janus/whatsapp_neonize.py": 676,
}

API_HELPER_BASELINE = {
    # JAN-404: remote A2A agent registry (serialize without the token; owner-scoped 404).
    "src/janus/api/a2a.py": {"_get_or_404", "_response"},
    "src/janus/api/auth.py": {
        "_build_google_login_flow",
        "_client_ip",
        "_enforce_failed_attempt_rate_limit",
        "_issue_session_cookie",
        "_require_public_setup_enabled",
        "finish_google_login",
    },
    # JAN-301: maps character-state refusals to 404/409 for the state routes.
    "src/janus/api/character.py": {"_response_from_profile", "_state_errors"},
    "src/janus/api/connections.py": {"_callback_url", "_provider", "_status", "_take_state"},
    "src/janus/api/dashboard.py": {
        "_load_widget_or_500",
        "_raise_widget_load_error",
    },
    "src/janus/api/dev.py": {"_resolve_storybook_file"},
    "src/janus/api/emails.py": {
        "_ensure_settings",
        "_serialize_mail_ingestion_settings",
    },
    "src/janus/api/github.py": {
        "_ensure_settings",
        "_get_github_service",
    },
    # Action inbox: resolves a web session or a device token. It cannot live in security.py, which the data layer
    # (schemas) imports and which must not reach into services (import-linter contract).
    "src/janus/api/inbox.py": {"get_inbox_user"},
    "src/janus/api/linear.py": {
        "_ensure_settings",
        "_get_linear_service",
    },
    "src/janus/api/planner.py": {"_ensure_settings", "_serialize_settings", "_sync_active_model"},
    "src/janus/api/planner_sources.py": {"_list_calendars", "_outlook_connector", "_outlook_options", "_source"},
    "src/janus/api/skills.py": {
        "_build_skill_payload",
        "_extract_json_payload",
        "_sanitize_prompt_fragment",
    },
    "src/janus/api/status.py": {"_janus_version"},
    "src/janus/api/topics.py": {"_parse_status"},
    # JAN-405: serializes the bot without its token.
    "src/janus/api/telegram.py": {"_status"},
    "src/janus/api/tools.py": {"_get_server_or_404", "_server_response"},
    "src/janus/api/trigger_definitions.py": {"_get_or_404", "_unprocessable", "with_outcomes"},
    # JAN-406: inbound webhooks (serialize without the secret; owner-scoped 404).
    "src/janus/api/webhooks.py": {"_get_or_404", "_response"},
}


def _line_count(path: Path) -> int:
    return len(path.read_text().splitlines())


def _assert_module_budgets(paths: list[Path], default_limit: int, exceptions: dict[str, int]) -> None:
    offenders: list[str] = []
    for path in paths:
        budget = exceptions.get(path.as_posix(), default_limit)
        actual = _line_count(path)
        if actual > budget:
            offenders.append(f"{path.as_posix()} has {actual} lines (budget {budget})")
    assert not offenders, "\n".join(offenders)


def _api_helpers(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    helpers: set[str] = set()
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        decorators = [ast.unparse(decorator) for decorator in node.decorator_list]
        if any("router." in decorator for decorator in decorators):
            continue
        helpers.add(node.name)
    return helpers


def test_api_module_size_budgets() -> None:
    """Keep API modules within the agreed size budget or explicit baseline."""
    paths = sorted(Path("src/janus/api").glob("*.py"))
    _assert_module_budgets(
        [path for path in paths if path.name != "__init__.py"],
        API_MODULE_LINE_LIMIT,
        API_MODULE_BUDGET_EXCEPTIONS,
    )


def test_service_module_size_budgets() -> None:
    """Keep service modules within the agreed size budget or explicit baseline."""
    paths = sorted(Path("src/janus/services").glob("*.py"))
    _assert_module_budgets(
        [path for path in paths if path.name != "__init__.py"],
        SERVICE_MODULE_LINE_LIMIT,
        SERVICE_MODULE_BUDGET_EXCEPTIONS,
    )


def test_core_module_size_budgets() -> None:
    """Keep top-level core modules within the agreed size budget or explicit baseline."""
    paths = [
        path
        for path in sorted(Path("src/janus").glob("*.py"))
        if path.name != "__init__.py" and path.parent == Path("src/janus")
    ]
    _assert_module_budgets(paths, CORE_MODULE_LINE_LIMIT, CORE_MODULE_BUDGET_EXCEPTIONS)


def test_model_module_size_budgets() -> None:
    """Keep each ORM domain module of the ``janus.models`` package within the core default (JAN-136).

    The package replaced the 1620-line ``models.py`` hotspot; a domain module that outgrows
    the default is split further rather than given an exception.
    """
    _assert_module_budgets(sorted(Path("src/janus/models").glob("*.py")), CORE_MODULE_LINE_LIMIT, {})


def test_budget_exceptions_stay_tight() -> None:
    """Every hotspot budget is at most EXCEPTION_SLACK lines above its file, which is still over the default."""
    offenders: list[str] = []
    for exceptions, default_limit in (
        (API_MODULE_BUDGET_EXCEPTIONS, API_MODULE_LINE_LIMIT),
        (SERVICE_MODULE_BUDGET_EXCEPTIONS, SERVICE_MODULE_LINE_LIMIT),
        (CORE_MODULE_BUDGET_EXCEPTIONS, CORE_MODULE_LINE_LIMIT),
    ):
        for path, budget in exceptions.items():
            actual = _line_count(Path(path))
            if actual <= default_limit:
                offenders.append(f"{path} has {actual} lines, within the default {default_limit}: drop its exception")
            elif budget - actual > EXCEPTION_SLACK:
                offenders.append(f"{path} has {actual} lines but a budget of {budget}: lower the budget to {actual}")
    assert not offenders, "\n".join(offenders)


def test_api_helpers_do_not_expand_without_explicit_baseline_update() -> None:
    """Prevent API helper creep without an intentional guardrail update."""
    offenders: list[str] = []
    for path in sorted(Path("src/janus/api").glob("*.py")):
        if path.name == "__init__.py":
            continue
        actual = _api_helpers(path)
        expected = API_HELPER_BASELINE.get(path.as_posix(), set())
        if actual != expected:
            offenders.append(f"{path.as_posix()} helpers changed: expected {sorted(expected)}, got {sorted(actual)}")
    assert not offenders, "\n".join(offenders)
