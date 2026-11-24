# Tech spec: streamed briefing and chat

**Status:** Superseded
**Linear:** JAN-231 (briefing moved to the built-in Morning briefing trigger)
**Depends on:** None

!!! note "Briefing streaming superseded; chat streaming unchanged"
    Chat responses still stream token by token over `/ws/chat`. The morning
    briefing no longer streams: since JAN-231 it is the built-in **Morning
    briefing** trigger, and `/ws/briefing` sends today's completed run (or runs
    the trigger once and sends its answer) as a single message between
    `__START__` and `__END__`. See
    [Background Workers and Runtime](../../tech-spec/background-workers-runtime.md#built-in-triggers-jan-231-jan-232).

## Status

Question-driven draft. This document intentionally avoids assuming product behavior that has not been confirmed.

## Confirmed requirements

1. The morning briefing generation should stream on the page.
2. Chat responses should be streamed as well.
3. This work should be isolated from the later conversation-history and agent-definition features.

## Current known state

Based on the existing codebase:

- Janus already has WebSocket briefing and chat endpoints.
- The repo already uses WebSockets for briefing/chat surfaces.
- The exact current mismatch between backend streaming and frontend rendering still needs to be documented feature-specifically when implementation starts.

## Open questions for user confirmation

1. Should the morning briefing keep using WebSockets, or should it move to another transport such as SSE?
2. Should chat and briefing share the same streaming UX conventions, including typing indicators and completion markers?
3. What should happen if the stream disconnects midway through generation?
4. Should partial output remain visible after an error, or should the UI discard incomplete output?
5. Should the page auto-reconnect, offer a manual retry, or both?
6. Is streaming required in the CLI as well, or only in the web UI?

## Technical decision areas

These need confirmation before a final implementation spec:

1. **Transport contract**
   - keep current WebSocket transport and normalize the frontend handling, or
   - change the transport contract for one or both surfaces
2. **Chunk protocol**
   - token-level chunks
   - sentence/block-level chunks
   - explicit start/end/error framing
3. **UI state model**
   - how partial assistant messages are represented locally
   - whether streamed messages can be cancelled
4. **Error handling**
   - reconnect semantics
   - duplicate-chunk protection
   - stale-session handling

## Proposed spec structure once questions are answered

1. Problem statement
2. Transport and message protocol
3. Frontend rendering states
4. Failure and reconnect behavior
5. Acceptance criteria
6. Regression coverage

## Initial acceptance criteria candidates pending confirmation

1. Briefing output becomes visible progressively in the UI.
2. Chat output becomes visible progressively in the UI.
3. Partial-output and disconnect behavior is documented and testable.
4. The implementation remains independent from conversation persistence.
