"""A scripted `NewClient` that a Playwright spec drives through files.

Each client is keyed by its `uuid` (`setup-<id>` for a pairing session and for the
account that pairing produced). In `$JANUS_E2E_FAKE_NEONIZE_DIR`:

- `<uuid>.log.jsonl` -- the client appends one JSON line per lifecycle step
  (`connect`, `connected`, `disconnect`, `closed`) and per dispatched command, so a spec
  can wait for a connection and count reconnects.
- `<uuid>.commands.jsonl` -- the spec appends one JSON command per line. A client only
  reads lines written after its `connect()` started, so a reconnect never replays
  earlier commands (which is also why no history is redelivered after a restart).

Commands (`type`): `qr` (`code`), `pair_success`, `pair_error` (`error`), `connected`,
`disconnected`, `logged_out`, `message` and `history_sync` (see `_message_event` and
`_history_sync_event` for their fields).

Like a real Neonize session store, the file at the client's session database path marks
the session as paired: the `connected` command writes it, and a client that starts on a
paired store connects on its own, the way the durable account connector resumes a
session after pairing.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from neonize.events import (
    ConnectedEv,
    DisconnectedEv,
    HistorySyncEv,
    LoggedOutEv,
    MessageEv,
    PairStatusEv,
)

_POLL_SECONDS = 0.1


def _control_dir() -> Path:
    raw = os.environ.get("JANUS_E2E_FAKE_NEONIZE_DIR")
    if not raw:
        raise RuntimeError("The fake Neonize client needs JANUS_E2E_FAKE_NEONIZE_DIR; it only runs under e2e.")
    path = Path(raw)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _jid(value: str | None) -> SimpleNamespace:
    user, _, server = (value or "").partition("@")
    return SimpleNamespace(User=user, Server=server)


def _no_fields() -> list[Any]:
    return []


def _text_message(text: str) -> SimpleNamespace:
    return SimpleNamespace(conversation=text, audioMessage=SimpleNamespace(ListFields=_no_fields))


def _message_event(command: dict[str, Any]) -> MessageEv:
    """Build a live message: `id`, `chat`, optional `sender`, `pushname`, `text`, `from_me`, `timestamp`."""
    chat = command["chat"]
    return MessageEv(
        Info=SimpleNamespace(
            ID=command["id"],
            Pushname=command.get("pushname", ""),
            Timestamp=command.get("timestamp", time.time()),
            MessageSource=SimpleNamespace(
                Chat=_jid(chat),
                Sender=_jid(command.get("sender")),
                IsGroup=chat.endswith("@g.us"),
                IsFromMe=bool(command.get("from_me", False)),
            ),
        ),
        Message=_text_message(command.get("text", "")),
    )


def _history_sync_event(command: dict[str, Any]) -> HistorySyncEv:
    """Build a backlog push: `conversations` of `{id, name, unread, messages: [{id, text, from_me, ...}]}`."""
    conversations = []
    for conversation in command["conversations"]:
        messages = [
            SimpleNamespace(
                message=SimpleNamespace(
                    key=SimpleNamespace(
                        ID=message["id"],
                        fromMe=bool(message.get("from_me", False)),
                        participant=message.get("participant", ""),
                    ),
                    message=_text_message(message.get("text", "")),
                    messageTimestamp=message.get("timestamp", time.time()),
                    pushName=message.get("pushname", ""),
                )
            )
            for message in conversation.get("messages", [])
        ]
        conversations.append(
            SimpleNamespace(
                ID=conversation["id"],
                name=conversation.get("name", ""),
                displayName="",
                unreadCount=conversation.get("unread", 0),
                messages=messages,
            )
        )
    return HistorySyncEv(Data=SimpleNamespace(conversations=conversations))


class NewClient:
    """Fake of `neonize.client.NewClient`: blocks in `connect()` and replays scripted commands."""

    def __init__(self, name: str, uuid: str | None = None) -> None:
        """Bind the client to a session store path and a client id, as the real one does."""
        self.name = name
        self.uuid = uuid or Path(name).stem
        self._handlers: dict[type, Callable[[Any, Any], None]] = {}
        self._qr_handler: Callable[[Any, bytes], None] | None = None
        self._stop = threading.Event()
        self._dir = _control_dir()
        self._log_path = self._dir / f"{self.uuid}.log.jsonl"
        self._commands_path = self._dir / f"{self.uuid}.commands.jsonl"

    def event(self, event_type: type) -> Callable[[Callable[[Any, Any], None]], Callable[[Any, Any], None]]:
        """Return a decorator that registers the handler for one event type."""

        def register(handler: Callable[[Any, Any], None]) -> Callable[[Any, Any], None]:
            self._handlers[event_type] = handler
            return handler

        return register

    def qr(self, handler: Callable[[Any, bytes], None]) -> Callable[[Any, bytes], None]:
        """Register the QR-code handler."""
        self._qr_handler = handler
        return handler

    def connect(self) -> None:
        """Block until `disconnect()`, dispatching every command the spec appends meanwhile."""
        offset = self._commands_path.stat().st_size if self._commands_path.exists() else 0
        self._log("connect", session=self.name)
        if Path(self.name).exists():
            self._emit(ConnectedEv())
            self._log("connected")
        while not self._stop.is_set():
            offset = self._dispatch_new_commands(offset)
            self._stop.wait(_POLL_SECONDS)
        self._log("closed")

    def disconnect(self) -> None:
        """Make `connect()` return; safe from any thread."""
        self._log("disconnect")
        self._stop.set()

    def _dispatch_new_commands(self, offset: int) -> int:
        if not self._commands_path.exists():
            return offset
        with self._commands_path.open("rb") as handle:
            handle.seek(offset)
            chunk = handle.read()
        # Only consume complete lines; a half-written one is read on the next poll.
        complete = chunk[: chunk.rfind(b"\n") + 1]
        for line in complete.decode("utf-8").splitlines():
            if line.strip():
                self._dispatch(json.loads(line))
        return offset + len(complete)

    def _dispatch(self, command: dict[str, Any]) -> None:
        kind = command["type"]
        if kind == "qr":
            if self._qr_handler is not None:
                self._qr_handler(self, command["code"].encode("utf-8"))
        elif kind == "pair_success":
            self._emit(PairStatusEv(Status=PairStatusEv.SUCCESS, Error=""))
        elif kind == "pair_error":
            self._emit(PairStatusEv(Status=PairStatusEv.ERROR, Error=command.get("error", "")))
        elif kind == "connected":
            Path(self.name).parent.mkdir(parents=True, exist_ok=True)
            Path(self.name).write_text(json.dumps({"paired": True, "uuid": self.uuid}), encoding="utf-8")
            self._emit(ConnectedEv())
        elif kind == "disconnected":
            self._emit(DisconnectedEv())
        elif kind == "logged_out":
            self._emit(LoggedOutEv())
        elif kind == "message":
            self._emit(_message_event(command))
        elif kind == "history_sync":
            self._emit(_history_sync_event(command))
        else:
            raise ValueError(f"Unknown fake Neonize command: {kind}")
        self._log("dispatched", command=kind)

    def _emit(self, event: Any) -> None:
        handler = self._handlers.get(type(event))
        if handler is not None:
            handler(self, event)

    def _log(self, event: str, **fields: Any) -> None:
        line = json.dumps({"event": event, "at": time.time(), **fields})
        with self._log_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
