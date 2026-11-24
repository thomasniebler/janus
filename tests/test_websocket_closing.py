"""A briefing or chat error after the client left must not write to the closed socket (JAN-293).

The e2e logs showed ``RuntimeError: Cannot call "send" once a close message has
been sent`` from ``/ws/briefing``: the run failed (no LLM reachable) after the
browser had already dropped the socket, the ``__ERROR__`` send hit the dead
connection, and the unconditional ``close()`` afterwards raised. These tests drive
the real Starlette ``WebSocket`` over a fake ASGI transport that behaves like
uvicorn once the peer is gone.
"""

import asyncio
from types import SimpleNamespace

import pytest
from starlette.websockets import WebSocket, WebSocketState

from janus.websocket import briefing
from janus.websocket.closing import send_error_and_close


class FakeTransport:
    """ASGI receive/send for one socket; sends fail with ``OSError`` once the client has gone."""

    def __init__(self, *, client_gone_after: int | None = None, incoming: list[dict] | None = None):
        """Fail sends after ``client_gone_after`` messages; deliver ``incoming`` after the connect."""
        self.client_gone_after = client_gone_after
        self.incoming = [{"type": "websocket.connect"}, *(incoming or [])]
        self.sent: list[dict] = []

    async def receive(self) -> dict:
        """Hand out the queued client messages, then wait like an idle client."""
        if self.incoming:
            return self.incoming.pop(0)
        await asyncio.sleep(3600)
        raise AssertionError("unreachable")

    async def send(self, message: dict) -> None:
        """Record a message, or fail like uvicorn once the peer is gone."""
        if self.client_gone_after is not None and len(self.sent) >= self.client_gone_after:
            raise OSError("client disconnected")
        self.sent.append(message)

    def socket(self) -> WebSocket:
        """A real Starlette WebSocket over this transport."""
        scope = {"type": "websocket", "path": "/ws/briefing", "headers": [], "query_string": b""}
        return WebSocket(scope, self.receive, self.send)


def _texts(transport: FakeTransport) -> list[str]:
    return [message["text"] for message in transport.sent if message["type"] == "websocket.send"]


def test_briefing_failure_after_the_client_left_does_not_raise(monkeypatch):
    """The original failure: the run fails after the client is gone; the handler ends quietly."""

    def failing_run(*_args, **_kwargs):
        raise RuntimeError("[Errno 111] Connection refused")

    monkeypatch.setattr(briefing, "_load_or_run_briefing", failing_run)
    # accept and __START__ go out; the client leaves before the error frame.
    transport = FakeTransport(client_gone_after=2)
    websocket = transport.socket()

    asyncio.run(briefing.ws_briefing(websocket, user=SimpleNamespace(id=1)))

    assert transport.sent[0]["type"] == "websocket.accept"
    assert _texts(transport) == ["__START__"]
    assert websocket.application_state == WebSocketState.DISCONNECTED


def test_briefing_failure_on_an_open_socket_still_reports_and_closes(monkeypatch):
    """With the client still there, the error frame and a close go out as before."""

    def failing_run(*_args, **_kwargs):
        raise RuntimeError("LLM exploded")

    monkeypatch.setattr(briefing, "_load_or_run_briefing", failing_run)
    transport = FakeTransport()

    asyncio.run(briefing.ws_briefing(transport.socket(), user=SimpleNamespace(id=1)))

    texts = _texts(transport)
    assert texts[0] == "__START__"
    assert texts[-1].startswith("__ERROR__:")
    assert transport.sent[-1]["type"] == "websocket.close"


def test_error_after_the_client_sent_its_close_is_not_written():
    """Once the client's close has been received, nothing more is sent."""

    async def scenario() -> FakeTransport:
        transport = FakeTransport(incoming=[{"type": "websocket.disconnect", "code": 1001}])
        websocket = transport.socket()
        await websocket.accept()
        await websocket.receive()  # the client's close
        assert websocket.client_state == WebSocketState.DISCONNECTED
        await send_error_and_close(websocket, RuntimeError("late failure"), "chat")
        return transport

    transport = asyncio.run(scenario())

    assert [message["type"] for message in transport.sent] == ["websocket.accept"]


@pytest.mark.parametrize("gone_after", [1, 2])
def test_send_error_and_close_tolerates_a_disconnect_at_either_step(gone_after):
    """The client may vanish before the error frame (1) or before the close (2)."""

    async def scenario() -> tuple[FakeTransport, WebSocket]:
        transport = FakeTransport(client_gone_after=gone_after)
        websocket = transport.socket()
        await websocket.accept()
        await send_error_and_close(websocket, RuntimeError("late failure"), "briefing")
        return transport, websocket

    transport, websocket = asyncio.run(scenario())

    assert transport.sent[0]["type"] == "websocket.accept"
    assert len(transport.sent) == gone_after
    assert websocket.application_state == WebSocketState.DISCONNECTED
