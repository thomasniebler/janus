"""Tests for owner-scoped domain-change notifications and ``/ws/changes`` (JAN-104)."""

import asyncio
import json
import re
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from websockets.exceptions import ConnectionClosed, InvalidStatus

from janus import config, domain_changes, models, security, trigger_service
from janus.agent_toolboxes.memory import build_memory_tools
from janus.agent_toolboxes.shared import ToolContext
from janus.agent_toolboxes.triggers import build_trigger_tools
from janus.services.memory_service import MemoryService
from janus.triggers import TriggerEnvelope, TriggerKind
from janus.websocket import changes as changes_socket

FRONTEND_MAPPING = Path(__file__).resolve().parents[1] / "frontend" / "src" / "lib" / "domainChanges.ts"


@pytest.fixture(autouse=True)
def _installed_listeners():
    domain_changes.install()


@contextmanager
def subscribed(user_id: int):
    """Subscribe ``user_id`` on a private loop; yields a reader of the delivered domains."""
    loop = asyncio.new_event_loop()

    async def _subscribe():
        return domain_changes.bus.subscribe(user_id)

    async def _read(subscription):
        await asyncio.sleep(0)
        return subscription.take()

    subscription = loop.run_until_complete(_subscribe())
    try:
        yield lambda: loop.run_until_complete(_read(subscription))
    finally:
        domain_changes.bus.unsubscribe(subscription)
        loop.close()


def _user(db, username: str) -> models.User:
    user = models.User(username=username, hashed_password="x", is_admin=False)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def test_committed_write_reaches_only_its_owner(db):
    """Committed write reaches only its owner."""
    owner = _user(db, "owner")
    other = _user(db, "other")
    with subscribed(owner.id) as owner_changes, subscribed(other.id) as other_changes:
        MemoryService(db).create_fact(user_id=owner.id, summary="Prefers tea")

        assert owner_changes() == {"memory"}
        assert other_changes() == frozenset()


def test_rolled_back_write_publishes_nothing(db):
    """Rolled back write publishes nothing."""
    owner = _user(db, "owner")
    with subscribed(owner.id) as changes:
        db.add(models.Rule(user_id=owner.id, name="r", pattern="x", field="subject", priority=models.Priority.DIGEST))
        db.flush()
        db.rollback()
        assert changes() == frozenset()

        db.commit()
        assert changes() == frozenset()


def test_nothing_is_recorded_without_subscribers(db):
    """Nothing is recorded without subscribers."""
    owner = _user(db, "owner")
    assert not domain_changes.bus.has_subscribers()
    db.add(models.Rule(user_id=owner.id, name="r", pattern="x", field="subject", priority=models.Priority.DIGEST))
    db.flush()
    assert domain_changes._PENDING_KEY not in db.info
    db.commit()


def test_update_and_delete_are_published(db):
    """Update and delete are published."""
    owner = _user(db, "owner")
    topic = models.TrackedTopic(user_id=owner.id, slug="vendor", title="Vendor")
    db.add(topic)
    db.commit()
    with subscribed(owner.id) as changes:
        topic.title = "Vendor contract"
        db.commit()
        assert changes() == {"topics"}

        db.delete(topic)
        db.commit()
        assert changes() == {"topics"}


def test_unmodified_dirty_rows_and_untracked_models_are_ignored(db):
    """Unmodified dirty rows and untracked models are ignored."""
    owner = _user(db, "owner")
    with subscribed(owner.id) as changes:
        db.add(models.Event(event_type=models.EventType.INFO, source="test", message="noise"))
        db.commit()
        assert changes() == frozenset()


def test_owner_is_resolved_through_the_email_account(db):
    """Owner is resolved through the email account."""
    owner = _user(db, "owner")
    account = models.EmailAccount(user_id=owner.id, name="inbox", email="o@example.com")
    db.add(account)
    db.commit()
    with subscribed(owner.id) as changes:
        db.add(models.EmailRecord(account_id=account.id, message_id="<m1>", subject="Hi"))
        db.commit()
        assert changes() == {"emails"}


def test_global_skill_library_is_broadcast(db):
    """Global skill library is broadcast."""
    first = _user(db, "first")
    second = _user(db, "second")
    with subscribed(first.id) as first_changes, subscribed(second.id) as second_changes:
        db.add(models.AgentSkill(name="triage", description="d", content="c"))
        db.commit()
        assert first_changes() == {"skills"}
        assert second_changes() == {"skills"}


def test_orm_bulk_update_resolves_owners_from_its_where_clause(db):
    """Orm bulk update resolves owners from its where clause."""
    owner = _user(db, "owner")
    other = _user(db, "other")
    record, _duplicate = trigger_service.enqueue_trigger(
        db,
        TriggerEnvelope(
            trigger_id="bulk-1",
            idempotency_key="bulk-1",
            task_id="task-bulk-1",
            kind=TriggerKind.MANUAL,
            source="test",
            payload={"objective": "x"},
            owner_id=owner.id,
        ),
    )
    with subscribed(owner.id) as owner_changes, subscribed(other.id) as other_changes:
        # The worker's claim is an ORM bulk UPDATE matched by primary key only.
        assert trigger_service.claim_trigger(db, record.id) is not None
        assert owner_changes() == {"trigger_runs"}
        assert other_changes() == frozenset()


def test_chat_tool_mutation_in_its_isolated_session_is_published(db):
    """Chat tool mutation in its isolated session is published."""
    owner = _user(db, "owner")
    tools = {tool.name: tool for tool in build_memory_tools(ToolContext(db=db, user=owner))}
    with subscribed(owner.id) as changes:
        tools["add_memory_fact"].invoke({"summary": "Lives in Kassel"})
        assert changes() == {"memory"}


def test_agent_authored_follow_up_publishes_trigger_domains(db):
    """Agent authored follow up publishes trigger domains."""
    owner = _user(db, "owner")
    tools = {tool.name: tool for tool in build_trigger_tools(ToolContext(db=db, user=owner))}
    due_at = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    with subscribed(owner.id) as changes:
        tools["create_scheduled_trigger"].invoke({"due_at": due_at, "objective": "Check the reply", "task_id": "t1"})
        assert changes() == {"trigger_definitions", "trigger_runs"}


def test_knowledge_usage_and_chunks_are_published(db):
    """Extraction bookkeeping refreshes the open Extraction card."""
    from janus import models_knowledge

    owner = _user(db, "owner")
    with subscribed(owner.id) as changes:
        db.add(models_knowledge.KnowledgeLlmUsage(user_id=owner.id, period="2026-10", input_tokens=10, calls=1))
        db.commit()
        assert changes() == {"knowledge"}


def test_whatsapp_conversation_writes_are_published(db):
    """Connector writes refresh the dashboard WhatsApp widget live."""
    owner = _user(db, "owner")
    account = models.WhatsAppAccount(user_id=owner.id, display_name="phone")
    db.add(account)
    db.commit()
    with subscribed(owner.id) as changes:
        db.add(
            models.WhatsAppConversation(
                user_id=owner.id,
                whatsapp_account_id=account.id,
                provider_chat_id="123@s.whatsapp.net",
                conversation_type=next(iter(models.WhatsAppConversationType)),
                title="Ana",
            )
        )
        db.commit()
        assert changes() == {"whatsapp"}
    assert domain_changes.TRACKED_MODELS[models.WhatsAppMessage].domain == "whatsapp"


def test_whatsapp_account_status_changes_are_published(db):
    """A connector's status flip refreshes the open Settings account list (JAN-223 regression).

    The connector thread writes `connection_status` (connected/degraded) after the request
    that started it has returned, so the account list only showed it after a reload.
    """
    owner = _user(db, "owner")
    other = _user(db, "other")
    account = models.WhatsAppAccount(user_id=owner.id, display_name="phone")
    db.add(account)
    db.commit()
    with subscribed(owner.id) as owner_changes, subscribed(other.id) as other_changes:
        account.connection_status = models.WhatsAppConnectionStatus.DEGRADED
        account.last_sync_error = "Disconnected from WhatsApp."
        db.commit()
        assert owner_changes() == {"whatsapp"}
        assert other_changes() == set()


def test_frontend_mapping_covers_every_backend_domain():
    """The browser must know how to invalidate every domain the backend can publish."""
    source = FRONTEND_MAPPING.read_text(encoding="utf-8")
    block = source.split("export const DOMAIN_QUERY_KEYS = {", 1)[1].split("} satisfies", 1)[0]
    frontend_domains = set(re.findall(r"^\s+([a-z_]+):", block, flags=re.MULTILINE))
    assert frontend_domains == set(domain_changes.DOMAINS)


# --- /ws/changes ------------------------------------------------------------


@pytest.fixture
def auth_client(client, db):
    """Return a client logged in as a fresh non-admin user."""
    hashed = security.security_service.get_password_hash("pw")
    db.add(models.User(username="live", hashed_password=hashed, is_admin=False))
    db.commit()
    response = client.post("/api/auth/login", json={"username": "live", "password": "pw"})
    client.headers[security.CSRF_HEADER_NAME] = response.json()["csrf_token"]
    return client


def test_changes_socket_rejects_anonymous_connections(client):
    """Changes socket rejects anonymous connections."""
    with client.websocket_connect("/ws/changes") as ws, pytest.raises(ConnectionClosed) as excinfo:
        ws.receive_text(timeout=5)
    assert excinfo.value.rcvd.code == 1008


def test_changes_socket_rejects_unexpected_origin(auth_client, monkeypatch):
    """Changes socket rejects unexpected origin."""
    monkeypatch.setattr(config.settings, "allowed_origins", ["https://janus.example.com"])
    with pytest.raises(InvalidStatus) as excinfo:  # noqa: SIM117
        with auth_client.websocket_connect("/ws/changes", headers={"origin": "https://evil.example.com"}):
            pass
    assert excinfo.value.response.status_code == 403


def test_changes_socket_closes_once_the_session_is_logged_out(auth_client, monkeypatch):
    """Changes socket closes once the session is logged out."""
    monkeypatch.setattr(changes_socket, "HEARTBEAT_SECONDS", 0.2)
    with auth_client.websocket_connect("/ws/changes") as ws:
        assert json.loads(ws.receive_text(timeout=5)) == {"type": "ready"}
        assert json.loads(ws.receive_text(timeout=5)) == {"type": "ping"}

        assert auth_client.post("/api/auth/logout").status_code == 200

        with pytest.raises(ConnectionClosed) as excinfo:
            while True:
                ws.receive_text(timeout=5)
    assert excinfo.value.rcvd.code == 1008


def test_changes_socket_streams_rest_mutations(auth_client):
    """Changes socket streams rest mutations."""
    with auth_client.websocket_connect("/ws/changes") as ws:
        assert json.loads(ws.receive_text(timeout=5)) == {"type": "ready"}

        response = auth_client.post("/api/memory/", json={"summary": "Uses the night train"})
        assert response.status_code == 201

        assert json.loads(ws.receive_text(timeout=5)) == {"type": "changed", "domains": ["memory"]}


def test_agent_proposal_lifecycle_publishes_trigger_definitions(db):
    """Janus's drafts, approvals, and rejections refresh the Suggested automations UI (JAN-235)."""
    from janus import trigger_proposal_service as proposals

    owner = _user(db, "proposal-owner")
    fields = {
        "name": "Monday review",
        "objective": "Remind me to review pull requests.",
        "tool_ids": ["core"],
        "kind": "manual",
        "rationale": "You asked for it.",
    }
    with subscribed(owner.id) as changes:
        draft = proposals.create_proposal(db, owner.id, **fields)
        assert changes() == {"trigger_definitions"}
        proposals.reject_proposal(db, draft, reason="No")
        assert changes() == {"trigger_definitions"}
