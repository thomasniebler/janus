"""Self-evolution verification: a scheduled production self-check after each release merge (JAN-321).

:func:`schedule_self_check` is allowed only for a release PR the loop itself
merged, once per release. It stores the checks on a ``self_check`` action row
and queues a one-time run through the ordinary durable trigger machinery (the
same agent-follow-up definition plus ``ScheduledTriggerSpec`` +
``enqueue_trigger`` path ``create_scheduled_trigger`` uses). The definition's
``builtin_key`` is ``selfevo.check:<row id>``, which ``builtin_triggers``
maps to :func:`run_self_check` as the run's ``prepare`` hook: the checks run
in code, with no model call, so a failure can never be talked away.

Each check is an HTTP GET of a path on the configured production base URL
(never another host, redirects not followed) that must answer the expected
status and, optionally, contain an expected text. On any failure
:func:`handle_failure` notifies the owner (desktop notification and an
``ERROR`` event), files a ``system_error`` Linear issue through
``self_evolution_intake.file_issue`` (dedupe and cap included), and opens a
revert PR of the feature PR through the GitHub GraphQL ``revertPullRequest``
mutation. The revert PR is not merged here: it goes through
``merge_feature_pr`` and its gates like any other PR.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from janus import models, notifications
from janus import trigger_definition_service as definitions
from janus.services import self_evolution_intake as intake
from janus.services import self_evolution_merge as merging
from janus.services import self_evolution_service as core
from janus.services.self_evolution_service import Action, SelfEvolutionError, Status
from janus.trigger_adapters import ScheduledTriggerSpec
from janus.trigger_service import MAX_ACTIVE_TRIGGERS_PER_OWNER, count_active_triggers, enqueue_trigger

#: ``TriggerDefinition.builtin_key`` prefix of a scheduled self-check; the suffix is the action row id.
#: Must equal ``builtin_triggers.SELF_CHECK_KEY_PREFIX``, which maps it back to :func:`run_self_check`.
CHECK_KEY_PREFIX = "selfevo.check:"
SELF_CHECK_SOURCE = "self-evolution"
MIN_DELAY_MINUTES = 10
MAX_DELAY_MINUTES = 24 * 60
MAX_CHECKS = 5
MAX_PATH_CHARS = 500
MAX_EXPECT_TEXT_CHARS = 200
CHECK_TIMEOUT_SECONDS = 20.0
MAX_BODY_CHARS = 200_000


def _validate_checks(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_CHECKS:
        raise SelfEvolutionError(f"give 1 to {MAX_CHECKS} checks")
    checks = []
    for item in raw:
        if not isinstance(item, dict):
            raise SelfEvolutionError("each check is an object with path, expect_status and optional expect_text")
        path = str(item.get("path") or "")
        if (
            not path.startswith("/")
            or path.startswith("//")
            or len(path) > MAX_PATH_CHARS
            or any(char.isspace() or ord(char) < 32 for char in path)
            or "\\" in path
            or "@" in path
        ):
            raise SelfEvolutionError(f"check path {path!r} must be a plain absolute path such as /api/status")
        expect_status = item.get("expect_status", 200)
        if isinstance(expect_status, bool) or not isinstance(expect_status, int) or not 100 <= expect_status <= 599:
            raise SelfEvolutionError("expect_status must be an HTTP status code")
        expect_text = item.get("expect_text")
        if expect_text is not None and (not isinstance(expect_text, str) or len(expect_text) > MAX_EXPECT_TEXT_CHARS):
            raise SelfEvolutionError(f"expect_text must be a string of at most {MAX_EXPECT_TEXT_CHARS} characters")
        checks.append({"path": path, "expect_status": expect_status, "expect_text": expect_text or None})
    return checks


def _describe(checks: list[dict[str, Any]], base_url: str) -> str:
    lines = []
    for check in checks:
        line = f"- GET {base_url}{check['path']} answers HTTP {check['expect_status']}"
        if check["expect_text"]:
            line += f" containing {check['expect_text']!r}"
        lines.append(line)
    return "\n".join(lines)


def _release_row(db: Session, user_id: int, release_pull_number: int) -> models.SelfEvolutionAction | None:
    rows = db.scalars(
        select(models.SelfEvolutionAction).where(
            models.SelfEvolutionAction.user_id == user_id,
            models.SelfEvolutionAction.action == Action.RELEASE_MERGED.value,
            models.SelfEvolutionAction.status == Status.OK.value,
        )
    )
    return next((row for row in rows if (row.data or {}).get("release_pr") == release_pull_number), None)


def _already_checked(db: Session, user_id: int, release_pull_number: int) -> bool:
    rows = db.scalars(
        select(models.SelfEvolutionAction).where(
            models.SelfEvolutionAction.user_id == user_id,
            models.SelfEvolutionAction.action == Action.SELF_CHECK.value,
            models.SelfEvolutionAction.status != Status.REFUSED.value,
        )
    )
    return any((row.data or {}).get("release_pr") == release_pull_number for row in rows)


def schedule_self_check(
    db: Session,
    user: models.User,
    *,
    release_pull_number: int,
    checks: list[dict[str, Any]],
    delay_minutes: int = 30,
) -> dict[str, Any]:
    """Queue the one-time production self-check for a release the loop merged."""
    settings = core.require_enabled(db, user)
    subject = f"release #{release_pull_number}"
    if not settings.production_base_url:
        raise core.refuse(db, user.id, Action.SELF_CHECK, "no production base URL configured", subject=subject)
    release = _release_row(db, user.id, release_pull_number)
    if release is None:
        raise core.refuse(
            db, user.id, Action.SELF_CHECK, "only a release PR the loop merged can be self-checked", subject=subject
        )
    if _already_checked(db, user.id, release_pull_number):
        raise core.refuse(db, user.id, Action.SELF_CHECK, "this release already has a self-check", subject=subject)
    if not MIN_DELAY_MINUTES <= delay_minutes <= MAX_DELAY_MINUTES:
        raise SelfEvolutionError(f"delay_minutes must be {MIN_DELAY_MINUTES} to {MAX_DELAY_MINUTES}")
    validated = _validate_checks(checks)
    if count_active_triggers(db, user.id) >= MAX_ACTIVE_TRIGGERS_PER_OWNER:
        raise core.refuse(db, user.id, Action.SELF_CHECK, "the trigger backlog is full", subject=subject)
    due_at = datetime.now(UTC) + timedelta(minutes=delay_minutes)
    row = core.reserve(
        db,
        user.id,
        Action.SELF_CHECK,
        subject=subject,
        data={
            "release_pr": release_pull_number,
            "feature_pr": (release.data or {}).get("feature_pr"),
            "base_url": settings.production_base_url,
            "checks": validated,
            "due_at": due_at.isoformat(),
        },
    )
    objective = (
        f"Production self-check after release PR #{release_pull_number} (self-evolution loop, JAN-321).\n"
        "Janus runs these checks in code, without a model call; on failure it notifies you, files a Linear issue "
        "and opens a revert PR:\n" + _describe(validated, settings.production_base_url)
    )
    definition = None
    try:
        definition = definitions.create_definition(
            db,
            user.id,
            origin=models.TriggerDefinitionOrigin.AGENT.value,
            builtin_key=f"{CHECK_KEY_PREFIX}{row.id}",
            name=f"Self-check after release #{release_pull_number}",
            objective=objective,
            tool_ids=[],
            kind=models.TriggerDefinitionKind.SCHEDULE.value,
            schedule={"type": definitions.SCHEDULE_ONCE, "at": due_at.isoformat()},
            timezone="UTC",
        )
        spec = ScheduledTriggerSpec(
            schedule_id=f"self-evolution-check-{row.id}",
            owner_id=user.id,
            task_id=f"self-evolution-check-{row.id}",
            objective=objective,
            due_at=due_at,
            source=SELF_CHECK_SOURCE,
            task_mode="trigger",
        )
        record, _duplicate = enqueue_trigger(db, spec.as_envelope(), definition_id=definition.id)
    except Exception as error:
        db.rollback()
        if definition is not None:
            db.delete(definition)
            db.commit()
        core.finish(db, row, Status.FAILED, detail=f"could not schedule: {error}")
        raise
    row.data = {**(row.data or {}), "definition_id": definition.id, "trigger_id": record.trigger_id}
    db.commit()
    return {"scheduled": True, "check_id": row.id, "trigger_id": record.trigger_id, "due_at": due_at.isoformat()}


def _run_one(client: httpx.Client, base_url: str, check: dict[str, Any]) -> str | None:
    """Run one check; return ``None`` when it passed, otherwise what went wrong."""
    url = f"{base_url}{check['path']}"
    if urlparse(url).netloc != urlparse(base_url).netloc:
        return f"{check['path']}: not on the production host"
    try:
        response = client.get(url)
    except httpx.HTTPError as error:
        return f"GET {check['path']}: {error.__class__.__name__}"
    if response.status_code != check["expect_status"]:
        return f"GET {check['path']}: HTTP {response.status_code}, expected {check['expect_status']}"
    if check.get("expect_text") and check["expect_text"] not in response.text[:MAX_BODY_CHARS]:
        return f"GET {check['path']}: the response does not contain {check['expect_text']!r}"
    return None


def run_self_check(db: Session, user: models.User, check_id: int) -> str:
    """Run a scheduled self-check once (idempotent on retries) and return the run's recorded output."""
    row = db.get(models.SelfEvolutionAction, check_id)
    if row is None or row.user_id != user.id or row.action != Action.SELF_CHECK.value:
        raise SelfEvolutionError(f"no self-check {check_id} for this user")
    if row.status != Status.PENDING.value:
        return f"Self-check {check_id} already ran: {row.status}."
    data = row.data or {}
    base_url = str(data.get("base_url") or "")
    with core.http_client(CHECK_TIMEOUT_SECONDS) as client:
        failures = [problem for check in data.get("checks") or [] if (problem := _run_one(client, base_url, check))]
    if not failures:
        core.finish(db, row, Status.PASSED, detail=f"{len(data.get('checks') or [])} check(s) passed")
        return f"Self-check after release #{data.get('release_pr')} passed."
    summary = "; ".join(failures)
    core.finish(db, row, Status.FAILED, detail=summary)
    return handle_failure(db, user, row, summary)


def handle_failure(db: Session, user: models.User, row: models.SelfEvolutionAction, summary: str) -> str:
    """Notify the owner, file a Linear issue, and open a revert PR for a failed self-check."""
    data = row.data or {}
    release_pr, feature_pr = data.get("release_pr"), data.get("feature_pr")
    notifications.NotificationService.notify_alert(
        "Janus: production self-check failed", f"After release #{release_pr}: {summary}"[:500]
    )
    outcome: dict[str, Any] = {}
    if not core.is_enabled(db, user):
        outcome["skipped"] = "self-evolution was switched off; no issue or revert"
    else:
        try:
            outcome["issue"] = intake.file_issue(
                db,
                user,
                kind="system_error",
                title=f"Production self-check failed after release #{release_pr}",
                description=(
                    f"The self-check scheduled after release PR #{release_pr} (feature PR #{feature_pr}) failed:\n\n"
                    f"{summary}\n\nChecks:\n{_describe(data.get('checks') or [], str(data.get('base_url') or ''))}"
                ),
            )
        except (SelfEvolutionError, ValueError) as error:
            outcome["issue_error"] = str(error)
        if feature_pr:
            try:
                outcome["revert"] = merging.open_revert_pr(db, user, int(feature_pr), f"self-check failed: {summary}")
            except (SelfEvolutionError, ValueError) as error:
                outcome["revert_error"] = str(error)
    core.finish(db, row, Status.FAILED, data={"failure_handling": outcome})
    return f"Self-check after release #{release_pr} FAILED: {summary}. Follow-up: {outcome}"
