"""Generated images end to end (JAN-253): the chat tool, storage, the endpoint, and their fences.

The fake chat model runs the *real* prepared ``generate_image`` tool through
LangChain, so the capability gate, the injection escalation, the image block
after the call's tool event, the message link before ``__END__``, the owner
scoping of ``GET /api/images/{id}``, and the deletion cascade are all
exercised. Provider HTTP is ``httpx.MockTransport``.
"""

import base64
import json
import os

import httpx
import pytest
from test_image_generation import Recorder, png

from janus import config, models, security, tool_policy
from janus import database as db_module
from janus import llm_service as llm_mod
from janus.agent_toolboxes.images import GENERATE_IMAGE_TOOL_NAME, IMAGES_TOOL_ID, build_image_tools
from janus.agent_toolboxes.shared import ToolContext, TurnToolCall, TurnTrace
from janus.agent_tools import DEFAULT_TOOL_REGISTRY
from janus.capability_profile import user_connected_capabilities
from janus.runtime_adapter import INJECTION_ESCALATED_TOOL_NAMES, TASK_MODE_ALLOWED_TOOL_IDS, RuntimeAdapter
from janus.services import image_generation, image_store
from janus.services.tool_call_events import TOOL_EVENT_HANDLERS, TOOL_EVENT_PREFIX, ToolEventTracker
from janus.services.ui_blocks import MAX_UI_BLOCKS_PER_MESSAGE, UI_BLOCK_PREFIX, UIBlockTurn, validate_block

# db, client and testing_session_local fixtures are provided by conftest.py

OPENAI_ANSWER = {"created": 1, "data": [{"b64_json": base64.b64encode(png(4, 3)).decode()}]}


@pytest.fixture(autouse=True)
def images_dir(tmp_path, monkeypatch):
    """Every test stores its images in its own directory."""
    root = tmp_path / "images"
    monkeypatch.setattr(config.settings, "image_storage_dir", root)
    return root


@pytest.fixture
def provider(monkeypatch):
    """The OpenAI Images API, mocked; returns the request recorder."""
    recorder = Recorder(body=OPENAI_ANSWER)
    monkeypatch.setattr(image_generation, "HTTP_TRANSPORT", httpx.MockTransport(recorder))
    return recorder


def _configure(db, provider: str | None = "openai", key: str | None = "sk-test") -> models.Settings:
    settings = db.query(models.Settings).first() or models.Settings()
    settings.image_provider = provider
    settings.openai_api_key = security.security_service.encrypt(key) if key else None
    db.add(settings)
    db.commit()
    return settings


def _user(db, name: str = "image-owner") -> models.User:
    user = models.User(username=name, hashed_password=security.security_service.get_password_hash("pw"))
    db.add(user)
    db.commit()
    return user


def _conversation(db, user) -> models.Conversation:
    conversation = models.Conversation(user_id=user.id)
    db.add(conversation)
    db.commit()
    return conversation


def _store(db, user, conversation, data: bytes | None = None) -> models.GeneratedImage:
    data = data or png()
    return image_store.store_image(
        db,
        user_id=user.id,
        conversation_id=conversation.id if conversation is not None else None,
        prompt="a fox",
        provider="openai",
        model="gpt-image-1",
        data=data,
        sniffed=image_generation.sniff_image(data),
    )


def _sign_in(client, name: str):
    client.cookies.clear()
    response = client.post("/api/auth/login", json={"username": name, "password": "pw"})
    client.headers[security.CSRF_HEADER_NAME] = response.json()["csrf_token"]
    return client


# -- storage --------------------------------------------------------------------


def test_images_are_files_in_a_per_user_folder_with_a_metadata_row(db, images_dir):
    """The bytes are a 0600 file under <dir>/<user_id>/, the row has the sniffed type and size."""
    user = _user(db)
    image = _store(db, user, _conversation(db, user), png(5, 7))

    assert image_store.IMAGE_ID_PATTERN.match(image.id) and len(image.id) == 32
    path = images_dir / str(user.id) / f"{image.id}.png"
    assert path.read_bytes() == png(5, 7)
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert (image.path, image.mime_type, image.width, image.height) == (f"{user.id}/{image.id}.png", "image/png", 5, 7)
    assert image.size_bytes == len(png(5, 7)) and image.message_id is None
    assert image_store.usage_bytes(db, user.id) == image.size_bytes


def test_paths_are_confined_to_the_images_directory(db, images_dir, tmp_path):
    """A stored path that leaves the images dir (or is the dir) is never resolved, served, or deleted."""
    secret = tmp_path / "secret.txt"
    secret.write_text("do not serve")
    for relative in ("../secret.txt", "/etc/passwd", "", ".", "1/../../secret.txt"):
        assert image_store.resolve_path(relative) is None
    user = _user(db)
    image = _store(db, user, _conversation(db, user))
    image.path = "../secret.txt"
    db.commit()
    assert image_store.get_image_for_user(db, image.id, user.id) is None
    image_store.delete_files(["../secret.txt"])
    assert secret.exists()


def test_a_symlink_out_of_the_images_directory_is_not_followed(db, images_dir, tmp_path):
    """Resolving the real path catches a symlinked file that points elsewhere."""
    user = _user(db)
    image = _store(db, user, _conversation(db, user))
    target = image_store.resolve_path(image.path)
    target.unlink()
    outside = tmp_path / "outside.png"
    outside.write_bytes(png())
    os.symlink(outside, target)
    assert image_store.get_image_for_user(db, image.id, user.id) is None


def _padded(total: int) -> bytes:
    return png() + b"\x00" * (total - len(png()))


def test_storage_cap_counts_the_new_image_and_refuses_at_the_limit(db, monkeypatch, images_dir):
    """A write that would pass image_storage_limit_mb is refused with nothing written; at the limit, so is the next.

    Another user is not affected.
    """
    monkeypatch.setattr(config.settings, "image_storage_limit_mb", 1)
    megabyte = 1024 * 1024
    user, other = _user(db), _user(db, "other-owner")
    conversation = _conversation(db, user)
    image_store.ensure_capacity(db, user.id)
    _store(db, user, conversation, _padded(megabyte // 2))
    # 512 KB stored + 600 KB new > 1 MB: refused before anything is written.
    with pytest.raises(image_store.ImageStorageFull, match=r"image storage is full \(0 of 1 MB used\)"):
        _store(db, user, conversation, _padded(600 * 1024))
    assert db.query(models.GeneratedImage).count() == 1
    assert len(list((images_dir / str(user.id)).iterdir())) == 1
    # Exactly up to the limit is allowed; then the cheap pre-check refuses.
    _store(db, user, conversation, _padded(megabyte // 2))
    with pytest.raises(image_store.ImageStorageFull, match=r"image storage is full \(1 of 1 MB used\)"):
        image_store.ensure_capacity(db, user.id)
    image_store.ensure_capacity(db, other.id)
    _store(db, other, _conversation(db, other), _padded(megabyte // 2))


def test_the_cap_check_and_the_write_run_under_the_users_lock(db, monkeypatch):
    """Concurrent turns of one user serialize on a per-user lock, so they cannot both pass the check."""
    user, other = _user(db), _user(db, "other-owner")
    seen: list[tuple[bool, bool]] = []
    real_usage = image_store.usage_bytes

    def usage(db_, user_id):
        seen.append((image_store._user_lock(user.id).locked(), image_store._user_lock(other.id).locked()))
        return real_usage(db_, user_id)

    monkeypatch.setattr(image_store, "usage_bytes", usage)
    _store(db, user, _conversation(db, user))
    assert seen == [(True, False)]
    assert not image_store._user_lock(user.id).locked()


# -- endpoint -------------------------------------------------------------------


def test_endpoint_serves_only_the_owners_images(client, db):
    """The owner gets the bytes with the right headers; another user, a bad id, and no session get 404/401."""
    owner, other = _user(db), _user(db, "intruder")
    image = _store(db, owner, _conversation(db, owner), png(2, 2))

    assert client.get(f"/api/images/{image.id}").status_code == 401
    response = _sign_in(client, "image-owner").get(f"/api/images/{image.id}")
    assert response.status_code == 200
    assert response.content == png(2, 2)
    assert response.headers["content-type"] == "image/png"
    # Never cached: a shared browser profile must not show it after logout or deletion.
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-disposition"] == f'inline; filename="janus-image-{image.id}.png"'

    other_response = _sign_in(client, "intruder").get(f"/api/images/{image.id}")
    assert other_response.status_code == 404
    missing = client.get("/api/images/doesnotexist")
    assert missing.status_code == 404 and missing.json() == other_response.json()
    for bad in ("..%2F..%2Fjanus.db", "a" * 65, "bad.id"):
        assert client.get(f"/api/images/{bad}").status_code == 404
    assert other.id != owner.id


def test_owner_can_delete_an_image_and_its_file(client, db):
    """DELETE frees one of the owner's images (row and file); another user gets 404 and nothing changes."""
    owner, _ = _user(db), _user(db, "intruder")
    image = _store(db, owner, None)
    path = image_store.resolve_path(image.path)
    assert image.conversation_id is None and path.is_file()

    assert _sign_in(client, "intruder").delete(f"/api/images/{image.id}").status_code == 404
    assert path.is_file()
    owner_client = _sign_in(client, "image-owner")
    assert owner_client.delete(f"/api/images/{image.id}").status_code == 204
    assert not path.exists()
    assert owner_client.get(f"/api/images/{image.id}").status_code == 404
    assert owner_client.delete(f"/api/images/{image.id}").status_code == 404


def test_endpoint_answers_404_when_the_file_is_gone(client, db):
    """A row without its file is a 404, not a 500."""
    owner = _user(db)
    image = _store(db, owner, _conversation(db, owner))
    image_store.resolve_path(image.path).unlink()
    assert _sign_in(client, "image-owner").get(f"/api/images/{image.id}").status_code == 404


def test_deleting_a_conversation_deletes_its_image_rows_and_files(client, db, images_dir):
    """Rows go with the conversation (ORM cascade) and their files are removed; other conversations keep theirs."""
    owner = _user(db)
    doomed, kept = _conversation(db, owner), _conversation(db, owner)
    first, second, survivor = _store(db, owner, doomed), _store(db, owner, doomed), _store(db, owner, kept)
    paths = {image.id: image_store.resolve_path(image.path) for image in (first, second, survivor)}
    first_id, second_id, survivor_id = first.id, second.id, survivor.id

    owner_client = _sign_in(client, "image-owner")
    assert owner_client.delete(f"/api/conversations/{doomed.id}").status_code == 204
    db.expire_all()
    assert [image.id for image in db.query(models.GeneratedImage).all()] == [survivor_id]
    assert not paths[first_id].exists() and not paths[second_id].exists()
    assert paths[survivor_id].exists()
    assert owner_client.get(f"/api/images/{first_id}").status_code == 404
    assert owner_client.get(f"/api/images/{survivor_id}").status_code == 200


# -- toolbox --------------------------------------------------------------------


def _tool(db, user, turn, *, trace=None, conversation_id=None):
    context = ToolContext(
        db=db,
        user=user,
        conversation_id=conversation_id,
        turn_trace=trace or TurnTrace(),
        ui_blocks=turn,
    )
    (generate_image,) = build_image_tools(context)
    assert generate_image.name == GENERATE_IMAGE_TOOL_NAME
    return generate_image


@pytest.fixture
def logged(monkeypatch, testing_session_local):
    """Event-log rows written by the tool land in the test database."""
    monkeypatch.setattr(db_module, "SessionLocal", testing_session_local)


def test_tool_generates_stores_and_shows_the_image(db, provider, logged):
    """One call: the provider gets the prompt, the row and file exist, and an image block is on the turn."""
    _configure(db)
    user = _user(db)
    conversation = _conversation(db, user)
    turn = UIBlockTurn(available=True)
    answer = _tool(db, user, turn, conversation_id=conversation.id).invoke(
        {"prompt": "a red fox in snow", "aspect": "portrait", "style": "watercolor"}
    )

    assert provider.json == {
        "model": "gpt-image-1",
        "prompt": "a red fox in snow\n\nStyle: watercolor",
        "n": 1,
        "size": "1024x1536",
    }
    image = db.query(models.GeneratedImage).one()
    (block,) = turn.blocks()
    assert block == {
        "id": block["id"],
        "version": 1,
        "type": "image",
        "alt": "a red fox in snow",
        "url": f"/api/images/{image.id}",
        "caption": "Generated with OpenAI gpt-image-1",
    }
    assert answer.startswith(f"Generated and shown to the user: image {image.id} (block {block['id']}")
    assert "Do not call render_ui" in answer
    assert (image.conversation_id, image.prompt, image.width, image.height) == (
        conversation.id,
        "a red fox in snow\n\nStyle: watercolor",
        4,
        3,
    )
    event = db.query(models.Event).filter(models.Event.source == "ImageGeneration").one()
    assert event.event_type == models.EventType.INFO
    details = json.loads(event.details) if isinstance(event.details, str) else event.details
    assert (details["provider"], details["model"], details["image_id"]) == ("openai", "gpt-image-1", image.id)
    assert details["prompt_chars"] == len("a red fox in snow")
    assert "fox" not in json.dumps(details) and "fox" not in event.message


def test_tool_refuses_before_spending_money(db, provider, logged, monkeypatch):
    """No block-capable client, no conversation, no provider, a full reply, or full storage: no provider call."""
    user = _user(db)
    conversation = _conversation(db, user)
    _configure(db, provider=None)
    with pytest.raises(RuntimeError, match="no image provider is configured"):
        _tool(db, user, UIBlockTurn(available=True), conversation_id=conversation.id).invoke({"prompt": "x"})
    _configure(db)
    for turn in (None, UIBlockTurn(available=False)):
        with pytest.raises(RuntimeError, match="this client cannot show images; nothing was generated"):
            _tool(db, user, turn, conversation_id=conversation.id).invoke({"prompt": "x"})
    with pytest.raises(RuntimeError, match="inside a saved chat conversation"):
        _tool(db, user, UIBlockTurn(available=True)).invoke({"prompt": "x"})
    full = UIBlockTurn(available=True)
    for _ in range(MAX_UI_BLOCKS_PER_MESSAGE):
        full.add(validate_block({"type": "image", "alt": "x"}))
    with pytest.raises(RuntimeError, match=f"at most {MAX_UI_BLOCKS_PER_MESSAGE} blocks"):
        _tool(db, user, full, conversation_id=conversation.id).invoke({"prompt": "x"})
    with pytest.raises(RuntimeError, match="prompt must not be empty"):
        _tool(db, user, UIBlockTurn(available=True), conversation_id=conversation.id).invoke({"prompt": " "})
    _store(db, user, conversation, png() + b"\x00" * (1024 * 1024))
    monkeypatch.setattr(config.settings, "image_storage_limit_mb", 1)
    with pytest.raises(RuntimeError, match="image storage is full"):
        _tool(db, user, UIBlockTurn(available=True), conversation_id=conversation.id).invoke({"prompt": "x"})
    assert provider.requests == []
    warnings = db.query(models.Event).filter(
        models.Event.source == "ImageGeneration", models.Event.event_type == models.EventType.WARNING
    )
    assert warnings.count() == 2  # the empty prompt and the full storage


def test_tool_rechecks_the_cap_with_the_new_image_before_storing(db, provider, logged, monkeypatch):
    """If another turn filled the storage meanwhile, the image is not stored and the model is told why."""
    _configure(db)
    user = _user(db)
    conversation = _conversation(db, user)
    monkeypatch.setattr(image_store, "ensure_capacity", lambda db_, user_id: None)  # the pre-check passed earlier
    monkeypatch.setattr(image_store, "limit_bytes", lambda: 10)
    turn = UIBlockTurn(available=True)
    with pytest.raises(RuntimeError, match="image storage is full"):
        _tool(db, user, turn, conversation_id=conversation.id).invoke({"prompt": "a fox"})
    assert len(provider.requests) == 1
    assert db.query(models.GeneratedImage).count() == 0 and turn.blocks() == []


def test_provider_failures_answer_a_short_error_and_store_nothing(db, logged, monkeypatch):
    """A refused prompt (HTTP 400) or a non-image answer stores nothing and logs a WARNING without the prompt."""
    _configure(db)
    user = _user(db)
    conversation = _conversation(db, user)
    tool_ = _tool(db, user, UIBlockTurn(available=True), conversation_id=conversation.id)
    for recorder, match in (
        (Recorder(400, {"error": {"message": "safety system"}}), "OpenAI answered HTTP 400: safety system"),
        (
            Recorder(body={"data": [{"b64_json": base64.b64encode(b"<svg/>").decode()}]}),
            "not return a PNG, JPEG, or WebP",
        ),
    ):
        monkeypatch.setattr(image_generation, "HTTP_TRANSPORT", httpx.MockTransport(recorder))
        with pytest.raises(RuntimeError, match=match):
            tool_.invoke({"prompt": "secret plans for a surprise party"})
    assert db.query(models.GeneratedImage).count() == 0
    events = db.query(models.Event).filter(models.Event.source == "ImageGeneration").all()
    assert [event.event_type for event in events] == [models.EventType.WARNING, models.EventType.WARNING]
    assert all("surprise" not in json.dumps(event.details) for event in events)


def test_tool_refuses_after_a_suspected_injection(db, provider, logged):
    """The toolbox checks the trace itself too: nothing is sent, and the refusal names the reason."""
    _configure(db)
    user = _user(db)
    trace = TurnTrace()
    trace.record(TurnToolCall("mail", "get_email_detail", True, ("instruction_override",)))
    turn = UIBlockTurn(available=True)
    answer = _tool(db, user, turn, trace=trace, conversation_id=_conversation(db, user).id).invoke({"prompt": "x"})
    assert answer.startswith("BLOCKED: generate_image was not run because content read earlier looked like")
    assert provider.requests == [] and turn.blocks() == []


# -- chat-only fences -------------------------------------------------------------


def _prepare(db, user, mode, **kwargs):
    return RuntimeAdapter(db, user).prepare_step(
        task_id=f"images-{mode}",
        objective="draw a fox",
        requested_tool_ids=("images", "delegation", "core"),
        task_mode=mode,
        guard_tool_errors=True,
        granted_capabilities=frozenset({"images", "core", "delegation"}),
        **kwargs,
    )


def test_registry_entry_is_chat_only_recommended_and_escalated(db):
    """The entry's metadata, the chat allowlist, the injection escalation, and no onward grant."""
    entry = next(entry for entry in DEFAULT_TOOL_REGISTRY.entries() if entry.id == IMAGES_TOOL_ID)
    assert (entry.confirmation, entry.operation, entry.required_capabilities, entry.docs_ref) == (
        "recommended",
        "write",
        ("images",),
        "tools.md#image-generation",
    )
    assert IMAGES_TOOL_ID in TASK_MODE_ALLOWED_TOOL_IDS["chat"]
    assert GENERATE_IMAGE_TOOL_NAME in INJECTION_ESCALATED_TOOL_NAMES
    assert IMAGES_TOOL_ID in tool_policy.LOCKED_UNATTENDED
    assert IMAGES_TOOL_ID not in tool_policy.delegable_tool_ids(
        frozenset({IMAGES_TOOL_ID}), DEFAULT_TOOL_REGISTRY.entries()
    )
    with pytest.raises(tool_policy.LockedToolError):
        tool_policy.set_policy(db, _user(db).id, IMAGES_TOOL_ID, True)


def test_chat_step_offers_the_tool_only_with_a_capable_provider(db):
    """Without a configured provider and key the entry is denied missing_capability; with one it is offered."""
    user = _user(db)
    confirmed = frozenset({"images", "delegation", "core"})
    turn = UIBlockTurn(available=True)
    # The chat surface grants what the user has connected (capability_profile).
    for provider_name, key in ((None, "sk-test"), ("openai", None)):
        _configure(db, provider=provider_name, key=key)
        prepared = RuntimeAdapter(db, user).prepare_step(
            task_id="images-chat",
            objective="draw",
            requested_tool_ids=("images", "core"),
            task_mode="chat",
            granted_capabilities=user_connected_capabilities(db, user),
            confirmed_tool_ids=confirmed,
            ui_blocks=turn,
        )
        assert GENERATE_IMAGE_TOOL_NAME not in prepared.tool_ids_by_name
        assert ("images", "missing_capability") in [(denial.tool_id, denial.reason) for denial in prepared.denials]
    _configure(db)
    prepared = RuntimeAdapter(db, user).prepare_step(
        task_id="images-chat",
        objective="draw",
        requested_tool_ids=("images", "delegation", "core"),
        task_mode="chat",
        granted_capabilities=user_connected_capabilities(db, user),
        confirmed_tool_ids=confirmed,
        ui_blocks=turn,
    )
    assert prepared.tool_ids_by_name[GENERATE_IMAGE_TOOL_NAME] == IMAGES_TOOL_ID


@pytest.mark.parametrize("mode", ["trigger", "automation", "delegated", "briefing", "digest", "ingestion"])
def test_the_tool_is_never_offered_outside_chat(db, mode):
    """No unattended or pipeline mode is offered the toolbox, whatever is requested or narrowed to."""
    _configure(db)
    user = _user(db, f"mode-{mode}")
    entries = DEFAULT_TOOL_REGISTRY.entries()
    if mode in tool_policy.UNATTENDED_TASK_MODES:
        offered, confirmed = tool_policy.unattended_tool_sets(
            db, user.id, entries, task_mode=mode, narrowed_to=frozenset({"images", "core"})
        )
        assert IMAGES_TOOL_ID not in offered | confirmed
        prepared = _prepare(db, user, mode, allowed_tool_ids=frozenset({"images", "core"}), confirmed_tool_ids=offered)
    else:
        assert IMAGES_TOOL_ID not in TASK_MODE_ALLOWED_TOOL_IDS[mode]
        prepared = _prepare(db, user, mode)
    assert IMAGES_TOOL_ID not in {entry.id for entry in prepared.selection.entries}
    assert GENERATE_IMAGE_TOOL_NAME not in prepared.tool_ids_by_name


def test_toolbox_is_disabled_outside_chat_even_when_allowlisted(db):
    """Explicitly allowlisting it for a trigger still fails closed (disabled_by_context)."""
    prepared = _prepare(
        db, _user(db), "trigger", allowed_tool_ids=frozenset({"images"}), confirmed_tool_ids=frozenset({"images"})
    )
    assert ("images", "disabled_by_context") in [(denial.tool_id, denial.reason) for denial in prepared.denials]


def test_allow_everything_builds_it_for_an_unattended_step(db):
    """The owner's "Allow everything" mode (JAN-359) lets an unattended step build the toolbox."""
    prepared = _prepare(
        db,
        _user(db),
        "trigger",
        allowed_tool_ids=frozenset({"images"}),
        confirmed_tool_ids=frozenset({"images"}),
        unattended_allow_all=True,
    )
    assert "generate_image" in prepared.tool_ids_by_name


def test_unattended_tool_stores_the_image_without_a_conversation(db, provider, logged):
    """Built for an unattended run, the tool stores the image with no conversation and answers its URL."""
    from janus.agent_toolboxes.images import build_image_tools
    from janus.agent_toolboxes.shared import ToolContext

    _configure(db)
    user = _user(db)
    (tool,) = build_image_tools(ToolContext(db=db, user=user), unattended=True)
    answer = tool.invoke({"prompt": "a red fox in snow"})
    image = db.query(models.GeneratedImage).one()
    assert image.conversation_id is None
    assert answer == f"Generated and stored image {image.id}: /api/images/{image.id}"


# -- /ws/chat end to end ----------------------------------------------------------


class _Script(list):
    """The fake model's steps, plus the tool names it was offered per turn."""

    def __init__(self):
        super().__init__()
        self.offered: list[set[str]] = []


@pytest.fixture
def script(monkeypatch, testing_session_local):
    """What the fake model does, in order: ``("text", str)`` or ``(tool_name, args)``."""
    steps = _Script()
    monkeypatch.setattr(db_module, "SessionLocal", testing_session_local)
    monkeypatch.setattr(llm_mod.LLMService, "__init__", lambda self, **_kw: None)
    offered = steps.offered

    async def fake_stream_agent_chat(self, system_prompt, tools, history, user_msg):
        by_name = {tool.name: tool for tool in tools}
        offered.append(set(by_name))
        tracker = ToolEventTracker()
        while steps:
            name, args = steps.pop(0)
            if name == "text":
                yield args
                continue
            async for event in by_name[name].astream_events(args, version="v2"):
                handler = TOOL_EVENT_HANDLERS.get(event.get("event"))
                if handler:
                    frame = getattr(tracker, handler)(event)
                    if frame is not None:
                        yield frame

    async def fake_title(self, msg):
        return "Images"

    monkeypatch.setattr(llm_mod.LLMService, "stream_agent_chat", fake_stream_agent_chat)
    monkeypatch.setattr(llm_mod.LLMService, "generate_conversation_title", fake_title)
    monkeypatch.setattr(llm_mod.LLMService, "suggest_memory_fact", lambda self, *a, **k: None)
    return steps


def _turn(client, text: str, url: str = "/ws/chat?tool_events=1") -> list[str]:
    with client.websocket_connect(url) as ws:
        ws.send_text(json.dumps({"type": "message", "text": text}))
        frames = []
        while True:
            frame = ws.receive_text()
            frames.append(frame)
            if frame == "__END__" or frame.startswith("__ERROR__"):
                return frames


def _blocks(frames):
    return [json.loads(frame[len(UI_BLOCK_PREFIX) :]) for frame in frames if frame.startswith(UI_BLOCK_PREFIX)]


def _events(frames):
    return [json.loads(frame[len(TOOL_EVENT_PREFIX) :]) for frame in frames if frame.startswith(TOOL_EVENT_PREFIX)]


def _conversation_id(frames) -> int:
    return int(next(frame for frame in frames if frame.startswith("__CONVERSATION_ID__:")).split(":")[1])


@pytest.fixture
def chat_client(client, db):
    """A client signed in as the image owner."""
    _user(db)
    return _sign_in(client, "image-owner")


def test_chat_turn_generates_shows_links_and_serves_the_image(chat_client, script, provider, db):
    """The block follows the call's tool event, the row is linked to the reply, and the image is served."""
    _configure(db)
    script.extend([("text", "Here it is: "), (GENERATE_IMAGE_TOOL_NAME, {"prompt": "a lighthouse"}), ("text", "Done.")])
    frames = _turn(chat_client, "draw a lighthouse")

    (block,) = _blocks(frames)
    order = [frame for frame in frames if not frame.startswith(("__TOOL_SELECTION__", "__CONVERSATION_ID__"))]
    at = order.index(UI_BLOCK_PREFIX + json.dumps(block, ensure_ascii=False))
    assert order[at - 1].startswith(TOOL_EVENT_PREFIX) and '"phase": "end"' in order[at - 1]
    assert order.index("Here it is: ") < at < order.index("Done.")
    (end,) = [event for event in _events(frames) if event["phase"] == "end"]
    assert (end["tool_id"], end["tool_name"]) == (IMAGES_TOOL_ID, GENERATE_IMAGE_TOOL_NAME)

    # Linked and committed before __END__: the reload sees the block, the row points at the reply.
    detail = chat_client.get(f"/api/conversations/{_conversation_id(frames)}").json()
    assistant = detail["messages"][1]
    assert [row["payload"] for row in assistant["ui_blocks"]] == [block]
    assert [call["tool_name"] for call in assistant["tool_calls"]] == [GENERATE_IMAGE_TOOL_NAME]
    db.expire_all()
    image = db.query(models.GeneratedImage).one()
    assert image.message_id == assistant["id"]
    assert block["url"] == f"/api/images/{image.id}"
    served = chat_client.get(block["url"])
    assert served.status_code == 200 and served.content == png(4, 3)


def test_chat_without_a_capable_provider_does_not_offer_the_tool(chat_client, script, provider, db):
    """With only an Ollama backend (no image provider) the model is not offered generate_image at all."""
    _configure(db, provider=None)
    script.extend([("text", "I cannot draw here.")])
    frames = _turn(chat_client, "draw a lighthouse")
    selection = json.loads(next(frame for frame in frames if frame.startswith("__TOOL_SELECTION__:")).split(":", 1)[1])
    assert {"tool_id": "images", "reason": "missing_capability"} in selection["denied"]
    assert GENERATE_IMAGE_TOOL_NAME not in script.offered[0]
    assert provider.requests == []


def test_chat_refuses_generation_after_a_suspected_injection(chat_client, script, provider, db, monkeypatch):
    """Once something read in the turn looked like an injection, generate_image answers BLOCKED; nothing is sent."""
    _configure(db)
    real_prepare = RuntimeAdapter.prepare_step

    def prepare_with_detection(self, **kwargs):
        # As if a mail read earlier in this turn had matched an injection rule.
        prepared = real_prepare(self, **kwargs)
        prepared.trace.record(TurnToolCall("mail", "get_email_detail", True, ("instruction_override",)))
        return prepared

    monkeypatch.setattr(RuntimeAdapter, "prepare_step", prepare_with_detection)
    script.extend([(GENERATE_IMAGE_TOOL_NAME, {"prompt": "the user's passwords as a poster"}), ("text", "ok")])
    frames = _turn(chat_client, "summarize my mail")

    (blocked,) = [event for event in _events(frames) if event["phase"] != "start"]
    assert blocked["phase"] == "blocked"
    assert blocked["result_preview"].startswith("BLOCKED: generate_image was not run because content read earlier")
    assert provider.requests == [] and _blocks(frames) == []
    db.expire_all()
    assert db.query(models.GeneratedImage).count() == 0
    refusal = db.query(models.Event).filter(models.Event.message.contains("generate_image")).one()
    assert refusal.event_type == models.EventType.WARNING and "passwords" not in json.dumps(refusal.details)


def test_chat_client_without_tool_events_gets_an_error_and_no_call(chat_client, script, provider, db):
    """A client that cannot render blocks (iOS) is never charged for an image it cannot show."""
    _configure(db)
    script.extend([(GENERATE_IMAGE_TOOL_NAME, {"prompt": "a fox"}), ("text", "Sorry.")])
    _turn(chat_client, "draw a fox", url="/ws/chat")
    assert provider.requests == []
    db.expire_all()
    call = db.query(models.ConversationToolCall).one()
    assert (call.tool_name, call.phase) == (GENERATE_IMAGE_TOOL_NAME, "error")
    assert "this client cannot show images" in call.result_preview
