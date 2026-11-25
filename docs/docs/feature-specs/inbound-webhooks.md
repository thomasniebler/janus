# Inbound Webhooks

**Status:** Shipped
**Linear:** JAN-406
**Depends on:** [Runtime triggers](../runtime-triggers.md) and the event catalog (`trigger_events.py`, JAN-210), event filters and rate limits (JAN-363), Fernet encryption (`security.py`)

## Idea

Outside services (GitHub, a CI system, a home server, a monitoring tool)
can tell Janus that something happened by POSTing signed JSON to a webhook
URL. Each accepted delivery fires the `webhook.received` trigger event, so
a trigger definition on that event can react: summarize a failed deploy,
file a follow-up, ping the user.

Inspired by the HMAC webhook endpoint of
[samalgotrader7-ops/janus](https://github.com/samalgotrader7-ops/janus)
(see [External Agents](external-agents.md#acknowledgement)). This is our own
implementation on the trigger catalog.

## Endpoints

- **Model** (`models/webhooks.py`, `WebhookEndpoint`, table `webhook_endpoints`, migration `f3c5a7e9b1d4`). Each endpoint stores:
  - owner, name, and enabled switch (default on);
  - the URL key's SHA-256 hash and an 8-character display prefix;
  - a Fernet-encrypted HMAC secret;
  - a delivery counter and the last delivery time.
- **Shown once.** `POST /api/webhooks` returns the URL path (`/api/webhooks/in/<key>`) and the secret (`whsec_...`); `POST /api/webhooks/{id}/rotate-secret` returns a new secret. Neither appears in the listing again. At most 20 endpoints per user.
- **Management API** (`api/webhooks.py`, cookie session): list, create, rename or switch on/off, rotate secret, delete.
- **UI:** Settings → Runtime triggers → **Webhooks** (`WebhookManager.tsx`, `webhooksApi.ts`). The new URL and secret are shown in a notice that closes with **Done**.

## Delivery

`POST /api/webhooks/in/<key>` is public: no session.

- **Authentication** is the HMAC-SHA256 signature with the endpoint's secret, in one of two styles:

  | Style | Headers | Signed content | Replay protection |
  |---|---|---|---|
  | Janus | `X-Janus-Timestamp: <unix seconds>`, `X-Janus-Signature: sha256=<hex>` | `<timestamp>.` followed by the body | timestamp within 5 minutes, plus deduplication |
  | GitHub | `X-Hub-Signature-256: sha256=<hex>` | the body | deduplication |

  Signatures are compared in constant time. An unsigned or wrongly signed request, or a stale timestamp, answers 401.
- **Deduplication.** The event id is the endpoint id plus a hash of the signed material. `emit_event`'s occurrence key therefore starts at most one run per definition for the same delivery, however often it is sent.
- **Limits.**
  - The body must be JSON, at most 64 KB. Larger bodies answer 413, also when only the declared `Content-Length` is too large; non-JSON answers 400.
  - Each endpoint takes at most 60 deliveries a minute (in process), else 429.
  - An unknown or switched-off key answers 404.
- **CSRF.** The path prefix `/api/webhooks/in/` is exempt from the cookie-session CSRF check (`security.CSRF_EXEMPT_PREFIXES`). The signature authenticates the request, and a sender that happens to hold a Janus cookie must not get a 403.
- **Semgrep.** The route is a triaged `janus-route-without-auth-dependency` exception in `pyproject.toml`.
- **Response.** `202 {"accepted": true, "runs": <n>}`. The delivery counter counts every accepted delivery, including ones that started no run.

## The event

`webhook.received` (subsystem *Webhooks*):

- **Payload:** `webhook` (the name), `webhook_id`, `event_header` (from `X-GitHub-Event`, `X-GitLab-Event`, `X-Event-Type` or `X-Janus-Event`, if present), `received_at`, and `body` (the compacted JSON, first 4,000 characters).
- **Default rate limit:** 30 runs an hour per definition, which the user can change.
- **Filtering:** payload filters narrow it, for example `webhook` *is one of* `GitHub` or `event_header` *is one of* `push`.
- **Untrusted.** Like every event, the payload is rendered into the run's objective inside the escaped `<event_data>` block, as untrusted input.

## Not built

- IP allowlists.
- Per-sender signature schemes beyond the two above (Stripe, Slack).
- Non-JSON bodies.
- Storing delivery bodies for later inspection: the run's payload is the record.
