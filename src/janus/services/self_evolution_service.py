"""Autonomous self-evolution loop: settings, switch, caps, scope fence, audit trail (JAN-320).

Janus closes the loop on its own development without a human gate: notice a
gap -> file a Linear issue (``self_evolution_intake.file_issue``) -> start an
implementation job in Claude Code via a Routine API trigger
(``self_evolution_intake.start_job``) -> merge the
feature PR, then the release-please PR (``self_evolution_merge``) -> verify
production with a scheduled self-check (``self_evolution_checks``).

Humans observe, they do not approve. Safety comes from gates enforced here, in
code, whatever the model's prompt says:

* **Opt-in switch** (:func:`is_enabled`): per user, off by default, and only an
  admin's switch counts (the loop acts with the instance-wide GitHub and
  Linear credentials). Every entry point calls :func:`require_enabled`.
* **Daily caps** (:data:`DAILY_CAPS`), counted over a rolling day from the
  persisted ``self_evolution_actions`` rows. An attempt first *reserves* a
  row (:func:`reserve`) and is refused when its rank in the window exceeds
  the cap, so concurrent calls cannot overshoot.
* **Scope fence** (:data:`SCOPE_FENCE`, :func:`fenced_reason`): the one list of
  paths a self-merged PR may not touch.
* **Audit trail**: every attempt, refusal and outcome is a row in
  ``self_evolution_actions`` *and* an event-log entry (source
  :data:`LOG_SOURCE`).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any
from urllib.parse import urlparse

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from janus import event_logger, models, security
from janus.services.github_service import GitHubService
from janus.services.linear_service import LinearService

LOG_SOURCE = "SelfEvolution"


class Action(StrEnum):
    """Kinds of recorded self-evolution actions."""

    ISSUE_FILED = "issue_filed"
    JOB_STARTED = "job_started"
    FEATURE_MERGED = "feature_merged"
    RELEASE_MERGED = "release_merged"
    SELF_CHECK = "self_check"
    REVERT_OPENED = "revert_opened"


class Status(StrEnum):
    """Lifecycle of one recorded action."""

    PENDING = "pending"
    OK = "ok"
    FAILED = "failed"
    REFUSED = "refused"
    DUPLICATE = "duplicate"
    PASSED = "passed"


#: Attempts per user per rolling :data:`CAP_WINDOW`. Refused and duplicate rows do not count; failed ones do,
#: so a flaky endpoint cannot be hammered.
DAILY_CAPS: dict[Action, int] = {
    Action.ISSUE_FILED: 5,
    Action.JOB_STARTED: 3,
    Action.FEATURE_MERGED: 3,
    Action.RELEASE_MERGED: 3,
    Action.SELF_CHECK: 3,
    Action.REVERT_OPENED: 3,
}
CAP_WINDOW = timedelta(days=1)
_UNCOUNTED_STATUSES = (Status.REFUSED.value, Status.DUPLICATE.value)

#: Paths a self-merged pull request must not touch (JAN-322). An entry ending in ``/`` fences a directory.
#: Matching is case-insensitive; a renamed file is checked under its old and new name. Any path whose name
#: mentions the loop itself (``self_evolution``, ``self-evolution``, ``SelfEvolution`` ...) is fenced too
#: (:data:`SELF_FENCE_KEYWORD`), so the loop can never rewrite its own gates or their tests.
SCOPE_FENCE: tuple[str, ...] = (
    # Authentication, sessions, credential encryption.
    "src/janus/security.py",
    "src/janus/services/auth_service.py",
    # The unattended tool switch and the prompt-injection guard.
    "src/janus/tool_policy.py",
    "src/janus/prompt_injection.py",
    "src/janus/services/injection_classifier.py",
    # CI, release and deploy automation.
    ".github/",
    "release-please-config.json",
    "scripts/janus-up.sh",
    "scripts/deployment_capabilities.py",
    "scripts/pre_release_snapshot.py",
    "scripts/release_host_checks.py",
    "scripts/release_host_checks.sh",
    "scripts/release_validation.py",
    # Database migrations.
    "alembic/",
    "alembic.ini",
)
SELF_FENCE_KEYWORD = "selfevolution"

_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
#: A Claude Code Routine API-trigger id; the fire URL is built from it, never taken from settings.
ROUTINE_TRIGGER_ID_RE = re.compile(r"^trig_[A-Za-z0-9]+$")
#: Per-routine bearer tokens generated in the claude.ai web UI start with this prefix.
ROUTINE_TOKEN_PREFIX = "sk-ant-oat01-"


def fenced_reason(path: str) -> str | None:
    """Return the :data:`SCOPE_FENCE` entry (or the self-fence) a repository path falls under, if any."""
    normalized = (path or "").strip().removeprefix("./").lower()
    if SELF_FENCE_KEYWORD in re.sub(r"[-_ .]", "", normalized):
        return "the self-evolution loop itself"
    for entry in SCOPE_FENCE:
        fenced = entry.lower()
        if normalized == fenced or (fenced.endswith("/") and normalized.startswith(fenced)):
            return entry
    return None


class SelfEvolutionError(ValueError):
    """A gate refused the action; the message says why (it is shown to the model and logged)."""


class SelfEvolutionDisabledError(SelfEvolutionError):
    """The owner's self-evolution switch is off (or the owner is not an admin)."""


# --- Settings ----------------------------------------------------------------


def get_settings(db: Session, user_id: int) -> models.SelfEvolutionSettings | None:
    """Return the user's settings row, if any."""
    return db.scalar(select(models.SelfEvolutionSettings).where(models.SelfEvolutionSettings.user_id == user_id))


def is_enabled(db: Session, user: models.User) -> bool:
    """Whether the loop may act for ``user``: an admin whose switch is on."""
    if not getattr(user, "is_admin", False):
        return False
    row = get_settings(db, user.id)
    return bool(row is not None and row.enabled)


def enabled_for_user_id(db: Session, user_id: int) -> bool:
    """:func:`is_enabled` for a user id (``tool_policy`` only has the id)."""
    user = db.get(models.User, user_id)
    return user is not None and is_enabled(db, user)


def require_enabled(db: Session, user: models.User) -> models.SelfEvolutionSettings:
    """Return the settings row or refuse when the loop is off for this user."""
    if not is_enabled(db, user):
        raise SelfEvolutionDisabledError(
            "self-evolution is switched off for this user (Settings -> Tools & Safety -> Self-evolution); "
            "nothing was done"
        )
    row = get_settings(db, user.id)
    assert row is not None  # noqa: S101 - is_enabled checked it
    return row


def _https_url(value: str, field: str) -> str | None:
    cleaned = value.strip()
    if not cleaned:
        return None
    parsed = urlparse(cleaned)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError(f"{field} must be an https:// URL without credentials")
    return cleaned.rstrip("/") if field == "production_base_url" else cleaned


def update_settings(db: Session, user: models.User, **changes: Any) -> models.SelfEvolutionSettings:
    """Apply a partial settings update. ``None`` leaves a field alone; ``""`` (or ``{}``) clears it."""
    if not user.is_admin:
        raise PermissionError("only an administrator can configure self-evolution")
    row = get_settings(db, user.id)
    if row is None:
        row = models.SelfEvolutionSettings(user_id=user.id, enabled=False)
        db.add(row)
    if changes.get("routine_trigger_id") is not None:
        trigger_id = changes["routine_trigger_id"].strip()
        if trigger_id and not ROUTINE_TRIGGER_ID_RE.match(trigger_id):
            raise ValueError("routine_trigger_id must look like trig_... (the routine's API trigger id)")
        row.routine_trigger_id = trigger_id or None
    if changes.get("production_base_url") is not None:
        row.production_base_url = _https_url(changes["production_base_url"], "production_base_url")
    if changes.get("github_repo") is not None:
        repo = changes["github_repo"].strip()
        if repo and not _REPO_RE.match(repo):
            raise ValueError("github_repo must look like owner/name")
        row.github_repo = repo or None
    if changes.get("routine_token") is not None:
        token = changes["routine_token"].strip()
        if token and (not token.startswith(ROUTINE_TOKEN_PREFIX) or any(char.isspace() for char in token)):
            raise ValueError(f"routine_token must be the routine's API token ({ROUTINE_TOKEN_PREFIX}...)")
        row.routine_token = security.security_service.encrypt(token) if token else None
    if changes.get("enabled") is not None:
        row.enabled = bool(changes["enabled"])
    db.commit()
    db.refresh(row)
    event_logger.log(
        models.EventType.INFO,
        LOG_SOURCE,
        f"Self-evolution settings updated (enabled={row.enabled})",
        {"changed": sorted(key for key, value in changes.items() if value is not None)},
        user_id=user.id,
    )
    return row


def describe_settings(db: Session, user: models.User) -> dict[str, Any]:
    """Public view of the settings (never the token) plus today's usage."""
    row = get_settings(db, user.id)
    return {
        "enabled": bool(row and row.enabled and user.is_admin),
        "routine_trigger_id": row.routine_trigger_id if row else None,
        "routine_token_configured": bool(row and row.routine_token),
        "github_repo": row.github_repo if row else None,
        "production_base_url": row.production_base_url if row else None,
        "daily_caps": {action.value: cap for action, cap in DAILY_CAPS.items()},
        "usage_last_24h": usage(db, user.id),
        "scope_fence": list(SCOPE_FENCE),
        "recent_actions": [serialize_action(action) for action in recent_actions(db, user.id)],
    }


# --- Actions, caps, audit ------------------------------------------------------


def now_naive_utc() -> datetime:
    """Return now as the naive UTC value stored in ``DateTime`` columns."""
    return datetime.now(UTC).replace(tzinfo=None)


def serialize_action(row: models.SelfEvolutionAction) -> dict[str, Any]:
    """Return an API/tool-friendly view of one action row."""
    return {
        "id": row.id,
        "action": row.action,
        "status": row.status,
        "subject": row.subject,
        "detail": row.detail,
        "data": row.data or {},
        "created_at": row.created_at.replace(tzinfo=UTC).isoformat() if row.created_at else None,
    }


def recent_actions(db: Session, user_id: int, limit: int = 20) -> list[models.SelfEvolutionAction]:
    """Return the user's newest actions."""
    return list(
        db.scalars(
            select(models.SelfEvolutionAction)
            .where(models.SelfEvolutionAction.user_id == user_id)
            .order_by(models.SelfEvolutionAction.id.desc())
            .limit(limit)
        )
    )


def usage(db: Session, user_id: int) -> dict[str, int]:
    """Return counted attempts per action within the cap window."""
    since = now_naive_utc() - CAP_WINDOW
    rows = db.execute(
        select(models.SelfEvolutionAction.action, func.count())
        .where(
            models.SelfEvolutionAction.user_id == user_id,
            models.SelfEvolutionAction.created_at >= since,
            models.SelfEvolutionAction.status.not_in(_UNCOUNTED_STATUSES),
        )
        .group_by(models.SelfEvolutionAction.action)
    ).all()
    counts = {action.value: 0 for action in DAILY_CAPS}
    counts.update({action: count for action, count in rows})
    return counts


def _log(user_id: int, row: models.SelfEvolutionAction) -> None:
    event_type = {
        Status.FAILED.value: models.EventType.ERROR,
        Status.REFUSED.value: models.EventType.WARNING,
        Status.OK.value: models.EventType.SUCCESS,
        Status.PASSED.value: models.EventType.SUCCESS,
    }.get(row.status, models.EventType.INFO)
    message = f"{row.action} {row.status}: {row.subject or '-'}"
    if row.detail:
        message += f" ({row.detail[:300]})"
    event_logger.log(event_type, LOG_SOURCE, message, {"action_id": row.id, **(row.data or {})}, user_id=user_id)


def record(
    db: Session,
    user_id: int,
    action: Action,
    status: Status,
    *,
    subject: str | None = None,
    detail: str | None = None,
    data: dict[str, Any] | None = None,
) -> models.SelfEvolutionAction:
    """Persist one action row and mirror it into the event log."""
    row = models.SelfEvolutionAction(
        user_id=user_id,
        action=action.value,
        status=status.value,
        subject=subject,
        detail=detail,
        data=data or None,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    _log(user_id, row)
    return row


def finish(
    db: Session,
    row: models.SelfEvolutionAction,
    status: Status,
    *,
    detail: str | None = None,
    data: dict[str, Any] | None = None,
    subject: str | None = None,
) -> models.SelfEvolutionAction:
    """Move a reserved row to its outcome and log it."""
    row.status = status.value
    if detail is not None:
        row.detail = detail
    if subject is not None:
        row.subject = subject
    if data:
        row.data = {**(row.data or {}), **data}
    db.commit()
    db.refresh(row)
    _log(row.user_id, row)
    return row


def refuse(
    db: Session, user_id: int, action: Action, reason: str, *, subject: str | None = None, data: dict | None = None
) -> SelfEvolutionError:
    """Record a refusal and return the error to raise."""
    record(db, user_id, action, Status.REFUSED, subject=subject, detail=reason, data=data)
    return SelfEvolutionError(f"refused: {reason}")


def reserve(
    db: Session, user_id: int, action: Action, *, subject: str | None = None, data: dict[str, Any] | None = None
) -> models.SelfEvolutionAction:
    """Reserve one attempt under the daily cap, or refuse (recorded) when the cap is reached.

    The row is committed first and then ranked among the window's counted rows
    up to its own id, so of two concurrent attempts for the last slot exactly
    one wins.
    """
    row = models.SelfEvolutionAction(
        user_id=user_id, action=action.value, status=Status.PENDING.value, subject=subject, data=data or None
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    rank = db.scalar(
        select(func.count()).where(
            models.SelfEvolutionAction.user_id == user_id,
            models.SelfEvolutionAction.action == action.value,
            models.SelfEvolutionAction.created_at >= now_naive_utc() - CAP_WINDOW,
            models.SelfEvolutionAction.status.not_in(_UNCOUNTED_STATUSES),
            models.SelfEvolutionAction.id <= row.id,
        )
    )
    cap = DAILY_CAPS[action]
    if rank > cap:
        finish(db, row, Status.REFUSED, detail=f"daily cap of {cap} {action.value} reached")
        raise SelfEvolutionError(f"refused: the daily cap of {cap} {action.value} actions is reached; try tomorrow")
    return row


# --- Integrations ------------------------------------------------------------

#: Test seams: tests replace these with factories over ``httpx.MockTransport``.
github_factory: Callable[[str], GitHubService] = GitHubService
linear_factory: Callable[[str], LinearService] = LinearService


def http_client(timeout: float) -> httpx.Client:
    """Return the client for outbound calls to the Routine endpoint and production (a test seam)."""
    return httpx.Client(timeout=timeout, follow_redirects=False)


def _instance_settings(db: Session) -> models.Settings:
    settings = db.query(models.Settings).first()
    if settings is None:
        raise SelfEvolutionError("GitHub and Linear are not configured")
    return settings


def github(db: Session) -> GitHubService:
    """Return a GitHub client over the instance-wide token."""
    settings = _instance_settings(db)
    if not settings.github_token:
        raise SelfEvolutionError("GitHub is not configured (Settings -> Connections -> Developer)")
    return github_factory(security.security_service.decrypt(settings.github_token))


def target_repo(db: Session, row: models.SelfEvolutionSettings) -> str:
    """Return the one repository the loop works on; tools never take a repository argument."""
    repo = row.github_repo or _instance_settings(db).github_default_repo
    if not repo:
        raise SelfEvolutionError("no repository configured for self-evolution or as the GitHub default")
    return repo


def linear(db: Session) -> tuple[LinearService, str]:
    """Return a Linear client and the default team."""
    settings = _instance_settings(db)
    if not settings.linear_api_key or not settings.linear_default_team_id:
        raise SelfEvolutionError("Linear needs an API key and a default team (Settings -> Connections -> Developer)")
    return linear_factory(security.security_service.decrypt(settings.linear_api_key)), settings.linear_default_team_id


def status(db: Session, user: models.User) -> dict[str, Any]:
    """Return what the loop may still do today, for the agent and the settings page."""
    view = describe_settings(db, user)
    remaining = {action: max(0, DAILY_CAPS[Action(action)] - count) for action, count in view["usage_last_24h"].items()}
    return {
        "enabled": view["enabled"],
        "remaining_today": remaining,
        "recent_actions": view["recent_actions"][:10],
    }
