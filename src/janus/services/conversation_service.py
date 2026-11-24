"""Owner-scoped persistence for Janus chat conversations (JAN-137, JAN-319)."""

import html
import re
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from urllib.parse import unquote

from markdown_it import MarkdownIt
from markdown_it.token import Token
from sqlalchemy import select

from janus import delegation_service, models
from janus.services import image_store, toolset_store
from janus.services.base import BaseService

#: Conversations unattended runs may start for one owner per rolling day (JAN-319).
MAX_JANUS_CONVERSATIONS_PER_DAY = 5
JANUS_CONVERSATION_WINDOW = timedelta(days=1)
#: Conversations one unattended run may start (JAN-319).
MAX_JANUS_CONVERSATIONS_PER_RUN = 1
#: Longest title and opening message of a conversation Janus starts.
MAX_JANUS_CONVERSATION_TITLE_CHARS = 120
MAX_JANUS_CONVERSATION_MESSAGE_CHARS = 4000

#: What a Janus-started conversation must not contain (JAN-319): a URL with a
#: scheme, a protocol-relative or ``www.`` link, or a Markdown image. The chat
#: renders the opening message as Markdown, so an image would be fetched the
#: moment the user opens it, and a link could carry data a run read out of
#: Janus. App paths such as ``/settings?tab=triggers`` stay allowed.
#:
#: This pattern runs on the *canonical* text (:func:`_canonical_text`), so a
#: backslash escape (``https\://``) or an entity (``https&#58;//``) cannot
#: hide a scheme from it; :func:`_link_destinations` additionally checks every
#: destination a CommonMark parser resolves.
_EXTERNAL_REFERENCE = re.compile(
    r"[a-z][a-z0-9+.-]*://|\b(?:https?|data|javascript|mailto|ftp|file):|(?<![\w/])//[^\s/]|\bwww\.|!\[",
    re.IGNORECASE,
)
#: CommonMark backslash escapes: a backslash before any ASCII punctuation character.
_BACKSLASH_ESCAPE = re.compile(r"\\([!-/:-@\[-`{-~])")
#: Characters the URL parser drops or trims before reading a scheme (ASCII tab and
#: newlines anywhere, C0 controls and spaces at the ends).
_URL_IGNORED = re.compile(r"[\x00-\x20\x7f]")
#: The only link destinations a Janus-started conversation may use: an app path
#: (``/settings``, never ``//host`` or ``/\\host``), or an in-page anchor.
_APP_DESTINATION = re.compile(r"^(?:/(?![/\\])|#)")
_MAX_DECODE_ROUNDS = 5
_MARKDOWN = MarkdownIt("commonmark").enable(["table", "strikethrough"])


def _canonical_text(text: str) -> str:
    """Resolves entities and backslash escapes the way a Markdown renderer would, repeatedly.

    Decoding until nothing changes (bounded) is stricter than a renderer, which
    decodes once, so a doubly encoded scheme is refused too.
    """
    for _ in range(_MAX_DECODE_ROUNDS):
        decoded = _BACKSLASH_ESCAPE.sub(r"\1", html.unescape(text))
        if decoded == text:
            break
        text = decoded
    return text


def _iter_tokens(tokens: list[Token]) -> Iterator[Token]:
    for token in tokens:
        yield token
        if token.children:
            yield from _iter_tokens(token.children)


def _link_destinations(text: str) -> Iterator[str]:
    """Yields every link destination and image source CommonMark resolves (inline, reference, autolink).

    Images yield ``"!"``, which never passes :data:`_APP_DESTINATION`.
    """
    for token in _iter_tokens(_MARKDOWN.parse(text)):
        if token.type == "image":
            yield "!"
        elif token.type == "link_open":
            yield str(token.attrGet("href") or "")


def _is_app_destination(destination: str) -> bool:
    canonical = _URL_IGNORED.sub("", _canonical_text(unquote(destination)))
    return bool(_APP_DESTINATION.match(canonical))


class JanusConversationError(ValueError):
    """Raised when Janus may not start a conversation (content, run identity, or a limit)."""


def _naive_utc(moment: datetime) -> datetime:
    """Stored timestamps are naive UTC; compare against the same."""
    return moment.astimezone(UTC).replace(tzinfo=None) if moment.tzinfo else moment


def _clean_title(title: str) -> str:
    cleaned = " ".join((title or "").split())
    if not cleaned:
        raise JanusConversationError("title must not be empty")
    if len(cleaned) > MAX_JANUS_CONVERSATION_TITLE_CHARS:
        raise JanusConversationError(f"title must be at most {MAX_JANUS_CONVERSATION_TITLE_CHARS} characters")
    return cleaned


def _clean_message(message: str) -> str:
    cleaned = (message or "").strip()
    if not cleaned:
        raise JanusConversationError("message must not be empty")
    if len(cleaned) > MAX_JANUS_CONVERSATION_MESSAGE_CHARS:
        raise JanusConversationError(
            f"message must be at most {MAX_JANUS_CONVERSATION_MESSAGE_CHARS} characters; summarize it"
        )
    return cleaned


def _refuse_external_references(*texts: str) -> None:
    for text in texts:
        if (
            _EXTERNAL_REFERENCE.search(text)
            or _EXTERNAL_REFERENCE.search(_canonical_text(text))
            or not all(_is_app_destination(destination) for destination in _link_destinations(text))
        ):
            raise JanusConversationError(
                "a conversation Janus starts may not contain links to other sites, URLs, or images; "
                "describe the item in words (links inside Janus such as /settings are fine)"
            )


def refuse_external_references(*texts: str) -> None:
    """Raise :class:`JanusConversationError` when any text holds a URL, an off-site link, or an image (JAN-319).

    The same check guards every text Janus pushes to the owner unprompted (the secretary's WhatsApp alert).
    """
    _refuse_external_references(*texts)


def owner_lock_statement(user_id: int):
    """The ``SELECT ... FOR UPDATE`` on the owner's ``users`` row that serializes Janus conversation starts."""
    return select(models.User.id).where(models.User.id == user_id).with_for_update()


class ConversationService(BaseService):
    """Reads and mutates chat conversations, always scoped to one owner.

    Every lookup filters on ``user_id``; a conversation that does not exist and
    one owned by another user are indistinguishable to callers (``None`` /
    ``False``), so routers answer both with the same non-disclosing 404.
    """

    def list_conversations_for_user(self, user_id: int) -> list[models.Conversation]:
        """Returns ``user_id``'s conversations, most recently updated first."""
        return (
            self.db.query(models.Conversation)
            .filter(models.Conversation.user_id == user_id)
            .order_by(models.Conversation.updated_at.desc())
            .all()
        )

    def create_conversation_for_user(self, user_id: int, title: str | None = None) -> models.Conversation:
        """Creates an empty conversation owned by ``user_id``."""
        conversation = models.Conversation(user_id=user_id, title=title)
        self.db.add(conversation)
        self.db.commit()
        self.db.refresh(conversation)
        return conversation

    def get_conversation_for_user(self, conversation_id: int, user_id: int) -> models.Conversation | None:
        """Returns a conversation only if it belongs to ``user_id``."""
        return (
            self.db.query(models.Conversation)
            .filter(
                models.Conversation.id == conversation_id,
                models.Conversation.user_id == user_id,
            )
            .first()
        )

    def mark_read_for_user(self, conversation: models.Conversation) -> None:
        """Clears the *new* marker of a conversation Janus started, once its owner opens it (JAN-319)."""
        if conversation.unread:
            conversation.unread = False
            self.db.commit()
            self.db.refresh(conversation)

    def count_janus_conversations_since(self, user_id: int, since: datetime) -> int:
        """Counts the conversations unattended runs started for ``user_id`` since ``since``."""
        return (
            self.db.query(models.Conversation)
            .filter(
                models.Conversation.user_id == user_id,
                models.Conversation.origin == models.ConversationOrigin.JANUS.value,
                models.Conversation.created_at >= _naive_utc(since),
            )
            .count()
        )

    def count_janus_conversations_of_run(self, user_id: int, trigger_id: str) -> int:
        """Counts the conversations one unattended run started for ``user_id``."""
        return (
            self.db.query(models.Conversation)
            .filter(
                models.Conversation.user_id == user_id,
                models.Conversation.origin_trigger_id == trigger_id,
            )
            .count()
        )

    def start_janus_conversation(
        self, user_id: int, *, title: str, message: str, trigger_id: str
    ) -> models.Conversation:
        """Starts a new, unread conversation for ``user_id`` whose first message is Janus's ``message`` (JAN-319).

        The owner is always the caller's ``user_id`` (the run's owner), never a
        model argument. Refuses, before writing anything, empty or overlong
        text, any URL, link to another site, or image, a second conversation
        from the same run (:data:`MAX_JANUS_CONVERSATIONS_PER_RUN`), and the
        owner's :data:`MAX_JANUS_CONVERSATIONS_PER_DAY`-plus-first within a
        rolling day. Deleting a conversation gives its slot back.
        """
        clean_title = _clean_title(title)
        clean_message = _clean_message(message)
        _refuse_external_references(clean_title, clean_message)
        self._lock_owner(user_id)
        since = datetime.now(UTC) - JANUS_CONVERSATION_WINDOW
        self._check_janus_limits(user_id, trigger_id, since, already_added=0)
        conversation = models.Conversation(
            user_id=user_id,
            title=clean_title,
            origin=models.ConversationOrigin.JANUS.value,
            origin_trigger_id=trigger_id,
            unread=True,
        )
        conversation.messages.append(
            models.ConversationMessage(role=models.MessageRole.ASSISTANT, content=clean_message)
        )
        self.db.add(conversation)
        self.db.flush()
        # Count again with the new row in place, now inside the write transaction: on SQLite the
        # insert holds the database's single write lock until the commit, so a concurrent start
        # for the same owner sees this row; the loser rolls back (JAN-319).
        try:
            self._check_janus_limits(user_id, trigger_id, since, already_added=1)
        except JanusConversationError:
            self.db.rollback()
            raise
        self.db.commit()
        self.db.refresh(conversation)
        return conversation

    def _lock_owner(self, user_id: int) -> None:
        """Serializes concurrent Janus conversation starts for one owner (JAN-319).

        On PostgreSQL this takes a row lock on the owner (``SELECT ... FOR
        UPDATE``) held until the commit, so two transactions cannot both count
        the other's insert as absent. SQLite has no row locks and SQLAlchemy
        omits the clause there; its single database write lock, taken by the
        insert and held through the recount and the commit, serializes instead.
        """
        self.db.execute(owner_lock_statement(user_id))

    def _check_janus_limits(self, user_id: int, trigger_id: str, since: datetime, *, already_added: int) -> None:
        """Refuses a Janus conversation over the per-run or per-day limit, counting ``already_added`` pending rows."""
        if (
            self.count_janus_conversations_of_run(user_id, trigger_id) - already_added
            >= MAX_JANUS_CONVERSATIONS_PER_RUN
        ):
            raise JanusConversationError(
                f"this run has already started {MAX_JANUS_CONVERSATIONS_PER_RUN} conversation(s), the most one run may"
            )
        if self.count_janus_conversations_since(user_id, since) - already_added >= MAX_JANUS_CONVERSATIONS_PER_DAY:
            raise JanusConversationError(
                f"Janus has already started {MAX_JANUS_CONVERSATIONS_PER_DAY} conversations in the last day, "
                "the most allowed; put this in the run's result instead"
            )

    def delete_conversation_for_user(self, conversation_id: int, user_id: int) -> bool:
        """Deletes a conversation owned by ``user_id`` with its messages and images; ``False`` if missing or foreign.

        The image rows go with the conversation (ORM cascade); their files are
        removed once the delete is committed (JAN-253).
        """
        conversation = self.get_conversation_for_user(conversation_id, user_id)
        if conversation is None:
            return False
        image_paths = image_store.conversation_image_paths(self.db, conversation.id)
        # Its brain-selected toolsets (JAN-300) go too, so a reused id never inherits them.
        toolset_store.forget_task(self.db, user_id, f"chat-conversation-{conversation.id}")
        self.db.delete(conversation)
        self.db.commit()
        image_store.delete_files(image_paths)
        return True

    def list_delegations_for_user(
        self,
        conversation_id: int,
        user_id: int,
        *,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[models.RuntimeDelegation], int] | None:
        """Returns one newest-first page of a conversation's delegated tasks and their total (JAN-208).

        ``None`` when the conversation is missing or owned by someone else, so
        the router answers both with the same 404. The delegation rows are also
        filtered on ``user_id`` as defense in depth. Delegations that outlived
        their lease are marked failed first, so a page never shows an orphaned
        run as still running.
        """
        if limit < 1:
            raise ValueError("limit must be at least 1")
        if offset < 0:
            raise ValueError("offset must not be negative")
        if self.get_conversation_for_user(conversation_id, user_id) is None:
            return None
        delegation_service.expire_stale_delegations(self.db, user_id)
        query = self.db.query(models.RuntimeDelegation).filter(
            models.RuntimeDelegation.conversation_id == conversation_id,
            models.RuntimeDelegation.user_id == user_id,
        )
        total = query.count()
        rows = (
            query.order_by(models.RuntimeDelegation.created_at.desc(), models.RuntimeDelegation.id.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        return rows, total
