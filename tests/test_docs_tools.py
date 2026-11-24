"""Tests for the ``docs`` toolbox and the shared docs service (JAN-297)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from janus import agent_runtime, llm_service, models, security, tool_policy
from janus.agent_toolboxes import docs as docs_tools
from janus.agent_tools import DEFAULT_TOOL_REGISTRY, build_managed_registry
from janus.runtime_adapter import TASK_MODE_ALLOWED_TOOL_IDS, RuntimeAdapter
from janus.services import docs_service


@pytest.fixture
def auth_client(client, db):
    """A client with an authenticated user session."""
    db.add(models.User(username="chat-user", hashed_password=security.security_service.get_password_hash("pw")))
    db.commit()
    response = client.post("/api/auth/login", json={"username": "chat-user", "password": "pw"})
    client.headers[security.CSRF_HEADER_NAME] = response.json()["csrf_token"]
    return client


def _user(db, username: str = "docs-reader") -> models.User:
    user = models.User(username=username, hashed_password="x", is_admin=False)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture
def docs_tree(tmp_path, monkeypatch):
    """A small docs/docs tree with a nav, a review, an outside secret, and an alias target."""
    repo = tmp_path / "repo"
    docs_dir = repo / "docs" / "docs"
    (docs_dir / "guide").mkdir(parents=True)
    (docs_dir / "reviews").mkdir()
    (docs_dir / "index.md").write_text("# Welcome\n\nJanus is a local-first assistant.\n", encoding="utf-8")
    (docs_dir / "guide" / "setup.md").write_text(
        "# Setup\n\nIntro text.\n\n## Connect mail\n\nAdd an IMAP account with an app password.\n\n"
        "### Gmail\n\nUse an app password for Gmail.\n\n## Unattended switch\n\nTurn tools on or off.\n\n"
        "```bash\n# not a heading\n```\n",
        encoding="utf-8",
    )
    (docs_dir / "unlisted.md").write_text("# Unlisted\n\nA page the nav does not list.\n", encoding="utf-8")
    (docs_dir / "reviews" / "2025-01-01.md").write_text(
        "# Security assessment\n\nZebrafinch weakness in the app password handling.\n", encoding="utf-8"
    )
    (repo / "secret.md").write_text("# Secret\n\nZebrafinch outside the docs.\n", encoding="utf-8")
    (repo / "TECH_SPEC.md").write_text("# Technical spec\n\nThe aliased root spec.\n", encoding="utf-8")
    (repo / "docs" / "zensical.toml").write_text(
        "[project]\nnav = [\n"
        '  {"Home" = "index.md"},\n'
        '  {"Guide" = [{"Setup" = "guide/setup.md"}]},\n'
        '  {"Reviews" = [{"2025" = "reviews/2025-01-01.md"}]},\n'
        '  {"Spec" = "tech-spec.md"}\n'
        "]\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(docs_service, "DOCS_DIR", docs_dir)
    monkeypatch.setattr(docs_service, "NAV_CONFIG", repo / "docs" / "zensical.toml")
    monkeypatch.setattr(docs_service, "ROOT_DOC_ALIASES", {"tech-spec.md": repo / "TECH_SPEC.md"})
    return docs_dir


# --- path confinement --------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "../secret.md",
        "../../secret.md",
        "guide/../../secret.md",
        "%2e%2e/secret.md",
        "guide",
        ".",
        "missing.md",
        "TECH_SPEC.md",
        "../TECH_SPEC.md",
        "index.md\x00.txt",
    ],
)
def test_read_doc_refuses_paths_outside_docs(docs_tree, path):
    """Traversal, directories, unknown pages, and anything but the one alias are 'not found'."""
    result = docs_tools.read_doc(path)
    assert "text" not in result
    assert "No documentation page" in result["message"]


def test_read_doc_refuses_absolute_paths(docs_tree):
    """An absolute path never escapes docs/docs, even when it names a real file."""
    secret = docs_tree.parent.parent / "secret.md"
    assert "text" not in docs_tools.read_doc(str(secret))
    assert "text" not in docs_tools.read_doc("/etc/passwd")


def test_read_doc_refuses_symlinks_pointing_outside(docs_tree):
    """A symlink inside docs/docs that resolves outside it is refused, file or directory."""
    outside = docs_tree.parent.parent
    (docs_tree / "link.md").symlink_to(outside / "secret.md")
    (docs_tree / "linkdir").symlink_to(outside)
    (docs_tree / "inside.md").symlink_to(docs_tree / "index.md")

    assert "text" not in docs_tools.read_doc("link.md")
    assert "text" not in docs_tools.read_doc("linkdir/secret.md")
    assert "link.md" not in docs_service.list_pages()
    # A symlink that stays inside the tree is an ordinary page.
    assert docs_tools.read_doc("inside.md")["title"] == "Welcome"


def test_tech_spec_alias_is_the_only_file_outside_docs(docs_tree):
    """``tech-spec.md`` reads the root TECH_SPEC.md."""
    result = docs_tools.read_doc("tech-spec.md")
    assert result["title"] == "Technical spec"
    assert "aliased root spec" in result["text"]


def test_reviews_are_excluded_from_the_tool_but_not_the_service(docs_tree):
    """The agent cannot read reviews/ (also via ``..`` from a sibling); the router still can."""
    for path in ("reviews/2025-01-01.md", "guide/../reviews/2025-01-01.md"):
        assert "text" not in docs_tools.read_doc(path)
    assert "Security assessment" in docs_service.read_doc_text("reviews/2025-01-01.md")
    assert "reviews/2025-01-01.md" in docs_service.list_pages()
    assert "reviews/2025-01-01.md" not in docs_service.list_pages(excluded_dirs=docs_service.AGENT_EXCLUDED_DIRS)


def test_router_serves_docs_to_a_logged_in_session(auth_client):
    """The router serves the shipped docs; since JAN-299 it needs a login."""
    assert auth_client.get("/api/docs/tools.md").status_code == 200
    assert auth_client.get("/api/docs/%2e%2e/%2e%2e/pyproject.toml").status_code == 404
    assert auth_client.get("/api/docs/nav").json()


# --- list / read / search ----------------------------------------------------


def test_list_docs_returns_nav_without_reviews_plus_unlisted_pages(docs_tree):
    """The nav tree keeps titles and paths, drops reviews/, and names pages outside the nav."""
    listing = docs_service.agent_nav()
    assert listing["nav"] == [
        {"name": "Home", "path": "index.md"},
        {"name": "Guide", "children": [{"name": "Setup", "path": "guide/setup.md"}]},
        {"name": "Spec", "path": "tech-spec.md"},
    ]
    assert listing["other_pages"] == ["unlisted.md"]


def test_read_doc_caps_size_and_continues_from_offset(docs_tree):
    """Long pages come back in chunks; following ``next_offset`` reassembles the whole page."""
    body = "# Long\n\n" + "".join(f"Line {n} of a very long page.\n" for n in range(2000))
    (docs_tree / "long.md").write_text(body, encoding="utf-8")

    first = docs_tools.read_doc("long.md")
    assert len(first["text"]) == docs_tools.READ_DOC_MAX_CHARS
    assert first["total_chars"] == len(body)
    assert first["next_offset"] == docs_tools.READ_DOC_MAX_CHARS

    pieces, offset = [first["text"]], first["next_offset"]
    while offset is not None:
        chunk = docs_tools.read_doc("long.md", offset=offset)
        assert len(chunk["text"]) <= docs_tools.READ_DOC_MAX_CHARS
        pieces.append(chunk["text"])
        offset = chunk["next_offset"]
    assert "".join(pieces) == body
    assert docs_tools.read_doc("long.md", offset=len(body) + 10)["text"] == ""


def test_read_doc_section_by_anchor_heading_or_fragment(docs_tree):
    """A section includes its subsections, stops at its sibling, and ignores headings in code."""
    by_anchor = docs_tools.read_doc("guide/setup.md", section="connect-mail")
    assert by_anchor["section"] == "Connect mail"
    assert "Gmail" in by_anchor["text"]
    assert "Unattended switch" not in by_anchor["text"]
    assert docs_tools.read_doc("guide/setup.md", section="Connect Mail")["text"] == by_anchor["text"]
    assert docs_tools.read_doc("guide/setup.md#connect-mail")["text"] == by_anchor["text"]

    missing = docs_tools.read_doc("guide/setup.md", section="not a heading")
    assert "text" not in missing
    assert missing["sections"] == ["Setup", "Connect mail", "Gmail", "Unattended switch"]


def test_search_docs_hits_misses_and_excludes_reviews(docs_tree):
    """Search returns path, heading, anchor and a snippet; reviews/ and outside files never match."""
    hits = docs_tools.search_docs("app password")["results"]
    assert hits[0]["path"] == "guide/setup.md"
    assert {hit["section"] for hit in hits} == {"connect-mail", "gmail"}
    assert all("password" in hit["snippet"].lower() for hit in hits)

    assert docs_tools.search_docs("zebrafinch")["results"] == []
    assert "Nothing in the documentation" in docs_tools.search_docs("zebrafinch")["message"]
    assert docs_tools.search_docs("  ")["results"] == []
    assert docs_tools.search_docs("aliased")["results"][0]["path"] == "tech-spec.md"


def test_search_docs_bounds_results(docs_tree):
    """At most ``limit`` (and never more than 20) hits come back."""
    for n in range(30):
        (docs_tree / f"page{n}.md").write_text(f"# Page {n}\n\nkeyword here\n", encoding="utf-8")
    assert len(docs_tools.search_docs("keyword", limit=3)["results"]) == 3
    assert len(docs_tools.search_docs("keyword", limit=500)["results"]) == 20


def test_real_docs_answer_a_how_do_i_question():
    """Against the shipped docs, a permissions question finds the tools page."""
    paths = {hit["path"] for hit in docs_tools.search_docs("unattended switch BLOCKED", limit=20)["results"]}
    assert "tools.md" in paths


# --- registry wiring, policy, docs_ref ---------------------------------------


def test_registry_entry_is_read_only_first_party_and_offered_in_chat():
    """``docs`` is a never-confirm read, not untrusted content, and in the chat catalog."""
    entry = next(entry for entry in DEFAULT_TOOL_REGISTRY.entries() if entry.id == "docs")
    assert (entry.operation, entry.confirmation, entry.risk_level) == ("read", "never", "low")
    assert entry.required_capabilities == ()
    assert entry.reads_untrusted_content is False
    assert entry.enabled_when(object()) is True
    assert "docs" in TASK_MODE_ALLOWED_TOOL_IDS["chat"]
    assert [tool.name for tool in entry.builder(MagicMock())] == ["list_docs", "read_doc", "search_docs"]


def test_docs_policy_defaults_allowed_unlocked_and_delegable(db):
    """Unattended runs follow the normal switch: allowed by default, delegable, switchable off."""
    user = _user(db)
    entries = DEFAULT_TOOL_REGISTRY.entries()
    assert "docs" not in tool_policy.LOCKED_UNATTENDED
    assert "docs" in tool_policy.unattended_allowed_tool_ids(db, user.id, entries)
    assert "docs" in tool_policy.delegable_tool_ids(frozenset({"docs"}), entries)
    offered, confirmed = tool_policy.unattended_tool_sets(db, user.id, entries, task_mode="delegated")
    assert "docs" in offered and "docs" in confirmed

    tool_policy.set_policy(db, user.id, "docs", False)
    assert "docs" not in tool_policy.unattended_allowed_tool_ids(db, user.id, entries)


def _resolve_ref(ref: str) -> dict[str, Any]:
    path, _, anchor = ref.partition("#")
    return docs_tools.read_doc(path, section=anchor or None)


def test_every_registry_docs_ref_resolves(db):
    """Every built-in entry and remote MCP entries point at a real page section."""
    refs = {entry.id: entry.docs_ref for entry in DEFAULT_TOOL_REGISTRY.entries()}
    assert all(refs.values()), [tool_id for tool_id, ref in refs.items() if not ref]
    unresolved = {tool_id: ref for tool_id, ref in refs.items() if "text" not in _resolve_ref(ref)}
    assert unresolved == {}

    user = _user(db, "mcp-owner")
    server = models.MCPServer(
        user_id=user.id,
        provider_name="Remote",
        endpoint="http://127.0.0.1:8787/mcp",
        enabled=True,
        discovery_status=models.MCPDiscoveryStatus.READY,
    )
    db.add(server)
    db.flush()
    db.add(models.MCPDiscoveredTool(server_id=server.id, upstream_name="search", namespaced_id="mcp.1.search"))
    db.commit()
    registry = build_managed_registry(db, user)
    remote = next(entry for entry in registry.entries() if entry.id == "mcp.1.search")
    assert "text" in _resolve_ref(remote.docs_ref)


def test_blocked_answer_cites_the_docs_page(db):
    """A tool switched off for unattended runs answers BLOCKED and names its docs section."""
    user = _user(db, "blocked-docs")
    tool_policy.set_policy(db, user.id, "docs", False)
    offered, confirmed = tool_policy.unattended_tool_sets(
        db, user.id, DEFAULT_TOOL_REGISTRY.entries(), task_mode="trigger"
    )
    prepared = RuntimeAdapter(db, user).prepare_step(
        task_id="trigger-docs-blocked",
        objective="Look something up",
        requested_tool_ids=("docs",),
        task_mode="trigger",
        allowed_tool_ids=offered,
        confirmed_tool_ids=confirmed,
        blocked_tool_ids=offered - confirmed,
        refusal_stubs=True,
    )
    read = next(tool for tool in prepared.tools if tool.name == "read_doc")
    answer = read.invoke({"path": "index.md"})
    assert answer.startswith("BLOCKED: read_doc was not run")
    assert "See the docs: tools.md#janus-documentation." in answer
    assert "text" in _resolve_ref("tools.md#janus-documentation")
    # The prompt-injection refusal cites the general permissions section.
    assert "text" in _resolve_ref("tools.md#how-permissions-work")


def test_unattended_run_reads_docs_by_default(db):
    """With no override, a trigger run may call the docs tools."""
    user = _user(db, "trigger-docs")
    offered, confirmed = tool_policy.unattended_tool_sets(
        db, user.id, DEFAULT_TOOL_REGISTRY.entries(), task_mode="trigger"
    )
    prepared = RuntimeAdapter(db, user).prepare_step(
        task_id="trigger-docs",
        objective="Look something up",
        requested_tool_ids=("docs",),
        task_mode="trigger",
        allowed_tool_ids=offered,
        confirmed_tool_ids=confirmed,
        blocked_tool_ids=offered - confirmed,
        refusal_stubs=True,
    )
    read = next(tool for tool in prepared.tools if tool.name == "read_doc")
    assert read.invoke({"path": "tools.md", "section": "send-mail"})["section"] == "Send mail"
    # First-party output is not spotlighted as untrusted content.
    assert not prepared.trace.has_read_untrusted_content()


def test_system_prompt_tells_janus_to_use_the_docs():
    """The shared prompt (chat and every unattended run) points Janus at the docs tools."""
    character = MagicMock()
    character.render_system_prompt.return_value = "# Character constitution: Janus"
    prompt = agent_runtime.build_janus_system_prompt(character)
    assert "search_docs" in prompt and "read_doc" in prompt
    assert "questions about Janus itself" in prompt


# --- chat level --------------------------------------------------------------


#: Tool results the scripted model saw on its last turn.
_SEEN_TOOL_OUTPUTS: list[str] = []


class _ScriptedToolModel(BaseChatModel):
    """A fake chat model that calls search_docs, then read_doc, then answers from the tool output."""

    @property
    def _llm_type(self) -> str:
        return "scripted-docs"

    def bind_tools(self, tools, **_kwargs):
        return self

    def _generate(self, messages: list[BaseMessage], stop=None, run_manager=None, **_kwargs) -> ChatResult:
        tool_outputs = [message for message in messages if isinstance(message, ToolMessage)]
        _SEEN_TOOL_OUTPUTS[:] = [str(message.content) for message in tool_outputs]
        if not tool_outputs:
            call = {"name": "search_docs", "args": {"query": "unattended switch"}, "id": "call-1"}
            message = AIMessage(content="", tool_calls=[call])
        elif len(tool_outputs) == 1:
            hit = json.loads(tool_outputs[0].content)["results"][0]
            call = {"name": "read_doc", "args": {"path": hit["path"], "section": hit["section"]}, "id": "call-2"}
            message = AIMessage(content="", tool_calls=[call])
        else:
            message = AIMessage(content="According to the docs, each tool has an Unattended switch.")
        return ChatResult(generations=[ChatGeneration(message=message)])


def test_chat_how_do_i_question_calls_search_docs_and_read_doc(auth_client, db):
    """A "how do I ..." chat turn runs the real docs tools through /ws/chat with a fake LLM."""
    service = object.__new__(llm_service.LLMService)
    service.llm = _ScriptedToolModel()
    chat_llm = MagicMock()
    chat_llm.stream_agent_chat = service.stream_agent_chat

    with patch("janus.main.llm_service.LLMService", return_value=chat_llm):  # noqa: SIM117
        with auth_client.websocket_connect("/ws/chat") as ws:
            ws.send_text("How do I stop Janus from sending mail in triggers?")
            messages = []
            while True:
                message = ws.receive_text()
                messages.append(message)
                if message in {"__END__"} or message.startswith("__ERROR__"):
                    break
            ws.close()

    selection = json.loads(next(m for m in messages if m.startswith("__TOOL_SELECTION__:")).split(":", 1)[1])
    assert "docs" in {tool["id"] for tool in selection["tools"]}
    assert "__TOOL_CALL__:search_docs" in messages
    assert "__TOOL_CALL__:read_doc" in messages
    assert not any(m.startswith("__ERROR__") for m in messages)
    search_output, read_output = _SEEN_TOOL_OUTPUTS
    assert "results" in search_output
    assert "Unattended" in read_output
    # First-party docs are not wrapped as untrusted content (JAN-127).
    assert "<untrusted" not in read_output.lower()


def test_tools_page_is_in_the_nav():
    """The tools reference page is reachable from the docs site navigation."""
    assert any(path == "tools.md" for path in docs_service._nav_paths(docs_service.load_nav()))
    assert Path(docs_service.DOCS_DIR / "tools.md").is_file()
