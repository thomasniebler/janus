# Knowledge and memory

Janus keeps what it learns about you in a few layers. Each layer has a tab or a settings page where you can read, correct, or delete it.

| Layer | What it holds | Where you manage it |
|---|---|---|
| Agent memory | Short facts about you and your work, checked overnight | `/memory` → **Agent Memory** |
| Topics | Ongoing subjects, backed by evidence from mail | `/memory` → **Topics** |
| Documents | Files and mail attachments Janus can search | `/memory` → **Documents** |
| Knowledge | Searchable passages, plus people, organizations and projects and how they relate | `/memory` → **Knowledge** |
| Wiki | One page per topic or busy entity, with citations | `/memory` → **Wiki** |
| Character | Who Janus is and how it talks to you | Settings → **Character** |
| Skills | Reusable instructions Janus loads when they apply | Settings → **Skills** |

The **Mail Prioritization Rules** tab on `/memory` holds your mail rules; see [Inbox](inbox.md).

Everything here is per user, except the skill library, which every user on the instance shares.

## Agent memory

A memory fact is one short summary, with optional detail. Janus reads your facts in chat and in trigger runs.

Facts come from three places:

- **You.** Add a fact under **Add memory fact** on the Agent Memory tab, or edit and delete facts in **Stored facts**.
- **Janus in chat.** With the `memory` toolbox, Janus lists, searches, adds, updates, deletes and groups facts. It writes a fact when you ask it to remember something, or when something is clearly worth remembering.
- **After each chat turn.** The utility model reads the turn and may store one fact. Your **Memory instructions** on Settings → Character decide what is worth keeping. A turn with a suspected prompt injection stores nothing, and neither does a turn in which Janus already wrote memory itself.

Adding a fact whose summary matches an active fact (ignoring case) refreshes that fact instead of creating a duplicate.

### Verification

Each fact shows how far it was checked:

| Badge | Meaning |
|---|---|
| unverified | Janus wrote it and the memory review has not checked it yet |
| confirmed | You wrote or edited it, or its conversation supports it as written |
| corrected | The memory review rewrote it to match its conversation |
| unsupported | Its conversation does not mention it. It is kept, but never added to the memory index on its own |
| contradicted | Its conversation says otherwise, so the memory review archived it |

A checked fact shows the **Evidence**: the exact words from the conversation that support it.

### Memory review

The **Memory review** built-in trigger runs every night (03:17 UTC; change or switch it off on Settings → Triggers). It is the "dreaming" step from Anthropic's research on agent memory:

1. **Check.** Up to 8 facts Janus wrote are compared with the conversation they came from. Unchecked facts go first, then the ones Janus uses most. A fact is only confirmed with an exact quote from the conversation.
2. **Merge.** Facts that say the same thing are merged, as with **Compact Memory**.
3. **Promote.** The review picks the confirmed facts every conversation should start with: the memory index.

Facts you wrote or edited yourself are never checked, changed or merged by the review; only your own **Compact Memory** merges them. When Janus edits one of your facts, it becomes Janus's fact and is checked against the conversation it was edited in. A checked fact is checked again after 90 days, or after 14 days when it is likely to change (a plan, a current status), and twice as often when Janus uses it. The review shares the monthly token budget of knowledge extraction and wiki synthesis. With a cloud utility model, the conversations it checks are sent to that provider.

### The memory index

Janus starts every conversation and every trigger run with a short memory index: at most 15 facts, about 2,000 characters. It holds the facts you pinned and the ones the memory review confirmed and promoted. Everything else stays searchable, and Janus is told to look there before it answers something that depends on your preferences, people, projects or earlier decisions.

Use the **Index** menu on a fact to decide yourself:

- **automatic**: the memory review decides;
- **pinned**: always in the index, first;
- **hidden**: never in the index.

### Compaction

Facts pile up and overlap. Click **Compact Memory** on the Agent Memory tab to clean them up:

1. The utility model reads your 100 most recently updated active facts.
2. It merges facts that say the same thing into one new fact.
3. The originals are archived, not deleted, and point to the merged fact.

The **Compaction status** card shows the last run's status and summary. Only one run per user runs at a time.

### Search

Memory facts and topics are searchable by meaning and by keyword once an embedding model is set (Settings → AI & System). The knowledge-index worker embeds new and changed facts in the background. To embed everything at once, run:

```bash
janus knowledge reembed          # missing or stale embeddings only
janus knowledge reembed --force  # everything, and rebuild the keyword index
```

Without an embedding model, search falls back to keyword matching.

## Topics

A topic is an ongoing subject, such as a project, a deal or a trip. Janus uses active topics to group the digest, in the dashboard's topic widget, and as context in chat.

### How topics appear

- **From mail.** When mail is processed, Janus extracts topics from messages in scope. By default that is IMPORTANT and DIGEST mail; SPAM and IGNORE mail never counts.
- **Manually.** Create a topic on the Topics tab with a title, a description and **marker keywords**. Mail that contains a marker keyword is attached to the topic.
- **In chat.** The `topics` toolbox lets Janus list, search, create and update topics.

An extracted topic starts as a **candidate**. It becomes **active** once enough distinct emails mention it (two by default).

| Status | Meaning |
|---|---|
| candidate | Seen, not yet confirmed |
| active | Tracked and shown |
| snoozed | Hidden until a date |
| resolved | Done |
| ignored | Never updated again |

You can **pin** a topic to keep it visible. A pinned topic also gets a wiki page.

### Evidence

Each topic lists the evidence behind it: emails, reminders, calendar events, tasks and memory facts. Open a topic on the Topics tab to see why it exists.

### Preferences

Topic tracking follows per-user preferences. They have no settings page yet, so the defaults apply:

| Preference | Default |
|---|---|
| Extraction scope | IMPORTANT and DIGEST mail |
| Minimum confidence | medium |
| Mentions to confirm a candidate | 2 |
| Most active topics in context | 10 |
| Link reminders, group the digest, dashboard widget, chat context | on |

## Documents

The Documents tab lists every knowledge source and lets you upload files. Janus also stages mail attachments automatically.

- **Formats:** PDF, Word (`.docx`), Excel (`.xlsx`), PowerPoint (`.pptx`), text, Markdown, CSV, HTML and mail (`.eml`).
- **Size:** up to 10 MB per file.
- **Parsing:** PDF and Office files need the optional `documents` extra (`uv sync --extra documents`). Without it, only plain text, Markdown and CSV are read; other files are listed as skipped, with the reason.

Filter the list by **Source type** to see mail, chats, WhatsApp chats, calendar events, planner tasks or uploads. Deleting a source removes its passages and everything the knowledge graph learned from it.

## Knowledge graph

### Ingestion

Janus turns your data into knowledge sources and splits each one into passages (chunks):

- mail (not SPAM or IGNORE) and its attachments,
- chat conversations and WhatsApp chats,
- planner tasks and calendar events,
- uploaded documents.

The knowledge-index worker picks up new and changed data in small batches and embeds the passages. It runs unless `JANUS_KNOWLEDGE_INDEX_WORKER_ENABLED` is off. To ingest everything that exists already, run `janus knowledge backfill`.

### Search

**Search your knowledge** on the Knowledge tab searches mail, chats, calendar, tasks and documents. In chat, Janus uses the `knowledge` toolbox for the same search.

Retrieval is hybrid. Janus filters candidates first (by source type, date or entity), then fuses two rankings:

- **direct:** vector similarity plus keyword matches on the passages;
- **graph:** passages that mention the entities your query names, and their neighbours up to two relation hops away.

Only your query is embedded; no chat model reads your data during a search.

### Entity extraction

The **Knowledge extraction** card switches the built-in trigger `builtin.knowledge` on or off. It is off by default.

When on, every 30 minutes the utility model reads newly ingested passages and records:

- **entities** of six types: person, organization, project, place, product and concept;
- **relations** between them, each with the passage that supports it.

Extraction stays within a **monthly token budget** (2,000,000 tokens by default). The card shows the tokens used this month, the calls made and the passages waiting. Set your own budget there. Wiki synthesis draws from the same budget.

!!! warning "Cloud models"
    If your utility model is a cloud model, turning extraction on sends the text of all ingested sources to that provider. Pick an Ollama model as the utility model under Settings → AI & System to keep it local.

### Merge review

Janus merges two entities on its own only when their normalized names or aliases match exactly. Similar names, such as "ACME" and "Acme Corp", land under **Possible duplicates** instead:

- **Accept** merges the duplicate into the suggested entity, with its mentions, relations and names.
- **Reject** keeps both. The pair is never suggested again.

The **Entities** list below can be searched and filtered by type. Select an entity to see its mentions and relations.

## Wiki

The wiki holds one page per active or pinned topic, and one per entity mentioned in at least five passages. Each page is Markdown written from the passages and memory facts behind its subject. Statements cite their sources with `[n]` markers.

### Writing pages

The **Wiki synthesis** switch on the Wiki tab turns the built-in trigger `builtin.wiki` on or off. It is off by default. When on, it runs every 60 minutes:

1. It creates missing pages.
2. It finds pages whose inputs changed.
3. It rewrites up to five of them with the utility model.

The same cloud-model warning as for entity extraction applies.

### Reading and editing

Open a page from the list, or search the pages. Each page has two views:

- **Page:** the text. Click a `[n]` marker to jump to its source.
- **Sources:** every citation with the passage or memory fact it points to, the linked entities and the linked facts. A source deleted since the page was written is marked as unavailable.

You can edit a page yourself. From then on it is marked **Edited by you**, and Janus no longer overwrites it: its rewrites arrive as **proposed revisions** that you accept or dismiss.

**History** lists every published revision. **Restore** brings an earlier one back.

### Proposed edits from chat

With the `wiki.propose` toolbox, Janus can propose a new text for a page from chat. The proposal waits on the page until you accept or dismiss it. The `wiki` toolbox lets Janus list and read pages.

### Graph view

The **Pages / Graph** switch on the Wiki tab shows how your topics and entities connect:

- **topic → entity** edges, weighted by the passages that link them;
- **entity → entity** edges, labelled with the relation.

Select a node to highlight its links and **Open wiki page**. **Show table** lists every link as text.

## Character

Settings → Character holds two things: the stable **character profile** and the adaptive **character state**.

### Character profile

The character profile is Janus's constitution. It is rendered into every system prompt, in chat and in trigger runs.

| Field | What it does |
|---|---|
| Name, identity | Who Janus is |
| Values and boundaries | Core values and non-negotiables, one per line |
| Additional instructions | Free text appended to the constitution |
| Memory instructions | Replaces the built-in rules for which chat facts get remembered |

**Prompt preview** shows the rendered system prompt and the effective memory rules before you save.

### Character state

The character state is the part Janus may adapt:

| Dimension | Values |
|---|---|
| Tone | neutral, warm, formal, direct, playful |
| Verbosity | brief, balanced, detailed |
| Proactivity | reactive, balanced, proactive |
| Current focus | One line of free text, up to 200 characters |

You control it in four places:

- **Current adaptive state:** edit it directly. Your edits apply at once. **Reset to defaults** restores the defaults from your bounds.
- **Bounds and defaults:** choose which values are allowed per dimension, and the default. A state outside the bounds is refused, whoever asks. Narrowing the bounds pulls the current state back inside them.
- **Proposals:** in chat, Janus can propose a change with the `character.propose` toolbox. **Approve** or **Reject** it. You can have at most five open proposals. Trigger runs can never propose changes.
- **History:** every change, who made it and the state before it. **Restore** returns to an earlier state.

Janus reads its own state with the `character` toolbox. A proposed focus that looks like a prompt injection is refused.

## Skills

A skill is a named block of instructions, in the `SKILL.md` format. Janus's system prompt lists every skill in one line, its name and description, so write the description as "when to use it". Janus loads a skill's full instructions with the `read_skill` tool when it needs them. The mail classifier, which has no tools, still gets every skill's full text.

Every signed-in user can read the library on Settings → Skills. Admins can also:

- create, edit and delete skills;
- describe a goal under **What should this skill do?** and let the design assistant draft the skill;
- **Import from GitHub community catalog**: Janus lists the `SKILL.md` files in the `anthropics/skills` repository and imports the one you pick.

### Skill suggestions

**Suggested skills** (shown to admins) looks at your chats from the last 30 days. When the same run of two to four tools appears in at least three turns, it offers that sequence as a skill. Up to five suggestions are shown.

- **Draft skill** hands the sequence to the design assistant. Nothing joins the library until you save the draft.
- A dismissed suggestion stays hidden for 30 days.

Suggestions use no model call; they count tool sequences.

### Who can change skills

The library is shared by every user on the instance, so a skill steers everyone's Janus.

- In chat, only admins get the `skills` toolbox. It always asks for approval.
- On Settings → Skills, only admins can create, edit, delete or import skills. Other users see the library read-only; the API answers their writes with 403.

See [Tools](tools.md) for how toolboxes and approvals work, and [Runtime triggers](runtime-triggers.md) for the built-in triggers mentioned above.
