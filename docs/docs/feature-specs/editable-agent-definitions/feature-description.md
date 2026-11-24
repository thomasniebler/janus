# Editable agent definitions

**Status:** Superseded
**Linear:** JAN-51 (original feature); removed by JAN-227 and JAN-169 under JAN-226
**Depends on:** None

!!! warning "Removed"
    Agent definitions no longer exist. Janus is a single agent: its identity is
    your Character (Settings → Character), its instructions are one shared skill
    library (Settings → Skills), tool access follows your real connections plus
    the per-capability tool policy (Settings → Tools & Connections), and
    everything outside chat is a trigger (Settings → Triggers). The
    `agent_definitions`, `agent_definition_skills`, and `agent_handoff_runs`
    tables were dropped by migration `d5e8f1a2b3c4`. This page is kept as a
    historical record; see [Runtime triggers](../../runtime-triggers.md),
    [Plan-driven Janus runtime](../plan-driven-janus-runtime.md), and
    [Background Workers and Runtime](../../tech-spec/background-workers-runtime.md)
    for the replacement.

## Feature definition

This feature isolates the configuration model for conversation-start agent definitions.

It covers:

1. requiring each conversation to start with a selected agent definition,
2. making agent definitions editable on the settings page,
3. defining each agent definition as:
   - a system prompt
   - a boolean access toggle per tool category

## User value

- The user can control the role and boundaries of a conversation before it starts.
- Different conversations can begin with different assistant behaviors.
- Access control stays understandable because each capability is one on/off toggle.
- Sensitive capabilities (planner-task write, trigger self-scheduling, agent management) are explicit opt-ins, off by default.

## In scope

- Data and settings representation for agent definitions.
- Settings-page editing experience for those definitions.
- Conversation-start requirement to choose or attach a specific definition.
- One boolean access flag per tool category, editable from the Settings **Agent Definitions** editor:
  - `mail_access`, `calendar_access`, `jira_access` — mail/calendar/task context
  - `planner_task_read_access` / `planner_task_write_access` — separate read and write permissions for Janus-owned local planner tasks; write access is off by default
  - `github_access`, `linear_access` — GitHub and Linear tool access
  - `memory_access`, `topic_access` — memory and tracked-topic tools

  Since JAN-201, `planner_task_read_access`, `planner_task_write_access`,
  `memory_access`, and `topic_access` are still stored and editable but no
  longer gate any tools (the single Janus agent always has them), and an
  agent definition's system prompt, default model, and attached skills no
  longer drive chat, mail triage, or briefings -- those come from the
  Character settings and the flat skill library. Removal is tracked in JAN-169.
  - `trigger_access` — the self-service trigger toolbox (create/list/cancel the agent's own scheduled follow-ups); off by default
  - `agent_management_access` — tools that create/edit other agent definitions and skills

## Out of scope

- General-purpose tool registries.
- Complex role-based authorization models.
- Streaming behavior by itself.
- Conversation persistence mechanics by themselves.

## Implementation task definition

**Task name:** editable agent definitions

**Definition of done:**

1. Agent definitions are editable in settings.
2. Each definition includes a system prompt plus one boolean access flag per tool category (see **In scope** above).
3. New conversations require a selected agent definition.
4. The Agent Definition editor presents planner task read and write as separate permissions, with write explicitly opt-in.
5. Planner task write permission can mutate only Janus-owned local planner tasks and does not authorize imported planner records, calendar events, Jira tasks, or other integrations.
6. Each new tool category (GitHub, Linear, memory, topics, triggers, agent management) ships with its own off-by-default or context-appropriate-default access flag rather than widening an existing one.
