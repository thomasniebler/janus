# Fixture for `semgrep --test .semgrep` (JAN-267): each `ruleid:` line must be flagged,
# each `ok:` line must not be.
from fastapi import APIRouter, Depends, WebSocket

from janus import database, models, security

router = APIRouter(prefix="/api/example")


# ruleid: janus-route-without-auth-dependency
@router.get("/open")
def open_route(db=Depends(database.get_db)):
    return db


# ok: janus-route-without-auth-dependency
@router.get("/mine")
def my_route(current_user: models.User = Depends(security.get_current_user)):
    return current_user


# ok: janus-route-without-auth-dependency
@router.post("/admin")
async def admin_route(admin=Depends(security.admin_required)):
    return admin


# ruleid: janus-route-without-auth-dependency
@router.post("/token-only")
async def token_only(token=Depends(security.get_token)):
    return token


def lookup(db, event_id, skill_id):
    # ruleid: janus-unscoped-owned-row-lookup
    event = db.get(models.CalendarEvent, event_id)
    # ruleid: janus-unscoped-owned-row-lookup
    rule = db.query(models.Rule).get(event_id)
    # ok: janus-unscoped-owned-row-lookup
    skill = db.get(models.AgentSkill, skill_id)
    return event, rule, skill


# ruleid: janus-websocket-without-auth-dependency
async def ws_open(websocket: WebSocket, db=Depends(database.get_db)):
    await websocket.accept()


# ok: janus-websocket-without-auth-dependency
async def ws_mine(websocket: WebSocket, user: models.User | None = Depends(security.get_user_or_none)):
    await websocket.accept()


# ok: janus-websocket-without-auth-dependency
async def close_quietly(websocket: WebSocket) -> None:
    await websocket.close()


# ok: janus-websocket-without-auth-dependency
async def ws_short_lived(websocket: WebSocket, user: models.User | None = Depends(security.get_websocket_user_or_none)):
    await websocket.accept()


def register_example_websockets(api) -> None:
    api.websocket("/ws/open")(ws_open)
    api.websocket("/ws/mine")(ws_mine)
    api.websocket("/ws/short-lived")(ws_short_lived)
