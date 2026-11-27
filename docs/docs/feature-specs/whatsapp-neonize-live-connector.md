# WhatsApp Neonize Live Connector

**Status:** Shipped
**Linear:** JAN-150 (parent cluster JAN-148); review and follow-up fixes: JAN-151, JAN-161, JAN-172; chat read tools (the read-only `whatsapp` toolbox): JAN-347; sending text messages (`whatsapp.send`): JAN-360 (shipped)
**Depends on:** [WhatsApp Neonize Foundation](whatsapp-neonize-foundation.md), `neonize` package (native library, optional `whatsapp` extra), background workers runtime tech spec


## Context

- Linear issue: `JAN-150`
- Parent feature cluster: `JAN-148 Add WhatsApp access in Janus via Neonize`
- Prior track: [WhatsApp Neonize Foundation](whatsapp-neonize-foundation.md) (`JAN-149`)
- Scope type: first live-implementation track, superseding the foundation track's stub adapter

## Problem

The foundation track (`JAN-149`) intentionally shipped a docs-and-scaffolding-only WhatsApp integration: `WhatsAppAccount`/`WhatsAppConversation`/`WhatsAppMessage` persistence, a setup-session wizard, a REST API, and a background sync worker all existed, but the underlying `NeonizeAdapter` never called into the real `neonize` package. `fetch_recent_history()` always returned empty results and pairing always reported "unsupported." Nothing in Janus could actually connect to WhatsApp.

This track replaces that stub with a real connection to WhatsApp using the `neonize` library (v0.4.1.post0), while reusing as much of the foundation track's persistence and API surface as possible.

## Provider/runtime resolution

The foundation doc's Rollout Risk #1 ("Provider/runtime mismatch") is resolved: `neonize.client.NewClient.connect()` is a blocking call that runs for the life of one WhatsApp connection and dispatches events to registered Python callbacks. This fits Janus's existing thread-per-connection model directly (the same shape as `IMAPMonitor.idle_loop`) — no architecture pivot or async runtime was needed.

## Pull-based ingestion is retired

The foundation track's `fetch_recent_history(since)` design assumed a pull/fetch-by-date API. No such API exists in `neonize`: message history only arrives as a one-time `HistorySyncEv` pushed by WhatsApp shortly after pairing (bounded by however much backlog WhatsApp's servers choose to send — not configurable from the client), and as ongoing live `MessageEv` events for anything sent or received while connected. Consequently:

- `WhatsAppService.sync_recent_history()` is retired. Its upsert logic was extracted into two reusable methods, `upsert_conversation()` and `upsert_message()`, which the live connector's event handlers call directly.
- The `POST /api/whatsapp/accounts/{id}/sync` endpoint no longer runs a bounded history pull. It restarts the persistent connector as a best-effort connectivity refresh (`WhatsAppService.restart_connector()`) and cannot force redelivery of messages already missed.
- The "last 3 days" experience is now a function of when the account was paired and how much backlog `HistorySyncEv` happens to deliver, not a guaranteed lookback query. This is an accepted limitation, not a bug.

## Installation (optional `whatsapp` extra)

`neonize` and `segno` (the pairing QR code) are the optional `whatsapp` extra, not base dependencies: `uv sync --extra whatsapp`. Neonize's Go library needs glibc 2.34, so the extra stays off the glibc 2.17 production host, which keeps Neonize's Pillow dependency off it too. Without the extra, the import probe reports "WhatsApp support is not installed" and live pairing and the connector stay unavailable, as on a host where the native library fails to load. See [deployment operations](../tech-spec/deployment-operations.md).

## Connector architecture

`NeonizeConnector` (`src/janus/whatsapp_neonize.py`) owns one Neonize client and runs in one of two modes. It turns Neonize events into `NeonizeConversationPayload`/`NeonizeMessagePayload` with the functions in `src/janus/whatsapp_translate.py`.

- **Account mode** (`account_id` set): a durable, supervised connection registered as `whatsapp:{account.id}` via `worker_manager`. Registers `ConnectedEv`/`DisconnectedEv`/`LoggedOutEv`/`ConnectFailureEv` (connection-status bookkeeping) plus `MessageEv`/`HistorySyncEv` (message ingestion). One thread per enabled account, started at process boot (`cli.py`) and whenever an account is created, re-enabled, or has its session material refreshed.
- **Setup mode** (`setup_session_id` set): a short-lived, unsupervised connection used only during the pairing wizard. Registers `ConnectedEv`/`DisconnectedEv`/`LoggedOutEv`/`ConnectFailureEv` plus the QR callback and `PairStatusEv`. On a successful connection it marks the setup session `PAIRED`, persists the sqlite session path, and immediately stops — the durable per-account connection is started separately once `complete_setup_session` finalizes the account, reopening the same sqlite session file (Neonize resumes a paired session by reusing the same path and client UUID). A setup-mode connector ends with the client's `stop()`, since Neonize's `disconnect()` leaves the blocking `connect()` running.
- **Fresh session store per pairing attempt:** every `start-pairing` call opens a new `whatsapp_sessions/setup-<id>-<random>.db` that does not exist yet. Neonize logs in as the first device stored in an existing file instead of offering a QR code, so a reused path (for example one left by an older Janus database whose setup-session ids collide) would resume a stale, possibly unlinked device.
- **Pairing failures are terminal:** a `LoggedOutEv` or `ConnectFailureEv` during pairing marks the setup session `FAILED` with the reason and stops the client, so the wizard reports the failure instead of waiting for a QR code that never comes.

Every event handler opens and closes its own `database.SessionLocal()`; nothing shares a session with the blocking `connect()` call or across callbacks, since Neonize's event dispatch does not guarantee callbacks run on the same thread that called `connect()`.

`NeonizeAdapter` keeps its narrower, setup-time-only role: pairing capability checks (now genuinely `supported=True` when the package imports), starting/cancelling a setup-mode `NeonizeConnector` via a small in-process pairing registry, and fallback session import/export.

## Voice messages (not yet transcribed)

This track detects push-to-talk voice notes (`audioMessage.PTT`) and persists them with `message_type="voice_note"` and a duration/mimetype summary in `attachment_summary`, matching the foundation track's "metadata only" media policy. Local transcription is out of scope for this track and is planned as a follow-up (new `transcript` column, a queued transcription worker, `faster-whisper`).

## Non-goals for this track

1. Local voice-note transcription (follow-up track).
2. Persisted, dashboard-surfaced daily digests per conversation (follow-up track).
3. Pair-by-phone-number (`PairPhone`) as an alternative to QR pairing — QR is the only pairing path implemented here.
4. Group metadata beyond what arrives inline on messages/history sync (no dedicated `GroupInfoEv` handling, so group titles may show as a raw JID until WhatsApp pushes a name via history sync).
5. Sending media, reactions, or WhatsApp Status posts. Janus can *read* the stored conversations of enabled accounts through the read-only `whatsapp` toolbox (JAN-347, see [tools](../tools.md#read-whatsapp)) and, since JAN-360, send plain-text messages over the live connector through the `whatsapp.send` toolbox (`send_whatsapp_message`, approved per call in chat; see [tools](../tools.md#send-whatsapp)).

## Test implications

- Event-translation and ingestion logic (`describe_message`, `translate_live_message`, `translate_history_sync`, `NeonizeConnector._ingest`) is covered with real `neonize` protobuf objects, not a live connection — see `tests/test_whatsapp_connector.py`.
- Pairing state transitions (QR received, pair success, pair failure) are covered against a fake client injected into `NeonizeConnector`.
- The whole flow through the UI is covered by `frontend/e2e/whatsapp.spec.ts` (JAN-223): the e2e backend imports a scripted fake of the `neonize` package (`frontend/e2e/fake-neonize/`) in place of the real library, so the spec plays QR, pair success/failure, connected, disconnected, history-sync and live-message events while the connector, setup service, API and UI run unchanged. It covers QR pairing through to the durable connector taking over, a rejected pairing and cancel, the dashboard message list, a restart after a dropped connection (no history redelivered), a disabled account refusing a restart, and deleting an account.
- **Not automatable:** actually scanning a QR code with a phone, WhatsApp's server-side decision about how much `HistorySyncEv` backlog to push, and end-to-end latency of a live incoming message reaching the dashboard. These require manual verification against a real WhatsApp account after each significant connector change.
