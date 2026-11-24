# Streamed briefing and chat

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

## Feature definition

This feature isolates the live-response experience of the Janus assistant surfaces.

It covers:

1. streaming the morning briefing generation directly in the dashboard page,
2. streaming chat responses in the conversation UI consistently,
3. aligning the product definition of “streaming” across the briefing and chat experiences.

## User value

- The user can see briefing output appear progressively instead of waiting for one final block.
- The user can see chat responses unfold in real time.
- The UI behavior becomes easier to reason about before deeper conversation-history work lands.

## In scope

- Frontend and backend behavior required to stream the morning briefing on its page.
- Frontend and backend behavior required to stream chat responses.
- Loading, error, reconnect, and partial-output expectations for these two streamed surfaces.

## Out of scope

- Persisting conversations or messages.
- Multi-conversation management.
- Agent-definition selection and settings management.
- Any change to authorization or tool/resource permissions.

## Implementation task definition

**Task name:** streamed briefing and chat

**Definition of done:**

1. The morning briefing page receives incremental output instead of only a final payload.
2. Chat responses are streamed visibly in the UI from first token to completion.
3. Failure and reconnect behavior is explicitly defined.
4. The feature definition does not depend on conversation persistence or agent-definition editing.
