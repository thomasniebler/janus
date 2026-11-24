"""Self-evolution merge gates: feature PR, then the release-please PR, and revert PRs (JAN-322).

Nothing here asks a human. A pull request is merged only when every gate in
:func:`check_gates` passes, checked against GitHub immediately before the
merge, and the merge call pins the head commit that was checked (GitHub
refuses it if the branch moved in between):

* the PR is open, not a draft, comes from a branch of the configured
  repository itself (no forks), and targets that repository's default branch;
* GitHub reports it ``mergeable`` with a clean merge state;
* every check run on the head commit has completed with a passing conclusion
  (:data:`PASSING_CHECK_CONCLUSIONS`) and every commit status is ``success``
  -- nothing pending, failed or errored -- and at least one check exists;
* no changed file (old or new name) is inside the scope fence
  (``self_evolution_service.SCOPE_FENCE``), and every changed file could be
  listed.

``merge_feature_pr`` additionally merges only a PR whose provenance
``self_evolution_provenance.provenance`` accepts: produced by a Claude Code
session the loop started, a minor/patch Dependabot bump, or a revert PR the
loop opened.

The release-please PR additionally must come from a ``release-please--``
branch, may only touch :data:`RELEASE_PR_ALLOWED_FILES`, and is merged only
after the loop itself merged the named feature PR and the release PR's head
already contains that merge commit. release-please opens its PR with the
workflow's ``GITHUB_TOKEN``, which starts no workflow runs, so a release PR
with no checks at all is accepted (:data:`REQUIRE_CHECKS_ON_RELEASE_PR`); any
check that does exist must have passed.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from janus import models
from janus.services import self_evolution_service as core
from janus.services.github_service import GitHubService
from janus.services.self_evolution_provenance import provenance
from janus.services.self_evolution_service import Action, SelfEvolutionError, Status

#: Check-run conclusions that count as passing; anything else (failure, cancelled, timed_out,
#: action_required, stale, startup_failure) fails the gate, and so does a run that has not completed.
PASSING_CHECK_CONCLUSIONS = frozenset({"success", "skipped", "neutral"})
#: GitHub ``mergeable_state`` values that allow a merge (``blocked``, ``behind``, ``dirty``, ``draft``,
#: ``unknown`` and ``unstable`` do not).
MERGEABLE_STATES = frozenset({"clean", "has_hooks"})
RELEASE_BRANCH_PREFIX = "release-please--"
#: The only files a release-please PR may change (the python release type plus the uv.lock extra file).
RELEASE_PR_ALLOWED_FILES = frozenset({"CHANGELOG.md", ".release-please-manifest.json", "pyproject.toml", "uv.lock"})
REQUIRE_CHECKS_ON_FEATURE_PR = True
REQUIRE_CHECKS_ON_RELEASE_PR = False
#: GitHub lists at most 3000 files of a pull request; a bigger one can never be fully checked.
MAX_PR_FILES = 3000
MERGE_METHOD = "squash"


def _changed_paths(files: list[dict[str, Any]]) -> list[str]:
    paths: list[str] = []
    for item in files:
        for key in ("filename", "previous_filename"):
            if item.get(key):
                paths.append(str(item[key]))
    return list(dict.fromkeys(paths))


def _check_problems(service: GitHubService, repo: str, sha: str, *, require_checks: bool) -> list[str]:
    runs = service.list_check_runs(repo, sha)
    statuses = service.list_commit_statuses(repo, sha)
    pending = [run.get("name") for run in runs if run.get("status") != "completed"]
    pending += [status.get("context") for status in statuses if status.get("state") == "pending"]
    failed = [
        run.get("name")
        for run in runs
        if run.get("status") == "completed" and run.get("conclusion") not in PASSING_CHECK_CONCLUSIONS
    ]
    failed += [status.get("context") for status in statuses if status.get("state") in {"failure", "error"}]
    problems = []
    if pending:
        problems.append(f"CI still running: {', '.join(map(str, pending[:10]))}")
    if failed:
        problems.append(f"CI failed: {', '.join(map(str, failed[:10]))}")
    if require_checks and not runs and not statuses:
        problems.append("no CI check has reported on the head commit")
    return problems


def check_gates(service: GitHubService, repo: str, pull_number: int, *, release: bool) -> dict[str, Any]:
    """Run every merge gate against GitHub; raise :class:`SelfEvolutionError` listing each failed gate."""
    pr = service.get_pull_request(repo, pull_number)
    if pr.get("state") != "open" or pr.get("merged"):
        raise SelfEvolutionError(f"pull request #{pull_number} is not open")
    head = pr.get("head") or {}
    base = pr.get("base") or {}
    head_sha = head.get("sha")
    default_branch = service.get_repository(repo).get("default_branch")
    problems: list[str] = []
    if pr.get("draft"):
        problems.append("it is a draft")
    if base.get("ref") != default_branch or ((base.get("repo") or {}).get("full_name") or repo) != repo:
        problems.append(f"it does not target the default branch {default_branch!r} of {repo}")
    if (head.get("repo") or {}).get("full_name") != repo:
        problems.append("its branch is not in the repository itself (forks are never merged)")
    if pr.get("mergeable") is not True:
        problems.append(f"GitHub has not confirmed it can be merged (mergeable={pr.get('mergeable')})")
    if pr.get("mergeable_state") not in MERGEABLE_STATES:
        problems.append(f"its merge state is {pr.get('mergeable_state')!r}, not clean")
    head_ref = str(head.get("ref") or "")
    if release != head_ref.startswith(RELEASE_BRANCH_PREFIX):
        problems.append("it is not a release-please pull request" if release else "it is a release-please pull request")
    files = service.list_pull_request_files(repo, pull_number)
    if len(files) >= MAX_PR_FILES or len(files) != pr.get("changed_files", len(files)):
        problems.append("not every changed file could be listed")
    paths = _changed_paths(files)
    fenced = [f"{path} ({reason})" for path in paths if (reason := core.fenced_reason(path))]
    if fenced:
        problems.append(f"it touches fenced paths: {', '.join(fenced[:10])}")
    if release:
        extra = sorted(set(paths) - RELEASE_PR_ALLOWED_FILES)
        if extra:
            problems.append(f"a release PR may only change release files, not: {', '.join(extra[:10])}")
    if not head_sha:
        problems.append("its head commit is unknown")
    else:
        require = REQUIRE_CHECKS_ON_RELEASE_PR if release else REQUIRE_CHECKS_ON_FEATURE_PR
        problems += _check_problems(service, repo, head_sha, require_checks=require)
    if problems:
        raise SelfEvolutionError(f"pull request #{pull_number} cannot be merged: " + "; ".join(problems))
    return {"pr": pr, "head_sha": head_sha, "paths": paths}


def _ok_row(db: Session, user_id: int, action: Action, subject: str) -> models.SelfEvolutionAction | None:
    return db.scalar(
        select(models.SelfEvolutionAction)
        .where(
            models.SelfEvolutionAction.user_id == user_id,
            models.SelfEvolutionAction.action == action.value,
            models.SelfEvolutionAction.subject == subject,
            models.SelfEvolutionAction.status == Status.OK.value,
        )
        .order_by(models.SelfEvolutionAction.id.desc())
    )


def _merge(
    db: Session,
    user: models.User,
    service: GitHubService,
    repo: str,
    pull_number: int,
    *,
    action: Action,
    gated: dict[str, Any],
    data: dict[str, Any],
) -> dict[str, Any]:
    row = core.reserve(db, user.id, action, subject=f"#{pull_number}", data={"repo": repo, **data})
    try:
        result = service.merge_pull_request(
            repo=repo, pull_number=pull_number, merge_method=MERGE_METHOD, sha=gated["head_sha"]
        )
    except ValueError as error:
        core.finish(db, row, Status.FAILED, detail=str(error))
        raise SelfEvolutionError(f"GitHub refused the merge of #{pull_number}: {error}") from error
    if not result.get("merged"):
        core.finish(db, row, Status.FAILED, detail=str(result.get("message")))
        raise SelfEvolutionError(f"GitHub did not merge #{pull_number}: {result.get('message')}")
    core.finish(db, row, Status.OK, detail=str(gated["pr"].get("html_url") or ""), data={"merge_sha": result["sha"]})
    return {"merged": True, "pull_number": pull_number, "merge_sha": result["sha"], "repo": repo}


def merge_feature_pr(db: Session, user: models.User, pull_number: int) -> dict[str, Any]:
    """Merge one job, Dependabot minor/patch or revert PR of the configured repository once every gate passes."""
    settings = core.require_enabled(db, user)
    repo = core.target_repo(db, settings)
    service = core.github(db)
    try:
        try:
            gated = check_gates(service, repo, pull_number, release=False)
            origin = provenance(db, user.id, service, repo, gated["pr"])
        except SelfEvolutionError as error:
            raise core.refuse(db, user.id, Action.FEATURE_MERGED, str(error), subject=f"#{pull_number}") from error
        return _merge(
            db,
            user,
            service,
            repo,
            pull_number,
            action=Action.FEATURE_MERGED,
            gated=gated,
            data={"head_sha": gated["head_sha"], "files": len(gated["paths"]), **origin},
        )
    finally:
        service.close()


def merge_release_pr(db: Session, user: models.User, release_pull_number: int, feature_pull_number: int) -> dict:
    """Merge the release-please PR, only after the loop merged ``feature_pull_number`` and the release contains it."""
    settings = core.require_enabled(db, user)
    repo = core.target_repo(db, settings)
    subject = f"#{release_pull_number}"
    feature_row = _ok_row(db, user.id, Action.FEATURE_MERGED, f"#{feature_pull_number}")
    if feature_row is None or (feature_row.data or {}).get("repo") != repo:
        raise core.refuse(
            db,
            user.id,
            Action.RELEASE_MERGED,
            f"feature PR #{feature_pull_number} was not merged by the self-evolution loop; merge it first",
            subject=subject,
        )
    released = db.scalars(
        select(models.SelfEvolutionAction).where(
            models.SelfEvolutionAction.user_id == user.id,
            models.SelfEvolutionAction.action == Action.RELEASE_MERGED.value,
            models.SelfEvolutionAction.status == Status.OK.value,
        )
    )
    if any((row.data or {}).get("feature_pr") == feature_pull_number for row in released):
        raise core.refuse(
            db,
            user.id,
            Action.RELEASE_MERGED,
            f"feature PR #{feature_pull_number} is already released",
            subject=subject,
        )
    service = core.github(db)
    try:
        try:
            feature = service.get_pull_request(repo, feature_pull_number)
            feature_sha = feature.get("merge_commit_sha")
            if not feature.get("merged") or not feature_sha:
                raise SelfEvolutionError(f"feature PR #{feature_pull_number} is not merged on GitHub")
            gated = check_gates(service, repo, release_pull_number, release=True)
            comparison = service.compare_commits(repo, feature_sha, gated["head_sha"])
            if comparison.get("status") not in {"ahead", "identical"}:
                raise SelfEvolutionError(
                    f"release PR #{release_pull_number} does not contain the merge of #{feature_pull_number} yet; "
                    "wait for release-please to update it"
                )
        except SelfEvolutionError as error:
            raise core.refuse(db, user.id, Action.RELEASE_MERGED, str(error), subject=subject) from error
        return _merge(
            db,
            user,
            service,
            repo,
            release_pull_number,
            action=Action.RELEASE_MERGED,
            gated=gated,
            data={"release_pr": release_pull_number, "feature_pr": feature_pull_number, "head_sha": gated["head_sha"]},
        )
    finally:
        service.close()


def open_revert_pr(db: Session, user: models.User, feature_pull_number: int, reason: str) -> dict[str, Any]:
    """Open a PR reverting a feature PR the loop merged; it is merged later through :func:`merge_feature_pr`."""
    settings = core.require_enabled(db, user)
    repo = core.target_repo(db, settings)
    subject = f"#{feature_pull_number}"
    if _ok_row(db, user.id, Action.FEATURE_MERGED, subject) is None:
        raise core.refuse(db, user.id, Action.REVERT_OPENED, "only a PR the loop merged is reverted", subject=subject)
    service = core.github(db)
    row = core.reserve(db, user.id, Action.REVERT_OPENED, subject=subject, data={"repo": repo})
    try:
        pr = service.get_pull_request(repo, feature_pull_number)
        if not pr.get("merged") or not pr.get("node_id"):
            raise ValueError(f"#{feature_pull_number} is not a merged pull request")
        revert = service.revert_pull_request(
            pr["node_id"],
            title=f"Revert #{feature_pull_number}: {pr.get('title') or ''}".strip()[:200],
            body=(
                f"Opened automatically by Janus's self-evolution loop (JAN-321): {reason[:2000]}\n\n"
                "It is merged only through the same gates as any self-merged pull request."
            ),
        )
    except ValueError as error:
        core.finish(db, row, Status.FAILED, detail=str(error))
        raise SelfEvolutionError(f"the revert of #{feature_pull_number} could not be opened: {error}") from error
    finally:
        service.close()
    core.finish(db, row, Status.OK, detail=revert.get("url"), data={"revert_pr": revert["number"]})
    return {"reverted_pr": feature_pull_number, "revert_pr": revert["number"], "url": revert.get("url")}
