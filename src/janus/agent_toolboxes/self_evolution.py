"""Self-evolution toolbox: file -> implement -> merge -> release -> verify (JAN-320..324).

Thin wrappers over ``services/self_evolution_service.py`` (switch, caps),
``self_evolution_intake.py`` (issues, jobs), ``self_evolution_merge.py`` and
``self_evolution_checks.py``,
where every gate lives. The tools never take a repository, URL or token
argument: those come from the owner's self-evolution settings.

The entry needs the ``self_evolution`` capability, which
``capability_profile`` grants only while the owner (an admin) has the switch
on and GitHub and Linear are configured; its unattended allowance follows the
same switch (``tool_policy.SWITCH_GOVERNED``), and each service call checks the
switch again. A gate refusal comes back as ``REFUSED: ...``; nothing was done.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from janus.agent_toolboxes.shared import ToolContext, run_isolated_tool_mutation
from janus.services import self_evolution_checks as checks_service
from janus.services import self_evolution_intake as intake
from janus.services import self_evolution_merge as merge_service
from janus.services import self_evolution_service as service
from janus.tool_registry import ToolRegistryEntry

SELF_EVOLUTION_TOOL_ID = "self_evolution"


class ProductionCheck(BaseModel):
    """One HTTP check against the production instance."""

    path: str = Field(description="Absolute path on the production instance, e.g. /api/status")
    expect_status: int = Field(default=200, description="HTTP status the path must answer")
    expect_text: str | None = Field(default=None, description="Optional text the response body must contain")


def _run(context: ToolContext, action: Callable[[Session], Any]) -> Any:
    """Run one service call in a short-lived session; a gate refusal becomes a ``REFUSED`` answer."""
    try:
        return run_isolated_tool_mutation(context, lambda db: db, action)
    except service.SelfEvolutionError as error:
        return f"REFUSED: {error}. Nothing was done."


def build_self_evolution_tools(context: ToolContext) -> list[BaseTool]:
    """Build the self-evolution tools for the context's user."""
    user = context.user

    @tool("self_evolution_status")
    def self_evolution_status() -> Any:
        """Show whether self-evolution is on, how many actions of each kind remain today, and recent actions."""
        return _run(context, lambda db: service.status(db, user))

    @tool("file_improvement_issue")
    def file_improvement_issue(kind: Literal["capability_gap", "system_error"], title: str, description: str) -> Any:
        """File a Linear issue about something Janus cannot do (capability_gap) or a fault it hit (system_error).

        Give a specific title (10-120 characters) and a description of at least 40 characters: what was
        attempted, what is missing or broken, and what done looks like. An open issue with a similar title
        is returned instead of filing a duplicate. At most 5 issues per day.
        """
        return _run(context, lambda db: intake.file_issue(db, user, kind=kind, title=title, description=description))

    @tool("start_implementation_job")
    def start_implementation_job(issue_identifier: str, prompt: str) -> Any:
        """Start a Claude Code implementation job for a Linear issue (e.g. JAN-123) by firing the Routine.

        ``prompt`` is a detailed brief (100-20000 characters): the goal, acceptance criteria, relevant files,
        tests to add, and docs to update. The job opens a pull request naming its session; it never merges.
        Each call starts a new session and is never retried automatically. At most 3 jobs per day and one per
        issue per day.
        """
        return _run(context, lambda db: intake.start_job(db, user, issue_identifier=issue_identifier, prompt=prompt))

    @tool("merge_feature_pr")
    def merge_feature_pr(pull_number: int) -> Any:
        """Merge a pull request of the configured repository that the loop may merge.

        Only a PR opened by an implementation job you started (its description names that Claude Code
        session), a minor/patch Dependabot bump, or a revert PR the loop opened. Refused unless it targets the
        default branch, GitHub reports it mergeable, every CI check on its head commit has passed (none pending
        or failed), and it touches no fenced path. At most 3 merges per day.
        """
        return _run(context, lambda db: merge_service.merge_feature_pr(db, user, pull_number))

    @tool("merge_release_pr")
    def merge_release_pr(release_pull_number: int, feature_pull_number: int) -> Any:
        """Merge the release-please pull request after you merged ``feature_pull_number`` with merge_feature_pr.

        Refused until the release PR contains that feature's merge commit, its checks are green, and it only
        changes release files. Then schedule a production self-check with schedule_production_self_check.
        """
        return _run(
            context, lambda db: merge_service.merge_release_pr(db, user, release_pull_number, feature_pull_number)
        )

    @tool("schedule_production_self_check")
    def schedule_production_self_check(
        release_pull_number: int, checks: list[ProductionCheck], delay_minutes: int = 30
    ) -> Any:
        """Schedule a one-time check of production after a release you merged (give the deploy time to finish).

        Each check GETs a path on the configured production URL and expects a status and optionally a text.
        If any check fails, Janus notifies the owner, files a Linear issue and opens a revert pull request.
        """
        specs = [check.model_dump() if isinstance(check, BaseModel) else dict(check) for check in checks]
        return _run(
            context,
            lambda db: checks_service.schedule_self_check(
                db, user, release_pull_number=release_pull_number, checks=specs, delay_minutes=delay_minutes
            ),
        )

    return [
        self_evolution_status,
        file_improvement_issue,
        start_implementation_job,
        merge_feature_pr,
        merge_release_pr,
        schedule_production_self_check,
    ]


SELF_EVOLUTION_ENTRY = ToolRegistryEntry(
    id=SELF_EVOLUTION_TOOL_ID,
    display_name="Self-evolution loop",
    description=(
        "File capability-gap issues, start Claude Code implementation jobs, merge gated pull requests and "
        "releases, and verify production -- without a human approval step, behind code-enforced gates (opt-in)."
    ),
    required_capabilities=("self_evolution",),
    operation="write",
    risk_level="high",
    confirmation="required",
    docs_ref="tools.md#self-evolution-tools",
    builder=lambda context: build_self_evolution_tools(context.tool_context),
    # Admin-only: the loop acts with the instance-wide GitHub and Linear credentials.
    enabled_when=lambda context: bool(context.user.is_admin),
    # Linear titles, check names and endpoint replies are outside text (JAN-127).
    reads_untrusted_content=True,
)
