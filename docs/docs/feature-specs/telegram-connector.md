# Telegram Connector

**Status:** Shipped (inbound trigger event and outbound alerts to the owner; chatting with Janus over Telegram is planned, not built)
**Linear:** JAN-405
**Depends on:** the trigger event catalog (JAN-210) with filters and rate limits (JAN-363), `worker_manager`, Fernet encryption, the WhatsApp alert's pattern (`whatsapp.notify`)

## Idea

Reach Janus from Telegram without a public URL. Each user connects their own
bot, made with @BotFather, and links their own private chat to it. Messages
in that chat start Telegram triggers, and trigger runs can message the user
there. Inspired by the Telegram gateway of
[samalgotrader7-ops/janus](https://github.com/samalgotrader7-ops/janus) (see
[External Agents](external-agents.md#acknowledgement)).

## Connecting and pairing

- **Model.** `models/telegram.py`, `TelegramBot`, table `telegram_bots`, migration `c6f8d0e2a4b7`. Each user has at most one bot. It stores:
  - the Fernet-encrypted token and the bot's `@username`;
  - the linked `chat_id` and a one-time `pairing_code`;
  - `enabled`, the update offset (`last_update_id`), and the last error;
  - the daily send counter.
- **Connect.** `services/telegram_service.connect` checks the token's shape, then calls `getMe`; it must be a bot. It stores the token encrypted and issues a fresh 8-digit pairing code, which unlinks any previous chat.
- **Pairing.** Until a chat is linked, the bot reacts only to `/start <code>` (or the bare code) in a **private** chat. That chat becomes `chat_id`, the code is cleared, and the bot confirms. Every other message, from anyone, is ignored.
- **Who can reach the bot.** After pairing, only messages from the linked chat count.
- **API** (`api/telegram.py`, cookie session):
  - `GET /api/telegram`: connected, `@username`, linked, the pairing code while unlinked, last error.
  - `POST /api/telegram/connect` with `{token}`.
  - `POST /api/telegram/test`: a test message, which counts toward the cap.
  - `DELETE /api/telegram`.

  The token is never returned.
- **User deletion** removes the bot explicitly (`telegram_service.delete_all_for_user`), like webhooks and remote agents.

## Inbound: polling and the event

- **Worker.** `workers/telegram.py` runs one supervised worker per enabled bot (`telegram:<user id>`), started by `startup.ensure_runtime_initialized` for every enabled bot and by `connect`.
  - It long-polls `getUpdates` (25 s, `allowed_updates=["message"]`), reloading the bot row on every poll. A new token or pairing therefore applies on the next poll without restarting the thread, which `worker_manager` cannot do during a long poll.
  - It ends once the row is gone or switched off.
  - A failed poll records `last_error` and retries after 15 s.
  - Owner-bound logging (`event_logger.owned_by`) attributes the work and any LLM usage to the user.
- **Event.** A text message from the linked chat emits `telegram.message_received` for the bot's owner:
  - payload: `message_id`, `bot`, `sender`, `received_at`, `body` (the first 4,000 characters);
  - event id `<bot>:<chat>:<message id>`;
  - default limit 30 runs an hour.
- **Untrusted.** The payload reaches the run as untrusted `<event_data>`, like every event.
- **Offset.** It advances past every handled update, so nothing is handled twice. One failing update is logged and skipped rather than blocking the rest.
- **Reconnects during a poll.** When a long poll returns, the worker reloads the bot. If the token changed (the user connected another bot) or the bot was removed meanwhile, the updates are discarded, so the old bot's offset never lands on the new one.
- **Token in logs.** The Bot API puts the token in the URL path, and httpx logs request URLs at INFO. A logging filter on the `httpx` and `httpcore` loggers replaces it with `/bot<redacted>` before any handler, including the log file, sees the record.

## Outbound: alerts to the owner

`telegram.notify` (`agent_toolboxes/telegram.py`, tool `notify_me_on_telegram`):

- **Tier:** `operation="write"`, `risk_level="low"`, `confirmation="recommended"`. The recipient is never an argument, so the tool can only reach the owner's linked chat.
- **Availability:** only in `trigger` and `automation` steps, and only with the `telegram` capability, which `capability_profile` grants once a chat is linked.
- **Message rules:** at most 600 characters; no URLs, links or images (`refuse_external_references`); 20 messages a day per bot.
- **Injection:** after a suspected prompt injection in the step it answers `BLOCKED` and sends nothing.

The Bot API base URL is the setting `telegram_api_base_url`, a test seam for the e2e fake.

## Not built

- Chatting with Janus over Telegram: a full agent turn per message, with approvals.
- Group chats, media and voice messages.
- Telegram webhooks instead of long polling (they would need a public URL).
