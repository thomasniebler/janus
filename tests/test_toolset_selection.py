"""Brain-selected, task-scoped toolsets approved by policy (JAN-300).

The selection step may only *narrow* what today's fences authorize. These
tests feed it unauthorized ids directly and through (untrusted) model output,
check that they are trimmed, that every failure falls back to the full
authorized set, that the choice is persisted and reused on resume, and that
chat streams it and can widen within the authorized set.
"""

import json
import random
import threading
import time

import pytest
from langchain_core.messages import AIMessage

from janus import config, llm_service, models, security, tool_policy, toolset_selection
from janus import database as db_module
from janus import llm_service as llm_mod
from janus.agent_task_runner import AgentTaskRun
from janus.agent_tools import DEFAULT_TOOL_REGISTRY
from janus.plan_graph import PlanStep, TaskContext
from janus.runtime_adapter import TASK_MODE_ALLOWED_TOOL_IDS, RuntimeAdapter
from janus.services.conversation_service import ConversationService
from janus.toolset_selection import (
    ANCHOR_TOOL_IDS,
    SOURCE_FALLBACK,
    SOURCE_PERSISTED,
    SOURCE_SELECTED,
    SOURCE_UNNARROWED,
    ToolsetChoice,
    ToolsetSelectionError,
    choose_toolset,
    llm_selector,
    narrow,
    parse_selection,
)
from janus.unattended_execution import execute_unattended_step_detailed

ENTRIES = DEFAULT_TOOL_REGISTRY.entries()
ENTRIES_BY_ID = {entry.id: entry for entry in ENTRIES}


def _user(db, username: str, *, is_admin: bool = False) -> models.User:
    """User."""
    user = models.User(username=username, hashed_password="not-a-password", is_admin=is_admin)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _selected(*tool_ids: str, reason: str = "test") -> ToolsetChoice:
    """Selected."""
    return ToolsetChoice(source=SOURCE_SELECTED, reason=reason, tool_ids=tuple(tool_ids), proposed_tool_ids=tool_ids)


class FakeChatModel:
    """A LangChain-like chat model that answers every request with fixed text and counts the calls."""

    def __init__(self, answer: str):
        """Store the canned answer."""
        self.answer = answer
        self.requests: list[list] = []

    def invoke(self, messages):
        """Record the request and answer it."""
        self.requests.append(messages)
        return AIMessage(content=self.answer)


# -- strict parsing of untrusted model output ----------------------------------------


def test_parse_selection_accepts_only_the_strict_schema():
    """Test parse selection accepts only the strict schema."""
    assert parse_selection('{"tool_ids": ["memory", "calendar"], "reason": "Needs facts."}') == (
        ("memory", "calendar"),
        "Needs facts.",
    )
    # One surrounding code fence is tolerated; duplicates collapse; control characters are stripped.
    assert parse_selection('```json\n{"tool_ids": ["memory", "memory"], "reason": "a\\nb"}\n```') == (
        ("memory",),
        "a b",
    )


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "memory, calendar",
        "[]",
        '{"tool_ids": ["memory"]}',
        '{"tool_ids": ["memory"], "reason": "x", "grant": ["mail.send"]}',
        '{"tool_ids": "memory", "reason": "x"}',
        '{"tool_ids": [1], "reason": "x"}',
        '{"tool_ids": [" "], "reason": "x"}',
        '{"tool_ids": ["memory"], "reason": 3}',
        json.dumps({"tool_ids": [f"t{i}" for i in range(41)], "reason": "x"}),
        json.dumps({"tool_ids": ["memory"], "reason": "x" * 5000}),
    ],
)
def test_parse_selection_rejects_anything_else(raw):
    """Test parse selection rejects anything else."""
    with pytest.raises(ToolsetSelectionError):
        parse_selection(raw)


# -- choose_toolset fails safe -------------------------------------------------------

CANDIDATES = tuple(ENTRIES_BY_ID[tool_id] for tool_id in ("core", "memory", "topics", "calendar", "jira", "docs"))


def test_a_selector_error_falls_back_to_the_full_set():
    """Test a selector error falls back to the full set."""

    def broken(_objective, _candidates):
        """Broken."""
        raise RuntimeError("provider down")

    choice = choose_toolset("Plan my day", CANDIDATES, broken, timeout_seconds=5)
    assert choice.source == SOURCE_FALLBACK
    assert choice.tool_ids is None
    assert "RuntimeError" in choice.reason


def test_a_slow_selector_times_out_into_the_full_set():
    """Test a slow selector times out into the full set."""

    def slow(_objective, _candidates):
        """Slow."""
        time.sleep(1)
        return ("memory",), "late"

    choice = choose_toolset("Plan my day", CANDIDATES, slow, timeout_seconds=0.05)
    assert choice.source == SOURCE_FALLBACK
    assert choice.tool_ids is None
    assert "timed out" in choice.reason


@pytest.mark.parametrize("answer", ["not json", '{"tool_ids": [], "reason": "nothing"}', '{"tool_ids": 3}'])
def test_malformed_or_empty_model_output_falls_back_to_the_full_set(answer):
    """Test malformed or empty model output falls back to the full set."""
    choice = choose_toolset("Plan my day", CANDIDATES, llm_selector(FakeChatModel(answer)), timeout_seconds=5)
    assert choice.source == SOURCE_FALLBACK
    assert choice.tool_ids is None


def test_a_selection_while_every_slot_is_hung_falls_back_at_once():
    """Codex P2 on #559: hung provider calls must not make later selections queue behind them."""
    release = threading.Event()

    def hung(_objective, _candidates):
        release.wait(30)
        return ("memory",), "late"

    try:
        for _ in range(toolset_selection.MAX_CONCURRENT_SELECTIONS):
            assert choose_toolset("x", CANDIDATES, hung, timeout_seconds=0.05).source == SOURCE_FALLBACK
        started = time.monotonic()
        choice = choose_toolset("x", CANDIDATES, hung, timeout_seconds=10)
        assert time.monotonic() - started < 1
        assert choice.source == SOURCE_FALLBACK
        assert "busy" in choice.reason
    finally:
        release.set()
    # Once the hung calls end, their slots are free again.
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        choice = choose_toolset("x", CANDIDATES, lambda *_: (("calendar",), "ok"), timeout_seconds=5)
        if choice.source == SOURCE_SELECTED:
            break
        time.sleep(0.05)
    assert choice.source == SOURCE_SELECTED


def test_the_timeout_bounds_one_hung_call_and_its_thread_is_a_daemon():
    """A hung call returns the fallback within the timeout, on a thread that cannot block shutdown."""
    release = threading.Event()
    threads: list[threading.Thread] = []

    def hung(_objective, _candidates):
        threads.append(threading.current_thread())
        release.wait(30)
        return ("memory",), "late"

    try:
        started = time.monotonic()
        choice = choose_toolset("x", CANDIDATES, hung, timeout_seconds=0.2)
        assert time.monotonic() - started < 1
        assert choice.source == SOURCE_FALLBACK and "timed out" in choice.reason
        assert threads and threads[0].daemon
    finally:
        release.set()
        for thread in threads:
            thread.join(5)


@pytest.mark.parametrize(
    ("provider", "extra"),
    [
        ("openai", {"openai_api_key": "k"}),
        ("anthropic", {"anthropic_api_key": "k"}),
        ("ollama", {}),
    ],
)
def test_the_utility_service_bounds_each_provider_request(db, provider, extra):
    """The selector's own client ends a hung request after the timeout, without retries."""
    service = llm_service.LLMService(settings_override={"llm_provider": provider, "request_timeout": 7, **extra}, db=db)
    llm = service.llm
    if provider == "openai":
        assert llm.request_timeout == 7 and llm.max_retries == 0
    elif provider == "anthropic":
        assert llm.default_request_timeout == 7 and llm.max_retries == 0
    else:
        assert llm.client_kwargs["timeout"] == 7
    # Without the override nothing changes for the other surfaces.
    plain = llm_service.LLMService(settings_override={"llm_provider": provider, **extra}, db=db).llm
    if provider == "openai":
        assert plain.max_retries != 0
    elif provider == "ollama":
        assert "timeout" not in (plain.client_kwargs or {})


def test_utility_service_uses_the_utility_model_choice(db, monkeypatch):
    """utility_service builds the configured utility model, adding only the timeout."""
    seen = {}

    class Recorder:
        def __init__(self, settings_override=None, db=None):
            seen.update(settings_override or {})

    monkeypatch.setattr(llm_service, "LLMService", Recorder)
    monkeypatch.setattr(
        llm_service, "model_choice_from_settings", lambda _db, purpose: llm_service.ModelChoice("openai", "mini")
    )
    toolset_selection.utility_service(db, 3)
    assert seen == {"request_timeout": 3, "llm_provider": "openai", "model_override": "mini"}


def test_no_selector_falls_back_and_a_small_set_is_not_narrowed():
    """Test no selector falls back and a small set is not narrowed."""
    assert choose_toolset("x", CANDIDATES, None, timeout_seconds=5).source == SOURCE_FALLBACK

    def must_not_run(_objective, _candidates):
        """Must not run."""
        raise AssertionError("a small candidate set needs no model call")

    small = tuple(ENTRIES_BY_ID[tool_id] for tool_id in ("core", "docs", "memory", "topics", "events"))
    choice = choose_toolset("x", small, must_not_run, timeout_seconds=5)
    assert choice.source == SOURCE_UNNARROWED
    assert choice.tool_ids is None


def test_the_selector_sees_registry_metadata_and_the_task_only_as_data():
    """Test the selector sees registry metadata and the task only as data."""
    model = FakeChatModel('{"tool_ids": ["calendar"], "reason": "Meetings."}')
    choice = choose_toolset("What is on tomorrow?", CANDIDATES, llm_selector(model), timeout_seconds=5)
    assert choice.source == SOURCE_SELECTED
    assert choice.tool_ids == ("calendar",)
    system, human = (message.content for message in model.requests[0])
    assert system.startswith(toolset_selection.SELECTOR_PROMPT_MARKER)
    assert "- calendar: " in system and "- jira: " in system
    assert "- core:" not in system  # anchors are never offered for selection
    assert "<task>\nWhat is on tomorrow?\n</task>" in system
    assert "What is on tomorrow?" not in human


# -- narrow(): the enforcement point ---------------------------------------------------


def test_narrow_trims_unauthorized_ids_and_keeps_anchors():
    """Test narrow trims unauthorized ids and keeps anchors."""
    authorized = tuple(ENTRIES_BY_ID[tool_id] for tool_id in ("core", "memory", "topics", "docs"))
    kept, effective = narrow(_selected("topics", "mail.send", "triggers.propose", "nope"), authorized)
    assert [entry.id for entry in kept] == ["core", "topics", "docs"]
    assert effective.tool_ids == ("core", "topics", "docs")
    assert effective.trimmed_tool_ids == ("mail.send", "triggers.propose", "nope")
    assert effective.narrowed


def test_a_choice_with_no_authorized_id_falls_back_to_the_full_set():
    """Test a choice with no authorized id falls back to the full set."""
    authorized = tuple(ENTRIES_BY_ID[tool_id] for tool_id in ("core", "memory", "topics"))
    kept, effective = narrow(_selected("mail.send", "skills"), authorized)
    assert kept == authorized
    assert effective.source == SOURCE_FALLBACK
    assert effective.tool_ids is None
    assert effective.trimmed_tool_ids == ("mail.send", "skills")


# -- prepare_step(toolset=...) only ever narrows -----------------------------------


def _tool_names(prepared) -> set[str]:
    """Tool names."""
    return {tool.name for tool in prepared.tools}


def _prepare(adapter, mode: str, toolset=None, **policy):
    """Prepare."""
    return adapter.prepare_step(
        task_id=f"{mode}-task",
        objective="Do the thing",
        requested_tool_ids=tuple(entry.id for entry in ENTRIES),
        task_mode=mode,
        granted_capabilities=frozenset({"core", "memory", "topics", "planner.local", "triggers", "skills", "mcp"}),
        toolset=toolset,
        **policy,
    )


def test_an_unauthorized_selection_never_reaches_a_trigger_step(db):
    """triggers.propose and ui_actions are locked out of unattended runs; selecting them changes nothing."""
    user = _user(db, "toolset-trigger")
    offered, confirmed = tool_policy.unattended_tool_sets(db, user.id, ENTRIES, task_mode="trigger")
    adapter = RuntimeAdapter(db, user)
    policy = {
        "allowed_tool_ids": offered,
        "confirmed_tool_ids": confirmed,
        "blocked_tool_ids": offered - confirmed,
        "refusal_stubs": True,
    }
    prepared = _prepare(
        adapter, "trigger", _selected("memory", "triggers.propose", "ui_actions", "images", "bogus"), **policy
    )
    assert {entry.id for entry in prepared.selection.entries} == {"core", "memory", "docs"}
    assert set(prepared.toolset.trimmed_tool_ids) == {"triggers.propose", "ui_actions", "images", "bogus"}
    names = _tool_names(prepared)
    assert "add_memory_fact" in names
    assert not names & {"propose_trigger_definition", "navigate", "generate_image"}
    # Trigger mode still withholds delete_fact from the selected memory toolbox.
    assert "delete_fact" not in names


def test_selection_cannot_bypass_the_confirmation_gate(db):
    """A selected confirmation-required tool that is not confirmed is still withheld."""
    user = _user(db, "toolset-confirm")
    adapter = RuntimeAdapter(db, user)
    offered, confirmed = tool_policy.unattended_tool_sets(db, user.id, ENTRIES, task_mode="trigger")
    assert "planner.local.write" in offered - confirmed
    prepared = _prepare(
        adapter,
        "trigger",
        _selected("planner.local.write", "planner.local"),
        allowed_tool_ids=offered,
        confirmed_tool_ids=confirmed,
        blocked_tool_ids=offered - confirmed,
    )
    assert "planner.local.write" in prepared.awaiting_confirmation_tool_ids
    assert "create_task" not in _tool_names(prepared)
    assert "planner.local.write" not in {entry.id for entry in prepared.selection.entries if entry.id in confirmed}


def test_selection_cannot_widen_a_delegation_grant_or_a_definition_list(db):
    """Test selection cannot widen a delegation grant or a definition list."""
    user = _user(db, "toolset-grant")
    adapter = RuntimeAdapter(db, user)
    offered, confirmed = tool_policy.unattended_tool_sets(
        db, user.id, ENTRIES, task_mode="delegated", narrowed_to=frozenset({"core", "memory", "topics"})
    )
    prepared = _prepare(
        adapter,
        "delegated",
        _selected("topics", "triggers", "delegation", "memory"),
        allowed_tool_ids=offered,
        confirmed_tool_ids=confirmed,
    )
    assert {entry.id for entry in prepared.selection.entries} <= {"core", "topics"}
    assert {"triggers", "delegation", "memory"} <= set(prepared.toolset.trimmed_tool_ids)


def test_chat_selection_stays_inside_the_mode_allowlist_and_connections(db):
    """Test chat selection stays inside the mode allowlist and connections."""
    user = _user(db, "toolset-chat")
    adapter = RuntimeAdapter(db, user)
    prepared = _prepare(
        adapter,
        "chat",
        _selected("calendar", "mail.send", "mail.processing", "topics"),
        confirmed_tool_ids=frozenset(entry.id for entry in ENTRIES if entry.confirmation != "required"),
    )
    # calendar and mail.* are not connected, mail.processing is not on chat's allowlist.
    assert {entry.id for entry in prepared.selection.entries} == {"core", "topics", "docs"}
    assert set(prepared.toolset.trimmed_tool_ids) == {"calendar", "mail.send", "mail.processing"}
    assert {denial.tool_id for denial in prepared.denials} >= {"calendar", "mail.send", "mail.processing"}


@pytest.mark.parametrize("mode", ["chat", "briefing", "trigger", "delegated"])
def test_any_selection_yields_a_subset_of_the_unselected_step(db, mode):
    """The narrowing property: for many selections, built tools and grants only shrink."""
    user = _user(db, f"toolset-subset-{mode}", is_admin=True)
    adapter = RuntimeAdapter(db, user)
    policy: dict = {"refusal_stubs": mode in {"trigger", "delegated"}}
    if mode in {"trigger", "delegated"}:
        offered, confirmed = tool_policy.unattended_tool_sets(db, user.id, ENTRIES, task_mode=mode)
        policy.update(allowed_tool_ids=offered, confirmed_tool_ids=confirmed, blocked_tool_ids=offered - confirmed)
    else:
        policy.update(confirmed_tool_ids=frozenset(entry.id for entry in ENTRIES))
    baseline = _prepare(adapter, mode, **policy)
    base_names = _tool_names(baseline)
    base_ids = {entry.id for entry in baseline.selection.entries}
    ids = [entry.id for entry in ENTRIES] + ["bogus", "mcp_1_x"]
    rng = random.Random(300)
    combos = [(tool_id,) for tool_id in ids] + [tuple(rng.sample(ids, rng.randint(2, 6))) for _ in range(15)]
    for combo in combos:
        prepared = _prepare(adapter, mode, _selected(*combo), **policy)
        assert _tool_names(prepared) <= base_names, combo
        assert {entry.id for entry in prepared.selection.entries} <= base_ids, combo
        assert set(prepared.toolset.tool_ids or ()) <= set(prepared.toolset.authorized_tool_ids), combo


def test_mode_allowlists_are_untouched_by_selection():
    """Selection is a separate input: the per-mode allowlists keep their entries (sanity anchor)."""
    assert TASK_MODE_ALLOWED_TOOL_IDS["chat"] >= ANCHOR_TOOL_IDS
    assert "core" in TASK_MODE_ALLOWED_TOOL_IDS["briefing"]


# -- unattended runs: model output, persistence, resume ------------------------------


class Recorder:
    """Recorder."""

    def __init__(self):
        """Init."""
        self.tool_names: list[list[str]] = []

    def __call__(self, _db, _system_prompt, tools, _objective):
        """Call."""
        self.tool_names.append(sorted(tool.name for tool in tools))
        return AgentTaskRun(output="Done.", tool_calls=())


def _utility_model(monkeypatch, answer: str) -> FakeChatModel:
    """Utility model."""
    model = FakeChatModel(answer)

    class Service:
        """Service."""

        llm = model

    monkeypatch.setattr(toolset_selection, "utility_service", lambda *_a, **_k: Service())
    return model


def _run(db, user, task_id="adhoc-1", step_id=None, executor=None, **kwargs):
    """Run."""
    task = TaskContext(task_id=task_id, objective="Remind me about my open topics", task_mode="trigger")
    step = PlanStep(step_id=step_id or f"{task_id}::step-1", objective=task.objective, **kwargs)
    executor = executor or Recorder()
    outcome = execute_unattended_step_detailed(db, user, task, step, guidance="", task_executor=executor)
    return outcome, executor


def test_an_adhoc_trigger_is_narrowed_by_untrusted_model_output_and_trimmed(db, monkeypatch):
    """Test an adhoc trigger is narrowed by untrusted model output and trimmed."""
    user = _user(db, "toolset-adhoc")
    model = _utility_model(
        monkeypatch,
        '{"tool_ids": ["topics", "triggers.propose", "mail.send", "ui_actions"], "reason": "Topics only."}',
    )
    outcome, executor = _run(db, user)
    assert len(model.requests) == 1
    assert "list_topics" in executor.tool_names[0]
    assert not {"add_memory_fact", "propose_trigger_definition", "navigate", "send_email"} & set(executor.tool_names[0])
    assert "Authorized tools: core, topics, docs" in outcome.result
    assert "Tool selection: selected, 3 of" in outcome.result
    assert "trimmed: triggers.propose, mail.send, ui_actions" in outcome.result
    assert "Why: Topics only." in outcome.result

    row = db.query(models.RuntimeToolSelection).filter_by(user_id=user.id, task_id="adhoc-1").one()
    assert row.source == SOURCE_SELECTED
    assert row.tool_ids == ["core", "topics", "docs"]
    assert row.trimmed_tool_ids == ["triggers.propose", "mail.send", "ui_actions"]
    assert row.proposed_tool_ids == ["topics", "triggers.propose", "mail.send", "ui_actions"]


def test_a_resumed_step_reuses_the_persisted_choice_without_selecting_again(db, monkeypatch):
    """Test a resumed step reuses the persisted choice without selecting again."""
    user = _user(db, "toolset-resume")
    model = _utility_model(monkeypatch, '{"tool_ids": ["topics"], "reason": "Topics."}')
    _first, first = _run(db, user, task_id="resume-1")
    # Retry of the same step, and a continuation step of the same task (after a wait).
    retried, again = _run(db, user, task_id="resume-1")
    continued, cont = _run(db, user, task_id="resume-1", step_id="resume-1::continue")
    assert len(model.requests) == 1
    assert first.tool_names == again.tool_names == cont.tool_names
    assert f"Tool selection: {SOURCE_PERSISTED}" in retried.result
    assert f"Tool selection: {SOURCE_PERSISTED}" in continued.result


def test_a_persisted_choice_is_re_trimmed_when_policy_tightened(db, monkeypatch):
    """Test a persisted choice is re trimmed when policy tightened."""
    user = _user(db, "toolset-tightened")
    _utility_model(monkeypatch, '{"tool_ids": ["topics", "memory"], "reason": "Both."}')
    _run(db, user, task_id="tight-1")
    tool_policy.set_policy(db, user.id, "memory", False)
    outcome, executor = _run(db, user, task_id="tight-1")
    assert "add_memory_fact" not in executor.tool_names[0]
    assert "list_topics" in executor.tool_names[0]


@pytest.mark.parametrize("answer", ["I think topics", '{"tool_ids": [], "reason": ""}', '{"tool_ids": ["nope"]}'])
def test_broken_model_output_runs_the_step_with_the_full_authorized_set(db, monkeypatch, answer):
    """Test broken model output runs the step with the full authorized set."""
    user = _user(db, f"toolset-broken-{len(answer)}")
    monkeypatch.setattr(config.settings, "tool_selection_enabled", False)
    _baseline, base = _run(db, user, task_id="broken-base")
    monkeypatch.setattr(config.settings, "tool_selection_enabled", True)
    _utility_model(monkeypatch, answer)
    outcome, executor = _run(db, user, task_id="broken-1")
    assert executor.tool_names == base.tool_names
    assert "Tool selection: fallback, all" in outcome.result


def test_an_unusable_utility_model_falls_back(db, monkeypatch):
    """Test an unusable utility model falls back."""
    user = _user(db, "toolset-unusable")

    def unusable(*_a, **_k):
        """Unusable."""
        raise llm_service.ModelUnavailableError("no key")

    monkeypatch.setattr(toolset_selection, "utility_service", unusable)
    outcome, _executor = _run(db, user, task_id="unusable-1")
    assert "Tool selection: fallback, all" in outcome.result
    assert "ModelUnavailableError" in outcome.result


def test_definition_runs_and_explicit_requests_are_not_narrowed(db, monkeypatch):
    """Test definition runs and explicit requests are not narrowed."""
    user = _user(db, "toolset-explicit")
    model = _utility_model(monkeypatch, '{"tool_ids": ["topics"], "reason": "x"}')
    outcome, executor = _run(db, user, task_id="explicit-1", required_tool_ids=("core", "memory"))
    assert model.requests == []
    assert "add_memory_fact" in executor.tool_names[0]
    assert "Tool selection" not in outcome.result


def test_selection_can_be_switched_off(db, monkeypatch):
    """Test selection can be switched off."""
    user = _user(db, "toolset-off")
    monkeypatch.setattr(config.settings, "tool_selection_enabled", False)
    model = _utility_model(monkeypatch, '{"tool_ids": ["topics"], "reason": "x"}')
    outcome, _executor = _run(db, user, task_id="off-1")
    assert model.requests == []
    assert "Tool selection" not in outcome.result
    assert db.query(models.RuntimeToolSelection).count() == 0


# -- chat: trace frame, prompt narrowing, widening ------------------------------------


@pytest.fixture
def chat_client(client, db):
    """Chat client."""
    hashed_pw = security.security_service.get_password_hash("pw")
    db.add(models.User(username="toolset-chat-user", hashed_password=hashed_pw, is_admin=False))
    db.commit()
    response = client.post("/api/auth/login", json={"username": "toolset-chat-user", "password": "pw"})
    client.headers[security.CSRF_HEADER_NAME] = response.json()["csrf_token"]
    return client


@pytest.fixture
def chat_models(monkeypatch, testing_session_local):
    """Chat model stand-in that records offered tools and may call request_tools; utility model answers JSON."""
    monkeypatch.setattr(db_module, "SessionLocal", testing_session_local)
    state = {"answer": '{"tool_ids": ["topics"], "reason": "Topics question."}', "offered": [], "widen": None}

    def fake_init(self, **_kwargs):
        """Fake init."""
        self.llm = FakeChatModel(state["answer"])

    async def fake_stream(self, system_prompt, tools, history, user_msg):
        """Fake stream."""
        state["offered"].append(sorted(tool.name for tool in tools))
        if state["widen"] is not None:
            widen = next(tool for tool in tools if tool.name == toolset_selection.WIDEN_TOOL_NAME)
            state["widen_result"] = widen.invoke(state["widen"])
            state["widen"] = None
        yield "ok"

    async def fake_title(self, msg):
        """Fake title."""
        return "Toolsets"

    monkeypatch.setattr(llm_mod.LLMService, "__init__", fake_init)
    monkeypatch.setattr(llm_mod.LLMService, "stream_agent_chat", fake_stream)
    monkeypatch.setattr(llm_mod.LLMService, "generate_conversation_title", fake_title)
    monkeypatch.setattr(llm_mod.LLMService, "suggest_memory_fact", lambda self, *a, **k: None)
    return state


def _turn(ws, text: str) -> dict:
    """Turn."""
    ws.send_text(text)
    selection = None
    while True:
        frame = ws.receive_text()
        if frame.startswith("__TOOL_SELECTION__:"):
            selection = json.loads(frame[len("__TOOL_SELECTION__:") :])
        if frame == "__END__" or frame.startswith("__ERROR__"):
            assert frame == "__END__", frame
            return selection


def test_chat_streams_the_selection_offers_fewer_tools_and_widens_on_request(chat_client, chat_models, db):
    """Test chat streams the selection offers fewer tools and widens on request."""
    with chat_client.websocket_connect("/ws/chat?tool_events=1") as ws:
        first = _turn(ws, "What topics am I tracking?")
        toolset = first["toolset"]
        assert toolset["source"] == SOURCE_SELECTED
        assert toolset["reason"] == "Topics question."
        assert toolset["narrowed"] is True
        assert toolset["tool_ids"] == ["core", "topics", "docs"]
        assert [tool["id"] for tool in first["tools"]] == ["core", "topics", "docs"]
        offered = chat_models["offered"][0]
        assert "list_topics" in offered and "add_memory_fact" not in offered
        assert toolset_selection.WIDEN_TOOL_NAME in offered

        # Janus adds memory (authorized, left out) and tries two ids it may not have.
        chat_models["widen"] = {"tool_ids": ["memory", "mail.send", "nope"], "reason": "User asked."}
        _turn(ws, "Also remember that I like tea.")
        assert "Added memory" in chat_models["widen_result"]
        assert "mail.send, nope" in chat_models["widen_result"]

        third = _turn(ws, "What topics again?")
        assert third["toolset"]["widened"] == ["memory"]
        assert "memory" in third["toolset"]["tool_ids"]
        assert "add_memory_fact" in chat_models["offered"][2]
        assert "send_email" not in chat_models["offered"][2]

    conversation_id = db.query(models.Conversation).one().id
    assert db.query(models.RuntimeToolSelection).filter_by(task_id=f"chat-conversation-{conversation_id}").count() == 3
    user = db.query(models.User).filter_by(username="toolset-chat-user").one()
    assert ConversationService(db).delete_conversation_for_user(conversation_id, user.id)
    assert db.query(models.RuntimeToolSelection).count() == 0


def test_chat_falls_back_to_every_authorized_tool_on_malformed_output(chat_client, chat_models, monkeypatch):
    """Test chat falls back to every authorized tool on malformed output."""
    monkeypatch.setattr(config.settings, "tool_selection_enabled", False)
    with chat_client.websocket_connect("/ws/chat") as ws:
        baseline = _turn(ws, "Hello")
    assert baseline["toolset"] is None
    monkeypatch.setattr(config.settings, "tool_selection_enabled", True)
    chat_models["answer"] = "Sure! I would use topics."
    with chat_client.websocket_connect("/ws/chat") as ws:
        selection = _turn(ws, "Hello")
    assert selection["toolset"]["source"] == SOURCE_FALLBACK
    assert selection["toolset"]["tool_ids"] is None
    assert [tool["id"] for tool in selection["tools"]] == [tool["id"] for tool in baseline["tools"]]
    assert chat_models["offered"][1] == chat_models["offered"][0]
