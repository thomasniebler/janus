# Skill Suggestions

**Status:** Shipped (detection and drafting through the skill designer; offline refinement is planned, not built)
**Linear:** JAN-407
**Depends on:** the skill library and its design assistant (`api/skills.py`), recorded chat tool calls (`ConversationToolCall`, JAN-249)

## Idea

When Janus keeps doing the same thing the same way (the same tools, in the
same order, across several chats), that routine is a candidate for a skill.
A skill makes the routine explicit and reliable. Janus proposes, the user
decides: a suggestion is only an offer to draft, and nothing joins the skill
library until the user saves it.

Inspired by the skill proposer of
[samalgotrader7-ops/janus](https://github.com/samalgotrader7-ops/janus) (see
[External Agents](external-agents.md#acknowledgement)).

## Detection (no model call)

`services/skill_proposer.py`, computed on request:

1. **Read turns.** The user's assistant turns from the last 30 days that recorded tool calls are read as tool-name sequences, in call order. Rows sharing a `call_id` count once, since a call can be recorded in several phases.
2. **Collect candidates.** Every consecutive run of 2–4 tools that appears in at least 3 different turns is a candidate. A run of the same tool repeated is not a routine.
3. **Fold.** A candidate inside a longer candidate that appears just as often is dropped.
4. **Rank.** At most 5 suggestions are shown: most repeated first, then the longest.
5. **Dismiss.** **Not now** stores a dismissal (`SkillSuggestionDismissal`, table `skill_suggestion_dismissals`, migration `b5e7c9d1f3a6`) that hides the pattern for 30 days.

Only the user's own conversations count. Tool names are Janus's own registry names. Conversation titles are the user's own data and appear only as examples.

## Drafting

- **Where.** Settings → Skills shows **Suggested skills** (`SkillSuggestions.tsx`, `skillSuggestionsApi.ts`) when there are any.
- **Draft skill.** Opens the skill editor with the designer's goal prefilled with the suggestion's brief. The brief describes the tool chain and example chats, and asks for when the routine applies, the order of tools, the checks between steps, and the shape of the answer.
- **Who decides.** The user runs **Get design help** (`POST /api/skills/design-assist`), edits, and saves. Nothing is created automatically.

## API

- `GET /api/skills/suggestions`: `key`, `tools`, `turns`, `examples`, `brief`.
- `POST /api/skills/suggestions/dismiss` with `{ "key": ... }`.

The router is mounted before `api/skills.py`, so `/api/skills/{skill_id}` does not capture the path.

## Not built

- Suggestions from trigger runs' plan steps (only chat turns are read).
- Offline refinement of existing skills against past runs (the inspiring project's "GEPA", about 200 model calls per run).
