# Knowledge stack visualization

**Status:** Shipped
**Linear:** JAN-334
**Depends on:** [Knowledge graph and wiki](knowledge-graph-and-wiki.md) (sources and chunks, entity graph, wiki synthesis; JAN-242 – JAN-244), [structured topic tracking](structured-topic-tracking.md) (topic evidence), memory facts, [frontend tech spec](../tech-spec/frontend.md) (shared palette, three shells)

## Problem

The knowledge layers were silos in the UI. A wiki page listed the titles of
its sources, but you could not read the passage behind a statement, see which
people or organizations the page draws on, or see the memory facts Janus keeps
about the subject. Wiki synthesis never read memory facts at all, so no
statement could be traced back to one. And nothing showed how topics and
entities connect.

## What was already persisted

Citations, entities, and graph edges are only shown where the link is
stored; nothing is inferred at read time. The one read-time lookup is the
Sources tab's list of uncited memory facts (the facts the next synthesis would
use, matched by `page_facts`), which carry no citation and are labelled
"not cited yet".

| Link | Stored in | Since |
|---|---|---|
| Wiki statement `[n]` → passage | `WikiPage.citations` (`{n, chunk_id, source_id, …}`) | JAN-244 |
| Passage → entity | `KnowledgeMention` | JAN-243 |
| Entity → entity | `KnowledgeRelation` (+ `KnowledgeRelationEvidence`) | JAN-243 |
| Topic → email / calendar event / memory fact | `TopicEvidence` | structured topic tracking |
| Email / calendar event → passages | `KnowledgeSource` (`source_type`, `owner_id`) → `KnowledgeChunk` | JAN-242 |
| Wiki statement `[n]` → memory fact | `WikiPage.citations` (`{n, fact_id, source_type: "memory_fact", …}`) | **this change** |

## What shipped

### Memory facts as synthesis inputs (statement → fact)

`wiki_synthesis.page_facts` picks up to 5 active memory facts per page: for a
topic, its `memory_fact` evidence first, then facts whose summary or detail
names the topic's title; for an entity, facts naming the entity or one of its
aliases (case-insensitive substring, names of at least 3 characters, `%`/`_`
taken literally, owner-scoped). They are numbered after the passages in the
prompt (`<passage number="n" kind="remembered fact">`, HTML-escaped like the
passages), and `cite` turns a marker on one of them into a citation with
`fact_id` and `source_type: "memory_fact"` instead of `chunk_id`.

The facts' ids and text hashes join the page's input hash **only when there are
facts**, so pages without matching facts keep their hash and are not rewritten
by the upgrade; pages that do have facts are rewritten once on the next run
(within the usual 5 pages per run and the shared monthly budget). A page whose
only inputs are facts is written instead of cleared, and clearing a user-edited
page keeps citations whose fact still exists. No schema change: `citations` is
already JSON.

### Provenance API and the Sources tab

`GET /api/wiki/pages/{id}/provenance`
(`knowledge_provenance.page_provenance_for_user`) resolves every citation of
the published page against the caller's own rows:

- **passage** citations carry the live chunk text (`excerpt`, up to 4,000
  characters), its source, and the entities mentioned in it;
- **fact** citations carry the live memory fact (summary, detail, status,
  origin, dates);
- `available: false` marks a cited row deleted since the page was written (the
  snapshot title and date still describe it);
- `entities` are the entities mentioned in the cited passages (and the page's
  own entity), each with the markers citing it and its wiki page;
- `facts` are the cited facts, then the facts the next synthesis would use.

`WikiPageView` has **Page** and **Sources** tabs. Clicking a `[n]` marker in the
page text switches to Sources with that citation expanded and highlighted
(drill-down to the raw passage or memory fact); citations can be opened one by
one, and an entity with its own page opens it.

### Graph view

`GET /api/knowledge/graph` (`knowledge_provenance.graph_for_user`; `topics`
and `entities` limits, default 30 and 60, at most 150 each) returns:

- nodes: active or pinned topics (pinned, then most recently seen) and
  entities — those linked to a shown topic first (by number of linking
  passages), then the most mentioned — each with its wiki page id;
- `evidence` edges topic → entity, weighted by the number of passages linking
  them: passages of the topic's email / calendar evidence, plus passages its
  wiki page cites, that mention the entity;
- `relation` edges entity → entity, labelled with the predicate.

Every graph query is aggregated in SQL or limited: evidence weights are
`COUNT(DISTINCT chunk)` per topic/entity pair, a cited passage that is already
one of the topic's evidence passages is not counted twice, a passage cited by
several topic pages links each of them, and the "most mentioned" entities are
an ordered, limited aggregate rather than a count of every entity.

The Wiki tab has a **Pages / Graph** switch. The graph is a dependency-free SVG
with a deterministic force layout (`lib/graphLayout.ts`, Fruchterman–Reingold,
no random seed). No new npm dependency: the repository has no chart or graph
library, and the existing Stats charts are hand-written SVG too. Node colors
use the shared `--viz-*` categorical slots (`VIZ_STYLE` from the Stats charts)
and are always named by the legend; selected nodes use `text-primary`, which
each shell retints. Selecting a node (mouse or keyboard) dims the rest, lists
its links, and offers **Open wiki page**; **Show table** lists every link.

## Security

Both endpoints and the synthesis fact lookup filter every row by the
requesting user: another user's page is a `404`, and a citation that points at
another user's chunk or fact (which synthesis never writes) resolves to
`available: false` with no content. Covered by
`tests/test_tenant_isolation.py` and `tests/test_knowledge_provenance.py`.
Memory facts reach the synthesis model only when wiki synthesis is on, under
the same cloud-model warning as the passages.

## Non-goals and limits

- Sentence-level provenance beyond the model's `[n]` markers: a statement is
  linked to what it cites, not to a span of the passage.
- Topic → memory fact edges in the graph (facts appear in the Sources tab).
- Pending proposals' provenance: the Sources tab describes the published page.
