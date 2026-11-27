"""Microsoft 365 agent tools: admin/connection gating, confirmation tiers, validation, and error mapping."""

from __future__ import annotations

import pytest

from janus import models, security
from janus.agent_tools import DEFAULT_TOOL_REGISTRY, build_authorized_tool_set, build_tool_set
from janus.capability_profile import user_connected_capabilities
from janus.connectors.microsoft_graph import GraphError
from janus.runtime_adapter import TASK_MODE_ALLOWED_TOOL_IDS, RuntimeAdapter
from janus.services import microsoft_auth_service
from janus.tool_policy import default_unattended_allowed
from janus.tool_registry import ToolSelectionRequest

READ_IDS = ("microsoft.mail", "microsoft.calendar", "microsoft.teams")
SEND_IDS = ("microsoft.mail.send", "microsoft.teams.send")
ALL_IDS = READ_IDS + SEND_IDS
ALL_TOOL_NAMES = {
    "microsoft_list_mail",
    "microsoft_get_mail",
    "microsoft_send_mail",
    "microsoft_list_calendar_events",
    "microsoft_list_teams_chats",
    "microsoft_read_teams_chat",
    "microsoft_send_teams_message",
}


def _user(db, name: str, *, admin: bool) -> models.User:
    user = models.User(
        username=name,
        hashed_password=security.security_service.get_password_hash("secret"),
        is_admin=admin,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _names(db, user) -> set[str]:
    return {tool.name for tool in build_tool_set(db, user)}


@pytest.fixture
def connected(monkeypatch):
    """Pretend a Microsoft sign-in exists on this machine."""
    monkeypatch.setattr(microsoft_auth_service, "is_connected", lambda settings=None: True)


class FakeGraphClient:
    """Records calls instead of reaching Microsoft Graph."""

    def __init__(self, error: Exception | None = None):
        """Set up the fake."""
        self.calls: list[tuple] = []
        self.closed = False
        self.error = error

    def _record(self, name, *args, **kwargs):
        if self.error is not None:
            raise self.error
        self.calls.append((name, args, kwargs))
        return {"ok": name}

    def list_messages(self, **kwargs):
        """Fake ``list_messages``: record the call."""
        return self._record("list_messages", **kwargs)

    def get_message(self, *args):
        """Fake ``get_message``: record the call."""
        return self._record("get_message", *args)

    def send_mail(self, **kwargs):
        """Fake ``send_mail``: record the call."""
        return self._record("send_mail", **kwargs)

    def list_events(self, *args, **kwargs):
        """Fake ``list_events``: record the call."""
        return self._record("list_events", *args, **kwargs)

    def list_chats(self, **kwargs):
        """Fake ``list_chats``: record the call."""
        return self._record("list_chats", **kwargs)

    def list_chat_messages(self, *args, **kwargs):
        """Fake ``list_chat_messages``: record the call."""
        return self._record("list_chat_messages", *args, **kwargs)

    def send_chat_message(self, *args):
        """Fake ``send_chat_message``: record the call."""
        return self._record("send_chat_message", *args)

    def close(self):
        """Fake ``close``: record the call."""
        self.closed = True


@pytest.fixture
def fake_graph(monkeypatch):
    """Replace the Graph client factory with a recording fake."""
    client = FakeGraphClient()
    monkeypatch.setattr(microsoft_auth_service, "build_graph_client", lambda settings=None: client)
    return client


def _tools(db, connected_admin_name="owner"):
    user = _user(db, connected_admin_name, admin=True)
    return {tool.name: tool for tool in build_tool_set(db, user)}


# --- gating -------------------------------------------------------------------------


def test_tools_are_absent_until_a_microsoft_sign_in_exists(db, monkeypatch):
    """Tools are absent until a microsoft sign in exists."""
    monkeypatch.setattr(microsoft_auth_service, "is_connected", lambda settings=None: False)
    admin = _user(db, "owner", admin=True)

    assert not (_names(db, admin) & ALL_TOOL_NAMES)


def test_tools_are_offered_to_an_admin_once_connected(db, connected):
    """Tools are offered to an admin once connected."""
    admin = _user(db, "owner", admin=True)

    assert _names(db, admin) >= ALL_TOOL_NAMES


def test_a_non_admin_never_gets_the_instance_wide_mailbox(db, connected):
    """The sign-in is one identity for the instance; another Janus user must not read that mailbox."""
    other = _user(db, "colleague", admin=False)

    assert not (_names(db, other) & ALL_TOOL_NAMES)


def test_authorization_denies_a_non_admin_with_missing_capability(db, connected):
    """Authorization denies a non admin with missing capability."""
    other = _user(db, "colleague", admin=False)
    selection, tools = build_authorized_tool_set(
        db,
        other,
        ToolSelectionRequest(
            task_id="t1",
            requested_tool_ids=ALL_IDS,
            allowed_tool_ids=frozenset(ALL_IDS),
            granted_capabilities=frozenset({"microsoft"}),
        ),
    )

    assert selection.entries == ()
    assert tools == []
    assert {denial.tool_id for denial in selection.denials} == set(ALL_IDS)


def test_authorization_grants_an_admin_and_still_needs_the_capability(db, connected):
    """Authorization grants an admin and still needs the capability."""
    admin = _user(db, "owner", admin=True)
    request = ToolSelectionRequest(
        task_id="t1",
        requested_tool_ids=("microsoft.mail",),
        allowed_tool_ids=frozenset({"microsoft.mail"}),
        granted_capabilities=frozenset({"microsoft"}),
    )
    selection, tools = build_authorized_tool_set(db, admin, request)

    assert [entry.id for entry in selection.entries] == ["microsoft.mail"]
    assert {tool.name for tool in tools} == {"microsoft_list_mail", "microsoft_get_mail"}


def _chat_step(db, user, *, confirmed=frozenset()):
    return RuntimeAdapter(db, user).prepare_step(
        task_id="t",
        objective="check my Outlook",
        requested_tool_ids=ALL_IDS,
        task_mode="chat",
        granted_capabilities=user_connected_capabilities(db, user),
        confirmed_tool_ids=confirmed,
    )


def test_chat_allowlists_every_microsoft_entry_and_system_pipelines_do_not():
    """Chat offers the Microsoft toolboxes; briefing, digest and ingestion never do."""
    assert set(ALL_IDS) <= TASK_MODE_ALLOWED_TOOL_IDS["chat"]
    for mode in ("briefing", "digest", "ingestion"):
        assert not (set(ALL_IDS) & TASK_MODE_ALLOWED_TOOL_IDS[mode]), mode


def test_chat_step_gives_an_admin_the_reads_and_asks_before_sending(db, connected):
    """In chat an admin gets the read tools at once; the send tools wait for approval."""
    admin = _user(db, "owner", admin=True)

    prepared = _chat_step(db, admin)
    assert {tool.name for tool in prepared.tools} == ALL_TOOL_NAMES - {
        "microsoft_send_mail",
        "microsoft_send_teams_message",
    }
    assert set(prepared.awaiting_confirmation_tool_ids) == set(SEND_IDS)

    approved = _chat_step(db, admin, confirmed=frozenset(SEND_IDS))
    assert {tool.name for tool in approved.tools} == ALL_TOOL_NAMES


def test_chat_step_gives_a_non_admin_nothing(db, connected):
    """A non-admin gets no Microsoft tool in chat, even approved."""
    other = _user(db, "colleague", admin=False)

    prepared = _chat_step(db, other, confirmed=frozenset(SEND_IDS))
    assert not ({tool.name for tool in prepared.tools} & ALL_TOOL_NAMES)
    assert prepared.awaiting_confirmation_tool_ids == ()


# --- tiers ----------------------------------------------------------------------------


def test_registry_tiers_read_vs_send():
    """Registry tiers read vs send."""
    entries = {entry.id: entry for entry in DEFAULT_TOOL_REGISTRY.entries()}

    for tool_id in READ_IDS:
        entry = entries[tool_id]
        assert entry.operation == "read"
        assert entry.confirmation == "never"
        assert entry.reads_untrusted_content, "mail and chat text is third-party content"
        assert default_unattended_allowed(entry)
    for tool_id in SEND_IDS:
        entry = entries[tool_id]
        assert entry.operation == "write"
        assert entry.risk_level == "high"
        assert entry.confirmation == "required", "sending as the user always needs a human OK"
        assert not default_unattended_allowed(entry)
    assert all(entries[tool_id].required_capabilities == ("microsoft",) for tool_id in ALL_IDS)


def test_each_tool_belongs_to_exactly_one_registry_entry(db, connected):
    """A name in two entries would let a read grant also expose a send tool."""
    admin = _user(db, "owner", admin=True)
    from janus.agent_toolboxes.shared import ToolContext
    from janus.tool_registry import ToolBuildContext

    context = ToolBuildContext(
        tool_context=ToolContext(db=db, user=admin, connected_capabilities=frozenset({"microsoft"}))
    )
    owners: dict[str, list[str]] = {}
    for entry in DEFAULT_TOOL_REGISTRY.entries():
        if entry.id in ALL_IDS:
            for tool in entry.builder(context):
                owners.setdefault(tool.name, []).append(entry.id)

    assert set(owners) == ALL_TOOL_NAMES
    assert all(len(ids) == 1 for ids in owners.values())
    assert owners["microsoft_send_mail"] == ["microsoft.mail.send"]
    assert owners["microsoft_send_teams_message"] == ["microsoft.teams.send"]


# --- behaviour ------------------------------------------------------------------------


def test_read_tools_pass_arguments_through_and_close_the_client(db, connected, fake_graph):
    """Read tools pass arguments through and close the client."""
    tools = _tools(db)

    tools["microsoft_list_mail"].invoke({"folder": "sentitems", "limit": 3, "unread_only": True, "search": "invoice"})
    tools["microsoft_get_mail"].invoke({"message_id": "m1"})
    tools["microsoft_list_teams_chats"].invoke({"limit": 4})
    tools["microsoft_read_teams_chat"].invoke({"chat_id": "c1", "limit": 5})

    assert fake_graph.calls == [
        ("list_messages", (), {"folder": "sentitems", "top": 3, "unread_only": True, "search": "invoice"}),
        ("get_message", ("m1",), {}),
        ("list_chats", (), {"top": 4}),
        ("list_chat_messages", ("c1",), {"top": 5}),
    ]
    assert fake_graph.closed


def test_calendar_window_is_bounded(db, connected, fake_graph):
    """Calendar window is bounded."""
    tools = _tools(db)

    tools["microsoft_list_calendar_events"].invoke({"days_ahead": 10_000, "days_back": 10_000})
    _, (start, end), _ = fake_graph.calls[0]

    assert (end - start).days == 180, "90 days back and 90 days ahead at most"


def test_send_mail_validates_recipients_and_content_before_calling_graph(db, connected, fake_graph):
    """Send mail validates recipients and content before calling graph."""
    tools = _tools(db)
    send = tools["microsoft_send_mail"]

    for bad in (
        {"to": [], "subject": "s", "body": "b"},
        {"to": ["not-an-address"], "subject": "s", "body": "b"},
        {"to": ["a b@example.com"], "subject": "s", "body": "b"},
        {"to": ["a@example.com"], "subject": "  ", "body": "b"},
        {"to": ["a@example.com"], "subject": "s", "body": ""},
        {"to": [f"u{i}@example.com" for i in range(21)], "subject": "s", "body": "b"},
        {"to": ["a@example.com"], "subject": "s", "body": "b", "cc": ["nope"]},
    ):
        with pytest.raises(ValueError):
            send.invoke(bad)

    assert fake_graph.calls == []

    send.invoke({"to": [" a@example.com "], "subject": " Hi ", "body": "Body", "cc": ["c@example.com"]})
    assert fake_graph.calls == [
        ("send_mail", (), {"to": ["a@example.com"], "subject": "Hi", "body": "Body", "cc": ["c@example.com"]})
    ]


def test_send_teams_message_requires_text(db, connected, fake_graph):
    """Send teams message requires text."""
    send = _tools(db)["microsoft_send_teams_message"]

    with pytest.raises(ValueError):
        send.invoke({"chat_id": "c1", "text": "   "})
    with pytest.raises(ValueError):
        send.invoke({"chat_id": "c1", "text": "x" * 20001})
    assert fake_graph.calls == []

    send.invoke({"chat_id": "c1", "text": " hello "})
    assert fake_graph.calls == [("send_chat_message", ("c1", "hello"), {})]


@pytest.mark.parametrize(
    "error",
    [
        GraphError("Microsoft Graph returned 403 Forbidden: nope. run `janus microsoft login`", 403),
        microsoft_auth_service.MicrosoftAuthError("Microsoft sign-in expired. Run `janus microsoft login` again."),
    ],
)
def test_graph_and_auth_failures_become_actionable_tool_errors(db, connected, monkeypatch, error):
    """Graph and auth failures become actionable tool errors."""
    client = FakeGraphClient(error=error)
    monkeypatch.setattr(microsoft_auth_service, "build_graph_client", lambda settings=None: client)
    tools = _tools(db)

    with pytest.raises(ValueError, match="janus microsoft login"):
        tools["microsoft_list_teams_chats"].invoke({})

    assert client.closed, "the client is released even when the call fails"


def test_a_token_failure_while_building_the_request_is_also_mapped(db, connected, monkeypatch):
    """build_graph_client itself is lazy, but a failure raised from the first request must still map."""

    class RaisingOnUse(FakeGraphClient):
        def list_chats(self, **kwargs):
            raise microsoft_auth_service.MicrosoftAuthError("Not signed in to Microsoft. Run `janus microsoft login`.")

    monkeypatch.setattr(microsoft_auth_service, "build_graph_client", lambda settings=None: RaisingOnUse())
    tools = _tools(db)

    with pytest.raises(ValueError, match="Not signed in"):
        tools["microsoft_list_teams_chats"].invoke({})
