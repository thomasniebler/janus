"""Linear task tools for agent execution.

Team handling (JAN-330): no team is hardcoded. ``list_linear_teams`` shows the
workspace's teams and which one is the default; every ``team_id`` argument
takes a team UUID, key or exact name, falls back to the configured default
team, and, with no default configured, to the workspace's only team.

Write validation (JAN-332, JAN-333): titles and descriptions are checked
before anything reaches Linear; a refused call creates or changes nothing and
answers ``REFUSED: ...`` so the model can retry.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from langchain_core.tools import BaseTool, tool

from janus import models, security
from janus.agent_toolboxes.shared import ToolContext
from janus.services.linear_service import LinearInputError, LinearService

_COPILOT_CANDIDATE_NAMES = ("GitHub Copilot", "Github Copilot", "Copilot")

TITLE_MIN_CHARS = 5
TITLE_MAX_CHARS = 120
#: Same floor as the self-evolution ``file_improvement_issue`` tool.
DESCRIPTION_MIN_CHARS = 40
DESCRIPTION_MAX_CHARS = 20_000
COMMENT_MAX_CHARS = 10_000
PRIORITY_RANGE = range(0, 5)
#: A title that carries its own "Description: ..." section (JAN-332 evidence).
_INLINE_DESCRIPTION_RE = re.compile(r"\bdescription\s*:", re.IGNORECASE)

Operation = Callable[[LinearService, str | None], Any]


def _load_linear_service(context: ToolContext) -> tuple[LinearService, str | None]:
    """Return a configured Linear service and default team reference."""
    settings = context.db.query(models.Settings).first()
    if settings is None or not settings.linear_api_key:
        raise ValueError("Linear API key is not configured. Add it in Settings → Linear.")
    api_key = security.security_service.decrypt(settings.linear_api_key)
    return LinearService(api_key=api_key), settings.linear_default_team_id


def _configured_default_team(context: ToolContext) -> str | None:
    """Return the configured default team reference without calling Linear."""
    settings = context.db.query(models.Settings).first()
    if settings is None:
        return None
    return (settings.linear_default_team_id or "").strip() or None


def title_problem(title: str) -> str | None:
    """Return why ``title`` is not an acceptable issue title, if it is not."""
    text = (title or "").strip()
    if "\n" in text or "\r" in text:
        return "The title must be a single line."
    if not TITLE_MIN_CHARS <= len(text) <= TITLE_MAX_CHARS:
        return f"The title must be {TITLE_MIN_CHARS} to {TITLE_MAX_CHARS} characters (got {len(text)})."
    if _INLINE_DESCRIPTION_RE.search(text):
        return "The title contains an inline 'Description:' section; move that text into `description`."
    return None


def description_problem(description: str) -> str | None:
    """Return why ``description`` is not an acceptable issue description, if it is not."""
    length = len((description or "").strip())
    if not DESCRIPTION_MIN_CHARS <= length <= DESCRIPTION_MAX_CHARS:
        return (
            f"The description must be {DESCRIPTION_MIN_CHARS} to {DESCRIPTION_MAX_CHARS} characters of Markdown "
            f"(got {length}): give the context and why, what to change, and acceptance criteria."
        )
    return None


def update_problem(title: str | None, description: str | None, state: str | None, priority: int | None) -> str | None:
    """Return why an ``update_linear_task`` call is not acceptable, if it is not."""
    if title is None and description is None and state is None and priority is None:
        return "Nothing to update: give at least one of title, description, state, priority."
    if priority is not None and priority not in PRIORITY_RANGE:
        return "priority must be 0 (none), 1 (urgent), 2 (high), 3 (medium) or 4 (low)."
    if state is not None and not state.strip():
        return "state must name a workflow state or a state type."
    if title is not None and (problem := title_problem(title)):
        return problem
    return description_problem(description) if description is not None else None


def comment_problem(body: str) -> str | None:
    """Return why a comment body is not acceptable, if it is not."""
    length = len((body or "").strip())
    if not 1 <= length <= COMMENT_MAX_CHARS:
        return f"The comment must be 1 to {COMMENT_MAX_CHARS} characters of Markdown (got {length})."
    return None


def _refused(reason: str) -> str:
    return f"REFUSED: {reason} Nothing was sent to Linear."


def _call(context: ToolContext, operation: Operation, *problems: str | None) -> Any:
    """Refuse on the first input problem, else run ``operation`` on a short-lived service."""
    problem = next((item for item in problems if item), None)
    if problem:
        return _refused(problem)
    service, default_team = _load_linear_service(context)
    try:
        return operation(service, default_team)
    except LinearInputError as error:
        return f"REFUSED: {error} Nothing was changed in Linear."
    finally:
        service.close()


def _resolve_copilot_user_id(service: LinearService) -> str:
    """Find the GitHub Copilot integration user."""
    user = service.find_user(*_COPILOT_CANDIDATE_NAMES)
    if user is None or not user.get("id"):
        raise ValueError("GitHub Copilot user was not found in the connected Linear workspace.")
    return str(user["id"])


def _default_team_id(teams: list[dict[str, Any]], default_team: str | None) -> str | None:
    """Return the id of the team the tools fall back to, matched like ``resolve_team_id`` (id, key, name)."""
    ref = (default_team or "").strip().casefold()
    if not ref:
        return teams[0]["id"] if len(teams) == 1 else None
    for field in ("id", "key", "name"):
        match = next((team for team in teams if str(team.get(field) or "").casefold() == ref), None)
        if match:
            return match["id"]
    return None


def _list_teams(service: LinearService, default_team: str | None) -> list[dict[str, Any]]:
    teams = service.list_teams()
    default_id = _default_team_id(teams, default_team)
    return [{**team, "is_default": default_id is not None and team["id"] == default_id} for team in teams]


def _list_tasks(
    service: LinearService, default_team: str | None, team_id: str | None, limit: int, include_completed: bool
) -> Any:
    team, note = service.choose_team(team_id, default_team)
    issues = service.list_team_issues(team, limit=limit, include_completed=include_completed)
    return {"note": note, "issues": issues} if note else issues


def _create(service: LinearService, default_team: str | None, team_id: str | None, **fields: Any) -> dict[str, Any]:
    team, note = service.choose_team(team_id, default_team)
    issue = service.create_issue(team_id=team, **fields)
    return {**issue, "note": note} if note else issue


def _team_sentence(default_team: str | None) -> str:
    if default_team:
        return f" Without team_id, the configured default team `{default_team}` is used."
    return " No default team is configured: pass team_id unless the workspace has only one team (list_linear_teams)."


def build_linear_tools(context: ToolContext) -> list[BaseTool]:
    """Build Linear task-management tools."""

    @tool("list_linear_teams")
    def list_linear_teams() -> Any:
        """Lists the Linear teams the configured API key can see as {id, key, name, is_default}.

        Use a team's key (e.g. ENG), exact name, or id as team_id in the other Linear tools.
        """
        return _call(context, _list_teams)

    @tool("list_linear_tasks")
    def list_linear_tasks(team_id: str | None = None, limit: int = 20, include_completed: bool = False) -> Any:
        """Lists the most recently updated Linear issues of a team (team key, exact name, or id)."""
        return _call(context, lambda svc, default: _list_tasks(svc, default, team_id, limit, include_completed))

    @tool("add_linear_task")
    def add_linear_task(title: str, description: str, team_id: str | None = None) -> Any:
        """Creates a new Linear issue in a team (team key, exact name, or id).

        title: one line of 5-120 characters naming the change. Never put the description in the title
        (a title with an inline "Description:" section is refused).
        description: required Markdown, 40-20000 characters, that a teammate can act on without asking:
        the context and why it matters, what should change, and acceptance criteria (a short checklist
        of how to tell it is done). A call that breaks these rules answers REFUSED and creates nothing.
        """
        fields = {"title": (title or "").strip(), "description": (description or "").strip()}
        return _call(
            context,
            lambda svc, default: _create(svc, default, team_id, **fields),
            title_problem(title),
            description_problem(description),
        )

    @tool("add_linear_subtask")
    def add_linear_subtask(parent_issue_id: str, title: str, description: str, team_id: str | None = None) -> Any:
        """Creates a new Linear sub-issue under an existing issue (parent id such as ENG-123, or UUID).

        title: one line of 5-120 characters naming the change. Never put the description in the title
        (a title with an inline "Description:" section is refused).
        description: required Markdown, 40-20000 characters, that a teammate can act on without asking:
        the context and why it matters, what should change, and acceptance criteria (a short checklist
        of how to tell it is done). A call that breaks these rules answers REFUSED and creates nothing.
        """
        fields = {"title": (title or "").strip(), "description": (description or "").strip()}
        return _call(
            context,
            lambda svc, default: _create(svc, default, team_id, parent_id=parent_issue_id, **fields),
            title_problem(title),
            description_problem(description),
        )

    @tool("update_linear_task")
    def update_linear_task(
        issue_id: str,
        title: str | None = None,
        description: str | None = None,
        state: str | None = None,
        priority: int | None = None,
    ) -> Any:
        """Updates an existing Linear issue (identifier such as ENG-123, or UUID); give at least one field.

        title and description follow the same rules as add_linear_task and replace the old text.
        state is a workflow-state name of the issue's team (e.g. "In Progress", case-insensitive) or a
        state type: backlog, unstarted, started, completed, canceled; an unknown state is refused with
        the valid ones. priority is 0 (none), 1 (urgent), 2 (high), 3 (medium) or 4 (low).
        """
        fields = {
            "title": title.strip() if title is not None else None,
            "description": description.strip() if description is not None else None,
            "state": state,
            "priority": priority,
        }
        return _call(
            context,
            lambda svc, _default: svc.update_issue(issue_id, **fields),
            update_problem(title, description, state, priority),
        )

    @tool("comment_on_linear_task")
    def comment_on_linear_task(issue_id: str, body: str) -> Any:
        """Adds a Markdown comment (1-10000 characters) to a Linear issue (identifier such as ENG-123, or UUID)."""
        return _call(context, lambda svc, _default: svc.create_comment(issue_id, body.strip()), comment_problem(body))

    @tool("start_linear_task")
    def start_linear_task(issue_id: str) -> Any:
        """Assigns a Linear task to GitHub Copilot."""
        return _call(context, lambda svc, _default: svc.assign_issue(issue_id, _resolve_copilot_user_id(svc)))

    @tool("start_linear_subtask")
    def start_linear_subtask(issue_id: str) -> Any:
        """Assigns a Linear subtask to GitHub Copilot."""
        return _call(context, lambda svc, _default: svc.assign_issue(issue_id, _resolve_copilot_user_id(svc)))

    team_sentence = _team_sentence(_configured_default_team(context))
    for team_tool in (list_linear_tasks, add_linear_task, add_linear_subtask):
        team_tool.description += team_sentence

    return [
        list_linear_teams,
        list_linear_tasks,
        add_linear_task,
        add_linear_subtask,
        update_linear_task,
        comment_on_linear_task,
        start_linear_task,
        start_linear_subtask,
    ]
