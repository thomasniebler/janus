"""The signed-in user's Telegram bot (JAN-405)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy.orm import Session

from janus import database, models, security
from janus.services import telegram_service
from janus.workers.telegram import start_bot_worker, stop_bot_worker

router = APIRouter(prefix="/api/telegram", tags=["telegram"])


class TelegramConnect(BaseModel):
    """Connect a bot by its @BotFather token (write-only)."""

    token: SecretStr = Field(max_length=100)


class TelegramStatus(BaseModel):
    """The user's bot; ``pairing_code`` is shown only until the user's chat is linked."""

    connected: bool
    bot_username: str | None = None
    linked: bool = False
    pairing_code: str | None = None
    last_error: str | None = None


def _status(bot: models.TelegramBot | None) -> TelegramStatus:
    if bot is None:
        return TelegramStatus(connected=False)
    return TelegramStatus(
        connected=True,
        bot_username=bot.bot_username,
        linked=bot.chat_id is not None,
        pairing_code=bot.pairing_code if bot.chat_id is None else None,
        last_error=bot.last_error,
    )


@router.get("", response_model=TelegramStatus)
def get_status(db: Session = Depends(database.get_db), user: models.User = Depends(security.get_current_user)):
    """Whether a bot is connected and linked to the user's chat."""
    return _status(telegram_service.get_bot(db, user))


@router.post("/connect", response_model=TelegramStatus)
def connect(
    payload: TelegramConnect,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Check the token with Telegram, store it encrypted, and issue a pairing code."""
    try:
        bot = telegram_service.connect(db, user, payload.token.get_secret_value())
    except telegram_service.TelegramError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    db.commit()
    start_bot_worker(user.id)
    return _status(bot)


@router.post("/test", status_code=status.HTTP_204_NO_CONTENT)
def send_test(db: Session = Depends(database.get_db), user: models.User = Depends(security.get_current_user)):
    """Send a test message to the linked chat (counts toward the daily cap)."""
    try:
        telegram_service.send_to_owner(db, user.id, "Test message from Janus.")
    except telegram_service.TelegramError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
def disconnect(db: Session = Depends(database.get_db), user: models.User = Depends(security.get_current_user)):
    """Forget the bot and its token; polling stops."""
    telegram_service.disconnect(db, user)
    db.commit()
    stop_bot_worker(user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
