"""Tests for the autonomous self-evolution loop and its code-enforced gates (JAN-320..324)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from janus import database, models, notifications, security, tool_policy, trigger_service
from janus.agent_tools import DEFAULT_TOOL_REGISTRY
from janus.capability_profile import user_connected_capabilities
from janus.services import self_evolution_checks as checks
from janus.services import self_evolution_intake as intake
from janus.services import self_evolution_merge as merging
from janus.services import self_evolution_provenance as provenance
from janus.services import self_evolution_service as service
from janus.services.github_service import GitHubService
from janus.services.linear_service import LinearService
from janus.services.self_evolution_service import Action, SelfEvolutionError, Status
from janus.trigger_runtime import build_runtime_trigger_handler

REPO = "acme/janus"
PROD = "https://janus.example.org"
TRIGGER = "trig_01ABCdef"
TOKEN = "sk-ant-oat01-routine-secret"
#: The Claude Code session of the job the ``owner`` fixture started (two days ago).
SEED_SESSION = "session_01SeedSessionAbc"
SEED_URL = "https://claude.ai/code/session_01SeedSessionAbc"
FIRE_URL = f"https://api.anthropic.com/v1/claude_code/routines/{TRIGGER}/fire"


# --- fixtures -------------------------------------------------------------------


@pytest.fixture
def session_local(testing_session_local, monkeypatch):
    """Route event-log writes and trigger runs to the test database."""
    monkeypatch.setattr(database, "SessionLocal", testing_session_local)
    return testing_session_local


def _user(db, username="aurelius-owner", *, is_admin=True) -> models.User:
    user = models.User(
        username=username,
        hashed_password=security.security_service.get_password_hash("secret"),
        is_admin=is_admin,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _configure(db, user, *, enabled=True, **fields) -> models.User:
    settings = db.query(models.Settings).first() or models.Settings()
    settings.github_token = security.security_service.encrypt("gh-token")
    settings.github_default_repo = REPO
    settings.linear_api_key = security.security_service.encrypt("lin_api_key")
    settings.linear_default_team_id = "JAN"
    db.add(settings)
    db.commit()
    values = {
        "routine_trigger_id": TRIGGER,
        "routine_token": TOKEN,
        "production_base_url": PROD,
        **fields,
    }
    service.update_settings(db, user, enabled=enabled, **values)
    return user


@pytest.fixture
def owner(db, session_local):
    """An admin with the loop configured and on, who started job JAN-1000 (session SEED_SESSION) two days ago."""
    user = _configure(db, _user(db))
    row = service.record(
        db,
        user.id,
        Action.JOB_STARTED,
        Status.OK,
        subject="JAN-1000",
        data={"session_id": SEED_SESSION, "session_url": SEED_URL, "repo": REPO},
    )
    row.created_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=2)
    db.commit()
    return user


class FakeLinear:
    """In-memory Linear API behind ``httpx.MockTransport``."""

    def __init__(self, open_titles=()):
        """Start empty."""
        self.issues = [
            {"id": f"id-{n}", "identifier": f"JAN-{n}", "title": title, "url": f"https://linear.app/i/{n}"}
            for n, title in enumerate(open_titles, start=1)
        ]
        self.created: list[dict[str, Any]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        """Answer one API request."""
        body = json.loads(request.content)
        query = body["query"]
        if "TeamLookup" in query:
            nodes = [{"id": "team-uuid", "key": "JAN", "name": "Janus"}]
            if body["variables"]["ref"].casefold() not in {"jan", "janus"}:
                nodes = []
            return httpx.Response(200, json={"data": {"teams": {"nodes": nodes}}})
        if "query Teams" in query:
            return httpx.Response(200, json={"data": {"teams": {"nodes": [{"id": "team-uuid", "key": "JAN"}]}}})
        if "TeamIssues" in query:
            nodes = [{**issue, "state": {"name": "Todo", "type": "unstarted"}} for issue in self.issues]
            return httpx.Response(200, json={"data": {"team": {"issues": {"nodes": nodes}}}})
        if "IssueCreate" in query:
            n = len(self.issues) + 100
            issue = {"id": f"id-{n}", "identifier": f"JAN-{n}", "title": body["variables"]["input"]["title"]}
            issue["url"] = f"https://linear.app/i/{n}"
            issue["description"] = body["variables"]["input"]["description"]
            self.issues.append(issue)
            self.created.append(issue)
            return httpx.Response(200, json={"data": {"issueCreate": {"success": True, "issue": issue}}})
        raise AssertionError(query)


class FakeGitHub:
    """In-memory GitHub REST/GraphQL API behind ``httpx.MockTransport``."""

    def __init__(self):
        """Start empty."""
        self.pulls: dict[int, dict[str, Any]] = {}
        self.files: dict[int, list[dict[str, Any]]] = {}
        self.check_runs: dict[str, list[dict[str, Any]]] = {}
        self.statuses: dict[str, list[dict[str, Any]]] = {}
        self.compare_status = "ahead"
        self.merged: list[dict[str, Any]] = []
        self.reverts: list[dict[str, Any]] = []
        self.commits: dict[str, dict[str, Any]] = {}

    def add_pr(self, number, *, files=("src/janus/feature.py",), ref=None, checks=("success",), **overrides):
        """Add an open, green, mergeable pull request (``overrides`` change any field)."""
        sha = f"sha-{number}"
        self.pulls[number] = {
            "number": number,
            "title": f"PR {number}",
            "html_url": f"https://github.com/{REPO}/pull/{number}",
            "node_id": f"PR_node_{number}",
            "state": "open",
            "merged": False,
            "draft": False,
            "mergeable": True,
            "mergeable_state": "clean",
            "changed_files": len(files),
            "head": {"sha": sha, "ref": ref or f"feature-{number}", "repo": {"full_name": REPO}},
            "base": {"ref": "main", "repo": {"full_name": REPO}},
            "user": {"login": "janus-bot"},
            "commits": 1,
            "body": f"Implements JAN-1000.\n\nhttps://claude.ai/code/{SEED_SESSION}",
            **overrides,
        }
        self.files[number] = [{"filename": name} for name in files]
        self.check_runs[sha] = [
            {"name": f"ci-{i}", "status": "completed" if c != "pending" else "in_progress", "conclusion": c}
            for i, c in enumerate(checks)
        ]
        self.statuses[sha] = []
        return self.pulls[number]

    def handler(self, request: httpx.Request) -> httpx.Response:
        """Answer one API request."""
        path = request.url.path
        if path == "/graphql":
            variables = json.loads(request.content)["variables"]["input"]
            self.reverts.append(variables)
            data = {"revertPullRequest": {"revertPullRequest": {"number": 999, "url": "https://gh/pull/999"}}}
            return httpx.Response(200, json={"data": data})
        if path == f"/repos/{REPO}":
            return httpx.Response(200, json={"default_branch": "main"})
        parts = path.removeprefix(f"/repos/{REPO}/").split("/")
        if parts[0] == "pulls":
            number = int(parts[1])
            if len(parts) == 2:
                return httpx.Response(200, json=self.pulls[number])
            if parts[2] == "files":
                return httpx.Response(200, json=self.files[number])
            if parts[2] == "merge":
                body = json.loads(request.content)
                pr = self.pulls[number]
                assert body["sha"] == pr["head"]["sha"]
                pr.update(state="closed", merged=True, merge_commit_sha=f"merge-{number}")
                self.merged.append({"number": number, **body})
                return httpx.Response(200, json={"merged": True, "sha": f"merge-{number}", "message": "ok"})
        if parts[0] == "commits":
            sha = parts[1]
            if len(parts) == 2:
                return httpx.Response(200, json=self.commits[sha])
            if parts[2] == "check-runs":
                return httpx.Response(200, json={"check_runs": self.check_runs.get(sha, [])})
            if parts[2] == "status":
                return httpx.Response(200, json={"statuses": self.statuses.get(sha, [])})
        if parts[0] == "compare":
            return httpx.Response(200, json={"status": self.compare_status})
        raise AssertionError(path)


@pytest.fixture
def linear_api(monkeypatch):
    """A fake Linear workspace wired into the service."""
    fake = FakeLinear()
    monkeypatch.setattr(
        service, "linear_factory", lambda key: LinearService(key, transport=httpx.MockTransport(fake.handler))
    )
    return fake


@pytest.fixture
def github_api(monkeypatch):
    """A fake GitHub repository wired into the service."""
    fake = FakeGitHub()
    monkeypatch.setattr(
        service, "github_factory", lambda token: GitHubService(token, transport=httpx.MockTransport(fake.handler))
    )
    return fake


def _fired(request: httpx.Request) -> httpx.Response:
    """A successful Routine fire answer, with a session per call."""
    n = len(getattr(_fired, "seen", []))
    _fired.seen = [*getattr(_fired, "seen", []), n]  # type: ignore[attr-defined]
    return httpx.Response(
        200,
        json={
            "type": "routine_fire",
            "claude_code_session_id": f"session_01Fired{n:04d}x",
            "claude_code_session_url": f"https://claude.ai/code/session_01Fired{n:04d}x",
        },
    )


class _Calls(list):
    """Recorded requests plus a mutable ``state["responder"]``."""

    state: dict[str, Any]


@pytest.fixture
def http_calls(monkeypatch):
    """Capture outbound Routine/production calls; ``responder`` decides the answer."""
    calls = _Calls()
    state = {"responder": _fired}

    def handler(request):
        calls.append(request)
        return state["responder"](request)

    monkeypatch.setattr(
        service, "http_client", lambda timeout: httpx.Client(transport=httpx.MockTransport(handler), timeout=timeout)
    )
    calls.state = state
    return calls


def _actions(db, action: Action) -> list[models.SelfEvolutionAction]:
    db.expire_all()
    return (
        db.query(models.SelfEvolutionAction)
        .filter_by(action=action.value)
        .order_by(models.SelfEvolutionAction.id)
        .all()
    )


# --- scope fence -------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "src/janus/security.py",
        "src/janus/services/auth_service.py",
        "src/janus/tool_policy.py",
        "src/janus/prompt_injection.py",
        "src/janus/services/injection_classifier.py",
        ".github/workflows/ci.yml",
        "alembic/versions/abc_new.py",
        "scripts/janus-up.sh",
        "src/janus/services/self_evolution_merge.py",
        "frontend/src/components/features/SelfEvolutionConfig.tsx",
        "docs/docs/feature-specs/autonomous-self-evolution-loop.md",
        "SRC/Janus/Security.py",
    ],
)
def test_fence_covers_sensitive_paths(path):
    """Auth, tool policy, injection guard, CI/deploy, migrations and the loop itself are fenced."""
    assert service.fenced_reason(path) is not None


@pytest.mark.parametrize("path", ["src/janus/agent_toolboxes/github.py", "docs/docs/tools.md", "README.md"])
def test_fence_leaves_ordinary_paths_open(path):
    """Ordinary code and docs are not fenced."""
    assert service.fenced_reason(path) is None


# --- switch ---------------------------------------------------------------------------


def test_switch_is_off_by_default_and_refuses_everything(db, session_local, linear_api, github_api):
    """Without the opt-in, every entry point refuses and nothing reaches Linear or GitHub."""
    user = _configure(db, _user(db), enabled=False)
    calls = [
        lambda: intake.file_issue(db, user, kind="capability_gap", title="Cannot read PDFs", description="x" * 50),
        lambda: intake.start_job(db, user, issue_identifier="JAN-1", prompt="p" * 200),
        lambda: merging.merge_feature_pr(db, user, 1),
        lambda: merging.merge_release_pr(db, user, 2, 1),
        lambda: checks.schedule_self_check(db, user, release_pull_number=2, checks=[{"path": "/"}]),
    ]
    for call in calls:
        with pytest.raises(service.SelfEvolutionDisabledError):
            call()
    assert linear_api.created == [] and github_api.merged == []
    assert "self_evolution" not in user_connected_capabilities(db, user)


def test_a_non_admin_can_never_switch_it_on(db, session_local):
    """The loop acts with instance-wide credentials, so only an admin's switch counts."""
    admin = _configure(db, _user(db))
    plain = _user(db, "plain", is_admin=False)
    with pytest.raises(PermissionError):
        service.update_settings(db, plain, enabled=True)
    db.add(models.SelfEvolutionSettings(user_id=plain.id, enabled=True))
    db.commit()
    assert not service.is_enabled(db, plain)
    assert service.is_enabled(db, admin)


def test_settings_encrypt_the_token_and_headers(db, owner):
    """The Routine token is Fernet-encrypted at rest and never described."""
    row = service.get_settings(db, owner.id)
    assert row.routine_token != TOKEN
    assert security.security_service.decrypt(row.routine_token) == TOKEN
    view = service.describe_settings(db, owner)
    assert view["routine_token_configured"] is True and view["routine_trigger_id"] == TRIGGER
    assert TOKEN not in json.dumps(view)


@pytest.mark.parametrize(
    "changes",
    [
        {"routine_trigger_id": "https://evil.example.com/fire"},
        {"routine_trigger_id": "trig_abc/../../v1/messages"},
        {"routine_token": "Bearer something-else"},
        {"production_base_url": "https://user:pw@example.com"},
        {"production_base_url": "http://insecure.example.com"},
        {"github_repo": "not a repo"},
    ],
)
def test_settings_validation(db, owner, changes):
    """A trig_ id (never a URL), a routine token, https without credentials, owner/name repos."""
    with pytest.raises(ValueError):
        service.update_settings(db, owner, **changes)


def test_tool_policy_follows_the_switch(db, owner):
    """The entry is allowed unattended exactly when the switch is on, and can't be flipped on the Tools page."""
    entries = DEFAULT_TOOL_REGISTRY.entries()
    assert "self_evolution" in tool_policy.unattended_allowed_tool_ids(db, owner.id, entries)
    assert "self_evolution" in user_connected_capabilities(db, owner)
    rows = {row["tool_id"]: row for row in tool_policy.describe_policies(db, owner.id, entries)}
    assert rows["self_evolution"]["unattended_allowed"] is True
    assert rows["self_evolution"]["locked_reason"] is None, "selectable on a trigger definition while on"
    with pytest.raises(tool_policy.LockedToolError):
        tool_policy.set_policy(db, owner.id, "self_evolution", True)

    service.update_settings(db, owner, enabled=False)
    assert "self_evolution" not in tool_policy.unattended_allowed_tool_ids(db, owner.id, entries)
    assert "self_evolution" not in user_connected_capabilities(db, owner)
    rows = {row["tool_id"]: row for row in tool_policy.describe_policies(db, owner.id, entries)}
    assert rows["self_evolution"]["locked_reason"] == tool_policy.SWITCH_GOVERNED["self_evolution"]
    definition = models.TriggerDefinition(
        user_id=owner.id,
        name="d",
        objective="o",
        tool_ids=["self_evolution"],
        origin=models.TriggerDefinitionOrigin.USER.value,
    )
    _offered, confirmed = tool_policy.unattended_tool_sets(
        db, owner.id, entries, task_mode="automation", definition=definition
    )
    assert "self_evolution" not in confirmed, "a definition naming it never overrides the switch"


# --- JAN-324: file issue -------------------------------------------------------------


def test_file_issue_files_once_and_records_it(db, owner, linear_api):
    """A new issue is filed with a kind prefix and an autonomy footer, recorded and logged."""
    result = intake.file_issue(
        db, owner, kind="capability_gap", title="Cannot read PDF attachments", description="d" * 60
    )
    assert result["filed"] is True
    created = linear_api.created[0]
    assert created["title"] == "Capability gap: Cannot read PDF attachments"
    assert "No human reviewed this issue" in created["description"]
    (row,) = _actions(db, Action.ISSUE_FILED)
    assert row.status == Status.OK.value and row.subject == created["identifier"]
    event = db.query(models.Event).filter_by(source=service.LOG_SOURCE, user_id=owner.id).all()
    assert any("issue_filed ok" in item.message for item in event)


def test_file_issue_resolves_default_team_by_name(db, owner, linear_api):
    """A default team configured by its name resolves like a key (JAN-330); the gate itself is unchanged."""
    settings = db.query(models.Settings).first()
    settings.linear_default_team_id = "janus"
    db.commit()
    result = intake.file_issue(
        db, owner, kind="capability_gap", title="Cannot read PDF attachments", description="d" * 60
    )
    assert result["filed"] is True


def test_file_issue_unknown_default_team_is_refused_with_keys(db, owner, linear_api):
    """An unusable default team refuses with the available keys and files nothing (JAN-330)."""
    settings = db.query(models.Settings).first()
    settings.linear_default_team_id = "NOPE"
    db.commit()
    with pytest.raises(SelfEvolutionError, match="Available team keys: JAN"):
        intake.file_issue(db, owner, kind="capability_gap", title="Cannot read PDF attachments", description="d" * 60)
    assert linear_api.created == []


def test_linear_still_needs_a_configured_default_team(db, owner):
    """The single-team fallback of the chat tools does not loosen the loop's configuration gate."""
    settings = db.query(models.Settings).first()
    settings.linear_default_team_id = None
    db.commit()
    with pytest.raises(SelfEvolutionError, match="default team"):
        service.linear(db)


def test_file_issue_dedupes_against_open_issues(db, owner, linear_api):
    """An open issue with a similar title is returned instead of a new one."""
    linear_api.issues.append(
        {"id": "x", "identifier": "JAN-7", "title": "Capability gap: cannot read PDF attachment", "url": "u"}
    )
    result = intake.file_issue(
        db, owner, kind="capability_gap", title="Cannot read PDF attachments", description="d" * 60
    )
    assert result == {
        "filed": False,
        "duplicate_of": {"identifier": "JAN-7", "title": linear_api.issues[0]["title"], "url": "u"},
    }
    assert linear_api.created == []
    assert [row.status for row in _actions(db, Action.ISSUE_FILED)] == [Status.DUPLICATE.value]


def test_file_issue_daily_cap(db, owner, linear_api):
    """The sixth distinct issue in a day is refused; refusals and duplicates do not use up the cap."""
    titles = [
        "Mail search lacks date filters",
        "Calendar sync drops recurring events",
        "Briefing ignores holidays",
        "WhatsApp voice notes unsupported",
        "Topics never merge duplicates",
        "Wiki lacks page history export",
    ]
    for title in titles[:5]:
        assert intake.file_issue(db, owner, kind="capability_gap", title=title, description="d" * 60)["filed"]
    with pytest.raises(SelfEvolutionError, match="daily cap of 5"):
        intake.file_issue(db, owner, kind="capability_gap", title=titles[5], description="d" * 60)
    assert len(linear_api.created) == 5


# --- JAN-323: start job --------------------------------------------------------------


def test_start_job_fires_the_routine_api_trigger(db, owner, http_calls):
    """POST .../routines/{trig_id}/fire with the routine token, version and beta headers; session recorded."""
    result = intake.start_job(db, owner, issue_identifier="jan-42", prompt="Implement it. " * 20)
    (request,) = http_calls
    assert str(request.url) == FIRE_URL
    assert request.headers["authorization"] == f"Bearer {TOKEN}"
    assert request.headers["anthropic-version"] == "2023-06-01"
    assert request.headers["anthropic-beta"] == "experimental-cc-routine-2026-04-01"
    assert request.headers["content-type"] == "application/json"
    body = json.loads(request.content)
    assert list(body) == ["text"]
    text = body["text"]
    assert text.startswith(f"Implement Linear issue JAN-42 in the GitHub repository {REPO}.")
    assert "MUST contain the Linear issue id JAN-42 and the URL of this Claude Code session" in text
    assert "src/janus/tool_policy.py" in text and "Implement it." in text
    assert result["started"] is True and result["session_id"].startswith("session_01Fired")
    row = [row for row in _actions(db, Action.JOB_STARTED) if row.subject == "JAN-42"][0]
    assert row.status == Status.OK.value
    assert row.data["session_id"] == result["session_id"]
    assert row.data["session_url"] == result["session_url"]
    assert TOKEN not in json.dumps(row.data) and TOKEN not in (row.detail or "")


def test_job_text_is_bounded_to_the_payload_limit():
    """The fire text never exceeds 65,536 characters; an over-long brief is cut with a marker."""
    text = intake.build_job_text("JAN-1", "x" * 70_000, REPO)
    assert len(text) <= intake.MAX_ROUTINE_TEXT_CHARS
    assert text.endswith("[brief truncated to the routine payload limit]")


@pytest.mark.parametrize(
    ("status", "error_type", "retry"),
    [
        (400, "invalid_request_error", False),
        (401, "authentication_error", False),
        (429, "rate_limit_error", True),
        (503, "overloaded_error", True),
    ],
)
def test_start_job_records_api_errors_and_never_retries(db, owner, http_calls, status, error_type, retry):
    """An error answer is recorded with its type and message (never the token) and not retried."""
    headers = {"retry-after": "120"} if status == 429 else {}
    http_calls.state["responder"] = lambda request: httpx.Response(
        status, headers=headers, json={"type": "error", "error": {"type": error_type, "message": "nope"}}
    )
    with pytest.raises(SelfEvolutionError) as raised:
        intake.start_job(db, owner, issue_identifier="JAN-5", prompt="p" * 200)
    assert len(http_calls) == 1
    assert ("Try again later" in str(raised.value)) is retry
    row = [row for row in _actions(db, Action.JOB_STARTED) if row.subject == "JAN-5"][0]
    assert row.status == Status.FAILED.value
    assert row.data["error_type"] == error_type and row.data["error_message"] == "nope"
    assert (row.data.get("retry_after") == "120") is (status == 429)
    assert TOKEN not in json.dumps(row.data)


def test_start_job_caps_dedupes_and_records_failures(db, owner, http_calls):
    """One job per issue per day, three jobs per day; a failed call counts and is recorded."""
    intake.start_job(db, owner, issue_identifier="JAN-1", prompt="p" * 200)
    with pytest.raises(SelfEvolutionError, match="already started"):
        intake.start_job(db, owner, issue_identifier="JAN-1", prompt="p" * 200)
    http_calls.state["responder"] = lambda request: httpx.Response(500, json={"error": "boom"})
    with pytest.raises(SelfEvolutionError, match="HTTP 500"):
        intake.start_job(db, owner, issue_identifier="JAN-2", prompt="p" * 200)
    http_calls.state["responder"] = _fired
    intake.start_job(db, owner, issue_identifier="JAN-3", prompt="p" * 200)
    with pytest.raises(SelfEvolutionError, match="daily cap of 3"):
        intake.start_job(db, owner, issue_identifier="JAN-4", prompt="p" * 200)
    assert len(http_calls) == 3
    statuses = [row.status for row in _actions(db, Action.JOB_STARTED) if row.subject != "JAN-1000"]
    assert statuses == ["ok", "refused", "failed", "ok", "refused"]


def test_start_job_needs_an_endpoint(db, session_local, http_calls):
    """Without a configured Routine endpoint nothing is sent."""
    user = _configure(db, _user(db), routine_trigger_id="", routine_token="")
    with pytest.raises(SelfEvolutionError, match="no Claude Code Routine trigger id"):
        intake.start_job(db, user, issue_identifier="JAN-1", prompt="p" * 200)
    assert http_calls == []


# --- JAN-322: merge gates -------------------------------------------------------------


@pytest.mark.parametrize(
    ("setup", "reason"),
    [
        (lambda gh: gh.add_pr(1, checks=("success", "pending")), "CI still running"),
        (lambda gh: gh.add_pr(1, checks=("success", "failure")), "CI failed"),
        (lambda gh: gh.add_pr(1, checks=()), "no CI check"),
        (lambda gh: gh.add_pr(1, files=("src/janus/x.py", "src/janus/security.py")), "fenced paths"),
        (lambda gh: gh.add_pr(1, files=(".github/workflows/ci.yml",)), "fenced paths"),
        (lambda gh: gh.add_pr(1, files=("src/janus/services/self_evolution_service.py",)), "self-evolution loop"),
        (lambda gh: gh.add_pr(1, mergeable=None), "has not confirmed"),
        (lambda gh: gh.add_pr(1, mergeable_state="blocked"), "merge state"),
        (lambda gh: gh.add_pr(1, draft=True), "draft"),
        (lambda gh: gh.add_pr(1, base={"ref": "develop", "repo": {"full_name": REPO}}), "default branch"),
        (lambda gh: gh.add_pr(1, head={"sha": "sha-1", "ref": "x", "repo": {"full_name": "evil/fork"}}), "forks"),
        (lambda gh: gh.add_pr(1, ref="release-please--branches--main"), "is a release-please"),
    ],
)
def test_merge_feature_pr_gates(db, owner, github_api, setup, reason):
    """Each failed gate refuses the merge, is recorded, and does not use up the cap."""
    setup(github_api)
    with pytest.raises(SelfEvolutionError, match=reason):
        merging.merge_feature_pr(db, owner, 1)
    assert github_api.merged == []
    (row,) = _actions(db, Action.FEATURE_MERGED)
    assert row.status == Status.REFUSED.value and reason in row.detail


def test_merge_feature_pr_checks_renamed_files_and_statuses(db, owner, github_api):
    """A rename out of a fenced path and a failing commit status are both caught."""
    github_api.add_pr(1)
    github_api.files[1] = [{"filename": "src/janus/moved.py", "previous_filename": "src/janus/tool_policy.py"}]
    github_api.statuses["sha-1"] = [{"context": "external", "state": "error"}]
    with pytest.raises(SelfEvolutionError) as raised:
        merging.merge_feature_pr(db, owner, 1)
    assert "tool_policy.py" in str(raised.value) and "external" in str(raised.value)


def test_merge_feature_pr_merges_pinned_head_and_caps(db, owner, github_api):
    """A green PR is squash-merged pinned to the checked head; the fourth merge of the day is refused."""
    for number in (1, 2, 3, 4):
        github_api.add_pr(number, checks=("success", "skipped"))
    for number in (1, 2, 3):
        result = merging.merge_feature_pr(db, owner, number)
        assert result["merged"] is True and result["merge_sha"] == f"merge-{number}"
    assert github_api.merged[0] == {"number": 1, "merge_method": "squash", "sha": "sha-1"}
    with pytest.raises(SelfEvolutionError, match="daily cap of 3"):
        merging.merge_feature_pr(db, owner, 4)
    assert len(github_api.merged) == 3


def _release(gh, number=2, **kwargs):
    return gh.add_pr(
        number,
        ref="release-please--branches--main",
        files=("CHANGELOG.md", "pyproject.toml", "uv.lock", ".release-please-manifest.json"),
        **kwargs,
    )


def test_release_waits_for_the_loops_feature_merge(db, owner, github_api):
    """The release PR is refused until the loop itself merged the feature PR."""
    github_api.add_pr(1)
    _release(github_api)
    with pytest.raises(SelfEvolutionError, match="was not merged by the self-evolution loop"):
        merging.merge_release_pr(db, owner, 2, 1)
    assert github_api.merged == []


def test_release_must_contain_the_feature_merge(db, owner, github_api):
    """A release PR not yet regenerated after the feature merge is refused."""
    github_api.add_pr(1)
    _release(github_api)
    merging.merge_feature_pr(db, owner, 1)
    github_api.compare_status = "diverged"
    with pytest.raises(SelfEvolutionError, match="does not contain the merge of #1"):
        merging.merge_release_pr(db, owner, 2, 1)
    assert [m["number"] for m in github_api.merged] == [1]


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"checks": ("pending",)}, "CI still running"),
        ({"checks": ("failure",)}, "CI failed"),
    ],
)
def test_release_checks_must_be_green(db, owner, github_api, kwargs, reason):
    """Checks that exist on the release PR must all have passed."""
    github_api.add_pr(1)
    _release(github_api, **kwargs)
    merging.merge_feature_pr(db, owner, 1)
    with pytest.raises(SelfEvolutionError, match=reason):
        merging.merge_release_pr(db, owner, 2, 1)


def test_release_may_only_touch_release_files(db, owner, github_api):
    """A release PR changing code is refused."""
    github_api.add_pr(1)
    github_api.add_pr(2, ref="release-please--branches--main", files=("CHANGELOG.md", "src/janus/main.py"))
    merging.merge_feature_pr(db, owner, 1)
    with pytest.raises(SelfEvolutionError, match="only change release files"):
        merging.merge_release_pr(db, owner, 2, 1)


def test_release_merges_after_feature_even_without_checks(db, owner, github_api):
    """release-please PRs get no workflow runs, so no checks at all is accepted; then it is merged once."""
    github_api.add_pr(1)
    _release(github_api, checks=())
    merging.merge_feature_pr(db, owner, 1)
    result = merging.merge_release_pr(db, owner, 2, 1)
    assert result["merged"] is True
    assert [m["number"] for m in github_api.merged] == [1, 2]
    with pytest.raises(SelfEvolutionError):
        merging.merge_release_pr(db, owner, 2, 1)


# --- JAN-321: self-check ------------------------------------------------------------------


def _released(db, owner, github_api):
    github_api.add_pr(1)
    _release(github_api, checks=())
    merging.merge_feature_pr(db, owner, 1)
    merging.merge_release_pr(db, owner, 2, 1)


def test_self_check_needs_a_release_the_loop_merged(db, owner):
    """A self-check is only scheduled for a release PR the loop merged."""
    with pytest.raises(SelfEvolutionError, match="only a release PR the loop merged"):
        checks.schedule_self_check(db, owner, release_pull_number=2, checks=[{"path": "/api/status"}])


@pytest.mark.parametrize("path", ["https://evil.example.com/", "//evil.example.com/x", "/a b", "/x@evil"])
def test_self_check_paths_stay_on_the_production_host(db, owner, github_api, path):
    """Check paths are plain absolute paths, never another host."""
    _released(db, owner, github_api)
    with pytest.raises(SelfEvolutionError, match="plain absolute path"):
        checks.schedule_self_check(db, owner, release_pull_number=2, checks=[{"path": path}])


def _run_due(session_local, due_at):
    executor_calls = []

    def executor(*args):
        executor_calls.append(args)
        raise AssertionError("a self-check never calls the model")

    worker = trigger_service.DurableTriggerWorker(
        session_local, build_runtime_trigger_handler(session_local, task_executor=executor)
    )
    results = worker.run_once(now=due_at + timedelta(minutes=1))
    assert executor_calls == []
    return results


def test_self_check_passes_through_the_trigger_worker(db, owner, github_api, http_calls, session_local):
    """The scheduled run executes the checks in code, without a model call, and records the pass."""
    _released(db, owner, github_api)
    http_calls.state["responder"] = lambda request: httpx.Response(200, text='{"status": "ok", "version": "1.8.0"}')
    scheduled = checks.schedule_self_check(
        db, owner, release_pull_number=2, checks=[{"path": "/api/status", "expect_text": "1.8.0"}], delay_minutes=30
    )
    run = db.query(models.RuntimeTrigger).filter_by(trigger_id=scheduled["trigger_id"]).one()
    assert run.definition_id is not None and run.source == checks.SELF_CHECK_SOURCE
    with pytest.raises(SelfEvolutionError, match="already has a self-check"):
        checks.schedule_self_check(db, owner, release_pull_number=2, checks=[{"path": "/"}])

    results = _run_due(session_local, datetime.fromisoformat(scheduled["due_at"]))

    assert [result.status.value for result in results] == ["completed"]
    assert str(http_calls[-1].url) == f"{PROD}/api/status"
    (row,) = [row for row in _actions(db, Action.SELF_CHECK) if row.status != Status.REFUSED.value]
    assert row.status == Status.PASSED.value
    db.expire_all()
    assert "passed" in db.query(models.RuntimeTrigger).filter_by(trigger_id=scheduled["trigger_id"]).one().output


def test_self_check_failure_files_issue_notifies_and_opens_revert(
    db, owner, github_api, linear_api, http_calls, session_local, monkeypatch
):
    """On failure: an alert, a system-error issue (via the filing path) and a revert PR of the feature."""
    alerts = []
    monkeypatch.setattr(notifications.NotificationService, "notify_alert", lambda title, body: alerts.append(title))
    _released(db, owner, github_api)
    http_calls.state["responder"] = lambda request: httpx.Response(502, text="Bad gateway")
    scheduled = checks.schedule_self_check(db, owner, release_pull_number=2, checks=[{"path": "/api/status"}])

    results = _run_due(session_local, datetime.fromisoformat(scheduled["due_at"]))

    assert [result.status.value for result in results] == ["completed"]
    assert alerts == ["Janus: production self-check failed"]
    (issue,) = linear_api.created
    assert issue["title"].startswith("System error: Production self-check failed after release #2")
    assert "HTTP 502" in issue["description"]
    (revert,) = github_api.reverts
    assert revert["pullRequestId"] == "PR_node_1" and revert["title"].startswith("Revert #1")
    (row,) = [row for row in _actions(db, Action.SELF_CHECK) if row.status != Status.REFUSED.value]
    assert row.status == Status.FAILED.value
    assert row.data["failure_handling"]["revert"]["revert_pr"] == 999
    assert [r.status for r in _actions(db, Action.REVERT_OPENED)] == ["ok"]
    errors = db.query(models.Event).filter_by(source=service.LOG_SOURCE, event_type=models.EventType.ERROR).all()
    assert any("self_check failed" in event.message for event in errors)

    # A retried run never re-runs a finished check.
    assert "already ran" in checks.run_self_check(db, owner, row.id)


# --- tools and API -------------------------------------------------------------------------


def test_revert_only_for_a_pr_the_loop_merged(db, owner, github_api):
    """The loop never opens a revert of a PR it did not merge itself."""
    github_api.add_pr(5, merged=True)
    with pytest.raises(SelfEvolutionError, match="only a PR the loop merged"):
        merging.open_revert_pr(db, owner, 5, "test")
    assert github_api.reverts == []


def test_tools_answer_refused_instead_of_raising(db, owner, github_api):
    """A gate refusal reaches the model as ``REFUSED: ...``."""
    from janus.agent_toolboxes.self_evolution import build_self_evolution_tools
    from janus.agent_toolboxes.shared import ToolContext

    github_api.add_pr(1, checks=("failure",))
    tools = {tool.name: tool for tool in build_self_evolution_tools(ToolContext(db=db, user=owner))}
    answer = tools["merge_feature_pr"].invoke({"pull_number": 1})
    assert answer.startswith("REFUSED:") and "CI failed" in answer
    status = tools["self_evolution_status"].invoke({})
    assert status["enabled"] is True and status["remaining_today"]["feature_merged"] == 3


def test_registry_entry_is_admin_high_risk_and_required():
    """The entry is high-risk, confirmation-required (chat asks), needs its capability, and isn't delegable."""
    entry = next(e for e in DEFAULT_TOOL_REGISTRY.entries() if e.id == "self_evolution")
    assert (entry.confirmation, entry.risk_level, entry.operation) == ("required", "high", "write")
    assert entry.required_capabilities == ("self_evolution",)
    assert "self_evolution" not in tool_policy.delegable_tool_ids(frozenset({"self_evolution"}), [entry])


@pytest.fixture
def admin_client(client, db):
    """A client logged in as an admin."""
    _user(db, "admin")
    response = client.post("/api/auth/login", json={"username": "admin", "password": "secret"})
    client.headers[security.CSRF_HEADER_NAME] = response.json()["csrf_token"]
    return client


def test_settings_api_roundtrip(admin_client, db):
    """Admins configure the loop; the token is never echoed."""
    response = admin_client.get("/api/self-evolution/settings")
    assert response.status_code == 200 and response.json()["enabled"] is False
    response = admin_client.patch(
        "/api/self-evolution/settings",
        json={"enabled": True, "routine_trigger_id": TRIGGER, "routine_token": "sk-ant-oat01-tok-123"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is True and body["routine_token_configured"] is True
    assert "tok-123" not in response.text and body["routine_trigger_id"] == TRIGGER
    assert body["daily_caps"]["feature_merged"] == 3
    bad = admin_client.patch("/api/self-evolution/settings", json={"routine_trigger_id": "https://x/fire"})
    assert bad.status_code == 422


def test_settings_api_update_requires_admin(client, db):
    """Non-admins can look but not configure."""
    _user(db, "plain", is_admin=False)
    response = client.post("/api/auth/login", json={"username": "plain", "password": "secret"})
    client.headers[security.CSRF_HEADER_NAME] = response.json()["csrf_token"]
    assert client.get("/api/self-evolution/settings").status_code == 200
    assert client.patch("/api/self-evolution/settings", json={"enabled": True}).status_code == 403


def test_caps_count_a_rolling_day(db, owner, linear_api):
    """Attempts older than a day no longer count."""
    for _ in range(5):
        service.record(db, owner.id, Action.ISSUE_FILED, Status.OK, subject="old")
    db.query(models.SelfEvolutionAction).update(
        {"created_at": datetime.now(UTC).replace(tzinfo=None) - timedelta(days=2)}
    )
    db.commit()
    assert service.usage(db, owner.id)["issue_filed"] == 0
    assert intake.file_issue(
        db, owner, kind="system_error", title="Briefing crashes on empty calendar", description="d" * 60
    )["filed"]


# --- merge scope: job sessions, Dependabot minor/patch, the loop's reverts -------------------------


@pytest.mark.parametrize(
    "body",
    [
        "",
        "A helpful change from a contributor.",
        "https://claude.ai/code/session_01SomebodyElse",
        "https://claude.ai/code/session_01SeedSessionAbcExtra",
    ],
)
def test_only_prs_of_the_loops_own_sessions_are_merged(db, owner, github_api, body):
    """A PR whose description names no session this user's loop started is refused, even when green."""
    github_api.add_pr(1, body=body)
    with pytest.raises(SelfEvolutionError, match="not produced by a job the loop started"):
        merging.merge_feature_pr(db, owner, 1)
    assert github_api.merged == []


def test_a_job_pr_records_its_issue_and_session(db, owner, github_api):
    """The merge row names the job (Linear issue and session) that produced the PR."""
    github_api.add_pr(1, body=f"Fixes JAN-1000 ({SEED_SESSION})")
    merging.merge_feature_pr(db, owner, 1)
    (row,) = [row for row in _actions(db, Action.FEATURE_MERGED) if row.status == "ok"]
    assert row.data["kind"] == "job" and row.data["issue"] == "JAN-1000" and row.data["session_id"] == SEED_SESSION


def test_old_job_sessions_no_longer_qualify(db, owner, github_api):
    """A session started more than two weeks ago no longer vouches for a PR."""
    db.query(models.SelfEvolutionAction).update(
        {"created_at": datetime.now(UTC).replace(tzinfo=None) - timedelta(days=15)}
    )
    db.commit()
    github_api.add_pr(1)
    with pytest.raises(SelfEvolutionError, match="not produced by a job"):
        merging.merge_feature_pr(db, owner, 1)


def test_another_users_session_does_not_qualify(db, owner, github_api, session_local):
    """Sessions are matched per user."""
    other = _configure(db, _user(db, "other-admin"))
    github_api.add_pr(1)
    with pytest.raises(SelfEvolutionError, match="not produced by a job"):
        merging.merge_feature_pr(db, other, 1)


def _dependabot_message(*update_types):
    entries = "".join(
        f"- dependency-name: pkg{index}\n  dependency-version: 1.2.{index}\n  dependency-type: direct:production\n"
        f"  update-type: {update_type}\n  dependency-group: python\n"
        for index, update_type in enumerate(update_types)
    )
    return (
        "chore(deps): bump the python group with 2 updates\n\nBumps pkg0 from 1.2.0 to 1.2.1.\n\n---\n"
        f"updated-dependencies:\n{entries}...\n\nSigned-off-by: dependabot[bot] <support@github.com>"
    )


def _dependabot_pr(github_api, *update_types, message=None, author="dependabot[bot]", **overrides):
    fields = {
        "ref": "dependabot/uv/python-abc123",
        "files": ("pyproject.toml", "uv.lock"),
        "body": "Bumps the python group.",
        "user": {"login": "dependabot[bot]"},
        **overrides,
    }
    pr = github_api.add_pr(3, **fields)
    github_api.commits[pr["head"]["sha"]] = {
        "author": {"login": author},
        "commit": {"message": message if message is not None else _dependabot_message(*update_types)},
    }
    return pr


def test_dependabot_minor_and_patch_group_is_merged(db, owner, github_api):
    """A Dependabot group whose every update is minor or patch is merged and counts as a feature merge."""
    _dependabot_pr(github_api, "version-update:semver-patch", "version-update:semver-minor")
    result = merging.merge_feature_pr(db, owner, 3)
    assert result["merged"] is True
    (row,) = [row for row in _actions(db, Action.FEATURE_MERGED) if row.status == "ok"]
    assert row.data["kind"] == "dependabot" and row.data["dependencies"] == ["pkg0", "pkg1"]
    assert service.usage(db, owner.id)["feature_merged"] == 1


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"update_types": ("version-update:semver-patch", "version-update:semver-major")}, "only minor and patch"),
        ({"message": "chore(deps): bump pkg from 1 to 2"}, "no updated-dependencies metadata"),
        ({"message": "x\n---\nupdated-dependencies:\n...\n"}, "empty or incomplete"),
        ({"message": "x\n---\nupdated-dependencies:\n- dependency-name: a\n...\n"}, "empty or incomplete"),
        ({"update_types": ("version-update:semver-patch",), "author": "mallory"}, "not authored by dependabot"),
        ({"update_types": ("version-update:semver-patch",), "commits": 2}, "single commit"),
        ({"update_types": ("version-update:semver-patch",), "user": {"login": "mallory"}}, "opened by dependabot"),
    ],
)
def test_dependabot_gates(db, owner, github_api, kwargs, reason):
    """Major bumps, missing or broken metadata, foreign commits, and impostors are refused."""
    update_types = kwargs.pop("update_types", ())
    _dependabot_pr(github_api, *update_types, **kwargs)
    with pytest.raises(SelfEvolutionError, match=reason):
        merging.merge_feature_pr(db, owner, 3)
    assert github_api.merged == []


def test_dependabot_prs_still_need_green_ci(db, owner, github_api):
    """The provenance rule adds to the other gates; it never replaces them."""
    _dependabot_pr(github_api, "version-update:semver-patch", checks=("failure",))
    with pytest.raises(SelfEvolutionError, match="CI failed"):
        merging.merge_feature_pr(db, owner, 3)


def test_parse_dependabot_updates():
    """The updated-dependencies block is read entry by entry."""
    updates = provenance.parse_dependabot_updates(
        _dependabot_message("version-update:semver-minor", "version-update:semver-patch")
    )
    assert [(u["dependency-name"], u["update-type"]) for u in updates] == [
        ("pkg0", "version-update:semver-minor"),
        ("pkg1", "version-update:semver-patch"),
    ]


def test_a_revert_the_loop_opened_is_mergeable(db, owner, github_api):
    """A revert PR the loop opened may be merged without a session in its description."""
    service.record(db, owner.id, Action.REVERT_OPENED, Status.OK, subject="#1", data={"revert_pr": 7})
    github_api.add_pr(7, body="Reverts acme/janus#1")
    assert merging.merge_feature_pr(db, owner, 7)["merged"] is True
    (row,) = [row for row in _actions(db, Action.FEATURE_MERGED) if row.status == "ok"]
    assert row.data["kind"] == "revert"


def test_a_session_started_for_another_repo_does_not_vouch(db, owner, github_api):
    """Job provenance is bound to the repository the job was launched for."""
    row = db.query(models.SelfEvolutionAction).filter_by(subject="JAN-1000").one()
    row.data = {**row.data, "repo": "acme/other"}
    db.commit()
    github_api.add_pr(1)
    with pytest.raises(SelfEvolutionError, match="started for acme/other, not acme/janus"):
        merging.merge_feature_pr(db, owner, 1)
    assert github_api.merged == []


@pytest.mark.parametrize(
    "payload",
    [
        {"type": "routine_fire"},
        {"type": "routine_fire", "claude_code_session_id": "session_01Abc"},
        {
            "type": "routine_fire",
            "claude_code_session_id": "sess-1",
            "claude_code_session_url": "https://claude.ai/code/sess-1",
        },
        {
            "type": "routine_fire",
            "claude_code_session_id": "session_01Abc",
            "claude_code_session_url": "https://evil.example.com/code/session_01Abc",
        },
        {
            "type": "routine_fire",
            "claude_code_session_id": "session_01Abc",
            "claude_code_session_url": "https://claude.ai/code/session_01Abc/../x",
        },
    ],
)
def test_a_200_without_a_valid_session_is_a_failed_start(db, owner, http_calls, payload):
    """No valid session id and URL: recorded failed, ``started`` false, and the issue may be retried."""
    http_calls.state["responder"] = lambda request: httpx.Response(200, json=payload)
    result = intake.start_job(db, owner, issue_identifier="JAN-77", prompt="p" * 200)
    assert result["started"] is False and "check claude.ai" in result["reason"]
    (row,) = [row for row in _actions(db, Action.JOB_STARTED) if row.subject == "JAN-77"]
    assert row.status == Status.FAILED.value

    http_calls.state["responder"] = _fired
    retried = intake.start_job(db, owner, issue_identifier="JAN-77", prompt="p" * 200)
    assert retried["started"] is True
    assert [row.status for row in _actions(db, Action.JOB_STARTED) if row.subject == "JAN-77"] == ["failed", "ok"]
