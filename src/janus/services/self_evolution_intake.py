"""Self-evolution intake: file an issue (JAN-324) and start an implementation job (JAN-323).

Both steps run through the gates in ``self_evolution_service``: the owner's
switch, the daily caps (reserved before any outbound call), and the audit
trail. Filing dedupes against the team's open Linear issues by title
similarity (:data:`DUPLICATE_TITLE_SIMILARITY`) and appends a footer saying no
human reviewed the issue. A job fires the configured Claude Code Routine
through its API trigger; the ``text`` starts with fixed guardrails (branch +
PR against the default branch whose description names the Linear issue and
the session URL, never merge, the scope fence) before Janus's own brief, at
most one job per issue per day. The returned session id and URL are recorded:
they are what ties a pull request back to this job (``self_evolution_merge``).
"""

from __future__ import annotations

import difflib
import re
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from janus import models, security
from janus.services import self_evolution_service as core
from janus.services.linear_service import LinearInputError, LinearService
from janus.services.self_evolution_service import (
    CAP_WINDOW,
    ROUTINE_TRIGGER_ID_RE,
    SCOPE_FENCE,
    Action,
    SelfEvolutionError,
    Status,
    finish,
    linear,
    now_naive_utc,
    record,
    refuse,
    require_enabled,
    reserve,
    target_repo,
)

# --- JAN-324: file an issue ----------------------------------------------------

ISSUE_KINDS = {"capability_gap": "Capability gap", "system_error": "System error"}
MAX_TITLE_CHARS = 120
MIN_DESCRIPTION_CHARS = 40
MAX_DESCRIPTION_CHARS = 8000
#: Titles at least this similar (0..1) to an open issue's count as duplicates.
DUPLICATE_TITLE_SIMILARITY = 0.8
#: Open issues compared against (Linear's newest first).
DEDUPE_OPEN_ISSUES = 100
_KIND_PREFIX_RE = re.compile(r"^\s*(capability gap|system error)\s*:\s*", re.IGNORECASE)


def _normalize_title(title: str) -> str:
    stripped = _KIND_PREFIX_RE.sub("", title or "")
    return " ".join(re.sub(r"[^a-z0-9]+", " ", stripped.lower()).split())


def title_similarity(first: str, second: str) -> float:
    """Return how alike two issue titles are, 0..1 (sequence ratio or word overlap, whichever is higher)."""
    a, b = _normalize_title(first), _normalize_title(second)
    if not a or not b:
        return 0.0
    words_a, words_b = set(a.split()), set(b.split())
    jaccard = len(words_a & words_b) / len(words_a | words_b)
    return max(difflib.SequenceMatcher(None, a, b).ratio(), jaccard)


def _issue_body(kind: str, description: str) -> str:
    return (
        f"{description.strip()}\n\n---\n"
        f"Filed autonomously by Janus's self-evolution loop (JAN-320) as a {ISSUE_KINDS[kind].lower()}. "
        "No human reviewed this issue before it was filed."
    )


def _team_uuid(service: LinearService, team: str) -> str:
    """Resolve the configured default team (UUID, key or exact name; JAN-330); an unknown one refuses."""
    try:
        return service.resolve_team_id(team)
    except LinearInputError as error:
        raise SelfEvolutionError(f"the configured Linear default team is not usable: {error}") from error


def file_issue(db: Session, user: models.User, *, kind: str, title: str, description: str) -> dict[str, Any]:
    """File a capability-gap or system-error issue in Linear, unless an open one already covers it."""
    require_enabled(db, user)
    cleaned_title = " ".join((title or "").split())
    if kind not in ISSUE_KINDS:
        raise SelfEvolutionError(f"kind must be one of: {', '.join(ISSUE_KINDS)}")
    if not 10 <= len(cleaned_title) <= MAX_TITLE_CHARS:
        raise SelfEvolutionError(f"title must be 10 to {MAX_TITLE_CHARS} characters")
    if not MIN_DESCRIPTION_CHARS <= len((description or "").strip()) <= MAX_DESCRIPTION_CHARS:
        raise SelfEvolutionError(f"description must be {MIN_DESCRIPTION_CHARS} to {MAX_DESCRIPTION_CHARS} characters")
    full_title = f"{ISSUE_KINDS[kind]}: {_KIND_PREFIX_RE.sub('', cleaned_title)}"
    service, team = linear(db)
    try:
        team = _team_uuid(service, team)
        open_issues = service.list_team_issues(team, limit=DEDUPE_OPEN_ISSUES, include_completed=False)
        best = max(open_issues, key=lambda issue: title_similarity(full_title, issue.get("title") or ""), default=None)
        if best is not None and title_similarity(full_title, best.get("title") or "") >= DUPLICATE_TITLE_SIMILARITY:
            existing = {key: best.get(key) for key in ("identifier", "title", "url")}
            record(
                db,
                user.id,
                Action.ISSUE_FILED,
                Status.DUPLICATE,
                subject=best.get("identifier"),
                detail=f"open issue already covers: {full_title}",
                data={"title": full_title},
            )
            return {"filed": False, "duplicate_of": existing}
        row = reserve(db, user.id, Action.ISSUE_FILED, subject=full_title, data={"title": full_title, "kind": kind})
        try:
            issue = service.create_issue(team, full_title, _issue_body(kind, description))
        except ValueError as error:
            finish(db, row, Status.FAILED, detail=str(error))
            raise
        finish(db, row, Status.OK, subject=issue.get("identifier"), detail=issue.get("url"))
        return {"filed": True, "issue": {key: issue.get(key) for key in ("id", "identifier", "title", "url")}}
    finally:
        service.close()


# --- JAN-323: start an implementation job ---------------------------------------
#
# Claude Code Routine API trigger (https://platform.claude.com/docs/en/api/claude-code/routines-fire):
# ``POST https://api.anthropic.com/v1/claude_code/routines/{trig_id}/fire`` with the routine's own bearer
# token, ``anthropic-version: 2023-06-01`` and the (optional, sent anyway) experimental beta header; body
# ``{"text": ...}`` of at most 65,536 characters. 200 answers ``{"type": "routine_fire",
# "claude_code_session_id": "session_...", "claude_code_session_url": "https://claude.ai/code/session_..."}``
# once the session exists; errors are ``{"type": "error", "error": {"type": ..., "message": ...}}``. There is
# no idempotency key -- every successful call starts a new session -- so a fire is never retried.

ROUTINE_FIRE_BASE_URL = "https://api.anthropic.com/v1/claude_code/routines"
ROUTINE_ANTHROPIC_VERSION = "2023-06-01"
ROUTINE_BETA = "experimental-cc-routine-2026-04-01"
MAX_ROUTINE_TEXT_CHARS = 65_536
ISSUE_IDENTIFIER_RE = re.compile(r"^[A-Z][A-Z0-9]{0,9}-\d{1,7}$")
SESSION_ID_RE = re.compile(r"^session_[A-Za-z0-9]+$")
SESSION_URL_PREFIX = "https://claude.ai/code/session_"
SESSION_URL_RE = re.compile(r"^https://claude\.ai/code/session_[A-Za-z0-9]+$")
MIN_JOB_PROMPT_CHARS = 100
MAX_JOB_PROMPT_CHARS = 20_000
ROUTINE_TIMEOUT_SECONDS = 60.0
_TRUNCATION_MARK = "\n[brief truncated to the routine payload limit]"
#: Error statuses after which trying again later may help (never automatically).
_RETRY_LATER_STATUSES = frozenset({429, 500, 503})


def routine_fire_url(trigger_id: str) -> str:
    """Build the fire URL from the validated trigger id; settings never hold a URL."""
    if not ROUTINE_TRIGGER_ID_RE.match(trigger_id or ""):
        raise SelfEvolutionError("the configured routine trigger id is not a trig_... id")
    return f"{ROUTINE_FIRE_BASE_URL}/{trigger_id}/fire"


def build_job_text(issue_identifier: str, prompt: str, repo: str) -> str:
    """Return the ``text`` sent to the Routine: fixed guardrails, then the issue and Janus's brief (bounded)."""
    fence = "\n".join(f"- {entry}" for entry in SCOPE_FENCE)
    preamble = (
        f"Implement Linear issue {issue_identifier} in the GitHub repository {repo}.\n\n"
        "This job was started autonomously by Janus's self-evolution loop (JAN-320). Rules:\n"
        "- Work on a new branch of that repository (not a fork) and open exactly one pull request against "
        "the default branch.\n"
        f"- The pull request description MUST contain the Linear issue id {issue_identifier} and the URL of "
        f"this Claude Code session ({SESSION_URL_PREFIX}...). Janus merges only pull requests whose description "
        "names the session it started.\n"
        "- Do not merge anything yourself; Janus merges after every CI check passes.\n"
        "- Janus refuses to merge a pull request that touches any of these paths, or any path whose name "
        "mentions self-evolution, so do not change them:\n"
        f"{fence}\n\n"
        f"--- Janus's instructions for {issue_identifier} ---\n"
    )
    brief = prompt.strip()
    room = MAX_ROUTINE_TEXT_CHARS - len(preamble)
    if len(brief) > room:
        brief = brief[: room - len(_TRUNCATION_MARK)] + _TRUNCATION_MARK
    return preamble + brief


def _error_reference(response: httpx.Response) -> dict[str, Any]:
    """Return the API error type and message (never request headers, so never the token)."""
    try:
        payload = response.json()
    except ValueError:
        payload = None
    error = payload.get("error") if isinstance(payload, dict) else None
    reference: dict[str, Any] = {"status_code": response.status_code}
    if isinstance(error, dict):
        reference["error_type"] = str(error.get("type") or "")[:100]
        reference["error_message"] = str(error.get("message") or "")[:300]
    if response.headers.get("retry-after"):
        reference["retry_after"] = response.headers["retry-after"][:20]
    return reference


def _session_reference(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    payload = payload if isinstance(payload, dict) else {}
    session_id = str(payload.get("claude_code_session_id") or "")
    session_url = str(payload.get("claude_code_session_url") or "")
    return {
        "session_id": session_id if SESSION_ID_RE.match(session_id) else None,
        "session_url": session_url if SESSION_URL_RE.match(session_url) and len(session_url) <= 300 else None,
    }


def _has_recent_job(db: Session, user_id: int, issue_identifier: str) -> bool:
    return (
        db.scalar(
            select(func.count()).where(
                models.SelfEvolutionAction.user_id == user_id,
                models.SelfEvolutionAction.action == Action.JOB_STARTED.value,
                models.SelfEvolutionAction.subject == issue_identifier,
                models.SelfEvolutionAction.status.in_((Status.OK.value, Status.PENDING.value)),
                models.SelfEvolutionAction.created_at >= now_naive_utc() - CAP_WINDOW,
            )
        )
        > 0
    )


def start_job(db: Session, user: models.User, *, issue_identifier: str, prompt: str) -> dict[str, Any]:
    """Fire the configured Claude Code Routine once for one Linear issue and record its session."""
    settings = require_enabled(db, user)
    identifier = (issue_identifier or "").strip().upper()
    if not ISSUE_IDENTIFIER_RE.match(identifier):
        raise SelfEvolutionError("issue_identifier must be a Linear identifier such as JAN-123")
    if not MIN_JOB_PROMPT_CHARS <= len((prompt or "").strip()) <= MAX_JOB_PROMPT_CHARS:
        raise SelfEvolutionError(f"prompt must be {MIN_JOB_PROMPT_CHARS} to {MAX_JOB_PROMPT_CHARS} characters")
    if not settings.routine_trigger_id or not settings.routine_token:
        raise refuse(db, user.id, Action.JOB_STARTED, "no Claude Code Routine trigger id and token", subject=identifier)
    if _has_recent_job(db, user.id, identifier):
        raise refuse(db, user.id, Action.JOB_STARTED, "a job for this issue already started today", subject=identifier)
    repo = target_repo(db, settings)
    url = routine_fire_url(settings.routine_trigger_id)
    token = security.security_service.decrypt(settings.routine_token)
    row = reserve(db, user.id, Action.JOB_STARTED, subject=identifier, data={"repo": repo})
    headers = {
        "Authorization": f"Bearer {token}",
        "anthropic-version": ROUTINE_ANTHROPIC_VERSION,
        "anthropic-beta": ROUTINE_BETA,
        "Content-Type": "application/json",
    }
    try:
        with core.http_client(ROUTINE_TIMEOUT_SECONDS) as client:
            response = client.post(url, json={"text": build_job_text(identifier, prompt, repo)}, headers=headers)
    except httpx.HTTPError as error:
        # Not retried: without an idempotency key a retry could start a second session.
        finish(db, row, Status.FAILED, detail=f"Routine endpoint unreachable: {error.__class__.__name__}")
        raise SelfEvolutionError(
            "the Claude Code Routine endpoint could not be reached; a session may or may not have started, "
            "so check claude.ai before trying again later"
        ) from error
    if response.status_code != 200:
        reference = _error_reference(response)
        finish(
            db,
            row,
            Status.FAILED,
            detail=f"HTTP {response.status_code} {reference.get('error_type', '')}".strip(),
            data=reference,
        )
        hint = " Try again later." if response.status_code in _RETRY_LATER_STATUSES else ""
        raise SelfEvolutionError(
            f"the Claude Code Routine refused the job (HTTP {response.status_code} "
            f"{reference.get('error_type', '')}: {reference.get('error_message', '')}).{hint}"
        )
    session = _session_reference(response)
    if not session["session_id"] or not session["session_url"]:
        # Without a valid session id and URL the job's pull request could never be tied back to it, so this
        # is not a start: it is recorded as failed, which also leaves the issue free for another attempt.
        reason = "HTTP 200 without a valid claude_code_session_id and claude_code_session_url"
        finish(db, row, Status.FAILED, detail=reason, data=session)
        return {
            "started": False,
            "issue": identifier,
            "reason": f"{reason}; a session may still have been created, so check claude.ai before retrying",
        }
    finish(db, row, Status.OK, detail=session["session_url"], data=session)
    return {"started": True, "issue": identifier, **session}
