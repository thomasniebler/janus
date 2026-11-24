"""Which pull requests the self-evolution loop may merge at all (JAN-322, owner decision).

On top of the merge gates in ``self_evolution_merge`` (CI, merge state,
default branch, no forks, scope fence, caps), ``merge_feature_pr`` only merges
a pull request of one of these kinds, decided here by :func:`provenance`:

* **job** -- produced by a Claude Code session the loop itself started: the PR
  description contains the session id (or URL) the Routine API returned for a
  ``job_started`` row of this user within :data:`JOB_PR_WINDOW` that was
  started for the same repository (``data["repo"]``). The job's
  preamble tells the session to put its session URL and the Linear issue id in
  the description.
* **dependabot** -- a Dependabot minor/patch bump: opened by
  ``dependabot[bot]`` from a ``dependabot/...`` branch, one commit authored by
  Dependabot, and every entry of the commit message's ``updated-dependencies:``
  block has ``update-type`` ``version-update:semver-patch`` or
  ``version-update:semver-minor`` (group PRs list several). Missing or
  unparseable metadata, or any major bump, refuses. This deliberately departs,
  for the loop only and at the owner's request, from the repository's
  "combine Dependabot PRs into one" rule.
* **revert** -- a revert PR the loop opened itself (``revert_opened`` row).

Release-please PRs go through ``merge_release_pr`` instead. Everything else is
refused with the reason.
"""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from janus import models
from janus.services.github_service import GitHubService
from janus.services.self_evolution_service import Action, SelfEvolutionError, Status, now_naive_utc

#: How long after a job was started its session's pull request may still be merged.
JOB_PR_WINDOW = timedelta(days=14)
DEPENDABOT_LOGIN = "dependabot[bot]"
DEPENDABOT_BRANCH_PREFIX = "dependabot/"
ALLOWED_DEPENDABOT_UPDATE_TYPES = frozenset({"version-update:semver-patch", "version-update:semver-minor"})


def _ok_rows(db: Session, user_id: int, action: Action, since=None) -> list[models.SelfEvolutionAction]:
    query = select(models.SelfEvolutionAction).where(
        models.SelfEvolutionAction.user_id == user_id,
        models.SelfEvolutionAction.action == action.value,
        models.SelfEvolutionAction.status == Status.OK.value,
    )
    if since is not None:
        query = query.where(models.SelfEvolutionAction.created_at >= since)
    return list(db.scalars(query.order_by(models.SelfEvolutionAction.id.desc())))


def _names_session(body: str, session_id: str | None, session_url: str | None) -> bool:
    # Word-bounded, so ``session_abc`` (or its URL) never matches inside ``session_abcdef``.
    return any(
        needle and re.search(re.escape(needle) + r"(?![A-Za-z0-9_])", body) for needle in (session_url, session_id)
    )


def parse_dependabot_updates(message: str) -> list[dict[str, str]]:
    """Parse the ``updated-dependencies:`` YAML block of a Dependabot commit message.

    Raises ``ValueError`` when the block is missing, empty, or an entry lacks
    ``dependency-name`` or ``update-type``.
    """
    lines = (message or "").splitlines()
    try:
        start = next(index for index, line in enumerate(lines) if line.strip() == "updated-dependencies:")
    except StopIteration:
        raise ValueError("the head commit has no updated-dependencies metadata") from None
    entries: list[dict[str, str]] = []
    for line in lines[start + 1 :]:
        stripped = line.strip()
        if stripped in {"...", "---"} or (stripped and not line.startswith((" ", "-"))):
            break
        if not stripped:
            continue
        if stripped.startswith("- "):
            entries.append({})
            stripped = stripped[2:].strip()
        if not entries or ":" not in stripped:
            raise ValueError(f"unparseable updated-dependencies line: {stripped[:80]!r}")
        key, _, value = stripped.partition(":")
        entries[-1][key.strip()] = value.strip().strip("'\"")
    if not entries or any(not entry.get("dependency-name") or not entry.get("update-type") for entry in entries):
        raise ValueError("the updated-dependencies metadata is empty or incomplete")
    return entries


def _dependabot(service: GitHubService, repo: str, pr: dict[str, Any]) -> dict[str, Any]:
    head = pr.get("head") or {}
    if (pr.get("user") or {}).get("login") != DEPENDABOT_LOGIN or not str(head.get("ref") or "").startswith(
        DEPENDABOT_BRANCH_PREFIX
    ):
        raise SelfEvolutionError("a Dependabot PR must be opened by dependabot[bot] from a dependabot/ branch")
    if pr.get("commits") != 1:
        raise SelfEvolutionError("a Dependabot PR must consist of Dependabot's single commit")
    commit = service.get_commit(repo, str(head.get("sha")))
    if (commit.get("author") or {}).get("login") != DEPENDABOT_LOGIN:
        raise SelfEvolutionError("the Dependabot PR's commit was not authored by dependabot[bot]")
    try:
        updates = parse_dependabot_updates((commit.get("commit") or {}).get("message") or "")
    except ValueError as error:
        raise SelfEvolutionError(f"Dependabot metadata unusable: {error}") from error
    blocked = [
        f"{entry['dependency-name']} ({entry['update-type']})"
        for entry in updates
        if entry["update-type"] not in ALLOWED_DEPENDABOT_UPDATE_TYPES
    ]
    if blocked:
        raise SelfEvolutionError(f"only minor and patch Dependabot bumps are merged, not: {', '.join(blocked[:10])}")
    return {"kind": "dependabot", "dependencies": [entry["dependency-name"] for entry in updates][:20]}


def provenance(db: Session, user_id: int, service: GitHubService, repo: str, pr: dict[str, Any]) -> dict[str, Any]:
    """Return why the loop may merge ``pr`` (``kind`` job/dependabot/revert), or refuse."""
    number = pr.get("number")
    for row in _ok_rows(db, user_id, Action.REVERT_OPENED):
        if (row.data or {}).get("revert_pr") == number:
            return {"kind": "revert", "reverts": row.subject}
    head_ref = str((pr.get("head") or {}).get("ref") or "")
    if (pr.get("user") or {}).get("login") == DEPENDABOT_LOGIN or head_ref.startswith(DEPENDABOT_BRANCH_PREFIX):
        return _dependabot(service, repo, pr)
    body = str(pr.get("body") or "")
    for row in _ok_rows(db, user_id, Action.JOB_STARTED, since=now_naive_utc() - JOB_PR_WINDOW):
        data = row.data or {}
        if _names_session(body, data.get("session_id"), data.get("session_url")):
            if data.get("repo") != repo:
                # A session started for another repository never vouches for a PR in this one.
                raise SelfEvolutionError(
                    f"the Claude Code session it names was started for {data.get('repo') or 'an unknown repository'}, "
                    f"not {repo}"
                )
            return {"kind": "job", "issue": row.subject, "session_id": data.get("session_id")}
    raise SelfEvolutionError(
        "it was not produced by a job the loop started (its description names no recorded Claude Code session), "
        "and it is neither a minor/patch Dependabot bump nor a revert the loop opened"
    )
