"""Tests for knowledge sources, parsing, and chunks (JAN-242)."""

import io
import sys
from datetime import timedelta
from email.message import EmailMessage

import pytest
from typer.testing import CliRunner

from janus import cli, database, models, security, time_utils
from janus.imap_service import IMAPMonitor
from janus.processor import EmailProcessor
from janus.services import embedding_service, knowledge_chunking, knowledge_index, knowledge_parsing
from janus.services import knowledge_sources as ks

SourceType = models.KnowledgeSourceType
Status = models.KnowledgeSourceStatus


class _FakeEmbeddings:
    """Deterministic embedder; counts how many texts it embedded."""

    def __init__(self):
        """Starts with no calls."""
        self.embedded: list[str] = []

    def embed_documents(self, texts):
        """Embeds a batch; every text gets the same small vector."""
        self.embedded += texts
        return [[1.0, float(len(text) % 7), 0.5] for text in texts]

    def embed_query(self, text):
        """Embeds one query."""
        return [1.0, 0.0, 0.5]


def _user(db, username: str = "alice") -> models.User:
    user = models.User(username=username, hashed_password=security.security_service.get_password_hash("secret"))
    db.add(user)
    db.commit()
    return user


def _email(db, user, *, subject="Quarterly report", body="Hi team,\n\nThe numbers are in.", priority=None):
    account = db.query(models.EmailAccount).filter_by(user_id=user.id).first()
    if account is None:
        account = models.EmailAccount(
            name=f"acc{user.id}", email=f"{user.username}@test", password="x", user_id=user.id
        )
        db.add(account)
        db.flush()
    record = models.EmailRecord(
        account_id=account.id,
        message_id=f"<{subject}-{user.id}>",
        subject=subject,
        sender="boss@test",
        snippet=body[:100],
        body=body,
        priority=priority or models.Priority.IMPORTANT,
    )
    db.add(record)
    db.commit()
    return record


def _sources(db, source_type: SourceType | None = None) -> list[models.KnowledgeSource]:
    query = db.query(models.KnowledgeSource)
    if source_type is not None:
        query = query.filter_by(source_type=source_type.value)
    return query.order_by(models.KnowledgeSource.id).all()


def _chunks(db, source) -> list[models.KnowledgeChunk]:
    return db.query(models.KnowledgeChunk).filter_by(source_id=source.id).order_by(models.KnowledgeChunk.ordinal).all()


#: Tests that parse real PDF/Office files need the optional 'documents' extra (markitdown).
needs_documents = pytest.mark.skipif(
    not knowledge_parsing.markitdown_available(),
    reason="needs the optional 'documents' extra (uv sync --extra documents)",
)


def _xlsx_bytes() -> bytes:
    import openpyxl  # part of the optional 'documents' extra

    workbook = openpyxl.Workbook()
    workbook.active["A1"], workbook.active["B1"] = "Budget", 42
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


#: A truncated XLSX (ZIP) that every parser rejects.
_BROKEN_XLSX = b"PK\x03\x04" + b"\x00" * 500


def _pdf_bytes(text: str) -> bytes:
    """A minimal single-page PDF with correct xref offsets."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


# --- Chunking ------------------------------------------------------------------


def test_appending_messages_keeps_earlier_chunks_identical():
    """Packing is deterministic from the start, so only the tail changes when a chat grows."""
    messages = [f"alice: message {index} " + "lorem ipsum " * 20 for index in range(40)]
    before = knowledge_chunking.pack(messages)
    after = knowledge_chunking.pack(messages + ["bob: one more"])
    assert len(before) > 3
    assert after[: len(before) - 1] == before[:-1]


def test_headings_start_chunks_and_stay_with_their_text():
    """A heading opens a new chunk, is never left alone, and a new section has no overlap."""
    document = "# Intro\n\nShort.\n\n## Section\n\n" + "Sentence. " * 400
    chunks = knowledge_chunking.pack(knowledge_chunking.document_blocks(document))
    assert chunks[0].text == "# Intro\n\nShort."
    assert chunks[1].text.startswith("## Section\n\nSentence.")
    # The continuation of the long section repeats the end of its predecessor.
    assert chunks[2].text.startswith(knowledge_chunking._overlap_tail(chunks[1].text))
    limit = knowledge_chunking.TARGET_CHARS + knowledge_chunking.OVERLAP_CHARS + 2
    assert all(len(chunk.text) <= limit for chunk in chunks)


def test_email_quotes_become_their_own_block():
    """Quoted replies are kept together and apart from the new text."""
    blocks = knowledge_chunking.email_blocks("Thanks!\n\n> earlier line one\n> earlier line two\n\nBest")
    assert blocks == ["Thanks!", "> earlier line one\n> earlier line two", "Best"]


def test_tables_are_not_mixed_with_prose():
    """Markdown tables form their own block."""
    blocks = knowledge_chunking.document_blocks("Intro text\n| a | b |\n|---|---|\n| 1 | 2 |\nAfter")
    assert blocks == ["Intro text", "| a | b |\n|---|---|\n| 1 | 2 |", "After"]


def test_oversized_words_are_split_hard():
    """Text with no spaces still ends up in bounded chunks."""
    chunks = knowledge_chunking.pack(["x" * 4000])
    assert [len(chunk.text) for chunk in chunks][0] == knowledge_chunking.TARGET_CHARS


# --- Parsing -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("filename", "mime", "size", "accepted", "reason"),
    [
        pytest.param("report.PDF", "application/octet-stream", 10, True, None, marks=needs_documents),
        (None, "text/csv", 10, True, None),
        ("photo.jpg", "image/jpeg", 10, False, "unsupported file type (image/jpeg)"),
        ("big.pdf", "application/pdf", knowledge_parsing.MAX_DOCUMENT_BYTES + 1, False, "larger than 10 MB"),
        ("empty.txt", "text/plain", 0, False, "empty file"),
    ],
)
def test_check_document(filename, mime, size, accepted, reason):
    """Only documents up to 10 MB are accepted, by extension or MIME type."""
    check = knowledge_parsing.check_document(filename, mime, size)
    assert (check.accepted, check.reason) == (accepted, reason)


@needs_documents
def test_parse_document_handles_pdf_and_xlsx():
    """The markitdown parser turns real documents into text."""
    assert "Hello from a PDF" in knowledge_parsing.parse_document(_pdf_bytes("Hello from a PDF"), ".pdf")
    assert "| Budget | 42 |" in knowledge_parsing.parse_document(_xlsx_bytes(), ".xlsx")


@needs_documents
def test_parse_document_reports_broken_files():
    """A corrupt document raises DocumentParseError instead of crashing the worker."""
    with pytest.raises(knowledge_parsing.DocumentParseError):
        knowledge_parsing.parse_document(_BROKEN_XLSX, ".xlsx")


def test_parse_document_without_markitdown(monkeypatch):
    """Without the optional 'documents' extra, plain text still parses and other formats fail clearly."""
    monkeypatch.setitem(sys.modules, "markitdown", None)  # makes `from markitdown import ...` raise ImportError
    assert knowledge_parsing.check_document("notes.txt", "text/plain", 10).accepted
    check = knowledge_parsing.check_document("report.pdf", "application/pdf", 10)
    assert (check.accepted, check.reason) == (False, ".pdf files need the optional 'documents' extra")
    assert knowledge_parsing.parse_document(b"a,b\n1,2\n", ".csv") == "a,b\n1,2"
    with pytest.raises(knowledge_parsing.DocumentParseError, match="'documents' extra"):
        knowledge_parsing.parse_document(_pdf_bytes("Hello"), ".pdf")
    with pytest.raises(knowledge_parsing.DocumentParseError, match="no text"):
        knowledge_parsing.parse_document(b"  \n", ".txt")


# --- Staging and staged processing --------------------------------------------


@needs_documents
def test_attachments_are_staged_parsed_and_their_bytes_dropped(db):
    """Documents wait with their bytes, are parsed by the tick, and keep only text afterwards."""
    user = _user(db)
    email = _email(db, user)
    for part, (name, mime, data) in enumerate(
        [("budget.xlsx", "application/vnd.ms-excel", _xlsx_bytes()), ("photo.jpg", "image/jpeg", b"\xff\xd8")]
    ):
        ks.stage_document(
            db,
            user_id=user.id,
            source_type=SourceType.EMAIL_ATTACHMENT,
            owner_id=email.id,
            part=part,
            filename=name,
            mime_type=mime,
            data=data,
        )
    db.commit()
    xlsx, photo = _sources(db, SourceType.EMAIL_ATTACHMENT)
    assert (xlsx.status, photo.status) == (Status.PENDING.value, Status.SKIPPED.value)
    assert photo.staged_content is None and "image/jpeg" in photo.status_detail

    ks.run_tick(db)
    db.refresh(xlsx)
    assert xlsx.status == Status.READY.value
    assert xlsx.staged_content is None
    assert "Budget" in _chunks(db, xlsx)[0].text


def test_restaging_the_same_attachment_is_a_no_op(db):
    """An IMAP re-sync never duplicates attachment sources."""
    user = _user(db)
    email = _email(db, user)
    kwargs = dict(
        user_id=user.id,
        source_type=SourceType.EMAIL_ATTACHMENT,
        owner_id=email.id,
        part=0,
        filename="a.txt",
        mime_type="text/plain",
        data=b"hello",
    )
    first = ks.stage_document(db, **kwargs)
    second = ks.stage_document(db, **kwargs)
    assert first.id == second.id


def test_staging_backlog_limit(db, monkeypatch):
    """Once the staged bytes reach the limit, further documents are skipped, not stored."""
    monkeypatch.setattr(ks, "STAGING_LIMIT_BYTES", 8)
    user = _user(db)
    email = _email(db, user)
    for part in range(2):
        ks.stage_document(
            db,
            user_id=user.id,
            source_type=SourceType.EMAIL_ATTACHMENT,
            owner_id=email.id,
            part=part,
            filename="a.txt",
            mime_type="text/plain",
            data=b"12345",
        )
    first, second = _sources(db, SourceType.EMAIL_ATTACHMENT)
    assert first.status == Status.PENDING.value
    assert (second.status, second.status_detail, second.staged_content) == (
        Status.SKIPPED.value,
        "staging backlog full",
        None,
    )


@needs_documents
def test_a_broken_document_gets_one_retry_then_fails(db):
    """The first parse failure keeps the bytes; the second marks the source failed and drops them."""
    user = _user(db)
    email = _email(db, user)
    ks.stage_document(
        db,
        user_id=user.id,
        source_type=SourceType.EMAIL_ATTACHMENT,
        owner_id=email.id,
        part=0,
        filename="broken.xlsx",
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        data=_BROKEN_XLSX,
    )
    db.commit()
    (source,) = _sources(db, SourceType.EMAIL_ATTACHMENT)

    ks.run_tick(db)
    db.refresh(source)
    assert (source.status, source.attempts) == (Status.PENDING.value, 1)
    assert source.staged_content is not None

    ks.run_tick(db)
    db.refresh(source)
    assert (source.status, source.attempts, source.staged_content) == (Status.FAILED.value, 2, None)
    assert source.status_detail


# --- Discovery -----------------------------------------------------------------


def test_emails_are_ingested_and_spam_is_left_out(db):
    """Mail becomes a chunked source; SPAM/IGNORE mail does not, and reclassifying to SPAM removes it."""
    user = _user(db)
    kept = _email(db, user, subject="Budget", body="Hi,\n\nPlease review.\n\n> earlier text")
    _email(db, user, subject="Win a prize", priority=models.Priority.SPAM)

    ks.run_tick(db)
    (source,) = _sources(db, SourceType.EMAIL)
    assert (source.owner_id, source.status, source.title) == (kept.id, Status.READY.value, "Budget")
    assert "Subject: Budget" in _chunks(db, source)[0].text

    kept.priority = models.Priority.SPAM
    db.commit()
    result = ks.run_tick(db)
    assert result.deleted == 1
    assert _sources(db, SourceType.EMAIL) == []
    assert db.query(models.KnowledgeChunk).count() == 0


def test_growing_conversation_keeps_unchanged_chunks(db):
    """Only the tail chunk of a conversation is rewritten when messages are appended."""
    user = _user(db)
    conversation = models.Conversation(user_id=user.id, title="Planning")
    db.add(conversation)
    db.flush()
    for index in range(30):
        db.add(
            models.ConversationMessage(
                conversation_id=conversation.id, role=models.MessageRole.USER, content=f"note {index} " + "x " * 120
            )
        )
    db.commit()
    ks.run_tick(db)
    (source,) = _sources(db, SourceType.CONVERSATION)
    before = [(chunk.id, chunk.content_hash) for chunk in _chunks(db, source)]

    db.add(models.ConversationMessage(conversation_id=conversation.id, role=models.MessageRole.ASSISTANT, content="ok"))
    conversation.updated_at = time_utils.utcnow() + timedelta(seconds=1)
    db.commit()
    ks.run_tick(db)
    after = [(chunk.id, chunk.content_hash) for chunk in _chunks(db, source)]
    assert after[: len(before) - 1] == before[:-1]
    assert "assistant: ok" in _chunks(db, source)[-1].text


def test_whatsapp_planner_and_calendar_sources(db):
    """WhatsApp chats, planner tasks, and calendar events are ingested; in-place calendar edits are noticed."""
    user = _user(db)
    wa_account = models.WhatsAppAccount(user_id=user.id, display_name="Phone")
    db.add(wa_account)
    db.flush()
    chat = models.WhatsAppConversation(
        user_id=user.id, whatsapp_account_id=wa_account.id, provider_chat_id="c1", title="Family"
    )
    db.add(chat)
    db.flush()
    db.add(
        models.WhatsAppMessage(
            user_id=user.id,
            whatsapp_account_id=wa_account.id,
            whatsapp_conversation_id=chat.id,
            provider_message_id="m1",
            provider_timestamp=time_utils.utcnow(),
            direction=models.WhatsAppMessageDirection.INCOMING,
            sender_display_name="Mum",
            body="Dinner on Sunday?",
            snippet="Dinner on Sunday?",
        )
    )
    db.add(models.PlannerTask(user_id=user.id, title="Renew passport", description="Book appointment"))
    planner = models.PlannerAccount(user_id=user.id, name="cal", provider=list(models.Provider)[0], token="t")
    db.add(planner)
    db.flush()
    event = models.CalendarEvent(
        planner_account_id=planner.id,
        external_id="e1",
        summary="Dentist",
        start_time=time_utils.utcnow().replace(hour=9, minute=0, second=0, microsecond=0),
        end_time=time_utils.utcnow().replace(hour=10, minute=0, second=0, microsecond=0),
    )
    db.add(event)
    db.commit()

    ks.run_tick(db)
    texts = {source.source_type: _chunks(db, source)[0].text for source in _sources(db)}
    assert "Mum: Dinner on Sunday?" in texts[SourceType.WHATSAPP_CONVERSATION.value]
    assert "Renew passport" in texts[SourceType.PLANNER_TASK.value]
    assert "Event: Dentist" in texts[SourceType.CALENDAR_EVENT.value]

    assert "09:00 to " in texts[SourceType.CALENDAR_EVENT.value]

    event.summary = "Dentist (moved)"  # calendar sync edits in place, without a timestamp
    db.commit()
    ks.run_tick(db)
    (calendar,) = _sources(db, SourceType.CALENDAR_EVENT)
    assert "Dentist (moved)" in _chunks(db, calendar)[0].text

    event.end_time = event.end_time + timedelta(hours=1)  # an end-only reschedule is noticed too
    db.commit()
    ks.run_tick(db)
    assert "11:00" in _chunks(db, calendar)[0].text

    db.delete(event)
    db.commit()
    ks.run_tick(db)
    assert _sources(db, SourceType.CALENDAR_EVENT) == []


def _planner_task_texts(db) -> set[tuple[int, str]]:
    return {
        (chunk.user_id, chunk.text) for source in _sources(db, SourceType.PLANNER_TASK) for chunk in _chunks(db, source)
    }


def test_deleting_a_planner_task_drops_its_source_before_the_id_is_reused(db):
    """Deleting a task deletes its source and chunks in the same commit (JAN-221).

    SQLite reuses the highest freed id, so another user's next task gets the same owner id; the old
    source must already be gone, or the orphan sweep would never see it as orphaned.
    """
    from janus.services.planner_item_deletion import delete_local_task

    alice, bob = _user(db, "alice"), _user(db, "bob")
    task = models.PlannerTask(user_id=alice.id, title="Alice secret plan")
    db.add(task)
    db.commit()
    ks.run_tick(db)
    (source,) = _sources(db, SourceType.PLANNER_TASK)
    assert _chunks(db, source)
    task_id, source_id = task.id, source.id

    assert delete_local_task(db, alice.id, task_id) is True
    db.expire_all()
    assert _sources(db, SourceType.PLANNER_TASK) == []
    assert db.query(models.KnowledgeChunk).filter_by(source_id=source_id).count() == 0

    reused = models.PlannerTask(user_id=bob.id, title="Bob errand")
    db.add(reused)
    db.commit()
    assert reused.id == task_id  # the id really is reused
    ks.run_tick(db)
    texts = _planner_task_texts(db)
    assert any(user_id == bob.id and "Bob errand" in text for user_id, text in texts)
    assert not any("Alice secret plan" in text for _, text in texts)


def test_orphan_sweep_matches_the_owner_user_not_only_the_id(db):
    """A source whose owner id now belongs to another user's row is an orphan (JAN-221)."""
    alice, bob = _user(db, "alice"), _user(db, "bob")
    task = models.PlannerTask(user_id=alice.id, title="Alice secret plan")
    db.add(task)
    db.commit()
    ks.run_tick(db)
    task_id = task.id
    db.delete(task)  # removed without the deletion service, e.g. by an older release
    db.commit()
    reused = models.PlannerTask(user_id=bob.id, title="Bob errand")
    db.add(reused)
    db.commit()
    assert reused.id == task_id

    result = ks.run_tick(db)
    assert result.deleted == 1
    assert {source.user_id for source in _sources(db, SourceType.PLANNER_TASK)} == {bob.id}
    assert not any("Alice secret plan" in text for _, text in _planner_task_texts(db))


def test_sources_are_owner_scoped(db):
    """Each user's sources carry their own user_id."""
    alice, bob = _user(db, "alice"), _user(db, "bob")
    _email(db, alice, subject="Alice mail")
    _email(db, bob, subject="Bob mail")
    ks.run_tick(db)
    assert {(source.user_id, source.title) for source in _sources(db)} == {
        (alice.id, "Alice mail"),
        (bob.id, "Bob mail"),
    }
    assert {chunk.user_id for chunk in db.query(models.KnowledgeChunk)} == {alice.id, bob.id}


def test_backfill_runs_until_nothing_is_left(db, monkeypatch):
    """Backfill processes every pending item even when it exceeds one tick's batch."""
    monkeypatch.setattr(ks, "PROCESS_BATCH", 2)
    user = _user(db)
    for index in range(5):
        _email(db, user, subject=f"Mail {index}")
    result = ks.backfill(db)
    assert result.processed == 5
    assert len(_sources(db, SourceType.EMAIL)) == 5


# --- Embeddings ----------------------------------------------------------------


def test_chunks_are_embedded_and_cleaned_up(db, monkeypatch):
    """The sweep embeds chunks; deleting a source removes its chunk embeddings."""
    fake = _FakeEmbeddings()
    monkeypatch.setattr(embedding_service, "build_embeddings", lambda settings, choice: fake)
    user = _user(db)
    db.add(models.Settings(embedding_provider="ollama", embedding_model="fake"))
    db.commit()
    _email(db, user)
    ks.run_tick(db)
    knowledge_index.sweep(db)
    assert db.query(models.KnowledgeEmbedding).filter_by(owner_type="chunk").count() == 1

    (source,) = _sources(db, SourceType.EMAIL)
    ks.delete_source(db, source)
    db.commit()
    assert db.query(models.KnowledgeEmbedding).filter_by(owner_type="chunk").count() == 0


def test_unchanged_chunks_are_not_re_embedded(db, monkeypatch):
    """Appending to a conversation only embeds the chunks whose text changed."""
    fake = _FakeEmbeddings()
    monkeypatch.setattr(embedding_service, "build_embeddings", lambda settings, choice: fake)
    user = _user(db)
    db.add(models.Settings(embedding_provider="ollama", embedding_model="fake"))
    conversation = models.Conversation(user_id=user.id, title="Long")
    db.add(conversation)
    db.flush()
    for index in range(30):
        db.add(
            models.ConversationMessage(
                conversation_id=conversation.id, role=models.MessageRole.USER, content=f"note {index} " + "y " * 120
            )
        )
    db.commit()
    ks.run_tick(db)
    knowledge_index.sweep(db, max_batches=None)
    first_round = len(fake.embedded)

    db.add(models.ConversationMessage(conversation_id=conversation.id, role=models.MessageRole.USER, content="new"))
    conversation.updated_at = time_utils.utcnow() + timedelta(seconds=1)
    db.commit()
    ks.run_tick(db)
    knowledge_index.sweep(db, max_batches=None)
    assert first_round > 3
    assert len(fake.embedded) - first_round == 1


# --- IMAP and processor ----------------------------------------------------------


def test_imap_extracts_attachments():
    """Attachment parts are returned with their bytes; the text body is not."""
    message = EmailMessage()
    message["Subject"] = "With files"
    message.set_content("Body text")
    message.add_attachment(b"col1,col2\n1,2\n", maintype="text", subtype="csv", filename="data.csv")
    service = IMAPMonitor.__new__(IMAPMonitor)
    attachments = service._extract_attachments(message)
    assert attachments == [{"filename": "data.csv", "mime_type": "text/csv", "data": b"col1,col2\n1,2\n"}]


def test_processor_stages_attachments_but_not_for_spam(db):
    """The processor stages attachments of stored mail, except mail filed SPAM/IGNORE."""
    user = _user(db)
    email = _email(db, user)
    spam = _email(db, user, subject="Spam", priority=models.Priority.SPAM)
    processor = EmailProcessor(db)
    data = {"attachments": [{"filename": "notes.txt", "mime_type": "text/plain", "data": b"hello"}]}
    processor._stage_attachments(email, data, user.id)
    processor._stage_attachments(spam, data, user.id)
    (source,) = _sources(db, SourceType.EMAIL_ATTACHMENT)
    assert (source.owner_id, source.status) == (email.id, Status.PENDING.value)


def test_provisional_mail_waits_for_its_classification(db):
    """Mail awaiting its mail.received run, and its attachments, are ingested only once classified (not SPAM)."""
    from janus.processor import PENDING_TRIGGER_PROCESSING_REASON

    user = _user(db)
    waiting = _email(db, user, subject="Pending")
    waiting.reasoning = PENDING_TRIGGER_PROCESSING_REASON
    spam_later = _email(db, user, subject="Later spam")
    spam_later.reasoning = PENDING_TRIGGER_PROCESSING_REASON
    db.commit()
    data = {"attachments": [{"filename": "notes.txt", "mime_type": "text/plain", "data": b"hello"}]}
    EmailProcessor(db)._stage_attachments(waiting, data, user.id)
    EmailProcessor(db)._stage_attachments(spam_later, data, user.id)

    ks.run_tick(db)
    assert _sources(db, SourceType.EMAIL) == []
    assert {source.status for source in _sources(db, SourceType.EMAIL_ATTACHMENT)} == {Status.PENDING.value}
    assert db.query(models.KnowledgeChunk).count() == 0

    waiting.reasoning, waiting.priority = "Classified by Janus", models.Priority.IMPORTANT
    spam_later.reasoning, spam_later.priority = "Classified by Janus", models.Priority.SPAM
    db.commit()
    ks.run_tick(db)
    (email_source,) = _sources(db, SourceType.EMAIL)
    (attachment,) = _sources(db, SourceType.EMAIL_ATTACHMENT)
    assert email_source.owner_id == waiting.id
    assert (attachment.owner_id, attachment.status) == (waiting.id, Status.READY.value)


# --- API and CLI -----------------------------------------------------------------


@pytest.fixture
def auth_client(client, db):
    """An authenticated client for user ``bob``."""
    _user(db, "bob")
    response = client.post("/api/auth/login", json={"username": "bob", "password": "secret"})
    client.headers[security.CSRF_HEADER_NAME] = response.json()["csrf_token"]
    return client


def _upload(client, filename: str, content: bytes, mime: str):
    return client.post("/api/knowledge/uploads", files={"file": (filename, content, mime)})


def test_upload_parses_immediately_and_lists(auth_client, db):
    """An upload is parsed in the request and shows up, with its chunks, in the source list."""
    response = _upload(auth_client, "notes.md", b"# Notes\n\nRemember the milk.", "text/markdown")
    assert response.status_code == 201, response.text
    body = response.json()
    assert (body["status"], body["source_type"], body["chunk_count"]) == ("ready", "upload", 1)

    listed = auth_client.get("/api/knowledge/sources", params={"source_type": "upload"}).json()
    assert [row["title"] for row in listed] == ["notes.md"]
    assert "staged_content" not in listed[0]


def test_upload_rejects_unsupported_and_oversized_files(auth_client, monkeypatch):
    """Unsupported types are 422, files over the limit are 413."""
    assert _upload(auth_client, "photo.jpg", b"\xff\xd8", "image/jpeg").status_code == 422
    monkeypatch.setattr(knowledge_parsing, "MAX_DOCUMENT_BYTES", 4)
    assert _upload(auth_client, "big.txt", b"123456", "text/plain").status_code == 413


@needs_documents
def test_upload_of_a_broken_document_reports_the_error(auth_client):
    """A document that fails to parse is stored as pending with the reason (one retry left)."""
    body = _upload(auth_client, "broken.xlsx", _BROKEN_XLSX, "application/octet-stream").json()
    assert body["status"] == "pending"
    assert body["status_detail"]


def test_sources_api_is_owner_scoped_and_deletes(auth_client, db):
    """Users see and delete only their own sources."""
    alice = _user(db, "alice")
    _email(db, alice, subject="Alice private")
    ks.run_tick(db)
    (alice_source,) = _sources(db, SourceType.EMAIL)

    assert auth_client.get("/api/knowledge/sources").json() == []
    assert auth_client.delete(f"/api/knowledge/sources/{alice_source.id}").status_code == 404

    own = _upload(auth_client, "mine.txt", b"my text", "text/plain").json()
    assert auth_client.delete(f"/api/knowledge/sources/{own['id']}").status_code == 204
    db.expire_all()
    assert db.get(models.KnowledgeSource, own["id"]) is None
    assert db.query(models.KnowledgeChunk).filter_by(source_id=own["id"]).count() == 0


def test_cli_backfill(db, monkeypatch, testing_session_local):
    """``janus knowledge backfill`` reports what it processed."""
    monkeypatch.setattr(database, "SessionLocal", testing_session_local)
    user = _user(db)
    _email(db, user)
    result = CliRunner().invoke(cli.app, ["knowledge", "backfill"])
    assert result.exit_code == 0, result.output
    assert "Processed 1 sources" in result.output


def test_worker_tick_ingests_before_embedding(db, monkeypatch, testing_session_local):
    """The knowledge-index worker tick runs ingestion even with embeddings off."""
    monkeypatch.setattr(database, "SessionLocal", testing_session_local)
    user = _user(db)
    _email(db, user)
    knowledge_index.run_worker_sweep()
    db.expire_all()
    assert len(_sources(db, SourceType.EMAIL)) == 1


def test_schema_never_exposes_staged_bytes():
    """The API schema has no field for staged content."""
    from janus.schemas.knowledge import KnowledgeSource

    assert "staged_content" not in KnowledgeSource.model_fields
