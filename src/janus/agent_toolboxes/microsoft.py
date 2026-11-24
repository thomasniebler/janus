"""Microsoft 365 tools (Outlook mail/calendar, Teams chats) over Microsoft Graph.

Everything here acts as the signed-in Microsoft user through the delegated sign-in in
``services/microsoft_auth_service.py``. Mail and chat text is untrusted content (the
registry entries set ``reads_untrusted_content``), and the two send tools are
``confirmation="required"`` so a human approves each call.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from langchain_core.tools import BaseTool, tool

from janus.agent_toolboxes.shared import ToolContext
from janus.connectors.microsoft_graph import GraphError, MicrosoftGraphClient
from janus.services import microsoft_auth_service
from janus.tool_registry import ToolBuildContext, ToolRegistryEntry, ToolRisk

_MAX_RECIPIENTS = 20
_MAX_TEXT_CHARS = 20000


def _clean_recipients(addresses: list[str] | None) -> list[str]:
    cleaned = [address.strip() for address in addresses or [] if address and address.strip()]
    if len(cleaned) > _MAX_RECIPIENTS:
        raise ValueError(f"at most {_MAX_RECIPIENTS} recipients are allowed per mail")
    for address in cleaned:
        if "@" not in address or any(char.isspace() for char in address):
            raise ValueError(f"not an email address: {address!r}")
    return cleaned


def _require_text(value: str, name: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{name} must not be blank")
    if len(cleaned) > _MAX_TEXT_CHARS:
        raise ValueError(f"{name} must be at most {_MAX_TEXT_CHARS} characters")
    return cleaned


def build_microsoft_tools(context: ToolContext) -> list[BaseTool]:
    """Build the Outlook and Teams tools. ``context`` is unused: the identity is the instance's sign-in."""
    del context  # Not user-scoped; access is gated by the registry entries (admin + connected).

    def _call(action: Any) -> Any:
        """Run one Graph action with a fresh client, turning auth failures into tool errors."""
        client: MicrosoftGraphClient = microsoft_auth_service.build_graph_client()
        try:
            return action(client)
        except (GraphError, microsoft_auth_service.MicrosoftAuthError) as exc:
            raise ValueError(str(exc)) from exc
        finally:
            client.close()

    # --- Outlook mail -------------------------------------------------------------

    @tool("microsoft_list_mail")
    def microsoft_list_mail(
        folder: str = "inbox", limit: int = 10, unread_only: bool = False, search: str | None = None
    ) -> list[dict[str, Any]]:
        """Lists or searches messages in the user's Outlook mailbox (Microsoft 365), newest first.

        ``folder`` is a well-known name (inbox, sentitems, drafts, archive) or a folder id.
        ``search`` is a full-text query; it is ranked by relevance instead of date.
        """
        return _call(
            lambda client: client.list_messages(folder=folder, top=limit, unread_only=unread_only, search=search)
        )

    @tool("microsoft_get_mail")
    def microsoft_get_mail(message_id: str) -> dict[str, Any]:
        """Reads one Outlook message (headers and plain-text body) by the id from microsoft_list_mail."""
        return _call(lambda client: client.get_message(message_id))

    @tool("microsoft_send_mail")
    def microsoft_send_mail(to: list[str], subject: str, body: str, cc: list[str] | None = None) -> dict[str, Any]:
        """Sends a plain-text email from the user's Outlook mailbox (Microsoft 365)."""
        recipients = _clean_recipients(to)
        if not recipients:
            raise ValueError("at least one recipient is required")
        return _call(
            lambda client: client.send_mail(
                to=recipients,
                subject=_require_text(subject, "subject"),
                body=_require_text(body, "body"),
                cc=_clean_recipients(cc),
            )
        )

    # --- Outlook calendar ---------------------------------------------------------

    @tool("microsoft_list_calendar_events")
    def microsoft_list_calendar_events(days_ahead: int = 7, days_back: int = 0) -> list[dict[str, Any]]:
        """Lists the user's Outlook calendar events (recurring ones expanded), in UTC, from days_back to days_ahead."""
        now = datetime.now(UTC)
        start = now - timedelta(days=max(0, min(days_back, 90)))
        end = now + timedelta(days=max(1, min(days_ahead, 90)))
        return _call(lambda client: client.list_events(start, end))

    # --- Teams chats --------------------------------------------------------------

    @tool("microsoft_list_teams_chats")
    def microsoft_list_teams_chats(limit: int = 20) -> list[dict[str, Any]]:
        """Lists the user's Microsoft Teams chats (1:1, group, meeting), most recently active first, with members."""
        return _call(lambda client: client.list_chats(top=limit))

    @tool("microsoft_read_teams_chat")
    def microsoft_read_teams_chat(chat_id: str, limit: int = 20) -> list[dict[str, Any]]:
        """Reads the newest messages of one Teams chat, newest first, by the id from microsoft_list_teams_chats."""
        return _call(lambda client: client.list_chat_messages(chat_id, top=limit))

    @tool("microsoft_send_teams_message")
    def microsoft_send_teams_message(chat_id: str, text: str) -> dict[str, Any]:
        """Posts a plain-text message into a Microsoft Teams chat as the user."""
        return _call(lambda client: client.send_chat_message(chat_id, _require_text(text, "text")))

    return [
        microsoft_list_mail,
        microsoft_get_mail,
        microsoft_send_mail,
        microsoft_list_calendar_events,
        microsoft_list_teams_chats,
        microsoft_read_teams_chat,
        microsoft_send_teams_message,
    ]


MAIL_READ_TOOLS = frozenset({"microsoft_list_mail", "microsoft_get_mail"})
MAIL_SEND_TOOLS = frozenset({"microsoft_send_mail"})
CALENDAR_READ_TOOLS = frozenset({"microsoft_list_calendar_events"})
TEAMS_READ_TOOLS = frozenset({"microsoft_list_teams_chats", "microsoft_read_teams_chat"})
TEAMS_SEND_TOOLS = frozenset({"microsoft_send_teams_message"})


def _entry(
    *,
    entry_id: str,
    display_name: str,
    description: str,
    docs_anchor: str,
    names: frozenset[str],
    write: bool,
    risk_level: ToolRisk,
) -> ToolRegistryEntry:
    """One registry entry over a named subset of this toolbox.

    Reads are untrusted-content and never need confirmation; writes (sending as the user)
    always do. Every entry needs the instance-wide sign-in and an administrator.
    """

    def build(context: ToolBuildContext) -> list[BaseTool]:
        return [tool for tool in build_microsoft_tools(context.tool_context) if tool.name in names]

    def enabled(context: ToolBuildContext) -> bool:
        return bool(context.user.is_admin and "microsoft" in context.tool_context.connected_capabilities)

    return ToolRegistryEntry(
        id=entry_id,
        reads_untrusted_content=not write,
        display_name=display_name,
        description=description,
        required_capabilities=("microsoft",),
        operation="write" if write else "read",
        risk_level=risk_level,
        confirmation="required" if write else "never",
        docs_ref=f"tools.md#{docs_anchor}",
        builder=build,
        enabled_when=enabled,
    )


def registry_entries() -> list[ToolRegistryEntry]:
    """The five ``microsoft.*`` registry entries, in registry order (see agent_tools.py)."""
    return [
        _entry(
            entry_id="microsoft.mail",
            display_name="Microsoft Outlook mail",
            description="Search and read the user's Outlook (Microsoft 365) mailbox.",
            docs_anchor="microsoft-outlook-mail",
            names=MAIL_READ_TOOLS,
            write=False,
            risk_level="medium",
        ),
        _entry(
            entry_id="microsoft.calendar",
            display_name="Microsoft Outlook calendar",
            description="List the user's Outlook (Microsoft 365) calendar events.",
            docs_anchor="microsoft-outlook-calendar",
            names=CALENDAR_READ_TOOLS,
            write=False,
            risk_level="low",
        ),
        _entry(
            entry_id="microsoft.teams",
            display_name="Microsoft Teams chats",
            description="List and read the user's Microsoft Teams chats.",
            docs_anchor="microsoft-teams-chats",
            names=TEAMS_READ_TOOLS,
            write=False,
            risk_level="medium",
        ),
        _entry(
            entry_id="microsoft.mail.send",
            display_name="Send Microsoft Outlook mail",
            description="Send email from the user's Outlook (Microsoft 365) mailbox.",
            docs_anchor="send-microsoft-outlook-mail",
            names=MAIL_SEND_TOOLS,
            write=True,
            risk_level="high",
        ),
        _entry(
            entry_id="microsoft.teams.send",
            display_name="Send Microsoft Teams messages",
            description="Post messages into the user's Microsoft Teams chats as the user.",
            docs_anchor="send-microsoft-teams-messages",
            names=TEAMS_SEND_TOOLS,
            write=True,
            risk_level="high",
        ),
    ]
