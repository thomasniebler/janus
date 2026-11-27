"""Janus-seeded trigger definitions: briefing, digest, mail, knowledge, wiki, secretary (JAN-231/232/243/244).

Each built-in is an ordinary :class:`~janus.models.TriggerDefinition` the user
can edit (objective, tools, schedule) or disable, plus two code hooks keyed by
its ``builtin_key``:

* ``prepare`` runs right before the model is called and returns fresh context
  to append to the objective (today's planner summary, the digest's emails).
  It may return ``skip_output`` instead, which completes the run without an
  LLM call (e.g. "no digest emails since the last digest").
* ``finalize`` runs after a successful run with the model's final answer and
  delivers it (desktop notification, topic bookkeeping).

Both hooks only *feed* and *deliver*; everything in between is the normal
trigger run: the user's Janus character, the full skill library, the
definition's selected tools, and the unattended tool policy.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from janus import event_logger, llm_service, models, notifications, prompt_injection
from janus import trigger_definition_service as definitions
from janus.trigger_events import TriggerEvent

BUILTIN_BRIEFING = "builtin.briefing"
BUILTIN_DIGEST = "builtin.digest"
BUILTIN_MAIL = "builtin.mail"
BUILTIN_KNOWLEDGE = "builtin.knowledge"
BUILTIN_WIKI = "builtin.wiki"
BUILTIN_SECRETARY = "builtin.secretary"
BUILTIN_MEMORY_REVIEW = "builtin.memory_review"

#: Upper bound on digest emails handed to one digest run.
_DIGEST_EMAIL_LIMIT = 50
_DIGEST_CANDIDATE_BATCH = 200
_DIGEST_EMAIL_CHARS = 1_500


@dataclass(frozen=True)
class PreparedRun:
    """Context a built-in hands to its run, or a reason to skip the model call."""

    context: str | None = None
    skip_output: str | None = None
    state: dict[str, Any] = field(default_factory=dict)


Prepare = Callable[[Session, models.User, models.TriggerDefinition], PreparedRun]
Finalize = Callable[[Session, models.User, str, dict[str, Any]], None]


@dataclass(frozen=True)
class BuiltinTrigger:
    """Seed values and hooks for one built-in definition."""

    key: str
    name: str
    objective: str
    tool_ids: tuple[str, ...]
    kind: str
    schedule: dict[str, Any] | None = None
    event_type: str | None = None
    prepare: Prepare | None = None
    finalize: Finalize | None = None
    #: Whether the definition starts enabled; opt-in built-ins are seeded off.
    enabled: bool = True


# --- Morning briefing -------------------------------------------------------

BRIEFING_OBJECTIVE = (
    "Write my morning briefing.\n\n"
    "Based on today's schedule, active reminders, the medium-term preparation outlook, and any email context "
    "below, give me a short, encouraging morning briefing. Identify what needs focus today and call out any "
    "upcoming item in the next 4-6 weeks that likely needs preparation now. Use your tools if the summary "
    "below is not enough to be accurate.\n\n"
    "Keep it concise and professional. Use emojis. Reply with only the briefing text (Markdown)."
)


def _prepare_briefing(db: Session, user: models.User, _definition: models.TriggerDefinition) -> PreparedRun:
    # Imported lazily: the dashboard service imports the runtime adapter.
    from janus.services.dashboard_service import DashboardService

    payload = DashboardService(db).get_briefing_generation_context(user)
    parts = [
        "--- Today's planner summary (schedule, tasks, reminders, preparation outlook, email context) ---",
        # Calendar titles, imported tasks, reminders, and mail are outside text;
        # the tagged block is scanned for prompt injections before the run (JAN-127).
        prompt_injection.prepared_context_block("planner summary", str(payload["summary"])),
    ]
    prompt_override = payload.get("prompt_override")
    if isinstance(prompt_override, str) and prompt_override.strip():
        parts.extend(["--- Additional instructions from the dashboard briefing widget ---", prompt_override.strip()])
    topic_ids = [int(topic_id) for topic_id in payload.get("topic_ids", []) if isinstance(topic_id, int)]
    return PreparedRun(context="\n".join(parts), state={"topic_ids": topic_ids})


def _finalize_briefing(db: Session, user: models.User, _output: str, state: dict[str, Any]) -> None:
    from janus.services.topic_service import TopicService

    topic_ids = state.get("topic_ids") or []
    if topic_ids:
        TopicService(db).mark_topics_briefed(user_id=user.id, topic_ids=list(topic_ids))


# --- Afternoon digest -------------------------------------------------------

DIGEST_OBJECTIVE = (
    'Create a concise "Afternoon Digest" from the emails below.\n\n'
    "Use the structured topic threads as the primary grouping signal whenever they are present, and prefer "
    "durable thread names over one-off per-message buckets. Format the output as clean Markdown: a "
    "`## Topic Threads` section when topics exist (with `### <topic title>` subsections), and a "
    "`## Other Updates` section for emails without a durable topic. Highlight items that might be interesting "
    "but not urgent. If an email implies a task or event, propose it with suggest_planner_item.\n\n"
    "Reply with only the digest text."
)


def _digest_preference(db: Session, user_id: int) -> models.NotificationPreference:
    preference = (
        db.query(models.NotificationPreference).filter(models.NotificationPreference.user_id == user_id).first()
    )
    if preference is None:
        preference = models.NotificationPreference(user_id=user_id)
        db.add(preference)
        db.commit()
        db.refresh(preference)
    return preference


def _prepare_digest(db: Session, user: models.User, _definition: models.TriggerDefinition) -> PreparedRun:
    from janus.workers.email import _build_digest_generation_payloads, _email_matches_notification_filters

    preference = _digest_preference(db, user.id)
    now = datetime.now(UTC).replace(tzinfo=None)
    lookback_start = now - timedelta(hours=preference.digest_lookback_hours)
    last_digest = preference.last_daily_digest_at
    if last_digest is not None and last_digest.tzinfo is not None:
        last_digest = last_digest.astimezone(UTC).replace(tzinfo=None)
    since = max(lookback_start, last_digest) if last_digest is not None else lookback_start
    candidates = (
        db.query(models.EmailRecord)
        .join(models.EmailAccount)
        .filter(
            models.EmailAccount.user_id == user.id,
            models.EmailRecord.priority == models.Priority.DIGEST,
            models.EmailRecord.received_at >= since,
        )
        .order_by(models.EmailRecord.received_at.desc(), models.EmailRecord.id.desc())
    )
    # Apply the notification filters before capping: capping first could drop
    # every eligible email behind filtered-out newer ones, and the finalizer
    # advances last_daily_digest_at past them for good.
    filtered: list[models.EmailRecord] = []
    offset = 0
    while len(filtered) < _DIGEST_EMAIL_LIMIT:
        batch = candidates.offset(offset).limit(_DIGEST_CANDIDATE_BATCH).all()
        filtered.extend(email for email in batch if _email_matches_notification_filters(email, preference))
        if len(batch) < _DIGEST_CANDIDATE_BATCH:
            break
        offset += _DIGEST_CANDIDATE_BATCH
    filtered = filtered[:_DIGEST_EMAIL_LIMIT]
    state: dict[str, Any] = {"digested_at": now.isoformat()}
    if not filtered:
        return PreparedRun(skip_output="No new digest emails since the last digest.", state=state)

    payloads, topic_ids = _build_digest_generation_payloads(db, filtered, user=user)
    threads = llm_service._build_digest_topic_threads_text(payloads)  # noqa: SLF001 - shared prompt helper
    rendered = "\n\n".join(
        llm_service.format_email_content(
            index,
            payload.get("sender"),
            payload.get("subject"),
            payload.get("body") or payload.get("snippet"),
            max_content_chars=_DIGEST_EMAIL_CHARS,
        )
        for index, payload in enumerate(payloads, start=1)
    )
    context = "\n\n".join(
        part
        for part in (
            _pending_suggestions_context(db, user.id) or "",
            llm_service.UNTRUSTED_EMAIL_CONTENT_WARNING,
            prompt_injection.prepared_context_block("topic thread summary", threads) if threads else "",
            f"--- Digest emails ({len(filtered)}, email ids: {', '.join(str(email.id) for email in filtered)}) ---",
            rendered,
        )
        if part
    )
    state.update({"topic_ids": topic_ids, "email_count": len(filtered)})
    return PreparedRun(context=context, state=state)


def _finalize_digest(db: Session, user: models.User, output: str, state: dict[str, Any]) -> None:
    from janus.services.topic_service import TopicService

    preference = _digest_preference(db, user.id)
    digested_at = state.get("digested_at")
    if isinstance(digested_at, str):
        preference.last_daily_digest_at = datetime.fromisoformat(digested_at)
        db.commit()
    if not state.get("email_count"):
        return
    topic_ids = state.get("topic_ids") or []
    if topic_ids:
        TopicService(db).mark_topics_digested(user_id=user.id, topic_ids=list(topic_ids))
    if preference.notify_digest:
        notifications.NotificationService.notify_digest(output)
    event_logger.log(
        models.EventType.INFO,
        "BuiltinTriggers",
        f"Afternoon digest delivered for {state.get('email_count')} emails.",
        user_id=user.id,
    )


# --- Mail processing --------------------------------------------------------

#: How many pending planner suggestions a mail or digest run is shown, newest first.
_PENDING_SUGGESTION_CONTEXT_LIMIT = 40


def _pending_suggestions_context(db: Session, user_id: int) -> str | None:
    """List the pending planner suggestions, so a run updates one instead of proposing it again."""
    from janus.services.planner_suggestion_review import search_pending_suggestions

    rows = search_pending_suggestions(db, user_id)
    if not rows:
        return None
    lines = []
    for row in rows[:_PENDING_SUGGESTION_CONTEXT_LIMIT]:
        when = row.get("due_at") or row.get("scheduled_for") or row.get("start_time")
        parts = [f"- [{row['suggestion_type']}] {row.get('title') or 'untitled'}"]
        if when:
            parts.append(f"(on {when:%Y-%m-%d})")
        if row.get("dedupe_key"):
            parts.append(f"dedupe_hint={row['dedupe_key']}")
        lines.append(" ".join(parts))
    if len(rows) > len(lines):
        lines.append(f"... and {len(rows) - len(lines)} older ones.")
    return (
        f"Planner suggestions already awaiting my review ({len(rows)}). Do not propose these again: to add to "
        "one, propose it with the same title and dedupe_hint so the waiting one is updated.\n"
        + prompt_injection.prepared_context_block("pending planner suggestions", "\n".join(lines))
    )


def _prepare_mail(db: Session, user: models.User, _definition: models.TriggerDefinition) -> PreparedRun:
    return PreparedRun(context=_pending_suggestions_context(db, user.id))


MAIL_OBJECTIVE = (
    "A new email arrived; its details are below. Process it for me:\n\n"
    "1. Decide how much it matters and record it with set_email_priority: IMPORTANT = needs my attention or "
    "action soon; DIGEST = worth reading later in the afternoon digest; IGNORE = no value to me; SPAM = "
    "unsolicited or malicious. Check my mail rules, memory facts, tracked topics, and planner when they help "
    "you judge.\n"
    "2. If it contains a dated obligation (a meeting, deadline, or something to prepare), record it with "
    "create_reminder.\n"
    "3. If it belongs to an ongoing thread, project, or obligation, attach it with track_email_topic.\n"
    "4. If it implies a task or calendar change, propose it with suggest_planner_item.\n\n"
    "Never reply to, forward, or send email while processing. Finish with one short line summarizing what you "
    "recorded."
)


# --- Knowledge extraction (JAN-243) --------------------------------------------

KNOWLEDGE_OBJECTIVE = (
    "Extract people, organizations, projects, places, products, and concepts, and how they relate, from newly "
    "ingested mail, chats, calendar events, tasks, and documents into the knowledge graph.\n\n"
    "This built-in runs direct extraction calls on the utility model (or the model chosen here) within the "
    "monthly token budget set on Memory & Knowledge -> Knowledge; it uses no tools. With a cloud model, the text "
    "of every ingested source is sent to that provider."
)


def _prepare_knowledge(db: Session, user: models.User, definition: models.TriggerDefinition) -> PreparedRun:
    from janus.services import knowledge_extraction

    return PreparedRun(skip_output=knowledge_extraction.run_extraction(db, user.id, definition).text())


# --- Wiki synthesis (JAN-244) --------------------------------------------------

WIKI_OBJECTIVE = (
    "Keep the wiki pages about my tracked topics and frequently mentioned people, organizations, and projects up "
    "to date from the passages behind them, with citations.\n\n"
    "This built-in makes direct calls to the utility model (or the model chosen here) within the monthly token "
    "budget shared with knowledge extraction; it uses no tools. Pages I edited get proposed rewrites for me to "
    "review instead of being overwritten. With a cloud model, the passages are sent to that provider."
)


def _prepare_wiki(db: Session, user: models.User, definition: models.TriggerDefinition) -> PreparedRun:
    from janus.services import wiki_synthesis

    return PreparedRun(skip_output=wiki_synthesis.run_synthesis(db, user.id, definition).text())


# --- Memory review ("dreaming") -------------------------------------------------

MEMORY_REVIEW_OBJECTIVE = (
    "Review my memory overnight: check the facts Janus remembered against the conversations they came from, fix or "
    "archive the wrong ones with the exact passage as evidence, merge duplicates, and choose the few confirmed facts "
    "every session should know.\n\n"
    "This built-in makes direct calls to the utility model (or the model chosen here) within the monthly token "
    "budget shared with knowledge extraction and wiki synthesis; it uses no tools. Facts I wrote or edited myself "
    "are never changed, and my pins always win. With a cloud model, the source conversations are sent to that "
    "provider."
)


def _prepare_memory_review(db: Session, user: models.User, definition: models.TriggerDefinition) -> PreparedRun:
    from janus.services import memory_review

    return PreparedRun(skip_output=memory_review.run_review(db, user.id, definition).text())


# --- Proactive secretary --------------------------------------------------------

SECRETARY_OBJECTIVE = (
    "You are my secretary, thinking ahead on my behalf without being asked. Look at my world with the tools you have "
    "(calendar, planner, reminders, mail, WhatsApp, topics, memory, knowledge) and work out what I have not thought of "
    "yet:\n"
    "- What is coming in the next days or weeks that needs preparation now?\n"
    "- What is stuck, overdue, or waiting on me or on someone else?\n"
    "- What connects across mail, calendar, topics and memory that I would not notice myself?\n"
    "- What is worth looking into for me, even though I did not ask (a question to research, an option to weigh, an "
    "idea worth raising)? Pick one thing that matters and go a step deeper instead of skimming everything.\n\n"
    "Act on the safe parts yourself: create reminders, track topics, record durable facts, and propose planner "
    "tasks or events with propose_planner_item (I approve or dismiss them; never re-propose one listed below). "
    "Never send mail or messages to other people.\n\n"
    "Then decide how to reach me, and prefer the quietest option that works:\n"
    "1. Nothing worth my attention: finish with exactly NOTHING_TO_REPORT and do not call a delivery tool.\n"
    "2. I would want to read, decide, or answer something: start a conversation with start_conversation. Open with "
    "the finding, say what you did already, and ask the concrete question or offer the options. At most one per run.\n"
    "3. Only if it is time-critical (I must act within about a day and would miss it otherwise): also send one short "
    "notify_me_on_whatsapp message, in addition to the conversation, so I find the details there.\n\n"
    "Use the history below. Do not repeat what you already told me. If I ignored a topic twice, drop it. If I "
    "replied to a kind of message, do more of that kind. If you want to look at something again later, schedule a "
    "follow-up instead of keeping it in mind. Be specific and brief; a vague nudge is worse than silence."
)


def _prepare_secretary(db: Session, user: models.User, definition: models.TriggerDefinition) -> PreparedRun:
    from janus.services import secretary_service

    plan = secretary_service.prepare(db, user, definition)
    state = {"planner_fingerprint": plan.planner_fingerprint} if plan.planner_fingerprint else {}
    pending = None if plan.skip_output is not None else _pending_suggestions_context(db, user.id)
    context = "\n\n".join(part for part in (plan.context, pending) if part) or None
    return PreparedRun(context=context, skip_output=plan.skip_output, state=state)


def _finalize_secretary(db: Session, user: models.User, _output: str, state: dict[str, Any]) -> None:
    """After a run that thought, remember what the planners held, so only a real change re-opens the gate."""
    from janus.services import secretary_service

    fingerprint = state.get("planner_fingerprint")
    if isinstance(fingerprint, str) and fingerprint:
        secretary_service.remember_planner_fingerprint(db, user.id, fingerprint)


BUILTINS: dict[str, BuiltinTrigger] = {
    BUILTIN_BRIEFING: BuiltinTrigger(
        key=BUILTIN_BRIEFING,
        name="Morning briefing",
        objective=BRIEFING_OBJECTIVE,
        tool_ids=("core", "calendar", "jira", "planner.local", "topics", "memory", "mail"),
        kind=models.TriggerDefinitionKind.SCHEDULE.value,
        schedule={"type": "daily", "time": "06:30", "days": []},
        prepare=_prepare_briefing,
        finalize=_finalize_briefing,
    ),
    BUILTIN_DIGEST: BuiltinTrigger(
        key=BUILTIN_DIGEST,
        name="Afternoon digest",
        objective=DIGEST_OBJECTIVE,
        tool_ids=("core", "mail", "topics", "memory", "planner.local", "mail.processing"),
        kind=models.TriggerDefinitionKind.SCHEDULE.value,
        schedule={"type": "daily", "time": "16:00", "days": []},
        prepare=_prepare_digest,
        finalize=_finalize_digest,
    ),
    BUILTIN_MAIL: BuiltinTrigger(
        key=BUILTIN_MAIL,
        name="Mail processing",
        objective=MAIL_OBJECTIVE,
        tool_ids=("core", "mail", "mail.processing", "rules", "memory", "topics", "planner.local", "calendar"),
        kind=models.TriggerDefinitionKind.EVENT.value,
        event_type=TriggerEvent.MAIL_RECEIVED.value,
        prepare=_prepare_mail,
    ),
    BUILTIN_SECRETARY: BuiltinTrigger(
        key=BUILTIN_SECRETARY,
        name="Secretary",
        objective=SECRETARY_OBJECTIVE,
        tool_ids=(
            "core",
            "calendar",
            "jira",
            "planner.local",
            "topics",
            "memory",
            "mail",
            "whatsapp",
            "knowledge",
            "wiki",
            "web_search",
            "triggers",
            "planner.suggest",
            "conversations.start",
            "whatsapp.notify_me",
        ),
        kind=models.TriggerDefinitionKind.SCHEDULE.value,
        schedule={"type": "interval", "minutes": 60},
        prepare=_prepare_secretary,
        finalize=_finalize_secretary,
    ),
    BUILTIN_KNOWLEDGE: BuiltinTrigger(
        key=BUILTIN_KNOWLEDGE,
        name="Knowledge extraction",
        objective=KNOWLEDGE_OBJECTIVE,
        tool_ids=(),
        kind=models.TriggerDefinitionKind.SCHEDULE.value,
        schedule={"type": "interval", "minutes": 30},
        prepare=_prepare_knowledge,
        enabled=False,
    ),
    BUILTIN_WIKI: BuiltinTrigger(
        key=BUILTIN_WIKI,
        name="Wiki synthesis",
        objective=WIKI_OBJECTIVE,
        tool_ids=(),
        kind=models.TriggerDefinitionKind.SCHEDULE.value,
        schedule={"type": "interval", "minutes": 60},
        prepare=_prepare_wiki,
        enabled=False,
    ),
    BUILTIN_MEMORY_REVIEW: BuiltinTrigger(
        key=BUILTIN_MEMORY_REVIEW,
        name="Memory review",
        objective=MEMORY_REVIEW_OBJECTIVE,
        tool_ids=(),
        kind=models.TriggerDefinitionKind.SCHEDULE.value,
        # Overnight, like the offline replay it is modelled on; seeded in UTC.
        schedule={"type": "daily", "time": "03:17", "days": []},
        prepare=_prepare_memory_review,
    ),
}


def _seed_schedule(db: Session, user_id: int, spec: BuiltinTrigger) -> tuple[dict[str, Any] | None, bool]:
    """Return the seed schedule and enabled flag, honoring pre-existing digest settings."""
    if spec.key != BUILTIN_DIGEST:
        return spec.schedule, spec.enabled
    preference = (
        db.query(models.NotificationPreference).filter(models.NotificationPreference.user_id == user_id).first()
    )
    if preference is None:
        return spec.schedule, False
    schedule = {
        "type": "daily",
        "time": f"{preference.daily_digest_hour:02d}:{preference.daily_digest_minute:02d}",
        "days": [],
    }
    return schedule, bool(preference.daily_digest_enabled)


def ensure_builtin_definitions(db: Session, user_id: int) -> list[models.TriggerDefinition]:
    """Create any missing built-in definitions for one user; existing ones are never overwritten."""
    created: list[models.TriggerDefinition] = []
    known_tool_ids = {entry for entry in _registry_tool_ids()}
    for spec in BUILTINS.values():
        if definitions.get_builtin_definition(db, user_id, spec.key) is not None:
            continue
        schedule, enabled = _seed_schedule(db, user_id, spec)
        created.append(
            definitions.create_definition(
                db,
                user_id,
                origin=models.TriggerDefinitionOrigin.SYSTEM.value,
                builtin_key=spec.key,
                enabled=enabled,
                name=spec.name,
                objective=spec.objective,
                tool_ids=[tool_id for tool_id in spec.tool_ids if tool_id in known_tool_ids],
                kind=spec.kind,
                schedule=schedule,
                timezone="UTC",
                event_type=spec.event_type,
            )
        )
    return created


def sync_digest_schedule(db: Session, user_id: int) -> None:
    """Apply the notification preference's digest on/off and time to the built-in digest trigger."""
    ensure_builtin_definitions(db, user_id)
    digest = definitions.get_builtin_definition(db, user_id, BUILTIN_DIGEST)
    if digest is None:
        return
    schedule, enabled = _seed_schedule(db, user_id, BUILTINS[BUILTIN_DIGEST])
    definitions.update_definition(db, digest, schedule=schedule, timezone="UTC", enabled=enabled)


def adopt_client_timezone(db: Session, user_id: int, tz_name: str | None) -> None:
    """Move a still-untouched, UTC-seeded morning briefing and secretary to the user's browser timezone.

    Built-ins are seeded by the background worker before Janus knows where the
    user lives, so "06:30" would otherwise mean 06:30 UTC. The first time a
    browser reports its timezone, an unedited briefing adopts it; once the user
    has edited the definition, it is never changed behind their back.
    """
    if not tz_name or tz_name == "UTC":
        return
    try:
        cleaned = definitions.validate_timezone(tz_name)
    except definitions.TriggerDefinitionError:
        return
    for key in (BUILTIN_BRIEFING, BUILTIN_SECRETARY):
        builtin = definitions.get_builtin_definition(db, user_id, key)
        if builtin is None or builtin.timezone != "UTC":
            continue
        if builtin.updated_at and builtin.created_at and (builtin.updated_at - builtin.created_at).total_seconds() > 1:
            continue
        definitions.update_definition(db, builtin, timezone=cleaned)


def ensure_builtin_definitions_for_all_users(db: Session) -> int:
    """Seed built-ins for every user; returns how many definitions were created."""
    return sum(len(ensure_builtin_definitions(db, user_id)) for (user_id,) in db.query(models.User.id).all())


def _registry_tool_ids() -> list[str]:
    from janus.agent_tools import DEFAULT_TOOL_REGISTRY

    return [entry.id for entry in DEFAULT_TOOL_REGISTRY.entries()]


def todays_briefing(db: Session, definition: models.TriggerDefinition) -> models.RuntimeTrigger | None:
    """Return the newest completed briefing run from today (in the definition's timezone)."""
    latest = (
        db.query(models.RuntimeTrigger)
        .filter(
            models.RuntimeTrigger.definition_id == definition.id,
            models.RuntimeTrigger.status == models.RuntimeTriggerStatus.COMPLETED.value,
            models.RuntimeTrigger.output.is_not(None),
        )
        .order_by(models.RuntimeTrigger.completed_at.desc(), models.RuntimeTrigger.id.desc())
        .first()
    )
    if latest is None or latest.completed_at is None:
        return None
    zone = ZoneInfo(definition.timezone or "UTC")
    completed_local = latest.completed_at.replace(tzinfo=UTC).astimezone(zone).date()
    return latest if completed_local == datetime.now(zone).date() else None


# --- Self-evolution self-check (JAN-321) -----------------------------------------
# Not seeded: ``services/self_evolution_checks.schedule_self_check`` creates one
# one-time definition per release, keyed ``selfevo.check:<action id>``. Its run
# executes the checks in code (no model call) and handles a failure.

SELF_CHECK_KEY_PREFIX = "selfevo.check:"


def _prepare_self_check(db: Session, user: models.User, definition: models.TriggerDefinition) -> PreparedRun:
    from janus.services import self_evolution_checks

    check_id = int(str(definition.builtin_key).removeprefix(SELF_CHECK_KEY_PREFIX))
    return PreparedRun(skip_output=self_evolution_checks.run_self_check(db, user, check_id))


_SELF_CHECK = BuiltinTrigger(
    key=SELF_CHECK_KEY_PREFIX,
    name="Self-evolution self-check",
    objective="Run a scheduled production self-check.",
    tool_ids=(),
    kind=models.TriggerDefinitionKind.SCHEDULE.value,
    prepare=_prepare_self_check,
)


def builtin_for(definition: models.TriggerDefinition | None) -> BuiltinTrigger | None:
    """Return the hook spec for a built-in definition (or a self-evolution self-check), if it is one."""
    if definition is None or definition.builtin_key is None:
        return None
    if definition.builtin_key.startswith(SELF_CHECK_KEY_PREFIX):
        return _SELF_CHECK
    return BUILTINS.get(definition.builtin_key)


__all__ = [
    "BUILTINS",
    "BUILTIN_BRIEFING",
    "BUILTIN_DIGEST",
    "BUILTIN_SECRETARY",
    "BUILTIN_KNOWLEDGE",
    "BUILTIN_MAIL",
    "BUILTIN_MEMORY_REVIEW",
    "BUILTIN_WIKI",
    "BuiltinTrigger",
    "PreparedRun",
    "adopt_client_timezone",
    "sync_digest_schedule",
    "builtin_for",
    "ensure_builtin_definitions",
    "ensure_builtin_definitions_for_all_users",
    "todays_briefing",
]
