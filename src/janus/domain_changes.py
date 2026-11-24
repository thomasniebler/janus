"""Owner-scoped domain-change notifications for live UI cache invalidation (JAN-104).

Every committed write to a tracked model publishes the *name* of the UI
domain it belongs to (``memory``, ``topics``, ``trigger_definitions`` ...) to
the user who owns the row. The browser subscribes once over ``/ws/changes``
(``janus.websocket.changes``) and turns each domain into TanStack Query
invalidations; no domain data travels over the channel.

Detection is centralised in SQLAlchemy session events instead of per call
site, so a REST router, a chat tool, a trigger run, a delegated task and a
background worker all emit the same events:

* ``after_flush`` records the domains of new, modified and deleted tracked
  instances, resolving each row's owner;
* ``do_orm_execute`` records ORM bulk ``update()``/``delete()`` statements
  (for example the trigger worker's claim/complete transitions) by selecting
  the owners of the rows the statement's ``WHERE`` clause matches;
* ``after_commit`` publishes what the transaction recorded, and a rollback of
  the outermost transaction drops it.

All of this is skipped while no browser is subscribed. The bus is in-process,
so it assumes the single backend process Janus runs as today. Raw ``text()``
SQL and Core statements outside the ORM are not observed.

This channel is deliberately separate from the trigger event catalog
(``trigger_definition_service`` event activations such as ``mail.received``):
domain changes are cache-invalidation hints for the UI, never trigger
activations.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import event, inspect, select
from sqlalchemy.engine import Connection
from sqlalchemy.orm import ORMExecuteState, Session, SessionTransaction
from sqlalchemy.sql.elements import ColumnElement

from janus import models
from janus import models_knowledge as knowledge_models

logger = logging.getLogger("janus")

BROADCAST = object()
"""Owner marker for global rows (the shared skill library) that every user sees."""

_PENDING_KEY = "janus_domain_changes"

MEMORY = "memory"
TOPICS = "topics"
RULES = "rules"
EMAIL_CLARIFICATIONS = "email_clarifications"
EMAILS = "emails"
PLANNER = "planner"
SKILLS = "skills"
DASHBOARD_WIDGETS = "dashboard_widgets"
TRIGGER_DEFINITIONS = "trigger_definitions"
TRIGGER_RUNS = "trigger_runs"
KNOWLEDGE = "knowledge"
WIKI = "wiki"
CHARACTER = "character"
TOOLS = "tools"
WHATSAPP = "whatsapp"
CONVERSATIONS = "conversations"

DOMAINS: tuple[str, ...] = (
    MEMORY,
    TOPICS,
    RULES,
    EMAIL_CLARIFICATIONS,
    EMAILS,
    PLANNER,
    SKILLS,
    DASHBOARD_WIDGETS,
    TRIGGER_DEFINITIONS,
    TRIGGER_RUNS,
    KNOWLEDGE,
    WIKI,
    CHARACTER,
    TOOLS,
    WHATSAPP,
    CONVERSATIONS,
)


@dataclass(frozen=True)
class _ViaParent:
    """Resolve a row's owner through a foreign key to an owned parent row.

    ``lookup(ids)`` selects ``(parent_id, owner_id)`` pairs; ``bulk(where)``
    selects the owner ids of the child rows a bulk statement matches.
    """

    fk_attr: str
    lookup: Callable[[set[Any]], Any]
    bulk: Callable[[ColumnElement[bool] | None], Any]


@dataclass(frozen=True)
class _Tracked:
    """How one model maps to a UI domain and to the user who owns its rows."""

    domain: str
    owner_attr: str | None = "user_id"
    via: _ViaParent | None = None
    is_global: bool = False


def _where(statement: Any, where: ColumnElement[bool] | None) -> Any:
    return statement if where is None else statement.where(where)


def _email_account_owner(fk_attr: str, child: Any) -> _ViaParent:
    return _ViaParent(
        fk_attr=fk_attr,
        lookup=lambda ids: select(models.EmailAccount.id, models.EmailAccount.user_id).where(
            models.EmailAccount.id.in_(ids)
        ),
        bulk=lambda where: _where(
            select(models.EmailAccount.user_id).join(child, child.account_id == models.EmailAccount.id), where
        ),
    )


def _planner_account_owner(child: Any) -> _ViaParent:
    return _ViaParent(
        fk_attr="planner_account_id",
        lookup=lambda ids: select(models.PlannerAccount.id, models.PlannerAccount.user_id).where(
            models.PlannerAccount.id.in_(ids)
        ),
        bulk=lambda where: _where(
            select(models.PlannerAccount.user_id).join(child, child.planner_account_id == models.PlannerAccount.id),
            where,
        ),
    )


_REMINDER_OWNER = _ViaParent(
    fk_attr="source_email_id",
    lookup=lambda ids: (
        select(models.EmailRecord.id, models.EmailAccount.user_id)
        .join(models.EmailAccount, models.EmailRecord.account_id == models.EmailAccount.id)
        .where(models.EmailRecord.id.in_(ids))
    ),
    bulk=lambda where: _where(
        select(models.EmailAccount.user_id)
        .join(models.EmailRecord, models.EmailRecord.account_id == models.EmailAccount.id)
        .join(models.ReminderMemory, models.ReminderMemory.source_email_id == models.EmailRecord.id),
        where,
    ),
)

_TOPIC_EVIDENCE_OWNER = _ViaParent(
    fk_attr="topic_id",
    lookup=lambda ids: select(models.TrackedTopic.id, models.TrackedTopic.user_id).where(
        models.TrackedTopic.id.in_(ids)
    ),
    bulk=lambda where: _where(
        select(models.TrackedTopic.user_id).join(
            models.TopicEvidence, models.TopicEvidence.topic_id == models.TrackedTopic.id
        ),
        where,
    ),
)

_MCP_TOOL_OWNER = _ViaParent(
    fk_attr="server_id",
    lookup=lambda ids: select(models.MCPServer.id, models.MCPServer.user_id).where(models.MCPServer.id.in_(ids)),
    bulk=lambda where: _where(
        select(models.MCPServer.user_id).join(
            models.MCPDiscoveredTool, models.MCPDiscoveredTool.server_id == models.MCPServer.id
        ),
        where,
    ),
)

TRACKED_MODELS: dict[type, _Tracked] = {
    models.AgentMemoryFact: _Tracked(MEMORY),
    models.AgentMemoryCompactionRun: _Tracked(MEMORY),
    models.TrackedTopic: _Tracked(TOPICS),
    models.TopicEvidence: _Tracked(TOPICS, owner_attr=None, via=_TOPIC_EVIDENCE_OWNER),
    models.TopicSuggestion: _Tracked(TOPICS),
    models.Rule: _Tracked(RULES),
    models.EmailClarificationRequest: _Tracked(EMAIL_CLARIFICATIONS),
    models.EmailRecord: _Tracked(EMAILS, owner_attr=None, via=_email_account_owner("account_id", models.EmailRecord)),
    models.PlannerTask: _Tracked(PLANNER),
    models.PlannerEvent: _Tracked(PLANNER),
    models.PlannerSuggestion: _Tracked(PLANNER),
    models.PlannerLink: _Tracked(PLANNER),
    models.TaskRecord: _Tracked(PLANNER, owner_attr=None, via=_planner_account_owner(models.TaskRecord)),
    models.CalendarEvent: _Tracked(PLANNER, owner_attr=None, via=_planner_account_owner(models.CalendarEvent)),
    models.ReminderMemory: _Tracked(PLANNER, owner_attr=None, via=_REMINDER_OWNER),
    models.AgentSkill: _Tracked(SKILLS, owner_attr=None, is_global=True),
    models.DashboardWidgetPreference: _Tracked(DASHBOARD_WIDGETS),
    models.TriggerDefinition: _Tracked(TRIGGER_DEFINITIONS),
    models.RuntimeTrigger: _Tracked(TRIGGER_RUNS),
    knowledge_models.KnowledgeSource: _Tracked(KNOWLEDGE),
    knowledge_models.KnowledgeEntity: _Tracked(KNOWLEDGE),
    knowledge_models.KnowledgeRelation: _Tracked(KNOWLEDGE),
    knowledge_models.KnowledgeMergeSuggestion: _Tracked(KNOWLEDGE),
    knowledge_models.KnowledgeChunk: _Tracked(KNOWLEDGE),
    knowledge_models.KnowledgeMention: _Tracked(KNOWLEDGE),
    knowledge_models.KnowledgeLlmUsage: _Tracked(KNOWLEDGE),
    knowledge_models.WikiPage: _Tracked(WIKI),
    knowledge_models.WikiPageRevision: _Tracked(WIKI),
    models.CharacterProfile: _Tracked(CHARACTER),
    # Adaptive state, Janus's proposals and the audit trail (JAN-301).
    models.CharacterStateRecord: _Tracked(CHARACTER),
    models.CharacterStateProposal: _Tracked(CHARACTER),
    models.CharacterStateChange: _Tracked(CHARACTER),
    models.MCPServer: _Tracked(TOOLS),
    models.MCPDiscoveredTool: _Tracked(TOOLS, owner_attr=None, via=_MCP_TOOL_OWNER),
    models.ToolPolicy: _Tracked(TOOLS),
    # Connection status flips (connected/degraded) come from the connector thread (JAN-223).
    models.WhatsAppAccount: _Tracked(WHATSAPP),
    models.WhatsAppConversation: _Tracked(WHATSAPP),
    models.WhatsAppMessage: _Tracked(WHATSAPP),
    # JAN-319: a conversation Janus started (or one the user opened) refreshes the list and the Chat badge.
    models.Conversation: _Tracked(CONVERSATIONS),
}


class _Subscription:
    """One connected browser socket: a coalescing set of changed domains."""

    def __init__(self, user_id: int, loop: asyncio.AbstractEventLoop) -> None:
        self.user_id = user_id
        self._loop = loop
        self._pending: set[str] = set()
        self._ready = asyncio.Event()

    def deliver_threadsafe(self, domains: frozenset[str]) -> None:
        try:  # noqa: SIM105
            self._loop.call_soon_threadsafe(self._add, domains)
        except RuntimeError:
            # The socket's event loop already closed; the subscriber is going away.
            pass

    def _add(self, domains: frozenset[str]) -> None:
        self._pending |= domains
        self._ready.set()

    async def wait(self, timeout: float) -> frozenset[str]:
        """Wait up to ``timeout`` seconds and return (and clear) the changed domains."""
        try:
            await asyncio.wait_for(self._ready.wait(), timeout)
        except TimeoutError:
            return frozenset()
        return self.take()

    def take(self) -> frozenset[str]:
        """Return (and clear) the domains changed since the last read, without waiting."""
        changed = frozenset(self._pending)
        self._pending.clear()
        self._ready.clear()
        return changed


class DomainChangeBus:
    """Thread-safe in-process fan-out of committed domain changes to subscribers."""

    def __init__(self) -> None:
        """Start with no subscribers."""
        self._lock = threading.Lock()
        self._subscribers: dict[int, set[_Subscription]] = defaultdict(set)

    def subscribe(self, user_id: int) -> _Subscription:
        """Register a subscriber for ``user_id`` on the running event loop."""
        subscription = _Subscription(user_id, asyncio.get_running_loop())
        with self._lock:
            self._subscribers[user_id].add(subscription)
        return subscription

    def unsubscribe(self, subscription: _Subscription) -> None:
        """Remove a subscriber; unknown subscribers are ignored."""
        with self._lock:
            subscribers = self._subscribers.get(subscription.user_id)
            if subscribers is None:
                return
            subscribers.discard(subscription)
            if not subscribers:
                del self._subscribers[subscription.user_id]

    def has_subscribers(self) -> bool:
        """Whether any browser is subscribed (the session hooks do nothing otherwise)."""
        with self._lock:
            return bool(self._subscribers)

    def publish(self, changes: dict[Any, set[str]]) -> None:
        """Deliver ``{owner_id | BROADCAST: domains}`` to the matching subscribers."""
        with self._lock:
            targets: list[tuple[_Subscription, frozenset[str]]] = []
            broadcast = frozenset(changes.get(BROADCAST, ()))
            for user_id, subscribers in self._subscribers.items():
                domains = frozenset(changes.get(user_id, ())) | broadcast
                if domains:
                    targets.extend((subscription, domains) for subscription in subscribers)
        for subscription, domains in targets:
            subscription.deliver_threadsafe(domains)


bus = DomainChangeBus()


def _pending(session: Session) -> dict[Any, set[str]]:
    return session.info.setdefault(_PENDING_KEY, defaultdict(set))


def _loaded_value(instance: Any, attr: str) -> Any:
    """Return an attribute value without triggering a lazy load (``None`` if unloaded)."""
    return inspect(instance).dict.get(attr)


def _collect_instance_owners(
    connection: Connection, instances: Iterable[Any], record: Callable[[Any, str], None]
) -> None:
    """Record ``(owner, domain)`` for each tracked instance, batching parent lookups."""
    via_ids: dict[_ViaParent, set[Any]] = defaultdict(set)
    via_domain: dict[_ViaParent, str] = {}
    for instance in instances:
        spec = TRACKED_MODELS.get(type(instance))
        if spec is None:
            continue
        if spec.is_global:
            record(BROADCAST, spec.domain)
        elif spec.via is not None:
            fk_value = _loaded_value(instance, spec.via.fk_attr)
            if fk_value is not None:
                via_ids[spec.via].add(fk_value)
                via_domain[spec.via] = spec.domain
        elif spec.owner_attr is not None:
            owner = _loaded_value(instance, spec.owner_attr)
            if owner is not None:
                record(owner, spec.domain)
    for via, ids in via_ids.items():
        for _parent_id, owner in connection.execute(via.lookup(ids)):
            if owner is not None:
                record(owner, via_domain[via])


def _owner_path_attr(spec: _Tracked) -> str | None:
    if spec.via is not None:
        return spec.via.fk_attr
    return spec.owner_attr


def _before_flush(session: Session, _flush_context: Any, _instances: Any) -> None:
    """Load the owner path of rows about to be deleted while the rows still exist."""
    if not bus.has_subscribers():
        return
    with session.no_autoflush:
        for instance in session.deleted:
            spec = TRACKED_MODELS.get(type(instance))
            attr = None if spec is None else _owner_path_attr(spec)
            if attr is not None and attr not in inspect(instance).dict:
                try:
                    getattr(instance, attr)
                except Exception:  # pragma: no cover - the row may already be gone
                    logger.debug("Could not load %s of a deleted %s", attr, type(instance).__name__)


def _after_flush(session: Session, _flush_context: Any) -> None:
    if not bus.has_subscribers():
        return
    instances = [*session.new, *session.deleted]
    instances.extend(
        instance
        for instance in session.dirty
        if type(instance) in TRACKED_MODELS and session.is_modified(instance, include_collections=False)
    )
    if not any(type(instance) in TRACKED_MODELS for instance in instances):
        return
    pending = _pending(session)
    try:
        _collect_instance_owners(session.connection(), instances, lambda owner, domain: pending[owner].add(domain))
    except Exception:  # pragma: no cover - notifications must never break a write
        logger.exception("Could not record domain changes for a flush")


def _do_orm_execute(state: ORMExecuteState) -> None:
    if not (state.is_update or state.is_delete) or state.bind_mapper is None:
        return
    if not bus.has_subscribers():
        return
    model = state.bind_mapper.class_
    spec = TRACKED_MODELS.get(model)
    if spec is None:
        return
    pending = _pending(state.session)
    if spec.is_global:
        pending[BROADCAST].add(spec.domain)
        return
    where = state.statement.whereclause
    try:
        if spec.via is not None:
            owners_query = spec.via.bulk(where)
        elif spec.owner_attr is not None:
            owners_query = _where(select(getattr(model, spec.owner_attr)), where)
        else:  # pragma: no cover - every tracked model declares an owner path
            return
        connection = state.session.connection()
        for (owner,) in connection.execute(owners_query.distinct()):
            if owner is not None:
                pending[owner].add(spec.domain)
    except Exception:  # pragma: no cover - notifications must never break a write
        logger.exception("Could not record domain changes for a bulk %s", model.__name__)


def _after_commit(session: Session) -> None:
    changes = session.info.pop(_PENDING_KEY, None)
    if changes:
        bus.publish(changes)


def _after_soft_rollback(session: Session, previous_transaction: SessionTransaction) -> None:
    if not previous_transaction.nested:
        session.info.pop(_PENDING_KEY, None)


_install_lock = threading.Lock()
_installed = False


def install() -> None:
    """Attach the session listeners once per process (idempotent)."""
    global _installed
    with _install_lock:
        if _installed:
            return
        event.listen(Session, "before_flush", _before_flush)
        event.listen(Session, "after_flush", _after_flush)
        event.listen(Session, "do_orm_execute", _do_orm_execute)
        event.listen(Session, "after_commit", _after_commit)
        event.listen(Session, "after_soft_rollback", _after_soft_rollback)
        _installed = True
