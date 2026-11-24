"""WhatsApp (JAN-361) and planner (JAN-362) trigger events: who emits what, and when not."""

import importlib.util
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from janus import database, models, planner_service
from janus import trigger_definition_service as definitions
from janus.trigger_events import TriggerEvent
from janus.whatsapp_neonize import NeonizeConnector, _translate_live_message
from janus.whatsapp_trigger_events import referenced_users

# Uses real Neonize protobuf objects (or segno), which are the optional `whatsapp` extra.
requires_whatsapp_extra = pytest.mark.skipif(
    importlib.util.find_spec("neonize") is None, reason="needs the optional 'whatsapp' extra"
)


def _user(db) -> models.User:
    user = models.User(username="owner", hashed_password="x", is_admin=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _subscribe(db, user_id: int, event: TriggerEvent) -> models.TriggerDefinition:
    return definitions.create_definition(
        db,
        user_id,
        now=datetime(2026, 11, 10, 12, 0, tzinfo=UTC),
        name=f"React to {event.value}",
        objective="React.",
        tool_ids=["core"],
        kind="event",
        schedule=None,
        event_type=event.value,
        timezone="UTC",
    )


def _runs(db, event: TriggerEvent) -> list[models.RuntimeTrigger]:
    return [run for run in db.query(models.RuntimeTrigger).all() if run.payload.get("event", {}).get("type") == event]


# --- WhatsApp -----------------------------------------------------------------------------------


def _live_event(
    *, text="hi", message_id="MSG1", is_from_me=False, is_group=False, mentions=(), quoted=None, chat=None, message=None
):
    from neonize.proto.Neonize_pb2 import JID, MessageInfo, MessageSource
    from neonize.proto.Neonize_pb2 import Message as MessageEv
    from neonize.proto.waE2E.WAWebProtobufsE2E_pb2 import ContextInfo, ExtendedTextMessage
    from neonize.proto.waE2E.WAWebProtobufsE2E_pb2 import Message as WAMessage

    chat_user, chat_server = chat or (("g1", "g.us") if is_group else ("49123", "s.whatsapp.net"))
    chat_jid = JID(User=chat_user, Server=chat_server)
    sender_jid = JID(User="49999", Server="s.whatsapp.net")
    source = MessageSource(Chat=chat_jid, Sender=sender_jid, IsFromMe=is_from_me, IsGroup=is_group)
    info = MessageInfo(ID=message_id, Timestamp=1732000000, Pushname="Alice", MessageSource=source)
    if mentions or quoted:
        context = ContextInfo(mentionedJID=list(mentions), participant=quoted or "", stanzaID="Q1" if quoted else "")
        message = WAMessage(extendedTextMessage=ExtendedTextMessage(text=text, contextInfo=context))
    elif message is None:
        message = WAMessage(conversation=text)
    return MessageEv(Info=info, Message=message)


@pytest.fixture
def wa(db, monkeypatch, testing_session_local):
    """A connected WhatsApp account with a definition subscribed to every WhatsApp event."""
    monkeypatch.setattr(database, "SessionLocal", testing_session_local)
    user = _user(db)
    account = models.WhatsAppAccount(user_id=user.id, display_name="Phone", session_database_path="/tmp/a.db")
    db.add(account)
    db.commit()
    connector = NeonizeConnector(session_database_path="/tmp/a.db", client_uuid="a-1", account_id=account.id)
    connector.own_users = frozenset({"49555", "777lid"})
    for event in TriggerEvent:
        if event.value.startswith("whatsapp."):
            _subscribe(db, user.id, event)
    return SimpleNamespace(connector=connector, user=user, account=account)


def _deliver(connector, event, *, live=True):
    connector._ingest(*_translate_live_message(event), live=live)


@requires_whatsapp_extra
def test_incoming_direct_message_fires_direct_event_only(db, wa):
    """An incoming 1:1 message fires the direct-message event with its text as untrusted context."""
    _deliver(wa.connector, _live_event(text="Are you free?"))
    db.expire_all()
    runs = _runs(db, TriggerEvent.WHATSAPP_DIRECT_MESSAGE_RECEIVED)
    assert len(runs) == 1
    assert runs[0].payload["event"]["body"] == "Are you free?"
    assert "Are you free?" in runs[0].payload["objective"]
    assert _runs(db, TriggerEvent.WHATSAPP_GROUP_MESSAGE_RECEIVED) == []


@requires_whatsapp_extra
def test_own_messages_history_sync_and_redelivery_never_fire(db, wa):
    """Own messages, history sync and re-deliveries never fire, so a reaction cannot trigger itself."""
    _deliver(wa.connector, _live_event(message_id="OUT", is_from_me=True))
    _deliver(wa.connector, _live_event(message_id="OLD"), live=False)
    _deliver(wa.connector, _live_event(message_id="NEW"))
    _deliver(wa.connector, _live_event(message_id="NEW"))  # neonize re-delivers
    _deliver(wa.connector, _live_event(message_id="OLD"))  # a history message arriving live again
    db.expire_all()
    runs = _runs(db, TriggerEvent.WHATSAPP_DIRECT_MESSAGE_RECEIVED)
    assert [run.payload["event"]["id"] for run in runs] == [f"{wa.account.id}:NEW"]
    assert db.query(models.WhatsAppMessage).count() == 3


@requires_whatsapp_extra
def test_group_message_fires_group_event_and_mention_only_when_you_are_referenced(db, wa):
    """Every group message fires the group event; only a mention or reply to you also fires the mention event."""
    _deliver(wa.connector, _live_event(message_id="G1", is_group=True))
    _deliver(wa.connector, _live_event(message_id="G2", is_group=True, mentions=["49555@s.whatsapp.net"]))
    _deliver(wa.connector, _live_event(message_id="G3", is_group=True, quoted="777lid@lid"))
    _deliver(wa.connector, _live_event(message_id="G4", is_group=True, mentions=["49000@s.whatsapp.net"]))
    db.expire_all()
    group_ids = {
        run.payload["event"]["id"].split(":")[1] for run in _runs(db, TriggerEvent.WHATSAPP_GROUP_MESSAGE_RECEIVED)
    }
    mention_ids = {run.payload["event"]["id"].split(":")[1] for run in _runs(db, TriggerEvent.WHATSAPP_GROUP_MENTION)}
    assert group_ids == {"G1", "G2", "G3", "G4"}
    assert mention_ids == {"G2", "G3"}
    assert _runs(db, TriggerEvent.WHATSAPP_DIRECT_MESSAGE_RECEIVED) == []


@requires_whatsapp_extra
def test_mentions_stay_off_without_a_known_own_identity(db, wa):
    """Without the linked identity no mention event fires, but the group event still does."""
    wa.connector.own_users = frozenset()
    _deliver(wa.connector, _live_event(message_id="G1", is_group=True, mentions=["49555@s.whatsapp.net"]))
    db.expire_all()
    assert _runs(db, TriggerEvent.WHATSAPP_GROUP_MENTION) == []
    assert len(_runs(db, TriggerEvent.WHATSAPP_GROUP_MESSAGE_RECEIVED)) == 1


@requires_whatsapp_extra
def test_referenced_users_strips_device_and_server():
    """Mentioned and quoted JIDs reduce to their user part."""
    event = _live_event(mentions=["49555:3@s.whatsapp.net", "12@lid"], quoted="49999@s.whatsapp.net")
    assert referenced_users(event.Message) == ("12", "49555", "49999")
    assert referenced_users(_live_event().Message) == ()


def test_on_connected_reads_own_identity(db, wa):
    """Connecting remembers the linked account's own user ids."""
    me = SimpleNamespace(JID=SimpleNamespace(User="49555"), LID=SimpleNamespace(User=""))
    wa.connector.own_users = frozenset()
    wa.connector._on_connected(SimpleNamespace(get_me=lambda: me), None)
    assert wa.connector.own_users == frozenset({"49555"})


# --- Planner ------------------------------------------------------------------------------------


class _FakeConnector:
    def __init__(self):
        self.tasks: list[dict] = []
        self.events: list[dict] = []

    def fetch_tasks(self):
        return [dict(task) for task in self.tasks]

    def fetch_events(self, start, end):
        del start, end
        return [dict(event) for event in self.events]


@pytest.fixture
def planner(db, monkeypatch):
    """A planner account on a fake connector with a definition subscribed to every planner event."""
    user = _user(db)
    account = models.PlannerAccount(name="Work", provider=models.Provider.JIRA, user_id=user.id, token="x")
    db.add(account)
    db.commit()
    fake = _FakeConnector()
    monkeypatch.setattr(planner_service.PlannerService, "get_connector", lambda self, account: fake)
    for event in TriggerEvent:
        if event.value.startswith("planner."):
            _subscribe(db, user.id, event)
    return SimpleNamespace(fake=fake, account=account, sync=lambda: planner_service.PlannerService(db).sync_all())


def _task(key, title="Write notes", status=models.TaskStatus.TODO):
    return {"external_id": key, "title": title, "status": status, "due_date": None}


def _calendar_event(key, start=datetime(2026, 11, 10, 9, 0, tzinfo=UTC), location=None):
    return {
        "external_id": key,
        "summary": "Standup",
        "start_time": start,
        "end_time": start + timedelta(minutes=15),
        "location": location,
    }


def test_first_sync_is_a_baseline(db, planner):
    """An account's first sync emits nothing."""
    planner.fake.tasks = [_task("J-1")]
    planner.fake.events = [_calendar_event("E-1")]
    planner.sync()
    assert db.query(models.RuntimeTrigger).count() == 0
    assert db.query(models.TaskRecord).count() == 1


def test_later_syncs_fire_created_and_changed_once(db, planner):
    """Later syncs fire created/changed once per distinct change."""
    planner.fake.tasks = [_task("J-1")]
    planner.fake.events = [_calendar_event("E-1")]
    planner.sync()

    planner.fake.tasks = [_task("J-1", status=models.TaskStatus.DONE), _task("J-2", title="Review")]
    planner.fake.events = [_calendar_event("E-1", location="Room 4"), _calendar_event("E-2")]
    planner.sync()
    planner.sync()  # nothing new: nothing fires again
    db.expire_all()

    created = _runs(db, TriggerEvent.PLANNER_TASK_CREATED)
    assert [run.payload["event"]["title"] for run in created] == ["Review"]
    changed = _runs(db, TriggerEvent.PLANNER_TASK_CHANGED)
    assert len(changed) == 1
    assert changed[0].payload["event"]["changes"] == "status: Todo -> Done"
    assert len(_runs(db, TriggerEvent.PLANNER_CALENDAR_EVENT_CREATED)) == 1
    event_changes = _runs(db, TriggerEvent.PLANNER_CALENDAR_EVENT_CHANGED)
    assert [run.payload["event"]["changes"] for run in event_changes] == ["location:  -> Room 4"]

    # A later change fires again, and so does a transition that repeats an earlier one.
    planner.fake.tasks[0] = _task("J-1", status=models.TaskStatus.TODO)
    planner.sync()
    planner.fake.tasks[0] = _task("J-1", status=models.TaskStatus.DONE)
    planner.sync()
    db.expire_all()
    changes = [run.payload["event"]["changes"] for run in _runs(db, TriggerEvent.PLANNER_TASK_CHANGED)]
    assert changes == ["status: Todo -> Done", "status: Done -> Todo", "status: Todo -> Done"]


def test_failed_sync_publishes_nothing(db, planner, monkeypatch):
    """A sync that fails publishes none of what it saw."""
    planner.fake.tasks = [_task("J-1")]
    planner.sync()
    planner.fake.tasks = [_task("J-1"), _task("J-2")]
    monkeypatch.setattr(planner.fake, "fetch_events", lambda start, end: (_ for _ in ()).throw(RuntimeError("boom")))
    planner.sync()
    db.expire_all()
    assert db.query(models.RuntimeTrigger).count() == 0


@requires_whatsapp_extra
def test_a_publish_failure_never_loses_the_message(db, wa, monkeypatch):
    """The message stays stored when publishing its trigger events fails."""
    from janus import whatsapp_trigger_events

    def boom(*args, **kwargs):
        raise RuntimeError("queue down")

    monkeypatch.setattr(whatsapp_trigger_events, "publish", boom)
    _deliver(wa.connector, _live_event(message_id="KEEP"))
    db.expire_all()
    assert db.query(models.WhatsAppMessage).filter_by(provider_message_id="KEEP").count() == 1
    assert db.query(models.RuntimeTrigger).count() == 0


@requires_whatsapp_extra
def test_status_updates_broadcasts_and_channels_never_fire(db, wa):
    """Status stories (status@broadcast), broadcast lists and channels are not one-to-one chats."""
    _deliver(wa.connector, _live_event(message_id="S1", chat=("status", "broadcast")))
    _deliver(wa.connector, _live_event(message_id="N1", chat=("123", "newsletter")))
    db.expire_all()
    assert db.query(models.RuntimeTrigger).count() == 0


@requires_whatsapp_extra
def test_reactions_and_edits_never_fire(db, wa):
    """A reaction or an edit is a new message id but carries no content of its own."""
    from neonize.proto.waE2E.WAWebProtobufsE2E_pb2 import Message as WAMessage
    from neonize.proto.waE2E.WAWebProtobufsE2E_pb2 import ProtocolMessage, ReactionMessage

    _deliver(wa.connector, _live_event(message_id="R1", message=WAMessage(reactionMessage=ReactionMessage(text="👍"))))
    _deliver(wa.connector, _live_event(message_id="E1", message=WAMessage(protocolMessage=ProtocolMessage(type=0))))
    db.expire_all()
    assert db.query(models.RuntimeTrigger).count() == 0


@requires_whatsapp_extra
def test_a_participant_without_a_quoted_message_is_not_a_reply():
    """contextInfo.participant only counts with a quoted message id (stanzaID)."""
    from neonize.proto.waE2E.WAWebProtobufsE2E_pb2 import ContextInfo, ExtendedTextMessage
    from neonize.proto.waE2E.WAWebProtobufsE2E_pb2 import Message as WAMessage

    forwarded = WAMessage(
        extendedTextMessage=ExtendedTextMessage(text="fwd", contextInfo=ContextInfo(participant="49555@s.whatsapp.net"))
    )
    assert referenced_users(forwarded) == ()


@requires_whatsapp_extra
def test_messages_before_connected_still_know_the_own_identity(db, wa):
    """A message handled before ConnectedEv loads the identity itself, so its mention is not missed."""
    me = SimpleNamespace(JID=SimpleNamespace(User="49555"), LID=SimpleNamespace(User=""))
    wa.connector.own_users = frozenset()
    event = _live_event(message_id="EARLY", is_group=True, mentions=["49555@s.whatsapp.net"])
    wa.connector._on_message(SimpleNamespace(get_me=lambda: me), event)
    db.expire_all()
    assert len(_runs(db, TriggerEvent.WHATSAPP_GROUP_MENTION)) == 1


@requires_whatsapp_extra
def test_one_failing_event_does_not_stop_the_others(db, wa, monkeypatch):
    """When the group event fails to publish, the mention event for the same message still does."""
    original = definitions.emit_event

    def flaky(db_, owner_id, event, event_id, data, **kwargs):
        if event == TriggerEvent.WHATSAPP_GROUP_MESSAGE_RECEIVED:
            raise RuntimeError("locked")
        return original(db_, owner_id, event, event_id, data, **kwargs)

    monkeypatch.setattr(definitions, "emit_event", flaky)
    _deliver(wa.connector, _live_event(message_id="G9", is_group=True, mentions=["49555@s.whatsapp.net"]))
    db.expire_all()
    assert len(_runs(db, TriggerEvent.WHATSAPP_GROUP_MENTION)) == 1


def test_calendar_times_with_an_offset_are_not_changes(db, planner):
    """An event at 10:00+01:00 stays unchanged across syncs (SQLite stores its wall time)."""
    from datetime import timezone

    berlin = timezone(timedelta(hours=1))
    planner.fake.events = [_calendar_event("E-1", start=datetime(2026, 11, 10, 10, 0, tzinfo=berlin))]
    planner.sync()
    planner.sync()
    planner.sync()
    db.expire_all()
    assert _runs(db, TriggerEvent.PLANNER_CALENDAR_EVENT_CHANGED) == []


def test_janus_proposal_tool_lists_every_catalogued_event():
    """The propose tool's description is built from the event catalog, so new events reach Janus."""
    from janus.agent_toolboxes.automation_proposals import PROPOSE_DESCRIPTION

    assert all(event.value in PROPOSE_DESCRIPTION for event in TriggerEvent)
