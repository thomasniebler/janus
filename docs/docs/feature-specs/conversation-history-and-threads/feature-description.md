# Conversation history and threads

## Feature definition

This feature isolates persistent assistant conversations from the streaming transport work.

It covers:

1. storing conversations in the database,
2. storing messages in the database,
3. starting new conversations,
4. reopening and continuing previous conversations,
5. autogenerating a conversation title immediately after the first user prompt.

## User value

- The user can return to earlier assistant discussions.
- The UI can present multiple separate threads instead of one long implicit session.
- Conversation naming becomes automatic instead of relying on manual labeling.

## In scope

- Conversation and message persistence.
- Retrieval and listing of prior conversations.
- New-conversation creation flow.
- Continue-existing-conversation flow.
- Automatic title generation after the first prompt.

## Out of scope

- Choosing an agent per conversation *(moot: agent definitions were removed in JAN-227; Janus is a single agent)*.
- Character and instruction editing in settings (see the Character page).
- Streaming transport behavior by itself.
- Complex search, filtering, archival, or sharing behavior unless later confirmed.

## Implementation task definition

**Task name:** conversation history and threads

**Definition of done:**

1. Conversations and messages are persisted durably.
2. The UI can start a new conversation and resume an older one.
3. A title is generated automatically after the first prompt.
4. The feature definition stays separate from streaming UX and settings management.
