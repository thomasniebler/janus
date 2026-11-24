# Tech spec: editable agent definitions

**Status:** Superseded
**Linear:** JAN-51 (original feature); removed by JAN-227 and JAN-169 under JAN-226
**Depends on:** None

## Status

Superseded and removed. Everything below describes the former
per-conversation agent-definition model, which no longer exists in the code:
there is no `AgentDefinition` table, no `/api/agents` API, no access toggles,
no skill assignment, and no handoffs. See the
[feature description](feature-description.md) for what replaced it.

The original text follows unchanged as a historical record. It was a
question-driven draft that intentionally avoided guessing product and security
behavior that had not been confirmed.

## Confirmed requirements

1. Every conversation should start with a specific agent definition.
2. Agent definitions should be editable on the settings page.
3. An agent definition consists of:
   - a system prompt
   - `mail` access boolean
   - `calendar` access boolean
   - `jira` access boolean
   - `planner_task_read_access` boolean
   - `planner_task_write_access` boolean

Planner task read and write are separate controls in the Agent Definition
editor. Both default to disabled for new definitions. Write permission is an
explicit opt-in and is scoped only to Janus-owned local planner-task
mutations; it does not grant write access to imported planner records,
calendar events, Jira tasks, or other integrations. Enabling write also makes
the planner task read tools available so that mutations have the corresponding
task context, but enabling read alone never grants mutation tools.

## Open questions for user confirmation

1. Should there be exactly one default agent definition, or can users choose from many without a default?
2. Can existing conversations change agent definition after creation, or is the selection immutable once the thread starts?
3. Should agent definitions be user-specific, global to the Janus instance, or admin-managed?
4. Is deleting an agent definition allowed if existing conversations reference it?
5. Should there be any built-in non-editable starter definitions?
6. What should happen if a conversation requests access to a resource that is disabled in its chosen definition?
7. Should the UI expose the selected definition prominently inside a conversation view?

## Technical decision areas

1. **Schema**
   - agent-definition table
   - relationship from conversations to agent definitions
   - ownership and audit fields
2. **Enforcement model**
   - how resource booleans constrain prompt context assembly and tool exposure
   - how planner task read/write separation is enforced for chat and local-task mutations
   - whether disabled resources are hidden, omitted, or explicitly denied in responses
3. **Settings UX**
   - list/edit/create/delete flows
   - validation for system prompts
4. **Migration behavior**
   - how pre-existing conversations behave if agent definitions are introduced after conversation history already exists

## Proposed spec structure once questions are answered

1. Data model
2. Settings API and frontend editing flow
3. Conversation creation contract
4. Runtime enforcement of resource booleans
5. Backward compatibility and migration plan
6. Acceptance criteria and tests

## Initial acceptance criteria candidates pending confirmation

1. A conversation cannot be created without an agent definition reference.
2. The settings page can create and edit agent definitions.
3. Runtime context assembly respects the chosen `mail`, `calendar`, `jira`, and planner-task permission booleans.
4. Read-only planner access exposes planner task reads without mutation tools.
5. Planner task write access is opt-in and exposes mutations only for Janus-owned local planner tasks.
