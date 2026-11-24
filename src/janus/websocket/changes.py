"""Owner-scoped domain-change WebSocket for live UI cache invalidation (JAN-104).

``/ws/changes`` authenticates exactly like ``/ws/chat`` (cookie or bearer JWT
via ``security.get_websocket_user_or_none``, whose DB session is closed before the
socket opens) and then streams JSON frames:

* ``{"type": "ready"}`` once the subscription is registered;
* ``{"type": "changed", "domains": [...]}`` after a commit touched one of the
  user's domains (see ``janus.domain_changes.DOMAINS``); bursts within a short
  window are coalesced into one frame;
* ``{"type": "ping"}`` as a heartbeat. Before each heartbeat the session is
  re-validated, so a logged-out or revoked session is closed with 1008.

Frames carry domain names only, never row data. The client sends nothing; any
inbound frame is ignored.
"""

import asyncio
import json
import logging

from fastapi import Depends, WebSocket, WebSocketDisconnect, status

from janus import database, domain_changes, models, security

logger = logging.getLogger("janus")

HEARTBEAT_SECONDS = 25.0
COALESCE_SECONDS = 0.1


async def _session_still_valid(token: security.AuthToken | None, user_id: int) -> bool:
    """Re-run the handshake's session checks with a short-lived DB session."""
    db = database.SessionLocal()
    try:
        user = await security.get_user_or_none(db=db, token=token)
        return user is not None and user.id == user_id
    finally:
        db.close()


async def _drain_client(websocket: WebSocket) -> None:
    """Consume (and ignore) inbound frames until the client disconnects."""
    try:
        while True:
            await websocket.receive_text()
    except (WebSocketDisconnect, RuntimeError):
        return


async def ws_changes(
    websocket: WebSocket,
    user: models.User | None = Depends(security.get_websocket_user_or_none),
    token: security.AuthToken | None = Depends(security.get_token),
):
    """Stream the authenticated user's domain-change notifications."""
    await websocket.accept()
    if not user:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    # The socket lives for the whole tab session, so it holds no DB session: the
    # handshake's is already closed, and each heartbeat re-check opens its own.
    user_id = user.id

    subscription = domain_changes.bus.subscribe(user_id)
    receiver = asyncio.create_task(_drain_client(websocket))
    try:
        await websocket.send_text(json.dumps({"type": "ready"}))
        while not receiver.done():
            waiter = asyncio.create_task(subscription.wait(HEARTBEAT_SECONDS))
            await asyncio.wait({waiter, receiver}, return_when=asyncio.FIRST_COMPLETED)
            if receiver.done():
                waiter.cancel()
                break
            changed = waiter.result()
            if changed:
                await asyncio.sleep(COALESCE_SECONDS)
                changed = changed | subscription.take()
                await websocket.send_text(json.dumps({"type": "changed", "domains": sorted(changed)}))
                continue
            if not await _session_still_valid(token, user_id):
                await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                break
            await websocket.send_text(json.dumps({"type": "ping"}))
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        domain_changes.bus.unsubscribe(subscription)
        receiver.cancel()


def register_changes_websocket(api) -> None:
    """Install the session listeners and register the domain-change WebSocket."""
    domain_changes.install()
    api.websocket("/ws/changes")(ws_changes)
