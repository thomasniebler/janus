"""Real per-user tool-capability signal for the plan-driven runtime.

Supersedes ``capabilities_from_registry_entries()`` (the decorative
union-of-declared-capabilities helper flagged in JAN-171): that helper granted
each requested tool exactly the capability it itself declared needing, so the
registry's ``missing_capability`` denial could never fire. It is the
*enablement* gate for mail/calendar/jira/github/linear/web-search tools (see
``enabled_when`` on the corresponding entries in ``agent_tools.py``).

What "real" means here: whether the user has an actual live connection for an
integration (an active ``EmailAccount``, an active ``PlannerAccount`` for a
calendar- or task-capable provider, an enabled WhatsApp account, configured
GitHub/Linear tokens, or a configured web search backend), not
whether some row's boolean column says so. A user who has never connected
Jira sees Jira tools denied with ``missing_capability``.

Memory, topic, and local-planner tools have no "connection" equivalent: the
single Janus agent always gets them (JAN-201), so their capabilities sit in
``ALWAYS_GRANTED_CAPABILITIES`` below and their registry entries have no
``enabled_when`` gate.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from janus import models
from janus.services import azure_auth_service, microsoft_auth_service
from janus.services.image_generation import is_image_generation_configured
from janus.services.self_evolution_service import is_enabled as self_evolution_enabled
from janus.services.web_search_service import is_web_search_configured

#: Planner-account providers that back the "calendar" tool capability.
CALENDAR_PROVIDERS = frozenset({models.Provider.GOOGLE_CALENDAR, models.Provider.GOOGLE_TASKS, models.Provider.O365})

#: Planner-account providers that back the "jira" (task-tracker) tool capability.
TASK_PROVIDERS = frozenset({models.Provider.JIRA, models.Provider.ADO})

#: Capabilities every user has regardless of connection state: either
#: Janus-owned local data/tools with no third-party integration to be
#: "connected" to (core, memory, topics, planner.local, triggers), or capabilities whose
#: real gate is deliberately left on ``enabled_when`` instead of moved here
#: (skills is admin-only; remote MCP tools are gated per-server by
#: ownership/enabled flags), so the capability-intersection tier must not
#: shadow those gates with a spurious ``missing_capability`` denial.
ALWAYS_GRANTED_CAPABILITIES = frozenset(
    {"core", "memory", "topics", "knowledge", "wiki", "planner.local", "triggers", "skills", "mcp"}
)


def user_connected_capabilities(db: Session, user: models.User) -> frozenset[str]:
    """Return the tool-registry capability ids this user has a live connection for."""
    capabilities = set(ALWAYS_GRANTED_CAPABILITIES)

    has_mail = (
        db.query(models.EmailAccount.id)
        .filter(models.EmailAccount.user_id == user.id, models.EmailAccount.is_active.is_(True))
        .first()
        is not None
    )
    if has_mail:
        capabilities.add("mail")
        # Classification rules act on incoming mail.
        capabilities.add("rules")

    has_calendar = (
        db.query(models.PlannerAccount.id)
        .filter(
            models.PlannerAccount.user_id == user.id,
            models.PlannerAccount.is_active.is_(True),
            models.PlannerAccount.provider.in_(CALENDAR_PROVIDERS),
        )
        .first()
        is not None
    )
    if has_calendar:
        capabilities.add("calendar")

    has_tasks = (
        db.query(models.PlannerAccount.id)
        .filter(
            models.PlannerAccount.user_id == user.id,
            models.PlannerAccount.is_active.is_(True),
            models.PlannerAccount.provider.in_(TASK_PROVIDERS),
        )
        .first()
        is not None
    )
    if has_tasks:
        capabilities.add("jira")

    has_whatsapp = (
        db.query(models.WhatsAppAccount.id)
        .filter(models.WhatsAppAccount.user_id == user.id, models.WhatsAppAccount.is_enabled.is_(True))
        .first()
        is not None
    )
    if has_whatsapp:
        # Stored WhatsApp conversations to read (JAN-347).
        capabilities.add("whatsapp")

    if has_calendar or has_tasks:
        capabilities.add("dashboard")

    settings = db.query(models.Settings).first()
    if settings is not None:
        if settings.github_token:
            capabilities.add("github")
        if settings.linear_api_key:
            capabilities.add("linear")
    if is_web_search_configured(settings):
        capabilities.add("web_search")
    if "github" in capabilities and "linear" in capabilities and self_evolution_enabled(db, user):
        # The owner's self-evolution switch is on and both integrations are configured (JAN-320).
        capabilities.add("self_evolution")
    if is_image_generation_configured(settings):
        # A capable cloud provider is chosen and its key is stored (JAN-253).
        capabilities.add("images")

    # One instance-wide Microsoft identity (like GitHub/Linear), usable only by admins:
    # see the ``microsoft.*`` entries in agent_tools.py.
    if user.is_admin and microsoft_auth_service.is_connected():
        capabilities.add("microsoft")

    if user.is_admin and azure_auth_service.is_enabled():
        capabilities.add("azure")

    return frozenset(capabilities)


__all__ = [
    "ALWAYS_GRANTED_CAPABILITIES",
    "CALENDAR_PROVIDERS",
    "TASK_PROVIDERS",
    "user_connected_capabilities",
]
